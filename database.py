import sqlite3
import os
import re
import xlsxwriter
from urllib.parse import unquote

import struct

DB_PATH = 'stash/find_a_grave_spatial.gpkg'
GPKG_PATH = 'stash/find_a_grave_spatial.gpkg'

def make_gpkg_point(lng, lat):
    """
    Encodes (lng, lat) into an OGC GeoPackage standard binary Point geometry (EPSG:4326).
    Header (8 bytes) + WKB Point (21 bytes) = 29 bytes.
    Recognized natively by QGIS and ArcGIS as vector points.
    """
    if lng is None or lat is None:
        return None
    try:
        lng_f, lat_f = float(lng), float(lat)
        if lng_f == 0 and lat_f == 0:
            return None
        header = struct.pack('<2sBB i', b'GP', 0, 1, 4326)
        wkb = struct.pack('<B I d d', 1, 1, lng_f, lat_f)
        return header + wkb
    except Exception:
        return None

def get_db_conn():
    conn = sqlite3.connect(DB_PATH, timeout=30.0)
    conn.execute("PRAGMA journal_mode=WAL;")
    conn.execute("PRAGMA synchronous=NORMAL;")
    conn.execute("PRAGMA cache_size=-64000;")  # 64MB cache
    conn.row_factory = sqlite3.Row
    conn.create_function('gpkg_point', 2, make_gpkg_point)
    return conn

