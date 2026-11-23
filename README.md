# Adding more medicines (up to 1000+)

Files here:
- `india_medicines.txt` and `india_medicines_extended.txt` - about 640 generic medicines used in India (add your own names, one per line).
- `build_database.py` - makes side effects, uses, precautions and interactions from official FDA drug labels.
- `import_ddinter.py` - adds many more interactions from the free DDInter database.

Steps (project folder, venv active, internet on, Gemini key in api_keys.py):
1. `python data_tools/build_database.py`            (about 172 missing medicines, 15-30 minutes; safe to stop and run again)
2. Download the DDInter CSV files into `data_tools/ddinter/`, then `python data_tools/import_ddinter.py`
3. Restart the app. New medicines appear with a warning: "Auto-summarised from the official FDA drug label".

To go beyond 300: add more names to `india_medicines.txt` (for example from the National List of Essential Medicines 2022 or the WHO Essential Medicines List) and run step 1 again.
Medicines with no FDA label (some Indian-only products) are listed at the end - add those by hand or skip them.
