"""
spatial_setup.py - Spatially enable the Find A Grave database for QGIS, ArcGIS, and SQLite R*Tree.

Generates:
1. 'stash/find_a_grave_spatial.db' - Spatially indexed SQLite database with GeoPackage Point geometry & R*Tree tables.
2. 'stash/find_a_grave_spatial.gpkg' - OGC GeoPackage standard file directly openable in QGIS & ArcGIS as Point layers.
"""

import sqlite3
import os
import shutil
import struct

SOURCE_DB = 'stash/find_a_grave_v2.db'
SPATIAL_DB = 'stash/find_a_grave_spatial.db'
GPKG_FILE = 'stash/find_a_grave_spatial.gpkg'

def make_gpkg_point(lng, lat):
    """
    Encodes (lng, lat) into an OGC GeoPackage standard binary Point geometry (EPSG:4326).
    Header (8 bytes) + WKB Point (21 bytes) = 29 bytes.
    """
    if lng is None or lat is None:
        return None
    try:
        lng_f, lat_f = float(lng), float(lat)
        if lng_f == 0 and lat_f == 0:
            return None
        # GPKG Header: magic 'GP' (0x47, 0x50), version 0, flags 0x01 (little endian, no envelope), srs_id 4326
        header = struct.pack('<2sBB i', b'GP', 0, 1, 4326)
        # WKB Point: byte order 1 (little endian), geom type 1 (Point), X (lng), Y (lat)
        wkb = struct.pack('<B I d d', 1, 1, lng_f, lat_f)
        return header + wkb
    except Exception:
        return None

