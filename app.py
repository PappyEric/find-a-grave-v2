import os
import threading
import time
import requests
import re
from urllib.parse import unquote
from bs4 import BeautifulSoup
from flask import Flask, request, jsonify, render_template, send_file, send_from_directory

import database
import grave_digger
import toolbox
import gedcom_exporter
import geo_utils
import data_quality
import io
import glob
import shutil

app = Flask(__name__)

# --- Patch toolbox printing ---
scraping_logs = []
def log_gui(message):
    timestamp = time.strftime('%H:%M:%S')
    log_line = f"[{timestamp}] {message}"
    scraping_logs.append(log_line)
    if len(scraping_logs) > 500:
        scraping_logs.pop(0)
    print(log_line)

# Patch toolbox to log to GUI
def print_l_patched(string='', last='\n'):
    log_gui(string)
toolbox.print_l = print_l_patched

# --- Scraper Thread State ---
scraper_thread = None
scraper_active = False
scraper_paused = False
current_cemetery_id = None
current_groups = []

def discover_and_enqueue_burials(session, cemetery_id):
    log_gui(f"Discovering burial URLs for cemetery {cemetery_id}...")
    cemetery_index = f"{grave_digger.find_a_grave}/cemetery/{cemetery_id}"
    page = 1
    max_pages = 200
    loop = True
    burial_urls = []
    
    while loop and scraper_active:
        if scraper_paused:
            time.sleep(1)
            continue
            
        if page == max_pages:
            log_gui("Exceeded max pages.")
            break
            
        cemetery_index_page = f"{cemetery_index}/memorial-search?page={page}"
        log_gui(f"Fetching search page {page}...")
        
        req = toolbox.get_url(session, cemetery_index_page)
        if req.status_code != 200:
            log_gui(f"Error fetching page {page}: {req.status_code}")
            break
            
        soup = BeautifulSoup(req.content, 'html.parser')
        
        warnings = grave_digger.soup_find(soup, 'warnings')
        for warning in warnings:
            if warning.parent.text.lower().find('no matches found') > 0:
                loop = False
                break
        if not loop:
            break
            
        memorials = grave_digger.soup_find(soup, 'memorials')
        if not memorials:
            loop = False
            break
            
        page_urls = []
        for memorial in memorials:
            if len(memorial.find_all('a')) > 0:
                memorial_url = grave_digger.find_a_grave + memorial.a['href']
                page_urls.append(memorial_url)
                burial_urls.append(memorial_url)
                
        if page_urls:
            database.enqueue_urls(page_urls, cemetery_id, 'burial')
            log_gui(f"Enqueued {len(page_urls)} burials from page {page}.")
            
        page += 1
        time.sleep(1)
        
    log_gui(f"Finished burial discovery. Total discovered: {len(burial_urls)}")
    return burial_urls

def enqueue_family_urls(cemetery_id, group_name):
    conn = database.get_db_conn()
    try:
        cursor = conn.cursor()
        cursor.execute("""
            SELECT DISTINCT r.to_memorial_id
            FROM relationships r
            JOIN memorials m ON r.from_memorial_id = m.id
            WHERE m.cemetery_id = ? AND r.relationship_type = ?
              AND r.to_memorial_id NOT IN (SELECT id FROM memorials)
              AND NOT EXISTS (
                  SELECT 1 FROM download_queue q
                  WHERE q.url LIKE '%' || r.to_memorial_id || '%'
              );
        """, (cemetery_id, group_name))
        rows = cursor.fetchall()
    finally:
        conn.close()
        
    urls = []
    for r in rows:
        rel_id = r[0]
        url = f"https://www.findagrave.com/memorial/{rel_id}"
        urls.append(url)
        
    if urls:
        database.enqueue_urls(urls, cemetery_id, group_name)
        log_gui(f"Enqueued {len(urls)} related '{group_name}' pages for download.")
    else:
        log_gui(f"No new '{group_name}' relatives found to download.")

