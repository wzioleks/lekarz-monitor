/**
 * ZnanyLekarz Slot Monitor — Cloudflare Worker
 *
 * Zastępuje konstrukcję na GitHub Actions (pętla 5h40m + self-restart przez PAT
 * + concurrency + cache). Tutaj to po prostu funkcja odpalana cronem co 5 minut:
 * bez limitu 6h, bez łańcucha, bez tokenu, bez throttlingu harmonogramu.
 *
 * Uwierzytelnianie: ZnanyLekarz nie ma logowania pacjenta w API. Strona profilu
 * lekarza osadza za to świeży ANONIMOWY token OAuth w HTML
 * (ZLApp.APICredentials.ACCESS_TOKEN), generowany przy każdym wczytaniu.
 *
 * Endpointy:
 *   cron  → scheduled(): sprawdza i powiadamia przy nowych terminach
 *   GET / → fetch(): to samo, ale zwraca JSON z diagnostyką (bez powiadomień,
 *           chyba że dodasz ?notify=1). Do ręcznego testowania.
 */

const BROWSER_HEADERS = {
  "User-Agent":
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 " +
    "(KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36",
  Accept: "application/json, text/plain, */*",
  "Accept-Language": "pl-PL,pl;q=0.9",
  Referer: "https://www.znanylekarz.pl/",
};

const TOKEN_RE = /ACCESS_TOKEN'\s*:\s*'([^']+)'/;

/** Przesunięcie strefy Europe/Warsaw dla danej daty, np. "+02:00".
 *  Liczone dynamicznie, żeby nie wysypać się na zmianie czasu w październiku. */
function warsawOffset(date) {
  const s = new Intl.DateTimeFormat("en-US", {
    timeZone: "Europe/Warsaw",
    timeZoneName: "longOffset",
  }).format(date);
  const m = s.match(/GMT([+-]\d{2}:\d{2})/);
  return m ? m[1] : "+02:00";
}

/** Dzisiejsza data (YYYY-MM-DD) w strefie Europe/Warsaw, nie UTC. */
function warsawToday(now) {
  return new Intl.DateTimeFormat("en-CA", {
    timeZone: "Europe/Warsaw",
    year: "numeric",
    month: "2-digit",
    day: "2-digit",
  }).format(now);
}

/** Pobiera świeży anonimowy token z profilu lekarza. */
async function getAccessToken(env) {
  const r = await fetch(env.PROFILE_URL, { headers: BROWSER_HEADERS });
  if (!r.ok) throw new Error(`profil HTTP ${r.status}`);
  const html = await r.text();
  const m = html.match(TOKEN_RE);
  if (!m) throw new Error("nie znaleziono ACCESS_TOKEN w HTML profilu");
  return m[1];
}

/** Pobiera wolne sloty w skonfigurowanym oknie dat. */
async function fetchSlots(env, now = new Date()) {
  const token = await getAccessToken(env);

  // start = późniejsza z dat: SEARCH_FROM albo dziś (nie pytamy o przeszłość)
  const today = warsawToday(now);
  const start = env.SEARCH_FROM > today ? env.SEARCH_FROM : today;
  const off = warsawOffset(now);

  const url =
    `https://www.znanylekarz.pl/api/v3/doctors/${env.DOCTOR_ID}` +
    `/addresses/${env.ADDRESS_ID}/slots` +
    `?service_id=${env.SERVICE_ID}` +
    `&start=${encodeURIComponent(`${start}T00:00:00${off}`)}` +
    `&end=${encodeURIComponent(`${env.SEARCH_UNTIL}T23:59:59${off}`)}`;

  const r = await fetch(url, {
    headers: { ...BROWSER_HEADERS, Authorization: `Bearer ${token}` },
  });
  if (!r.ok) throw new Error(`slots HTTP ${r.status}`);

  const data = await r.json();
  const items = Array.isArray(data?._items) ? data._items : [];
  // _items zawiera JUŻ wolne sloty (mają booking_url, brak pola `booked`).
  // Gdyby API kiedyś zaczęło zwracać cały grafik — odfiltruj zajęte.
  const free = items.filter((s) => s?.booked !== true);

  return { free, window: { start, end: env.SEARCH_UNTIL }, url };
}

async function notify(env, slots) {
  if (!env.NTFY_TOPIC) return "brak NTFY_TOPIC";
  const nearest = slots[0]?.start ?? "";
  const pretty = nearest ? nearest.replace("T", " ").slice(0, 16) : "";
  const click = slots[0]?.booking_url || env.PROFILE_URL;

  const body =
    `Dostepnych terminow: ${slots.length}` +
    (pretty ? ` | Najblizszy: ${pretty}` : "");

  const r = await fetch(`https://ntfy.sh/${env.NTFY_TOPIC}`, {
    method: "POST",
    body,
    headers: {
      Title: `${env.DOCTOR_NAME || "Lekarz"} — WOLNY TERMIN!`,
      Priority: "urgent",
      Tags: "hospital,bell,rotating_light",
      Click: click,
    },
  });
  return r.ok ? "OK" : `blad HTTP ${r.status}`;
}

/** Alert, gdy monitor sam się wykłada (np. ZnanyLekarz zmienił stronę). */
async function notifyFailure(env, message) {
  if (!env.NTFY_TOPIC) return;
  await fetch(`https://ntfy.sh/${env.NTFY_TOPIC}`, {
    method: "POST",
    body: `Monitor nie moze sprawdzic terminow: ${message}`,
    headers: {
      Title: "ZnanyLekarz Monitor — AWARIA",
      Priority: "urgent",
      Tags: "warning,skull",
    },
  }).catch(() => {});
}

/** Jedno sprawdzenie: pobierz, porównaj ze stanem, powiadom jeśli przybyło. */
async function runCheck(env, { allowNotify = true } = {}) {
  const { free, window } = await fetchSlots(env);
  const count = free.length;

  const prevRaw = await env.STATE.get("last_count");
  const prev = prevRaw === null ? -1 : parseInt(prevRaw, 10);

  // Powiadamiamy tylko gdy terminów PRZYBYŁO — zero spamu.
  const shouldNotify = count > 0 && (prev === -1 || count > prev);

  let notified = null;
  if (shouldNotify && allowNotify) notified = await notify(env, free);

  if (count !== prev) await env.STATE.put("last_count", String(count));

  // Znacznik życia — watchdog/diagnostyka mogą sprawdzić, kiedy ostatnio żyliśmy.
  await env.STATE.put("last_run", new Date().toISOString());

  return {
    ok: true,
    window,
    count,
    prev,
    notified,
    slots: free.slice(0, 10).map((s) => s.start),
  };
}

export default {
  async scheduled(event, env, ctx) {
    ctx.waitUntil(
      runCheck(env).catch(async (e) => {
        console.error("check failed:", e.message);
        await notifyFailure(env, e.message);
      })
    );
  },

  async fetch(request, env) {
    const notifyFlag = new URL(request.url).searchParams.get("notify") === "1";
    try {
      const result = await runCheck(env, { allowNotify: notifyFlag });
      return Response.json(result);
    } catch (e) {
      return Response.json({ ok: false, error: e.message }, { status: 500 });
    }
  },
};
