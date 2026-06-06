#!/usr/bin/env python3
"""
ZnanyLekarz Monitor — wersja chmurowa (GitHub Actions), z AUTO-LOGOWANIEM.
Sprawdza wolne terminy u JEDNEGO lekarza i wysyła powiadomienie na telefon.

Zmiana vs poprzednia wersja: skrypt sam loguje się przy KAŻDYM uruchomieniu
(email + hasło z Secrets ZL_EMAIL / ZL_PASSWORD), więc nie ma już problemu
z wygasającym cookie/tokenem. URL do slotów jest budowany z twardych parametrów
(doctor/address/service) — NIE z Secret API_URL.
"""

import os
import sys
import requests
from datetime import datetime, date, timezone

# ── Twarde parametry lekarza (NIE z Secrets) ────────────────────
BASE          = "https://www.znanylekarz.pl"
DOCTOR_ID     = 274464
ADDRESS_ID    = 1042708
SERVICE_ID    = 2868908

# Endpoint slotów v3. Gdyby zwracał 404 — to JEDYNA linia do poprawienia
# (struktura ścieżki bywa różna; parametry IDs są pewne).
SLOTS_URL = (
    f"{BASE}/api/v3/doctors/{DOCTOR_ID}/addresses/{ADDRESS_ID}/slots"
)

# ── Konfiguracja (z GitHub Secrets) ─────────────────────────────
ZL_EMAIL           = os.environ.get("ZL_EMAIL", "").strip()
ZL_PASSWORD        = os.environ.get("ZL_PASSWORD", "").strip()
DOCTOR_NAME        = os.environ.get("DOCTOR_NAME", "Lekarz").strip()
DOCTOR_PAGE_URL    = os.environ.get("DOCTOR_PAGE_URL", "https://www.znanylekarz.pl/").strip()
NTFY_TOPIC         = os.environ.get("NTFY_TOPIC", "").strip()
TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "").strip()
TELEGRAM_CHAT_ID   = os.environ.get("TELEGRAM_CHAT_ID", "").strip()

# Szukaj terminów tylko do końca czerwca
SEARCH_UNTIL = date(2026, 6, 30)

# Plik stanu — pamięta poprzedni wynik między uruchomieniami (Actions Cache)
STATE_FILE = "last_count.txt"

BROWSER_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                  "(KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36",
    "Accept": "application/json, text/plain, */*",
    "Accept-Language": "pl-PL,pl;q=0.9",
    "Referer": "https://www.znanylekarz.pl/",
    "Origin": "https://www.znanylekarz.pl",
}


# ── Logowanie ───────────────────────────────────────────────────

def login(session: requests.Session) -> str:
    """
    Loguje się na ZnanyLekarz i zwraca pełną wartość nagłówka Authorization
    (np. "Bearer eyJ...") lub "" jeśli logowanie tokenem się nie udało.
    Ciasteczka sesji lądują w `session` i są używane niezależnie od tokenu.

    Próbuje kilku znanych wariantów API v3 (Docplanner) — pierwszy, który
    zwróci token, wygrywa. Logi wskażą, który zadziałał.
    """
    # 1. Rozgrzej sesję (cookies, ewentualny CSRF)
    try:
        session.get(BASE + "/", timeout=20)
    except Exception as e:
        print(f"  Logowanie: nie udalo sie pobrac strony glownej ({e})")

    attempts = [
        ("/api/v3/authentication_token", {"email": ZL_EMAIL, "password": ZL_PASSWORD}),
        ("/api/v3/authentication_token", {"username": ZL_EMAIL, "password": ZL_PASSWORD}),
        ("/api/v3/login",                {"email": ZL_EMAIL, "password": ZL_PASSWORD}),
    ]

    for path, payload in attempts:
        try:
            r = session.post(BASE + path, json=payload, headers=BROWSER_HEADERS, timeout=20)
        except Exception as e:
            print(f"  Logowanie {path}: blad polaczenia — {e}")
            continue

        if r.status_code in (200, 201):
            try:
                j = r.json()
            except ValueError:
                print(f"  Logowanie {path}: HTTP {r.status_code}, ale odpowiedz nie jest JSON")
                continue
            token = j.get("token") or j.get("access_token") or j.get("jwt")
            if token:
                print(f"  Logowanie OK ({path}) — token uzyskany.")
                return f"Bearer {token}"
            print(f"  Logowanie {path}: HTTP {r.status_code}, brak pola token w odpowiedzi "
                  f"(klucze: {list(j.keys())[:8]})")
        else:
            print(f"  Logowanie {path}: HTTP {r.status_code}")

    # 2. Fallback: klasyczny formularz /login (uwierzytelnienie przez cookies sesji)
    try:
        r = session.post(BASE + "/login",
                         data={"email": ZL_EMAIL, "password": ZL_PASSWORD},
                         headers=BROWSER_HEADERS, timeout=20, allow_redirects=True)
        if r.ok:
            print("  Logowanie: formularz /login zwrocil OK — uzywam cookies sesji.")
        else:
            print(f"  Logowanie: formularz /login HTTP {r.status_code}")
    except Exception as e:
        print(f"  Logowanie /login: blad — {e}")

    return ""


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


