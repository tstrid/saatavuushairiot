import sys
from pathlib import Path
from datetime import date
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from generate_reimbursements import parse_snapshot, build_feed


def xml(classes='<Korvausluokka koodi="PK"/>', vnr="000123"):
    return f'''<Laaketietokanta xmlns="http://www.kanta.fi/skeemat/laaketietokanta/2022">
    <Voimassaoloaika><Alkupvm>2026-11-01</Alkupvm><Loppupvm>2026-11-14</Loppupvm></Voimassaoloaika>
    <Versionumero>2026.022</Versionumero><Laakepakkaus>
    <Yksilointitiedot><TunnuksenTyyppi>1</TunnuksenTyyppi><Yksilointitunnus>{vnr}</Yksilointitunnus></Yksilointitiedot>
    {classes}</Laakepakkaus></Laaketietokanta>'''


class ReimbursementTests(unittest.TestCase):
    def test_multiple_classes_and_leading_zero(self):
        s = parse_snapshot(xml('<Korvausluokka koodi="PK"/><Korvausluokka koodi="AEK"><ErityiskorvattavaSairaus koodi="203"/></Korvausluokka>'), 1)
        self.assertEqual(s['packages'], {'000123': ['AEK', 'PK']})

    def test_non_reimbursed_and_unknown_are_distinct(self):
        self.assertEqual(parse_snapshot(xml('<Korvausluokka koodi="EK"/>'), 1)['packages']['000123'], ['EK'])
        self.assertEqual(parse_snapshot(xml(''), 1)['packages']['000123'], [])

    def test_reject_corrupt_or_incomplete_data(self):
        for data in [xml(vnr='123'), xml('<Korvausluokka koodi="NEW"/>'), xml('<Korvausluokka koodi="EK"/><Korvausluokka koodi="PK"/>'), '<broken>']:
            with self.subTest(data=data), self.assertRaises(Exception):
                parse_snapshot(data, 1)
        with self.assertRaises(ValueError):
            parse_snapshot(xml())

    def test_future_release_keeps_current_and_correction_wins(self):
        current = parse_snapshot(xml(), 1)
        corrected = dict(current, version='2026.023')
        future = dict(current, version='2026.024', validFrom='2026-11-15', validThrough='2026-11-30')
        feed = build_feed([current, corrected, future], date(2026, 11, 1))
        self.assertEqual([s['version'] for s in feed['snapshots']], ['2026.023', '2026.024'])
        with self.assertRaises(ValueError):
            build_feed([future], date(2026, 11, 1))
        with self.assertRaises(ValueError):
            build_feed([current], date(2026, 12, 1))


if __name__ == '__main__':
    unittest.main()
