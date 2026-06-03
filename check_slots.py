#!/usr/bin/env python3
"""
ZnanyLekarz Monitor — wersja chmurowa (GitHub Actions)
Sprawdza wolne terminy u JEDNEGO lekarza i wysyła powiadomienie na telefon.
Działa 24/7 w chmurze GitHub — komputer nie musi być włączony.
Konfiguracja przez GitHub Secrets (patrz README.md).
"""

import os
import sys
import requests
from datetime import datetime, date, timezone
from urllib.parse import urlparse, parse_qs, urlencode, urlunparse

# ── Konfiguracja (z GitHub Secrets) ─────────────────────────────
API_URL            = os.environ.get("API_URL", "").strip()
DOCTOR_NAME        = os.environ.get("DOCTOR_NAME", "Lekarz").strip()
DOCTOR_PAGE_URL    = os.environ.get("DOCTOR_PAGE_URL", "https://www.znanylekarz.pl/").strip()
COOKIE             = os.environ.get("COOKIE", "").strip()
# Pełna wartość nagłówka Authorization, np. "Bearer eyJ...". ZnanyLekarz v3 go wymaga.
AUTHORIZATION      = os.environ.get("AUTHORIZATION", "").strip()
NTFY_TOPIC         = os.environ.get("NTFY_TOPIC", "").strip()
TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "").strip()
TELEGRAM_CHAT_ID   = os.environ.get("TELEGRAM_CHAT_ID", "").strip()

# Szukaj terminów tylko do końca czerwca
SEARCH_UNTIL = date(2026, 6, 30)

# Plik stanu — pamięta poprzedni wynik między uruchomieniami (Actions Cache)
STATE_FILE = "last_count.txt"


# ── Auto-odświeżanie dat w URL ──────────────────────────────────

def refresh_date_params(url: str) -> str:
    """
    Jeśli URL zawiera parametry dat (start/end/since/until itp.),
    ustawia je na 'od dziś do dziś+DAYS_AHEAD'. Dzięki temu skrypt
    nie utknie na minionym tygodniu po kilku dniach działania.
    """
    parsed = urlparse(url)
    params = parse_qs(parsed.query, keep_blank_values=True)
    today = date.today()
    future = SEARCH_UNTIL

    START_KEYS = {"start", "from", "since", "date", "start_date",
                  "startdate", "date_from", "datefrom", "day"}
    END_KEYS   = {"end", "to", "until", "end_date", "enddate",
                  "date_to", "dateto"}

    def fmt(new_date, old_value):
        # Zachowaj sufiks czasu/strefy z oryginalnej wartości (np. T00:00:00+02:00).
        # ZnanyLekarz v3 ODRZUCA samą datę (HTTP 404), wymaga pełnego ISO z offsetem.
        s = str(old_value)
        return new_date.isoformat() + s[s.index("T"):] if "T" in s else new_date.isoformat()

    changed = False
    for key in list(params.keys()):
        kl = key.lower()
        old = params[key][0] if params[key] else ""
        if kl in START_KEYS:
            params[key] = [fmt(today, old)]
            changed = True
        elif kl in END_KEYS:
            params[key] = [fmt(future, old)]
            changed = True

    if changed:
        return urlunparse(parsed._replace(query=urlencode(params, doseq=True)))
    return url


# ── Stan między uruchomieniami ──────────────────────────────────

def read_last_count() -> int:
    """Zwraca poprzedni wynik (-1 = pierwsze uruchomienie / brak pliku)."""
    try:
        with open(STATE_FILE) as f:
            return int(f.read().strip())
    except Exception:
        return -1

def write_count(count: int):
    with open(STATE_FILE, "w") as f:
        f.write(str(count))


# ── Parsowanie odpowiedzi API ───────────────────────────────────

def extract_slots(data) -> list:
    """Wyciąga listę slotów — obsługuje różne struktury JSON DocPlanner/ZnanyLekarz."""
    if data is None:
        return []
    if isinstance(data, list):
        return data
    if isinstance(data, dict):
        for key in ["_items", "data", "slots", "available_slots", "availabilities",
                    "items", "results", "appointments", "timeSlots", "time_slots"]:
            if key in data:
                val = data[key]
                if isinstance(val, list):
                    return val
                if isinstance(val, dict):
                    for sub in ["slots", "data", "items"]:
                        if sub in val and isinstance(val[sub], list):
                            return val[sub]
        # Mapa dni: {"2026-06-10": [...], "2026-06-11": [...]}
        date_map = {k: v for k, v in data.items()
                    if isinstance(v, list) and str(k)[:4].isdigit()}
        if date_map:
            out = []
            for day, day_slots in date_map.items():
                for s in day_slots:
                    out.append({**s, "_date": day} if isinstance(s, dict)
                               else {"time": s, "_date": day})
            return out
    return []


def is_free(slot) -> bool:
    """Czy slot jest WOLNY? ZnanyLekarz w _items zwraca też zajęte godziny grafiku
    (booked=true) — interesują nas tylko booked=false. Inne API zwracają od razu
    same wolne sloty (brak pola 'booked') — wtedy traktujemy slot jako wolny."""
    if isinstance(slot, dict) and "booked" in slot:
        return slot.get("booked") is False
    return True


def format_slot(slot) -> str:
    """Czytelna data/godzina jednego slota."""
    if isinstance(slot, dict):
        for key in ["start", "startTime", "start_time", "datetime",
                    "date", "time", "begins_at"]:
            if key in slot and slot[key]:
                val = str(slot[key])
                if "T" in val:
                    val = val.replace("T", " ")[:16]
                return val
        if "_date" in slot:
            t = slot.get("time", slot.get("hour", ""))
            return f"{slot['_date']} {t}".strip()
    return str(slot)


