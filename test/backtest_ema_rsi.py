import pandas as pd
import numpy as np

def calculate_rsi(series, period=14):
    delta = series.diff()
    gain = delta.where(delta > 0, 0.0)
    loss = -delta.where(delta < 0, 0.0)
    
    avg_gain = gain.ewm(alpha=1/period, adjust=False).mean()
    avg_loss = loss.ewm(alpha=1/period, adjust=False).mean()
    
    rs = avg_gain / avg_loss
    return 100 - (100 / (1 + rs))

def calculate_atr(df, period=14):
    high_low = df['high'] - df['low']
    high_close = np.abs(df['high'] - df['close'].shift())
    low_close = np.abs(df['low'] - df['close'].shift())
    
    ranges = pd.concat([high_low, high_close, low_close], axis=1)
    true_range = ranges.max(axis=1)
    
    return true_range.ewm(alpha=1/period, adjust=False).mean()

def run_backtest(csv_file='BTCUSDT_1h_2y.csv', initial_capital=10000.0, fee_rate=0.0005):
    print(f"Loading data from {csv_file}...")
    df = pd.read_csv(csv_file)
    
    # Calculate Indicators
    print("Calculating indicators...")
    df['ema100'] = df['close'].ewm(span=100, adjust=False).mean()
    df['rsi14'] = calculate_rsi(df['close'], period=14)
    df['atr14'] = calculate_atr(df, period=14)
    
    # Shift indicators by 1 to represent what was known at the open of the current candle
    df['prev_close'] = df['close'].shift(1)
    df['prev_ema'] = df['ema100'].shift(1)
    df['prev_rsi'] = df['rsi14'].shift(1)
    df['prev2_rsi'] = df['rsi14'].shift(2)
    df['prev_atr'] = df['atr14'].shift(1)
    
    # Drop NaNs to start simulation
    df = df.dropna()
    
    balance = initial_capital
    peak = initial_capital
    max_dd = 0.0
    
    pos = None
    trades = []
    
    print(f"Starting simulation over {len(df)} 1h candles...\n")
    
    for i, row in df.iterrows():
        # -- Check Exits --
        if pos is not None:
            hit_reason = None
            exit_price = None
            
            # Pessimistic evaluation: if both SL and TP hit in same candle, assume SL hit first
            if pos['side'] == 'long':
                if row['low'] <= pos['sl']:
                    hit_reason = 'Stop Loss'
                    exit_price = pos['sl']
                elif row['high'] >= pos['tp']:
                    hit_reason = 'Take Profit'
                    exit_price = pos['tp']
            elif pos['side'] == 'short':
                if row['high'] >= pos['sl']:
                    hit_reason = 'Stop Loss'
                    exit_price = pos['sl']
                elif row['low'] <= pos['tp']:
                    hit_reason = 'Take Profit'
                    exit_price = pos['tp']
                    
            if hit_reason:
                if pos['side'] == 'long':
                    pnl = (exit_price - pos['entry']) / pos['entry'] * pos['size']
                else:
                    pnl = (pos['entry'] - exit_price) / pos['entry'] * pos['size']
                    
                fee = pos['size'] * fee_rate
                pnl -= fee
                
                balance += pnl
                trades.append({
                    'side': pos['side'],
                    'entry_time': pos['entry_time'],
                    'exit_time': row['datetime'],
                    'entry': pos['entry'],
                    'exit': exit_price,
                    'pnl': pnl,
                    'reason': hit_reason
                })
                
                pos = None
                
                # Update Max DD
                if balance > peak:
                    peak = balance
                dd = (peak - balance) / peak
                if dd > max_dd:
                    max_dd = dd
                    
        # -- Check Entries --
        if pos is None:
            # LONG: Close > EMA100 AND RSI crossed above 40
            is_long_trend = row['prev_close'] > row['prev_ema']
            rsi_cross_up = row['prev2_rsi'] < 40 and row['prev_rsi'] >= 40
            
            # SHORT: Close < EMA100 AND RSI crossed below 60
            is_short_trend = row['prev_close'] < row['prev_ema']
            rsi_cross_down = row['prev2_rsi'] > 60 and row['prev_rsi'] <= 60
            
            side = None
            if is_long_trend and rsi_cross_up:
                side = 'long'
            elif is_short_trend and rsi_cross_down:
                side = 'short'
                
            if side:
                entry_price = row['open']
                atr = row['prev_atr']
                
                stop_dist = 2.0 * atr
                tp_dist = 2.5 * stop_dist  # 1:2.5 RR
                
                if side == 'long':
                    sl = entry_price - stop_dist
                    tp = entry_price + tp_dist
                else:
                    sl = entry_price + stop_dist
                    tp = entry_price - tp_dist
                    
                # Risk 3% of balance
                risk_amt = balance * 0.03
                margin_sl_pct = stop_dist / entry_price
                size = risk_amt / margin_sl_pct if margin_sl_pct > 0 else 0
                
                # Cannot trade more than what we have unleveraged (capped at 5x max here)
                size = min(size, balance * 5.0) 
                
                entry_fee = size * fee_rate
                
                if balance > 0:
                    pos = {
                        'side': side,
                        'entry_time': row['datetime'],
                        'entry': entry_price,
                        'size': size,
                        'sl': sl,
                        'tp': tp
                    }

    # -- Calculate Final Metrics --
    wins = [t for t in trades if t['pnl'] > 0]
    losses = [t for t in trades if t['pnl'] <= 0]
    
    win_rate = len(wins) / len(trades) * 100 if len(trades) > 0 else 0
    total_pnl = balance - initial_capital
    avg_win = np.mean([w['pnl'] for w in wins]) if wins else 0
    avg_loss = np.mean([l['pnl'] for l in losses]) if losses else 0
    profit_factor = abs(sum([w['pnl'] for w in wins]) / sum([l['pnl'] for l in losses])) if len(losses) > 0 and sum([l['pnl'] for l in losses]) != 0 else float('inf')

    print("=" * 50)
    print("BACKTEST RESULTS (EMA 100 + RSI 14 Strategy)")
    print("=" * 50)
    print(f"Total Trades:      {len(trades)}")
    print(f"Win Rate:          {win_rate:.2f}%")
    print(f"Initial Capital:   ${initial_capital:,.2f}")
    print(f"Final Balance:     ${balance:,.2f}")
    print(f"Net Return:        {(balance-initial_capital)/initial_capital*100:.2f}%")
    print(f"Max Drawdown:      {max_dd*100:.2f}%")
    print(f"Profit Factor:     {profit_factor:.2f}")
    print(f"Expectancy:        ${(win_rate/100*avg_win) - ((1-(win_rate/100))*abs(avg_loss)):,.2f}")
    print(f"Avg Win:           +${avg_win:,.2f}")
    print(f"Avg Loss:          -${abs(avg_loss):,.2f}")
    print("=" * 50)

if __name__ == "__main__":
    run_backtest()
