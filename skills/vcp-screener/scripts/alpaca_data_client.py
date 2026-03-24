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
        self, symbols: list, days: int = 260
    ) -> dict:
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

    def get_quote(self, symbol: str) -> list:
        """Return a one-element list with quote fields computed from bar data."""
        data = self.get_historical_prices(symbol, days=365)
        if not data or not data.get("historical"):
            return []
        bars = data["historical"]
        volumes = [b["volume"] for b in bars[:30]]
        return [{
            "symbol": symbol,
            "price": bars[0]["close"],
            "yearHigh": max(b["high"] for b in bars[:252]),
            "yearLow": min(b["low"] for b in bars[:252]),
            "volume": bars[0]["volume"],
            "avgVolume": int(mean(volumes)) if volumes else 0,
        }]

    def get_batch_quotes(self, symbols: list) -> dict:
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
