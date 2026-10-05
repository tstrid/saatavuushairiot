import importlib.util
import sys
import tempfile
import unittest
from pathlib import Path


SCRIPT = Path(__file__).parents[1] / "generate_medicine_registry.py"
SPEC = importlib.util.spec_from_file_location("generate_medicine_registry", SCRIPT)
MODULE = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = MODULE
SPEC.loader.exec_module(MODULE)


class GeneratorTests(unittest.TestCase):
    def test_clean_normalizes_delimiters_and_whitespace(self):
        self.assertEqual(MODULE.clean("  A;B\n C  "), "A,B C")

    def test_parse_and_rows_include_veterinary_and_alias(self):
        xml = """<?xml version="1.0" encoding="UTF-8"?>
        <Perusrekisteri>
          <Versiotiedot><Ajopvm>2026-10-05</Ajopvm></Versiotiedot>
          <Pakkaus Laakevalmiste-ref="REL1">
            <Pakkaustunnus>42</Pakkaustunnus><VNR-numero>123456</VNR-numero>
            <VanhaVNR>111111</VanhaVNR><Pakkauskokoteksti>10 ml</Pakkauskokoteksti>
            <Pakkauskokoyksikko value="ml"/><Annostelulaite value="Ruisku"/>
          </Pakkaus>
          <Pakkaus Laakevalmiste-ref="REL1">
            <Pakkaustunnus>43</Pakkaustunnus><VNR-numero>222222</VNR-numero>
            <Pakkauskokoteksti>20 ml</Pakkauskokoteksti><Kaupanolo><Kaupastapoistumispaiva>2026-10-04</Kaupastapoistumispaiva></Kaupanolo>
          </Pakkaus>
          <Laakevalmiste id="REL1"><Kauppanimi>Vetmed</Kauppanimi><Vahvuus>2 mg/ml</Vahvuus>
            <Laakemuoto value="injektioneste"/><HUM>0</HUM><VET>1</VET>
          </Laakevalmiste>
        </Perusrekisteri>"""
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "source.xml"
            source.write_text(xml, encoding="utf-8")
            source_date, packages, products = MODULE.parse_registry(source)
        rows = MODULE.rows_for(packages, products)
        self.assertEqual(source_date, "2026-10-05")
        self.assertEqual(rows[0][0], "111111")
        self.assertEqual(rows[0][1], "123456")
        self.assertEqual(rows[0][12], "1")
        self.assertEqual(rows[0][13], "0")
        self.assertEqual(rows[1][0], "123456")
        self.assertEqual(rows[1][13], "1")
        self.assertEqual(len(rows), 2)

    def test_current_vnr_wins_over_colliding_alias(self):
        product = MODULE.Product("Name", "1 mg", "tablet", "A01", "1234", "1", "0")
        packages = [
            MODULE.Package("A", "1", "123456", tuple(), "10", "kpl", ""),
            MODULE.Package("A", "2", "654321", ("123456",), "20", "kpl", ""),
        ]
        rows = MODULE.rows_for(packages, {"A": product})
        selected = next(row for row in rows if row[0] == "123456")
        self.assertEqual(selected[1], "123456")
        self.assertEqual(selected[13], "1")


if __name__ == "__main__":
    unittest.main()
