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

/** Nagłówki HTTP przenoszą tylko ASCII. Polskie znaki i myślniki trzeba zakodować
 *  wg RFC 2047, inaczej ntfy odrzuca żądanie albo tytuł przychodzi zniekształcony. */
function headerSafe(s) {
  if (/^[\x20-\x7E]*$/.test(s)) return s;
  const bytes = new TextEncoder().encode(s);
  const b64 = btoa(String.fromCharCode(...bytes));
  return `=?UTF-8?B?${b64}?=`;
}

/** Nagłówek autoryzacji ntfy. Bez niego limity liczone są PER IP — a Workers
 *  wychodzą ze współdzielonych IP Cloudflare, więc cudzy ruch wyczerpuje nasz limit
 *  (HTTP 429). Z tokenem konta limit jest nasz własny. */
function ntfyAuth(env) {
  return env.NTFY_TOKEN ? { Authorization: `Bearer ${env.NTFY_TOKEN}` } : {};
}

/**
 * Wysyłka przez Telegram. Główny kanał powiadomień: limity są per bot (ogromne),
 * a nie per IP — w przeciwieństwie do darmowego ntfy.sh, które przy wysyłce
 * ze współdzielonych IP Cloudflare losowo zwraca HTTP 429.
 *
 * `silent: true` → wiadomość bez dźwięku i wibracji.
 */
async function sendTelegram(env, { title, body, click, silent = false }) {
  if (!(env.TELEGRAM_BOT_TOKEN && env.TELEGRAM_CHAT_ID)) return null;

  const text =
    `<b>${title}</b>\n${body}` + (click ? `\n\n<a href="${click}">Otworz »</a>` : "");

  const r = await fetch(
    `https://api.telegram.org/bot${env.TELEGRAM_BOT_TOKEN}/sendMessage`,
    {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        chat_id: env.TELEGRAM_CHAT_ID,
        text,
        parse_mode: "HTML",
        disable_notification: silent,
      }),
    }
  );
  const detail = r.ok ? "" : ` — ${(await r.text()).slice(0, 200)}`;
  return `TG HTTP ${r.status}${detail}`;
}

/** Wysyłka przez ntfy (kanał zapasowy — patrz uwaga o limitach per IP). */
async function sendNtfy(env, { title, body, click, priority, tags }) {
  if (!env.NTFY_TOPIC) return null;
  const r = await fetch(`https://ntfy.sh/${env.NTFY_TOPIC}`, {
    method: "POST",
    body,
    headers: {
      ...ntfyAuth(env),
      Title: headerSafe(title),
      Priority: priority,
      Tags: tags,
      ...(click ? { Click: click } : {}),
    },
  });
  const detail = r.ok ? "" : ` — ${(await r.text()).slice(0, 200)}`;
  return `ntfy HTTP ${r.status}${detail}`;
}

/** Wyślij wszystkimi skonfigurowanymi kanałami; zwróć zbiorczy status. */
async function push(env, msg) {
  const results = await Promise.all([sendTelegram(env, msg), sendNtfy(env, msg)]);
  const summary = results.filter(Boolean).join(" | ") || "brak skonfigurowanego kanalu";
  console.log(`push: ${summary}`);
  return summary;
}

async function notify(env, slots) {
  const nearest = slots[0]?.start ?? "";
  const pretty = nearest ? nearest.replace("T", " ").slice(0, 16) : "";
  const click = slots[0]?.booking_url || env.PROFILE_URL;

  const body =
    `Dostepnych terminow: ${slots.length}` +
    (pretty ? ` | Najblizszy: ${pretty}` : "");

  return push(env, {
    title: `${env.DOCTOR_NAME || "Lekarz"} - WOLNY TERMIN!`,
    body,
    click,
    priority: "urgent",
    tags: "hospital,bell,rotating_light",
  });
}

/** Cron pulsu — musi być identyczny jak wpis w wrangler.toml. */
const HEARTBEAT_CRON = "0 6 * * *";

/**
 * Puls: raz dziennie potwierdza, że monitor żyje. Sens jest taki, że BRAK pulsu
 * jest sygnałem — gdyby padł Worker albo ntfy, cisza wygląda identycznie jak
 * "brak terminów", a alarm awaryjny leci tym samym kanałem, więc też by nie doszedł.
 *
 * Priority "default" = pojedyncza krótka wibracja, celowo inna niż "urgent"
 * (długa seria) używany przy prawdziwych terminach.
 */
