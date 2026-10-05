# Weekly medicine registry update

Update the generated Fimea medicine registry in `https://github.com/tstrid/saatavuushairiot`.

1. Clone or update the repository and work on its default branch.
2. Run `python3 Scripts/generate_medicine_registry.py`. The generator must download the current-year Fimea `Perusrekisteri` XML, include current human and veterinary packages, and create historical VNR alias rows.
3. Run `python3 -m unittest discover -s Scripts/tests -v`.
4. Verify that `Data/medicines.txt` has exactly this 14-column header:
   `VNRNRO;NYKYINEN_VNRNRO;PAKKAUSTUNNUS;LAAKENIMI;VAHVUUS;LAAKEMUOTONIMI;PAKKAUSKOKO;YKSIKKO;LAITE;ATCKOODI;SUBSTITUUTIORYHMA;HUM;VET;VNR_VOIMASSA`
5. Inspect `Data/medicines-metadata.json`. Stop without publishing if validation or tests fail, the registry has fewer than 10,000 current records, either human or veterinary records are missing, or any medicine name is empty.
6. If the generated data did not change, report that no update was needed.
7. If it changed, commit only the generated data, metadata, generator, tests, and related documentation with a concise message containing the Fimea source date, then push to the default branch.
8. Report the source date, current/alias/human/veterinary record counts, output SHA-256, commit hash, and whether the push succeeded.

Do not modify the iOS application source code during this scheduled task.