def auto_heal_missing_cemeteries(session, target_cemetery_id=None):
    try:
        conn = database.get_db_conn()
        cursor = conn.cursor()
        if target_cemetery_id:
            cursor.execute("SELECT id, nickname FROM cemeteries WHERE id = ? AND (name IS NULL OR name = '' OR location IS NULL OR location = '' OR gps_lat IS NULL);", (target_cemetery_id,))
        else:
            cursor.execute("SELECT id, nickname FROM cemeteries WHERE name IS NULL OR name = '' OR location IS NULL OR location = '' OR gps_lat IS NULL;")
        rows = cursor.fetchall()
        conn.close()
        
        if not rows:
            return
            
        log_gui(f"Found {len(rows)} cemetery records missing details. Auto-fetching metadata...")
        for row in rows:
            cem_id = row[0]
            nickname = row[1] or str(cem_id)
            cemetery_url = f"https://www.findagrave.com/cemetery/{cem_id}"
            log_gui(f"Fetching metadata for cemetery {cem_id}...")
            
            req = toolbox.get_url(session, cemetery_url)
            if req.status_code == 200:
                soup = BeautifulSoup(req.text, 'html.parser')
                
                # 1. Parse Name
                name_val = grave_digger.soup_find(soup, 'cemetery_name')
                if not name_val:
                    h1 = soup.find('h1')
                    name_val = h1.text.strip() if h1 else 'Cemetery'
                name_val = name_val.strip()
                
                # 2. Parse Location
                location_val = ""
                address_el = soup.find(attrs={"itemprop": "address"})
                if address_el:
                    location_val = " ".join(address_el.text.split())
                    
                # 3. Parse GPS Coordinates
                gps_lat = None
                gps_lng = None
                gps_link = soup.find('a', href=re.compile(r'destination=([0-9.-]+),([0-9.-]+)'))
                if gps_link:
                    match = re.search(r'destination=([0-9.-]+),([0-9.-]+)', gps_link.get('href'))
                    if match:
                        try:
                            gps_lat = float(match.group(1))
                            gps_lng = float(match.group(2))
                        except ValueError:
                            pass
                
                database.add_cemetery(cem_id, nickname, name_val, location_val, gps_lat, gps_lng)
                log_gui(f"Auto-populated details for cemetery {cem_id}: {name_val} ({location_val})")
            else:
                log_gui(f"Error fetching metadata for cemetery {cem_id}: status {req.status_code}")
    except Exception as e:
        log_gui(f"Error in auto-heal cemeteries: {e}")

