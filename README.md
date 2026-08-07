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
4. If the count went **up**, a push notification is sent via [ntfy](https://ntfy.sh).
   Otherwise it stays silent — no repeat alerts for slots you already know about.
5. If a run throws (e.g. the page structure changed and the token can't be extracted), a
   separate urgent alert is sent instead, so failures aren't silent.

The whole thing is one file: [`worker/src/index.js`](worker/src/index.js).

> **Historical note.** This started on GitHub Actions. Actions is built for
> "run something after a commit", not for continuous polling, and it showed: `schedule` is
> throttled to roughly hourly in practice, jobs are capped at 6 hours (so runs had to chain
> themselves), chaining needs a PAT that expires, private repos meter minutes (a 24/7 loop
> burns any plan's quota in ~2 days), and when a job failed to get a runner the in-job
> failure alert could never fire — so it died silently. A cron-triggered Worker removes all
> of those failure modes rather than working around them. The old workflow is kept in
> [`.github/workflows/monitor.yml`](.github/workflows/monitor.yml) for reference, disabled.

## Requirements

- A free [Cloudflare](https://dash.cloudflare.com/sign-up) account (Workers free plan is
  ample: 100k requests/day, we use ~288)
- A free [ntfy.sh](https://ntfy.sh) account and the ntfy app on your phone

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
SEARCH_FROM  = "2026-08-21"   # or omit/blank to search from today
SEARCH_UNTIL = "2026-08-31"
```

To find the IDs: open the doctor's page, press <kbd>F12</kbd> → **Network** → filter
**Fetch/XHR**, click through the calendar, and read the request to
`/api/v3/doctors/<DOCTOR_ID>/addresses/<ADDRESS_ID>/slots`.

**3. Set the notification secrets:**

```bash
npx wrangler secret put NTFY_TOPIC   # your random ntfy topic
npx wrangler secret put NTFY_TOKEN   # ntfy access token — set it to never expire
```

> `NTFY_TOKEN` is not optional in practice. ntfy.sh rate-limits publishing **per IP**, and
> Workers egress from shared Cloudflare addresses — without an account token you inherit
> a stranger's exhausted quota and get `HTTP 429` with no notification. An account token
> moves the limit onto your own account.

**4. Deploy:**

```bash
npx wrangler deploy
```

The Worker has no public URL (`workers_dev = false`) — it only ever runs from cron, so
nobody can trigger it from outside.

## Testing

`wrangler dev --remote` runs the Worker on Cloudflare's edge with your real bindings and
exposes it locally:

```bash
npx wrangler dev --remote
curl "http://127.0.0.1:8787/"          # run a real check, return JSON diagnostics
curl "http://127.0.0.1:8787/?test=1"   # send a test notification, show ntfy's exact reply
```

Check liveness at any time — `last_run` is written on every successful pass:

```bash
npx wrangler kv key get last_run --namespace-id <id> --remote
```

## Limitations

- Uses an undocumented public endpoint. If ZnanyLekarz changes its page structure, token
  extraction breaks — you get a failure alert rather than silence.
- Notifications trigger on an *increase* in free slots, not on every individual change.
- If the Worker stops running entirely, nothing reports it — the alarm travels over the
  same channel as everything else.

## Disclaimer

Unofficial and unaffiliated with ZnanyLekarz / Docplanner. It reads the same public
availability data the website serves to anonymous visitors, at a modest polling rate, and
it does not book anything on your behalf. Intended for personal use — please be considerate
with the request rate.

## License

[MIT](LICENSE)
