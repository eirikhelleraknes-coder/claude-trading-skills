#!/usr/bin/env python3
"""
FMP API Client for Macro Regime Detector

Provides rate-limited access to Financial Modeling Prep stable API endpoints
for macro regime detection analysis. Falls back to yfinance for
ETF/index symbols not available on the FMP free plan.
"""

import datetime
import os
import sys
import time
from typing import Optional

try:
    import requests
except ImportError:
    print("ERROR: requests library not found. Install with: pip install requests", file=sys.stderr)
    sys.exit(1)


def _yf_historical(symbol: str, days: int) -> Optional[dict]:
    """Fetch historical prices via yfinance (fallback for FMP premium symbols)."""
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
        print(f"WARNING: yfinance historical fallback failed for {symbol}: {e}", file=sys.stderr)
        return None


class FMPClient:
    """Client for Financial Modeling Prep stable API with rate limiting and yfinance fallback."""

    BASE_URL = "https://financialmodelingprep.com/stable"
    RATE_LIMIT_DELAY = 0.3  # 300ms between requests

    def __init__(self, api_key: Optional[str] = None):
        self.api_key = api_key or os.getenv("FMP_API_KEY")
        if not self.api_key:
            raise ValueError(
                "FMP API key required. Set FMP_API_KEY environment variable "
                "or pass api_key parameter."
            )
        self.session = requests.Session()
        self.cache = {}
        self.last_call_time = 0
        self.rate_limit_reached = False
        self.retry_count = 0
        self.max_retries = 1
        self.api_calls_made = 0

    def _rate_limited_get(self, url: str, params: Optional[dict] = None) -> Optional[dict]:
        if self.rate_limit_reached:
            return None

        req_params = dict(params) if params else {}
        req_params["apikey"] = self.api_key

        elapsed = time.time() - self.last_call_time
        if elapsed < self.RATE_LIMIT_DELAY:
            time.sleep(self.RATE_LIMIT_DELAY - elapsed)

        try:
            response = self.session.get(url, params=req_params, timeout=30)
            self.last_call_time = time.time()
            self.api_calls_made += 1

            if response.status_code == 200:
                self.retry_count = 0
                return response.json()
            elif response.status_code == 402:
                return {"_premium": True}
            elif response.status_code == 429:
                self.retry_count += 1
                if self.retry_count <= self.max_retries:
                    print("WARNING: Rate limit exceeded. Waiting 60 seconds...", file=sys.stderr)
                    time.sleep(60)
                    return self._rate_limited_get(url, params)
                else:
                    print("ERROR: Daily API rate limit reached.", file=sys.stderr)
                    self.rate_limit_reached = True
                    return None
            else:
                print(
                    f"ERROR: API request failed: {response.status_code} - {response.text[:200]}",
                    file=sys.stderr,
                )
                return None
        except requests.exceptions.RequestException as e:
            print(f"ERROR: Request exception: {e}", file=sys.stderr)
            return None

    def get_historical_prices(self, symbol: str, days: int = 600) -> Optional[dict]:
        """Fetch historical daily OHLCV data."""
        cache_key = f"prices_{symbol}_{days}"
        if cache_key in self.cache:
            return self.cache[cache_key]

        end_date = datetime.date.today()
        start_date = end_date - datetime.timedelta(days=int(days * 1.6) + 10)
        url = f"{self.BASE_URL}/historical-price-eod/full"
        data = self._rate_limited_get(url, {"symbol": symbol, "from": str(start_date), "to": str(end_date)})

        if data is not None and not (isinstance(data, dict) and data.get("_premium")):
            # Stable API returns a flat list; normalize to legacy format
            normalized = {"symbol": symbol, "historical": data[:days] if isinstance(data, list) else data}
            self.cache[cache_key] = normalized
            return normalized

        # Fall back to yfinance
        yf_data = _yf_historical(symbol, days)
        if yf_data:
            self.cache[cache_key] = yf_data
        return yf_data

    def get_batch_historical(self, symbols: list[str], days: int = 600) -> dict[str, list[dict]]:
        """Fetch historical prices for multiple symbols."""
        results = {}
        for symbol in symbols:
            data = self.get_historical_prices(symbol, days=days)
            if data and "historical" in data:
                results[symbol] = data["historical"]
        return results

    def get_treasury_rates(self, days: int = 600) -> Optional[list[dict]]:
        """
        Fetch treasury rate data from FMP stable endpoint.

        Returns list of dicts with keys like 'date', 'year2', 'year10', etc.
        Most recent first.
        """
        cache_key = f"treasury_{days}"
        if cache_key in self.cache:
            return self.cache[cache_key]

        url = f"{self.BASE_URL}/treasury-rates"
        params = {"limit": days}
        data = self._rate_limited_get(url, params)
        if data and isinstance(data, list):
            self.cache[cache_key] = data
            return data
        return None

    def get_api_stats(self) -> dict:
        return {
            "cache_entries": len(self.cache),
            "api_calls_made": self.api_calls_made,
            "rate_limit_reached": self.rate_limit_reached,
        }
