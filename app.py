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
    try:
        print(log_line)
    except UnicodeEncodeError:
        print(log_line.encode('ascii', errors='replace').decode('ascii'))

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

@app.route('/api/cemeteries/<cemetery_id>/rescrape', methods=['POST'])
def api_rescrape_cemetery(cemetery_id):
    try:
        data = request.get_json(silent=True) or {}
        max_pages = int(data.get('max_pages', 3))
        result = grave_digger.rescrape_cemetery_by_id(cemetery_id, max_pages=max_pages)
        if 'error' in result:
            return jsonify(result), 400
        return jsonify(result)
    except Exception as e:
        return jsonify({'error': f"Failed to re-sync cemetery {cemetery_id}: {str(e)}"}), 500

@app.route('/api/map/cemeteries', methods=['GET'])
def api_get_map_cemeteries():
    try:
        cemeteries = geo_utils.get_cemeteries_with_gps()
        return jsonify({'cemeteries': cemeteries, 'count': len(cemeteries)})
    except Exception as e:
        return jsonify({'error': f"Failed to fetch map cemeteries: {str(e)}"}), 500

@app.route('/api/map/kinship', methods=['GET'])
def api_get_map_kinship():
    try:
        focus_id = request.args.get('focus_id')
        generations = int(request.args.get('generations', 3))
        if not focus_id:
            return jsonify({'error': 'Missing focus_id parameter'}), 400
        result = geo_utils.get_kinship_map_data(focus_id=focus_id, max_generations=generations)
        if 'error' in result:
            return jsonify(result), 404
        return jsonify(result)
    except Exception as e:
        return jsonify({'error': f"Failed to compute kinship map: {str(e)}"}), 500

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

# --- County Discovery & External Sync Endpoints ---

county_scraper_status = {"running": False, "message": "Idle", "discovered": 0, "current_page": 0}