def scraper_worker():
    global scraper_active, scraper_paused, current_cemetery_id, current_groups
    
    log_gui("Background scraper thread started.")
    session = None
    
    try:
        from DrissionPage import ChromiumPage, ChromiumOptions
        co = ChromiumOptions()
        co.set_argument('--headless=new')
        co.set_user_agent('Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36')
        co.set_argument('--blink-settings=imagesEnabled=false')
        co.set_timeouts(base=15, page_load=15)
        session = ChromiumPage(co)
        
        # Only auto-heal details for the cemetery currently being scraped (prevents looping 200+ cemeteries here)
        auto_heal_missing_cemeteries(session, target_cemetery_id=current_cemetery_id)
                
        # Check if we already have burials in the database or queue and if full discovery was run
        stats = database.get_queue_stats(current_cemetery_id)
        discovered_before = database.is_burials_discovered(current_cemetery_id)
        
        if stats['pending'] == 0 and stats['downloading'] == 0 and not discovered_before:
            log_gui("Queue is empty and burials have not been fully discovered yet. Starting burial discovery...")
            try:
                discover_and_enqueue_burials(session, current_cemetery_id)
                database.set_burials_discovered(current_cemetery_id, 1)
            except Exception as e:
                log_gui(f"Error during discovery: {e}")
                scraper_active = False
                return

        family_enqueued = False
        consecutive_failures = 0
        download_count = 0
        page_count = 0
        while scraper_active:
            if scraper_paused:
                time.sleep(1)
                continue
                
            # Periodically recycle Chrome to release memory and prevent hangs
            page_count += 1
            if page_count >= 50:
                log_gui("Recycling browser session to clear memory and prevent hangs...")
                try:
                    session.quit()
                except:
                    pass
                try:
                    session = ChromiumPage(co)
                    page_count = 0
                    log_gui("Fresh browser session started successfully.")
                except Exception as ex:
                    log_gui(f"Failed to recycle browser: {ex}")
                    
            item = database.get_next_queued_item(current_cemetery_id)
            
            if not item:
                # If burials completed, enqueue relatives
                if not family_enqueued:
                    log_gui("Burials completed. Searching and enqueuing family relations...")
                    for g in current_groups:
                        if g != 'burial':
                            enqueue_family_urls(current_cemetery_id, g)
                    family_enqueued = True
                    # Get next item
                    item = database.get_next_queued_item(current_cemetery_id)
                    
                if not item:
                    # Fully complete!
                    log_gui("All queue items successfully processed!")
                    scraper_active = False
                    current_cemetery_id = None
                    break
                    
            url = item['url']
            group = item['group_name']
            cemetery_id = item['cemetery_id']
            
            log_gui(f"Fetching {group}: {url}")
            database.update_queue_status(url, 'downloading')
            
            try:
                # Refresh cemetery metadata
                cemetery = database.get_cemetery(cemetery_id)
                cemetery_slug = f"{cemetery_id}_{cemetery['name']}"
                path_to_cemetery_folder = f"{grave_digger.path_to_stash}{cemetery_slug}"
                os.makedirs(path_to_cemetery_folder, exist_ok=True)
                
                path_to_folder = {
                    'cemetery': path_to_cemetery_folder,
                    'burial': f"{path_to_cemetery_folder}/{cemetery_id}_burials",
                    'parent': f"{path_to_cemetery_folder}/{cemetery_id}_parents",
                    'spouse': f"{path_to_cemetery_folder}/{cemetery_id}_spouses",
                    'child': f"{path_to_cemetery_folder}/{cemetery_id}_children",
                    'sibling': f"{path_to_cemetery_folder}/{cemetery_id}_siblings",
                    'half-sibling': f"{path_to_cemetery_folder}/{cemetery_id}_half-siblings"
                }
                os.makedirs(path_to_folder[group], exist_ok=True)
                
                if group == 'burial':
                    args = [session, group, url, '', path_to_folder]
                else:
                    # Find parent burial ID
                    parent_id = None
                    mem_id = url.rstrip('/').split('/')[-1]
                    if '-' in mem_id:
                        mem_id = mem_id.split('-')[0]
                    if mem_id.isdigit():
                        conn = database.get_db_conn()
                        cursor = conn.cursor()
                        cursor.execute("SELECT from_memorial_id FROM relationships WHERE to_memorial_id = ? LIMIT 1", (mem_id,))
                        row = cursor.fetchone()
                        conn.close()
                        if row:
                            parent_id = row[0]
                            
                    parent_url = ""
                    if parent_id:
                        conn = database.get_db_conn()
                        cursor = conn.cursor()
                        cursor.execute("SELECT url FROM memorials WHERE id = ?", (parent_id,))
                        row = cursor.fetchone()
                        conn.close()
                        if row:
                            parent_url = row[0]
                            
                    if not parent_url:
                        parent_url = f"https://www.findagrave.com/memorial/0"
                        
                    args = [session, group, parent_url, url, path_to_folder]
                    
                grave_digger.stash_group_page(args)
                database.update_queue_status(url, 'completed')
                consecutive_failures = 0  # Reset on successful download
                
            except Exception as e:
                log_gui(f"Error stashing {url}: {e}")
                database.update_queue_status(url, 'failed', str(e))
                consecutive_failures += 1
                
                # Self-healing: Restart browser if we hit a timeout or connection issue
                err_str = str(e).lower()
                if "timeout" in err_str or "stuck" in err_str or "connection" in err_str or "getouterhtml" in err_str:
                    log_gui("Timeout or connection issue detected. Self-healing browser by restarting Chrome...")
                    try:
                        session.quit()
                    except:
                        pass
                    try:
                        session = ChromiumPage(co)
                        page_count = 0
                        log_gui("Browser restarted successfully. Resuming...")
                    except Exception as ex:
                        log_gui(f"Failed to restart browser during self-healing: {ex}")
                        
                if consecutive_failures >= 3:
                    log_gui("Error: 3 consecutive stashing failures. Automatically pausing scraper to prevent IP block.")
                    scraper_paused = True
                    consecutive_failures = 0
                
            # sleep range
            toolbox.pause(0.5, 2.0, False)
            
    except Exception as ex:
        log_gui(f"Scraper thread exception: {ex}")
        scraper_active = False
    finally:
        if session:
            try:
                session.quit()
                log_gui("Headless browser closed.")
            except:
                pass
        log_gui("Background scraper thread stopped.")

# --- Page Navigation Routes ---

@app.route('/')
def home():
    return render_template('index.html')

@app.route('/tree')
def tree_page():
    return render_template('tree.html')

@app.route('/map')
def map_page():
    return render_template('map.html')

@app.route('/analytics')
def analytics_page():
    return render_template('analytics.html')

@app.route('/quality')
def quality_page():
    return render_template('quality.html')

