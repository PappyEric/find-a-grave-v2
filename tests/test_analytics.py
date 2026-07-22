import unittest
import os
import json
import sqlite3
import database
from app import app

class TestAnalyticsAndMultiPage(unittest.TestCase):
    def setUp(self):
        app.config['TESTING'] = True
        self.client = app.test_client()
        database.init_db()

    def test_page_routes(self):
        """Verify that all multi-page HTML routes return HTTP 200."""
        routes = ['/', '/tree', '/map', '/analytics']
        for route in routes:
            response = self.client.get(route)
            self.assertEqual(response.status_code, 200, f"Route {route} failed with status {response.status_code}")
            self.assertIn(b'Find a Grave', response.data, f"Route {route} missing branding")

    def test_analytics_api_endpoint(self):
        """Verify /api/analytics returns valid JSON with expected summary metrics."""
        response = self.client.get('/api/analytics')
        self.assertEqual(response.status_code, 200)
        
        data = json.loads(response.data)
        self.assertIn('total_memorials', data)
        self.assertIn('total_cemeteries', data)
        self.assertIn('total_veterans', data)
        self.assertIn('average_lifespan', data)
        self.assertIn('top_surnames', data)
        self.assertIn('age_distribution', data)
        self.assertIn('decades_timeline', data)

    def test_analytics_summary_data(self):
        """Test database.get_analytics_summary directly."""
        summary = database.get_analytics_summary()
        self.assertIsInstance(summary['total_memorials'], int)
        self.assertIsInstance(summary['top_surnames'], list)
        self.assertIsInstance(summary['age_distribution'], dict)

if __name__ == '__main__':
    unittest.main()
