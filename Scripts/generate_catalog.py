#!/usr/bin/env python3
"""Version 2 unified Fimea/Kela catalog. Never changes legacy publication files."""
import argparse
import csv
from datetime import date
import io
import json
from pathlib import Path
import re
import xml.etree.ElementTree as ET
import zipfile
from generate_reimbursements import BASE, NS, CODES, download
from generate_medicine_registry import HEADER, validate as validate_fimea


def kela_snapshot(data):
    root = ET.fromstring(data)
    def text(node, path):
        return node.findtext('/'.join('k:' + p for p in path.split('/')), default='', namespaces=NS)
    packages = {}
    products = []
    for p in root.findall('k:Laakepakkaus', NS):
        details = p.find('k:Yksilointitiedot', NS)
        kind = details.find('k:ValmisteenLaji', NS).get('code')
        typ = text(details, 'TunnuksenTyyppi')
        number = text(details, 'Yksilointitunnus')
        # Only known identifier domains are admitted. Preserve leading zeroes.
        if typ not in {'0', '1'} or not re.fullmatch(r'[0-9]{6}', number):
            raise ValueError('Unknown package identity')
        key = typ + ':' + number
        if key in packages:
            raise ValueError('Duplicate typed identity: ' + key)
        codes = sorted({e.get('koodi') for e in p.findall('k:Korvausluokka', NS)})
        if not set(codes) <= CODES or ('EK' in codes and len(codes) > 1):
            raise ValueError('Unknown reimbursement codes')
        packages[key] = codes
        form = details.find('k:Laakemuoto', NS)
        products.append(dict(
            identifierType=typ, number=number, kind=kind,
            name=text(details, 'Kauppanimi'), strength=text(details, 'Vahvuus'),
            dosageForm=form.get('suomeksi', '') if form is not None else '',
            packageSize=text(details, 'Pakkaus/KokoTeksti'), unit='',
            device=text(details, 'Pakkaus/Laite'), atcCode='', substitutionGroup='',
            currentNumber=number, packageIdentifier='', isHumanMedicine=True,
            isVeterinaryMedicine=False, isOnMarket=True, isCurrent=True,
            marketRemovalDate='', prescriptionStatus=text(p, 'MuutTiedot/Reseptistatus'),
            shortageCovered=typ == '1' and kind != '2'))
    start, end = text(root, 'Voimassaoloaika/Alkupvm'), text(root, 'Voimassaoloaika/Loppupvm')
    version = text(root, 'Versionumero')
    if date.fromisoformat(start) > date.fromisoformat(end) or not re.fullmatch(r'\d{4}\.\d{3}', version):
        raise ValueError('Invalid Kela period')
    if len(packages) < 5000:
        raise ValueError('Incomplete Kela release')
    return dict(version=version, validFrom=start, validThrough=end, packages=packages), products


def build_catalog(rows, releases, source_date, today=None):
    today = (today or date.today()).isoformat()
    validate_fimea(rows)
    current = [r for r in releases if r[0]['validFrom'] <= today <= r[0]['validThrough']]
    if not current:
        raise ValueError('No current Kela release')
    current = max(current, key=lambda r: r[0]['version'])
    selected = [current] + sorted([r for r in releases if r[0]['validFrom'] > today], key=lambda r: r[0]['version'])
    if len({r[0]['version'] for r in selected}) != len(selected):
        raise ValueError('Duplicate release')
    records = {}
    for r in rows:
        records['1:' + r[0]] = dict(
            identifierType='1', number=r[0], kind='medicine', currentNumber=r[1],
            packageIdentifier=r[2], name=r[3], strength=r[4], dosageForm=r[5],
            packageSize=r[6], unit=r[7], device=r[8], atcCode=r[9],
            substitutionGroup=r[10], isHumanMedicine=r[11] == '1',
            isVeterinaryMedicine=r[12] == '1', isOnMarket=r[13] == '1',
            isCurrent=r[14] == '1', marketRemovalDate=r[15],
            prescriptionStatus='', shortageCovered=True)
    # Current Kela release supplies prescription metadata and missing human medicines,
    # plus perusvoiteet. Future packages are not searchable before their effective day.
    for p in current[1]:
        key = p['identifierType'] + ':' + p['number']
        if key in records:
            records[key]['prescriptionStatus'] = p['prescriptionStatus']
            records[key]['kind'] = p['kind']
        elif p['kind'] == '2' or p['identifierType'] == '1':
            records[key] = p
    # Supplement package definitions per release so the app can activate future creams offline.
    supplements = {s['version']: [p for p in products if p['kind'] == '2'] for s, products in selected}
    return dict(schemaVersion=2, source='Fimea + Kela', fimeaSourceDate=source_date,
                records=sorted(records.values(), key=lambda p:(p['identifierType'], p['number'])),
                snapshots=[s for s, _ in selected], supplements=supplements)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--medicines', type=Path, default=Path('Data/medicines.txt'))
    parser.add_argument('--metadata', type=Path, default=Path('Data/medicines-metadata.json'))
    parser.add_argument('--xml', type=Path, action='append')
    parser.add_argument('--output', type=Path, default=Path('Data/catalog-v2.json'))
    args = parser.parse_args()
    with args.medicines.open() as f:
        reader = csv.reader(f, delimiter=';')
        if next(reader) != HEADER:
            raise ValueError('Unexpected legacy input schema')
        rows = list(reader)
    releases = []
    if args.xml:
        releases = [kela_snapshot(p.read_bytes()) for p in args.xml]
    else:
        files = json.loads(download(BASE + '/files'))
        for item in sorted(files, key=lambda x: (x['year'], x['ver']), reverse=True):
            with zipfile.ZipFile(io.BytesIO(download(BASE + f'/download/version?year={item["year"]}&ver={item["ver"]}'))) as z:
                names = [n for n in z.namelist() if n.endswith('.xml')]
                if len(names) != 1 or z.getinfo(names[0]).file_size > 150_000_000:
                    raise ValueError('Unexpected archive')
                release = kela_snapshot(z.read(names[0]))
            releases.append(release)
            if release[0]['validFrom'] <= date.today().isoformat() <= release[0]['validThrough']:
                break
    feed = build_catalog(rows, releases, json.loads(args.metadata.read_text())['fimeaSourceDate'])
    content = json.dumps(feed, ensure_ascii=False, sort_keys=True, indent=2) + '\n'
    args.output.parent.mkdir(parents=True, exist_ok=True)
    tmp = args.output.with_suffix('.tmp')
    tmp.write_text(content)
    tmp.replace(args.output)
    print('Catalog:', len(feed['records']), 'records;', len(feed['snapshots']), 'Kela releases')

if __name__ == '__main__':
    main()
