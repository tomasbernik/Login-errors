#!/usr/bin/env python3
# pipeline.py — clean verzia, ktorá používa analyzer.py

import os
import re
import smtplib
import urllib.parse
from pathlib import Path
from datetime import datetime
from email.message import EmailMessage

import requests
from requests.auth import HTTPBasicAuth

from loginerror import load_known_entities, parse_filename, analyze_log



# =============================
# ENV variables (GitHub Secrets)
# =============================

NEXTCLOUD_PUBLIC_URL   = os.environ["NEXTCLOUD_PUBLIC_URL"]
NEXTCLOUD_PUBLIC_TOKEN = os.environ["NEXTCLOUD_PUBLIC_TOKEN"]
NEXTCLOUD_PUBLIC_PASS  = os.environ.get("NEXTCLOUD_PUBLIC_PASSWORD", "")
VERIFY_SSL             = os.environ.get("NEXTCLOUD_VERIFY_SSL", "true").lower() == "true"

SMTP_SERVER = os.environ["SMTP_SERVER"]
SMTP_PORT = int(os.environ.get("SMTP_PORT") or 587)
SMTP_USER   = os.environ["SMTP_USER"]
SMTP_PASS   = os.environ["SMTP_PASS"]
MAIL_TO = ["21a847ec.aubexGmbH.onmicrosoft.com@emea.teams.ms"]

START_HOUR = 5
END_HOUR = 22

BASE_DIR    = Path(__file__).parent
LOGS_DIR    = BASE_DIR / "logs"
PROCESSED   = BASE_DIR / "processed_files.txt"
COMP_JSON   = BASE_DIR / "shared" / "config" / "companies_locations.json"

LOGS_DIR.mkdir(exist_ok=True)


# =============================
# Helper
# =============================

def now_local():
    return datetime.now()  # runner podľa timezone; dá sa pridať pytz ak chceš DE čas


def in_window():
    h = now_local().hour
    return START_HOUR <= h <= END_HOUR


def read_processed():
    if not PROCESSED.exists():
        return set()
    return set(PROCESSED.read_text(encoding="utf-8").splitlines())


def write_processed(s):
    PROCESSED.write_text("\n".join(sorted(s)), encoding="utf-8")


# =============================
# Nextcloud download
# =============================

def download_todays_logs(processed):
    today = now_local().strftime("%Y%m%d")
    auth = HTTPBasicAuth(NEXTCLOUD_PUBLIC_TOKEN, NEXTCLOUD_PUBLIC_PASS)

    print("PROPFIND → Nextcloud…")
    resp = requests.request(
        "PROPFIND",
        NEXTCLOUD_PUBLIC_URL,
        auth=auth,
        headers={"Depth": "1"},
        verify=VERIFY_SSL,
        timeout=30
    )

    if resp.status_code not in (200, 207):
        raise RuntimeError(f"PROPFIND failed: {resp.status_code}")

    hrefs = re.findall(r"<d:href>(.*?)</d:href>", resp.text, flags=re.I)
    downloaded = []

    for href in hrefs:
        name = Path(href).name
        if not name:
            continue

        decoded = urllib.parse.unquote(name)

        # chceme iba dnešné *.txt
        if not (decoded.startswith(today) and decoded.endswith(".txt")):
            continue
        
        if decoded in processed:
            print(f"Skipping already processed: {decoded}")
            continue

        out_path = LOGS_DIR / decoded
        if out_path.exists():
            continue

        file_url = f"{NEXTCLOUD_PUBLIC_URL.rstrip('/')}/{name}"

        print(f"Downloading {decoded}…")
        r = requests.get(file_url, auth=auth, verify=VERIFY_SSL, timeout=60)
        if r.ok:
            out_path.write_bytes(r.content)
            downloaded.append(out_path)
        else:
            print(f"Failed {decoded}: {r.status_code}")

    return downloaded


# =============================
# Email
# =============================

def send_email(subject, body):
    if not MAIL_TO:
        print("⚠️ MAIL_TO nie je nastavené")
        return

    msg = EmailMessage()
    msg["From"] = SMTP_USER
    msg["To"] = ", ".join(MAIL_TO)
    msg["Subject"] = subject
    msg.set_content(body)

    with smtplib.SMTP(SMTP_SERVER, SMTP_PORT, timeout=30) as s:
        s.starttls()
        s.login(SMTP_USER, SMTP_PASS)
        s.send_message(msg)


# =============================
# Main
# =============================

def main():
    if not in_window():
        print(f"⏸️ Mimo časového okna {START_HOUR}:00–{END_HOUR}:00")
        return

    print("🚀 Pipeline štartuje")

    # 1) Stiahnuť logy
    processed = read_processed()
    download_todays_logs(processed)

    # 2) Analyzovať všetky dnešné lokálne logy
    
    known = load_known_entities(COMP_JSON)

    results = []
    newly = set()

    print("🔍 Kontrola lokálnych logov:")

    for f in sorted(LOGS_DIR.glob("*.txt")):

        if f.name in processed:
            print(f"   ➖ Preskakujem (už spracovaný): {f.name}")
            continue

        print(f"   🔎 Analyzujem: {f.name}")

        # Najprv analyzuj log, či obsahuje chybu
        finding = analyze_log(f, known)

        # Potom spracuj názov súboru → firma, lokalita, čas
        meta = parse_filename(f.stem, known)

        newly.add(f.name)

        if finding:
            print(f"      ❗ Nájdená chyba → {meta['company']} | {meta['location']} | {meta['time']}")
            
            # pridaj nález do výsledkov
            results.append({
                "company": meta["company"],
                "location": meta["location"],
                "time": meta["time"],
                "label": finding.get("label", "unknown"),
                "link": finding.get("link", "")
            })
        else:
            print("      ✅ Bez chyby")
    # 🔥 Zapíš processed_files.txt
    write_processed(processed.union(newly))
    # 🔥 Ak nie sú žiadne chyby → neodosielať email
    if not results:
        print("✅ Žiadne login chyby")
        return


    # 3) zostavenie emailu
    lines = []
    for r in results:
        line = f"{r['company']} | {r['location']} | {r['time']} | {r['label']}"
        if r["link"]:
            line += f" | {r['link']}"
        lines.append(line)

    subject = f"BMW RPA Loginfehler — {now_local():%Y-%m-%d %H:%M}"
    body = "Alarm!!\n" + "\n".join(lines)

    send_email(subject, body)
    print("📨 Email sent")


if __name__ == "__main__":
    main()
