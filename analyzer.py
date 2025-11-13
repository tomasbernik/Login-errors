#!/usr/bin/env python3
# analyzer.py
# Modul s jednotnou logikou pre analýzu BMW ISPA login chýb

import re
import json
from pathlib import Path
from datetime import datetime
import urllib.parse

# ===========================
# Regex vzory
# ===========================

ERROR_PATTERNS = [
    # (regex, label)
    (re.compile(r"Exception occurred:\s*Unknown error during authentication", re.I), "auth?"),
    (re.compile(r"Exception occurred:\s*find_by_image.*Element pointer", re.I), "auth?"),
    (re.compile(r"Exception occurred:\s*wait_for_condition.*automation_id", re.I), "Registrierung?"),
]

SKIP_NO_WORK = re.compile(r"couldn'?t find any potential processes", re.I)
PROCESS_PATTERN = re.compile(r"starting with process", re.I)


# ===========================
# Load companies from JSON
# ===========================

def load_companies(json_path: str | Path):
    """Načíta companies_locations.json a vytvorí mapu {company_name: {...}}"""
    json_path = Path(json_path)
    if not json_path.exists():
        return {}

    with open(json_path, "r", encoding="utf-8") as f:
        data = json.load(f)

    # companies sú dicty s "name", "URL_Name", "URL_ID"
    return {c["name"]: c for c in data.get("companies", []) if isinstance(c, dict)}


def make_job_link(company: str, cmap: dict) -> str | None:
    """Vytvorí link na ISPA job ak má URL_Name a URL_ID."""
    c = cmap.get(company)
    if not c:
        return None
    if not c.get("URL_Name") or not c.get("URL_ID"):
        return None
    return f"https://ispa.bmwgroup.net/jobdetails/{c['URL_Name']}/{c['URL_ID']}"


# ===========================
# Parsovanie názvu súboru
# ===========================

def parse_filename(path: Path) -> dict:
    """
    Vstup: Path("20251112-1001-nefzger-Nonnendammallee-order-bot-ispa-log.txt")
    Výstup:
    {
        "date": "20251112",
        "time": "10:01",
        "company_raw": "nefzger Nonnendammallee"
    }
    """
    name = path.stem.replace("-order-bot-ispa-log", "")
    parts = name.split("-")

    date_str = parts[0] if len(parts) > 0 else ""
    time_raw = parts[1] if len(parts) > 1 else ""
    rest = parts[2:] if len(parts) > 2 else []

    # HHMM → HH:MM
    def fmt_time(x):
        return f"{x[:2]}:{x[2:]}" if x.isdigit() and len(x) == 4 else x

    return {
        "date": date_str,
        "time": fmt_time(time_raw),
        "remaining_raw": " ".join(rest),
        "remaining_norm": " ".join(rest).lower().replace("-", "").replace(" ", "")
    }


# ===========================
# Analyzer jadro
# ===========================

def analyze_log_file(path: Path, company_map: dict) -> dict | None:
    """
    Vráti dict (jeden riadok výsledku) alebo None.
    """
    try:
        text = path.read_text(encoding="utf-8", errors="ignore")
    except Exception:
        return None

    # 1) total processes
    total_processes = len(PROCESS_PATTERN.findall(text))
    if total_processes != 0:
        return None  # chceme iba total=0

    # 2) skip "no work"
    if SKIP_NO_WORK.search(text):
        return None

    # 3) posledných pár riadkov
    tail = "\n".join(text.strip().splitlines()[-12:])
    label = "unknown"
    for rx, tag in ERROR_PATTERNS:
        if rx.search(tail):
            label = tag
            break

    # 4) Parsovanie filename
    meta = parse_filename(path)
    remaining_norm = meta["remaining_norm"]

    # 5) Nájsť firmu podľa mena v názve
    company = "Unknown"
    for comp_name in company_map.keys():
        norm = comp_name.lower().replace(" ", "").replace("-", "")
        if norm and norm in remaining_norm:
            company = comp_name
            break

    # 6) Lokácia (ak budeš mať v JSON)
    location = "Unknown"

    # 7) Link
    link = make_job_link(company, company_map) or ""

    return {
        "date": meta["date"],
        "time": meta["time"],
        "company": company,
        "location": location,
        "label": label,
        "link": link,
        "filename": path.name,
    }
