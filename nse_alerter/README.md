# Futures 5m Alerter — NIFTY (NSE) + Crude / Natural Gas (MCX) → Telegram

**Instruments (`SYMBOLS=`, comma list — default: `NIFTY1!~env` +
`MCX:CRUDEOIL~env@17:00-22:00`, both on Magic Envelope; crude alerts gated
17:00–22:05 IST. NG off — add `MCX:NATURALGAS` back any time to re-enable it):**

**Weekly rotation (card v5):** every weekday is an intraday-strangle entry
day — NIFTY Fri/Mon/Tue (expires Tue), SENSEX Wed/Thu (expires Thu): closer
0.95% OTM strikes, credit floor ₹18/₹40, 20 lots fixed, whole-position stop
−0.5C (−1.0C on NIFTY 0-DTE), target = session decay to 15:15. Thu = SENSEX
**NO ENTRY** (measured E<0 at every VIX). The envelope pilot runs **every
day** (NIFTY + crude — charts checked on NIFTY only) and a **09:45 alert**
fires each trading day with the day's decision. SENSEX feed: TradingView
`BSE:SENSEX` → Yahoo `^BSESN`, both verified against the press close (see
`nse_alerts/expiry.py`).

| Watch | Exchange | Session (IST, Mon–Fri) | Data source |
|---|---|---|---|
| `NIFTY1!` — NIFTY futures | NSE | 09:15–15:35 | TradingView → `^NSEI` yahoo proxy |
| `MCX:CRUDEOIL` — crude oil futures | MCX | 09:00–23:35, **alerts gated 17:00–22:05** (`@17:00-22:00`) | TradingView `TVC:UKOIL` (real-time Brent CFD, anonymous OK) → yahoo `BZ=F` fallback (**+10 min stale**, measured 2026-10-06) — MCX symbols are blocked for anonymous TV |
| `MCX:NATURALGAS` — natural gas futures | MCX | 09:00–23:35 | yahoo `NG=F` (Henry Hub) — same reason |

QQE is RSI-scale based, so Brent/Henry-Hub proxies track MCX closely even
though prices differ (USD vs INR). Exact MCX contracts come later via Kite (§6).

**Strategies (toggle via `STRATEGY=`, no code changes):**