def discover_county_cemeteries_worker(state, county, location_id=None):
    global county_scraper_status
    county_scraper_status = {"running": True, "message": f"Starting discovery for {county}, {state}...", "discovered": 0, "current_page": 0}
    log_gui(f"🌐 [County Scraper] Starting cemetery discovery scan for {county}, {state}...")
    
    session = None
    try:
        from DrissionPage import ChromiumPage, ChromiumOptions
        co = ChromiumOptions().auto_port()
        co.set_argument('--headless=new')
        co.set_user_agent('Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36')
        co.set_argument('--blink-settings=imagesEnabled=false')
        co.set_timeouts(base=15, page_load=15)
        session = ChromiumPage(co)
        
        target_loc_id = location_id.strip() if location_id else None
        
        # Parse if full search URL was pasted
        if target_loc_id and 'locationId=' in target_loc_id:
            m = re.search(r'locationId=([^&]+)', target_loc_id)
            if m:
                target_loc_id = m.group(1)
                
        # Auto-detect locationId for Cabell County, WV if not provided
        if not target_loc_id:
            if state.lower().strip() in ('west virginia', 'wv') and 'cabell' in county.lower():
                target_loc_id = 'county_3069'
                
        if target_loc_id:
            log_gui(f"🌐 [County Scraper] Using locationId: {target_loc_id}")
        else:
            log_gui(f"🌐 [County Scraper] Resolving locationId for '{county}, {state}' via UI search...")
            session.get('https://www.findagrave.com/cemetery/search')
            time.sleep(3)
            
            loc_input = session.ele('#cemetery-loc') or session.ele('css:input[name="cemetery-loc"]')
            if loc_input:
                loc_input.input(f"{county}, {state}")
                time.sleep(2.5)
                try:
                    session.actions.key_down('DOWN').key_up('DOWN').key_down('ENTER').key_up('ENTER')
                    time.sleep(1.5)
                except Exception:
                    pass
                
                btn = session.ele('#cem-search-button') or session.ele('css:button[type="submit"]')
                if btn:
                    btn.click()
                    time.sleep(4)
                    m = re.search(r'locationId=([^&]+)', session.url)
                    if m:
                        target_loc_id = m.group(1)
                        log_gui(f"🌐 [County Scraper] UI resolved locationId: {target_loc_id}")

        loc_str = f"{county}, {state}, USA"
        page = 1
        max_pages = 100
        total_discovered = 0
        consecutive_empty_pages = 0
        
        while page <= max_pages:
            msg = f"Fetching search page {page} (Harvested so far: {total_discovered})..."
            county_scraper_status["current_page"] = page
            county_scraper_status["message"] = msg
            log_gui(f"🌐 [County Scraper] {msg}")
            
            if target_loc_id:
                search_url = f"https://www.findagrave.com/cemetery/search?cemetery-name=&cemetery-loc={requests.utils.quote(loc_str)}&only-with-cemeteries=cemOnly&locationId={target_loc_id}&page={page}"
            else:
                search_url = f"https://www.findagrave.com/cemetery/search?cemetery-name=&cemetery-loc={requests.utils.quote(loc_str)}&only-with-cemeteries=cemOnly&page={page}"
                
            session.get(search_url)
            
            tries = 3
            seen_page_ids = set()
            soup = None
            
            while tries > 0:
                time.sleep(2.0)
                try:
                    session.scroll.to_bottom()
                except Exception:
                    pass
                    
                html = session.html
                soup = BeautifulSoup(html, 'html.parser')
                
                cem_links = soup.find_all('a', href=re.compile(r'/cemetery/\d+'))
                if cem_links:
                    for link in cem_links:
                        href = link.get('href', '')
                        m = re.search(r'/cemetery/(\d+)', href)
                        if m:
                            seen_page_ids.add(m.group(1))
                    if seen_page_ids:
                        break
                tries -= 1
                if tries > 0:
                    log_gui(f"🌐 [County Scraper] Page {page} elements loading... retrying render (tries left: {tries})")
            
            if not seen_page_ids:
                consecutive_empty_pages += 1
                log_gui(f"🌐 [County Scraper] Warning: No cemetery links found on page {page} (Empty pages count: {consecutive_empty_pages}).")
                if consecutive_empty_pages >= 2:
                    log_gui(f"🌐 [County Scraper] Stopping scan after {consecutive_empty_pages} empty pages.")
                    break
            else:
                consecutive_empty_pages = 0
                
            cem_links = soup.find_all('a', href=re.compile(r'/cemetery/\d+')) if soup else []
            page_processed_ids = set()
            page_new_count = 0
            
            for link in cem_links:
                href = link.get('href', '')
                m = re.search(r'/cemetery/(\d+)', href)
                if not m:
                    continue
                cem_id = m.group(1)
                if cem_id in page_processed_ids:
                    continue
                page_processed_ids.add(cem_id)
                
                card = link.find_parent(['div', 'li', 'tr', 'td']) or link.parent
                card_text = card.get_text(separator=' ', strip=True) if card else ''
                
                name_val = link.text.strip() or f"Cemetery {cem_id}"
                
                burial_count = 0
                count_match = re.search(r'([\d,]+)\s*(?:graves|burials|memorials)', card_text, re.IGNORECASE)
                if count_match:
                    try:
                        burial_count = int(count_match.group(1).replace(',', ''))
                    except ValueError:
                        pass
                        
                gps_lat = None
                gps_lng = None
                gps_link = card.find('a', href=re.compile(r'destination=([0-9.-]+),([0-9.-]+)')) if card else None
                if gps_link:
                    g_match = re.search(r'destination=([0-9.-]+),([0-9.-]+)', gps_link.get('href'))
                    if g_match:
                        try:
                            gps_lat = float(g_match.group(1))
                            gps_lng = float(g_match.group(2))
                        except ValueError:
                            pass
                            
                c_loc_str = f"{county}, {state}, USA"
                database.upsert_county_cemetery(
                    cemetery_id=cem_id,
                    name=name_val,
                    location=c_loc_str,
                    state=state,
                    county=county,
                    gps_lat=gps_lat,
                    gps_lng=gps_lng,
                    fag_burial_count=burial_count
                )
                total_discovered += 1
                page_new_count += 1
                
            log_gui(f"🌐 [County Scraper] Page {page} complete: Processed {page_new_count} cemeteries (Total: {total_discovered}).")
            county_scraper_status["discovered"] = total_discovered
            page += 1
            
        county_scraper_status["running"] = False
        county_scraper_status["message"] = f"Finished discovery. Total cemeteries harvested: {total_discovered}"
        log_gui(f"🌐 [County Scraper] ✅ Discovery scan complete! Total cemeteries: {total_discovered}")
    except Exception as e:
        county_scraper_status["running"] = False
        county_scraper_status["message"] = f"Error during county discovery: {e}"
        log_gui(f"🌐 [County Scraper] ❌ Error during discovery: {e}")
    finally:
        if session:
            try:
                session.quit()
            except:
                pass

@app.route('/county')
def view_county():
    return render_template('county.html')

