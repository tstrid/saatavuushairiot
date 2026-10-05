#!/usr/bin/env python3
"""Build the compact app medicine registry from Fimea's XML export."""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import re
import shutil
import tempfile
import urllib.request
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path


HEADER = [
    "VNRNRO",
    "NYKYINEN_VNRNRO",
    "PAKKAUSTUNNUS",
    "LAAKENIMI",
    "VAHVUUS",
    "LAAKEMUOTONIMI",
    "PAKKAUSKOKO",
    "YKSIKKO",
    "LAITE",
    "ATCKOODI",
    "SUBSTITUUTIORYHMA",
    "HUM",
    "VET",
    "VNR_VOIMASSA",
]
VNR_PATTERN = re.compile(r"^[0-9]{6}$")


def clean(value: str | None) -> str:
    if not value:
        return ""
    return " ".join(value.replace(";", ",").split())


def attribute_value(element: ET.Element | None) -> str:
    if element is None:
        return ""
    return clean(element.attrib.get("value") or element.text)


@dataclass(frozen=True)
class Product:
    name: str
    strength: str
    dosage_form: str
    atc_code: str
    substitution_group: str
    human: str
    veterinary: str


@dataclass(frozen=True)
class Package:
    product_reference: str
    package_identifier: str
    current_vnr: str
    previous_vnrs: tuple[str, ...]
    package_size: str
    unit: str
    device: str


def local_source(source: str, temporary_directory: Path) -> Path:
    source_path = Path(source)
    if source_path.exists():
        return source_path

    destination = temporary_directory / "Perusrekisteri.xml"
    request = urllib.request.Request(source, headers={"User-Agent": "Saatavuushairiot-registry-builder/1.0"})
    with urllib.request.urlopen(request, timeout=180) as response, destination.open("wb") as output:
        shutil.copyfileobj(response, output)
    return destination


def parse_registry(source: Path) -> tuple[str, list[Package], dict[str, Product]]:
    source_date = ""
    packages: list[Package] = []
    products: dict[str, Product] = {}

    for _, element in ET.iterparse(source, events=("end",)):
        if element.tag == "Ajopvm" and not source_date:
            source_date = clean(element.text)
        elif element.tag == "Pakkaus":
            current_vnr = clean(element.findtext("VNR-numero"))
            withdrawal_date = clean(element.findtext("Kaupanolo/Kaupastapoistumispaiva"))
            is_current_package = not withdrawal_date or withdrawal_date >= source_date
            if current_vnr and is_current_package:
                packages.append(
                    Package(
                        product_reference=clean(element.attrib.get("Laakevalmiste-ref")),
                        package_identifier=clean(element.findtext("Pakkaustunnus")),
                        current_vnr=current_vnr,
                        previous_vnrs=tuple(
                            clean(item.text)
                            for item in element.findall("VanhaVNR")
                            if clean(item.text)
                        ),
                        package_size=clean(element.findtext("Pakkauskokoteksti")),
                        unit=attribute_value(element.find("Pakkauskokoyksikko")),
                        device=attribute_value(element.find("Annostelulaite")),
                    )
                )
            element.clear()
        elif element.tag == "Laakevalmiste":
            reference = clean(element.attrib.get("id"))
            if reference:
                products[reference] = Product(
                    name=clean(element.findtext("Kauppanimi")),
                    strength=clean(element.findtext("Vahvuus")),
                    dosage_form=attribute_value(element.find("Laakemuoto")),
                    atc_code=attribute_value(element.find("ATC-koodi")),
                    substitution_group=attribute_value(element.find("Substituutioryhma")),
                    human=clean(element.findtext("HUM")) or "0",
                    veterinary=clean(element.findtext("VET")) or "0",
                )
            element.clear()

    if not source_date:
        raise ValueError("The XML does not contain Versiotiedot/Ajopvm")
    return source_date, packages, products


def rows_for(packages: list[Package], products: dict[str, Product]) -> list[list[str]]:
    candidates: list[list[str]] = []
    for package in packages:
        product = products.get(package.product_reference)
        if product is None:
            raise ValueError(f"Missing product {package.product_reference} for package {package.package_identifier}")

        common = [
            package.current_vnr,
            package.package_identifier,
            product.name,
            product.strength,
            product.dosage_form,
            package.package_size,
            package.unit,
            package.device,
            product.atc_code,
            product.substitution_group,
            product.human,
            product.veterinary,
        ]
        candidates.append([package.current_vnr, *common, "1"])
        for previous_vnr in package.previous_vnrs:
            if previous_vnr != package.current_vnr:
                candidates.append([previous_vnr, *common, "0"])

    # Core Data uses VNR as the lookup key. Prefer a current assignment if an old
    # alias collides with it, then select deterministically among duplicate source rows.
    candidates.sort(key=lambda row: (row[0], -int(row[13]), row[1], row[2]))
    rows_by_vnr: dict[str, list[str]] = {}
    for row in candidates:
        rows_by_vnr.setdefault(row[0], row)
    return list(rows_by_vnr.values())


