# Market Dashboard — Design Spec
**Date:** 2026-03-20
**Status:** Approved

---

## 1. Overview

A locally-hosted always-on market monitoring dashboard built with FastAPI + HTMX. Runs during market hours, refreshes skill signals automatically, displays live TradingView charts at zero API cost, and integrates Alpaca for live portfolio data and optional trade execution.

**Location:** `examples/market-dashboard/` (alongside existing `daily-market-dashboard`)
**Start command:** `uvicorn main:app`

---

## 2. Architecture & Data Flow

```
Browser (HTMX)
    │
    ├── TradingView iframe ──────────────────► TradingView CDN (live prices, free)
    │
    ├── GET /                ────────────────► FastAPI → Jinja2 → dashboard.html
    ├── GET /api/signals     (HTMX, 30s) ───► reads cache/*.json → HTML fragment
    ├── GET /api/portfolio   (HTMX, 5s)  ───► reads Alpaca state → HTML fragment
    └── GET /detail/<skill>  (on click)  ───► reads cache/<skill>.json → detail page

FastAPI (main.py)
    ├── APScheduler ─────────────────────────► runs skill scripts on cadence → cache/*.json
    ├── AlpacaClient ────────────────────────► REST polling (GET /account, GET /positions) → in-memory portfolio state
    │                                          Trading stream WebSocket → order fill notifications
    └── PivotWatchlistMonitor ───────────────► Alpaca data WebSocket → subscribes to VCP candidate symbols
                                               fires order when price crosses pivot (Auto mode only)
```

**Key principles:**
- Skills run as subprocesses — no refactoring of existing skill scripts required
- API keys (`FMP_API_KEY`, `FINVIZ_API_KEY`, `ANTHROPIC_API_KEY`) are injected into subprocess environments at launch from the dashboard's loaded `.env`
- Cache is JSON files on disk — simple, inspectable, survives restarts
- HTMX polls two endpoints: signals (30s) and portfolio (5s) — no full page reloads
- TradingView handles all live price rendering — zero FMP API cost for charts
- Stale cache (skill failed or older than 2× its scheduled cadence) shows a warning badge; previous data remains visible

---

## 3. Directory Structure

```
examples/market-dashboard/
├── main.py                  # FastAPI app, routes, startup
├── scheduler.py             # APScheduler — skill cadence background jobs
├── skills_runner.py         # Subprocess runner + JSON cache writer
├── alpaca_client.py         # Two Alpaca clients: TradingClient (portfolio REST + trading stream WebSocket + order placement) and StockHistoricalDataClient (last-trade price lookup at order execution time)
├── pivot_monitor.py         # PivotWatchlistMonitor: loads VCP candidates at open, subscribes to Alpaca data WebSocket, fires bracket order when price crosses pivot (Auto mode only)
├── config.py                # .env loading, constants, skill schedule config
│
├── templates/
│   ├── base.html            # Layout A shell: ticker tape, nav, 3-column grid
│   ├── dashboard.html       # Main view: chart + signals panel + bottom row
│   ├── fragments/
│   │   ├── signals.html     # HTMX fragment: right-side signal panel (7 skills)
│   │   └── portfolio.html   # HTMX fragment: portfolio P&L strip
│   └── detail/
│       ├── ftd.html
│       ├── vcp.html
│       ├── breadth.html
│       ├── uptrend.html
│       ├── market_top.html
│       ├── macro_regime.html
│       ├── themes.html
│       ├── exposure.html
│       ├── economic_cal.html
│       ├── earnings_cal.html
│       └── news.html              # Pre-market: Market News Analyst full output
│
├── static/
│   └── style.css            # Dark theme, Layout A styles
│
├── cache/                   # Skill JSON outputs (auto-created on startup)
├── settings.json            # Runtime settings (mode, risk limits) — auto-created
│
├── .env.example
├── requirements.txt
└── CLAUDE.md                # See Section 12 for required content
```

---

## 4. UI Layout — Layout A (Command Center)

### Top bar
- App name + status indicator — three states: `Pre-Market` (7:00–9:30 AM ET) / `Market Open` (9:30 AM–4:00 PM ET) / `Market Closed`
- Live index prices (SPY, QQQ, VIX) — updated via TradingView ticker tape
- **Trading mode badge** (e.g. "✅ Semi-Auto") — click to open settings modal

