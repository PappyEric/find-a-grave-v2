import math
import database

EARTH_RADIUS_FEET = 20902231.0  # Earth mean radius in feet (~6,371 km)

def haversine_feet(lat1, lon1, lat2, lon2):
    """
    Calculates the great-circle distance in feet between two points
    specified in decimal degrees (latitude, longitude).
    """
    if lat1 is None or lon1 is None or lat2 is None or lon2 is None:
        return None
        
    try:
        lat1, lon1, lat2, lon2 = float(lat1), float(lon1), float(lat2), float(lon2)
    except (ValueError, TypeError):
        return None

    phi1 = math.radians(lat1)
    phi2 = math.radians(lat2)
    delta_phi = math.radians(lat2 - lat1)
    delta_lambda = math.radians(lon2 - lon1)

    a = math.sin(delta_phi / 2.0)**2 + math.cos(phi1) * math.cos(phi2) * math.sin(delta_lambda / 2.0)**2
    c = 2.0 * math.atan2(math.sqrt(a), math.sqrt(1.0 - a))

    return round(EARTH_RADIUS_FEET * c, 2)

def get_burials_with_gps(cemetery_id=None, surname=None, conn=None):
    """
    Fetches stashed burials that have valid GPS coordinates.
    Optionally filters by cemetery_id and/or surname substring.
    """
    close_conn = False
    if conn is None:
        conn = database.get_db_conn()
        close_conn = True

    try:
        cursor = conn.cursor()
        sql = """
            SELECT m.id, m.name, m.surname, m.first_name, m.middle_name, m.maiden_name, m.last_name,
                   m.birth_date, m.death_date, m.plot, m.gps_lat, m.gps_lng, m.url,
                   c.id as cemetery_id, c.name as cemetery_name, c.location as cemetery_location
            FROM memorials m
            LEFT JOIN cemeteries c ON m.cemetery_id = c.id
            WHERE m.gps_lat IS NOT NULL AND m.gps_lng IS NOT NULL
        """
        params = []

        if cemetery_id and str(cemetery_id).lower() != 'all':
            sql += " AND m.cemetery_id = ?"
            params.append(str(cemetery_id))

        if surname and surname.strip():
            sql += " AND (m.surname LIKE ? OR m.last_name LIKE ? OR m.maiden_name LIKE ? OR m.name LIKE ?)"
            like_s = f"%{surname.strip()}%"
            params.extend([like_s, like_s, like_s, like_s])

        sql += " ORDER BY m.surname, m.name;"

        cursor.execute(sql, params)
        rows = cursor.fetchall()
        return [dict(row) for row in rows]
    finally:
        if close_conn:
            conn.close()

def get_cemeteries_with_gps(conn=None):
    """
    Fetches stashed cemeteries that have valid GPS coordinates.
    """
    close_conn = False
    if conn is None:
        conn = database.get_db_conn()
        close_conn = True

    try:
        cursor = conn.cursor()
        cursor.execute("""
            SELECT c.id, c.name, c.nickname, c.location, c.gps_lat, c.gps_lng,
                   (SELECT COUNT(*) FROM memorials m WHERE m.cemetery_id = c.id) as burial_count
            FROM cemeteries c
            WHERE c.gps_lat IS NOT NULL AND c.gps_lng IS NOT NULL
            ORDER BY c.name, c.id;
        """)
        rows = cursor.fetchall()
        return [dict(row) for row in rows]
    finally:
        if close_conn:
            conn.close()

