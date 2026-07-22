import sqlite3
import os
import re
import xlsxwriter
from urllib.parse import unquote

DB_PATH = 'stash/find_a_grave_v2.db'

def get_db_conn():
    conn = sqlite3.connect(DB_PATH, timeout=30.0)
    conn.execute("PRAGMA journal_mode=WAL;")
    conn.execute("PRAGMA synchronous=NORMAL;")
    conn.execute("PRAGMA cache_size=-64000;")  # 64MB cache
    conn.row_factory = sqlite3.Row
    return conn

def init_db():
    os.makedirs('stash', exist_ok=True)
    conn = get_db_conn()
    try:
        cursor = conn.cursor()
        
        # 1. Cemeteries table
        cursor.execute("""
        CREATE TABLE IF NOT EXISTS cemeteries (
            id TEXT PRIMARY KEY,
            nickname TEXT NOT NULL,
            name TEXT,
            location TEXT,
            gps_lat REAL,
            gps_lng REAL,
            burials_discovered INTEGER DEFAULT 0
        );
        """)
        
        try:
            cursor.execute("ALTER TABLE cemeteries ADD COLUMN burials_discovered INTEGER DEFAULT 0;")
        except sqlite3.OperationalError:
            pass
        
        # 2. Memorials table
        cursor.execute("""
        CREATE TABLE IF NOT EXISTS memorials (
            id TEXT PRIMARY KEY,
            cemetery_id TEXT,
            name TEXT,
            surname TEXT,
            prefix TEXT,
            first_name TEXT,
            middle_name TEXT,
            maiden_name TEXT,
            last_name TEXT,
            suffix TEXT,
            nickname TEXT,
            birth_date TEXT,
            birth_location TEXT,
            death_date TEXT,
            death_location TEXT,
            veteran INTEGER,
            cenotaph INTEGER,
            plot TEXT,
            bio TEXT,
            gps_lat REAL,
            gps_lng REAL,
            inscription TEXT,
            gravesite_details TEXT,
            url TEXT,
            stashed_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (cemetery_id) REFERENCES cemeteries (id) ON DELETE CASCADE
        );
        """)
        
        # 3. Relationships table
        cursor.execute("""
        CREATE TABLE IF NOT EXISTS relationships (
            from_memorial_id TEXT,
            to_memorial_id TEXT,
            relationship_type TEXT,
            PRIMARY KEY (from_memorial_id, to_memorial_id, relationship_type),
            FOREIGN KEY (from_memorial_id) REFERENCES memorials (id) ON DELETE CASCADE
        );
        """)
        
        # 4. Download Queue table
        cursor.execute("""
        CREATE TABLE IF NOT EXISTS download_queue (
            url TEXT PRIMARY KEY,
            cemetery_id TEXT,
            group_name TEXT,
            status TEXT DEFAULT 'pending',
            attempts INTEGER DEFAULT 0,
            error_message TEXT,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (cemetery_id) REFERENCES cemeteries (id) ON DELETE CASCADE
        );
        """)
        
        # --- Performance Indexes ---
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_memorials_cemetery_id ON memorials(cemetery_id);")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_memorials_surname_name ON memorials(surname, name);")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_memorials_last_name ON memorials(last_name, surname, name);")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_memorials_gps ON memorials(gps_lat, gps_lng) WHERE gps_lat IS NOT NULL;")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_relationships_from_id ON relationships(from_memorial_id);")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_relationships_to_id ON relationships(to_memorial_id);")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_relationships_from_type ON relationships(from_memorial_id, relationship_type);")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_download_queue_cem_status ON download_queue(cemetery_id, status);")
        
        # Clean up legacy numeric memorial IDs that mistakenly populated surname column
        cursor.execute("UPDATE memorials SET surname = '' WHERE surname GLOB '[0-9]*';")
        
        conn.commit()
    finally:
        conn.close()

# --- Cemetery CRUD ---

