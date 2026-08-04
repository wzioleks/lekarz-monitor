# ZnanyLekarz Slot Monitor

![monitor](https://github.com/wzioleks/lekarz-monitor/actions/workflows/monitor.yml/badge.svg)

Watches a doctor's calendar on [znanylekarz.pl](https://www.znanylekarz.pl) and pushes a
notification to your phone the moment a new appointment slot opens up in a date range you
care about. Runs entirely on GitHub Actions — no server, no always-on machine.

Cancellations on busy specialists are usually gone within minutes. Polling every 5 minutes
turns "next opening is in 7 weeks" into "somebody just cancelled for next Tuesday".

## How it works

1. A workflow job runs a polling loop, checking every 5 minutes.
2. Each check scrapes a short-lived **anonymous OAuth token** from the doctor's public
   profile page (`ZLApp.APICredentials.ACCESS_TOKEN`) and uses it to query the public
   availability API. No account, no credentials, no cookies to keep alive.
3. Free slots inside the configured window are counted and compared against the previous
   run (persisted in the Actions cache).
4. If the count went **up**, a push notification is sent via [ntfy](https://ntfy.sh).
   Otherwise it stays silent — no repeat alerts for slots you already know about.

### Why a loop instead of `cron: */5`

GitHub throttles scheduled workflows aggressively; a `*/5` cron realistically fires every
few *hours*, not every five minutes. So the schedule is only a safety net — the actual
cadence comes from a loop inside a single job (~5h40m, just under the 6-hour job limit).
When the loop finishes, the job triggers its own successor, producing a continuous chain.

Because of this you'll see a handful of **long-running jobs** per day rather than hundreds
of short ones. That's expected.

## Requirements

- A GitHub account (the repo should be **public** — Actions minutes are free and unlimited
  there; private repos are metered and a 24/7 loop will exhaust any plan's quota in days)
- The [ntfy](https://ntfy.sh) app on your phone (free, no account)

## Setup

1. **Fork** this repository (keep it public).

2. **Subscribe to an ntfy topic.** Install the ntfy app, tap **+**, and enter a random,
   hard-to-guess topic name — anyone who knows the topic can post to it.

3. **Add repository secrets** — *Settings → Secrets and variables → Actions*:

   | Secret | Required | Purpose |
   |---|:---:|---|
   | `NTFY_TOPIC` | ✅ | Your ntfy topic — where notifications are delivered |
   | `ZL_PAT` | ✅ | Fine-grained PAT with **Actions: Read and write** on this repo; lets each run trigger its successor |
   | `DOCTOR_NAME` | — | Display name used in the notification title |
   | `DOCTOR_PAGE_URL` | — | Link opened when the notification is tapped |
   | `TELEGRAM_BOT_TOKEN` / `TELEGRAM_CHAT_ID` | — | Optional second notification channel |

   > `ZL_PAT` is required because the built-in `GITHUB_TOKEN` is deliberately not allowed to
   > trigger workflows. If it expires, the chain stops restarting itself and you get a
   > failure alert — the hourly schedule keeps things alive until you rotate it.

4. **Point it at your doctor.** Edit the constants at the top of
   [`check_slots.py`](check_slots.py):

   ```python
   DOCTOR_ID    = 274464
   ADDRESS_ID   = 1042708
   SERVICE_ID   = 2868908
   PROFILE_URL  = "https://www.znanylekarz.pl/<slug>/<specialty>/<city>"

   SEARCH_FROM  = date(2026, 8, 21)   # None → start from today
   SEARCH_UNTIL = date(2026, 8, 31)
   ```

   To find the IDs: open the doctor's page, press <kbd>F12</kbd> → **Network** → filter
   **Fetch/XHR**, click through the calendar, and look at the request to
   `/api/v3/doctors/<DOCTOR_ID>/addresses/<ADDRESS_ID>/slots`.

5. **Enable Actions** on your fork and run the workflow once manually to verify.

> **Changing the date window?** Bump the cache key prefix (`slot-state-vN-`) in
> [`monitor.yml`](.github/workflows/monitor.yml). The alert fires on *count increases*, so a
> stale counter from a different window can silently suppress a notification.

## Running locally

```bash
pip install requests
NTFY_TOPIC=your-topic python check_slots.py
```

Without `NTFY_TOPIC` it just prints what it found — handy for checking a date range.

## Reliability

- **Transient failures are tolerated** — three consecutive errors are required before the
  run aborts, so a blip at the far end doesn't cause a false alarm.
- **Failures are loud.** If the monitor dies, a separate urgent notification is sent with a
  link to the failed run, so it never stops silently.
- **State** lives in `last_count.txt`, restored and saved through the Actions cache.

## Limitations

- Uses an undocumented public endpoint; if ZnanyLekarz changes its page structure or API,
  token extraction breaks (you'll get a failure alert).
- Scheduled workflows are disabled by GitHub after 60 days without repository activity.
- Notifications trigger on an *increase* in free slots, not on every individual change.

## Disclaimer

Unofficial and unaffiliated with ZnanyLekarz / Docplanner. It reads the same public
availability data the website itself serves to anonymous visitors, at a modest polling rate,
and it does not book anything on your behalf. Intended for personal use — please be
considerate with the request rate.

## License

[MIT](LICENSE)
