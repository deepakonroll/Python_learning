# Futures 5m Alerter — NIFTY (NSE) + Crude / Natural Gas (MCX) → Telegram

**Instruments (`SYMBOLS=`, comma list — default: `NIFTY1!~both` +
`MCX:CRUDEOIL~both@17:00-22:00`; both run **EMA20+QQE consensus** alerts
(alerts fire only when both engines agree on direction)
with the env flip/prep **OFF** since 2026-10-17, gated 17:00–22:05 IST.
NG off — add `MCX:NATURALGAS` back any time to re-enable it):**

**Weekly rotation (retired 2026-10-22):** the 09:45 intraday-strangle entry
alert is **off** — the premium/expiry machinery still lives in
`nse_alerts/expiry.py` only for the 09:20 snapshot's countdown header and the
research tools. SENSEX feed: TradingView `BSE:SENSEX` → Yahoo `^BSESN`, both
verified against the press close (see `nse_alerts/expiry.py`).

| Watch | Exchange | Session (IST, Mon–Fri) | Data source |
|---|---|---|---|
| `NIFTY1!` — NIFTY futures | NSE | 09:15–15:35 | TradingView → `^NSEI` yahoo proxy |
| `MCX:CRUDEOIL` — crude oil futures | MCX | 09:00–23:35, **alerts gated 17:00–22:05** (`@17:00-22:00`) | TradingView `NYMEX:BZ1!` (continuous **Brent futures** — the same `BZ1!` chart on TradingView; real-time, anonymous OK) → yahoo `BZ=F` fallback (**+10 min stale**, measured 2026-10-06) — MCX symbols are blocked for anonymous TV |
| `MCX:NATURALGAS` — natural gas futures | MCX | 09:00–23:35 | yahoo `NG=F` (Henry Hub) — same reason |

QQE is RSI-scale based, so Brent/Henry-Hub proxies track MCX closely even
though prices differ (USD vs INR). Exact MCX contracts come later via Kite (§6).

**Strategies (toggle via `STRATEGY=`, no code changes):**