def add_cemetery(cemetery_id, nickname=None, name=None, location=None, gps_lat=None, gps_lng=None):
    conn = get_db_conn()
    try:
        cursor = conn.cursor()
        db_nickname = str(cemetery_id)
        cursor.execute("""
            INSERT INTO cemeteries (id, nickname, name, location, gps_lat, gps_lng)
            VALUES (?, ?, ?, ?, ?, ?)
            ON CONFLICT(id) DO UPDATE SET
                nickname=excluded.nickname,
                name=coalesce(excluded.name, name),
                location=coalesce(excluded.location, location),
                gps_lat=coalesce(excluded.gps_lat, gps_lat),
                gps_lng=coalesce(excluded.gps_lng, gps_lng);
        """, (cemetery_id, db_nickname, name, location, gps_lat, gps_lng))
        conn.commit()
    finally:
        conn.close()

def get_cemeteries():
    conn = get_db_conn()
    try:
        cursor = conn.cursor()
        cursor.execute("""
            SELECT c.*, 
                   (SELECT count(*) FROM memorials WHERE cemetery_id = c.id) as burial_count
            FROM cemeteries c
            ORDER BY coalesce(c.name, c.id) ASC;
        """)
        rows = cursor.fetchall()
        return [dict(row) for row in rows]
    finally:
        conn.close()

def get_cemetery(cemetery_id):
    conn = get_db_conn()
    try:
        cursor = conn.cursor()
        cursor.execute("SELECT * FROM cemeteries WHERE id = ?;", (cemetery_id,))
        row = cursor.fetchone()
        return dict(row) if row else None
    finally:
        conn.close()

def delete_cemetery(cemetery_id):
    conn = get_db_conn()
    try:
        cursor = conn.cursor()
        cursor.execute("DELETE FROM cemeteries WHERE id = ?;", (cemetery_id,))
        conn.commit()
    finally:
        conn.close()

def set_burials_discovered(cemetery_id, discovered=1):
    conn = get_db_conn()
    try:
        cursor = conn.cursor()
        cursor.execute("UPDATE cemeteries SET burials_discovered = ? WHERE id = ?;", (discovered, cemetery_id))
        conn.commit()
    finally:
        conn.close()

def is_burials_discovered(cemetery_id):
    conn = get_db_conn()
    try:
        cursor = conn.cursor()
        cursor.execute("SELECT burials_discovered FROM cemeteries WHERE id = ?;", (cemetery_id,))
        row = cursor.fetchone()
        return (row[0] == 1) if row and row[0] is not None else False
    finally:
        conn.close()

# --- Memorial CRUD ---

def save_memorial(data):
    conn = get_db_conn()
    try:
        cursor = conn.cursor()
        cursor.execute("""
            INSERT INTO memorials (
                id, cemetery_id, name, surname, prefix, first_name, middle_name,
                maiden_name, last_name, suffix, nickname, birth_date, birth_location,
                death_date, death_location, veteran, cenotaph, plot, bio,
                gps_lat, gps_lng, inscription, gravesite_details, url
            ) VALUES (
                :id, :cemetery_id, :name, :surname, :prefix, :first_name, :middle_name,
                :maiden_name, :last_name, :suffix, :nickname, :birth_date, :birth_location,
                :death_date, :death_location, :veteran, :cenotaph, :plot, :bio,
                :gps_lat, :gps_lng, :inscription, :gravesite_details, :url
            ) ON CONFLICT(id) DO UPDATE SET
                cemetery_id=excluded.cemetery_id,
                name=excluded.name,
                surname=excluded.surname,
                prefix=excluded.prefix,
                first_name=excluded.first_name,
                middle_name=excluded.middle_name,
                maiden_name=excluded.maiden_name,
                last_name=excluded.last_name,
                suffix=excluded.suffix,
                nickname=excluded.nickname,
                birth_date=excluded.birth_date,
                birth_location=excluded.birth_location,
                death_date=excluded.death_date,
                death_location=excluded.death_location,
                veteran=excluded.veteran,
                cenotaph=excluded.cenotaph,
                plot=excluded.plot,
                bio=excluded.bio,
                gps_lat=excluded.gps_lat,
                gps_lng=excluded.gps_lng,
                inscription=excluded.inscription,
                gravesite_details=excluded.gravesite_details,
                url=excluded.url,
                stashed_at=CURRENT_TIMESTAMP;
        """, data)
        conn.commit()
    finally:
        conn.close()

def save_relationship(from_id, to_id, relationship_type):
    conn = get_db_conn()
    try:
        cursor = conn.cursor()
        cursor.execute("""
            INSERT OR IGNORE INTO relationships (from_memorial_id, to_memorial_id, relationship_type)
            VALUES (?, ?, ?);
        """, (from_id, to_id, relationship_type))
        conn.commit()
    finally:
        conn.close()

