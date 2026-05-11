"""
realtime_engine.py
==================
Fetches historical and live market data.

Crypto  — Yahoo Finance (primary), CoinGecko free API (fallback)
Stocks  — yfinance

Uses `requests` (sync, run via asyncio.to_thread) instead of aiohttp
to avoid DNS resolution issues caused by aiodns.
"""

import asyncio
import math
import time
import traceback
import requests
import yfinance as yf

# ─── CoinGecko helpers ────────────────────────────────────────────────────────

COINGECKO_BASE = "https://api.coingecko.com/api/v3"

# Map common trading symbols to CoinGecko IDs
_SYMBOL_TO_CG_ID = {
    "BTC/USDT": "bitcoin",      "BTC": "bitcoin",
    "ETH/USDT": "ethereum",     "ETH": "ethereum",
    "SOL/USDT": "solana",       "SOL": "solana",
    "BNB/USDT": "binancecoin",  "BNB": "binancecoin",
    "XRP/USDT": "ripple",       "XRP": "ripple",
    "ADA/USDT": "cardano",      "ADA": "cardano",
    "DOGE/USDT": "dogecoin",    "DOGE": "dogecoin",
    "AVAX/USDT": "avalanche-2", "AVAX": "avalanche-2",
    "DOT/USDT": "polkadot",     "DOT": "polkadot",
    "MATIC/USDT": "matic-network", "MATIC": "matic-network",
    "LINK/USDT": "chainlink",   "LINK": "chainlink",
    "LTC/USDT": "litecoin",     "LTC": "litecoin",
    "UNI/USDT": "uniswap",     "UNI": "uniswap",
    "ATOM/USDT": "cosmos",      "ATOM": "cosmos",
    "NEAR/USDT": "near",        "NEAR": "near",
    "TRX/USDT": "tron",         "TRX": "tron",
    "SHIB/USDT": "shiba-inu",   "SHIB": "shiba-inu",
    "ARB/USDT": "arbitrum",     "ARB": "arbitrum",
    "OP/USDT": "optimism",      "OP": "optimism",
    "SUI/USDT": "sui",          "SUI": "sui",
    "APT/USDT": "aptos",        "APT": "aptos",
    "FIL/USDT": "filecoin",     "FIL": "filecoin",
    "PEPE/USDT": "pepe",        "PEPE": "pepe",
}

# Search list (these all work with CoinGecko)
KNOWN_CRYPTO = list(dict.fromkeys(s for s in _SYMBOL_TO_CG_ID if "/" in s))


def _resolve_cg_id(symbol: str) -> str | None:
    """Convert a trading symbol (e.g. BTC/USDT) to a CoinGecko ID."""
    s = symbol.upper().strip()
    if s in _SYMBOL_TO_CG_ID:
        return _SYMBOL_TO_CG_ID[s]
    base = s.replace("/USDT", "").replace("/USD", "")
    if base in _SYMBOL_TO_CG_ID:
        return _SYMBOL_TO_CG_ID[base]
    return base.lower()


def _yf_crypto_symbol(symbol: str) -> str:
    """Convert BTC/USDT-style symbols into Yahoo Finance crypto tickers."""
    s = symbol.upper().strip()
    if "/" in s:
        base, quote = s.split("/", 1)
        if quote in {"USDT", "USDC"}:
            quote = "USD"
        return f"{base}-{quote}"
    if s.endswith("USDT"):
        return f"{s[:-4]}-USD"
    if s.endswith("USD"):
        return f"{s[:-3]}-USD"
    return f"{s}-USD"


# yfinance interval -> maximum allowed period mapping
# https://github.com/ranaroussi/yfinance/wiki/Ticker#history
_YF_INTERVAL_MAX_PERIOD = {
    "1m":  "7d",
    "2m":  "60d",
    "5m":  "60d",
    "15m": "60d",
    "30m": "60d",
    "60m": "730d",
    "1h":  "730d",
    "1d":  "max",
    "5d":  "max",
    "1wk": "max",
    "1mo": "max",
    "3mo": "max",
}


