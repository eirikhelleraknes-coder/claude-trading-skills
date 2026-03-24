# Alpaca Data Migration — VCP & CANSLIM Screeners Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace FMP market data calls in the VCP and CANSLIM screeners with Alpaca, eliminating VCP's FMP dependency entirely and reducing CANSLIM's from ~283 to ~123 calls/day.

**Architecture:** Two new `AlpacaDataClient` classes (one per screener) implement the same interface as the existing `FMPClient` — identical method signatures and return formats — so the screener logic needs only an import swap. Quote fields (`yearHigh`, `yearLow`, `avgVolume`) are computed from cached bar data to avoid extra API calls. Index symbols (`^GSPC`, `^VIX`) fall back to yfinance since Alpaca only serves equities.

**Tech Stack:** Python, `alpaca-py` (`alpaca.data.historical.StockHistoricalDataClient`, `StockBarsRequest`), `yfinance` (index fallback), existing `fmp_client.py` (kept for CANSLIM fundamentals only).

---

## File Map

| File | Action | Purpose |
|------|--------|---------|
| `skills/vcp-screener/scripts/alpaca_data_client.py` | Create | AlpacaDataClient for VCP — historical bars + quotes |
| `skills/vcp-screener/scripts/screen_vcp.py` | Modify (2 lines) | Swap import + instantiation |
| `skills/vcp-screener/scripts/tests/test_alpaca_data_client.py` | Create | Tests for VCP AlpacaDataClient |
| `skills/canslim-screener/scripts/alpaca_data_client.py` | Create | AlpacaDataClient for CANSLIM — same base + index fallback |
| `skills/canslim-screener/scripts/screen_canslim.py` | Modify (~10 lines) | Add AlpacaDataClient for price/historical calls |
| `skills/canslim-screener/scripts/tests/test_alpaca_data_client.py` | Create | Tests for CANSLIM AlpacaDataClient |

---

### Task 1: AlpacaDataClient for VCP screener

**Files:**
- Create: `skills/vcp-screener/scripts/alpaca_data_client.py`
- Create: `skills/vcp-screener/scripts/tests/test_alpaca_data_client.py`

**Context:**
Follow the pattern from `skills/macro-regime-detector/scripts/fmp_client.py` — it already uses `StockHistoricalDataClient` + `StockBarsRequest`. The existing `fmp_client.py` in the VCP skill has `_yf_historical()` and `_yf_quote()` module-level functions you can copy verbatim for the index fallback.

The `get_quote()` and `get_batch_quotes()` methods do NOT make a separate Alpaca quote API call. Instead they call `get_historical_prices()` internally and compute quote fields from the cached bar data:
- `price` = `historical[0]['close']`
- `yearHigh` = `max(b['high'] for b in historical[:252])`
- `yearLow` = `min(b['low'] for b in historical[:252])`
- `volume` = `historical[0]['volume']`
- `avgVolume` = `mean(b['volume'] for b in historical[:30])`

- [ ] **Step 1: Write the failing tests**

