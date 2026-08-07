import unittest
import os
import sqlite3
import tempfile
import database
import io

class TestCountyDiscovery(unittest.TestCase):
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

    def test_schema_migration_and_custom_cemetery(self):
        cid = database.add_custom_cemetery(
            name="Old Pioneer Cemetery",
            state="West Virginia",
            county="Cabell County",
            location="Huntington",
            gps_lat=38.41,
            gps_lng=-82.43,
            source_doc="1883 County Atlas, p.42",
            notes="Found on historical land deed"
        )
        self.assertTrue(cid.startswith("HIST_"))
        
        cemeteries = database.get_county_cemeteries("West Virginia", "Cabell County")
        self.assertEqual(len(cemeteries), 1)
        cem = cemeteries[0]
        self.assertEqual(cem['name'], "Old Pioneer Cemetery")
        self.assertEqual(cem['is_custom'], 1)
        self.assertEqual(cem['source_doc'], "1883 County Atlas, p.42")

    def test_link_custom_cemetery_to_fag(self):
        cid = database.add_custom_cemetery(
            name="Historical Graveyard",
            state="West Virginia",
            county="Cabell County",
            source_doc="Historical Deed"
        )
        success, msg = database.link_custom_cemetery_to_fag(cid, "999999")
        self.assertTrue(success)

        cem = database.get_cemetery("999999")
        self.assertIsNotNone(cem)
        self.assertEqual(cem['id'], "999999")
        self.assertEqual(cem['is_custom'], 0)
        self.assertTrue(len(cem['fag_added_date']) > 0)

    def test_external_sync_updates(self):
        database.upsert_county_cemetery(
            cemetery_id="53841",
            name="Spring Hill Cemetery",
            location="Huntington, Cabell County, West Virginia",
            state="West Virginia",
            county="Cabell County",
            fag_burial_count=18000
        )

        database.update_cemetery_external_sync("53841", {
            'wikidata_qid': 'Q123456',
            'wikidata_confirmed': 1,
            'wikidata_date': '2026-08-06',
            'osm_id': 'way/987654',
            'osm_confirmed': 1
        })

        cem = database.get_cemetery("53841")
        self.assertEqual(cem['wikidata_qid'], 'Q123456')
        self.assertEqual(cem['wikidata_confirmed'], 1)
        self.assertEqual(cem['osm_id'], 'way/987654')
        self.assertEqual(cem['osm_confirmed'], 1)

if __name__ == '__main__':
    unittest.main()
