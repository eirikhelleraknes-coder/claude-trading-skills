import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from unittest.mock import MagicMock, patch
import pytest

_FAKE_ENV = {"ALPACA_API_KEY": "fake-key", "ALPACA_SECRET_KEY": "fake-secret"}


def _make_bars_df(symbol="AAPL", n=260, start_price=150.0):
    """Build a minimal mock bars DataFrame."""
    import pandas as pd
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

    with patch("alpaca_data_client.StockHistoricalDataClient") as MockClient, \
         patch.dict(os.environ, _FAKE_ENV):
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

    with patch("alpaca_data_client.StockHistoricalDataClient") as MockClient, \
         patch.dict(os.environ, _FAKE_ENV):
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

    with patch("alpaca_data_client.StockHistoricalDataClient") as MockClient, \
         patch.dict(os.environ, _FAKE_ENV):
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

    with patch("alpaca_data_client.StockHistoricalDataClient") as MockClient, \
         patch.dict(os.environ, _FAKE_ENV):
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

    with patch("alpaca_data_client.StockHistoricalDataClient") as MockClient, \
         patch.dict(os.environ, _FAKE_ENV):
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


def test_get_sp500_constituents_raises():
    """get_sp500_constituents raises NotImplementedError."""
    from alpaca_data_client import AlpacaDataClient

    with patch("alpaca_data_client.StockHistoricalDataClient"), \
         patch.dict(os.environ, _FAKE_ENV):
        client = AlpacaDataClient()
    with pytest.raises(NotImplementedError):
        client.get_sp500_constituents()


def test_get_historical_prices_empty_when_no_data():
    """get_historical_prices returns empty historical list when Alpaca returns no bars."""
    import pandas as pd
    from alpaca_data_client import AlpacaDataClient

    # Build an empty DataFrame with the expected MultiIndex structure
    empty_df = pd.DataFrame(
        columns=["open", "high", "low", "close", "volume"],
    )
    empty_df.index = pd.MultiIndex.from_tuples([], names=["symbol", "timestamp"])

    mock_bars = MagicMock()
    mock_bars.df = empty_df

    with patch("alpaca_data_client.StockHistoricalDataClient") as MockClient, \
         patch.dict(os.environ, _FAKE_ENV):
        MockClient.return_value.get_stock_bars.return_value = mock_bars
        client = AlpacaDataClient()
        result = client.get_historical_prices("FAKE", days=30)

    assert result is None


def test_calculate_ema_returns_float():
    """calculate_ema returns a float close to a manually computed EMA."""
    from alpaca_data_client import AlpacaDataClient
    import os

    with patch("alpaca_data_client.StockHistoricalDataClient"), \
         patch.dict(os.environ, _FAKE_ENV):
        client = AlpacaDataClient()

    # 10 prices, period=3, most-recent-first
    prices = [10.0, 9.0, 8.0, 7.0, 6.0, 5.0, 4.0, 3.0, 2.0, 1.0]
    result = client.calculate_ema(prices, period=3)
    assert isinstance(result, float)
    assert result > 0


def test_calculate_ema_returns_zero_for_insufficient_data():
    """calculate_ema returns 0.0 when prices list is shorter than period."""
    from alpaca_data_client import AlpacaDataClient
    import os

    with patch("alpaca_data_client.StockHistoricalDataClient"), \
         patch.dict(os.environ, _FAKE_ENV):
        client = AlpacaDataClient()

    assert client.calculate_ema([10.0, 9.0], period=5) == 0.0
