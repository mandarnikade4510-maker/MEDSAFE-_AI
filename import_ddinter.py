"""Add many more drug-drug interactions from DDInter (free academic database, https://ddinter.scbdd.com).

1. On the DDInter site open "Download" and save the CSV files (one per drug category) into data_tools/ddinter/.
2. Run:   python data_tools/import_ddinter.py
3. It writes ../medicine_data_ddinter.json (loaded automatically by the app). Your hand-checked rules always win.

Only "Major" (becomes severe) and "Moderate" interactions between medicines the app knows are imported.
DDInter uses US drug names (acetaminophen, albuterol...) - they are converted to the Indian names used in the app.
If you use DDInter in your report, cite it and check its licence terms for non-commercial use.
"""
import csv
import glob
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)
import med_logic  # noqa: E402

ALIAS = {"acetaminophen": "paracetamol", "albuterol": "salbutamol", "levalbuterol": "levosalbutamol", "epinephrine": "adrenaline",
         "phenobarbital": "phenobarbitone", "valproic acid": "valproate", "divalproex": "valproate", "ascorbic acid": "vitamin c",
         "cyanocobalamin": "vitamin b12", "folate": "folic acid", "retinol": "vitamin a", "phytonadione": "vitamin k",
         "ethinylestradiol": "ethinyl estradiol", "glyburide": "glibenclamide", "isosorbide mononitrate": "isosorbide mononitrate"}
LEVEL = {"major": "severe", "moderate": "moderate"}
TEXT = {"severe": "Major interaction (DDInter database): can cause serious harm. Ask a doctor or pharmacist before combining.",
        "moderate": "Moderate interaction (DDInter database): may need a dose change or monitoring. Ask a doctor or pharmacist."}


def resolve(name, wanted):
    n = ALIAS.get(name.lower().strip(), name.lower().strip())
    n = med_logic.normalize_drug_name(n)
    return n if n in wanted else None


def main():
    files = glob.glob(os.path.join(HERE, "ddinter", "*.csv"))
    if not files:
        sys.exit("Put the DDInter CSV files in data_tools/ddinter/ first.")
    wanted = set(med_logic.KNOWN_DRUGS)
    listed = os.path.join(HERE, "india_medicines.txt")
    wanted |= {l.strip().lower() for l in open(listed, encoding="utf-8") if l.strip() and not l.startswith("#")}
    gen = os.path.join(ROOT, "medicine_data_generated.json")
    if os.path.exists(gen):
        wanted |= set(json.load(open(gen, encoding="utf-8")).get("medicines", {}))
    rules, seen = [], set()
    for f in files:
        for row in csv.DictReader(open(f, encoding="utf-8-sig")):
            low = {k.lower().replace(" ", "_"): v for k, v in row.items() if k}
            a = next((v for k, v in low.items() if k in ("drug_a", "druga", "drug_1")), None)
            b = next((v for k, v in low.items() if k in ("drug_b", "drugb", "drug_2")), None)
            lv = next((v for k, v in low.items() if k in ("level", "severity")), "")
            sev = LEVEL.get(str(lv).lower().strip())
            if not (a and b and sev):
                continue
            x, y = resolve(a, wanted), resolve(b, wanted)
            if x and y and x != y and frozenset((x, y)) not in seen:
                seen.add(frozenset((x, y)))
                rules.append({"a": [x], "b": [y], "severity": sev, "message": TEXT[sev]})
    out = os.path.join(ROOT, "medicine_data_ddinter.json")
    json.dump({"_note": "From DDInter (Major and Moderate only).", "interactions": rules}, open(out, "w", encoding="utf-8"), indent=1)
    print(f"Saved {len(rules)} interactions to {out}")


if __name__ == "__main__":
    main()
