# ZnanyLekarz Slot Monitor

Watches a doctor's calendar on [znanylekarz.pl](https://www.znanylekarz.pl) and pushes a
notification to your phone the moment a new appointment slot opens up in a date range you
care about. Runs as a Cloudflare Worker on a 5-minute cron — no server, no always-on machine.

Cancellations on busy specialists are usually gone within minutes. Polling every 5 minutes
turns "next opening is in 7 weeks" into "somebody just cancelled for next Tuesday".

## How it works

1. A Cloudflare cron trigger invokes the Worker every 5 minutes.
2. Each run scrapes a short-lived **anonymous OAuth token** from the doctor's public profile
   page (`ZLApp.APICredentials.ACCESS_TOKEN`) and uses it to query the public availability
   API. No account, no credentials, no cookies to keep alive.
3. Free slots inside the configured window are counted and compared against the previous
   run (stored in Workers KV).
4. If the count went **up**, a push notification goes out. Otherwise it stays silent — no
   repeat alerts for slots you already know about.
5. If a run throws (e.g. the page structure changed and the token can't be extracted), an
   urgent alert is sent instead, so breakage isn't silent.

Everything lives in one file: [`worker/src/index.js`](worker/src/index.js).

### Notification channels

Messages go out over **Telegram** (primary) and **ntfy** (secondary) in parallel — whichever
is configured. Both are optional individually, but you want at least one.

> **Use Telegram as the primary channel.** ntfy.sh rate-limits publishing **per source IP**,
> and Workers egress from shared Cloudflare addresses, so you inherit strangers' traffic
> against the same quota. In practice that means intermittent `HTTP 429` with no message
> delivered — it works, then randomly doesn't, with no signal that anything was dropped. An
> ntfy account token does *not* fix this on the free tier; the limit stays tied to the IP.
> Telegram's limits are per bot, so the problem disappears.

### Daily heartbeat

A second cron sends one **silent** message a day ("still alive, N slots in window"). It costs
nothing and closes the worst failure mode: if the Worker or the notification channel dies,
silence looks exactly like "no slots available". With a heartbeat, **the absence of the
message is itself the signal** — you don't have to remember to check anything.

If the heartbeat fires but the last successful check is stale, it switches to a loud
"something's wrong" variant instead.

> **Historical note.** This started on GitHub Actions. Actions is built for "run something
> after a commit", not continuous polling, and it showed: `schedule` is throttled to roughly
> hourly in practice, jobs are capped at 6 hours (so runs had to chain themselves), chaining
> needs a PAT that expires, private repos meter minutes (a 24/7 loop burns any plan's quota
> in ~2 days), and when a job failed to get a runner the in-job failure alert could never
> fire — so it died silently for hours. A cron-triggered Worker removes those failure modes
> rather than working around them.

## Setup

```bash
git clone https://github.com/wzioleks/lekarz-monitor
cd lekarz-monitor/worker
npm install
npx wrangler login
```

**1. Create the KV namespace** and paste the returned id into `wrangler.toml`:

```bash
npx wrangler kv namespace create STATE
```

**2. Point it at your doctor** — edit `[vars]` in [`worker/wrangler.toml`](worker/wrangler.toml):

```toml
DOCTOR_ID    = "274464"
ADDRESS_ID   = "1042708"
SERVICE_ID   = "2868908"
PROFILE_URL  = "https://www.znanylekarz.pl/<slug>/<specialty>/<city>"
SEARCH_FROM  = "2026-08-21"   # leave blank to search from today
SEARCH_UNTIL = "2026-08-31"
```

To find the IDs: open the doctor's page, press <kbd>F12</kbd> → **Network** → filter
**Fetch/XHR**, click through the calendar, and read the request to
`/api/v3/doctors/<DOCTOR_ID>/addresses/<ADDRESS_ID>/slots`.

**3. Create a Telegram bot:** message [@BotFather](https://t.me/BotFather), send `/newbot`,
follow the prompts, and copy the token. Then open a chat with your new bot and send it any
message — a bot cannot message you first until you've written to it.

```bash
npx wrangler secret put TELEGRAM_BOT_TOKEN
```

Find your chat id by running the Worker locally (see *Testing*) and hitting `/?chatid=1`,
then:

```bash
npx wrangler secret put TELEGRAM_CHAT_ID
```

**4. Optional — ntfy as a secondary channel.** Subscribe to a random, hard-to-guess topic in
the [ntfy](https://ntfy.sh) app, then:

```bash
npx wrangler secret put NTFY_TOPIC
npx wrangler secret put NTFY_TOKEN   # optional; see the rate-limit note above
```

**5. Deploy:**

```bash
npx wrangler deploy
```

The Worker has no public URL (`workers_dev = false`) — it only ever runs from cron, so
nobody can trigger it from the outside.

### Cron schedule

```toml
crons = ["*/5 * * * *",   # availability check
         "0 6 * * *"]     # daily heartbeat (06:00 UTC)
```

The heartbeat expression must match `HEARTBEAT_CRON` in `src/index.js` — that's how the
Worker tells the two triggers apart.

## Testing

`wrangler dev --remote` runs the Worker on Cloudflare's edge with your real bindings and
secrets, exposed on localhost:

```bash
npx wrangler dev --remote
```

| Request | What it does |
|---|---|
| `/` | Run a real check, return JSON diagnostics (no notification) |
| `/?notify=1` | Same, but allowed to notify |
| `/?test=1` | Send a test alert over every configured channel |
| `/?test=tg` | Send a test alert over Telegram only |
| `/?heartbeat=1` | Send the heartbeat now, return the exact API response |
| `/?chatid=1` | List chat ids that have messaged your bot |

Check liveness at any time — `last_run` is written on every successful pass:

```bash
npx wrangler kv key get last_run --namespace-id <id> --remote
```

## Tuning notifications on the phone

The Bot API only distinguishes *normal* from *silent* (`disable_notification`); it cannot set
a vibration pattern or priority. Those are receiver-side, per chat:

**Telegram → open the bot chat → tap its name → Notifications** — set **Vibrate** to `Long`,
**Priority** to `Urgent`, and pick a distinctive **Sound** so you recognise the alert without
looking. **Settings → Notifications and Sounds → Repeat Notifications** will keep reminding
you until you open it.

The daily heartbeat is sent with `disable_notification`, which overrides these — it stays
quiet no matter how loud you make the chat.

## Limitations

- Uses an undocumented public endpoint. If ZnanyLekarz changes its page structure, token
  extraction breaks — you get a failure alert rather than silence.
- Notifications trigger on an *increase* in free slots, not on every individual change.
- The heartbeat is the only guard against total failure; if you ignore its absence, a dead
  monitor still looks like a quiet one.

## Disclaimer

Unofficial and unaffiliated with ZnanyLekarz / Docplanner. It reads the same public
availability data the website serves to anonymous visitors, at a modest polling rate, and it
does not book anything on your behalf. Intended for personal use — please be considerate with
the request rate.

## License

[MIT](LICENSE)
