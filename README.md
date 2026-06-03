# ZnanyLekarz Monitor

Pinguje wolne terminy u JEDNEGO wybranego lekarza. Działa 24/7 w chmurze GitHub
(komputer NIE musi być włączony). Powiadomienia lecą na telefon.

═══════════════════════════════════════════════════════════════
  INSTRUKCJA KROK PO KROKU
═══════════════════════════════════════════════════════════════

──────────────────────────────────────────────
 CZĘŚĆ 1 — Powiadomienia na telefon (ntfy.sh)
──────────────────────────────────────────────
To najprostsza opcja — bez rejestracji, za darmo.

1. Zainstaluj na telefonie aplikację "ntfy" (Google Play / App Store)
2. Otwórz ją, kliknij "+" (Subscribe to topic)
3. Wpisz UNIKALNY, trudny do zgadnięcia temat, np.:
      TWOJ-TEMAT-NTFY
   (ktokolwiek zna ten temat może wysyłać Ci powiadomienia,
    więc niech będzie losowy)
4. Kliknij Subscribe. Zapisz sobie ten temat — wpiszesz go później.

──────────────────────────────────────────────
 CZĘŚĆ 2 — Znajdź adres API lekarza
──────────────────────────────────────────────
1. Na komputerze otwórz w Chrome stronę swojego lekarza na znanylekarz.pl
   (taką z widocznym kalendarzem terminów)
2. Naciśnij F12 (otworzy się panel deweloperski)
3. Przejdź na zakładkę "Network" (Sieć)
4. W polu filtra kliknij "Fetch/XHR"
5. Na stronie kliknij w kalendarz / zmień tydzień lub miesiąc
   — w panelu pojawią się nowe wiersze
6. Znajdź wiersz, który w nazwie ma "slot", "available" lub "calendar"
7. Kliknij na niego prawym → Copy → "Copy link address"
   (albo zakładka Headers → skopiuj "Request URL")
8. Zapisz ten adres — to Twój API_URL

   Dodatkowo (jeśli się okaże potrzebne):
   W tej samej zakładce Headers, w sekcji "Request Headers",
   znajdź linię "cookie:" i skopiuj całą jej wartość — to Twój COOKIE.
   (na początek możesz pominąć i dodać tylko jeśli skrypt zwróci błąd 401/403)

──────────────────────────────────────────────
 CZĘŚĆ 3 — Wrzuć projekt na GitHub
──────────────────────────────────────────────
1. Załóż darmowe konto na github.com (jeśli nie masz)
2. Kliknij "+" w prawym górnym rogu → "New repository"
3. Nazwa np. "lekarz-monitor", widoczność: Private, kliknij "Create repository"
4. Na stronie nowego repo kliknij "uploading an existing file"
5. Przeciągnij WSZYSTKIE pliki z tego folderu (z zachowaniem struktury):
      check_slots.py
      README.md
      .github/workflows/monitor.yml
   Najprościej: rozpakuj zip i przeciągnij cały folder.
   WAŻNE: folder ".github" musi się znaleźć w repo — bez niego nic nie ruszy.
6. Kliknij "Commit changes"

──────────────────────────────────────────────
 CZĘŚĆ 4 — Wpisz dane (GitHub Secrets)
──────────────────────────────────────────────
W repozytorium:
  Settings → (lewa kolumna) Secrets and variables → Actions
  → przycisk "New repository secret"

Dodaj kolejno (Name = nazwa, Secret = wartość):

  Name: API_URL
  Secret: <adres skopiowany w Części 2>            [WYMAGANE]

  Name: NTFY_TOPIC
  Secret: TWOJ-TEMAT-NTFY                        [WYMAGANE — Twój temat z Części 1]

  Name: DOCTOR_NAME
  Secret: np. Dr Kowalska — Dermatolog              [opcjonalne, ładniejsze powiadomienie]

  Name: DOCTOR_PAGE_URL
  Secret: <link do strony lekarza>                  [opcjonalne, klikalny link w powiadomieniu]

  Name: COOKIE
  Secret: <wartość cookie z Części 2>               [tylko jeśli błąd 401/403]

──────────────────────────────────────────────
 CZĘŚĆ 5 — Uruchom i przetestuj
──────────────────────────────────────────────
1. W repo wejdź w zakładkę "Actions"
2. Jeśli zobaczysz zielony przycisk zgody na workflows → kliknij go
3. Po lewej kliknij "ZnanyLekarz Monitor"
4. Po prawej "Run workflow" → "Run workflow" (ręczny test)
5. Po ~30 sekundach kliknij na uruchomienie i zobacz logi w kroku
   "Sprawdź terminy". Zobaczysz ile terminów znalazł.
6. Jeśli są wolne terminy — na telefon przyjdzie powiadomienie ntfy.

Od teraz workflow uruchamia się SAM co 10 minut, całą dobę.

──────────────────────────────────────────────
 WYŁĄCZENIE (gdy umówisz wizytę)
──────────────────────────────────────────────
Actions → ZnanyLekarz Monitor → "..." (prawy górny róg) → Disable workflow

═══════════════════════════════════════════════════════════════
  ROZWIĄZYWANIE PROBLEMÓW
═══════════════════════════════════════════════════════════════
• "Odpowiedz nie jest JSONem"  → API_URL wskazuje na zwykłą stronę, nie endpoint.
                                  Wróć do Części 2 i znajdź wiersz typu Fetch/XHR.
• HTTP 401 / 403               → dodaj Secret COOKIE (Część 2, akapit dodatkowy).
• Zawsze "Brak terminow"       → sprawdź czy struktura JSON pasuje. Wklej Claude
                                  przykład odpowiedzi z zakładki "Response" w DevTools.
• Nie przychodzi powiadomienie → sprawdź czy temat w aplikacji ntfy == NTFY_TOPIC.

═══════════════════════════════════════════════════════════════
  KOSZTY / LIMITY
═══════════════════════════════════════════════════════════════
GitHub Actions: darmowe.
  • Repo publiczne → minuty bez limitu.
  • Repo prywatne → 2000 min/miesiąc NA CAŁE KONTO (wszystkie repo razem).
    Ten monitor przy interwale 10 min zużywa ~1000 min/miesiąc — mieści się.
Uwaga: harmonogram GitHuba bywa opóźniony o kilka minut przy dużym obciążeniu
       — to normalne dla darmowego planu.