@app.route('/api/county/discover', methods=['POST'])
def api_county_discover():
    global county_scraper_status
    if county_scraper_status.get("running"):
        return jsonify({'error': 'County discovery scan is already running.'}), 400
        
    data = request.get_json() or {}
    state = data.get('state', '').strip()
    county = data.get('county', '').strip()
    location_id = data.get('location_id', '').strip()
    
    if not state or not county:
        return jsonify({'error': 'Please provide state and county.'}), 400
        
    t = threading.Thread(target=discover_county_cemeteries_worker, args=(state, county, location_id))
    t.daemon = True
    t.start()
    return jsonify({'success': True, 'message': f"Discovery thread started for {county}, {state}."})

@app.route('/api/county/discover_status')
def api_county_discover_status():
    return jsonify(county_scraper_status)

@app.route('/api/county/list')
def api_county_list():
    state = request.args.get('state', '').strip()
    county = request.args.get('county', '').strip()
    cemeteries = database.get_county_cemeteries(state, county)
    return jsonify({'cemeteries': cemeteries, 'count': len(cemeteries)})

@app.route('/api/county/add_custom', methods=['POST'])
def api_county_add_custom():
    data = request.get_json() or {}
    name = data.get('name', '').strip()
    state = data.get('state', '').strip()
    county = data.get('county', '').strip()
    location = data.get('location', '').strip()
    source_doc = data.get('source_doc', '').strip()
    notes = data.get('notes', '').strip()
    gps_lat = data.get('gps_lat')
    gps_lng = data.get('gps_lng')
    
    if not name or not state or not county:
        return jsonify({'error': 'Name, State, and County are required.'}), 400
        
    try:
        gps_lat = float(gps_lat) if gps_lat is not None and str(gps_lat).strip() != '' else None
        gps_lng = float(gps_lng) if gps_lng is not None and str(gps_lng).strip() != '' else None
    except ValueError:
        gps_lat, gps_lng = None, None
        
    cid = database.add_custom_cemetery(name, state, county, location, gps_lat, gps_lng, source_doc, notes)
    return jsonify({'success': True, 'id': cid, 'message': 'Custom cemetery created successfully.'})

@app.route('/api/county/link_fag', methods=['POST'])
def api_county_link_fag():
    data = request.get_json() or {}
    custom_id = data.get('custom_id', '').strip()
    fag_id = data.get('fag_id', '').strip()
    
    if not custom_id or not fag_id:
        return jsonify({'error': 'Custom ID and Find a Grave ID are required.'}), 400
        
    success, msg = database.link_custom_cemetery_to_fag(custom_id, fag_id)
    if success:
        return jsonify({'success': True, 'message': msg})
    else:
        return jsonify({'error': msg}), 400

@app.route('/api/county/update_sync', methods=['POST'])
def api_county_update_sync():
    data = request.get_json() or {}
    cemetery_id = data.get('cemetery_id', '').strip()
    if not cemetery_id:
        return jsonify({'error': 'Cemetery ID is required.'}), 400
        
    field_dict = data.get('fields', {})
    updated = database.update_cemetery_external_sync(cemetery_id, field_dict)
    if updated:
        return jsonify({'success': True, 'message': 'Sync state updated.'})
    else:
        return jsonify({'error': 'Failed to update sync state.'}), 400

@app.route('/api/county/export_csv')
def api_county_export_csv():
    import csv
    state = request.args.get('state', '').strip()
    county = request.args.get('county', '').strip()
    cemeteries = database.get_county_cemeteries(state, county)
    
    si = io.StringIO()
    cw = csv.writer(si)
    
    headers = [
        'Cemetery ID', 'Name', 'State', 'County', 'Location',
        'FAG Burial Count', 'Stashed Burial Count', 'GPS Lat', 'GPS Lng',
        'Is Historical/Custom', 'Source Documentation', 'FAG Added Date',
        'Wikidata QID', 'Wikidata Confirmed', 'Wikidata Date',
        'OSM ID', 'OSM Confirmed', 'OSM Date',
        'WikiTree ID', 'WikiTree Confirmed', 'WikiTree Date', 'Notes'
    ]
    cw.writerow(headers)
    
    for c in cemeteries:
        cw.writerow([
            c.get('id', ''),
            c.get('name', ''),
            c.get('state', ''),
            c.get('county', ''),
            c.get('location', ''),
            c.get('fag_burial_count', 0),
            c.get('stashed_burial_count', 0),
            c.get('gps_lat', ''),
            c.get('gps_lng', ''),
            'Yes' if c.get('is_custom') else 'No',
            c.get('source_doc', ''),
            c.get('fag_added_date', ''),
            c.get('wikidata_qid', ''),
            'Yes' if c.get('wikidata_confirmed') else 'No',
            c.get('wikidata_date', ''),
            c.get('osm_id', ''),
            'Yes' if c.get('osm_confirmed') else 'No',
            c.get('osm_date', ''),
            c.get('wikitree_id', ''),
            'Yes' if c.get('wikitree_confirmed') else 'No',
            c.get('wikitree_date', ''),
            c.get('notes', '')
        ])
        
    output_str = si.getvalue()
    filename = f"cemeteries_{county.lower().replace(' ', '_')}_{state.lower().replace(' ', '_')}.csv" if (county and state) else "county_cemeteries.csv"
    
    return send_file(
        io.BytesIO(output_str.encode('utf-8')),
        mimetype='text/csv',
        as_attachment=True,
        download_name=filename
    )

