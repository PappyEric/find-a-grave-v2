import unittest
import math
import os
import sqlite3

import geo_utils
import app
import database

class TestGeoProximity(unittest.TestCase):

    def setUp(self):
        app.app.config['TESTING'] = True
        self.client = app.app.test_client()

    def test_haversine_feet_calculation(self):
        # Coordinates for Statue of Liberty: 40.689247, -74.044502
        # Coordinates for Empire State Building: 40.748817, -73.985428
        # Approx distance: ~5.5 miles = ~29,200 feet
        dist = geo_utils.haversine_feet(40.689247, -74.044502, 40.748817, -73.985428)
        self.assertIsNotNone(dist)
        self.assertGreater(dist, 25000)
        self.assertLess(dist, 30000)

        # Same point distance should be 0 feet
        same_dist = geo_utils.haversine_feet(40.689247, -74.044502, 40.689247, -74.044502)
        self.assertEqual(same_dist, 0.0)

    def test_get_burials_with_gps(self):
        conn = database.get_db_conn()
        burials = geo_utils.get_burials_with_gps(conn=conn)
        conn.close()
        self.assertIsInstance(burials, list)
        if len(burials) > 0:
            b = burials[0]
            self.assertIn('gps_lat', b)
            self.assertIn('gps_lng', b)
            self.assertIsNotNone(b['gps_lat'])

    def test_find_nearby_burials(self):
        conn = database.get_db_conn()
        cur = conn.cursor()
        cur.execute("SELECT id, name FROM memorials WHERE gps_lat IS NOT NULL LIMIT 1;")
        row = cur.fetchone()
        conn.close()

        if row:
            focus_id = str(row['id'])
            res = geo_utils.find_nearby_burials(focus_id, radius_feet=10000.0)
            self.assertNotIn('error', res)
            self.assertIn('focus_memorial', res)
            self.assertIn('nearby_burials', res)
            self.assertEqual(res['focus_memorial']['id'], focus_id)

    def test_api_map_burials_endpoint(self):
        res = self.client.get('/api/map/burials')
        self.assertEqual(res.status_code, 200)
        data = res.get_json()
        self.assertIn('burials', data)
        self.assertIn('count', data)

    def test_api_proximity_endpoint(self):
        conn = database.get_db_conn()
        cur = conn.cursor()
        cur.execute("SELECT id FROM memorials WHERE gps_lat IS NOT NULL LIMIT 1;")
        row = cur.fetchone()
        conn.close()

        if row:
            focus_id = str(row['id'])
            res = self.client.get(f'/api/memorials/{focus_id}/proximity?radius_feet=500')
            self.assertEqual(res.status_code, 200)
            data = res.get_json()
            self.assertIn('focus_memorial', data)
            self.assertIn('nearby_burials', data)

if __name__ == '__main__':
    unittest.main()