def get_memorials(cemetery_id=None, query=None, limit=100, offset=0):
    conn = get_db_conn()
    try:
        cursor = conn.cursor()
        
        sql = "SELECT m.*, c.nickname as cemetery_nickname FROM memorials m JOIN cemeteries c ON m.cemetery_id = c.id WHERE 1=1"
        params = []
        
        if cemetery_id:
            sql += " AND m.cemetery_id = ?"
            params.append(cemetery_id)
            
        if query:
            sql += " AND (m.name LIKE ? OR m.id = ? OR m.plot LIKE ?)"
            like_q = f"%{query}%"
            params.extend([like_q, query, like_q])
            
        sql += " ORDER BY coalesce(nullif(m.last_name, ''), nullif(m.surname, ''), m.name) ASC, m.name ASC LIMIT ? OFFSET ?"
        params.extend([limit, offset])
        
        cursor.execute(sql, params)
        rows = cursor.fetchall()
        
        # Get total count for pagination
        count_sql = "SELECT count(*) FROM memorials m JOIN cemeteries c ON m.cemetery_id = c.id WHERE 1=1"
        count_params = []
        if cemetery_id:
            count_sql += " AND m.cemetery_id = ?"
            count_params.append(cemetery_id)
        if query:
            count_sql += " AND (m.name LIKE ? OR m.id = ? OR m.plot LIKE ?)"
            count_params.extend([like_q, query, like_q])
            
        cursor.execute(count_sql, count_params)
        total_count = cursor.fetchone()[0]
        
        return [dict(row) for row in rows], total_count
    finally:
        conn.close()

def get_memorial_details(memorial_id):
    conn = get_db_conn()
    try:
        cursor = conn.cursor()
        cursor.execute("SELECT m.*, c.name as cemetery_name, c.nickname as cemetery_nickname FROM memorials m JOIN cemeteries c ON m.cemetery_id = c.id WHERE m.id = ?;", (memorial_id,))
        row = cursor.fetchone()
        if not row:
            return None
            
        memorial = dict(row)
        
        # Get relationships
        cursor.execute("""
            SELECT r.relationship_type, m.id, m.name, m.birth_date, m.death_date, m.cemetery_id, c.name as cemetery_name, c.nickname as cemetery_nickname
            FROM relationships r
            JOIN memorials m ON r.to_memorial_id = m.id
            LEFT JOIN cemeteries c ON m.cemetery_id = c.id
            WHERE r.from_memorial_id = ?;
        """, (memorial_id,))
        rel_rows = cursor.fetchall()
        
        relationships = {}
        for r in rel_rows:
            rtype = r['relationship_type']
            if rtype not in relationships:
                relationships[rtype] = []
            relationships[rtype].append({
                'id': r['id'],
                'name': r['name'],
                'birth': r['birth_date'] or 'unknown',
                'death': r['death_date'] or 'unknown',
                'cemetery_id': r['cemetery_id'],
                'cemetery': r['cemetery_name'] or r['cemetery_nickname'] or 'Unknown Cemetery'
            })
            
        memorial['relationships'] = relationships
        return memorial
    finally:
        conn.close()

# --- Download Queue Management ---

def enqueue_urls(urls, cemetery_id, group_name):
    conn = get_db_conn()
    try:
        cursor = conn.cursor()
        for url in urls:
            cursor.execute("""
                INSERT INTO download_queue (url, cemetery_id, group_name, status)
                VALUES (?, ?, ?, 'pending')
                ON CONFLICT(url) DO UPDATE SET
                    status = CASE WHEN status = 'failed' THEN 'pending' ELSE status END;
            """, (url, cemetery_id, group_name))
        conn.commit()
    finally:
        conn.close()

def get_next_queued_item(cemetery_id=None):
    conn = get_db_conn()
    try:
        cursor = conn.cursor()
        sql = "SELECT * FROM download_queue WHERE status = 'pending'"
        params = []
        if cemetery_id:
            sql += " AND cemetery_id = ?"
            params.append(cemetery_id)
        sql += " ORDER BY group_name = 'burial' DESC, url LIMIT 1;"
        
        cursor.execute(sql, params)
        row = cursor.fetchone()
        return dict(row) if row else None
    finally:
        conn.close()

