import copy
import json
from pathlib import Path
import sys
import unittest
import xml.etree.ElementTree as ET
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from generate_catalog import kela_snapshot, NS

class CatalogTests(unittest.TestCase):
    def test_shipped_catalog_typed_identity_and_creams(self):
        path = Path(__file__).resolve().parents[2] / 'Data/catalog-v2.json'
        feed = json.loads(path.read_text())
        records = {(r['identifierType'], r['number']): r for r in feed['records']}
        self.assertEqual(len(records), len(feed['records']))
        for number, name in [('810576', 'CANODERM'), ('810318', 'CERADERM')]:
            record = records['0', number]
            self.assertEqual(record['name'], name)
            self.assertEqual(record['kind'], '2')
            self.assertFalse(record['shortageCovered'])
            self.assertEqual(feed['snapshots'][0]['packages']['0:' + number], ['RRPK'])
        self.assertGreater(sum(r['identifierType'] == '1' for r in records.values()), 10000)

    def fixture(self):
        ns = NS['k']
        root = ET.Element('{' + ns + '}Laaketietokanta')
        def child(parent, name, text=None, **attrs):
            e = ET.SubElement(parent, '{' + ns + '}' + name, attrs)
            e.text = text
            return e
        child(root, 'Versionumero', '2026.022')
        period = child(root, 'Voimassaoloaika')
        child(period, 'Alkupvm', '2026-11-01'); child(period, 'Loppupvm', '2026-11-14')
        for i in range(5000):
            p = child(root, 'Laakepakkaus'); d = child(p, 'Yksilointitiedot')
            child(d, 'TunnuksenTyyppi', '1'); child(d, 'Yksilointitunnus', f'{i:06d}')
            child(d, 'ValmisteenLaji', code='1'); child(d, 'Kauppanimi', 'Test')
            child(p, 'Korvausluokka', koodi='PK')
        return root

    def test_same_number_different_namespace_is_preserved(self):
        root = self.fixture()
        extra = copy.deepcopy(root.findall('k:Laakepakkaus', NS)[0])
        extra.find('k:Yksilointitiedot/k:TunnuksenTyyppi', NS).text = '0'
        extra.find('k:Yksilointitiedot/k:ValmisteenLaji', NS).set('code', '2')
        root.append(extra)
        snapshot, products = kela_snapshot(ET.tostring(root))
        self.assertIn('0:000000', snapshot['packages'])
        self.assertIn('1:000000', snapshot['packages'])
        self.assertEqual(len(products), 5001)

    def test_duplicate_same_namespace_rejected(self):
        root = self.fixture()
        root.append(copy.deepcopy(root.findall('k:Laakepakkaus', NS)[0]))
        with self.assertRaises(ValueError):
            kela_snapshot(ET.tostring(root))

    def test_unsupported_identifier_type_rejected(self):
        root = self.fixture()
        root.find('k:Laakepakkaus/k:Yksilointitiedot/k:TunnuksenTyyppi', NS).text = '2'
        with self.assertRaises(ValueError):
            kela_snapshot(ET.tostring(root))
