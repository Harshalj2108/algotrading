# SynthCrypto Market Simulator

## Overview
SynthCrypto is an advanced synthetic cryptocurrency market simulator designed to replicate realistic market behaviors, order flow dynamics, and financial asset movements. Developed using Python, it offers a robust environment for evaluating algorithmic trading strategies under typical and extreme simulated conditions. The project uniquely combines sophisticated statistical models with a live, TradingView-style frontend dashboard built on Flask and WebSockets.

## Key Engine Capabilities
- **GARCH(1,1) Volatility Engine**: Accurately simulates volatility clustering, mimicking real financial markets where periods of high or low volatility tend to group together.
- **Volume and Regime Engine**: Synthesizes trading volumes driven by market context, current regime (bull/bear), and momentum.
- **Dynamic Slippage Model**: Factors in execution delay and variable slippage costs linked to simulated liquidity, instrument volatility, and position size.
- **Correlated Asset Engine**: Connects multiple simulated pairs (e.g., BTC and ETH) using Cholesky decomposition to maintain realistic correlation matrices across simulated price actions.
- **Liquidation Cascade Engine**: Dynamically triggers extreme market moves by simulating forced liquidations of overly leveraged positions, amplifying crashes and rallies.
- **Real-Time Risk Metrics**: Constantly tracks account status, maximum drawdown, Value at Risk (VaR), Sharpe ratios, and overall system expectancy.
- **Advanced Stress Testing**: Allows manual toggling of extreme environments by injecting spread spikes, volatility multipliers, and latency delays into active runs.

## System Architecture
- **Web Interface (`simulator_web_v3.py`)**: The primary runner using Flask and Socket.IO. It streams granular tick data and indicators down to a responsive HTML/JS web dashboard at 20 frames per second.
- **Core Market Engine (`synthetic_market_simulator*.py`)**: Handling complex time-series price fabrication, multi-timeframe aggregation (1s through 1d), position lifecycle management, and fee/margin structures.
- **Unified Runner (`synthetic_market_simulator_unified.py`)**: Designed for headless benchmarking, automated unit tests, and large-scale, standalone strategy evaluations.

## Included Trading Strategies

### VETS (Volatility Expansion Trend Swing) Strategy
- Evaluates volatility compression through Bollinger Band width percentile and Average True Range (ATR).
- Tracks long-term macroeconomic alignment (EMA 50 vs EMA 200).
- Dynamically manages stop-loss (SL) and take-profit (TP) multiples, complete with real-time logging and marker rendering on the UI.

### EMA Bollinger Scalper (v2 Hybrid)
An adaptive, high-frequency mean-reversion and trend-following strategy heavily optimized for 5-minute candles.
- **Trend Alignment**: Executes entries only when the short-term trend (EMA 30 crossing EMA 50) completely aligns with the macroeconomic trend (EMA 200).
- **Advanced Market Filtering**: Incorporates an ADX (Average Directional Index) filter set to greater than 22 to bypass choppy, directionless markets, and dynamically screens Bollinger Band width (greater than 1.5%) to avoid low-volatility whipsaws (squeezes).
- **Entry Logic**: Triggers long positions upon a bullish EMA 30/EMA 50 trend configuration while price breaks below the lower Bollinger Band (mean reversion). Triggers short positions upon a bearish trend configuration while price breaks above the upper extreme.
- **Dynamic Risk Management**: 
  - Standard Stop-Loss is initially placed at a 2.0x ATR distance.
  - **Trailing Logic**: Once a position turns profitable, the system automatically shifts the Stop-Loss to absolute breakeven. It continues to trail progressively at 50% of the maximum unrealized favorable excursion to secure profits over time.
  - The standard Take-Profit exit is programmed at a rigid 1.5 R:R (Risk-to-Reward) geometric extension.
- **Capital Preservation Protocols**: Introduces an uncompromising 3-bar entry cooldown immediately following any Stop-Loss sequence, alongside a hard circuit breaker that disables new entries for a full volatility cycle after three back-to-back losses. A global kill switch also terminates the strategy logic entirely if a 10% maximum portfolio drawdown is sustained.

## Getting Started

### Prerequisites
- Python 3.9+
- The project utilizes a virtual environment for dependency management.

### Installation
1. Clone the repository and navigate into the `synthetic_market` directory.
2. Activate the local virtual environment:
   ```bash
   .venv\scripts\activate
   ```
3. Install any missing dependencies if necessary (requires `flask`, `flask-socketio`, `numpy`).

### Running the Live Simulator
To launch the interactive Phase 2 Simulator:
```bash
python simulator_web_v3.py
```
After the server initializes, open your browser and navigate to:
http://localhost:5000

From the web console, you can manually test long/short executions, adjust algorithmic speeds, toggle specific mathematical models, and analyze chart data. 