### Ticker tape
- TradingView Ticker Tape widget — free, live, no API key required

### Main 2-column grid
- **Left (2/3 width):** TradingView Advanced Chart widget — full interactive, switchable symbol
- **Right (1/3 width):** Signal panel — 7 skill signals with color-coded status, HTMX auto-refresh every 30s

### Signal panel — 7 skills displayed
| Signal | Drill-down page | Refresh cadence |
|---|---|---|
| FTD Detector | `/detail/ftd` | 15 min |
| Uptrend Analyzer | `/detail/uptrend` | 15 min |
| Market Breadth | `/detail/breadth` | 15 min |
| VCP Screener | `/detail/vcp` | 15 min |
| Market Top Detector | `/detail/market_top` | 60 min |
| Macro Regime | `/detail/macro_regime` | 60 min |
| Exposure Coach | `/detail/exposure` | 30 min |

The remaining skills (Theme Detector, Economic Calendar, Earnings Calendar) appear in the **bottom strip only** — not in the signal panel.

### Bottom strip — layout adapts by market state

**During Market Hours (9:30 AM–4:00 PM ET) — 3 columns:**
- **Portfolio:** Live P&L from Alpaca (value, daily gain/loss, position list)
- **Top Themes:** Top 3 bullish themes from Theme Detector — links to `/detail/themes`
- **Today's Events:** High-impact economic events + earnings — links to `/detail/economic_cal` and `/detail/earnings_cal`

**Pre-Market (7:00–9:30 AM ET) — 2 columns (wider):**
- **Pre-Market Brief:** Market News Analyst summary — top 3 overnight news items with market impact assessment — links to `/detail/news`
- **Today's Schedule:** Economic events + earnings reporting today (time, impact level, consensus) — links to `/detail/economic_cal` and `/detail/earnings_cal`

The portfolio strip is hidden pre-market (no live P&L until open). The news panel replaces it.

---

## 5. Trading Mode Selector

Accessible via the mode badge in the header — opens a settings modal on click.

### Modes
| Mode | Behaviour |
|---|---|
| **Level 1 — Advisory** | View signals only. No Execute buttons shown anywhere. |
| **Level 2 — Semi-Auto** | Execute buttons appear on drill-down pages. Every order requires explicit confirmation in the order preview. |
| **Level 3 — Auto** | AI places trades automatically when signals trigger. A status banner is shown prominently at the top. |

### Settings modal contents
- Mode selector (radio buttons)
- Default risk per trade (%)
- Max open positions
- Max position size (% of account)
- Warning confirmation required when switching to Level 3

### Level 3 Auto — Trade Trigger Logic

Trades in Auto mode are triggered by **price breakout above the VCP pivot**, not at signal detection time. This is the correct way to trade VCP patterns.

**Flow:**
1. VCP Screener runs once at 9:30 AM open → produces candidates with pivot prices
2. `PivotWatchlistMonitor` loads candidates from `cache/vcp-screener.json`
3. Monitor subscribes to the candidate symbols via **Alpaca data WebSocket** (free, real-time)
4. When a symbol's price crosses `pivot × 1.001` (0.1% buffer to avoid false triggers), the monitor fires automatically:
   - Fetches last-trade price via `StockHistoricalDataClient`
   - Calculates position size using default risk % and stop from VCP output
   - Places bracket order via `TradingClient` (entry limit + stop-loss, atomically)
5. Order fill notification arrives via Alpaca trading stream → dashboard updates portfolio panel
6. Monitor unsubscribes from that symbol after order is placed