# ── Powiadomienia na telefon ────────────────────────────────────

def notify_ntfy(title: str, message: str, click: str = ""):
    if not NTFY_TOPIC:
        return
    try:
        r = requests.post(
            f"https://ntfy.sh/{NTFY_TOPIC}",
            data=message.encode("utf-8"),
            headers={
                "Title": title.encode("utf-8"),
                "Priority": "urgent",
                "Tags": "hospital,bell,rotating_light",
                "Click": click or DOCTOR_PAGE_URL,
            },
            timeout=10,
        )
        print(f"  ntfy.sh: {'OK' if r.status_code == 200 else f'blad HTTP {r.status_code}'}")
    except Exception as e:
        print(f"  ntfy.sh: blad — {e}")


def notify_telegram(message: str):
    if not (TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID):
        return
    try:
        r = requests.post(
            f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage",
            json={"chat_id": TELEGRAM_CHAT_ID, "text": message,
                  "parse_mode": "HTML", "disable_web_page_preview": False},
            timeout=10,
        )
        print(f"  Telegram: {'OK' if r.status_code == 200 else f'blad HTTP {r.status_code}'}")
    except Exception as e:
        print(f"  Telegram: blad — {e}")


def send_alert(slots: list, prev_count: int):
    count = len(slots)
    nearest = format_slot(slots[0]) if slots else ""
    # Klik w powiadomienie prowadzi wprost do umawiania najbliższego wolnego slota.
    book_url = (slots[0].get("booking_url") if slots and isinstance(slots[0], dict) else "") or DOCTOR_PAGE_URL

    title = f"{DOCTOR_NAME} — WOLNY TERMIN!"
    plain = f"Dostepnych terminow: {count}"
    if nearest:
        plain += f" | Najblizszy: {nearest}"

    tg = (
        f"<b>{DOCTOR_NAME} — wolny termin!</b>\n"
        f"Dostepnych: <b>{count}</b>"
        + (f"\nNajblizszy: <code>{nearest}</code>" if nearest else "")
        + f"\n\n<a href='{book_url}'>Umow wizyte »</a>"
    )

    notify_ntfy(title, plain, click=book_url)
    notify_telegram(tg)


# ── Main ─────────────────────────────────────────────────────────

def main():
    print(f"\n[{datetime.now(timezone.utc):%Y-%m-%d %H:%M:%S} UTC] ZnanyLekarz Monitor")

    # Walidacja
    if not API_URL:
        print("BLAD: Brak API_URL w GitHub Secrets — ustaw go i uruchom ponownie.")
        sys.exit(1)
    if not (NTFY_TOPIC or (TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID)):
        print("Uwaga: Nie ustawiono zadnego kanalu powiadomien (NTFY_TOPIC lub TELEGRAM_*).")

    # Odśwież daty w URL
    url = refresh_date_params(API_URL)

    # Stan
    last_count = read_last_count()
    print(f"  Poprzedni wynik: {last_count if last_count >= 0 else 'pierwsze uruchomienie'}")

    # Zapytanie
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                      "(KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36",
        "Accept": "application/json, text/plain, */*",
        "Accept-Language": "pl-PL,pl;q=0.9",
        "Referer": "https://www.znanylekarz.pl/",
    }
    if COOKIE:
        headers["Cookie"] = COOKIE
    if AUTHORIZATION:
        headers["Authorization"] = AUTHORIZATION

    try:
        resp = requests.get(url, headers=headers, timeout=25)
        if resp.status_code == 401:
            print("BLAD: HTTP 401 — endpoint wymaga logowania. Dodaj Secret COOKIE (patrz README).")
            sys.exit(1)
        if resp.status_code == 403:
            print("BLAD: HTTP 403 — odmowa dostepu. Sprawdz COOKIE / naglowki.")
            sys.exit(1)
        resp.raise_for_status()
        all_slots = extract_slots(resp.json())
        slots = [s for s in all_slots if is_free(s)]
        count = len(slots)
        print(f"  W oknie dat: {len(all_slots)} slotow grafiku, z czego wolnych: {count}")
    except requests.exceptions.JSONDecodeError:
        print("BLAD: Odpowiedz nie jest JSONem — sprawdz czy API_URL to wlasciwy endpoint (nie zwykla strona HTML).")
        sys.exit(1)
    except Exception as e:
        print(f"BLAD: Blad zapytania: {e}")
        sys.exit(1)

    print(f"  Aktualnie wolnych terminow: {count}")

    # Czy powiadamiać? Tylko gdy pojawiły się NOWE terminy.
    should_notify = (
        (last_count == -1 and count > 0)
        or (last_count == 0 and count > 0)
        or (last_count > 0 and count > last_count)
    )

    if should_notify:
        print("  Nowe terminy — wysylam powiadomienia na telefon:")
        send_alert(slots, last_count)
        for i, s in enumerate(slots[:5]):
            print(f"    [{i+1}] {format_slot(s)}")
        if count > 5:
            print(f"    ... i {count - 5} wiecej")
    elif count > 0:
        print("  Terminy sa, ale bez zmian — nie powiadamiam ponownie (zero spamu).")
    else:
        print("  Brak wolnych terminow.")

    write_count(count)
    print("Gotowe.\n")


if __name__ == "__main__":
    main()
