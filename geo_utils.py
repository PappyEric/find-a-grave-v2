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

        # Fetch all GPS burials (optionally scoped to cemetery)
        all_gps_burials = get_burials_with_gps(cemetery_id=cemetery_id, conn=conn)

        nearby = []
        for b in all_gps_burials:
            b_id = str(b['id'])
            # Exclude focus memorial itself from nearby list
            if b_id == str(focus_memorial_id):
                continue

            dist = haversine_feet(f_lat, f_lng, b['gps_lat'], b['gps_lng'])
            if dist is not None and dist <= float(radius_feet):
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
            'radius_feet': float(radius_feet),
            'nearby_burials': nearby,
            'count': len(nearby)
        }
    finally:
        if close_conn:
            conn.close()
