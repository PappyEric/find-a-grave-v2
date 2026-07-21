import unittest
import sqlite3
import os
import sys

# Ensure root folder is in python path
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

import gedcom_exporter

class TestGedcomExport(unittest.TestCase):

    def test_format_gedcom_date(self):
        self.assertEqual(gedcom_exporter.format_gedcom_date('10 Nov 1925'), '10 NOV 1925')
        self.assertEqual(gedcom_exporter.format_gedcom_date('1925-11-10'), '10 NOV 1925')
        self.assertEqual(gedcom_exporter.format_gedcom_date('Nov 1925'), 'NOV 1925')
        self.assertEqual(gedcom_exporter.format_gedcom_date('1925'), '1925')
        self.assertEqual(gedcom_exporter.format_gedcom_date('abt 1925'), 'ABT 1925')
        self.assertEqual(gedcom_exporter.format_gedcom_date('Unknown'), None)

    def test_infer_gender(self):
        # Maiden name
        mem_f = {'id': '1', 'maiden_name': 'Jones', 'prefix': '', 'bio': ''}
        self.assertEqual(gedcom_exporter.infer_gender(mem_f, []), 'F')

        # Prefix
        mem_m = {'id': '2', 'maiden_name': '', 'prefix': 'Mr.', 'bio': ''}
        self.assertEqual(gedcom_exporter.infer_gender(mem_m, []), 'M')

        # Bio pronouns
        mem_bio = {'id': '3', 'maiden_name': '', 'prefix': '', 'bio': 'He was born in Ohio. He served in the Army. He died peacefully.'}
        self.assertEqual(gedcom_exporter.infer_gender(mem_bio, []), 'M')

    def test_parse_name_parts(self):
        mem = {
            'first_name': 'John',
            'middle_name': 'William',
            'last_name': 'Smith',
            'prefix': 'Mr.',
            'suffix': 'Jr.',
            'nickname': 'Johnny'
        }
        parsed = gedcom_exporter.parse_name_parts(mem)
        self.assertEqual(parsed['full_gedcom_name'], 'John William /Smith/ Jr.')
        self.assertEqual(parsed['given'], 'John William')
        self.assertEqual(parsed['surname'], 'Smith')

    def test_generate_gedcom_structure(self):
        # Create mock memorials and relationships
        memorials = {
            '101': {
                'id': '101', 'name': 'John Smith', 'first_name': 'John', 'last_name': 'Smith',
                'prefix': 'Mr.', 'birth_date': '10 Nov 1880', 'birth_location': 'Boston, MA',
                'death_date': '15 May 1950', 'death_location': 'Boston, MA',
                'cemetery_name': 'Greenwood Cemetery', 'cemetery_location': 'Boston, MA',
                'plot': 'Section A, Lot 12', 'gps_lat': 42.3601, 'gps_lng': -71.0589,
                'bio': 'He was a respected teacher.', 'url': 'https://www.findagrave.com/memorial/101'
            },
            '102': {
                'id': '102', 'name': 'Mary Jones Smith', 'first_name': 'Mary', 'maiden_name': 'Jones', 'last_name': 'Smith',
                'birth_date': '1885', 'death_date': '1960', 'cemetery_name': 'Greenwood Cemetery',
                'bio': 'She was an artist.', 'url': 'https://www.findagrave.com/memorial/102'
            },
            '103': {
                'id': '103', 'name': 'Robert Smith', 'first_name': 'Robert', 'last_name': 'Smith',
                'birth_date': '1910', 'death_date': '1980', 'url': 'https://www.findagrave.com/memorial/103'
            }
        }

        relationships = [
            {'from_memorial_id': '103', 'to_memorial_id': '101', 'relationship_type': 'parent'},
            {'from_memorial_id': '103', 'to_memorial_id': '102', 'relationship_type': 'parent'},
            {'from_memorial_id': '101', 'to_memorial_id': '102', 'relationship_type': 'spouse'}
        ]

        gedcom_text = gedcom_exporter.generate_gedcom_content(memorials, relationships)

        # Assert structural requirements
        self.assertTrue(gedcom_text.startswith('0 HEAD'))
        self.assertTrue(gedcom_text.strip().endswith('0 TRLR'))
        self.assertIn('1 SOUR FindAGraveTools', gedcom_text)
        self.assertIn('0 @I101@ INDI', gedcom_text)
        self.assertIn('1 NAME John /Smith/', gedcom_text)
        self.assertIn('1 SEX M', gedcom_text)
        self.assertIn('2 DATE 10 NOV 1880', gedcom_text)
        self.assertIn('2 MAP', gedcom_text)
        self.assertIn('3 LATI 42.3601', gedcom_text)
        self.assertIn('0 @I102@ INDI', gedcom_text)
        self.assertIn('1 NAME Mary /Jones/', gedcom_text)
        self.assertIn('1 SEX F', gedcom_text)
        self.assertIn('0 @F1@ FAM', gedcom_text)
        self.assertIn('1 HUSB @I101@', gedcom_text)
        self.assertIn('1 WIFE @I102@', gedcom_text)
        self.assertIn('1 CHIL @I103@', gedcom_text)
        self.assertIn('1 FAMC @F1@', gedcom_text)
        self.assertIn('1 FAMS @F1@', gedcom_text)

    def test_database_export_run(self):
        # Test database export functions against actual DB if stashed records exist
        db_path = 'stash/find_a_grave_v2.db'
        if os.path.exists(db_path):
            conn = sqlite3.connect(db_path, timeout=30.0)
            cursor = conn.cursor()
            cursor.execute("SELECT id FROM memorials LIMIT 1;")
            row = cursor.fetchone()
            conn.close()

            if row:
                focus_id = str(row[0])
                focus_gedcom = gedcom_exporter.export_focus_person_gedcom(focus_id)
                self.assertIsNotNone(focus_gedcom)
                self.assertTrue(focus_gedcom.startswith('0 HEAD'))
                self.assertTrue(focus_gedcom.strip().endswith('0 TRLR'))
                self.assertIn(f'0 @I{focus_id}@ INDI', focus_gedcom)

            conn2 = sqlite3.connect(db_path, timeout=30.0)
            cursor2 = conn2.cursor()
            cursor2.execute("SELECT cemetery_id FROM memorials WHERE cemetery_id IS NOT NULL LIMIT 1;")
            cem_row = cursor2.fetchone()
            conn2.close()

            if cem_row:
                cem_id = cem_row[0]
                cem_gedcom = gedcom_exporter.export_cemetery_gedcom(cem_id)
                if cem_gedcom:
                    self.assertTrue(cem_gedcom.startswith('0 HEAD'))
                    self.assertTrue(cem_gedcom.strip().endswith('0 TRLR'))

if __name__ == '__main__':
    unittest.main()