def validate(rows: list[list[str]]) -> dict[str, int]:
    if not rows:
        raise ValueError("No medicine rows were generated")
    if any(len(row) != len(HEADER) for row in rows):
        raise ValueError(f"At least one generated row does not have {len(HEADER)} columns")

    invalid_vnrs = sorted({row[0] for row in rows if not VNR_PATTERN.fullmatch(row[0])})
    invalid_current_vnrs = sorted({row[1] for row in rows if not VNR_PATTERN.fullmatch(row[1])})
    if invalid_vnrs or invalid_current_vnrs:
        raise ValueError(f"Invalid VNR values: {(invalid_vnrs + invalid_current_vnrs)[:10]}")
    if any(row[11] not in {"0", "1"} or row[12] not in {"0", "1"} for row in rows):
        raise ValueError("HUM and VET must contain only 0 or 1")
    if any(row[13] not in {"0", "1"} for row in rows):
        raise ValueError("VNR_VOIMASSA must contain only 0 or 1")

    current_rows = [row for row in rows if row[13] == "1"]
    alias_rows = [row for row in rows if row[13] == "0"]
    if len(current_rows) < 10_000:
        raise ValueError(f"Only {len(current_rows)} current records were generated")
    if not any(row[11] == "1" for row in current_rows):
        raise ValueError("No human medicines were generated")
    if not any(row[12] == "1" for row in current_rows):
        raise ValueError("No veterinary medicines were generated")
    if not alias_rows:
        raise ValueError("No previous-VNR aliases were generated")

    current_rows_with_substitution_group = [row for row in current_rows if row[10]]
    substitution_groups = {row[10] for row in current_rows_with_substitution_group}
    if len(current_rows_with_substitution_group) < 1_000 or len(substitution_groups) < 100:
        raise ValueError(
            "Substitution-group data is unexpectedly sparse: "
            f"{len(current_rows_with_substitution_group)} records in "
            f"{len(substitution_groups)} groups"
        )

    empty_names = sum(not row[3] for row in rows)
    if empty_names / len(rows) > 0.20:
        raise ValueError(f"Too many empty medicine names: {empty_names}/{len(rows)}")

    identities: dict[tuple[str, str], tuple[str, ...]] = {}
    for row in current_rows:
        key = (row[0], row[2])
        identity = tuple(row[1:13])
        previous = identities.setdefault(key, identity)
        if previous != identity:
            raise ValueError(f"Conflicting current record for VNR/package {key}")

    return {
        "recordCount": len(rows),
        "currentRecordCount": len(current_rows),
        "aliasRecordCount": len(alias_rows),
        "humanRecordCount": sum(row[11] == "1" for row in current_rows),
        "veterinaryRecordCount": sum(row[12] == "1" for row in current_rows),
        "currentRecordsWithSubstitutionGroup": len(current_rows_with_substitution_group),
        "substitutionGroupCount": len(substitution_groups),
        "emptyNameCount": empty_names,
    }


def encode(rows: list[list[str]]) -> bytes:
    with tempfile.SpooledTemporaryFile(mode="w+", encoding="utf-8", newline="") as stream:
        writer = csv.writer(stream, delimiter=";", lineterminator="\n")
        writer.writerow(HEADER)
        writer.writerows(rows)
        stream.seek(0)
        return stream.read().encode("utf-8")


def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def main() -> None:
    year = datetime.now(timezone.utc).year
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--source",
        default=f"https://data.pilvi.fimea.fi/avoin-data/Perusrekisteri{year}.xml",
    )
    parser.add_argument("--output", default="Data/medicines.txt")
    parser.add_argument("--metadata", default="Data/medicines-metadata.json")
    parser.add_argument("--source-label", help="Canonical source URL recorded in metadata")
    arguments = parser.parse_args()

    output_path = Path(arguments.output)
    metadata_path = Path(arguments.metadata)
    with tempfile.TemporaryDirectory() as temporary_directory_name:
        source_path = local_source(arguments.source, Path(temporary_directory_name))
        source_bytes_hash = hashlib.sha256()
        with source_path.open("rb") as source_stream:
            for block in iter(lambda: source_stream.read(1024 * 1024), b""):
                source_bytes_hash.update(block)

        source_date, packages, products = parse_registry(source_path)
        rows = rows_for(packages, products)
        counts = validate(rows)
        output = encode(rows)
        if len(output) >= 100_000_000:
            raise ValueError(f"Generated file is too large for normal GitHub storage: {len(output)} bytes")

    output_hash = sha256(output)
    existing_hash = sha256(output_path.read_bytes()) if output_path.exists() else None
    if existing_hash == output_hash:
        print(json.dumps({"changed": False, "sha256": output_hash, **counts}, ensure_ascii=False))
        return

    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_bytes(output)
    metadata = {
        "generatedAt": datetime.now(timezone.utc).isoformat(),
        "fimeaSourceDate": source_date,
        "sourceURL": arguments.source_label or arguments.source,
        "sourceSHA256": source_bytes_hash.hexdigest(),
        **counts,
        "outputByteCount": len(output),
        "outputSHA256": output_hash,
        "validationStatus": "passed",
    }
    metadata_path.parent.mkdir(parents=True, exist_ok=True)
    metadata_path.write_text(json.dumps(metadata, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"changed": True, **metadata}, ensure_ascii=False))


if __name__ == "__main__":
    main()