# --- API Endpoints ---

@app.route('/api/analytics', methods=['GET'])
def api_get_analytics():
    try:
        cemetery_id = request.args.get('cemetery_id')
        data = database.get_analytics_summary(cemetery_id=cemetery_id)
        return jsonify(data)
    except Exception as e:
        return jsonify({'error': f"Failed to compute analytics: {str(e)}"}), 500

@app.route('/api/quality/audit', methods=['GET'])
def api_get_quality_audit():
    try:
        cemetery_id = request.args.get('cemetery_id')
        report = data_quality.run_quality_audit(cemetery_id=cemetery_id)
        return jsonify(report)
    except Exception as e:
        return jsonify({'error': f"Quality audit failed: {str(e)}"}), 500

@app.route('/api/quality/search', methods=['GET'])
def api_get_quality_search():
    try:
        q = request.args.get('q', '')
        cemetery_id = request.args.get('cemetery_id')
        res = data_quality.search_inscriptions_and_bios(query=q, cemetery_id=cemetery_id)
        return jsonify(res)
    except Exception as e:
        return jsonify({'error': f"Full-text search failed: {str(e)}"}), 500

@app.route('/api/quality/sync', methods=['GET'])
def api_get_quality_sync():
    try:
        cemetery_id = request.args.get('cemetery_id')
        report = data_quality.get_stash_sync_report(cemetery_id=cemetery_id)
        return jsonify(report)
    except Exception as e:
        return jsonify({'error': f"Stash sync report failed: {str(e)}"}), 500

@app.route('/api/cemeteries', methods=['GET'])
def api_get_cemeteries():
    cemeteries = database.get_cemeteries()
    all_stats = database.get_all_queue_stats()
    for cem in cemeteries:
        cem['queue_stats'] = all_stats.get(cem['id'], {'pending': 0, 'downloading': 0, 'failed': 0})
    return jsonify(cemeteries)

@app.route('/api/cemeteries/<cemetery_id>', methods=['GET'])
def api_get_cemetery_details(cemetery_id):
    cem = database.get_cemetery(cemetery_id)
    if not cem:
        return jsonify({'error': 'Cemetery not found'}), 404
        
    stats = database.get_queue_stats(cemetery_id)
    cem['queue_stats'] = stats
    
    memorials, total_count = database.get_memorials(cemetery_id=cemetery_id, limit=5000, offset=0)
    cem['memorials'] = memorials
    cem['burial_count'] = total_count
    return jsonify(cem)

@app.route('/api/cemeteries', methods=['POST'])
def api_add_cemetery():
    data = request.json
    cemetery_id = data.get('id')
    nickname = data.get('nickname')
    
    if not cemetery_id:
        return jsonify({'error': 'Cemetery ID is required'}), 400
        
    if not nickname:
        # Auto-generate nickname if not provided (e.g. CEM8319)
        nickname = 'CEM' + (cemetery_id[-4:] if len(cemetery_id) > 4 else cemetery_id)
        
    database.add_cemetery(cemetery_id, nickname)
    return jsonify({'success': True})

@app.route('/api/cemeteries/<cemetery_id>', methods=['DELETE'])
def api_delete_cemetery(cemetery_id):
    database.delete_cemetery(cemetery_id)
    # Clear directory under stash
    # glob files like stash/<cemetery_id>*
    folders = glob.glob(f"stash/{cemetery_id}*_*/")
    for f in folders:
        try:
            shutil.rmtree(f)
        except Exception as e:
            print(f"Error deleting folder {f}: {e}")
    return jsonify({'success': True})

@app.route('/api/cemeteries/populate-metadata', methods=['POST'])
def api_populate_metadata():
    global scraper_thread, scraper_active, scraper_paused, current_cemetery_id
    
    if scraper_active:
        return jsonify({'error': 'Cannot run metadata update while scraper is active.'}), 400
        
    scraper_active = True
    scraper_paused = False
    current_cemetery_id = "METADATA_SYNC"
    
    def run_metadata_sync():
        global scraper_active, current_cemetery_id
        log_gui("Cemetery metadata sync worker started.")
        session = None
        try:
            from DrissionPage import ChromiumPage, ChromiumOptions
            co = ChromiumOptions()
            co.set_argument('--headless=new')
            co.set_user_agent('Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36')
            co.set_argument('--blink-settings=imagesEnabled=false')
            co.set_timeouts(base=15, page_load=15)
            session = ChromiumPage(co)
            
            auto_heal_missing_cemeteries(session)
            log_gui("Cemetery metadata sync completed successfully!")
        except Exception as e:
            log_gui(f"Error in metadata sync worker: {e}")
        finally:
            if session:
                try:
                    session.quit()
                except:
                    pass
            scraper_active = False
            current_cemetery_id = None
            log_gui("Cemetery metadata sync worker stopped.")
            
    scraper_thread = threading.Thread(target=run_metadata_sync, daemon=True)
    scraper_thread.start()
    return jsonify({'success': True})

