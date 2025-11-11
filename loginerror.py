#!/usr/bin/env python3
# monitoring_bmw_loginerror.py
# ------------------------------------------------------------
# Analýza denníkov ISPA logov – hľadá logy s total_processes = 0
# a prípadne chybou "Unknown error during authentication" na konci súboru.
# Výstup: Excelová tabuľka (.xlsx)
# ------------------------------------------------------------

import re
import json
from pathlib import Path
from datetime import datetime
import urllib.parse
import pandas as pd


# === 1️⃣ Načítanie známych firiem a standortov ==========================

def load_known_entities(json_path="shared/config/companies_locations.json"):
    path = Path(json_path)
    if not path.exists():
        print(f"⚠️ Súbor {json_path} neexistuje – vytváram prázdny template.")
        template = {"companies": [], "locations": []}
        path.write_text(json.dumps(template, indent=2, ensure_ascii=False), encoding="utf-8")
        return template

    with open(path, "r", encoding="utf-8") as f:
        data = json.load(f)

    if data.get("locations") and isinstance(data["locations"][0], dict):
        data["location_map"] = {l["name"]: l for l in data["locations"]}
    return data


# === 2️⃣ Parsovanie názvu súboru =======================================

def parse_filename(filename, known):
    filename = urllib.parse.unquote(filename)
    filename = filename.replace("-order-bot-ispa-log", "")
    parts = filename.split("-")

    if len(parts) < 3:
        print(f"⚠️ Súbor {filename} nemá očakávaný formát názvu.")
        return {"date": "Unknown", "time": "Unknown", "company": "Unknown", "location": "Unknown"}

    date_str, time_str = parts[0], parts[1]
    remaining = " ".join(parts[2:])

    def normalize(s):
        return str(s).lower().replace(" ", "").replace("-", "")

    company = next((c for c in known["companies"] if normalize(c) in normalize(remaining)), None)

    locations = known.get("locations", [])
    location = None
    for loc in locations:
        name = loc["name"] if isinstance(loc, dict) else loc
        if normalize(name) in normalize(remaining):
            location = name
            break

    return {
        "date": date_str,
        "time": time_str,
        "company": company or "Unknown",
        "location": location or "Unknown"
    }


# === 3️⃣ Hlavná analýza logu ===========================================

def analyze_log(file_path, known):
    """Analyzuje jeden log a vráti dictionary pre Excel ak total=0."""
    file_path = Path(file_path)
    filename = file_path.stem
    meta = parse_filename(filename, known)

    with open(file_path, "r", encoding="utf-8", errors="ignore") as f:
        text = f.read()

    # === Zisti počet procesov ===
    total_match = re.findall(r"starting with process", text, re.IGNORECASE)
    total_processes = len(total_match)

    # === Podmienka: total = 0 ===
    if total_processes != 0:
        return None

    # === Over, či je "Unknown error during authentication" na konci ===
    end_lines = "\n".join(text.strip().splitlines()[-5:])  # posledných 5 riadkov
    if "Unknown error during authentication" in end_lines:
        error_type = "authentification"
    else:
        error_type = "unknown"

    return {
        "date": meta["date"],
        "time": meta["time"],
        "company": meta["company"],
        "location": meta["location"],
        "total": total_processes,
        "error type": error_type
    }


# === 4️⃣ Uloženie spracovaných súborov ================================

def load_processed_list(path="processed_files.txt"):
    p = Path(path)
    if not p.exists():
        return set()
    return set(p.read_text(encoding="utf-8").splitlines())


def save_processed_list(processed, path="processed_files.txt"):
    p = Path(path)
    p.write_text("\n".join(sorted(processed)), encoding="utf-8")


# === 5️⃣ Hlavná funkcia ================================================

def main():
    base_folder = Path(__file__).parent
    logs_folder = base_folder / "logs"
    known = load_known_entities()

    today = datetime.now().strftime("%Y-%m-%d")
    processed_files = load_processed_list()

    results = []
    newly_processed = set()

    for file in logs_folder.glob("*.txt"):
        if today not in file.name:
            continue
        if file.name in processed_files:
            continue

        result = analyze_log(file, known)
        if result:
            results.append(result)

        newly_processed.add(file.name)

    # Aktualizuj zoznam spracovaných logov
    save_processed_list(processed_files.union(newly_processed))

    if not results:
        print("✅ Žiadne nové logy s total=0 pre dnešný deň.")
        return

    # === Vytvor Excel tabuľku ===
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    xlsx_name = f"summary_loginerror_{timestamp}.xlsx"
    xlsx_path = base_folder / xlsx_name

    df = pd.DataFrame(results, columns=["date", "time", "company", "location", "total", "error type"])
    df.to_excel(xlsx_path, index=False)
    print(f"📊 Excel tabuľka vytvorená: {xlsx_path}")


# === 6️⃣ Spustenie =====================================================

if __name__ == "__main__":
    main()