def _history_period_for_interval(interval: str) -> str:
    """Return the best period for a given yfinance interval to maximise candle count."""
    return _YF_INTERVAL_MAX_PERIOD.get(interval, "60d")


def _fetch_crypto_history_yfinance_sync(symbol: str, timeframe: str = '5m', limit: int = 500):
    """Fetch crypto OHLCV from Yahoo Finance (primary source for granular data)."""
    yf_symbol = _yf_crypto_symbol(symbol)
    period = _history_period_for_interval(timeframe)
    retries = 2
    for attempt in range(retries + 1):
        try:
            ticker = yf.Ticker(yf_symbol)
            df = ticker.history(period=period, interval=timeframe)
            if df is None or df.empty:
                print(f"[yfinance] No data for {yf_symbol} (period={period}, interval={timeframe}), attempt {attempt+1}")
                if attempt < retries:
                    time.sleep(0.5)
                    continue
                return []

            df = df.tail(limit)
            results = []
            for index, row in df.iterrows():
                open_ = float(row["Open"])
                high = float(row["High"])
                low = float(row["Low"])
                close = float(row["Close"])
                if not all(math.isfinite(value) for value in (open_, high, low, close)):
                    continue
                if open_ <= 0 or close <= 0:
                    continue
                volume = float(row.get("Volume", 0) or 0)
                if not math.isfinite(volume):
                    volume = 0
                results.append({
                    "time": int(index.timestamp()),
                    "open": open_,
                    "high": high,
                    "low": low,
                    "close": close,
                    "volume": volume,
                })
            if results:
                print(f"[yfinance] {yf_symbol} {timeframe}: {len(results)} candles")
                return results
            if attempt < retries:
                time.sleep(0.5)
        except Exception as e:
            print(f"[yfinance] crypto history attempt {attempt+1} failed for {symbol} ({yf_symbol}): {e}")
            if attempt < retries:
                time.sleep(1)
    return []


def _fetch_crypto_history_sync(symbol: str, timeframe: str = '5m', limit: int = 500):
    """Fetch OHLCV for crypto, preferring granular Yahoo data with CoinGecko fallback."""
    yf_results = _fetch_crypto_history_yfinance_sync(symbol, timeframe, limit)
    if len(yf_results) > 1:
        return yf_results

    print(f"[CoinGecko] Falling back for {symbol} {timeframe}")
    cg_id = _resolve_cg_id(symbol)
    if not cg_id:
        print(f"[CoinGecko] Could not resolve CoinGecko ID for {symbol}")
        return []

    # CoinGecko OHLC granularity:
    #   1-2 days   -> ~30-min candles (best for sub-hourly)
    #   3-30 days  -> 4-hour candles  (best for hourly)
    #   31+ days   -> 4-day candles   (best for daily)
    # Use larger ranges to get more candles even if they're less granular:
    tf_to_days = {
        '1m': 2, '5m': 2, '15m': 7, '30m': 14,
        '1h': 30, '4h': 90, '1d': 365,
    }
    days = tf_to_days.get(timeframe, 2)

    try:
        url = f"{COINGECKO_BASE}/coins/{cg_id}/ohlc"
        resp = requests.get(url, params={"vs_currency": "usd", "days": days}, timeout=15)
        if resp.status_code == 429:
            print(f"[CoinGecko] Rate limited for {cg_id}, retrying after 2s...")
            time.sleep(2)
            resp = requests.get(url, params={"vs_currency": "usd", "days": days}, timeout=15)
        if resp.status_code != 200:
            print(f"[CoinGecko] OHLC error: {resp.status_code} for {cg_id}")
            return []
        data = resp.json()

        results = []
        for row in data:
            if not isinstance(row, (list, tuple)) or len(row) < 5:
                continue
            open_ = float(row[1])
            high = float(row[2])
            low = float(row[3])
            close = float(row[4])
            if not all(math.isfinite(value) for value in (open_, high, low, close)):
                continue
            if open_ <= 0 or close <= 0:
                continue
            # [timestamp_ms, open, high, low, close]
            results.append({
                "time": int(row[0] / 1000),
                "open": open_,
                "high": high,
                "low": low,
                "close": close,
                "volume": 0,
            })
        print(f"[CoinGecko] {cg_id} {timeframe}: {len(results)} candles (days={days})")
        return results
    except Exception as e:
        print(f"[CoinGecko] Error fetching history for {symbol} ({cg_id}): {e}")
        traceback.print_exc()
        return []