@app.route('/api/scrape/start', methods=['POST'])
def api_scrape_start():
    global scraper_thread, scraper_active, scraper_paused, current_cemetery_id, current_groups
    
    if scraper_active:
        return jsonify({'error': 'Scraper is already running'}), 400
        
    data = request.json
    cemetery_id = data.get('cemetery_id')
    groups = data.get('groups', ['burial'])
    
    if not cemetery_id:
        return jsonify({'error': 'Cemetery ID is required'}), 400
        
    current_cemetery_id = cemetery_id
    current_groups = groups
    scraper_active = True
    scraper_paused = False
    
    scraper_thread = threading.Thread(target=scraper_worker, daemon=True)
    scraper_thread.start()
    
    return jsonify({'success': True})

@app.route('/api/scrape/pause', methods=['POST'])
def api_scrape_pause():
    global scraper_paused
    scraper_paused = True
    log_gui("Scraper paused.")
    return jsonify({'success': True})

@app.route('/api/scrape/resume', methods=['POST'])
def api_scrape_resume():
    global scraper_paused
    scraper_paused = False
    log_gui("Scraper resumed.")
    return jsonify({'success': True})

@app.route('/api/scrape/cancel', methods=['POST'])
def api_scrape_cancel():
    global scraper_active, scraper_paused, current_cemetery_id
    scraper_active = False
    scraper_paused = False
    log_gui("Scraper cancelled. Clearing queue for active cemetery...")
    if current_cemetery_id:
        database.clear_queue(current_cemetery_id)
    current_cemetery_id = None
    return jsonify({'success': True})

@app.route('/api/scrape/status', methods=['GET'])
def api_scrape_status():
    global scraper_active, scraper_paused, current_cemetery_id
    
    queue_stats = {}
    if current_cemetery_id:
        queue_stats = database.get_queue_stats(current_cemetery_id)
        
    return jsonify({
        'active': scraper_active,
        'paused': scraper_paused,
        'current_cemetery_id': current_cemetery_id,
        'queue_stats': queue_stats,
        'logs': scraping_logs
    })

@app.route('/api/memorials', methods=['GET'])
def api_get_memorials():
    cemetery_id = request.args.get('cemetery_id')
    query = request.args.get('query')
    limit = int(request.args.get('limit', 50))
    offset = int(request.args.get('offset', 0))
    
    memorials, total = database.get_memorials(cemetery_id, query, limit, offset)
    return jsonify({
        'memorials': memorials,
        'total': total,
        'limit': limit,
        'offset': offset
    })

@app.route('/api/memorials/<memorial_id>', methods=['GET'])
def api_get_memorial_details(memorial_id):
    details = database.get_memorial_details(memorial_id)
    if not details:
        return jsonify({'error': 'Memorial not found'}), 404
        
    # Scan for stashed photos
    photos_list = []
    photos_dir = os.path.join('photos', str(memorial_id))
    if os.path.exists(photos_dir):
        for f in os.listdir(photos_dir):
            photos_list.append(f"/photos/{memorial_id}/{f}")
                
    # Sort so primary image is first
    photos_list.sort(key=lambda x: 0 if 'primary' in x else 1)
    details['photos'] = photos_list
    
    return jsonify(details)

