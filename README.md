# ZnanyLekarz Monitor

Automatycznie pinguje wolne terminy u wybranego lekarza na
[znanylekarz.pl](https://www.znanylekarz.pl) i wysyła powiadomienie na telefon,
gdy tylko pojawi się nowy termin. Działa 24/7 w chmurze GitHub Actions —
komputer nie musi być włączony.

Domyślnie monitoruje: **Mariana Karwan — dermatolog (Kościerzyna)**, terminy
w oknie **18–31.08.2026**. Sprawdzanie co 5 minut.

## Jak to działa

1. Co 5 minut GitHub Actions uruchamia [`check_slots.py`](check_slots.py).
2. Skrypt pobiera świeży, **anonimowy** token ze strony profilu lekarza
   (`ZLApp.APICredentials.ACCESS_TOKEN`) — nie wymaga logowania ani cookie, więc
   nic nie wygasa.
3. Odpytuje API ZnanyLekarz v3 o wolne sloty w zadanym oknie dat.
4. Jeśli pojawią się **nowe** terminy (więcej niż poprzednio) — wysyła
   powiadomienie ntfy na telefon. Bez zmian = cisza (zero spamu).
5. Liczbę terminów z poprzedniego razu pamięta w `last_count.txt` (Actions Cache).

Gdy workflow z jakiegoś powodu padnie, dostajesz osobny alert na telefon —
nie zostaniesz bez wiedzy, że monitor przestał działać.

## Konfiguracja

Sekrety: **Settings → Secrets and variables → Actions → New repository secret**

| Secret | Wymagany | Opis |
|---|---|---|
| `NTFY_TOPIC` | tak | Twój losowy temat ntfy (patrz niżej) |
| `DOCTOR_NAME` | nie | Ładniejszy tytuł w powiadomieniu, np. „Mariana Karwan — dermatolog" |
| `DOCTOR_PAGE_URL` | nie | Link do profilu lekarza — klikalny w powiadomieniu |
| `TELEGRAM_BOT_TOKEN` + `TELEGRAM_CHAT_ID` | nie | Alternatywny / dodatkowy kanał powiadomień |

### Powiadomienia ntfy (za darmo, bez rejestracji)

1. Zainstaluj na telefonie aplikację **ntfy** (Google Play / App Store).
2. Otwórz ją → „+" (Subscribe to topic).
3. Wpisz **losowy, trudny do zgadnięcia** temat (kto zna temat, może Ci wysyłać
   powiadomienia — niech będzie unikalny).
4. Subscribe. Ten sam temat wpisz jako sekret `NTFY_TOPIC`.

## Uruchomienie i test

1. Zakładka **Actions** → *ZnanyLekarz Monitor* → **Run workflow** (ręczny test).
2. Po chwili kliknij uruchomienie i zobacz logi w kroku **„Sprawdź terminy"** —
   pokaże, ile terminów znalazł.
3. Dalej workflow chodzi sam co 5 minut.

## Zmiana lekarza / zakresu dat

Wszystko jest na górze [`check_slots.py`](check_slots.py):

```python
DOCTOR_ID   = 274464
ADDRESS_ID  = 1042708
SERVICE_ID  = 2868908
PROFILE_URL = "https://www.znanylekarz.pl/.../..."   # strona profilu lekarza
SEARCH_FROM  = date(2026, 8, 18)                      # od kiedy szukać
SEARCH_UNTIL = date(2026, 8, 31)                      # do kiedy szukać
```

IDs i `PROFILE_URL` znajdziesz w DevTools (F12 → Network → Fetch/XHR) na stronie
lekarza, w zapytaniu do `/api/v3/doctors/.../slots`.

## Wyłączenie

Gdy umówisz wizytę: **Actions → ZnanyLekarz Monitor → „…" → Disable workflow**.

## Koszty

Repo publiczne → minuty GitHub Actions są **darmowe i nielimitowane**.
(Na repo prywatnym darmowy limit to 2000 min/mc — co 5 min by się w nim nie
zmieściło, dlatego repo jest publiczne. Sekrety pozostają prywatne mimo to.)
