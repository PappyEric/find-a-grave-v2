import unittest
import os
import app
import database

class TestCemeteryRescrape(unittest.TestCase):

    def setUp(self):
        app.app.config['TESTING'] = True
        self.client = app.app.test_client()

    def test_rescrape_endpoint_validation(self):
        # Missing cemetery ID should return 404 route error or 405
        res = self.client.post('/api/cemeteries//rescrape')
        self.assertIn(res.status_code, [404, 405])

    def test_rescrape_endpoint_structure(self):
        conn = database.get_db_conn()
        cur = conn.cursor()
        cur.execute("SELECT id FROM cemeteries LIMIT 1;")
        row = cur.fetchone()
        conn.close()

        if row:
            cem_id = str(row[0])
            # Pass max_pages=1 to keep test fast
            res = self.client.post(f'/api/cemeteries/{cem_id}/rescrape', json={'max_pages': 1})
            self.assertEqual(res.status_code, 200)
            data = res.get_json()
            self.assertIn('scanned', data)
            self.assertIn('added', data)
            self.assertIn('updated', data)
            self.assertEqual(data['cemetery_id'], cem_id)

if __name__ == '__main__':
    unittest.main()
