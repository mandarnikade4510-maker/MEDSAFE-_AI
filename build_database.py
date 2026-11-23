"""Build extra medicine data from OFFICIAL FDA drug labels (openFDA) summarised by Gemini.

For every medicine name in a list this tool:
  1. downloads its official FDA drug label (free, no key needed),
  2. asks Gemini to turn ONLY that label text into the app's format (uses, side effects, precautions, interactions),
  3. saves everything in ../medicine_data_generated.json - the app loads it automatically.

Nothing is invented: if a medicine has no FDA label it is skipped and listed at the end.
Results are saved one by one in data_tools/cache/, so you can stop (Ctrl+C) and run again - it continues.

Usage (in the project folder, with the venv active and internet on):
    python data_tools/build_database.py                       # all missing medicines in india_medicines.txt
    python data_tools/build_database.py --names celecoxib,clindamycin
    python data_tools/build_database.py --list my_list.txt --delay 5
"""
import argparse
import json
import os
import re
import ssl
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)
from med_logic import _post_json, get_gemini_key, get_side_effects  # noqa: E402

CACHE = os.path.join(HERE, "cache")
DEFAULT_OUT = os.path.join(ROOT, "medicine_data_generated.json")
SSL_CTX = ssl.create_default_context()
SSL_CTX.verify_flags &= ~ssl.VERIFY_X509_STRICT
FIELDS = (("indications_and_usage", 1500), ("adverse_reactions", 3000), ("warnings_and_cautions", 2000), ("warnings", 2000),
          ("precautions", 1500), ("contraindications", 1200), ("drug_interactions", 3500))
VALID = {"critical", "severe", "moderate"}

PROMPT = """You are helping build a student medicine-safety app for India. Use ONLY the FDA label excerpts below for the medicine "{name}".
Write patient-friendly information in simple English and reply with ONLY a JSON object:
{{"uses": "one sentence: what it is used for",
 "common": ["3 to 6 short common side effects"],
 "serious": ["2 to 5 serious side effects or warning signs that need a doctor"],
 "precautions": "2 or 3 short sentences: who should be careful and how to take it",
 "interactions": [{{"with": "generic medicine name in lowercase", "severity": "critical|severe|moderate", "message": "one short sentence"}}]}}
Rules: interactions must name a specific medicine (not a class or enzyme) that the excerpts explicitly mention, maximum 8.
Use "critical" only for combinations the label forbids or that are life-threatening, "severe" for serious harm (bleeding, heart rhythm, toxicity), "moderate" otherwise.
If the excerpts do not say something, leave it out. Never add facts from memory.

FDA LABEL EXCERPTS:
{excerpts}
"""


def fetch_label(name):
    """Official FDA label text for a generic name, or None if the FDA has no label."""
    url = "https://api.fda.gov/drug/label.json?search=" + urllib.parse.quote(f'openfda.generic_name:"{name}"') + "&limit=1"
    try:
        with urllib.request.urlopen(url, timeout=30, context=SSL_CTX) as resp:
            res = json.loads(resp.read().decode("utf-8"))["results"][0]
    except urllib.error.HTTPError as exc:
        if exc.code == 404:
            return None
        raise
    parts = []
    for field, limit in FIELDS:
        text = " ".join(res.get(field, []))
        if text:
            parts.append(f"[{field}] {re.sub(chr(32) + '+', chr(32), text)[:limit]}")
    return "\n".join(parts) or None


def ask_gemini(prompt, key, model):
    """JSON text from Gemini, with patient retries when the free server is busy."""
    url = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"
    body = {"contents": [{"role": "user", "parts": [{"text": prompt}]}],
            "generationConfig": {"temperature": 0, "responseMimeType": "application/json", "maxOutputTokens": 2500}}
    for wait in (0, 20, 45, 90):
        time.sleep(wait)
        data, status = _post_json(url, body, {"content-type": "application/json", "x-goog-api-key": key}, timeout=90)
        if status == "ok":
            try:
                return "".join(p.get("text", "") for p in data["candidates"][0]["content"]["parts"])
            except (KeyError, IndexError, TypeError):
                return None
        if status == "auth":
            sys.exit("The Gemini key was rejected - check api_keys.py.")
    return None


def clean_list(items, lo, hi):
    out = [re.sub(r"\s+", " ", str(x)).strip() for x in (items or []) if str(x).strip()]
    return out[:hi] if len(out) >= lo else []


