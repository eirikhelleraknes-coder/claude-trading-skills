# Alpaca Data Migration Design — VCP & CANSLIM Screeners

## Goal

Migrate VCP and CANSLIM screeners off FMP for market data (historical bars + quotes), replacing with Alpaca's free data API. This eliminates FMP usage for VCP entirely and significantly reduces it for CANSLIM.

## Problem

The VCP screener makes ~600 FMP calls per run, far exceeding the 250/day free tier. CANSLIM makes ~283 calls for 40 stocks. Combined with the universe builder's ~42 nightly calls, neither screener can reliably run on the free tier.

## Solution

Add `AlpacaDataClient` to each screener. Alpaca provides historical OHLCV bars and snapshot quotes with no hard daily call limit on the free tier.

- **VCP screener**: full replacement — `alpaca_data_client.py` replaces `fmp_client.py` entirely. Zero FMP calls.
- **CANSLIM screener**: partial replacement — `alpaca_data_client.py` handles price/historical data; `fmp_client.py` stays for fundamental data (income statements, institutional holders, company profile). Reduces CANSLIM FMP usage from ~283 to ~123 calls/day.

---

## Architecture

### New Files

**`skills/vcp-screener/scripts/alpaca_data_client.py`**
- Class: `AlpacaDataClient`
- Methods:
  - `get_historical_prices(symbol: str, days: int = 365)` → `{"symbol": str, "historical": [{"date", "open", "high", "low", "close", "volume"}]}` (most-recent-first)
  - `get_batch_historical(symbols: list[str], days: int = 260)` → `{symbol: historical_list}`
  - `get_quote(symbol: str)` → `[{"symbol", "price", "yearHigh", "yearLow", "volume", "avgVolume"}]` (list with one entry, matching FMP signature)
  - `get_batch_quotes(symbols: list[str])` → `{symbol: quote_dict}`
  - `get_api_stats()` → `{"cache_entries": int, "api_calls_made": int}` (for screener reporting)
  - `get_sp500_constituents()` → not needed (VCP uses `--universe` flag); raises `NotImplementedError`
- Reads `ALPACA_API_KEY` and `ALPACA_SECRET_KEY` from environment
- Uses `alpaca.data.historical.StockHistoricalDataClient`
- Uses `StockBarsRequest` (TimeFrame.Day) for historical bars
- Uses `StockLatestQuoteRequest` for current price (more reliable than snapshots for equities)
- `yearHigh` and `yearLow` computed from 365-day bar data (cached per symbol to avoid double-fetching)
- `avgVolume` computed as mean of last 30 trading days' volume from bar data
- If Alpaca returns no data for a symbol, returns empty result; caller skips it silently

**`skills/canslim-screener/scripts/alpaca_data_client.py`**
- Same class and methods as VCP version
- Additionally includes `calculate_ema(prices: list[float], period: int) → float` (used by CANSLIM's leadership calculator)
- Index symbols (`^GSPC`, `^VIX`) are not supported by Alpaca — `get_quote("^GSPC")` and `get_quote("^VIX")` fall back to yfinance (already available as a dependency)

### Modified Files

**`skills/vcp-screener/scripts/screen_vcp.py`**
- Change import: `from fmp_client import FMPClient` → `from alpaca_data_client import AlpacaDataClient`
- Change instantiation: `FMPClient(api_key=...)` → `AlpacaDataClient()`
- All method calls unchanged (identical interface)

**`skills/canslim-screener/scripts/screen_canslim.py`**
- Add import: `from alpaca_data_client import AlpacaDataClient`
- Keep import: `from fmp_client import FMPClient`
- Replace `get_quote()` and `get_historical_prices()` calls with `AlpacaDataClient`
- Keep income statement, institutional holder, and profile calls on `FMPClient`

---

## Data Flow

### VCP Screener (after migration)
```
screen_vcp.py
  → AlpacaDataClient.get_batch_quotes(symbols)       # Alpaca latest quotes
  → AlpacaDataClient.get_historical_prices("SPY")    # Alpaca bars (260 days)
  → AlpacaDataClient.get_batch_historical(symbols)   # Alpaca bars (260 days)
  → [zero FMP calls]
```

### CANSLIM Screener (after migration)
```
screen_canslim.py
  → AlpacaDataClient.get_quote("^GSPC")              # yfinance fallback (index)
  → AlpacaDataClient.get_quote("^VIX")               # yfinance fallback (index)
  → AlpacaDataClient.get_quote(symbol)               # Alpaca latest quote (equities)
  → AlpacaDataClient.get_historical_prices(symbol)   # Alpaca bars
  → FMPClient.get_income_statement(symbol)           # FMP (kept)
  → FMPClient.get_institutional_holders(symbol)      # FMP (kept)
  → FMPClient.get_profile(symbol)                    # FMP (kept)
```

---

## API Usage After Migration

| Component | FMP calls/day | Alpaca calls/day |
|-----------|--------------|-----------------|
| Universe builder nightly batch | ~42 | 0 |
| VCP screener (50–100 stocks) | 0 | ~100–200 |
| CANSLIM screener (40 stocks + 3 market symbols) | ~123 | ~83 |
| **Total FMP** | **~165** | — |
| **vs. 250 free tier limit** | **✅ within** | — |

---

## Return Format Compatibility

Both clients return data in FMP-compatible format so screener logic is unchanged:

```python
# Historical prices (most-recent-first)
{
    "symbol": "AAPL",
    "historical": [
        {"date": "2026-03-24", "open": 170.0, "high": 172.0, "low": 169.0, "close": 171.5, "volume": 45000000},
        ...
    ]
}

# Quote (get_quote returns a list; get_batch_quotes returns a dict keyed by symbol)
[{
    "symbol": "AAPL",
    "price": 171.5,
    "yearHigh": 180.0,   # max(close) over last 365 days from bar cache
    "yearLow": 140.0,    # min(close) over last 365 days from bar cache
    "volume": 45000000,  # today's volume from latest bar
    "avgVolume": 48000000,  # mean volume over last 30 bars
}]
```

---

## Error Handling

- Missing `ALPACA_API_KEY` or `ALPACA_SECRET_KEY` → raise `EnvironmentError` with clear message at client instantiation
- Symbol not found or delisted → return empty list/dict for that symbol; caller skips it
- Index symbols (`^GSPC`, `^VIX`) → fall back to yfinance automatically; log to stderr if yfinance also fails
- Alpaca rate limit → `alpaca-py` SDK handles retries automatically
- Network error → log to stderr, return empty result for affected symbols

---

## Out of Scope

- No changes to the universe builder (already Alpaca-free on the FMP side)
- No changes to the dashboard UI or scheduler
- No migration of CANSLIM's fundamental data (income statements, institutional holders) — FMP stays for these
- No other skills beyond VCP and CANSLIM