COUNTY_QIDS = {
    "cabell county": "Q508535",
    "cabell": "Q508535",
    "kanawha county": "Q498704",
    "kanawha": "Q498704",
    "wayne county": "Q513220",
    "wayne": "Q513220",
    "mason county": "Q495697",
    "mason": "Q495697",
    "putnam county": "Q507421",
    "putnam": "Q507421",
    "lincoln county": "Q490074",
    "lincoln": "Q490074",
}

@app.route('/api/county/quickstatements')
def api_county_quickstatements():
    state = request.args.get('state', '').strip()
    county = request.args.get('county', '').strip()
    cemetery_id = request.args.get('cemetery_id', '').strip()
    fmt = request.args.get('format', '').strip()
    
    if cemetery_id:
        cem = database.get_cemetery(cemetery_id)
        cemeteries = [cem] if cem else []
    else:
        cemeteries = database.get_county_cemeteries(state, county)
    
    lines = []
    for c in cemeteries:
        if not c:
            continue
            
        qid = (c.get('wikidata_qid') or '').strip().upper()
        if qid and not qid.startswith('Q'):
            qid = f"Q{qid}"
            
        subject = qid if qid else "LAST"
        
        if not qid:
            lines.append("CREATE")
            lines.append("LAST\tP31\tQ39614")  # Instance of: cemetery
            name = c.get('name') or f"Cemetery {c.get('id')}"
            lines.append(f'LAST\tLen\t"{name}"')
            
            c_county = c.get('county') or county
            c_state = c.get('state') or state
            desc_loc = f" in {c_county}, {c_state}" if (c_county or c_state) else ""
            lines.append(f'LAST\tDen\t"cemetery{desc_loc}"')

        # Find a Grave Cemetery ID (P2025)
        if not c.get('is_custom') and c.get('id'):
            lines.append(f'{subject}\tP2025\t"{c["id"]}"')
            
        # Located in administrative territorial entity (P131)
        c_county_str = (c.get('county') or county or '').lower().strip()
        county_qid = COUNTY_QIDS.get(c_county_str)
        if county_qid:
            lines.append(f'{subject}\tP131\t{county_qid}')
            
        # Coordinate Location (P625)
        if c.get('gps_lat') is not None and c.get('gps_lng') is not None:
            lines.append(f'{subject}\tP625\t@{c["gps_lat"]}/{c["gps_lng"]}')
            
        # OpenStreetMap Way ID (P10689) / Relation ID (P402) / Node ID (P11693)
        osm = (c.get('osm_id') or '').strip()
        if osm:
            clean_osm = re.sub(r'^[^\d]+', '', osm)
            if 'relation' in osm.lower():
                lines.append(f'{subject}\tP402\t"{clean_osm}"')
            elif 'node' in osm.lower():
                lines.append(f'{subject}\tP11693\t"{clean_osm}"')
            else:
                lines.append(f'{subject}\tP10689\t"{clean_osm}"')
                
        # WikiTree Category ID (P7755)
        wikitree = (c.get('wikitree_id') or '').strip()
        if wikitree:
            lines.append(f'{subject}\tP7755\t"{wikitree}"')
            
        lines.append("")
        
    qs_content = "\n".join(lines)
    
    if fmt == 'json':
        return jsonify({'success': True, 'cemetery_id': cemetery_id, 'quickstatements': qs_content})
        
    filename = f"quickstatements_{cemetery_id}.txt" if cemetery_id else (f"quickstatements_{county.lower().replace(' ', '_')}.txt" if county else "quickstatements.txt")
    
    return send_file(
        io.BytesIO(qs_content.encode('utf-8')),
        mimetype='text/plain',
        as_attachment=True,
        download_name=filename
    )


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