| Value | Rule |
|---|---|
| **`qqe`** *(default)* | Ported **"QQE signals"** Pine script (colinmck): Wilders-RSI → smoothed ATR-of-RSI bands; alert when the trailing line flips across RSIndex → `BUY` (Long) / `SELL` (Short). Smoother, fewer whipsaws (~5 flips/day on recent NIFTY data vs EMA20's ~11). |
| `env` | **Magic Envelope** port: SMA20 ± `ENVELOPE_PERCENT` band (0.2%); a *full bar* beyond the band flips the side (carry-forward). Backtest on a month of 5m bars: **crude 5.3 flips/day (3–7, no dead days)**, NIFTY 0.5/day — tune per symbol. Attach per-watch with `SYMBOLS … ~env` or run globally via `/strategy env`. **Prep heads-up**: while an env watch is live, the *forming* bar beyond the band sends a one-off `⚠️ ENV pre-flip · … flip PENDING` within one cron tick — state never moves on prep, and a completed bar closing back inside the band re-arms the episode (`PREP_ALERTS=false` opts out). |
| `ema20` | The original rule: 5m close flips below→above EMA20 → `BUY`, above→below → `SELL`. |
| `both` | Both engines run side by side with **independent dedupe state** (`NIFTY1!` and `NIFTY1!#qqe`), one daily heartbeat. |

Alerts are flip-based only (silent while the side stays the same), delivered to
**Telegram** (push on your phone), deduplicated via local state. Cloud toggle:
repo **Settings → Secrets and variables → Actions → Variables** → add
`STRATEGY` = `qqe` / `ema20` / `both` (defaults to `qqe` when unset).

```
nse_alerter/
├── main.py               # entry point (one evaluation per invocation)
├── .env                  # your secrets/config (created from .env.example, gitignored)
├── state.json            # dedupe state (auto-created, gitignored)
├── nse_alerter.log       # appended run log (LOG_FILE in .env)
├── nse_alerts/           # the package
│   ├── app.py            # gate → fetch → evaluate → dedupe → send → save
│   ├── signals.py        # EMA20 cross engine (the rule)
│   ├── market_hours.py   # NSE 09:15–15:35 + MCX 09:00–23:35 IST, Mon–Fri · per-watch @windows
│   ├── state.py          # JSON state store (exactly-once alerts)
│   ├── notify.py         # Telegram sender
│   ├── config.py         # env-driven config
│   └── providers/        # tv (TradingView futures) → yahoo (spot proxy) → kite (later)
└── tests/                # 128 offline tests: `python -m pytest`
```

## 1. Install

```powershell
cd d:\Cursor\python_for_java_devs\nse_alerter
pip install -r requirements.txt      # deps are already installed on this machine
```

## 2. Telegram setup (~2 minutes)

**Step A — create the bot and get the TOKEN:**

1. Install Telegram (phone or desktop) and sign in.
2. In the search box, type **`BotFather`** — pick the official one (✓ verified badge).
   It is Telegram's own "bot that makes bots". 
3. Send it the command: `/newbot`
4. It asks *"What name do you want for your bot?"* → type any **display name**,
   e.g. `NSE Alerter` (spaces OK, this is just a label).
5. It then asks *"What username do you want for your bot?"* → pick a username
   that **ends with `bot`** and is lowercase, e.g. `deepak_nse_alerter_bot`.
   If taken, add digits.
6. BotFather replies with a success message containing your **API token** —
   a long string like `7925846123:AAHk3...xyz`. **Copy it.**
7. Paste it into `nse_alerter\.env`:
   ```ini
   TELEGRAM_BOT_TOKEN=7925846123:AAHk3...xyz
   ```
   (Treat it like a password — don't share it.)

**Step B — get your CHAT ID (automatic):**

8. In Telegram, search for the username **you just created** (e.g. `deepak_nse_alerter_bot`),
   open it and press **START** (or send `hi`). This creates the chat your alerts will enter.
9. On your PC:
   ```powershell
   cd d:\Cursor\python_for_java_devs\nse_alerter
   python get_chat_id.py
   ```
   It prints your `TELEGRAM_CHAT_ID` and **saves it into `.env` automatically**.

**Step C — verify:**

10. ```powershell
    python main.py --test-notify     # your phone should buzz with a test message
    ```

<details>
<summary>Manual alternative for the chat id (browser way)</summary>

After step 8, open in your browser (replace TOKEN with your actual token):
`https://api.telegram.org/bot<TOKEN>/getUpdates`
→ find `"chat":{"id": 7182...}` → copy that number into `TELEGRAM_CHAT_ID=` in `.env`.
If you see `{"ok":true,"result":[]}` you haven't pressed START on your bot yet.

</details>

## 3. Run it

```powershell
python main.py --dry-run                # evaluate + print, sends nothing, state untouched
python main.py                          # the real run (no-op outside NSE hours)
python main.py --replay 2026-09-25      # walk a past session bar-by-bar: every cross
                                         # that WOULD have fired (no sends, no state)
python main.py --verbose                 # debug logging
```

- **First real run** records a *baseline* (current side) without alerting.
- **Daily liveness heartbeat**: the first successful run each trading day sends
  `🔎 monitoring live · <date> · side=…` — if it's missing by ~09:25 IST, the
  scheduler itself is down (check the Actions page).
- **Manual runs reply**: `workflow_dispatch` ("Run workflow") **before the
  09:00 open** while every session is closed sends `🧪 manual run · … out of
  session` — instant proof-of-life. Scheduled runs, external-cron dispatches
  and post-close runs stay quiet (no nightly notes).
- **08:45 plan card (v5)**: the first run between 08:45–09:10 sends the day's
  discipline card once per trading day — every weekday leads with its
  strangle plan (NIFTY Fri/Mon/Tue, SENSEX Wed/Thu, Thu = NO ENTRY reminder)
  plus the envelope-pilot rules. The 09:10 end is a CI-startup grace — a
  dispatch triggered at 08:59 only *executes* after ~09:00. Primary tick:
  cron-job.org job `45,50,55 8 * * 1-5` (Asia/Kolkata); the 09:00/09:05
  main-cron ticks and the workflow schedule back it up.
- **09:45 entry alert (every trading day)**: `strike_rule()` → closer 0.95%
  OTM CE+PE → indicative Black-Scholes premiums (India VIX, r=0) → credit
  floor (₹18 NIFTY / ₹40 SENSEX, else SKIP) → **2× per-leg stops** +
  whole-position stop −0.5C (−1.0C on NIFTY 0-DTE) + session-decay target +
  20 lots fixed + 15:15 square-off (Thu SENSEX = ⛔ NO ENTRY alert). Once per
  day (dedupe key `expiry_alert_date`); a failed send retries every 5-min
  tick until 10:15. The envelope is **never paused** — it keeps evaluating
  and alerting every day; tripwires and the daily heartbeat are unaffected.
- **Trend tripwires (Nifty, all days)**: ±0.45% from the session open →
  `⚔️ … NO averaging`; ±0.8% → `🔴 TREND DAY … NO re-entry` — once per level
  per day (tuned on 60d of NIFTY 5m: 3/3 big days caught).
- Exit codes: `0` ok/no-op · `1` config (e.g. missing Telegram creds) ·
  `2` Telegram failed (state not saved → auto-retry next minute) · `3` data failed.
- Logs: console + `nse_alerter.log` (Task Scheduler has no console).

### Control it from your phone (Telegram commands)

The live run polls the bot chat first and honors **only your chat id**:

| Send to your bot | Effect |
|---|---|
| `/disable` | 🛑 cloud runs stay idle (no market fetch, no alerts) until you re-enable |
| `/enable` | ✅ resumes — even evaluates immediately on that run |
| `/status` | 📊 on/off flag, **effective strategy**, current side, last bar & last event |
| `/strategy qqe` · `/strategy ema20` · `/strategy both` | 🎯 **switch the strategy from your phone** — persisted in `state.json`, applied on the very next scan, shown in `/status` as "Telegram override" |
| `/strategy default` | 🎯 remove the override and return to the `.env`/repo-variable value |

**Menu paused:** the ☰ inline menu is built & tested but currently **not
attached to messages** — tap latency without an always-on server made it feel
dead. Typed commands (table above) are the interface; stale ☰ buttons on old
messages are acknowledged but inert. Re-enable later: `MENU_ENABLED = True`
in `nse_alerts/control.py` + re-attach `MENU_KEYBOARD` at the send sites.
Replies arrive within one scheduler cycle (≤5 min during market hours, or on
the next manual "Run workflow" — polling happens before the session gate, so
commands and taps answer anytime a run happens). The flag persists in `state.json`
(alongside signal state, already cached by GitHub Actions). Dry runs never
poll `getUpdates` (it's a consuming read reserved for the live scheduler).

> Note: GitHub's **mobile app cannot toggle workflows** — that's web-only.
> These Telegram commands are the phone-first way to pause/resume; the web
> toggle (Actions → nse-alerts → ⋯) remains for fully switching the cron off.

## 4. Schedule (Windows Task Scheduler — every minute, Mon–Fri, 09:14–15:35 IST)

Run once in PowerShell (**fill `.env` first** — otherwise in-session runs exit 1):

```powershell
$py    = "C:\Users\deepa\AppData\Local\Microsoft\WindowsApps\PythonSoftwareFoundation.Python.3.11_qbz5n2kfra8p0\pythonw.exe"
$dir  = "d:\Cursor\python_for_java_devs\nse_alerter"
$action = New-ScheduledTaskAction -Execute $py -Argument "$dir\main.py" -WorkingDirectory $dir
$trigger = New-ScheduledTaskTrigger -Daily -At "09:14"
# New-ScheduledTaskTrigger can't express repetition directly -> attach it to the CIM object:
$rep = New-CimInstance -CimClass (Get-CimClass -Namespace "Root\Microsoft\Windows\TaskScheduler" -ClassName "MSFT_TaskRepetitionPattern") -ClientOnly
$rep.Interval  = "PT1M"        # every 1 minute
$rep.Duration  = "PT6H21M"     # 09:14 -> 15:35
$trigger.Repetition = $rep
Register-ScheduledTask -TaskName "NSE-EMA-Cross-Alerter" -Action $action -Trigger $trigger -Description "NIFTY fut 5m EMA20 cross -> Telegram" -Force
```

Outside market hours the task still fires but exits immediately (cheap no-op);
weekends/holidays are skipped by `market_hours.py` — cloud runs carry the
2026 NSE/BSE holiday list as the workflow `HOLIDAYS` default (repo Variable
overrides; MCX trades some NSE-only holidays, which we conservatively skip).
Check it with:

```powershell
Get-ScheduledTask -TaskName "NSE-EMA-Cross-Alerter" | Get-ScheduledTaskInfo
Get-Content d:\Cursor\python_for_java_devs\nse_alerter\nse_alerter.log -Tail 20 -Wait
```

Remove: `Unregister-ScheduledTask -TaskName "NSE-EMA-Cross-Alerter" -Confirm:$false`

## 5. Data sources (and why two of them)

| Provider | What it gives | When used |
|---|---|---|
| **tv** (default for NSE + crude) | `NSE:NIFTY1!` — actual **NIFTY futures**, continuous front-month (auto-rolls at expiry), real-time for retail users; `TVC:UKOIL` Brent CFD for crude | first choice in `DATA_PROVIDER=auto` for NSE watches **and** for crude (built-in TV override) |
| **yahoo** (fallback for NSE & crude; **primary for other MCX**) | `^NSEI` spot proxy for NIFTY; `BZ=F` Brent + `NG=F` Henry Hub for crude/natgas | TradingView's unofficial API **rejects MCX symbols for anonymous sessions** (verified), so plain MCX watches go yahoo-first — but crude has a TV override (`TVC:UKOIL`, real-time) because `BZ=F` measured **+10 min stale** (2026-10-06: bar 20:55 at 21:09), making TV the lead and BZ=F the fallback. Alerts show `src=` of whichever fired |
| **kite** (opt-in paid) | your Zerodha account's real front-month futures — **exact MCX INR contracts too** (instrument master cached 6 h; a `-FUT` filter picks the NIFTY/crude/SENSEX front month automatically) | leads every chain when you set `DATA_PROVIDER=kite` + credentials; the free chain stays behind it, so a stale daily token just falls back |

## 6. Zerodha Kite (paid feed — leads the chain, with free-feed insurance)

You're on the **paid Kite Connect plan** (₹500 / 30 days) with an app at
`developers.kite.trade` → *My Apps* (`redirect_url = http://localhost:3000`).

1. Put in `.env` (both from the *My Apps* page — `api_secret` is shown once at
   app creation):
   ```ini
   DATA_PROVIDER=kite
   KITE_API_KEY=your_api_key
   KITE_API_SECRET=your_api_secret
   ```
2. **Daily token** (`access_token` dies every trading day, ~6 AM IST):
   ```powershell
   python kite_login.py      # opens the Kite login in your browser; do the
                             # TOTP yourself; the localhost:3000 redirect is
                             # caught and KITE_ACCESS_TOKEN is saved into .env
   ```
   Variants: `python kite_login.py --url "<redirect url from the address bar>"`
   (paste instead of letting the script listen), or `--auto` for fully headless
   login driven by `KITE_USER_ID` / `KITE_PASSWORD` / `KITE_TOTP_SECRET`
   (base32 from your authenticator app; an *unofficial* form flow — if
   Zerodha changes the form it fails loudly and browser mode still works).
   Schedule it at 08:55 each trading morning:
   ```powershell
   $action = New-ScheduledTaskAction -Execute "python" -Argument "kite_login.py --auto" `
       -WorkingDirectory "d:\Cursor\python_for_java_devs\nse_alerter"
   Register-ScheduledTask -TaskName "Kite-Daily-Login" -Action $action `
       -Trigger (New-ScheduledTaskTrigger -Daily -At 08:55) -Force
   ```
3. What kite gives the bot: the **real front-month futures** for every watch —
   NIFTY on NFO, crude on MCX (the exact INR contract, no proxy delay),
   SENSEX on BFO — picked automatically from the instrument master
   (`https://api.kite.trade/instruments`, downloaded once per **6-hour cache
   window**, never per fetch), so expiry rolls need zero maintenance. History
   depth beats the free feeds (months of 5-minute candles vs TradingView's
   ~6k-row cap ≈ 3 weeks and yahoo's 60 days), at the same one historical
   call per watch per cron tick.

**Insurance (why you can leave it on):** `DATA_PROVIDER=kite` puts kite
*first* but keeps the free TradingView/yahoo chain behind it — if the token
is stale (forgot the morning login, long weekend, credits exhausted), every
fetch logs `provider kite failed … -> using fallback provider` and the alerts
keep flying on the free feeds. Nothing goes dark.

**Credit caveat:** the plan's "500 credits" metering isn't documented
precisely (check *My Apps* for your balance), and the bot spends ~1 historical
call per watch per 5-minute tick while kite leads (≈500 calls/day for the
default NIFTY + crude list). If the balance drains faster than the ₹500/30-day
plan renews, drop back to `DATA_PROVIDER=auto` — the bot never depends on
kite succeeding.

## 7. Cloud deployment (GitHub Actions) — runs while your PC is OFF

Implemented in **`.github/workflows/nse-alerts.yml`** (repo root): the same
`main.py` runs on GitHub's servers **every 5 minutes across BOTH sessions**
(Mon–Fri, 03:30–18:05 UTC = 09:00–23:35 IST — IST has no DST, so this never
drifts; NSE-only hours are skipped by the per-exchange gate). ~176 runs/day
⇒ **make the repo PUBLIC** (Settings → Danger Zone → Change visibility) for
unlimited free Actions minutes; on a private repo the free 2,000 min/month
would last only ~11 days. Secrets stay hidden in Actions Secrets either way.

Watch list override: repo **Variables → `SYMBOLS`** (e.g. `NIFTY1!` alone),
else the default `NIFTY1!,MCX:CRUDEOIL,MCX:NATURALGAS`.

**One-time setup — add the 2 secrets** (Settings → Secrets, not in code):

1. Open: `https://github.com/deepakonroll/Python_learning/settings/secrets/actions`
2. **New repository secret** → Name `TELEGRAM_BOT_TOKEN`, Value = the token from `.env` → Save
3. **New repository secret** → Name `TELEGRAM_CHAT_ID`, Value = `674729729` → Save
4. First test: **Actions → nse-alerts → Run workflow** (manual button) → green check ✓
   (it prints `test`/`baseline`/`no cross` lines in the run log)
5. From then on the cron does everything — **you can keep the PC off.**

**Disable the local Windows task once cloud is confirmed** (avoids double alerts
on days you DO keep the PC on):

```powershell
Unregister-ScheduledTask -TaskName "NSE-EMA-Cross-Alerter" -Confirm:$false
# to bring it back later: re-run the Register-ScheduledTask block in §4
```

**Cloud caveats**

- State (`state.json`) is kept across runs via GitHub's cache; a rare cache miss
  self-heals as a silent baseline (worst case: one cross in that gap is skipped —
  never a duplicate).
- GitHub **e-mails you if the workflow fails**, and pauses scheduled runs after
  **60 days with zero repo activity** (any push/reactivate fixes it).
- Check usage anytime: `github.com/deepakonroll/Python_learning/actions`
- GitHub servers run outside India → TradingView's unofficial feed fails more
  often there, so alerts more often show `src=yahoo` (spot proxy) — set up Kite
  (§6) for authoritative futures data if that bothers you.
- Same pattern works for **AWS Lambda + EventBridge** (zip the package, env vars
  in configuration, `state.json` in S3) if you ever outgrow Actions.

## 8. Tests & troubleshooting

```powershell
cd d:\Cursor\python_for_java_devs\nse_alerter
python -m pytest                # 45 tests, all offline (synthetic candles, mocked HTTP)
```

| Symptom | Fix |
|---|---|
| exit 1 in logs during market hours | `TELEGRAM_BOT_TOKEN`/`TELEGRAM_CHAT_ID` missing in `.env` |
| `provider tv failed` warnings | TradingView's unofficial feed hiccuped — fallback to yahoo kicked in (alert will say `src=yahoo`) |
| No messages at all | run `python main.py --test-notify`; check Telegram spam/first-contact privacy (tap *Start* on your bot) |
| Alert repeated after restart | expected only if the side genuinely flipped back while the app was down |
| Wrong feed in alert text | `src=` tells you: `tv`=futures, `yahoo`=spot proxy, `kite`=your Zerodha data |

## 9. Roadmap / extension points

- **Email / ntfy push**: add a sibling function in `nse_alerts/notify.py`
  (SMTP via stdlib, or `POST https://ntfy.sh/<topic>`) and call it next to
  `send_telegram` in `app.py` — ~20 lines, no other changes.
- **Multiple symbols**: `evaluate()`/state are already keyed per symbol —
  loop over a comma-separated `SYMBOLS` list in `app.run`.
- **Different rule** (RSI, Supertrend, ...): implement in `signals.py`
  returning the same `CrossEvent` shape.

---

*Educational tool — signals are informational, not trade advice; you are
responsible for any trading decisions (and SEBI/RBI regulations apply to you,
not this script).*

