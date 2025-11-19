#!/usr/bin/env python3
# loginerror.py
# ------------------------------------------------------------
# Analýza ISPA logov na prípady login chýb.
# - Preskočí logy s "DEBUG - starting with process" (teda s procesmi)
# - Ignoruje "couldn't find any potential processes"
# - Vyhľadá vybrané "Exception occurred: ..." chyby
# - Nepíše Excel; vracia list dictov alebo vypíše do konzoly
# - Eviduje spracované logy v processed_files.txt (v tomto priečinku)
# - Voliteľne filtruje len dnešné logy (predvolené)
# ------------------------------------------------------------

import json
import re
import sys
import argparse
from pathlib import Path
from datetime import datetime
import urllib.parse
from dotenv import load_dotenv
load_dotenv()

# === Konštanty / vzory chýb ==============================================

ERROR_PATTERNS = [
    # (regex, label)
    (re.compile(r"Exception occurred:\s*Unknown error during authentication", re.I), "Authentifizierung"),
    (re.compile(r"Exception occurred:\s*find_by_image.*Element pointer", re.I), "Authentifizierung"),
    (re.compile(r"wait_for_condition.*'PART_image'", re.I | re.S), "Registrierung?"),
]

SKIP_HAS_PROCESSES_PATTERN = r"DEBUG\s*-\s*starting with process"
IGNORE_ZERO_NO_TASKS_PATTERN = r"couldn.?t\s+find any potential processes"

# === 1) Načítanie známych firiem a lokalít ===============================

def load_known_entities(json_path: str = None) -> dict:
        # po 'data = json.load(f)'
        # Debug pomocník:
        # print(f"[DEBUG] Loaded {len(data.get('companies', []))} companies and {len(data.get('locations', []))} locations from {path}")

    """
    Očakávaný formát:
      {
        "companies": ["Reisacher", ...] alebo [{"name": "Reisacher"}, ...],
        "locations": ["Ulm", ...] alebo [{"name": "Ulm"}, ...]
      }
    """
    
    # Ak cesta nebola zadaná, použijeme JSON v rovnakom priečinku ako loginerror.py
    if json_path is None:
        base_dir = Path(__file__).parent
        json_path = base_dir / "shared" / "config" / "companies_locations.json"
    path = Path(json_path)
    
    if not path.exists():
        # prázdna šablóna – ale analyzátor bude fungovať s "Unknown"
        template = {"companies": [], "locations": []}
        return template

    with open(path, "r", encoding="utf-8") as f:
        data = json.load(f)

    # Normalizačné mapy
    def get_name(x):
        return x.get("name") if isinstance(x, dict) else x

    data["companies"] = [get_name(x) for x in data.get("companies", []) if get_name(x)]
    data["locations"] = [get_name(x) for x in data.get("locations", []) if get_name(x)]
    return data

# === 2) Parsovanie názvu logu ============================================

def parse_filename(filename_stem: str, known: dict) -> dict:
    """
    filename_stem = meno súboru bez prípony (percent-decode, ostrániť prípony)
    Očakávame niečo ako: 2025-11-11-0515-<company>-<location>-order-bot-ispa-log
    - dátum = prvý token
    - čas   = druhý token
    - zvyšok využijeme na mapovanie firmy a lokality
    """
    decoded = urllib.parse.unquote(filename_stem)
    cleaned = decoded.replace("-order-bot-ispa-log", "")
    parts = cleaned.split("-")

    if len(parts) < 3:
        return {"date": "Unknown", "time": "Unknown", "company": "Unknown", "location": "Unknown"}

    date_str, time_str = parts[0], parts[1]
    remaining = " ".join(parts[2:])

    def norm(s: str) -> str:
        return str(s).lower().replace(" ", "").replace("-", "").replace("_", "")

    # Company
    company = next((c for c in known.get("companies", []) if norm(c) in norm(remaining)), None)
    # Location
    location = next((l for l in known.get("locations", []) if norm(l) in norm(remaining)), None)

    # Formát času: necháme ako v názve (napr. 0515 → 05:15)
    def format_time(s: str) -> str:
        s = s.strip()
        if len(s) == 4 and s.isdigit():
            return f"{s[:2]}:{s[2:]}"
        return s

    return {
        "date": date_str,
        "time": format_time(time_str),
        "company": company or "Unknown",
        "location": location or "Unknown",
    }

# === 3) Práca so zoznamom spracovaných ==================================

def _processed_file_path() -> Path:
    return Path(__file__).parent / "processed_files.txt"

def load_processed_list() -> set:
    p = _processed_file_path()
    if not p.exists():
        return set()
    return set(p.read_text(encoding="utf-8").splitlines())