@app.route('/api/memorials/<memorial_id>/graph', methods=['GET'])
def api_get_memorial_graph(memorial_id):
    ancestor_depth = int(request.args.get('ancestors', 2))
    descendant_depth = int(request.args.get('descendants', 2))
    
    conn = database.get_db_conn()
    try:
        cursor = conn.cursor()
        
        cursor.execute("SELECT id FROM memorials WHERE id = ?", (memorial_id,))
        exists = cursor.fetchone()
        if not exists:
            return jsonify({'error': 'Memorial not found'}), 404
            
        nodes = {}
        edges = set()
        name_cache = {}
        
        def get_mem_name(mem_id):
            if mem_id in name_cache:
                return name_cache[mem_id]
            cursor.execute("SELECT name, birth_date, death_date FROM memorials WHERE id = ?", (mem_id,))
            row = cursor.fetchone()
            if row:
                name_val = row['name']
                birth = row['birth_date']
                death = row['death_date']
                
                birth_yr, death_yr = "?", "?"
                if birth:
                    m = re.search(r'\b\d{4}\b', birth)
                    if m:
                        birth_yr = m.group(0)
                if death:
                    m = re.search(r'\b\d{4}\b', death)
                    if m:
                        death_yr = m.group(0)
                        
                if birth or death:
                    res = f"{name_val}\n({birth_yr} - {death_yr})"
                else:
                    res = name_val
            else:
                res = f"ID: {mem_id}"
            name_cache[mem_id] = res
            return res
            
        queue = [(memorial_id, 0, 'focus', 0)]
        visited = set()
        
        while queue:
            curr_id, depth, rel_group, level = queue.pop(0)
            if curr_id in visited:
                continue
            visited.add(curr_id)
            
            if curr_id not in nodes:
                nodes[curr_id] = {
                    'id': curr_id,
                    'label': get_mem_name(curr_id),
                    'group': rel_group,
                    'level': level
                }
                
            # Generation boundary checks
            if rel_group in ['ancestor', 'spouse_ancestor'] and abs(level) >= ancestor_depth:
                continue
            if rel_group == 'descendant' and abs(level) >= descendant_depth:
                continue
            if rel_group in ['focus_sibling', 'spouse_sibling', 'sibling']:
                continue
                
            cursor.execute("""
                SELECT to_memorial_id, relationship_type, 'to' as dir 
                FROM relationships 
                WHERE from_memorial_id = ?
                UNION
                SELECT from_memorial_id, relationship_type, 'from' as dir
                FROM relationships
                WHERE to_memorial_id = ?;
            """, (curr_id, curr_id))
            
            rows = cursor.fetchall()
            for r in rows:
                rel_id = r[0]
                rel_type = r[1]
                direction = r[2]
                
                logical_rel = None
                if direction == 'to':
                    logical_rel = rel_type
                else:
                    if rel_type == 'parent':
                        logical_rel = 'child'
                    elif rel_type == 'child':
                        logical_rel = 'parent'
                    else:
                        logical_rel = rel_type
                        
                next_group = rel_group
                edge_label = logical_rel.capitalize()
                next_level = level
                
                if rel_group == 'focus':
                    if logical_rel == 'parent':
                        if ancestor_depth > 0:
                            next_group = 'ancestor'
                            next_level = level - 1
                        else:
                            continue
                    elif logical_rel == 'child':
                        if descendant_depth > 0:
                            next_group = 'descendant'
                            next_level = level + 1
                        else:
                            continue
                    elif logical_rel == 'spouse':
                        next_group = 'spouse'
                        next_level = level
                    elif logical_rel in ['sibling', 'half-sibling']:
                        next_group = 'focus_sibling'
                        next_level = level
                elif rel_group == 'spouse':
                    if logical_rel in ['sibling', 'half-sibling']:
                        next_group = 'spouse_sibling'
                        next_level = level
                    elif logical_rel == 'parent':
                        if ancestor_depth > 0:
                            next_group = 'spouse_ancestor'
                            next_level = level - 1
                        else:
                            continue
                    elif logical_rel == 'child':
                        if descendant_depth > 0:
                            next_group = 'descendant'
                            next_level = level + 1
                        else:
                            continue
                elif rel_group in ['ancestor', 'spouse_ancestor']:
                    if logical_rel == 'parent':
                        next_group = rel_group
                        next_level = level - 1
                    else:
                        continue
                elif rel_group == 'descendant':
                    if logical_rel == 'child':
                        next_group = 'descendant'
                        next_level = level + 1
                    else:
                        continue
                else:
                    continue
                    
                edges.add((curr_id, rel_id, edge_label))
                
                if rel_id not in visited:
                    queue.append((rel_id, depth + 1, next_group, next_level))
                    
        return jsonify({
            'nodes': list(nodes.values()),
            'edges': [{'from': e[0], 'to': e[1], 'label': e[2]} for e in edges]
        })
    finally:
        conn.close()

