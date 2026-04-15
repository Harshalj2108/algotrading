import requests
import pandas as pd
import time
from datetime import datetime
from dateutil.relativedelta import relativedelta

def download_futures_data(symbol="BTCUSDT", interval="1h", years=2):
    limit = 1500  # max for binance fapi
    end_dt = datetime.now()
    start_dt = end_dt - relativedelta(years=years)

    end_ts = int(end_dt.timestamp() * 1000)
    start_ts = int(start_dt.timestamp() * 1000)

    current_start = start_ts
    all_data = []

    print(f"Downloading {symbol} {interval} futures candles for the last {years} years...")

    while current_start < end_ts:
        url = f"https://fapi.binance.com/fapi/v1/klines?symbol={symbol}&interval={interval}&startTime={current_start}&endTime={end_ts}&limit={limit}"
        response = requests.get(url)
        data = response.json()

        if not isinstance(data, list) or len(data) == 0:
            break
            
        all_data.extend(data)
        
        # Next start time = last candle open time + 1 ms to avoid duplicates
        current_start = data[-1][0] + 1
        
        print(f"Fetched {len(data)} candles. Total so far: {len(all_data)} (Last date: {datetime.fromtimestamp(data[-1][0]/1000).strftime('%Y-%m-%d %H:%M:%S')})")
        time.sleep(0.2)  # brief pause to respect API limits

    if not all_data:
        print("No data was fetched!")
        return

    # Create DataFrame
    df = pd.DataFrame(all_data, columns=[
        'timestamp', 'open', 'high', 'low', 'close', 'volume', 
        'close_time', 'quote_asset_volume', 'number_of_trades', 
        'taker_buy_base_asset_volume', 'taker_buy_quote_asset_volume', 'ignore'
    ])

    # Add human readable datetime
    df['datetime'] = pd.to_datetime(df['timestamp'], unit='ms')

    # Keep and reorder the essential columns
    df = df[['datetime', 'open', 'high', 'low', 'close', 'volume']]

    # Save to CSV
    filename = f"{symbol}_{interval}_{years}y.csv"
    df.to_csv(filename, index=False)
    print(f"\nSuccessfully downloaded and saved {len(df)} candles to {filename}.")

if __name__ == "__main__":
    download_futures_data()
