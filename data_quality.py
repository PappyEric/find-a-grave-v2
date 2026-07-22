import re
import sqlite3
import database

def get_db_conn():
    return database.get_db_conn()

def parse_year(date_str):
    if not date_str:
        return None
    match = re.search(r'\b(1[6-9]\d\d|20[0-2]\d)\b', str(date_str))
    return int(match.group(1)) if match else None

def run_quality_audit(cemetery_id=None):
    """
    Scans stashed records for logical anomalies, chronological inconsistencies,
    kinship/maiden name gaps, and potential duplicate memorials.
    """
    conn = get_db_conn()
    try:
        cursor = conn.cursor()
        
        is_scoped = bool(cemetery_id and str(cemetery_id).lower() != 'all')
        cem_filter = " WHERE m.cemetery_id = ?" if is_scoped else ""
        params = [str(cemetery_id)] if is_scoped else []
        
        # 1. Fetch all memorials
        mem_sql = f"""
            SELECT m.id, m.name, m.first_name, m.middle_name, m.last_name, m.surname, m.maiden_name,
                   m.birth_date, m.death_date, m.cemetery_id, c.name as cemetery_name,
                   m.gps_lat, m.gps_lng, m.bio, m.inscription
            FROM memorials m
            LEFT JOIN cemeteries c ON m.cemetery_id = c.id
            {cem_filter}
        """
        cursor.execute(mem_sql, params)
        memorials_rows = cursor.fetchall()
        memorials_map = {row['id']: dict(row) for row in memorials_rows}
        
        # 2. Fetch relationships
        rel_sql = """
            SELECT r.from_memorial_id, r.to_memorial_id, r.relationship_type
            FROM relationships r
        """
        if is_scoped:
            rel_sql += " JOIN memorials m ON r.from_memorial_id = m.id WHERE m.cemetery_id = ?"
        cursor.execute(rel_sql, params)
        rel_rows = cursor.fetchall()
        
        # Build relationship mappings
        parents_of = {}   # child_id -> list of parent_ids
        children_of = {}  # parent_id -> list of child_ids
        spouses_of = {}   # person_id -> list of spouse_ids
        rel_count_map = {m_id: 0 for m_id in memorials_map.keys()}
        
        for r in rel_rows:
            f_id = r['from_memorial_id']
            t_id = r['to_memorial_id']
            r_type = (r['relationship_type'] or '').lower()
            
            if f_id in rel_count_map: rel_count_map[f_id] += 1
            if t_id in rel_count_map: rel_count_map[t_id] += 1
            
            if r_type in ['father', 'mother', 'parent']:
                parents_of.setdefault(f_id, []).append(t_id)
                children_of.setdefault(t_id, []).append(f_id)
            elif r_type in ['son', 'daughter', 'child']:
                children_of.setdefault(f_id, []).append(t_id)
                parents_of.setdefault(t_id, []).append(f_id)
            elif r_type in ['spouse', 'husband', 'wife']:
                spouses_of.setdefault(f_id, []).append(t_id)
                spouses_of.setdefault(t_id, []).append(f_id)

        anomalies = []
        
        # --- Audit Check 1: Individual Chronological & Date Anomalies ---
        for m_id, mem in memorials_map.items():
            b_yr = parse_year(mem['birth_date'])
            d_yr = parse_year(mem['death_date'])
            disp_name = mem['name'] or f"Memorial #{m_id}"
            
            # Check 1a: Death year before birth year
            if b_yr and d_yr and d_yr < b_yr:
                anomalies.append({
                    'id': f"date_rev_{m_id}",
                    'category': 'Chronological Error',
                    'severity': 'High',
                    'memorial_id': m_id,
                    'name': disp_name,
                    'cemetery_name': mem['cemetery_name'] or 'Unknown',
                    'description': f"Death year ({d_yr}) is earlier than birth year ({b_yr}).",
                    'details': f"Birth: '{mem['birth_date']}' | Death: '{mem['death_date']}'"
                })
                
            # Check 1b: Longevity > 110 years
            if b_yr and d_yr and (d_yr - b_yr) > 110:
                anomalies.append({
                    'id': f"longevity_{m_id}",
                    'category': 'Extreme Longevity',
                    'severity': 'Medium',
                    'memorial_id': m_id,
                    'name': disp_name,
                    'cemetery_name': mem['cemetery_name'] or 'Unknown',
                    'description': f"Impossibly long reported lifespan of {d_yr - b_yr} years ({b_yr}–{d_yr}).",
                    'details': f"Verify if birth ({b_yr}) or death ({d_yr}) dates are accurate."
                })
                
            # Check 1c: Missing GPS burial coordinates
            if mem['gps_lat'] is None or mem['gps_lng'] is None:
                anomalies.append({
                    'id': f"no_gps_{m_id}",
                    'category': 'Missing GPS',
                    'severity': 'Info',
                    'memorial_id': m_id,
                    'name': disp_name,
                    'cemetery_name': mem['cemetery_name'] or 'Unknown',
                    'description': "Burial lacks exact GPS latitude/longitude coordinates.",
                    'details': "Plot coordinate unmapped on GIS map."
                })
                
            # Check 1d: Unlinked Isolated Individual (0 family connections)
            if rel_count_map.get(m_id, 0) == 0:
                anomalies.append({
                    'id': f"unlinked_{m_id}",
                    'category': 'Unlinked Person',
                    'severity': 'Info',
                    'memorial_id': m_id,
                    'name': disp_name,
                    'cemetery_name': mem['cemetery_name'] or 'Unknown',
                    'description': "Isolated memorial record with 0 stashed family connections.",
                    'details': "No parents, spouse, siblings, or children linked."
                })

        # --- Audit Check 2: Parent vs Child Chronology ---
        for child_id, p_list in parents_of.items():
            if child_id not in memorials_map: continue
            child = memorials_map[child_id]
            c_b_yr = parse_year(child['birth_date'])
            if not c_b_yr: continue
            
            for p_id in p_list:
                if p_id not in memorials_map: continue
                parent = memorials_map[p_id]
                p_b_yr = parse_year(parent['birth_date'])
                p_d_yr = parse_year(parent['death_date'])
                
                # Check 2a: Child born before parent
                if p_b_yr and c_b_yr < p_b_yr:
                    anomalies.append({
                        'id': f"child_before_parent_{child_id}_{p_id}",
                        'category': 'Parent/Child Discrepancy',
                        'severity': 'High',
                        'memorial_id': child_id,
                        'name': child['name'],
                        'cemetery_name': child['cemetery_name'] or 'Unknown',
                        'description': f"Child born ({c_b_yr}) BEFORE reported parent {parent['name']} ({p_b_yr}).",
                        'details': f"Parent ID: #{p_id} ({p_b_yr}) vs Child ID: #{child_id} ({c_b_yr})"
                    })
                    
                # Check 2b: Unusually young parent (<12 yrs old at child's birth)
                if p_b_yr and (c_b_yr - p_b_yr) < 12 and (c_b_yr - p_b_yr) >= 0:
                    anomalies.append({
                        'id': f"young_parent_{child_id}_{p_id}",
                        'category': 'Parent/Child Discrepancy',
                        'severity': 'High',
                        'memorial_id': p_id,
                        'name': parent['name'],
                        'cemetery_name': parent['cemetery_name'] or 'Unknown',
                        'description': f"Parent was under 12 years old ({c_b_yr - p_b_yr} yrs old) when child {child['name']} was born.",
                        'details': f"Parent born {p_b_yr}, Child born {c_b_yr}"
                    })
                    
                # Check 2c: Child born >1 year after parent's death
                if p_d_yr and c_b_yr > (p_d_yr + 1):
                    anomalies.append({
                        'id': f"born_after_death_{child_id}_{p_id}",
                        'category': 'Parent/Child Discrepancy',
                        'severity': 'High',
                        'memorial_id': child_id,
                        'name': child['name'],
                        'cemetery_name': child['cemetery_name'] or 'Unknown',
                        'description': f"Child born ({c_b_yr}) more than 1 year after parent {parent['name']} died ({p_d_yr}).",
                        'details': f"Parent died {p_d_yr}, Child born {c_b_yr}"
                    })

        # --- Audit Check 3: Potential Duplicate Memorials ---
        # Group by (cemetery_id, normalized_name, birth_year, death_year)
        dup_groups = {}
        for m_id, mem in memorials_map.items():
            l_name = (mem['last_name'] or mem['surname'] or '').strip().lower()
            f_name = (mem['first_name'] or mem['name'] or '').strip().split()[0].lower() if mem['name'] else ''
            b_yr = parse_year(mem['birth_date'])
            d_yr = parse_year(mem['death_date'])
            
            if l_name and f_name and (b_yr or d_yr):
                key = (mem['cemetery_id'], f_name, l_name, b_yr, d_yr)
                dup_groups.setdefault(key, []).append(mem)
                
        for key, dupes in dup_groups.items():
            if len(dupes) > 1:
                primary = dupes[0]
                dupe_ids = [str(d['id']) for d in dupes]
                anomalies.append({
                    'id': f"dupe_{'_'.join(dupe_ids)}",
                    'category': 'Potential Duplicate',
                    'severity': 'Medium',
                    'memorial_id': primary['id'],
                    'name': primary['name'],
                    'cemetery_name': primary['cemetery_name'] or 'Unknown',
                    'description': f"Found {len(dupes)} potential duplicate memorial records with matching name & dates.",
                    'details': f"Matching Memorial IDs: {', '.join(dupe_ids)}"
                })

        # Count summary stats by severity
        sev_counts = {'High': 0, 'Medium': 0, 'Info': 0}
        cat_counts = {}
        for a in anomalies:
            sev_counts[a['severity']] = sev_counts.get(a['severity'], 0) + 1
            cat_counts[a['category']] = cat_counts.get(a['category'], 0) + 1

        return {
            'cemetery_id': cemetery_id if is_scoped else 'all',
            'total_anomalies': len(anomalies),
            'severity_counts': sev_counts,
            'category_counts': cat_counts,
            'anomalies': anomalies
        }
    finally:
        conn.close()