```python
# skills/vcp-screener/scripts/tests/test_alpaca_data_client.py

import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from unittest.mock import MagicMock, patch
import pytest


def _make_bars_df(symbol="AAPL", n=260, start_price=150.0):
    """Build a minimal mock bars DataFrame."""
    import pandas as pd
    import numpy as np
    dates = pd.date_range(end="2026-03-24", periods=n, freq="B")
    closes = [start_price + i * 0.1 for i in range(n)]
    df = pd.DataFrame({
        "open": closes, "high": [c + 1 for c in closes],
        "low": [c - 1 for c in closes], "close": closes,
        "volume": [1_000_000] * n,
    }, index=pd.MultiIndex.from_tuples(
        [(sym, d) for sym, d in zip([symbol] * n, dates)],
        names=["symbol", "timestamp"],
    ))
    return df


def test_get_historical_prices_returns_fmp_format():
    """get_historical_prices returns FMP-compatible dict (most-recent-first)."""
    from alpaca_data_client import AlpacaDataClient

    mock_bars = MagicMock()
    mock_bars.df = _make_bars_df("AAPL", n=260)

    with patch("alpaca_data_client.StockHistoricalDataClient") as MockClient:
        MockClient.return_value.get_stock_bars.return_value = mock_bars
        client = AlpacaDataClient()
        result = client.get_historical_prices("AAPL", days=260)

    assert result["symbol"] == "AAPL"
    assert len(result["historical"]) == 260
    bar = result["historical"][0]
    assert all(k in bar for k in ("date", "open", "high", "low", "close", "volume"))
    # Most-recent-first: first bar date > last bar date
    assert result["historical"][0]["date"] > result["historical"][-1]["date"]


def test_get_batch_historical_returns_dict_keyed_by_symbol():
    """get_batch_historical returns {symbol: [bars]} for all requested symbols."""
    from alpaca_data_client import AlpacaDataClient
    import pandas as pd

    dates = pd.date_range(end="2026-03-24", periods=10, freq="B")
    rows = []
    for sym in ["AAPL", "MSFT"]:
        for d in dates:
            rows.append({
                "symbol": sym, "timestamp": d,
                "open": 100.0, "high": 101.0, "low": 99.0, "close": 100.5,
                "volume": 500_000,
            })
    df = pd.DataFrame(rows).set_index(["symbol", "timestamp"])

    mock_bars = MagicMock()
    mock_bars.df = df

    with patch("alpaca_data_client.StockHistoricalDataClient") as MockClient:
        MockClient.return_value.get_stock_bars.return_value = mock_bars
        client = AlpacaDataClient()
        result = client.get_batch_historical(["AAPL", "MSFT"], days=10)

    assert set(result.keys()) == {"AAPL", "MSFT"}
    assert len(result["AAPL"]) == 10


def test_get_quote_returns_list_with_computed_fields():
    """get_quote returns a list with one dict containing price/yearHigh/yearLow/volume/avgVolume."""
    from alpaca_data_client import AlpacaDataClient

    mock_bars = MagicMock()
    mock_bars.df = _make_bars_df("AAPL", n=260, start_price=100.0)

    with patch("alpaca_data_client.StockHistoricalDataClient") as MockClient:
        MockClient.return_value.get_stock_bars.return_value = mock_bars
        client = AlpacaDataClient()
        result = client.get_quote("AAPL")

    assert isinstance(result, list)
    assert len(result) == 1
    q = result[0]
    assert q["symbol"] == "AAPL"
    assert "price" in q
    assert "yearHigh" in q
    assert "yearLow" in q
    assert "volume" in q
    assert "avgVolume" in q
    assert q["yearHigh"] >= q["yearLow"]


def test_get_batch_quotes_returns_dict_keyed_by_symbol():
    """get_batch_quotes returns {symbol: quote_dict}."""
    from alpaca_data_client import AlpacaDataClient

    mock_bars = MagicMock()
    mock_bars.df = _make_bars_df("AAPL", n=260)

    with patch("alpaca_data_client.StockHistoricalDataClient") as MockClient:
        MockClient.return_value.get_stock_bars.return_value = mock_bars
        client = AlpacaDataClient()
        result = client.get_batch_quotes(["AAPL"])

    assert "AAPL" in result
    assert "price" in result["AAPL"]


def test_get_api_stats_returns_counts():
    """get_api_stats returns dict with api_calls_made and cache_entries."""
    from alpaca_data_client import AlpacaDataClient

    mock_bars = MagicMock()
    mock_bars.df = _make_bars_df("AAPL", n=10)

    with patch("alpaca_data_client.StockHistoricalDataClient") as MockClient:
        MockClient.return_value.get_stock_bars.return_value = mock_bars
        client = AlpacaDataClient()
        client.get_historical_prices("AAPL", days=10)
        stats = client.get_api_stats()

    assert "api_calls_made" in stats
    assert "cache_entries" in stats
    assert stats["api_calls_made"] >= 1


def test_missing_api_keys_raises():
    """AlpacaDataClient raises EnvironmentError if keys missing."""
    import os
    from alpaca_data_client import AlpacaDataClient

    with patch.dict(os.environ, {"ALPACA_API_KEY": "", "ALPACA_SECRET_KEY": ""}):
        with patch("alpaca_data_client.StockHistoricalDataClient"):
            with pytest.raises(EnvironmentError):
                AlpacaDataClient()
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
cd skills/vcp-screener/scripts
uv run pytest tests/test_alpaca_data_client.py -v
```
Expected: ImportError or FAIL (alpaca_data_client doesn't exist yet)

- [ ] **Step 3: Implement `alpaca_data_client.py`**

```python
# skills/vcp-screener/scripts/alpaca_data_client.py
#!/usr/bin/env python3
"""
Alpaca Data Client for VCP Screener

Drop-in replacement for FMPClient — same method signatures and return formats.
Uses Alpaca Market Data API for historical bars. Quote fields (yearHigh, yearLow,
avgVolume) are computed from cached bar data to avoid extra API calls.
"""

import datetime
import os
import sys
from statistics import mean
from typing import Optional

from alpaca.data.historical import StockHistoricalDataClient
from alpaca.data.requests import StockBarsRequest
from alpaca.data.timeframe import TimeFrame


def _yf_historical(symbol: str, days: int) -> Optional[dict]:
    """Fetch historical prices via yfinance (fallback for index symbols)."""
    try:
        import yfinance as yf
        end = datetime.date.today()
        start = end - datetime.timedelta(days=int(days * 1.6) + 10)
        ticker = yf.Ticker(symbol)
        hist = ticker.history(start=str(start), end=str(end))
        if hist.empty:
            return None
        historical = []
        for date_idx, row in hist.iterrows():
            historical.append({
                "date": str(date_idx.date()),
                "open": round(float(row["Open"]), 4),
                "high": round(float(row["High"]), 4),
                "low": round(float(row["Low"]), 4),
                "close": round(float(row["Close"]), 4),
                "volume": int(row["Volume"]),
            })
        historical.reverse()  # Most recent first
        return {"symbol": symbol, "historical": historical[:days]}
    except Exception as e:
        print(f"WARNING: yfinance fallback failed for {symbol}: {e}", file=sys.stderr)
        return None


# Index symbols not available via Alpaca — use yfinance fallback
_INDEX_SYMBOLS = {"^GSPC", "^VIX", "^DJI", "^IXIC"}


class AlpacaDataClient:
    """Alpaca-backed data client with FMP-compatible interface."""

    def __init__(self):
        api_key = os.environ.get("ALPACA_API_KEY", "")
        secret_key = os.environ.get("ALPACA_SECRET_KEY", "")
        if not api_key or not secret_key:
            raise EnvironmentError(
                "ALPACA_API_KEY and ALPACA_SECRET_KEY environment variables are required. "
                "Set them in your .env file."
            )
        self._alpaca = StockHistoricalDataClient(
            api_key=api_key, secret_key=secret_key
        )
        self._cache: dict = {}
        self._api_calls_made = 0

    def get_historical_prices(self, symbol: str, days: int = 365) -> Optional[dict]:
        """Fetch daily OHLCV bars (most-recent-first, FMP-compatible format)."""
        if symbol in _INDEX_SYMBOLS:
            return _yf_historical(symbol, days)

        cache_key = f"hist_{symbol}_{days}"
        if cache_key in self._cache:
            return self._cache[cache_key]

        start = (
            datetime.date.today() - datetime.timedelta(days=int(days * 1.6) + 10)
        ).isoformat()
        request = StockBarsRequest(
            symbol_or_symbols=symbol,
            timeframe=TimeFrame.Day,
            start=start,
        )
        try:
            bars = self._alpaca.get_stock_bars(request)
            self._api_calls_made += 1
        except Exception as e:
            print(f"WARNING: Alpaca bars failed for {symbol}: {e}", file=sys.stderr)
            return None

        df = bars.df
        if df.empty:
            return None

        if hasattr(df.index, "levels"):
            try:
                df = df.xs(symbol, level="symbol")
            except KeyError:
                return None
        df = df.sort_index(ascending=False)

        historical = []
        for ts, row in df.iterrows():
            historical.append({
                "date": str(ts.date()) if hasattr(ts, "date") else str(ts)[:10],
                "open": round(float(row["open"]), 4),
                "high": round(float(row["high"]), 4),
                "low": round(float(row["low"]), 4),
                "close": round(float(row["close"]), 4),
                "volume": int(row["volume"]),
            })

        result = {"symbol": symbol, "historical": historical[:days]}
        self._cache[cache_key] = result
        return result

    def get_batch_historical(
        self, symbols: list[str], days: int = 260
    ) -> dict[str, list[dict]]:
        """Fetch bars for multiple symbols in one Alpaca request."""
        equity_symbols = [s for s in symbols if s not in _INDEX_SYMBOLS]
        index_symbols = [s for s in symbols if s in _INDEX_SYMBOLS]

        cache_key = f"batch_{','.join(sorted(equity_symbols))}_{days}"
        if cache_key in self._cache:
            results = dict(self._cache[cache_key])
        else:
            results = {}
            if equity_symbols:
                start = (
                    datetime.date.today() - datetime.timedelta(days=int(days * 1.6) + 10)
                ).isoformat()
                request = StockBarsRequest(
                    symbol_or_symbols=equity_symbols,
                    timeframe=TimeFrame.Day,
                    start=start,
                )
                try:
                    bars = self._alpaca.get_stock_bars(request)
                    self._api_calls_made += 1
                except Exception as e:
                    print(f"WARNING: Alpaca batch fetch failed: {e}", file=sys.stderr)
                    return {s: [] for s in symbols}

                df = bars.df
                if not df.empty:
                    for sym in equity_symbols:
                        try:
                            sym_df = df.xs(sym, level="symbol").sort_index(ascending=False)
                            historical = []
                            for ts, row in sym_df.iterrows():
                                historical.append({
                                    "date": str(ts.date()) if hasattr(ts, "date") else str(ts)[:10],
                                    "open": round(float(row["open"]), 4),
                                    "high": round(float(row["high"]), 4),
                                    "low": round(float(row["low"]), 4),
                                    "close": round(float(row["close"]), 4),
                                    "volume": int(row["volume"]),
                                })
                            results[sym] = historical[:days]
                        except (KeyError, IndexError):
                            results[sym] = []
                self._cache[cache_key] = dict(results)

        # Handle index symbols via yfinance
        for sym in index_symbols:
            data = _yf_historical(sym, days)
            results[sym] = data["historical"] if data else []

        return results

    def get_quote(self, symbol: str) -> list[dict]:
        """Return a one-element list with quote fields computed from bar data."""
        data = self.get_historical_prices(symbol, days=365)
        if not data or not data.get("historical"):
            return []
        bars = data["historical"]
        closes = [b["close"] for b in bars[:252]]
        volumes = [b["volume"] for b in bars[:30]]
        return [{
            "symbol": symbol,
            "price": bars[0]["close"],
            "yearHigh": max(b["high"] for b in bars[:252]),
            "yearLow": min(b["low"] for b in bars[:252]),
            "volume": bars[0]["volume"],
            "avgVolume": int(mean(volumes)) if volumes else 0,
        }]

    def get_batch_quotes(self, symbols: list[str]) -> dict[str, dict]:
        """Return {symbol: quote_dict} for multiple symbols."""
        result = {}
        for sym in symbols:
            quotes = self.get_quote(sym)
            if quotes:
                result[sym] = quotes[0]
        return result

    def get_api_stats(self) -> dict:
        """Return call count and cache size for reporting."""
        return {
            "api_calls_made": self._api_calls_made,
            "cache_entries": len(self._cache),
        }

    def get_sp500_constituents(self):
        raise NotImplementedError(
            "Use --universe flag to pass symbols directly. "
            "S&P 500 constituent list is not available via Alpaca."
        )
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
cd skills/vcp-screener/scripts
uv run pytest tests/test_alpaca_data_client.py -v
```
Expected: 6 PASS

- [ ] **Step 5: Commit**

```bash
git add skills/vcp-screener/scripts/alpaca_data_client.py skills/vcp-screener/scripts/tests/test_alpaca_data_client.py
git commit -m "feat: add AlpacaDataClient for VCP screener — historical bars + computed quotes"
```

---

### Task 2: Wire VCP screener to use AlpacaDataClient

**Files:**
- Modify: `skills/vcp-screener/scripts/screen_vcp.py:42` (import line)
- Modify: `skills/vcp-screener/scripts/screen_vcp.py:495` (instantiation line)

**Context:**
Only two lines in `screen_vcp.py` reference `FMPClient`: line 42 (import) and line 495 (instantiation `client = FMPClient(api_key=args.api_key)`). `AlpacaDataClient()` takes no constructor arguments — it reads keys from environment directly.

- [ ] **Step 1: Write the failing test**

```python
# Add to skills/vcp-screener/scripts/tests/test_vcp_screener.py

def test_screen_vcp_uses_alpaca_data_client():
    """screen_vcp imports AlpacaDataClient, not FMPClient."""
    import importlib, importlib.util
    spec = importlib.util.spec_from_file_location(
        "screen_vcp",
        os.path.join(os.path.dirname(__file__), "..", "screen_vcp.py"),
    )
    mod = importlib.util.module_from_spec(spec)
    # Just check the source — don't execute
    import inspect
    src = open(spec.origin).read()
    assert "AlpacaDataClient" in src, "screen_vcp.py must import AlpacaDataClient"
    assert "FMPClient" not in src, "screen_vcp.py must not import FMPClient"
```

- [ ] **Step 2: Run test to verify it fails**

```bash
cd skills/vcp-screener/scripts
uv run pytest tests/test_vcp_screener.py::test_screen_vcp_uses_alpaca_data_client -v
```
Expected: FAIL (FMPClient still referenced)

- [ ] **Step 3: Update `screen_vcp.py`**

Change line 42:
```python
# Before:
from fmp_client import FMPClient
# After:
from alpaca_data_client import AlpacaDataClient
```

Change line 495 (inside the `try:` block in `main()`):
```python
# Before:
client = FMPClient(api_key=args.api_key)
print("FMP API client initialized")
# After:
client = AlpacaDataClient()
print("Alpaca data client initialized")
```

- [ ] **Step 4: Run tests**

```bash
cd skills/vcp-screener/scripts
uv run pytest tests/test_vcp_screener.py -v
```
Expected: All tests pass (existing tests use mocked client, not affected)

- [ ] **Step 5: Commit**

```bash
git add skills/vcp-screener/scripts/screen_vcp.py
git commit -m "feat: wire VCP screener to use AlpacaDataClient — zero FMP calls"
```

---

### Task 3: AlpacaDataClient for CANSLIM screener

**Files:**
- Create: `skills/canslim-screener/scripts/alpaca_data_client.py`
- Create: `skills/canslim-screener/scripts/tests/test_alpaca_data_client.py`

**Context:**
CANSLIM's `AlpacaDataClient` is identical to the VCP version with one addition: `calculate_ema()`. This method is defined on `FMPClient` in `skills/canslim-screener/scripts/fmp_client.py` (line 266) and called as `client.calculate_ema()` via `screen_canslim.py`. Add it to the CANSLIM `AlpacaDataClient` so the CANSLIM screener has one unified client for both price and EMA calculations.

The index fallback (`^GSPC`, `^VIX`) is required here since CANSLIM calls `client.get_quote("^GSPC")` and `client.get_quote("^VIX")` at lines 300-301 of `screen_canslim.py`.

- [ ] **Step 1: Write the failing tests**

```python
# skills/canslim-screener/scripts/tests/test_alpaca_data_client.py

import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from unittest.mock import MagicMock, patch
import pytest


def _make_bars_df(symbol="AAPL", n=260):
    import pandas as pd
    dates = pd.date_range(end="2026-03-24", periods=n, freq="B")
    closes = [150.0 + i * 0.1 for i in range(n)]
    df = pd.DataFrame({
        "open": closes, "high": [c + 1 for c in closes],
        "low": [c - 1 for c in closes], "close": closes,
        "volume": [1_000_000] * n,
    }, index=pd.MultiIndex.from_tuples(
        [(symbol, d) for d in dates], names=["symbol", "timestamp"]
    ))
    return df


def test_get_historical_prices_equity():
    """get_historical_prices works for equity symbols."""
    from alpaca_data_client import AlpacaDataClient

    mock_bars = MagicMock()
    mock_bars.df = _make_bars_df("AAPL", n=260)

    with patch("alpaca_data_client.StockHistoricalDataClient") as MockClient:
        MockClient.return_value.get_stock_bars.return_value = mock_bars
        client = AlpacaDataClient()
        result = client.get_historical_prices("AAPL", days=260)

    assert result["symbol"] == "AAPL"
    assert len(result["historical"]) == 260


def test_get_quote_index_symbol_uses_yfinance():
    """get_quote for ^GSPC falls back to yfinance, not Alpaca."""
    from alpaca_data_client import AlpacaDataClient

    yf_result = {
        "symbol": "^GSPC",
        "historical": [
            {"date": "2026-03-24", "open": 5000.0, "high": 5010.0,
             "low": 4990.0, "close": 5005.0, "volume": 0}
        ] * 365
    }

    with patch("alpaca_data_client.StockHistoricalDataClient"):
        with patch("alpaca_data_client._yf_historical", return_value=yf_result):
            client = AlpacaDataClient()
            result = client.get_quote("^GSPC")

    assert isinstance(result, list)
    assert len(result) == 1
    assert result[0]["symbol"] == "^GSPC"


def test_calculate_ema_returns_float():
    """calculate_ema returns a float EMA value."""
    from alpaca_data_client import AlpacaDataClient

    with patch("alpaca_data_client.StockHistoricalDataClient"):
        client = AlpacaDataClient()
        prices = [100.0 + i for i in range(60)]
        result = client.calculate_ema(prices, period=50)

    assert isinstance(result, float)
    assert result > 0


def test_calculate_ema_insufficient_data():
    """calculate_ema returns last price if fewer data points than period."""
    from alpaca_data_client import AlpacaDataClient

    with patch("alpaca_data_client.StockHistoricalDataClient"):
        client = AlpacaDataClient()
        prices = [100.0, 101.0, 102.0]
        result = client.calculate_ema(prices, period=50)

    assert result == prices[-1]
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
cd skills/canslim-screener/scripts
uv run pytest tests/test_alpaca_data_client.py -v
```
Expected: ImportError (file doesn't exist yet)

- [ ] **Step 3: Create `alpaca_data_client.py` for CANSLIM**

Copy the VCP version exactly, then add `calculate_ema()` before the closing of the class:

```python
# skills/canslim-screener/scripts/alpaca_data_client.py
# (same full content as VCP version, then add this method to AlpacaDataClient:)

    def calculate_ema(self, prices: list[float], period: int = 50) -> float:
        """Compute Exponential Moving Average. Returns last price if insufficient data."""
        if len(prices) < period:
            return prices[-1] if prices else 0.0
        k = 2.0 / (period + 1)
        ema = prices[0]
        for price in prices[1:]:
            ema = price * k + ema * (1 - k)
        return round(ema, 4)
```

The full file is identical to `skills/vcp-screener/scripts/alpaca_data_client.py` plus this one method. Do NOT import from the VCP version — keep each skill self-contained.

- [ ] **Step 4: Run tests to verify they pass**

```bash
cd skills/canslim-screener/scripts
uv run pytest tests/test_alpaca_data_client.py -v
```
Expected: 4 PASS

- [ ] **Step 5: Commit**

```bash
git add skills/canslim-screener/scripts/alpaca_data_client.py skills/canslim-screener/scripts/tests/test_alpaca_data_client.py
git commit -m "feat: add AlpacaDataClient for CANSLIM screener — bars + computed quotes + calculate_ema"
```

---

### Task 4: Wire CANSLIM screener to use AlpacaDataClient

**Files:**
- Modify: `skills/canslim-screener/scripts/screen_canslim.py`

**Context:**
`screen_canslim.py` uses a single `client` object for both price data AND fundamentals. After this task there will be two clients:
- `price_client` (`AlpacaDataClient`) — for `get_quote()`, `get_historical_prices()`, `get_api_stats()`
- `client` (`FMPClient`) — for `get_income_statement()`, `get_institutional_holders()`, `get_profile()`

The calls to replace (all currently on the single `client`):

| Line | Method | Move to |
|------|--------|---------|
| 300 | `client.get_quote("^GSPC")` | `price_client.get_quote("^GSPC")` |
| 301 | `client.get_quote("^VIX")` | `price_client.get_quote("^VIX")` |
| 309 | `client.get_historical_prices("^GSPC", days=365)` | `price_client.get_historical_prices(...)` |
| 153 | `client.get_quote(symbol)` | `price_client.get_quote(symbol)` |
| 180 | `client.get_historical_prices(symbol, days=90)` | `price_client.get_historical_prices(...)` |
| 189 | `client.get_historical_prices(symbol, days=365)` | `price_client.get_historical_prices(...)` |
| 404 | `client.get_api_stats()` | `price_client.get_api_stats()` |

FMP calls (keep on `client`): lines 143 (`get_profile`), 161 (`get_income_statement` quarterly), 169 (`get_income_statement` annual), 203 (`get_institutional_holders`).

- [ ] **Step 1: Write the failing test**

```python
# Add to skills/canslim-screener/scripts/tests/test_canslim_fixes.py

def test_screen_canslim_uses_alpaca_data_client():
    """screen_canslim.py must import AlpacaDataClient for price data."""
    import os
    src_path = os.path.join(os.path.dirname(__file__), "..", "screen_canslim.py")
    src = open(src_path).read()
    assert "AlpacaDataClient" in src, "screen_canslim.py must import AlpacaDataClient"
    assert "price_client" in src, "screen_canslim.py must use price_client for Alpaca calls"
```

- [ ] **Step 2: Run test to verify it fails**

```bash
cd skills/canslim-screener/scripts
uv run pytest tests/test_canslim_fixes.py::test_screen_canslim_uses_alpaca_data_client -v
```
Expected: FAIL

- [ ] **Step 3: Update `screen_canslim.py`**

**Change 1 — add import at line 33** (after `from fmp_client import FMPClient`):
```python
from fmp_client import FMPClient
from alpaca_data_client import AlpacaDataClient
```

**Change 2 — add `price_client` after `client` is initialized (~line 284)**:
```python
    # Initialize FMP client (fundamentals only)
    try:
        client = FMPClient(api_key=args.api_key)
        print("✓ FMP API client initialized (fundamentals)")
    except ValueError as e:
        print(f"ERROR: {e}", file=sys.stderr)
        sys.exit(1)

    # Initialize Alpaca client (price/historical data)
    try:
        price_client = AlpacaDataClient()
        print("✓ Alpaca data client initialized (price/historical)")
    except EnvironmentError as e:
        print(f"ERROR: {e}", file=sys.stderr)
        sys.exit(1)
```

**Change 3 — update `analyze_stock` function signature (~line 125)**:
```python
# Before:
def analyze_stock(symbol: str, client: FMPClient, market_data: dict, sp500_historical: list[dict] = None):
# After:
def analyze_stock(symbol: str, client: FMPClient, price_client: AlpacaDataClient, market_data: dict, sp500_historical: list[dict] = None):
```

**Change 4 — inside `analyze_stock`, replace price calls**:
```python
# Line 153: get_quote
quote = price_client.get_quote(symbol)
# Line 180: get_historical_prices 90 days
historical_prices = price_client.get_historical_prices(symbol, days=90)
# Line 189: get_historical_prices 365 days
historical_prices_52w_data = price_client.get_historical_prices(symbol, days=365)
```

**Change 5 — in `main()`, replace market data calls (~lines 300, 301, 309)**:
```python
sp500_quote = price_client.get_quote("^GSPC")
vix_quote = price_client.get_quote("^VIX")
# ...
sp500_historical = price_client.get_historical_prices("^GSPC", days=365)
```

**Change 6 — update `analyze_stock` call (~line 347)**:
```python
analysis = analyze_stock(symbol, client, price_client, market_data, sp500_historical)
```

**Change 7 — replace `api_stats` call (~line 404)**:
```python
api_stats = price_client.get_api_stats()
```

- [ ] **Step 4: Run tests**

```bash
cd skills/canslim-screener/scripts
uv run pytest tests/ -v
```
Expected: All tests pass

- [ ] **Step 5: Commit**

```bash
git add skills/canslim-screener/scripts/screen_canslim.py
git commit -m "feat: wire CANSLIM screener to use AlpacaDataClient for price data — FMP kept for fundamentals only"
```

---

### Task 5: Push and deploy to Pi

- [ ] **Step 1: Push to GitHub**

```bash
git push
```

- [ ] **Step 2: Pull and restart on Pi**

```bash
cd ~/claude-trading-skills && git pull && sudo systemctl restart trading-dashboard
```

- [ ] **Step 3: Manually trigger both screeners**

```bash
curl -X POST http://localhost:8000/api/skill/vcp-screener/refresh
sleep 5
curl -X POST http://localhost:8000/api/skill/canslim-screener/refresh
```

- [ ] **Step 4: Check logs**

```bash
cat ~/claude-trading-skills/examples/market-dashboard/cache/vcp-screener.stderr.log
cat ~/claude-trading-skills/examples/market-dashboard/cache/canslim-screener.stderr.log
```
Expected: "Alpaca data client initialized" in each log, no "Timeout" errors, JSON output files created.