def validate(name, obj):
    """Check Gemini's answer; returns (entry, interactions) or None if unusable."""
    if not isinstance(obj, dict):
        return None
    entry = {"uses": re.sub(r"\s+", " ", str(obj.get("uses", ""))).strip(),
             "common": clean_list(obj.get("common"), 1, 6), "serious": clean_list(obj.get("serious"), 1, 5),
             "precautions": re.sub(r"\s+", " ", str(obj.get("precautions", ""))).strip(), "auto": True}
    if not (entry["uses"] and entry["common"] and entry["serious"] and entry["precautions"]):
        return None
    rules = []
    for it in (obj.get("interactions") or [])[:8]:
        other = str(it.get("with", "")).lower().strip() if isinstance(it, dict) else ""
        sev, msg = (it.get("severity"), str(it.get("message", "")).strip()) if isinstance(it, dict) else (None, "")
        if sev in VALID and msg and other != name and re.fullmatch(r"[a-z][a-z \-]{2,40}", other):
            rules.append({"a": [name], "b": [other], "severity": sev, "message": msg})
    return entry, rules


def merge(out_path):
    meds, rules, seen = {}, [], set()
    for fn in sorted(os.listdir(CACHE)):
        if fn.endswith(".json"):
            item = json.load(open(os.path.join(CACHE, fn), encoding="utf-8"))
            meds[item["name"]] = item["entry"]
            for r in item["interactions"]:
                k = frozenset((r["a"][0], r["b"][0]))
                if k not in seen:
                    seen.add(k)
                    rules.append(r)
    with open(out_path, "w", encoding="utf-8") as fh:
        json.dump({"_note": "Auto-summarised from official FDA drug labels with Gemini. Verify before real-world use.",
                   "medicines": meds, "interactions": rules}, fh, ensure_ascii=False, indent=1)
    return len(meds), len(rules)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--list", default=os.path.join(HERE, "india_medicines.txt"))
    ap.add_argument("--names", help="comma-separated names instead of a list file")
    ap.add_argument("--delay", type=float, default=4.0, help="seconds between medicines (free key allows ~15 per minute)")
    ap.add_argument("--model", default="gemini-flash-lite-latest")
    ap.add_argument("--out", default=DEFAULT_OUT)
    ap.add_argument("--force", action="store_true", help="also redo medicines the app already has")
    a = ap.parse_args()
    key = get_gemini_key()
    if not key:
        sys.exit("No Gemini key found - put it in api_keys.py first.")
    os.makedirs(CACHE, exist_ok=True)
    names = [n.strip().lower() for n in (a.names.split(",") if a.names else
             [l for l in open(a.list, encoding="utf-8").read().splitlines() if l.strip() and not l.startswith("#")])]
    todo = [n for n in dict.fromkeys(names)
            if not os.path.exists(os.path.join(CACHE, n.replace(" ", "_") + ".json")) and (a.force or not get_side_effects(n)["found"])]
    print(f"{len(names)} names, {len(todo)} to build.")
    no_label, failed = [], []
    for i, name in enumerate(todo, 1):
        try:
            excerpts = fetch_label(name)
        except Exception as exc:  # network problem: stop cleanly, progress is saved
            print("Could not reach the FDA server:", exc); break
        if not excerpts:
            no_label.append(name); print(f"[{i}/{len(todo)}] {name}: no FDA label"); continue
        raw = ask_gemini(PROMPT.format(name=name, excerpts=excerpts), key, a.model)
        try:
            result = validate(name, json.loads(re.sub(r"^```(?:json)?|```$", "", (raw or "").strip(), flags=re.M)))
        except ValueError:
            result = None
        if not result:
            failed.append(name); print(f"[{i}/{len(todo)}] {name}: could not build"); continue
        entry, rules = result
        json.dump({"name": name, "entry": entry, "interactions": rules},
                  open(os.path.join(CACHE, name.replace(" ", "_") + ".json"), "w", encoding="utf-8"), ensure_ascii=False)
        print(f"[{i}/{len(todo)}] {name}: ok ({len(rules)} interactions)")
        time.sleep(a.delay)
    m, r = merge(a.out)
    print(f"\nSaved {m} medicines and {r} interactions to {a.out}")
    if no_label:
        print("No FDA label (add by hand or skip):", ", ".join(no_label))
    if failed:
        print("Gemini could not build (run again later):", ", ".join(failed))


if __name__ == "__main__":
    main()