def init_db():
    os.makedirs('stash', exist_ok=True)
    conn = get_db_conn()
    try:
        cursor = conn.cursor()
        
        # 1. GeoPackage Application ID & Pragma
        cursor.execute("PRAGMA application_id = 1196444487;")  # 'GPKG'
        cursor.execute("PRAGMA user_version = 10300;")          # GeoPackage 1.3
        
        # 2. GeoPackage Metadata Tables (for QGIS & ArcGIS Point layer support)
        cursor.execute("""
        CREATE TABLE IF NOT EXISTS gpkg_spatial_ref_sys (
            srs_name TEXT NOT NULL,
            srs_id INTEGER NOT NULL PRIMARY KEY,
            organization TEXT NOT NULL,
            organization_coordsys_id INTEGER NOT NULL,
            definition TEXT NOT NULL,
            description TEXT
        );
        """)
        
        cursor.execute("""
        INSERT OR IGNORE INTO gpkg_spatial_ref_sys (srs_name, srs_id, organization, organization_coordsys_id, definition, description)
        VALUES 
        ('WGS 84 geodetic', 4326, 'EPSG', 4326, 'GEOGCS["WGS 84",DATUM["WGS_1984",SPHEROID["WGS 84",6378137,298.257223563,AUTHORITY["EPSG","6326"]],AUTHORITY["EPSG","6326"]],PRIMEM["Greenwich",0,AUTHORITY["EPSG","8901"]],UNIT["degree",0.0174532925199433,AUTHORITY["EPSG","9122"]],AXIS["Latitude",NORTH],AXIS["Longitude",EAST],AUTHORITY["EPSG","4326"]]', 'longitude/latitude coordinates in decimal degrees on the WGS 84 spheroid'),
        ('Undefined cartesian SRS', -1, 'NONE', -1, 'undefined', 'undefined cartesian coordinate reference system'),
        ('Undefined geographic SRS', 0, 'NONE', 0, 'undefined', 'undefined geographic coordinate reference system');
        """)
        
        cursor.execute("""
        CREATE TABLE IF NOT EXISTS gpkg_contents (
            table_name TEXT NOT NULL PRIMARY KEY,
            data_type TEXT NOT NULL,
            identifier TEXT UNIQUE,
            description TEXT DEFAULT '',
            last_change DATETIME NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
            min_x DOUBLE,
            min_y DOUBLE,
            max_x DOUBLE,
            max_y DOUBLE,
            srs_id INTEGER,
            CONSTRAINT fk_gc_r_srs_id FOREIGN KEY (srs_id) REFERENCES gpkg_spatial_ref_sys(srs_id)
        );
        """)
        
        cursor.execute("""
        CREATE TABLE IF NOT EXISTS gpkg_geometry_columns (
            table_name TEXT NOT NULL,
            column_name TEXT NOT NULL,
            geometry_type_name TEXT NOT NULL,
            srs_id INTEGER NOT NULL,
            z TINYINT NOT NULL,
            m TINYINT NOT NULL,
            CONSTRAINT pk_geom_cols PRIMARY KEY (table_name, column_name),
            CONSTRAINT fk_gc_tn FOREIGN KEY (table_name) REFERENCES gpkg_contents(table_name),
            CONSTRAINT fk_gc_srs FOREIGN KEY (srs_id) REFERENCES gpkg_spatial_ref_sys(srs_id)
        );
        """)

        # 3. Cemeteries table
        cursor.execute("""
        CREATE TABLE IF NOT EXISTS cemeteries (
            id TEXT PRIMARY KEY,
            nickname TEXT NOT NULL,
            name TEXT,
            location TEXT,
            gps_lat REAL,
            gps_lng REAL,
            burials_discovered INTEGER DEFAULT 0,
            geom BLOB
        );
        """)
        
        new_cols = [
            ("state", "TEXT"),
            ("county", "TEXT"),
            ("fag_burial_count", "INTEGER DEFAULT 0"),
            ("is_custom", "INTEGER DEFAULT 0"),
            ("source_doc", "TEXT DEFAULT ''"),
            ("fag_added_date", "TEXT DEFAULT ''"),
            ("wikidata_qid", "TEXT DEFAULT ''"),
            ("wikidata_confirmed", "INTEGER DEFAULT 0"),
            ("wikidata_date", "TEXT DEFAULT ''"),
            ("osm_id", "TEXT DEFAULT ''"),
            ("osm_confirmed", "INTEGER DEFAULT 0"),
            ("osm_date", "TEXT DEFAULT ''"),
            ("wikitree_id", "TEXT DEFAULT ''"),
            ("wikitree_confirmed", "INTEGER DEFAULT 0"),
            ("wikitree_date", "TEXT DEFAULT ''"),
            ("notes", "TEXT DEFAULT ''"),
            ("burials_discovered", "INTEGER DEFAULT 0"),
            ("geom", "BLOB")
        ]
        for col_name, col_type in new_cols:
            try:
                cursor.execute(f"ALTER TABLE cemeteries ADD COLUMN {col_name} {col_type};")
            except sqlite3.OperationalError:
                pass
        
        # 4. Memorials table
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
            geom BLOB,
            FOREIGN KEY (cemetery_id) REFERENCES cemeteries (id) ON DELETE CASCADE
        );
        """)
        
        try:
            cursor.execute("ALTER TABLE memorials ADD COLUMN geom BLOB;")
        except sqlite3.OperationalError:
            pass

        # Register GeoPackage Feature Layers
        cursor.execute("INSERT OR REPLACE INTO gpkg_geometry_columns VALUES ('cemeteries', 'geom', 'POINT', 4326, 0, 0);")
        cursor.execute("INSERT OR REPLACE INTO gpkg_geometry_columns VALUES ('memorials', 'geom', 'POINT', 4326, 0, 0);")
        cursor.execute("""
        INSERT OR REPLACE INTO gpkg_contents (table_name, data_type, identifier, description, last_change, srs_id)
        VALUES 
        ('cemeteries', 'features', 'cemeteries', 'Find a Grave Cemeteries Point Layer', datetime('now'), 4326),
        ('memorials', 'features', 'memorials', 'Find a Grave Memorials Point Layer', datetime('now'), 4326);
        """)

        # 5. Relationships table
        cursor.execute("""
        CREATE TABLE IF NOT EXISTS relationships (
            from_memorial_id TEXT,
            to_memorial_id TEXT,
            relationship_type TEXT,
            PRIMARY KEY (from_memorial_id, to_memorial_id, relationship_type),
            FOREIGN KEY (from_memorial_id) REFERENCES memorials (id) ON DELETE CASCADE
        );
        """)
        
        # 6. Download Queue table
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
        
        # 7. Place QIDs Cache table for Wikidata QuickStatements
        cursor.execute("""
        CREATE TABLE IF NOT EXISTS place_qids (
            place_name TEXT PRIMARY KEY,
            qid TEXT NOT NULL,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );
        """)

        # 8. Spatial R*Tree Virtual Tables
        cursor.execute("""
        CREATE VIRTUAL TABLE IF NOT EXISTS memorials_spatial_idx USING rtree(
            id,              -- matches memorials.rowid
            min_lng, max_lng,
            min_lat, max_lat
        );
        """)
        
        cursor.execute("""
        CREATE VIRTUAL TABLE IF NOT EXISTS cemeteries_spatial_idx USING rtree(
            id,              -- matches cemeteries.rowid
            min_lng, max_lng,
            min_lat, max_lat
        );
        """)
        
        # 9. Spatial Auto-Sync Triggers for Memorials (Maintains both R*Tree and geom BLOB)
        cursor.execute("DROP TRIGGER IF EXISTS trg_memorials_spatial_insert;")
        cursor.execute("""
        CREATE TRIGGER trg_memorials_spatial_insert
        AFTER INSERT ON memorials
        WHEN NEW.gps_lat IS NOT NULL AND NEW.gps_lng IS NOT NULL AND NEW.gps_lat != 0 AND NEW.gps_lng != 0
        BEGIN
            INSERT OR REPLACE INTO memorials_spatial_idx (id, min_lng, max_lng, min_lat, max_lat)
            VALUES (NEW.rowid, NEW.gps_lng, NEW.gps_lng, NEW.gps_lat, NEW.gps_lat);
            UPDATE memorials SET geom = gpkg_point(NEW.gps_lng, NEW.gps_lat) WHERE rowid = NEW.rowid;
        END;
        """)
        
        cursor.execute("DROP TRIGGER IF EXISTS trg_memorials_spatial_update;")
        cursor.execute("""
        CREATE TRIGGER trg_memorials_spatial_update
        AFTER UPDATE OF gps_lat, gps_lng ON memorials
        BEGIN
            DELETE FROM memorials_spatial_idx WHERE id = OLD.rowid;
            INSERT INTO memorials_spatial_idx (id, min_lng, max_lng, min_lat, max_lat)
            SELECT NEW.rowid, NEW.gps_lng, NEW.gps_lng, NEW.gps_lat, NEW.gps_lat
            WHERE NEW.gps_lat IS NOT NULL AND NEW.gps_lng IS NOT NULL AND NEW.gps_lat != 0 AND NEW.gps_lng != 0;
            UPDATE memorials SET geom = gpkg_point(NEW.gps_lng, NEW.gps_lat) WHERE rowid = NEW.rowid;
        END;
        """)
        
        cursor.execute("DROP TRIGGER IF EXISTS trg_memorials_spatial_delete;")
        cursor.execute("""
        CREATE TRIGGER trg_memorials_spatial_delete
        AFTER DELETE ON memorials
        BEGIN
            DELETE FROM memorials_spatial_idx WHERE id = OLD.rowid;
        END;
        """)
        
        # 10. Spatial Auto-Sync Triggers for Cemeteries (Maintains both R*Tree and geom BLOB)
        cursor.execute("DROP TRIGGER IF EXISTS trg_cemeteries_spatial_insert;")
        cursor.execute("""
        CREATE TRIGGER trg_cemeteries_spatial_insert
        AFTER INSERT ON cemeteries
        WHEN NEW.gps_lat IS NOT NULL AND NEW.gps_lng IS NOT NULL AND NEW.gps_lat != 0 AND NEW.gps_lng != 0
        BEGIN
            INSERT OR REPLACE INTO cemeteries_spatial_idx (id, min_lng, max_lng, min_lat, max_lat)
            VALUES (NEW.rowid, NEW.gps_lng, NEW.gps_lng, NEW.gps_lat, NEW.gps_lat);
            UPDATE cemeteries SET geom = gpkg_point(NEW.gps_lng, NEW.gps_lat) WHERE rowid = NEW.rowid;
        END;
        """)
        
        cursor.execute("DROP TRIGGER IF EXISTS trg_cemeteries_spatial_update;")
        cursor.execute("""
        CREATE TRIGGER trg_cemeteries_spatial_update
        AFTER UPDATE OF gps_lat, gps_lng ON cemeteries
        BEGIN
            DELETE FROM cemeteries_spatial_idx WHERE id = OLD.rowid;
            INSERT INTO cemeteries_spatial_idx (id, min_lng, max_lng, min_lat, max_lat)
            SELECT NEW.rowid, NEW.gps_lng, NEW.gps_lng, NEW.gps_lat, NEW.gps_lat
            WHERE NEW.gps_lat IS NOT NULL AND NEW.gps_lng IS NOT NULL AND NEW.gps_lat != 0 AND NEW.gps_lng != 0;
            UPDATE cemeteries SET geom = gpkg_point(NEW.gps_lng, NEW.gps_lat) WHERE rowid = NEW.rowid;
        END;
        """)
        
        cursor.execute("DROP TRIGGER IF EXISTS trg_cemeteries_spatial_delete;")
        cursor.execute("""
        CREATE TRIGGER trg_cemeteries_spatial_delete
        AFTER DELETE ON cemeteries
        BEGIN
            DELETE FROM cemeteries_spatial_idx WHERE id = OLD.rowid;
        END;
        """)
        
        conn.commit()
        seed_place_qids()
    finally:
        conn.close()

def get_memorials_in_bbox(min_lat, max_lat, min_lng, max_lng, cemetery_id=None, conn=None):
    """
    Fast 2D spatial bounding box query using the SQLite R*Tree spatial index.
    Returns list of memorial dicts with cemetery details within the coordinate bounds.
    """
    close_conn = False
    if conn is None:
        conn = get_db_conn()
        close_conn = True
        
    try:
        cursor = conn.cursor()
        sql = """
            SELECT m.*, c.name as cemetery_name, c.location as cemetery_location
            FROM memorials m
            JOIN memorials_spatial_idx s ON m.rowid = s.id
            LEFT JOIN cemeteries c ON m.cemetery_id = c.id
            WHERE s.min_lng >= ? AND s.max_lng <= ?
              AND s.min_lat >= ? AND s.max_lat <= ?
        """
        params = [float(min_lng), float(max_lng), float(min_lat), float(max_lat)]
        
        if cemetery_id and str(cemetery_id).lower() != 'all':
            sql += " AND m.cemetery_id = ?"
            params.append(str(cemetery_id))
            
        sql += " ORDER BY m.surname, m.name;"
        cursor.execute(sql, params)
        rows = cursor.fetchall()
        return [dict(row) for row in rows]
    except sqlite3.OperationalError:
        # Fallback if spatial table is not available
        cursor = conn.cursor()
        sql = """
            SELECT m.*, c.name as cemetery_name, c.location as cemetery_location
            FROM memorials m
            LEFT JOIN cemeteries c ON m.cemetery_id = c.id
            WHERE m.gps_lat BETWEEN ? AND ?
              AND m.gps_lng BETWEEN ? AND ?
        """
        params = [float(min_lat), float(max_lat), float(min_lng), float(max_lng)]
        if cemetery_id and str(cemetery_id).lower() != 'all':
            sql += " AND m.cemetery_id = ?"
            params.append(str(cemetery_id))
        cursor.execute(sql, params)
        return [dict(row) for row in cursor.fetchall()]
    finally:
        if close_conn:
            conn.close()

def get_cemeteries_in_bbox(min_lat, max_lat, min_lng, max_lng, conn=None):
    """
    Fast 2D spatial bounding box query for cemeteries using SQLite R*Tree spatial index.
    """
    close_conn = False
    if conn is None:
        conn = get_db_conn()
        close_conn = True
        
    try:
        cursor = conn.cursor()
        sql = """
            SELECT c.*
            FROM cemeteries c
            JOIN cemeteries_spatial_idx s ON c.rowid = s.id
            WHERE s.min_lng >= ? AND s.max_lng <= ?
              AND s.min_lat >= ? AND s.max_lat <= ?
            ORDER BY c.name;
        """
        cursor.execute(sql, (float(min_lng), float(max_lng), float(min_lat), float(max_lat)))
        return [dict(row) for row in cursor.fetchall()]
    except sqlite3.OperationalError:
        cursor = conn.cursor()
        cursor.execute("""
            SELECT * FROM cemeteries
            WHERE gps_lat BETWEEN ? AND ?
              AND gps_lng BETWEEN ? AND ?
            ORDER BY name;
        """, (float(min_lat), float(max_lat), float(min_lng), float(max_lng)))
        return [dict(row) for row in cursor.fetchall()]
    finally:
        if close_conn:
            conn.close()

def rebuild_spatial_indexes(conn=None):
    """
    Rebuilds and re-populates the spatial R*Tree virtual tables and GeoPackage binary Point geometries
    for all records with GPS coordinates.
    """
    close_conn = False
    if conn is None:
        conn = get_db_conn()
        close_conn = True
        
    try:
        cursor = conn.cursor()
        cursor.execute("DELETE FROM memorials_spatial_idx;")
        cursor.execute("DELETE FROM cemeteries_spatial_idx;")
        
        cursor.execute("""
        INSERT INTO memorials_spatial_idx (id, min_lng, max_lng, min_lat, max_lat)
        SELECT rowid, gps_lng, gps_lng, gps_lat, gps_lat
        FROM memorials
        WHERE gps_lat IS NOT NULL AND gps_lng IS NOT NULL AND gps_lat != 0 AND gps_lng != 0;
        """)
        mem_count = cursor.rowcount
        
        cursor.execute("""
        INSERT INTO cemeteries_spatial_idx (id, min_lng, max_lng, min_lat, max_lat)
        SELECT rowid, gps_lng, gps_lng, gps_lat, gps_lat
        FROM cemeteries
        WHERE gps_lat IS NOT NULL AND gps_lng IS NOT NULL AND gps_lat != 0 AND gps_lng != 0;
        """)
        cem_count = cursor.rowcount

        # Populate GeoPackage Point geometries
        cursor.execute("""
        UPDATE memorials
        SET geom = gpkg_point(gps_lng, gps_lat)
        WHERE gps_lat IS NOT NULL AND gps_lng IS NOT NULL AND gps_lat != 0 AND gps_lng != 0;
        """)
        
        cursor.execute("""
        UPDATE cemeteries
        SET geom = gpkg_point(gps_lng, gps_lat)
        WHERE gps_lat IS NOT NULL AND gps_lng IS NOT NULL AND gps_lat != 0 AND gps_lng != 0;
        """)

        # Update spatial extents in gpkg_contents
        cem_bounds = cursor.execute("SELECT MIN(gps_lng), MIN(gps_lat), MAX(gps_lng), MAX(gps_lat) FROM cemeteries WHERE gps_lat IS NOT NULL AND gps_lng IS NOT NULL AND gps_lat != 0").fetchone()
        if cem_bounds and cem_bounds[0] is not None:
            cursor.execute("UPDATE gpkg_contents SET min_x = ?, min_y = ?, max_x = ?, max_y = ? WHERE table_name = 'cemeteries'", cem_bounds)

        mem_bounds = cursor.execute("SELECT MIN(gps_lng), MIN(gps_lat), MAX(gps_lng), MAX(gps_lat) FROM memorials WHERE gps_lat IS NOT NULL AND gps_lng IS NOT NULL AND gps_lat != 0").fetchone()
        if mem_bounds and mem_bounds[0] is not None:
            cursor.execute("UPDATE gpkg_contents SET min_x = ?, min_y = ?, max_x = ?, max_y = ? WHERE table_name = 'memorials'", mem_bounds)

        conn.commit()
        return mem_count, cem_count
    finally:
        if close_conn:
            conn.close()

def export_geopackage_file(target_path=GPKG_PATH):
    """
    Exports a standalone OGC GeoPackage (.gpkg) file ready for direct drag-and-drop into QGIS or ArcGIS Pro.
    """
    import shutil
    if os.path.exists(DB_PATH):
        os.makedirs(os.path.dirname(target_path) or '.', exist_ok=True)
        shutil.copyfile(DB_PATH, target_path)
        return target_path
    return None

SEEDED_PLACE_QIDS = {
    # West Virginia Counties (Verified Wikidata QIDs)
    "cabell county": "Q494129",
    "cabell": "Q494129",
    "kanawha county": "Q501800",
    "kanawha": "Q501800",
    "wayne county": "Q495126",
    "wayne": "Q495126",
    "mason county": "Q501830",
    "mason": "Q501830",
    "putnam county": "Q501809",
    "putnam": "Q501809",
    "lincoln county": "Q495151",
    "lincoln": "Q495151",

    # Municipalities / Places (Verified Wikidata QIDs)
    "salt rock": "Q4406403",
    "barboursville": "Q2280481",
    "huntington": "Q241808",
    "milton": "Q3313661",
    "culloden": "Q3161331",
    "lesage": "Q4406469",
    "ona": "Q3476433",
    "cox landing": "Q5179899",
    "greenbottom": "Q5603844",
    "charleston": "Q44571",
    "west virginia": "Q1371"
}

def seed_place_qids():
    conn = get_db_conn()
    try:
        cursor = conn.cursor()
        for name, qid in SEEDED_PLACE_QIDS.items():
            cursor.execute("INSERT OR IGNORE INTO place_qids (place_name, qid) VALUES (?, ?);", (name.lower().strip(), qid))
        conn.commit()
    finally:
        conn.close()

def get_place_qid(place_name):
    if not place_name:
        return None
    key = place_name.lower().strip()
    conn = get_db_conn()
    try:
        cursor = conn.cursor()
        cursor.execute("SELECT qid FROM place_qids WHERE place_name = ?;", (key,))
        row = cursor.fetchone()
        return row['qid'] if row else None
    finally:
        conn.close()

def save_place_qid(place_name, qid):
    if not place_name or not qid:
        return
    key = place_name.lower().strip()
    conn = get_db_conn()
    try:
        cursor = conn.cursor()
        cursor.execute("INSERT OR REPLACE INTO place_qids (place_name, qid, updated_at) VALUES (?, ?, CURRENT_TIMESTAMP);", (key, qid.strip().upper()))
        conn.commit()
    finally:
        conn.close()

def get_all_place_qids():
    conn = get_db_conn()
    try:
        cursor = conn.cursor()
        cursor.execute("SELECT place_name, qid FROM place_qids;")
        return {row['place_name']: row['qid'] for row in cursor.fetchall()}
    finally:
        conn.close()

# --- Cemetery CRUD ---

def add_cemetery(cemetery_id, nickname=None, name=None, location=None, gps_lat=None, gps_lng=None):
    conn = get_db_conn()
    try:
        cursor = conn.cursor()
        db_nickname = str(nickname).strip() if nickname and str(nickname).strip() else str(cemetery_id)
        cursor.execute("""
            INSERT INTO cemeteries (id, nickname, name, location, gps_lat, gps_lng)
            VALUES (?, ?, ?, ?, ?, ?)
            ON CONFLICT(id) DO UPDATE SET
                nickname=CASE 
                    WHEN excluded.nickname != '' AND excluded.nickname != cast(excluded.id as text) AND excluded.nickname != excluded.name 
                    THEN excluded.nickname 
                    ELSE cemeteries.nickname 
                END,
                name=coalesce(nullif(excluded.name, ''), cemeteries.name),
                location=coalesce(nullif(excluded.location, ''), cemeteries.location),
                gps_lat=coalesce(excluded.gps_lat, cemeteries.gps_lat),
                gps_lng=coalesce(excluded.gps_lng, cemeteries.gps_lng);
        """, (str(cemetery_id), db_nickname, name, location, gps_lat, gps_lng))
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

def parse_cemetery_text(raw_name, raw_location=None, raw_nickname=None):
    if raw_nickname and raw_nickname.strip():
        nickname = raw_nickname.strip()
    else:
        nickname = ""

    if not raw_name:
        return "", nickname, raw_location or ""
        
    lines = [line.strip() for line in raw_name.split('\n') if line.strip()]
    if not lines:
        return "", nickname, raw_location or ""
        
    clean_name = lines[0]
    clean_loc = raw_location or ""
    
    if not nickname:
        aka_patterns = [
            r'Also known as:\s*([^\n,]+)',
            r'AKA:\s*([^\n,]+)',
            r'Alternate name:\s*([^\n,]+)',
            r'\(aka\s+([^)]+)\)'
        ]
        for pat in aka_patterns:
            aka_match = re.search(pat, raw_name, re.IGNORECASE)
            if aka_match:
                nickname = aka_match.group(1).strip()
                break
        
    if 'also known as' in clean_name.lower():
        parts = re.split(r'also known as:?', clean_name, flags=re.IGNORECASE)
        clean_name = parts[0].strip()
        if len(parts) > 1 and not nickname:
            nickname = parts[1].strip()
            
    clean_name = re.sub(r',\s*Cabell County.*$', '', clean_name, flags=re.IGNORECASE).strip()
    clean_name = re.sub(r',\s*West Virginia.*$', '', clean_name, flags=re.IGNORECASE).strip()
    
    for line in lines[1:]:
        if 'County' in line or 'West Virginia' in line:
            clean_loc = line.strip()
            break
            
    return clean_name, nickname, clean_loc

def upsert_county_cemetery(cemetery_id, name, location, state=None, county=None, gps_lat=None, gps_lng=None, fag_burial_count=0, raw_text=None, nickname=None):
    combined_name = f"{name}\n{raw_text}" if raw_text else name
    clean_name, clean_nick, clean_loc = parse_cemetery_text(combined_name, location, raw_nickname=nickname)
    conn = get_db_conn()
    try:
        cursor = conn.cursor()
        cursor.execute("""
            INSERT INTO cemeteries (id, nickname, name, location, state, county, gps_lat, gps_lng, fag_burial_count, is_custom)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 0)
            ON CONFLICT(id) DO UPDATE SET
                nickname=CASE 
                    WHEN excluded.nickname != '' AND excluded.nickname != cast(excluded.id as text) AND excluded.nickname != excluded.name 
                    THEN excluded.nickname 
                    ELSE cemeteries.nickname 
                END,
                name=coalesce(nullif(excluded.name, ''), cemeteries.name),
                location=coalesce(nullif(excluded.location, ''), cemeteries.location),
                state=coalesce(nullif(excluded.state, ''), cemeteries.state),
                county=coalesce(nullif(excluded.county, ''), cemeteries.county),
                gps_lat=coalesce(excluded.gps_lat, cemeteries.gps_lat),
                gps_lng=coalesce(excluded.gps_lng, cemeteries.gps_lng),
                fag_burial_count=max(coalesce(excluded.fag_burial_count, 0), coalesce(cemeteries.fag_burial_count, 0));
        """, (str(cemetery_id), clean_nick, clean_name, clean_loc, state, county, gps_lat, gps_lng, fag_burial_count))
        conn.commit()
    finally:
        conn.close()

def add_custom_cemetery(name, state, county, location="", gps_lat=None, gps_lng=None, source_doc="", notes="", nickname=None):
    import time
    custom_id = f"HIST_{int(time.time() * 1000)}"
    nick_val = nickname.strip() if nickname and nickname.strip() else name
    conn = get_db_conn()
    try:
        cursor = conn.cursor()
        cursor.execute("""
            INSERT INTO cemeteries (id, nickname, name, location, state, county, gps_lat, gps_lng, is_custom, source_doc, notes)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, 1, ?, ?);
        """, (custom_id, nick_val, name, location, state, county, gps_lat, gps_lng, source_doc, notes))
        conn.commit()
        return custom_id
    finally:
        conn.close()

def link_custom_cemetery_to_fag(custom_id, fag_id):
    import time
    conn = get_db_conn()
    try:
        cursor = conn.cursor()
        today_str = time.strftime('%Y-%m-%d')
        cursor.execute("SELECT * FROM cemeteries WHERE id = ?;", (custom_id,))
        row = cursor.fetchone()
        if not row:
            return False, "Custom cemetery not found"
        row_dict = dict(row)
        
        cursor.execute("SELECT id FROM cemeteries WHERE id = ?;", (str(fag_id),))
        fag_row = cursor.fetchone()
        
        if fag_row:
            cursor.execute("""
                UPDATE cemeteries SET
                    source_doc = coalesce(nullif(source_doc, ''), ?),
                    fag_added_date = ?,
                    notes = coalesce(notes || ' ', '') || ?
                WHERE id = ?;
            """, (row_dict.get('source_doc', ''), today_str, f"Linked from {custom_id}: {row_dict.get('notes', '')}", str(fag_id)))
            cursor.execute("DELETE FROM cemeteries WHERE id = ?;", (custom_id,))
        else:
            cursor.execute("""
                UPDATE cemeteries SET
                    id = ?,
                    is_custom = 0,
                    fag_added_date = ?
                WHERE id = ?;
            """, (str(fag_id), today_str, custom_id))
            
        conn.commit()
        return True, "Successfully linked to Find a Grave ID"
    finally:
        conn.close()

def get_county_cemeteries(state=None, county=None):
    conn = get_db_conn()
    try:
        cursor = conn.cursor()
        query = """
            SELECT c.*,
                   (SELECT count(*) FROM memorials WHERE cemetery_id = c.id) as stashed_burial_count
            FROM cemeteries c
            WHERE 1=1
        """
        params = []
        if state and state.strip():
            query += " AND (c.state LIKE ? OR c.location LIKE ?)"
            params.append(f"%{state.strip()}%")
            params.append(f"%{state.strip()}%")
        if county and county.strip():
            query += " AND (c.county LIKE ? OR c.location LIKE ?)"
            params.append(f"%{county.strip()}%")
            params.append(f"%{county.strip()}%")
            
        query += " ORDER BY c.is_custom DESC, coalesce(c.name, c.id) ASC;"
        cursor.execute(query, params)
        rows = cursor.fetchall()
        return [dict(row) for row in rows]
    finally:
        conn.close()

def update_cemetery_external_sync(cemetery_id, field_dict):
    conn = get_db_conn()
    try:
        cursor = conn.cursor()
        allowed_fields = {
            'wikidata_qid', 'wikidata_confirmed', 'wikidata_date',
            'osm_id', 'osm_confirmed', 'osm_date',
            'wikitree_id', 'wikitree_confirmed', 'wikitree_date',
            'notes', 'source_doc'
        }
        set_clauses = []
        params = []
        for key, val in field_dict.items():
            if key in allowed_fields:
                set_clauses.append(f"{key} = ?")
                params.append(val)
        if set_clauses:
            query = f"UPDATE cemeteries SET {', '.join(set_clauses)} WHERE id = ?;"
            params.append(str(cemetery_id))
            cursor.execute(query, params)
            conn.commit()
            return True
        return False
    finally:
        conn.close()

def get_unique_states_counties():
    conn = get_db_conn()
    try:
        cursor = conn.cursor()
        cursor.execute("SELECT DISTINCT state, county FROM cemeteries WHERE state IS NOT NULL AND state != '';")
        rows = cursor.fetchall()
        return [dict(row) for row in rows]
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
        
        sql = "SELECT m.*, c.nickname as cemetery_nickname FROM memorials m LEFT JOIN cemeteries c ON m.cemetery_id = c.id WHERE 1=1"
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
        count_sql = "SELECT count(*) FROM memorials m LEFT JOIN cemeteries c ON m.cemetery_id = c.id WHERE 1=1"
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
        cursor.execute("SELECT m.*, c.name as cemetery_name, c.nickname as cemetery_nickname FROM memorials m LEFT JOIN cemeteries c ON m.cemetery_id = c.id WHERE m.id = ?;", (memorial_id,))
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