async function heartbeat(env) {
  const lastRun = await env.STATE.get("last_run");
  const lastCount = await env.STATE.get("last_count");
  const ageMin = lastRun
    ? Math.round((Date.now() - Date.parse(lastRun)) / 60000)
    : null;

  // Puls raportuje też ŚWIEŻOŚĆ ostatniego sprawdzenia — inaczej mógłby zapewniać
  // "żyję", podczas gdy same sprawdzenia od godzin się wywracają.
  const stale = ageMin === null || ageMin > 15;

  const body = stale
    ? `Sprawdzenia nie dzialaja. Ostatnie: ${ageMin === null ? "nigdy" : ageMin + " min temu"}`
    : `Wolnych terminow w oknie: ${lastCount ?? "?"}\nOstatnie sprawdzenie: ${ageMin} min temu`;

  return push(env, {
    title: stale ? "Monitor - COS NIE GRA" : "Monitor dziala",
    body,
    priority: stale ? "high" : "default",
    tags: stale ? "warning" : "heavy_check_mark",
  });
}

/** Alert, gdy monitor sam się wykłada (np. ZnanyLekarz zmienił stronę). */
async function notifyFailure(env, message) {
  await push(env, {
    title: "ZnanyLekarz Monitor - AWARIA",
    body: `Monitor nie moze sprawdzic terminow: ${message}`,
    priority: "urgent",
    tags: "warning,skull",
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
    if (event.cron === HEARTBEAT_CRON) {
      ctx.waitUntil(heartbeat(env).catch((e) => console.error("heartbeat:", e.message)));
      return;
    }
    ctx.waitUntil(
      runCheck(env).catch(async (e) => {
        console.error("check failed:", e.message);
        await notifyFailure(env, e.message);
      })
    );
  },

  async fetch(request, env) {
    const params = new URL(request.url).searchParams;

    // ?test=1 → wyślij powiadomienie testowe i pokaż DOKŁADNĄ odpowiedź ntfy.
    // Osiągalne tylko przez `wrangler dev` (Worker nie ma publicznego URL-a).
    if (params.get("test") === "1") {
      const result = await notify(env, [
        { start: "2026-01-01T12:00:00+01:00", booking_url: env.PROFILE_URL },
      ]);
      return Response.json({
        test: true,
        ntfy: result,
        topicUstawiony: Boolean(env.NTFY_TOPIC),
        topicDlugosc: env.NTFY_TOPIC ? env.NTFY_TOPIC.length : 0,
      });
    }

    // ?chatid=1 → odczytaj chat_id z ostatnich wiadomości wysłanych do bota.
    // Dzięki temu token bota zostaje w sekretach i nigdzie go nie trzeba wklejać.
    if (params.get("chatid") === "1") {
      if (!env.TELEGRAM_BOT_TOKEN) {
        return Response.json({ blad: "brak sekretu TELEGRAM_BOT_TOKEN" }, { status: 400 });
      }
      const r = await fetch(
        `https://api.telegram.org/bot${env.TELEGRAM_BOT_TOKEN}/getUpdates`
      );
      const j = await r.json();
      const czaty = (j.result || [])
        .map((u) => u.message?.chat)
        .filter(Boolean)
        .map((c) => ({ chat_id: c.id, kto: c.username || c.first_name || c.type }));
      return Response.json({
        znalezione: czaty,
        podpowiedz: czaty.length
          ? "Ustaw chat_id jako sekret TELEGRAM_CHAT_ID"
          : "Napisz cokolwiek do swojego bota na Telegramie i sprobuj ponownie",
      });
    }

    // ?heartbeat=1 → wyślij puls od razu, bez czekania na poranny cron.
    if (params.get("heartbeat") === "1") {
      return Response.json({ heartbeat: await heartbeat(env) });
    }

    const notifyFlag = params.get("notify") === "1";
    try {
      const result = await runCheck(env, { allowNotify: notifyFlag });
      return Response.json(result);
    } catch (e) {
      return Response.json({ ok: false, error: e.message }, { status: 500 });
    }
  },
};