def save_processed_list(processed: set) -> None:
    p = _processed_file_path()
    p.write_text("\n".join(sorted(processed)), encoding="utf-8")

# === 4) Analýza jedného logu =============================================

def _contains_any(patterns, text: str) -> bool:
    return any(re.search(p, text, flags=re.IGNORECASE) for p in patterns)

def analyze_log(file_path: Path, known: dict) -> dict | None:
    """
    Upravená verzia:
    - preskočí logy obsahujúce "Login successful"
    - NEpreskakuje logy bez chyby
    - ak sa nenájde ERROR_PATTERN, label zostane prázdny
    """
    try:
        text = file_path.read_text(encoding="utf-8", errors="ignore")
    except Exception:
        return None

    # 1) preskočiť úspešné logy
    if "Login successful" in text:
        return None

    # 2) nájsť chybu (ak nie je → label="")
    label = ""
    for pattern, lbl in ERROR_PATTERNS:
        if pattern.search(text):
            label = lbl
            break

    # 3) meta informácie z názvu súboru
    meta = parse_filename(file_path.stem, known)

    # 4) vrátiť výsledok – label môže byť prázdny
    return {
        "company": meta["company"],
        "location": meta["location"],
        "time": meta["time"],
        "label": label,
        "link": "",
    }



# === 5) Analýza priečinka s logmi =======================================

def analyze_logs_in_folder(
    folder: str | Path,
    only_today: bool,
    known: dict,
    processed_files: set,
    debug: bool = False
) -> tuple[list[dict], set]:
    """
    Prejde .txt logy v danom priečinku.
    - only_today=True → berie len logy, ktorých názov obsahuje dnešný YYYYMMDD
    - spracované súbory sa berú z processed_files
    Vráti (results_list, newly_processed_set)
    """
    folder = Path(folder)
    if not folder.exists():
        return [], set()

    today_str = datetime.now().strftime("%Y%m%d")
    results: list[dict] = []
    newly: set[str] = set()

    for file in sorted(folder.glob("*.txt")):
        name = file.name
        
                # --- Debug výpisy: pre každý súbor ukáž, čo sa deje ---
        if debug and only_today:
            print(f"[DEBUG] consider {name} | today_filter={'PASS' if today_str in name else 'FAIL'}")

        if only_today and today_str not in name:
            if debug:
                print(f"[DEBUG] skip {name}: not today")
            continue

        if name in processed_files:
            if debug:
                print(f"[DEBUG] skip {name}: already processed")
            continue

        if debug:
            meta_preview = parse_filename(file.stem, known)
            print(f"[DEBUG] meta {name} -> {meta_preview}")
        # ---------------------------------------------------------

        finding = analyze_log(file, known)

        if finding:
            if debug:
                print(f"[DEBUG] FOUND {name}: {finding}")
            results.append(finding)
        else:
            if debug:
                print(f"[DEBUG] no finding in {name}")

        newly.add(name)


    return results, newly

# === 6) CLI / modulové API ===============================================

def main(standalone: bool = True, folder: str | Path = "logs", only_today: bool = True, debug: bool = False):

    """
    - standalone=True  -> vypíše výsledky do konzoly vo formáte: "Firma, Standort, Uhrzeit, Link"
    - standalone=False -> vráti list dictov s rovnakými informáciami (bez printu)
    """
    base_dir = Path(__file__).parent
    known = load_known_entities(base_dir / "shared" / "config" / "companies_locations.json")

    processed = load_processed_list()
    results, newly = analyze_logs_in_folder(folder, only_today, known, processed, debug=debug)


    # aktualizuj processed_files.txt hneď po prebehnutí
    if newly:
        save_processed_list(processed.union(newly))

    if standalone:
        if results:
            for r in results:
                # Firma, Standort, Uhrzeit, Link
                print(f"{r['company']}, {r['location']}, {r['time']}, {r['link']}")
        else:
            print("✅ Žiadne nové login chyby pre dnešný deň.")
        return None
    else:
        return results

# === 7) Spúšťanie cez CLI ===============================================

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Analýza BMW ISPA login chýb v logoch.")
    parser.add_argument(
    "--debug",
    action="store_true",
    help="Ladiaci výpis: pre každý súbor ukáže dôvod preskočenia a parsované meta."
)
    parser.add_argument(
        "-f", "--folder",
        default="logs",
        help="Priečinok s logmi (predvolene: ./logs)"
    )
    parser.add_argument(
        "--all-dates",
        action="store_true",
        help="Analyzovať aj logy mimo dnešného dňa (inak iba dnešné)."
    )
    args = parser.parse_args()

    # only_today = True, ak nepoužijeme --all-dates
    main(
    standalone=True,
    folder=args.folder,
    only_today=not args.all_dates,
    debug=args.debug
)
