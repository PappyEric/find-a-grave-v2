import unittest
import os
import tempfile
import sqlite3
import database
import geo_utils

class TestSpatialDatabase(unittest.TestCase):
    def setUp(self):
        self.orig_db_path = database.DB_PATH
        self.db_fd, self.db_path = tempfile.mkstemp(suffix='.db')
        database.DB_PATH = self.db_path
        database.init_db()

    def tearDown(self):
        os.close(self.db_fd)
        if os.path.exists(self.db_path):
            os.remove(self.db_path)
        database.DB_PATH = self.orig_db_path

    def test_spatial_tables_and_triggers_created(self):
        conn = database.get_db_conn()
        try:
            cur = conn.cursor()
            tables = [r[0] for r in cur.execute("SELECT name FROM sqlite_master WHERE type='table';").fetchall()]
            self.assertIn("memorials_spatial_idx", tables)
            self.assertIn("cemeteries_spatial_idx", tables)

            triggers = [r[0] for r in cur.execute("SELECT name FROM sqlite_master WHERE type='trigger';").fetchall()]
            self.assertIn("trg_memorials_spatial_insert", triggers)
            self.assertIn("trg_memorials_spatial_update", triggers)
            self.assertIn("trg_memorials_spatial_delete", triggers)
            self.assertIn("trg_cemeteries_spatial_insert", triggers)
            self.assertIn("trg_cemeteries_spatial_update", triggers)
            self.assertIn("trg_cemeteries_spatial_delete", triggers)
        finally:
            conn.close()

    def test_memorial_spatial_lifecycle_triggers(self):
        conn = database.get_db_conn()
        try:
            cur = conn.cursor()
            # 1. Insert memorial with coordinates
            cur.execute("""
                INSERT INTO memorials (id, name, surname, first_name, gps_lat, gps_lng)
                VALUES ('1001', 'John Miller', 'Miller', 'John', 38.4100, -82.4200);
            """)
            conn.commit()

            spatial_rows = cur.execute("SELECT * FROM memorials_spatial_idx;").fetchall()
            self.assertEqual(len(spatial_rows), 1)

            # 2. Query bounding box containing coordinate
            results = database.get_memorials_in_bbox(38.40, 38.42, -82.43, -82.41, conn=conn)
            self.assertEqual(len(results), 1)
            self.assertEqual(results[0]['id'], '1001')

            # 3. Query bounding box NOT containing coordinate
            out_results = database.get_memorials_in_bbox(39.00, 39.10, -82.00, -81.90, conn=conn)
            self.assertEqual(len(out_results), 0)

            # 4. Update coordinates
            cur.execute("UPDATE memorials SET gps_lat = 39.05, gps_lng = -81.95 WHERE id = '1001';")
            conn.commit()

            new_in_results = database.get_memorials_in_bbox(39.00, 39.10, -82.00, -81.90, conn=conn)
            self.assertEqual(len(new_in_results), 1)
            self.assertEqual(new_in_results[0]['id'], '1001')

            # 5. Delete memorial
            cur.execute("DELETE FROM memorials WHERE id = '1001';")
            conn.commit()

            spatial_after_del = cur.execute("SELECT * FROM memorials_spatial_idx;").fetchall()
            self.assertEqual(len(spatial_after_del), 0)
        finally:
            conn.close()

    def test_find_nearby_burials_spatial_search(self):
        conn = database.get_db_conn()
        try:
            cur = conn.cursor()
            # Insert focus person and two nearby graves
            cur.execute("""
                INSERT INTO cemeteries (id, nickname, name, gps_lat, gps_lng)
                VALUES ('cem1', 'Oak Grove', 'Oak Grove Cemetery', 38.4000, -82.4000);
            """)
            cur.execute("""
                INSERT INTO memorials (id, cemetery_id, name, surname, first_name, gps_lat, gps_lng)
                VALUES ('m1', 'cem1', 'Alice Stone', 'Stone', 'Alice', 38.40000, -82.40000);
            """)
            # ~36 feet away (0.0001 deg lat ≈ 36.4 feet)
            cur.execute("""
                INSERT INTO memorials (id, cemetery_id, name, surname, first_name, gps_lat, gps_lng)
                VALUES ('m2', 'cem1', 'Bob Stone', 'Stone', 'Bob', 38.40010, -82.40000);
            """)
            # ~3,600 feet away
            cur.execute("""
                INSERT INTO memorials (id, cemetery_id, name, surname, first_name, gps_lat, gps_lng)
                VALUES ('m3', 'cem1', 'Charlie Far', 'Far', 'Charlie', 38.41000, -82.40000);
            """)
            conn.commit()

            # Search within 100 feet of Alice
            res_100ft = geo_utils.find_nearby_burials('m1', radius_feet=100.0, conn=conn)
            self.assertEqual(res_100ft['count'], 1)
            self.assertEqual(res_100ft['nearby_burials'][0]['id'], 'm2')
            self.assertLess(res_100ft['nearby_burials'][0]['distance_feet'], 50.0)

            # Search within 5000 feet
            res_5000ft = geo_utils.find_nearby_burials('m1', radius_feet=5000.0, conn=conn)
            self.assertEqual(res_5000ft['count'], 2)
        finally:
            conn.close()

    def test_find_nearby_cemeteries_spatial(self):
        conn = database.get_db_conn()
        try:
            cur = conn.cursor()
            cur.execute("""
                INSERT INTO cemeteries (id, nickname, name, gps_lat, gps_lng)
                VALUES ('c1', 'Spring Hill', 'Spring Hill Cemetery', 38.4150, -82.4400);
            """)
            cur.execute("""
                INSERT INTO cemeteries (id, nickname, name, gps_lat, gps_lng)
                VALUES ('c2', 'Woodmere', 'Woodmere Cemetery', 38.4040, -82.3968);
            """)
            conn.commit()

            nearby = geo_utils.find_nearby_cemeteries(38.4150, -82.4400, radius_feet=500.0, conn=conn)
            self.assertEqual(len(nearby), 1)
            self.assertEqual(nearby[0]['id'], 'c1')
        finally:
            conn.close()

    def test_rebuild_spatial_indexes(self):
        conn = database.get_db_conn()
        try:
            cur = conn.cursor()
            cur.execute("""
                INSERT INTO memorials (id, name, gps_lat, gps_lng)
                VALUES ('m99', 'Test Rebuild', 38.5, -82.5);
            """)
            conn.commit()

            # Clear index manually
            cur.execute("DELETE FROM memorials_spatial_idx;")
            conn.commit()
            self.assertEqual(len(cur.execute("SELECT * FROM memorials_spatial_idx;").fetchall()), 0)

            # Rebuild
            mem_c, cem_c = database.rebuild_spatial_indexes(conn=conn)
            self.assertEqual(mem_c, 1)
            self.assertEqual(len(cur.execute("SELECT * FROM memorials_spatial_idx;").fetchall()), 1)
        finally:
            conn.close()

    def test_geopackage_metadata_and_point_geometries(self):
        conn = database.get_db_conn()
        try:
            cur = conn.cursor()
            # 1. Verify GeoPackage Pragmas
            app_id = cur.execute("PRAGMA application_id;").fetchone()[0]
            self.assertEqual(app_id, 1196444487)  # 'GPKG'

            # 2. Verify GeoPackage Metadata Tables
            srs_rows = cur.execute("SELECT srs_id, srs_name FROM gpkg_spatial_ref_sys WHERE srs_id = 4326;").fetchall()
            self.assertEqual(len(srs_rows), 1)

            contents = [r[0] for r in cur.execute("SELECT table_name FROM gpkg_contents;").fetchall()]
            self.assertIn("cemeteries", contents)
            self.assertIn("memorials", contents)

            geom_cols = [r[0] for r in cur.execute("SELECT table_name FROM gpkg_geometry_columns WHERE column_name = 'geom' AND geometry_type_name = 'POINT';").fetchall()]
            self.assertIn("cemeteries", geom_cols)
            self.assertIn("memorials", geom_cols)

            # 3. Test insert and verify GPKG Point binary BLOB structure
            cur.execute("""
                INSERT INTO cemeteries (id, nickname, name, gps_lat, gps_lng)
                VALUES ('c_gpkg', 'Greenwood', 'Greenwood Cemetery', 38.40631, -82.31686);
            """)
            conn.commit()

            row = cur.execute("SELECT geom FROM cemeteries WHERE id = 'c_gpkg';").fetchone()
            self.assertIsNotNone(row[0])
            geom_blob = bytes(row[0])
            # Magic header 'GP'
            self.assertEqual(geom_blob[:2], b'GP')
            # Length = 29 bytes (8 byte header + 21 byte WKB Point)
            self.assertEqual(len(geom_blob), 29)

            # 4. Test GeoPackage file export
            gpkg_out = tempfile.mktemp(suffix='.gpkg')
            exported = database.export_geopackage_file(gpkg_out)
            self.assertTrue(os.path.exists(exported))
            os.remove(exported)
        finally:
            conn.close()

if __name__ == '__main__':
    unittest.main()
