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

## 🚀 New Features & Enhancements
- **Live Paper Trading**: Transitioned from a purely synthetic simulation to a live, production-ready environment integrated with real-time market data. Execute paper trades on live crypto assets with real-time price updates and slippage.
- **Modern React Frontend**: Replaced the previous Flask-based HTML UI with a visually stunning React SPA (Vite). Features premium design aesthetics using components like `StarBorder` and `LightRays`.
- **Decoupled Trading Engine & Portfolio Manager**: Re-architected state management to independently handle live account balances, multi-asset positions, and order states seamlessly.
- **Real-Time Synchronization**: Full integration of live market ticks via Socket.IO directly into the trading dashboard (`LiveMarketPage.jsx`), enabling robust updates for positions, unrealized PnL, and automatic TP/SL executions.
- **Unified Recent Trades Feed**: A real-time WebSocket feed that instantly displays every executed trade across Live Crypto, Live Stocks, and Paper Trading, complete with infinite scrolling and market source badges.
- **Interactive Dashboard Positions**: Current positions on the dashboard are now clickable, allowing traders to instantly jump to the corresponding interactive chart to monitor and manage specific trades.
- **Advanced TP/SL Chart Management**: Traders can now edit, save, and remove Take Profit (TP) and Stop Loss (SL) orders directly from the chart using intuitive contextual menus, with instant visual line updates.
- **Improved UI Stability**: Resolved critical rendering crashes and stabilized the Lightweight Charts component with proper React error boundaries and timestamp deduplication.

## System Architecture
- **React Frontend (`auth-client`)**: A modern Single Page Application built with React and Vite. It handles real-time charting, order placement, portfolio management, and strategy toggling with a premium user interface.
- **FastAPI Backend (`simulator_api.py`)**: The primary runner using FastAPI and Socket.IO. It serves REST endpoints and WebSocket streams for real-time market data and simulation state.
- **Real-Time Engine (`realtime_engine.py`)**: Responsible for fetching and streaming live crypto market data to power paper trading.
- **Core Market Engine (`simulator_core.py` & `synthetic_market_simulator*.py`)**: Handling complex time-series price fabrication, multi-timeframe aggregation, decoupled portfolio management, and fee/margin structures.
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
  - **Trailing Logic**: Once a position turns profitable, the system automatically shifts the Stop-Loss to absolute breakeven. It continues to trail progressively at 75% of the maximum unrealized favorable excursion to secure profits over time.
  - The standard Take-Profit exit is programmed at a rigid 1.5 R:R (Risk-to-Reward) geometric extension.
- **Capital Preservation Protocols**: Introduces an uncompromising 3-bar entry cooldown immediately following any Stop-Loss sequence, alongside a hard circuit breaker that disables new entries for a full volatility cycle after three back-to-back losses. A global kill switch also terminates the strategy logic entirely if a 10% maximum portfolio drawdown is sustained.

## Getting Started

### Prerequisites
- Python 3.9+
- Node.js & npm (for the React frontend)

### Installation
1. Clone the repository and navigate into the `synthetic_market` directory.
2. Activate the local Python virtual environment:
   ```bash
   .venv\scripts\activate
   ```
   *(Install backend dependencies via `pip install fastapi uvicorn python-socketio PyJWT numpy` if not already installed).*
3. Install frontend dependencies:
   ```bash
   cd auth-client
   npm install
   ```

### Running the Application

You will need to run both the backend API and the frontend development server.

1. **Start the FastAPI Backend**:
   ```bash
   # From the root synthetic_market directory
   uvicorn simulator_api:app --host 0.0.0.0 --port 8000 --reload
   ```

2. **Start the React Frontend**:
   ```bash
   # In a new terminal, from the auth-client directory
   npm run dev
   ```

Open your browser and navigate to the frontend URL (typically `http://localhost:5173`) to access the dashboard. From there, you can interact with live paper trading, manage your portfolio, and visualize real-time market data.