**Guard rails in Auto mode:**
- Max positions check before firing (won't exceed `MAX_POSITIONS` from settings)
- Max position size check (won't exceed `MAX_POSITION_SIZE_PCT` of account)
- Only fires during market hours (9:30 AM–4:00 PM ET) — monitor is inactive pre-market and after close
- If market is in a "Caution" or worse state per Market Top Detector, Auto mode pauses new entries and alerts the user

### Settings persistence
Mode and risk settings are written to `settings.json` in the project directory (not `.env`). The app reads `settings.json` on startup; if it does not exist, defaults are used and the file is created. `.env` holds secrets and initial defaults only and is never modified at runtime.

---

## 6. Drill-Down Pages

Each signal in the right panel links to `/detail/<skill-name>`.

### Structure (consistent across all skills)
1. **Back nav** — "← Dashboard" breadcrumb
2. **Summary strip** — 3–4 key metrics for that skill (e.g. candidate count, avg score, near-pivot count)
3. **Full data table** — complete skill output with sortable columns
4. **Execute buttons** — visible only in Semi-Auto and Auto modes; triggers order preview
5. **Refresh now** button — re-runs the skill on demand
6. **Generated at** timestamp + stale warning if cache is older than 2× this skill's cadence (see Section 7)

### Order Preview (Semi-Auto / Auto)
Appears inline below the selected row when Execute is clicked.

**Three linked control buttons:**
- Risk % button
- Shares button
- Dollar Amount button

Clicking any button activates it (highlighted border + arrow indicator) and reveals a slider below. Dragging the slider updates the active field; the other two recalculate automatically. +/− buttons allow one-step fine-tuning.

**Override scope:** Per-trade only. Default risk % in settings is unchanged.

### Order execution
- **Limit price:** Last traded price at the moment Execute is clicked (fetched from Alpaca REST `GET /v2/stocks/{symbol}/trades/latest`)
- **Order type:** Bracket order (`order_class="bracket"`) via `alpaca-py` — entry limit + stop-loss are submitted atomically, ensuring the stop is always attached even if the network drops after the entry fills
- **Stop price:** Pre-calculated by the Position Sizer skill output; user can adjust via the Shares slider which recalculates stop distance at the original risk %

---

## 7. Skill Schedule & Caching

### Cadence & FMP call estimates

**Pre-market window (Mon–Fri 7:00–9:30 AM ET):**
| Cadence | Skills | Notes |
|---|---|---|
| Once at 7:00 AM | Market News Analyst | WebSearch/WebFetch only — no FMP calls |
| Once at 7:00 AM | Macro Regime Detector | Structural context for the day |
| Once at 7:00 AM | Market Top Detector | Risk posture before open |
| Once at 7:00 AM | Sector Analyst | Which sectors likely to lead/lag |
| Once at 7:00 AM | Theme Detector | Active themes to watch |
| Already runs at 6:00 AM | Economic Calendar, Earnings Calendar | No change needed |

Pre-market runs are one-shot at 7:00 AM — not repeated during the 7:00–9:30 window. Results remain in cache until the market-hours scheduler takes over.

**Market hours (Mon–Fri 9:30 AM–4:00 PM ET):**
| Cadence | Skills | FMP calls/day | Notes |
|---|---|---|---|
| Once at 9:30 AM open | VCP Screener | ~20–50 | Daily candles don't change intraday — refreshing more often gives identical results |
| Every 30 min | FTD Detector | ~26–52 | Tracks intraday volume pace; 30 min granularity is sufficient |
| Every 30 min | Uptrend Analyzer, Market Breadth | 0 | CSV-based, no FMP |
| Every 30 min | Sector Analyst → Exposure Coach, Theme Detector | 0 | CSV/FINVIZ/WebSearch only |
| Every 60 min | Market Top Detector, Macro Regime Detector | 0 | CSV/WebFetch only |
| Daily 6:00 AM | Economic Calendar, Earnings Calendar | ~10 | Week-ahead fetch |

**Estimated FMP usage: ~56–112 calls/day — within the free tier (250/day). Total data cost = $0.**

What is given up vs. a paid cadence:
- VCP: nothing in practice — daily candles finalize at market close; intraday re-runs returned identical results
- FTD: very minor — 30 min vs 15 min volume tracking granularity; fully sufficient for human-reviewed signals

### Cache behaviour
- Skill scripts write timestamped output filenames (e.g. `ftd_detector_2026-03-20_143022.json`). After a successful subprocess run, `skills_runner.py` renames the skill's timestamped output file to `cache/<skill-name>.json`, overwriting the previous version. The staleness timestamp is read from the `generated_at` field inside the JSON, not the file modification time.
- Failed runs keep the previous `cache/<skill-name>.json` file; dashboard shows a stale badge
- Skills only run Mon–Fri during market hours (9:30 AM–4:00 PM ET) + 6 AM daily jobs
- On startup: any cache file older than 2× its cadence triggers an immediate background refresh

### Subprocess error handling
- **Failure detection:** Non-zero exit code from the skill subprocess → run marked as failed
- **Stderr capture:** Stderr output is written to `cache/<skill-name>.stderr.log` (overwritten each run) for debugging
- **Timeout:** Each skill subprocess has a 120-second hard timeout (configurable via `config.py`); timeout is treated as a failure
- **Retry policy:** No automatic retry. Failed runs log to stderr and show a stale badge. This prevents cascading API calls on repeated failures
- **Stale threshold:** Cache files older than 2× the skill's cadence are considered stale and trigger a badge

### Skill dependencies
Sector Analyst output is not displayed directly in the UI. Its `cache/sector-analyst.json` is passed as input when Exposure Coach runs. Within the 30-min scheduler group, the execution order must be: **Sector Analyst first, then Exposure Coach** (and Theme Detector independently). `scheduler.py` must enforce this ordering within the group.

---

## 8. Data Sources

| Source | Usage | Cost |
|---|---|---|
| TradingView widgets | Live charts, ticker tape, market overview | Free (no account needed) |
| Alpaca REST API | Portfolio P&L, positions (`GET /account`, `GET /positions`), order execution | Free (paper or live account) |
| Alpaca Trading Stream | Order fill notifications (WebSocket) | Free |
| FMP API | VCP + FTD skill data, economic + earnings calendars | Free tier (250/day) — ~56–112 calls/day |
| FINVIZ Elite | Theme Detector pre-screening | Existing subscription |
| yfinance | Earnings calendar fallback (no FMP needed) | Free |
| **Total ongoing cost** | | **$0** |

---

## 9. Configuration (.env)

`.env` holds secrets and initial defaults only. It is never modified at runtime. Runtime settings changes (mode, risk limits) are written to `settings.json`.

```env
# Required
ALPACA_API_KEY=...
ALPACA_SECRET_KEY=...
ALPACA_PAPER=true                  # true = paper trading endpoint
ALPACA_BASE_URL=https://paper-api.alpaca.markets   # omit for live trading

# Optional — injected into skill subprocess environments at launch
FMP_API_KEY=...
FINVIZ_API_KEY=...
ANTHROPIC_API_KEY=...              # required only if any skill internally uses Claude

# Dashboard settings (initial defaults — overridden by settings.json after first save)
TRADING_MODE=advisory              # advisory | semi_auto | auto
DEFAULT_RISK_PCT=1.0
MAX_POSITIONS=5
MAX_POSITION_SIZE_PCT=10
APP_PORT=8000
```

---

## 10. Dependencies

```
fastapi
uvicorn[standard]
jinja2
httpx
apscheduler
alpaca-py
python-dotenv
```

No Node.js or npm required. No frontend build step.

---

## 11. Out of Scope (v1)

- Mobile / responsive layout
- Multi-user / authentication
- Persistent trade history database (orders visible in Alpaca dashboard)
- Backtesting integration in the UI
- Push notifications / alerts

---

## 12. CLAUDE.md — Required Content

The dashboard's `CLAUDE.md` must cover:

1. **What this project is** — always-on FastAPI + HTMX market dashboard
2. **How to start:** `uvicorn main:app` (add `--port $APP_PORT` if changed from default 8000). Use `--reload` during development only — it restarts the server on every cache file write in production.
3. **Environment setup:** Copy `.env.example` to `.env`, fill in API keys
4. **How skills are invoked:** Skills run as subprocesses via `skills_runner.py`. The runner injects all API keys from the loaded `.env` into the subprocess environment. Skill scripts are located via `--project-root` pointing to the `claude-trading-skills` root
5. **Cache directory:** `cache/` is auto-created; delete a `.json` file to force a skill re-run on next scheduler tick
6. **Settings:** Runtime mode/risk settings are stored in `settings.json` (auto-created); delete to reset to `.env` defaults
7. **TDD requirement:** Follow the repo-wide TDD-first workflow for any new code