def find_nearby_burials(focus_memorial_id, radius_feet=100.0, cemetery_id=None, conn=None):
    """
    Finds all stashed burials with GPS coordinates within radius_feet of a focus grave.
    Utilizes SQLite R*Tree spatial bounding-box indexing for sub-millisecond search performance.
    Returns focus grave info, radius, and sorted list of nearby burials with distance_feet.
    """
    close_conn = False
    if conn is None:
        conn = database.get_db_conn()
        close_conn = True

    try:
        focus_mem = database.get_memorial_details(str(focus_memorial_id))
        if not focus_mem:
            return {'error': f"Focus memorial ID {focus_memorial_id} not found."}

        f_lat = focus_mem.get('gps_lat')
        f_lng = focus_mem.get('gps_lng')
        if f_lat is None or f_lng is None:
            return {
                'error': f"Focus memorial '{focus_mem.get('name')}' (ID {focus_memorial_id}) does not have GPS coordinates stashed.",
                'focus_memorial': focus_mem
            }

        radius_f = float(radius_feet)
        margin = 1.05
        delta_lat = (radius_f / EARTH_RADIUS_FEET) * (180.0 / math.pi) * margin
        cos_lat = max(0.01, math.cos(math.radians(float(f_lat))))
        delta_lng = delta_lat / cos_lat

        min_lat = float(f_lat) - delta_lat
        max_lat = float(f_lat) + delta_lat
        min_lng = float(f_lng) - delta_lng
        max_lng = float(f_lng) + delta_lng

        # High-performance spatial R*Tree candidate pre-filtering
        candidate_burials = database.get_memorials_in_bbox(
            min_lat=min_lat, max_lat=max_lat,
            min_lng=min_lng, max_lng=max_lng,
            cemetery_id=cemetery_id,
            conn=conn
        )

        nearby = []
        for b in candidate_burials:
            b_id = str(b['id'])
            # Exclude focus memorial itself from nearby list
            if b_id == str(focus_memorial_id):
                continue

            dist = haversine_feet(f_lat, f_lng, b['gps_lat'], b['gps_lng'])
            if dist is not None and dist <= radius_f:
                b_copy = dict(b)
                b_copy['distance_feet'] = dist
                b_copy['distance_meters'] = round(dist * 0.3048, 2)
                nearby.append(b_copy)

        nearby.sort(key=lambda x: x['distance_feet'])

        return {
            'focus_memorial': {
                'id': focus_mem['id'],
                'name': focus_mem['name'],
                'cemetery_id': focus_mem['cemetery_id'],
                'cemetery_name': focus_mem.get('cemetery_name'),
                'gps_lat': f_lat,
                'gps_lng': f_lng,
                'plot': focus_mem.get('plot')
            },
            'radius_feet': radius_f,
            'nearby_burials': nearby,
            'count': len(nearby)
        }
    finally:
        if close_conn:
            conn.close()

def find_nearby_cemeteries(lat, lng, radius_feet=5280.0, conn=None):
    """
    Finds all stashed cemeteries with GPS coordinates within radius_feet of a given coordinate.
    Uses SQLite R*Tree spatial indexing.
    """
    if lat is None or lng is None:
        return []

    close_conn = False
    if conn is None:
        conn = database.get_db_conn()
        close_conn = True

    try:
        radius_f = float(radius_feet)
        margin = 1.05
        delta_lat = (radius_f / EARTH_RADIUS_FEET) * (180.0 / math.pi) * margin
        cos_lat = max(0.01, math.cos(math.radians(float(lat))))
        delta_lng = delta_lat / cos_lat

        min_lat = float(lat) - delta_lat
        max_lat = float(lat) + delta_lat
        min_lng = float(lng) - delta_lng
        max_lng = float(lng) + delta_lng

        candidates = database.get_cemeteries_in_bbox(min_lat, max_lat, min_lng, max_lng, conn=conn)
        results = []
        for c in candidates:
            dist = haversine_feet(lat, lng, c['gps_lat'], c['gps_lng'])
            if dist is not None and dist <= radius_f:
                c_copy = dict(c)
                c_copy['distance_feet'] = dist
                c_copy['distance_meters'] = round(dist * 0.3048, 2)
                c_copy['distance_miles'] = round(dist / 5280.0, 2)
                results.append(c_copy)

        results.sort(key=lambda x: x['distance_feet'])
        return results
    finally:
        if close_conn:
            conn.close()