| Value | Rule |
|---|---|
| **`qqe`** *(default)* | Ported **"QQE signals"** Pine script (colinmck): Wilders-RSI → smoothed ATR-of-RSI bands; alert when the trailing line flips across RSIndex → `BUY` (Long) / `SELL` (Short). Smoother, fewer whipsaws (~5 flips/day on recent NIFTY data vs EMA20's ~11). |
| `env` | **Magic Envelope** port: SMA20 ± `ENVELOPE_PERCENT` band (0.2%); a *full bar* beyond the band flips the side (carry-forward). Backtest on a month of 5m bars: **crude 5.3 flips/day (3–7, no dead days)**, NIFTY 0.5/day —
  tune per symbol. Attach per-watch with `SYMBOLS … ~env` or run globally via `/strategy env`. **Prep heads-up**: while an env watch is live, the *forming* bar beyond the band sends a one-off `⚠️ ENV pre-flip · … flip PENDING` within one cron tick — state never moves on prep, and a completed bar closing back inside the band re-arms the episode (`PREP_ALERTS=false` opts out). |
| `ema20` | The original rule: 5m close flips below→above EMA20 → `BUY`, above→below → `SELL`. |
| `both` | **Consensus mode**: both engines (EMA20 + QQE) run on every bar, but an alert fires **only when both agree on the same direction AND the consensus direction changes** (flip). No alert on first run (baseline), no alert while the agreed direction stays the same, and no alert when engines disagree. Individual engine sides are tracked under `key` (EMA20) and `key#qqe` for warm-up continuity and `/status` diagnosis; the consensus direction is stored under `key#both`. One daily heartbeat. **Both NIFTY and Crude now default to `~both`** — high-conviction alerts only. **Backtest (last 20 trading days, 5m bars)**: NIFTY — 8 signals (0.4/day), 75% same-day accuracy; CRUDE (BZ=F, 17:00–22:00 IST) — 42 signals (2.1/day), 55% same-day accuracy. Run `python tools/consensus_backtest.py` (NIFTY) or `python tools/consensus_backtest_crude.py` (CRUDE). |

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
└── tests/                # 198 offline tests: `python -m pytest`
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
- **09:05 world-markets update (v1)**: the 09:02–09:10 run sends the day's
  overnight snapshot once per day (`world_date`) — 🇺🇸 Nasdaq/Dow-futures,
  🇪🇺 FTSE/CAC/DAX, 🌏 Nikkei/Hang-Seng (Yahoo, % vs prev close, 🟢/🔴 by
  sign), 🎯 GIFT Nifty (TradingView `NSEIX:NIFTY1!`), 💼 prior-session
  FII/DII cash flows (chirag127 static-JSON GitHub API; gitignore-clean, no
  keys). Every block is best-effort — a dead feed omits its row, never the
  message (see `nse_alerts/world.py`). The 08:45–08:55 external-cron
  dispatches fall in the pre-open quiet window and stay silent.
- **09:20 opening levels (v1)**: the 09:17–09:25 run sends the day's opening
  snapshot once per day (`levels_date`) — 🇮🇳 NIFTY spot and 😨 India VIX
  (Yahoo, level + day change by 🟢/🔴 sign), 🎯 GIFT Nifty overnight cue
  (TradingView), then the ATM straddles for NIFTY (50-pt grid) and SENSEX
  (100-pt grid): BS-indicative CE + PE premiums at the nearest strike with
  total ₹ and % of spot (the expiry.py pricing kernels, see
  `nse_alerts/snapshot.py`). The header counts down the next expiry
  (⏳ `exp Tue · 1.9 sess`, matching the Mon/Tue Wed/Thu Fri playbook
  rotation) and a 🛡️ guard-rail line (`−₹7k WARN · −₹10k EXIT · flat ALL
  by 15:15`) closes the message. Each block is best-effort; the 08:45
  discipline card was **retired** (2026-10-21) and the 09:45 expiry entry
  alert was **retired** (2026-10-22) — NIFTY + crude run their engines every
  day.
- **Trend tripwires (Nifty, all days)**: ±0.45% from the session open →
  `⚔️ … NO averaging`; ±0.8% → `🔴 TREND DAY … stops live` — once per level
  per day (tuned on 60d of NIFTY 5m: 3/3 big days caught). The tripwires are
  **information only**: every consensus/envelope signal is still **delivered**
  even after the day is declared (owner call 2026-10-26 — the gate that used
  to swallow re-entries was removed; 26 Oct's 11:10 SELL and 12:30 BUY both
  go out now).
- **Stale-feed guard (`FRESHNESS_GUARD=true`)**: live runs are **skipped** when
  the feed's last bar is still forming upstream (more than one bar behind the
  current boundary). A lagging feed otherwise produces alert prices that never
  appear on the chart (26 Oct: the 09:55 "close 22,638.90" card vs the real
  22,724.70). The state dedupe then fires the real cross once, on final data,
  at most a bar late. Set `false` to disable.
- **Signal latency**: entry alerts are **confirmed on completed 5m bars only** (no
  live half-bar whipsaws), so the earliest a cross can be announced is the
  bar's close. The GitHub schedule runs **+2 min offset** from the bar
  boundaries, so each tick lands inside a *forming* bar: `PREP_ALERTS=true`
  sends a **`⚠️ … flip PENDING`** heads-up for **ema20 / qqe / both** (not just
  env) while the live bar is crossing, before the full-bar confirmation. Every
  confirmed alert is stamped with its own age (`⏱️ bar closed 10:10 · 2 bar(s)
  late · sent 10:20`) so a slow pipeline never looks fresh.
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
| **tv** (default for NSE + crude) | `NSE:NIFTY1!` — actual **NIFTY futures**, continuous front-month (auto-rolls at expiry), real-time for retail users; `NYMEX:BZ1!` continuous **Brent futures** for crude (the same `BZ1!` chart on TradingView) | first choice in `DATA_PROVIDER=auto` for NSE watches **and** for crude (built-in TV override) |
| **yahoo** (fallback for NSE & crude; **primary for other MCX**) | `^NSEI` spot proxy for NIFTY; `BZ=F` Brent + `NG=F` Henry Hub for crude/natgas | TradingView's unofficial API **rejects MCX symbols for anonymous sessions** (verified), so plain MCX watches go yahoo-first — but crude has a TV override (`NYMEX:BZ1!`, real-time) because `BZ=F` measured **+10 min stale** (2026-10-06: bar 20:55 at 21:09), making TV the lead and BZ=F the fallback. Alerts show `src=` of whichever fired |
| **kite** (paid) | your Zerodha account's real front-month futures — **exact MCX INR contracts too** (instrument master cached 6 h; a `-FUT` filter picks the NIFTY/crude/SENSEX front month automatically) | leads when `DATA_PROVIDER=kite`; Yahoo is the only fallback, so an expired token never silently switches to TradingView |