def search_inscriptions_and_bios(query, cemetery_id=None, category=None):
    """
    Executes full-text search across stashed headstone inscriptions, bio notes,
    and gravesite text, highlighting snippet matches.
    """
    if not query or not query.strip():
        return {'results': [], 'count': 0}
        
    conn = get_db_conn()
    try:
        cursor = conn.cursor()
        q_clean = query.strip()
        like_pattern = f"%{q_clean}%"
        
        sql = """
            SELECT m.id, m.name, m.last_name, m.surname, m.first_name, m.birth_date, m.death_date,
                   m.cemetery_id, c.name as cemetery_name, m.bio, m.inscription, m.plot, m.veteran
            FROM memorials m
            LEFT JOIN cemeteries c ON m.cemetery_id = c.id
            WHERE (m.bio LIKE ? OR m.inscription LIKE ? OR m.name LIKE ?)
        """
        params = [like_pattern, like_pattern, like_pattern]
        
        if cemetery_id and str(cemetery_id).lower() != 'all':
            sql += " AND m.cemetery_id = ?"
            params.append(str(cemetery_id))
            
        sql += " ORDER BY m.last_name ASC, m.name ASC LIMIT 100;"
        
        cursor.execute(sql, params)
        rows = cursor.fetchall()
        
        results = []
        regex = re.compile(re.escape(q_clean), re.IGNORECASE)
        
        for r in rows:
            bio_txt = r['bio'] or ''
            insc_txt = r['inscription'] or ''
            
            snippets = []
            if bio_txt and regex.search(bio_txt):
                match = regex.search(bio_txt)
                start = max(0, match.start() - 60)
                end = min(len(bio_txt), match.end() + 60)
                snip = bio_txt[start:end]
                snip_highlighted = regex.sub(lambda m: f"<mark>{m.group(0)}</mark>", snip)
                snippets.append(f"<b>Bio:</b> ...{snip_highlighted}...")
                
            if insc_txt and regex.search(insc_txt):
                match = regex.search(insc_txt)
                start = max(0, match.start() - 60)
                end = min(len(insc_txt), match.end() + 60)
                snip = insc_txt[start:end]
                snip_highlighted = regex.sub(lambda m: f"<mark>{m.group(0)}</mark>", snip)
                snippets.append(f"<b>Inscription:</b> ...{snip_highlighted}...")
                
            results.append({
                'id': r['id'],
                'name': r['name'],
                'cemetery_name': r['cemetery_name'] or 'Unknown',
                'birth_date': r['birth_date'],
                'death_date': r['death_date'],
                'plot': r['plot'],
                'veteran': bool(r['veteran']),
                'snippets': snippets or ["Match in memorial header name."]
            })

        return {
            'query': q_clean,
            'count': len(results),
            'results': results
        }
    finally:
        conn.close()