@app.route('/api/export', methods=['POST'])
def api_export():
    try:
        output_file = 'output/burials.xlsx'
        success = database.db_to_excel(output_file)
        if success:
            return jsonify({'success': True, 'file': output_file})
        else:
            return jsonify({'error': 'Export failed: No data found'}), 400
    except Exception as e:
        return jsonify({'error': f"Export failed: {str(e)}"}), 500

@app.route('/api/download-excel')
def download_excel():
    output_file = 'output/burials.xlsx'
    if os.path.exists(output_file):
        return send_file(output_file, as_attachment=True)
    return "File not found", 404

@app.route('/api/memorials/<memorial_id>/export-gedcom', methods=['GET', 'POST'])
def export_memorial_gedcom(memorial_id):
    try:
        gedcom_content = gedcom_exporter.export_focus_person_gedcom(memorial_id)
        if not gedcom_content:
            return jsonify({'error': 'Memorial not found or no data available'}), 404
        
        mem_details = database.get_memorial_details(memorial_id)
        mem_name = mem_details.get('name', f'memorial_{memorial_id}') if mem_details else f'memorial_{memorial_id}'
        safe_name = re.sub(r'[^a-zA-Z0-9_-]', '_', mem_name).strip('_')
        filename = f"{safe_name}_family.ged"
        
        buffer = io.BytesIO(gedcom_content.encode('utf-8'))
        return send_file(
            buffer,
            as_attachment=True,
            download_name=filename,
            mimetype='text/plain'
        )
    except Exception as e:
        return jsonify({'error': f"GEDCOM export failed: {str(e)}"}), 500

@app.route('/api/cemeteries/<cemetery_id>/export-gedcom', methods=['GET', 'POST'])
def export_cemetery_gedcom(cemetery_id):
    try:
        gedcom_content = gedcom_exporter.export_cemetery_gedcom(cemetery_id)
        if not gedcom_content:
            return jsonify({'error': 'Cemetery not found or no stashed memorials available'}), 404
        
        filename = f"cemetery_{cemetery_id}_registry.ged"
        if str(cemetery_id).lower() == 'all':
            filename = "all_cemeteries_registry.ged"
            
        buffer = io.BytesIO(gedcom_content.encode('utf-8'))
        return send_file(
            buffer,
            as_attachment=True,
            download_name=filename,
            mimetype='text/plain'
        )
    except Exception as e:
        return jsonify({'error': f"GEDCOM export failed: {str(e)}"}), 500

@app.route('/api/map/cemeteries', methods=['GET'])
def api_get_map_cemeteries():
    try:
        cemeteries = geo_utils.get_cemeteries_with_gps()
        return jsonify({'cemeteries': cemeteries, 'count': len(cemeteries)})
    except Exception as e:
        return jsonify({'error': f"Failed to fetch map cemeteries: {str(e)}"}), 500

@app.route('/api/map/burials', methods=['GET'])
def api_get_map_burials():
    try:
        cemetery_id = request.args.get('cemetery_id')
        surname = request.args.get('surname')
        burials = geo_utils.get_burials_with_gps(cemetery_id=cemetery_id, surname=surname)
        return jsonify({'burials': burials, 'count': len(burials)})
    except Exception as e:
        return jsonify({'error': f"Failed to fetch map burials: {str(e)}"}), 500

@app.route('/api/memorials/<memorial_id>/proximity', methods=['GET'])
def api_get_memorial_proximity(memorial_id):
    try:
        radius_feet = float(request.args.get('radius_feet', 100))
        cemetery_id = request.args.get('cemetery_id')
        result = geo_utils.find_nearby_burials(memorial_id, radius_feet=radius_feet, cemetery_id=cemetery_id)
        if 'error' in result and 'focus_memorial' not in result:
            return jsonify(result), 404
        return jsonify(result)
    except Exception as e:
        return jsonify({'error': f"Failed to compute proximity: {str(e)}"}), 500