def create_spatial_schema(conn):
    cursor = conn.cursor()
    
    # 1. Set GeoPackage application_id & user_version
    cursor.execute("PRAGMA application_id = 1196444487;")  # ASCII 'GPKG'
    cursor.execute("PRAGMA user_version = 10300;")          # GeoPackage 1.3
    
    # 2. GeoPackage Metadata Tables
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
    
    # Add geom BLOB column to cemeteries and memorials if not present
    cem_cols = [c[1] for c in cursor.execute("PRAGMA table_info(cemeteries)").fetchall()]
    if 'geom' not in cem_cols:
        cursor.execute("ALTER TABLE cemeteries ADD COLUMN geom BLOB;")
        
    mem_cols = [c[1] for c in cursor.execute("PRAGMA table_info(memorials)").fetchall()]
    if 'geom' not in mem_cols:
        cursor.execute("ALTER TABLE memorials ADD COLUMN geom BLOB;")

    # Register in gpkg_geometry_columns & gpkg_contents
    cursor.execute("INSERT OR REPLACE INTO gpkg_geometry_columns VALUES ('cemeteries', 'geom', 'POINT', 4326, 0, 0);")
    cursor.execute("INSERT OR REPLACE INTO gpkg_geometry_columns VALUES ('memorials', 'geom', 'POINT', 4326, 0, 0);")
    cursor.execute("""
    INSERT OR REPLACE INTO gpkg_contents (table_name, data_type, identifier, description, last_change, srs_id)
    VALUES 
    ('cemeteries', 'features', 'cemeteries', 'Find a Grave Cemeteries Point Layer', datetime('now'), 4326),
    ('memorials', 'features', 'memorials', 'Find a Grave Memorials Point Layer', datetime('now'), 4326);
    """)

    # 3. Create R*Tree virtual tables for spatial search speed
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
    
    # 4. Triggers for Memorials
    cursor.execute("DROP TRIGGER IF EXISTS trg_memorials_spatial_insert;")
    cursor.execute("""
    CREATE TRIGGER trg_memorials_spatial_insert
    AFTER INSERT ON memorials
    WHEN NEW.gps_lat IS NOT NULL AND NEW.gps_lng IS NOT NULL AND NEW.gps_lat != 0 AND NEW.gps_lng != 0
    BEGIN
        INSERT OR REPLACE INTO memorials_spatial_idx (id, min_lng, max_lng, min_lat, max_lat)
        VALUES (NEW.rowid, NEW.gps_lng, NEW.gps_lng, NEW.gps_lat, NEW.gps_lat);
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
    
    # 5. Triggers for Cemeteries
    cursor.execute("DROP TRIGGER IF EXISTS trg_cemeteries_spatial_insert;")
    cursor.execute("""
    CREATE TRIGGER trg_cemeteries_spatial_insert
    AFTER INSERT ON cemeteries
    WHEN NEW.gps_lat IS NOT NULL AND NEW.gps_lng IS NOT NULL AND NEW.gps_lat != 0 AND NEW.gps_lng != 0
    BEGIN
        INSERT OR REPLACE INTO cemeteries_spatial_idx (id, min_lng, max_lng, min_lat, max_lat)
        VALUES (NEW.rowid, NEW.gps_lng, NEW.gps_lng, NEW.gps_lat, NEW.gps_lat);
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

def populate_spatial_data(conn):
    cursor = conn.cursor()
    
    # 1. Populate R*Tree indexes
    cursor.execute("DELETE FROM memorials_spatial_idx;")
    cursor.execute("DELETE FROM cemeteries_spatial_idx;")
    
    cursor.execute("""
    INSERT INTO memorials_spatial_idx (id, min_lng, max_lng, min_lat, max_lat)
    SELECT rowid, gps_lng, gps_lng, gps_lat, gps_lat
    FROM memorials
    WHERE gps_lat IS NOT NULL AND gps_lng IS NOT NULL AND gps_lat != 0 AND gps_lng != 0;
    """)
    mem_indexed = cursor.rowcount
    
    cursor.execute("""
    INSERT INTO cemeteries_spatial_idx (id, min_lng, max_lng, min_lat, max_lat)
    SELECT rowid, gps_lng, gps_lng, gps_lat, gps_lat
    FROM cemeteries
    WHERE gps_lat IS NOT NULL AND gps_lng IS NOT NULL AND gps_lat != 0 AND gps_lng != 0;
    """)
    cem_indexed = cursor.rowcount

    # 2. Populate GeoPackage binary point geometries
    cems = cursor.execute("SELECT rowid, gps_lat, gps_lng FROM cemeteries WHERE gps_lat IS NOT NULL AND gps_lng IS NOT NULL AND gps_lat != 0").fetchall()
    for rowid, lat, lng in cems:
        blob = make_gpkg_point(lng, lat)
        cursor.execute("UPDATE cemeteries SET geom = ? WHERE rowid = ?", (blob, rowid))

    mems = cursor.execute("SELECT rowid, gps_lat, gps_lng FROM memorials WHERE gps_lat IS NOT NULL AND gps_lng IS NOT NULL AND gps_lat != 0").fetchall()
    for rowid, lat, lng in mems:
        blob = make_gpkg_point(lng, lat)
        cursor.execute("UPDATE memorials SET geom = ? WHERE rowid = ?", (blob, rowid))

    # 3. Update spatial bounds in gpkg_contents
    cem_bounds = cursor.execute("SELECT MIN(gps_lng), MIN(gps_lat), MAX(gps_lng), MAX(gps_lat) FROM cemeteries WHERE gps_lat IS NOT NULL AND gps_lng IS NOT NULL AND gps_lat != 0").fetchone()
    if cem_bounds and cem_bounds[0] is not None:
        cursor.execute("UPDATE gpkg_contents SET min_x = ?, min_y = ?, max_x = ?, max_y = ? WHERE table_name = 'cemeteries'", cem_bounds)

    mem_bounds = cursor.execute("SELECT MIN(gps_lng), MIN(gps_lat), MAX(gps_lng), MAX(gps_lat) FROM memorials WHERE gps_lat IS NOT NULL AND gps_lng IS NOT NULL AND gps_lat != 0").fetchone()
    if mem_bounds and mem_bounds[0] is not None:
        cursor.execute("UPDATE gpkg_contents SET min_x = ?, min_y = ?, max_x = ?, max_y = ? WHERE table_name = 'memorials'", mem_bounds)

    conn.commit()
    return mem_indexed, cem_indexed

def run_spatial_migration():
    os.makedirs('stash', exist_ok=True)
    
    if os.path.exists(SOURCE_DB):
        print(f"Copying {SOURCE_DB} -> {SPATIAL_DB}...")
        shutil.copyfile(SOURCE_DB, SPATIAL_DB)
    else:
        print(f"Source DB {SOURCE_DB} not found, working with {SPATIAL_DB}...")
        
    conn = sqlite3.connect(SPATIAL_DB)
    try:
        conn.execute("PRAGMA journal_mode=WAL;")
        print("Creating GeoPackage metadata, point geometries & R*Tree tables...")
        create_spatial_schema(conn)
        
        print("Populating GeoPackage Point geometries & spatial indexes...")
        mem_count, cem_count = populate_spatial_data(conn)
        
        print(f"Successfully spatially enabled {SPATIAL_DB}:")
        print(f"  - Cemeteries as Point layer: {cem_count:,}")
        print(f"  - Memorials as Point layer:  {mem_count:,}")
    finally:
        conn.close()

    # Also make a direct .gpkg copy for convenient drag-and-drop into QGIS/ArcGIS
    shutil.copyfile(SPATIAL_DB, GPKG_FILE)
    print(f"Created GeoPackage file: {GPKG_FILE}")

if __name__ == '__main__':
    run_spatial_migration()
