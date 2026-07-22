import unittest
import json
import database
import data_quality
from app import app

class TestDataQualitySuite(unittest.TestCase):
    def setUp(self):
        app.config['TESTING'] = True
        self.client = app.test_client()
        database.init_db()

    def test_quality_page_route(self):
        """Verify GET /quality returns HTTP 200 and renders quality suite HTML."""
        response = self.client.get('/quality')
        self.assertEqual(response.status_code, 200)
        self.assertIn(b'Research & Data Quality Suite', response.data)
        self.assertIn(b'Genealogical Anomaly Detector', response.data)

    def test_quality_audit_api(self):
        """Verify /api/quality/audit endpoint returns valid JSON with anomaly metrics."""
        response = self.client.get('/api/quality/audit')
        self.assertEqual(response.status_code, 200)
        
        data = json.loads(response.data)
        self.assertIn('total_anomalies', data)
        self.assertIn('severity_counts', data)
        self.assertIn('anomalies', data)
        self.assertIsInstance(data['anomalies'], list)

    def test_fulltext_search_api(self):
        """Verify /api/quality/search endpoint performs text searches without crashing."""
        response = self.client.get('/api/quality/search?q=Infantry')
        self.assertEqual(response.status_code, 200)
        
        data = json.loads(response.data)
        self.assertIn('query', data)
        self.assertEqual(data['query'], 'Infantry')
        self.assertIn('results', data)

    def test_stash_sync_api(self):
        """Verify /api/quality/sync endpoint returns stash health stats."""
        response = self.client.get('/api/quality/sync')
        self.assertEqual(response.status_code, 200)
        
        data = json.loads(response.data)
        self.assertIn('total_memorials', data)
        self.assertIn('sync_health', data)

if __name__ == '__main__':
    unittest.main()