def import_existing_stash():
    print("Scanning stash/ folder for existing stashed memorials...")
    cemetery_folders = glob.glob(f"{grave_digger.path_to_stash}/*_*/")
    imported_count = 0
    
    for cem_folder in cemetery_folders:
        folder_name = os.path.basename(os.path.normpath(cem_folder))
        parts = folder_name.split('_')
        cem_id = parts[0]
        
        nickname = "CEM"
        try:
            instructions = grave_digger.dig_instructions()
            for k in instructions.keys():
                if k.startswith(cem_id + '-'):
                    nickname = k.split('-')[1]
                    break
        except:
            pass
            
        cemetery_name = ' '.join(parts[1:]).replace('-', ' ').title()
        database.add_cemetery(cem_id, nickname, cemetery_name)
        
        # Walk subfolders to find HTML files
        html_files = glob.glob(f"{cem_folder}/**/*.html", recursive=True)
        for h_file in html_files:
            try:
                parent_dir = os.path.basename(os.path.dirname(h_file))
                group = 'burial'
                for g in ['parent', 'spouse', 'child', 'sibling', 'half-sibling']:
                    if g in parent_dir:
                        group = g
                        break
                        
                with open(h_file, 'r', encoding='utf-8') as f:
                    content = f.read()
                    
                filename = os.path.basename(h_file)
                mem_id = filename.split('_')[0]
                url = f"https://www.findagrave.com/memorial/{mem_id}"
                
                mem_data = grave_digger.parse_memorial_from_html(content, url)
                if not mem_data['cemetery_id']:
                    mem_data['cemetery_id'] = cem_id
                    
                database.save_memorial(mem_data)
                
                rels = grave_digger.extract_relationships_from_html(content, mem_data['id'])
                for r in rels:
                    database.save_relationship(r['from_id'], r['to_id'], r['type'])
                    
                imported_count += 1
            except Exception as ex:
                print(f"Error importing stashed file {h_file}: {ex}")
                
    print(f"Finished scanning stash/ folder. Imported/Synced {imported_count} stashed memorials.")

def cleanup_zombie_chrome():
    import psutil
    print("Checking for zombie automated Chrome processes...")
    killed_count = 0
    for proc in psutil.process_iter(['pid', 'name', 'cmdline']):
        try:
            if proc.info['name'] and 'chrome' in proc.info['name'].lower():
                cmdline = proc.info['cmdline']
                if cmdline:
                    is_automated = False
                    for arg in cmdline:
                        if '--remote-debugging-port' in arg or 'drissionpage' in arg.lower():
                            is_automated = True
                            break
                    if is_automated:
                        print(f"Killing zombie automated Chrome process (PID {proc.info['pid']})...")
                        proc.kill()
                        killed_count += 1
        except Exception as e:
            pass
    if killed_count > 0:
        print(f"Killed {killed_count} zombie Chrome process(es).")

@app.route('/photos/<path:filepath>')
def serve_photo(filepath):
    return send_from_directory('photos', filepath)

@app.route('/api/memorials/<memorial_id>/download-photos', methods=['POST'])
def api_download_memorial_photos(memorial_id):
    details = database.get_memorial_details(memorial_id)
    if not details:
        return jsonify({'error': 'Memorial not found'}), 404
        
    cemetery_id = details.get('cemetery_id')
    # Find stashed HTML file by searching the stash folder
    fpattern = os.path.join(grave_digger.path_to_stash, f"**/{memorial_id}_*.html")
    files = glob.glob(fpattern, recursive=True)
    if not files:
        fpattern2 = os.path.join(grave_digger.path_to_stash, f"**/{memorial_id}.html")
        files = glob.glob(fpattern2, recursive=True)
        
    if not files:
        return jsonify({'error': 'Stashed HTML page not found. Please scrape this cemetery/memorial first.'}), 404
        
    h_file = files[0]
    try:
        with open(h_file, 'r', encoding='utf-8') as f:
            html_content = f.read()
            
        import photos_downloader
        downloaded = photos_downloader.extract_and_download_photos(cemetery_id, memorial_id, html_content)
        
        # Scan folder for files to return updated list
        photos_list = []
        photos_dir = os.path.join('photos', str(memorial_id))
        if os.path.exists(photos_dir):
            for f_img in os.listdir(photos_dir):
                photos_list.append(f"/photos/{memorial_id}/{f_img}")
                
        photos_list.sort(key=lambda x: 0 if 'primary' in x else 1)
        return jsonify({'success': True, 'photos': photos_list})
    except Exception as e:
        return jsonify({'error': f"Failed to download photos: {str(e)}"}), 500

if __name__ == '__main__':
    try:
        cleanup_zombie_chrome()
    except Exception as e:
        print(f"Error cleaning up zombie Chrome processes: {e}")
    database.init_db()
    
    import threading
    try:
        t = threading.Thread(target=import_existing_stash)
        t.daemon = True
        t.start()
        print("Stash import thread started in background.")
    except Exception as e:
        print(f"Error starting background stash import thread: {e}")
        
    app.run(debug=False, host='127.0.0.1', port=5050)
