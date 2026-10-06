import unittest
import os
from unittest.mock import patch
import app
import database
import grave_digger

class TestCemeteryRescrape(unittest.TestCase):

    def setUp(self):
        app.app.config['TESTING'] = True
        self.client = app.app.test_client()

    def test_rescrape_endpoint_validation(self):
        # Missing cemetery ID should return 404 route error or 405
        res = self.client.post('/api/cemeteries//rescrape')
        self.assertIn(res.status_code, [404, 405])

    @patch('grave_digger.rescrape_cemetery_by_id')
    def test_rescrape_endpoint_structure(self, mock_rescrape):
        conn = database.get_db_conn()
        cur = conn.cursor()
        cur.execute("SELECT id FROM cemeteries LIMIT 1;")
        row = cur.fetchone()
        conn.close()

        cem_id = str(row[0]) if row else "53841"
        mock_rescrape.return_value = {
            'cemetery_id': cem_id,
            'cemetery_name': 'Test Cemetery',
            'scanned': 10,
            'added': 2,
            'updated': 8
        }

        res = self.client.post(f'/api/cemeteries/{cem_id}/rescrape', json={'max_pages': 1})
        self.assertEqual(res.status_code, 200)
        data = res.get_json()
        self.assertIn('scanned', data)
        self.assertIn('added', data)
        self.assertIn('updated', data)
        self.assertEqual(data['cemetery_id'], cem_id)
        mock_rescrape.assert_called_once_with(cem_id, max_pages=1)

if __name__ == '__main__':
    unittest.main()
