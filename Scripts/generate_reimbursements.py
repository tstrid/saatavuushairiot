#!/usr/bin/env python3
"""Publish a separate Kela feed; never changes medicines.txt or its schema."""
import argparse
from datetime import date
import io
import json
from pathlib import Path
import re
import urllib.request
import xml.etree.ElementTree as ET
import zipfile

BASE = "https://www.kanta.fi/kantafi-rest/kantafi-laaketietokanta"
NS = {"k": "http://www.kanta.fi/skeemat/laaketietokanta/2022"}
CODES = {"EK", "PK", "RRPK", "LRPK", "AEK", "YEK", "RRAEK", "RRYEK", "LPK", "RPK"}


def parse_snapshot(data, minimum_records=5000):
    root = ET.fromstring(data)
    version = root.findtext("k:Versionumero", namespaces=NS)
    start = root.findtext("k:Voimassaoloaika/k:Alkupvm", namespaces=NS)
    end = root.findtext("k:Voimassaoloaika/k:Loppupvm", namespaces=NS)
    if not version or not re.fullmatch(r"\d{4}\.\d{3}", version):
        raise ValueError("Invalid Kela version")
    if date.fromisoformat(start) > date.fromisoformat(end):
        raise ValueError("Invalid validity interval")
    packages = {}
    for package in root.findall("k:Laakepakkaus", NS):
        if package.findtext("k:Yksilointitiedot/k:TunnuksenTyyppi", namespaces=NS) != "1":
            continue
        vnr = package.findtext("k:Yksilointitiedot/k:Yksilointitunnus", namespaces=NS)
        if not vnr or not re.fullmatch(r"[0-9]{6}", vnr) or vnr in packages:
            raise ValueError("Invalid or duplicate VNR")
        codes = sorted({e.get("koodi") for e in package.findall("k:Korvausluokka", NS)})
        if not set(codes) <= CODES or ("EK" in codes and len(codes) > 1):
            raise ValueError("Unknown or contradictory reimbursement class")
        packages[vnr] = codes
    if len(packages) < minimum_records:
        raise ValueError("Incomplete Kela dataset")
    return dict(version=version, validFrom=start, validThrough=end, packages=packages)


def download(url):
    request = urllib.request.Request(url)
    with urllib.request.urlopen(request, timeout=120) as response:
        return response.read()


def build_feed(snapshots, today=None):
    today = (today or date.today()).isoformat()
    if len({s["version"] for s in snapshots}) != len(snapshots):
        raise ValueError("Duplicate versions")
    current = [s for s in snapshots if s["validFrom"] <= today <= s["validThrough"]]
    if not current:
        raise ValueError("No currently valid Kela dataset; keep previously published feed")
    selected = [max(current, key=lambda s: s["version"])]
    selected += [s for s in snapshots if s["validFrom"] > today]
    return dict(schemaVersion=1, source="Kela", snapshots=sorted(selected, key=lambda s: s["version"]))


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--xml", action="append", type=Path, help="Local XML for reproducible builds")
    parser.add_argument("--output", type=Path, default=Path("Data/reimbursements-v1.json"))
    args = parser.parse_args()
    snapshots = []
    if args.xml:
        snapshots = [parse_snapshot(p.read_bytes()) for p in args.xml]
    else:
        files = json.loads(download(BASE + "/files"))
        # Include future releases but keep searching until the current period is found.
        for item in sorted(files, key=lambda f: (f["year"], f["ver"]), reverse=True):
            url = BASE + f'/download/version?year={int(item["year"])}&ver={int(item["ver"])}'
            with zipfile.ZipFile(io.BytesIO(download(url))) as archive:
                xml_files = [i for i in archive.infolist() if i.filename.endswith(".xml")]
                if len(xml_files) != 1 or xml_files[0].file_size > 150_000_000:
                    raise ValueError("Unexpected Kela archive")
                snapshot = parse_snapshot(archive.read(xml_files[0]))
            if snapshot["version"] != f'{int(item["year"])}.{int(item["ver"]):03d}':
                raise ValueError("Source version mismatch")
            snapshots.append(snapshot)
            if snapshot["validFrom"] <= date.today().isoformat() <= snapshot["validThrough"]:
                break
    feed = build_feed(snapshots)
    content = json.dumps(feed, ensure_ascii=False, sort_keys=True, indent=2) + "\n"
    args.output.parent.mkdir(parents=True, exist_ok=True)
    temporary = args.output.with_suffix(".tmp")
    temporary.write_text(content, encoding="utf-8")
    temporary.replace(args.output)
    print([(s["version"], len(s["packages"])) for s in feed["snapshots"]])


if __name__ == "__main__":
    main()