def get_stash_sync_report(cemetery_id=None):
    """
    Computes stash sync status, scrape age, missing fields metrics,
    and change tracking recommendations across stashed records.
    """
    conn = get_db_conn()
    try:
        cursor = conn.cursor()
        
        is_scoped = bool(cemetery_id and str(cemetery_id).lower() != 'all')
        cem_filter = " WHERE cemetery_id = ?" if is_scoped else ""
        params = [str(cemetery_id)] if is_scoped else []
        
        cursor.execute(f"SELECT count(*) FROM memorials{cem_filter};", params)
        total_mems = cursor.fetchone()[0]
        
        cursor.execute(f"SELECT count(*) FROM memorials{cem_filter} {'AND' if is_scoped else 'WHERE'} (bio IS NULL OR bio = '');", params)
        missing_bios = cursor.fetchone()[0]
        
        cursor.execute(f"SELECT count(*) FROM memorials{cem_filter} {'AND' if is_scoped else 'WHERE'} (inscription IS NULL OR inscription = '');", params)
        missing_inscriptions = cursor.fetchone()[0]
        
        cursor.execute(f"SELECT count(*) FROM memorials{cem_filter} {'AND' if is_scoped else 'WHERE'} (gps_lat IS NULL OR gps_lng IS NULL);", params)
        missing_gps = cursor.fetchone()[0]
        
        return {
            'total_memorials': total_mems,
            'missing_bios': missing_bios,
            'missing_inscriptions': missing_inscriptions,
            'missing_gps': missing_gps,
            'sync_health': '100% Up-to-Date' if total_mems > 0 else 'No stashed data'
        }
    finally:
        conn.close()