def _fetch_crypto_ticker_sync(symbol: str):
    """Fetch latest price from CoinGecko (synchronous)."""
    cg_id = _resolve_cg_id(symbol)
    if not cg_id:
        return None
    try:
        url = f"{COINGECKO_BASE}/simple/price"
        resp = requests.get(url, params={
            "ids": cg_id,
            "vs_currencies": "usd",
            "include_24hr_vol": "true",
        }, timeout=10)
        if resp.status_code != 200:
            return None
        data = resp.json()
        if cg_id in data:
            price = float(data[cg_id].get("usd", 0) or 0)
            volume = float(data[cg_id].get("usd_24h_vol", 0) or 0)
            if not math.isfinite(price) or price <= 0:
                return None
            if not math.isfinite(volume):
                volume = 0
            return {
                "symbol": symbol,
                "price": price,
                "volume": volume,
                "time": int(time.time()),
            }
        return None
    except Exception as e:
        print(f"Error fetching crypto ticker for {symbol}: {e}")
        return None


def _search_crypto_coingecko_sync(query: str):
    """Search CoinGecko for coins (synchronous)."""
    try:
        resp = requests.get(f"{COINGECKO_BASE}/search", params={"query": query}, timeout=10)
        if resp.status_code != 200:
            return []
        data = resp.json()
        results = []
        for coin in (data.get("coins") or [])[:15]:
            sym = coin.get("symbol", "").upper()
            cg_id = coin.get("id", "")
            display = f"{sym}/USDT"
            _SYMBOL_TO_CG_ID[display] = cg_id
            _SYMBOL_TO_CG_ID[sym] = cg_id
            results.append({"symbol": display, "name": coin.get("name", sym), "type": "crypto"})
        return results
    except Exception as e:
        print(f"CoinGecko search error: {e}")
        return []


# ─── YFinance (already synchronous) ──────────────────────────────────────────

def fetch_stock_history(symbol: str, interval: str = '5m', period: str = '5d'):
    """Fetch historical OHLCV data using yfinance with retry logic."""
    retries = 2
    for attempt in range(retries + 1):
        try:
            ticker = yf.Ticker(symbol)
            df = ticker.history(period=period, interval=interval)
            if df is None or df.empty:
                print(f"[yfinance] No stock data for {symbol} (period={period}, interval={interval}), attempt {attempt+1}")
                if attempt < retries:
                    time.sleep(0.5)
                    continue
                return []

            results = []
            for index, row in df.iterrows():
                open_ = float(row['Open'])
                high = float(row['High'])
                low = float(row['Low'])
                close = float(row['Close'])
                if not all(math.isfinite(value) for value in (open_, high, low, close)):
                    continue
                if open_ <= 0 or close <= 0:
                    continue
                volume = float(row['Volume'] or 0)
                if not math.isfinite(volume):
                    volume = 0
                results.append({
                    "time": int(index.timestamp()),
                    "open": open_,
                    "high": high,
                    "low": low,
                    "close": close,
                    "volume": volume,
                })
            if results:
                print(f"[yfinance] Stock {symbol} {interval}: {len(results)} candles")
                return results
            if attempt < retries:
                time.sleep(0.5)
        except Exception as e:
            print(f"[yfinance] Stock history attempt {attempt+1} failed for {symbol}: {e}")
            if attempt < retries:
                time.sleep(1)
    return []


def fetch_stock_ticker(symbol: str):
    """Fetch latest price for live update."""
    try:
        ticker = yf.Ticker(symbol)
        df = ticker.history(period="1d", interval="1m")
        if not df.empty:
            latest = df.iloc[-1]
            price = float(latest['Close'])
            volume = float(latest['Volume'] or 0)
            if not math.isfinite(price):
                return None
            if not math.isfinite(volume):
                volume = 0
            return {
                "symbol": symbol,
                "price": price,
                "volume": volume,
                "time": int(df.index[-1].timestamp()),
            }
        return None
    except Exception as e:
        print(f"Error fetching stock ticker for {symbol}: {e}")
        return None