# ── Budowa zapytania o sloty ────────────────────────────────────

def build_params() -> dict:
    """Parametry zapytania: okno dat od dziś do SEARCH_UNTIL w ISO+offset.
    ZnanyLekarz v3 wymaga pełnego ISO z offsetem (sama data → HTTP 404)."""
    offset = "+02:00"  # Polska czas letni (czerwiec)
    return {
        "service_id": SERVICE_ID,
        "start": f"{date.today().isoformat()}T00:00:00{offset}",
        "end":   f"{SEARCH_UNTIL.isoformat()}T23:59:59{offset}",
    }


# ── Main ─────────────────────────────────────────────────────────

def main():
    print(f"\n[{datetime.now(timezone.utc):%Y-%m-%d %H:%M:%S} UTC] ZnanyLekarz Monitor (auto-login)")

    # Walidacja
    if not (ZL_EMAIL and ZL_PASSWORD):
        print("BLAD: Brak ZL_EMAIL / ZL_PASSWORD w GitHub Secrets — ustaw je i uruchom ponownie.")
        sys.exit(1)
    if not (NTFY_TOPIC or (TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID)):
        print("Uwaga: Nie ustawiono zadnego kanalu powiadomien (NTFY_TOPIC lub TELEGRAM_*).")

    # Logowanie
    session = requests.Session()
    authorization = login(session)

    # Stan
    last_count = read_last_count()
    print(f"  Poprzedni wynik: {last_count if last_count >= 0 else 'pierwsze uruchomienie'}")

    # Zapytanie o sloty
    headers = dict(BROWSER_HEADERS)
    if authorization:
        headers["Authorization"] = authorization

    params = build_params()
    print(f"  GET {SLOTS_URL}")
    print(f"      start={params['start']}  end={params['end']}  service_id={params['service_id']}")

    try:
        resp = session.get(SLOTS_URL, headers=headers, params=params, timeout=25)
        if resp.status_code == 401:
            print("BLAD: HTTP 401 — logowanie nie dalo dostepu. Sprawdz ZL_EMAIL/ZL_PASSWORD "
                  "albo endpoint logowania (patrz logi 'Logowanie' wyzej).")
            sys.exit(1)
        if resp.status_code == 403:
            print("BLAD: HTTP 403 — odmowa dostepu.")
            sys.exit(1)
        if resp.status_code == 404:
            print("BLAD: HTTP 404 — zly adres endpointu slotow (SLOTS_URL). Popraw sciezke w check_slots.py.")
            sys.exit(1)
        resp.raise_for_status()
        all_slots = extract_slots(resp.json())
        slots = [s for s in all_slots if is_free(s)]
        count = len(slots)
        print(f"  W oknie dat: {len(all_slots)} slotow grafiku, z czego wolnych: {count}")
    except requests.exceptions.JSONDecodeError:
        print("BLAD: Odpowiedz nie jest JSONem — prawdopodobnie zwrocono strone HTML (zle logowanie/adres).")
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