def get_kinship_map_data(focus_id, max_generations=3, conn=None):
    """
    Crawls BFS family relationships up to max_generations from focus_id,
    extracting stashed GPS coordinates and categorizing relatives by kinship role.
    Also returns explicit edge flowline connections between relatives.
    """
    close_conn = False
    if conn is None:
        conn = database.get_db_conn()
        close_conn = True

    try:
        focus_mem = database.get_memorial_details(str(focus_id))
        if not focus_mem:
            return {'error': f"Focus memorial ID {focus_id} not found."}

        cursor = conn.cursor()
        
        visited = {str(focus_id): {'role': 'focus', 'role_label': 'Focus Person', 'depth': 0}}
        queue = [(str(focus_id), 0)]
        connections = []

        while queue:
            curr_id, depth = queue.pop(0)
            if depth >= max_generations:
                continue

            # Forward relationships
            cursor.execute("""
                SELECT to_memorial_id, relationship_type 
                FROM relationships 
                WHERE from_memorial_id = ?
            """, (curr_id,))
            rel_rows = cursor.fetchall()

            for r in rel_rows:
                t_id = str(r['to_memorial_id'])
                r_type = (r['relationship_type'] or '').lower()

                role = 'relative'
                role_label = r['relationship_type'].capitalize() if r['relationship_type'] else 'Relative'

                if r_type in ['father', 'mother', 'parent']:
                    role = 'parent'
                elif r_type in ['son', 'daughter', 'child']:
                    role = 'child'
                elif r_type in ['spouse', 'husband', 'wife']:
                    role = 'spouse'
                elif r_type in ['brother', 'sister', 'sibling', 'half-brother', 'half-sister']:
                    role = 'sibling'

                connections.append({'from_id': curr_id, 'to_id': t_id, 'relationship': r_type})

                if t_id not in visited:
                    visited[t_id] = {
                        'role': role,
                        'role_label': role_label,
                        'depth': depth + 1
                    }
                    queue.append((t_id, depth + 1))

            # Reverse relationships
            cursor.execute("""
                SELECT from_memorial_id, relationship_type 
                FROM relationships 
                WHERE to_memorial_id = ?
            """, (curr_id,))
            rev_rows = cursor.fetchall()

            for r in rev_rows:
                f_id = str(r['from_memorial_id'])
                r_type = (r['relationship_type'] or '').lower()

                role = 'relative'
                if r_type in ['father', 'mother', 'parent']:
                    role = 'child'
                    role_label = 'Child'
                elif r_type in ['son', 'daughter', 'child']:
                    role = 'parent'
                    role_label = 'Parent'
                elif r_type in ['spouse', 'husband', 'wife']:
                    role = 'spouse'
                    role_label = 'Spouse'
                elif r_type in ['brother', 'sister', 'sibling', 'half-brother', 'half-sister']:
                    role = 'sibling'
                    role_label = 'Sibling'

                connections.append({'from_id': f_id, 'to_id': curr_id, 'relationship': r_type})

                if f_id not in visited:
                    visited[f_id] = {
                        'role': role,
                        'role_label': role_label,
                        'depth': depth + 1
                    }
                    queue.append((f_id, depth + 1))

        # Fetch details and GPS coordinates for all visited relatives
        placeholders = ','.join(['?'] * len(visited))
        cursor.execute(f"""
            SELECT m.id, m.name, m.first_name, m.last_name, m.surname, m.maiden_name,
                   m.birth_date, m.death_date, m.plot, m.gps_lat, m.gps_lng,
                   c.id as cemetery_id, c.name as cemetery_name
            FROM memorials m
            LEFT JOIN cemeteries c ON m.cemetery_id = c.id
            WHERE m.id IN ({placeholders})
        """, list(visited.keys()))
        
        m_rows = cursor.fetchall()
        
        relatives = []
        f_lat = focus_mem.get('gps_lat')
        f_lng = focus_mem.get('gps_lng')

        for row in m_rows:
            m_id = str(row['id'])
            meta = visited.get(m_id, {})
            r_lat = row['gps_lat']
            r_lng = row['gps_lng']

            dist_ft = None
            if f_lat is not None and f_lng is not None and r_lat is not None and r_lng is not None:
                dist_ft = haversine_feet(f_lat, f_lng, r_lat, r_lng)

            relatives.append({
                'id': m_id,
                'name': row['name'] or f"Memorial #{m_id}",
                'birth_date': row['birth_date'],
                'death_date': row['death_date'],
                'plot': row['plot'],
                'gps_lat': r_lat,
                'gps_lng': r_lng,
                'cemetery_id': row['cemetery_id'],
                'cemetery_name': row['cemetery_name'] or 'Unknown',
                'role': meta.get('role', 'relative'),
                'role_label': meta.get('role_label', 'Relative'),
                'depth': meta.get('depth', 0),
                'distance_feet': dist_ft,
                'distance_miles': round(dist_ft / 5280.0, 2) if dist_ft is not None else None,
                'has_gps': (r_lat is not None and r_lng is not None)
            })

        return {
            'focus_memorial': {
                'id': focus_mem['id'],
                'name': focus_mem['name'],
                'gps_lat': f_lat,
                'gps_lng': f_lng,
                'cemetery_name': focus_mem.get('cemetery_name')
            },
            'max_generations': max_generations,
            'relatives': relatives,
            'connections': connections,
            'total_mapped': len([r for r in relatives if r['has_gps']])
        }
    finally:
        if close_conn:
            conn.close()