# ─── Unified Async Interface ─────────────────────────────────────────────────

class RealTimeDataEngine:
    def __init__(self):
        pass

    async def get_history(self, asset_class: str, symbol: str, tf: str = '5m', limit: int = 500):
        print(f"[DataEngine] get_history: asset={asset_class} symbol={symbol} tf={tf} limit={limit}")
        try:
            if asset_class == 'crypto':
                data = await asyncio.to_thread(_fetch_crypto_history_sync, symbol, tf, limit)
            elif asset_class == 'stock':
                yf_interval = tf
                yf_period = _history_period_for_interval(tf)
                data = await asyncio.to_thread(fetch_stock_history, symbol, yf_interval, yf_period)
            else:
                raise ValueError(f"Unknown asset class: {asset_class}")
            
            print(f"[DataEngine] Returning {len(data)} candles for {asset_class}/{symbol}/{tf}")
            return data
        except Exception as e:
            print(f"[DataEngine] Error in get_history: {e}")
            traceback.print_exc()
            return []

    async def get_ticker(self, asset_class: str, symbol: str):
        if asset_class == 'crypto':
            return await asyncio.to_thread(_fetch_crypto_ticker_sync, symbol)
        elif asset_class == 'stock':
            return await asyncio.to_thread(fetch_stock_ticker, symbol)
        else:
            raise ValueError(f"Unknown asset class: {asset_class}")

    async def search_symbols(self, query: str, asset_class: str):
        """Search for symbols."""
        query_upper = query.upper().strip()
        results = []

        if asset_class == 'crypto':
            # Match from our known list
            for sym in KNOWN_CRYPTO:
                if query_upper in sym:
                    results.append({"symbol": sym, "name": sym, "type": "crypto"})
                    if len(results) >= 20:
                        break
            # Fallback to CoinGecko search if no local matches
            if len(results) == 0:
                results = await asyncio.to_thread(_search_crypto_coingecko_sync, query)

        elif asset_class == 'stock':
            common = [
                "AAPL", "MSFT", "GOOGL", "AMZN", "TSLA", "META", "NVDA",
                "NFLX", "AMD", "INTC", "CRM", "ORCL", "UBER", "PYPL",
                "RELIANCE.NS", "TCS.NS", "INFY.NS", "HDFCBANK.NS",
                "WIPRO.NS", "BHARTIARTL.NS", "ITC.NS", "SBIN.NS",
            ]
            for sym in common:
                if query_upper in sym.upper():
                    results.append({"symbol": sym, "name": sym, "type": "stock"})
            if not any(r['symbol'].upper() == query_upper for r in results):
                results.append({"symbol": query_upper, "name": query_upper, "type": "stock"})

        return results


# Singleton instance
data_engine = RealTimeDataEngine()


async def _test():
    print("=== Crypto Search ===")
    r = await data_engine.search_symbols("BTC", "crypto")
    print(f"  {len(r)} results:", [x['symbol'] for x in r[:5]])

    print("\n=== Crypto History (BTC/USDT, 5m) ===")
    h = await data_engine.get_history('crypto', 'BTC/USDT', '5m')
    print(f"  {len(h)} candles")
    if h:
        print(f"  Last candle: {h[-1]}")

    print("\n=== Crypto Ticker (BTC/USDT) ===")
    t = await data_engine.get_ticker('crypto', 'BTC/USDT')
    print(f"  {t}")

    print("\n=== Stock History (AAPL, 5m) ===")
    h2 = await data_engine.get_history('stock', 'AAPL', '5m')
    print(f"  {len(h2)} candles")
    if h2:
        print(f"  Last candle: {h2[-1]}")

    print("\n=== Stock Search ===")
    r2 = await data_engine.search_symbols("AAP", "stock")
    print(f"  {len(r2)} results:", [x['symbol'] for x in r2])


if __name__ == "__main__":
    asyncio.run(_test())