def update_queue_status(url, status, error=None):
    conn = get_db_conn()
    try:
        cursor = conn.cursor()
        if status == 'completed':
            cursor.execute("DELETE FROM download_queue WHERE url = ?;", (url,))
        elif status == 'failed':
            cursor.execute("""
                UPDATE download_queue
                SET status = 'failed',
                    attempts = attempts + 1,
                    error_message = ?,
                    updated_at = CURRENT_TIMESTAMP
                WHERE url = ?;
            """, (error, url))
        else:
            cursor.execute("""
                UPDATE download_queue
                SET status = ?,
                    updated_at = CURRENT_TIMESTAMP
                WHERE url = ?;
            """, (status, url))
        conn.commit()
    finally:
        conn.close()

def get_queue_stats(cemetery_id=None):
    conn = get_db_conn()
    try:
        cursor = conn.cursor()
        sql = "SELECT status, count(*) as count FROM download_queue"
        params = []
        if cemetery_id:
            sql += " WHERE cemetery_id = ?"
            params.append(cemetery_id)
        sql += " GROUP BY status;"
        
        cursor.execute(sql, params)
        rows = cursor.fetchall()
        
        stats = {'pending': 0, 'downloading': 0, 'failed': 0}
        for r in rows:
            stats[r['status']] = r['count']
        return stats
    finally:
        conn.close()

def get_all_queue_stats():
    """
    Returns queue statistics aggregated for all cemeteries in a single fast query.
    Returns dict mapping cemetery_id -> {'pending': int, 'downloading': int, 'failed': int}
    """
    conn = get_db_conn()
    try:
        cursor = conn.cursor()
        cursor.execute("SELECT cemetery_id, status, count(*) as count FROM download_queue GROUP BY cemetery_id, status;")
        rows = cursor.fetchall()
        
        stats_map = {}
        for r in rows:
            cid = r['cemetery_id']
            if cid not in stats_map:
                stats_map[cid] = {'pending': 0, 'downloading': 0, 'failed': 0}
            stats_map[cid][r['status']] = r['count']
        return stats_map
    finally:
        conn.close()

def clear_queue(cemetery_id=None):
    conn = get_db_conn()
    try:
        cursor = conn.cursor()
        if cemetery_id:
            cursor.execute("DELETE FROM download_queue WHERE cemetery_id = ?;", (cemetery_id,))
        else:
            cursor.execute("DELETE FROM download_queue;")
        conn.commit()
    finally:
        conn.close()

# --- Demographics & Analytics ---