## 6. Zerodha Kite (paid feed — Yahoo fallback)

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

**Fallback behavior:** `DATA_PROVIDER=kite` tries Kite first and Yahoo second.
If Kite's token is stale or Kite is unavailable, fetch logs identify the
fallback and alerts use the configured Yahoo proxy (for example `^NSEI` for
NIFTY and `BZ=F` for crude), not the exact Zerodha contract.

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

**Initial setup — add these repository secrets** (Settings → Secrets, not in code):

1. Open: `https://github.com/deepakonroll/Python_learning/settings/secrets/actions`
2. **New repository secret** → Name `TELEGRAM_BOT_TOKEN`, Value = the token from `.env` → Save
3. **New repository secret** → Name `TELEGRAM_CHAT_ID`, Value = `674729729` → Save
4. **New repository secret** → Name `KITE_API_KEY`, Value from your Kite developer app → Save
5. **New repository secret** → Name `KITE_ACCESS_TOKEN`, Value from this morning's Kite login → Save. Update this secret each trading morning after generating a fresh token; it expires around 06:00 IST the next day.
6. First test: **Actions → nse-alerts → Run workflow** (manual button) → green check ✓
   (it prints `test`/`baseline`/`no cross` lines in the run log)
7. From then on the cron does everything — **you can keep the PC off.**

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
- Both Actions workflows use `DATA_PROVIDER=kite`: Kite is tried first, with
  Yahoo as the only fallback. The Kite access-token secret must be refreshed
  each trading morning; when it expires, the alert source changes to Yahoo.
- Same pattern works for **AWS Lambda + EventBridge** (zip the package, env vars
  in configuration, `state.json` in S3) if you ever outgrow Actions.

## 8. Tests & troubleshooting

```powershell
cd d:\Cursor\python_for_java_devs\nse_alerter
python -m pytest                # 198 tests, all offline (synthetic candles, mocked HTTP)
```

| Symptom | Fix |
|---|---|
| exit 1 in logs during market hours | `TELEGRAM_BOT_TOKEN`/`TELEGRAM_CHAT_ID` missing in `.env` |
| `provider tv failed` warnings | TradingView's unofficial feed hiccuped — fallback to yahoo kicked in (alert will say `src=yahoo`) |
| No messages at all | run `python main.py --test-notify`; check Telegram spam/first-contact privacy (tap *Start* on your bot) |
| Alert repeated after restart | expected only if the side genuinely flipped back while the app was down |
| Wrong feed in alert text | `src=` tells you: `tv`=futures, `yahoo`=spot proxy, `kite`=your Zerodha data |

## 9. Research tools (offline EV checks — `tools/`)

Separate from the bot: they measure whether the playbook is +EV on
historical candles (yfinance), then stay out of the runtime. Run from
`nse_alerter/`; the first run downloads data and caches it 24h in
`tools/_data/` (gitignored, no `.env` needed).

| Run | Answers | Basis |
|---|---|---|
| `python tools/vix_strangle_sweep.py` | Strangle slot grid — dist × stop × VIX × lots per weekday slot; its result fixed the card-v5 rotation (every weekday an entry day, −0.5C / −1.0C stops) | daily OHLC × 3y, session-time T |
| `python tools/envelope_ev.py` | Envelope pilot EV/day on real paths, card rules verbatim — zero-trade days counted, so the number has no survivorship bias | 5m × 60d, NIFTY + crude |
| `python tools/tue_5m_check.py` | Cross-checks the daily-grid verdict on **real 5m paths** where the intrabar stop order is visible: daily bars overstate stops (17–34% vs 5–9% measured — the daily model charges pre-09:45 extremes a stop that doesn't exist yet), and the 0.50% dist candidate stays **positive at all three distances** (worst-day ≈ −₹41k per entry at 10 lots — still your call) | 5m × 12 same Tuesdays, same IV ladder 11–14 |
| `python tools/crude_ema_filter.py` | Tests the HTF EMA suggestion (only take crude envelope entries with hourly EMA20>50): **does not hold** — the gate refused a +EV stream and flipped the pilot negative. Side finding: crude's pilot EV is window-unstable (₹4,598→Rs7/day gross when the 60d window rolled — net negative after 0.5pt cost) | 5m × 60d, gate on completed 1h bars |

Findings feed decisions, not code: rotation/tuning changes land in
`nse_alerts/expiry.py` (the pricing kernels) only when you say go.

## 10. Roadmap / extension points

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