def get_analytics_summary(cemetery_id=None):
    """
    Computes demographic metrics, lifespan distributions, decade timelines,
    and surname breakdowns across all stashed records or scoped to a specific cemetery.
    """
    conn = get_db_conn()
    try:
        cursor = conn.cursor()
        
        is_scoped = bool(cemetery_id and str(cemetery_id).lower() != 'all')
        cem_filter = " WHERE cemetery_id = ?" if is_scoped else ""
        params = [str(cemetery_id)] if is_scoped else []
        
        cursor.execute(f"SELECT count(*) FROM memorials{cem_filter};", params)
        total_memorials = cursor.fetchone()[0]
        
        if is_scoped:
            cursor.execute("SELECT count(*) FROM cemeteries WHERE id = ?;", params)
            total_cemeteries = cursor.fetchone()[0]
        else:
            cursor.execute("SELECT count(*) FROM cemeteries;")
            total_cemeteries = cursor.fetchone()[0]
        
        vet_where = " WHERE veteran = 1 AND cemetery_id = ?" if is_scoped else " WHERE veteran = 1"
        cursor.execute(f"SELECT count(*) FROM memorials{vet_where};", params)
        total_veterans = cursor.fetchone()[0]
        
        ceno_where = " WHERE cenotaph = 1 AND cemetery_id = ?" if is_scoped else " WHERE cenotaph = 1"
        cursor.execute(f"SELECT count(*) FROM memorials{ceno_where};", params)
        total_cenotaphs = cursor.fetchone()[0]
        
        gps_where = " WHERE gps_lat IS NOT NULL AND gps_lng IS NOT NULL AND cemetery_id = ?" if is_scoped else " WHERE gps_lat IS NOT NULL AND gps_lng IS NOT NULL"
        cursor.execute(f"SELECT count(*) FROM memorials{gps_where};", params)
        total_gps = cursor.fetchone()[0]
        
        rel_sql = """
            SELECT count(*) FROM relationships r
            JOIN memorials m ON r.from_memorial_id = m.id
        """ + (" WHERE m.cemetery_id = ?" if is_scoped else "")
        cursor.execute(rel_sql, params)
        total_relationships = cursor.fetchone()[0]
        
        surname_sql = f"""
            SELECT coalesce(nullif(surname, ''), nullif(last_name, ''), 'Unknown') as sname, count(*) as cnt
            FROM memorials
            {cem_filter}
            GROUP BY sname
            ORDER BY cnt DESC
            LIMIT 10;
        """
        cursor.execute(surname_sql, params)
        top_surnames = [{'surname': r[0], 'count': r[1]} for r in cursor.fetchall()]
        
        dates_sql = f"SELECT birth_date, death_date FROM memorials WHERE (birth_date IS NOT NULL OR death_date IS NOT NULL)" + (" AND cemetery_id = ?" if is_scoped else "")
        cursor.execute(dates_sql, params)
        dates_rows = cursor.fetchall()
        
        ages = []
        birth_decades = {}
        death_decades = {}
        month_deaths = {m: 0 for m in ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec']}
        
        month_pattern = re.compile(r'\b(Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)[a-z]*\b', re.IGNORECASE)
        year_pattern = re.compile(r'\b(1[6-9]\d\d|20[0-2]\d)\b')
        
        for r in dates_rows:
            b_str = r['birth_date'] or ''
            d_str = r['death_date'] or ''
            
            b_yr_match = year_pattern.search(b_str)
            d_yr_match = year_pattern.search(d_str)
            
            b_yr = int(b_yr_match.group(1)) if b_yr_match else None
            d_yr = int(d_yr_match.group(1)) if d_yr_match else None
            
            if b_yr:
                decade = (b_yr // 10) * 10
                birth_decades[decade] = birth_decades.get(decade, 0) + 1
                
            if d_yr:
                decade = (d_yr // 10) * 10
                death_decades[decade] = death_decades.get(decade, 0) + 1
                
            if b_yr and d_yr and d_yr >= b_yr and (d_yr - b_yr) <= 120:
                ages.append(d_yr - b_yr)
                
            m_match = month_pattern.search(d_str)
            if m_match:
                m_abbr = m_match.group(1).capitalize()[:3]
                if m_abbr in month_deaths:
                    month_deaths[m_abbr] += 1

        avg_lifespan = round(sum(ages) / len(ages), 1) if ages else 0
        
        age_bins = {'0-17': 0, '18-35': 0, '36-50': 0, '51-65': 0, '66-80': 0, '81-95': 0, '96+': 0}
        for a in ages:
            if a <= 17: age_bins['0-17'] += 1
            elif a <= 35: age_bins['18-35'] += 1
            elif a <= 50: age_bins['36-50'] += 1
            elif a <= 65: age_bins['51-65'] += 1
            elif a <= 80: age_bins['66-80'] += 1
            elif a <= 95: age_bins['81-95'] += 1
            else: age_bins['96+'] += 1
            
        all_decades = sorted(list(set(list(birth_decades.keys()) + list(death_decades.keys()))))
        decade_data = []
        for dec in all_decades:
            decade_data.append({
                'decade': f"{dec}s",
                'births': birth_decades.get(dec, 0),
                'deaths': death_decades.get(dec, 0)
            })

        return {
            'cemetery_id': cemetery_id if is_scoped else 'all',
            'total_memorials': total_memorials,
            'total_cemeteries': total_cemeteries,
            'total_veterans': total_veterans,
            'veteran_percentage': round((total_veterans / total_memorials * 100), 1) if total_memorials else 0,
            'total_cenotaphs': total_cenotaphs,
            'total_gps': total_gps,
            'total_relationships': total_relationships,
            'average_lifespan': avg_lifespan,
            'top_surnames': top_surnames,
            'age_distribution': age_bins,
            'decades_timeline': decade_data,
            'month_deaths': month_deaths
        }
    finally:
        conn.close()

# --- Excel Export Integration ---

def db_to_excel(output_path='output/burials.xlsx'):
    os.makedirs('output', exist_ok=True)
    conn = get_db_conn()
    try:
        cursor = conn.cursor()
        
        cursor.execute("SELECT * FROM cemeteries;")
        cemeteries = cursor.fetchall()
        
        if not cemeteries:
            return False
            
        workbook = xlsxwriter.Workbook(output_path)
        workbook.set_size(1200, 800)
        
        format_bold = workbook.add_format({'bold': 1})
        format_wrap = workbook.add_format({'text_wrap': True})
        
        headers = [
            'Cemetery', 'Surname', 'Name', 
            'Prefix', 'First Name', 'Middle Name', 'Maiden Name', 'Last Name', 'Suffix', 'Nickname',
            'ID', 'Birth', 'Birth Location',
            'Death', 'Death Location', "Parent's Surname", 'Parents', 'Father', 'Mother',
            'Spouses', 'Children', 'Siblings', 'Half-siblings', 'Veteran', 'Cenotaph',
            'Plot', 'Bio', 'Google Map', 'Latitude', 'Longitude', 'Inscription', 'Gravesite Details'
        ]
        
        exceptions = ['jr', 'sr', 'i', 'ii', 'iii', 'iv', 'v', 'vi']
        
        def bold_last_name(full_name):
            if not full_name:
                return ['']
            parts = full_name.split(' ')
            num_parts = len(parts)
            suffix = ""
            if parts[num_parts-1].lower() in exceptions:
                suffix = " " + parts[num_parts-1]
                parts.remove(parts[num_parts-1])
                num_parts -= 1
            for i in range(len(parts)-1):
                parts[i] += ' '
            
            segments = []
            for p in parts[:-1]:
                segments.append(p)
            segments.append(format_bold)
            segments.append(parts[-1] + suffix)
            return segments

        for cem in cemeteries:
            cem_id = cem['id']
            
            worksheet = workbook.add_worksheet(str(cem_id))
            worksheet.ignore_errors({'number_stored_as_text': 'A1:XFD1048576'})
            worksheet.freeze_panes(1, 0)
            
            for col_idx, header in enumerate(headers):
                worksheet.write(0, col_idx, header, format_bold)
                
            cursor.execute("SELECT * FROM memorials WHERE cemetery_id = ? ORDER BY surname, name;", (cem_id,))
            memorials = cursor.fetchall()
            
            for row_idx, mem in enumerate(memorials, start=1):
                mem_id = mem['id']
                
                def write_link(col, url, label):
                    if url:
                        worksheet.write_url(row_idx, col, url, string=str(label))
                    else:
                        worksheet.write(row_idx, col, '')
                
                write_link(0, f"https://www.findagrave.com/cemetery/{cem_id}", cem_id)
                worksheet.write(row_idx, 1, mem['surname'] or '', format_wrap)
                
                name_segments = bold_last_name(mem['name'])
                if len(name_segments) > 1:
                    worksheet.write_rich_string(row_idx, 2, *name_segments, format_wrap)
                else:
                    worksheet.write(row_idx, 2, name_segments[0], format_wrap)
                    
                worksheet.write(row_idx, 3, mem['prefix'] or '', format_wrap)
                worksheet.write(row_idx, 4, mem['first_name'] or '', format_wrap)
                worksheet.write(row_idx, 5, mem['middle_name'] or '', format_wrap)
                worksheet.write(row_idx, 6, mem['maiden_name'] or '', format_wrap)
                worksheet.write(row_idx, 7, mem['last_name'] or '', format_wrap)
                worksheet.write(row_idx, 8, mem['suffix'] or '', format_wrap)
                worksheet.write(row_idx, 9, mem['nickname'] or '', format_wrap)
                
                write_link(10, mem['url'] or f"https://www.findagrave.com/memorial/{mem_id}", mem_id)
                
                worksheet.write(row_idx, 11, mem['birth_date'] or '', format_wrap)
                worksheet.write(row_idx, 12, mem['birth_location'] or '', format_wrap)
                worksheet.write(row_idx, 13, mem['death_date'] or '', format_wrap)
                worksheet.write(row_idx, 14, mem['death_location'] or '', format_wrap)
                
                cursor.execute("""
                    SELECT m.url FROM relationships r
                    JOIN memorials m ON r.to_memorial_id = m.id
                    WHERE r.from_memorial_id = ? AND r.relationship_type = 'parent';
                """, (mem_id,))
                parent_rows = cursor.fetchall()
                
                p_surname = ''
                if parent_rows:
                    father_url = parent_rows[0][0]
                    if father_url:
                        parts = father_url.split('/')
                        father_name_string = parts[-1]
                        parts = re.split('-|_', father_name_string)
                        p_surname = unquote(parts[-1].capitalize())
                worksheet.write(row_idx, 15, p_surname, format_wrap)
                
                def get_rich_relation_list(rtype):
                    cursor.execute("""
                        SELECT m.name, m.birth_date, m.death_date, c.id, c.name as cemetery_name, c.nickname
                        FROM relationships r
                        JOIN memorials m ON r.to_memorial_id = m.id
                        LEFT JOIN cemeteries c ON m.cemetery_id = c.id
                        WHERE r.from_memorial_id = ? AND r.relationship_type = ?;
                    """, (mem_id, rtype))
                    relatives = cursor.fetchall()
                    if not relatives:
                        return None
                        
                    segments = []
                    for i, rel in enumerate(relatives):
                        rel_name = rel['name']
                        birth = rel['birth_date'] or 'unknown'
                        death = rel['death_date'] or 'unknown'
                        c_name = rel['cemetery_name'] or rel['nickname'] or f"#{rel['id']}"
                        
                        name_parts = bold_last_name(rel_name)
                        segments.extend(name_parts)
                        
                        etc = f", {birth} - {death}, #{c_name}"
                        if i < len(relatives) - 1:
                            etc += "\n"
                        segments.append(etc)
                    return segments
                
                parents_segments = get_rich_relation_list('parent')
                if parents_segments:
                    worksheet.write_rich_string(row_idx, 16, *parents_segments, format_wrap)
                else:
                    worksheet.write(row_idx, 16, '')
                    
                cursor.execute("""
                    SELECT m.url, m.name FROM relationships r
                    JOIN memorials m ON r.to_memorial_id = m.id
                    WHERE r.from_memorial_id = ? AND r.relationship_type = 'parent';
                """, (mem_id,))
                parents_list = cursor.fetchall()
                
                if len(parents_list) >= 1:
                    write_link(17, parents_list[0][0], parents_list[0][1])
                else:
                    worksheet.write(row_idx, 17, '')
                    
                if len(parents_list) >= 2:
                    write_link(18, parents_list[1][0], parents_list[1][1])
                else:
                    worksheet.write(row_idx, 18, '')
                    
                for col_num, rtype in [(19, 'spouse'), (20, 'child'), (21, 'sibling'), (22, 'half-sibling')]:
                    rel_segments = get_rich_relation_list(rtype)
                    if rel_segments:
                        worksheet.write_rich_string(row_idx, col_num, *rel_segments, format_wrap)
                    else:
                        worksheet.write(row_idx, col_num, '')
                        
                worksheet.write(row_idx, 23, 'Y' if mem['veteran'] else '', format_wrap)
                worksheet.write(row_idx, 24, 'Y' if mem['cenotaph'] else '', format_wrap)
                worksheet.write(row_idx, 25, mem['plot'] or '', format_wrap)
                worksheet.write(row_idx, 26, mem['bio'] or '', format_wrap)
                
                gmap_url = ""
                if mem['gps_lat'] and mem['gps_lng']:
                    gmap_url = f"https://maps.google.com/?q={mem['gps_lat']},{mem['gps_lng']}"
                    write_link(27, gmap_url, 'map')
                else:
                    worksheet.write(row_idx, 27, '')
                    
                worksheet.write(row_idx, 28, mem['gps_lat'] or '')
                worksheet.write(row_idx, 29, mem['gps_lng'] or '')
                worksheet.write(row_idx, 30, mem['inscription'] or '', format_wrap)
                worksheet.write(row_idx, 31, mem['gravesite_details'] or '', format_wrap)

            worksheet.autofit()
            worksheet.set_column(12, 12, 35)
            worksheet.set_column(14, 14, 35)
            worksheet.set_column(26, 26, 40)
            worksheet.set_column(30, 30, 40)
            worksheet.set_column(31, 31, 40)
            
        workbook.close()
        return True
    finally:
        conn.close()
