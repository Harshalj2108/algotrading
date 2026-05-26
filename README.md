# TradeSeekho Market Simulator

## Overview
TradeSeekho is an advanced synthetic cryptocurrency market simulator designed to replicate realistic market behaviors, order flow dynamics, and financial asset movements. Developed using Python, it offers a robust environment for evaluating algorithmic trading strategies under typical and extreme simulated conditions. The project uniquely combines sophisticated statistical models with a live, TradingView-style frontend dashboard built on React, powered by a robust Python/FastAPI trading engine and a secure Node/Express authentication server.

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
- **Secure Authentication & OTP Verification**: Integrated a robust JWT-based authentication system with Google OAuth 2.0 and email/password login. Features a secure NodeMailer OTP email verification flow to prevent spam registrations.
- **Referral Rewards System**: Users can invite friends via unique referral links. The system automatically distributes synthetic balance rewards (+1000S for the new user, +2000S for the referrer) *only* after the new account is fully verified via email OTP.
- **Modern React Frontend**: Replaced the previous Flask-based HTML UI with a visually stunning React SPA (Vite). Features premium design aesthetics using components like `StarBorder` and `LightRays`. Fully compatible with Vercel deployment.
- **Decoupled Trading Engine & Portfolio Manager**: Re-architected state management to independently handle live account balances, multi-asset positions, and order states seamlessly via PostgreSQL.
- **Real-Time Synchronization**: Full integration of live market ticks via Socket.IO directly into the trading dashboard (`LiveMarketPage.jsx`), enabling robust updates for positions, unrealized PnL, and automatic TP/SL executions.
- **Unified Recent Trades Feed**: A real-time WebSocket feed that instantly displays every executed trade across Live Crypto, Live Stocks, and Paper Trading, complete with infinite scrolling and market source badges.
- **Interactive Dashboard Positions**: Current positions on the dashboard are now clickable, allowing traders to instantly jump to the corresponding interactive chart to monitor and manage specific trades.
- **Advanced TP/SL Chart Management**: Traders can now edit, save, and remove Take Profit (TP) and Stop Loss (SL) orders directly from the chart using intuitive contextual menus, with instant visual line updates.

## System Architecture
- **React Frontend (`auth-client`)**: A modern Single Page Application built with React and Vite. It handles real-time charting, order placement, portfolio management, and strategy toggling with a premium user interface. Configured for Vercel serverless hosting.
- **Express Auth Server (`auth-server`)**: A Node.js backend managing PostgreSQL database connections, JWT user sessions, Google OAuth flows, OTP email delivery, and referral reward distribution.
- **FastAPI Backend (`simulator_api.py`)**: The primary runner using FastAPI and Socket.IO. It serves REST endpoints and WebSocket streams for real-time market data and simulation state.
- **Real-Time Engine (`realtime_engine.py`)**: Responsible for fetching and streaming live crypto market data to power paper trading.
- **Core Market Engine (`simulator_core.py` & `synthetic_market_simulator*.py`)**: Handling complex time-series price fabrication, multi-timeframe aggregation, decoupled portfolio management, and fee/margin structures.

## Included Trading Strategies

### VETS (Volatility Expansion Trend Swing) Strategy
- Evaluates volatility compression through Bollinger Band width percentile and Average True Range (ATR).
- Tracks long-term macroeconomic alignment (EMA 50 vs EMA 200).
- Dynamically manages stop-loss (SL) and take-profit (TP) multiples, complete with real-time logging and marker rendering on the UI.

### EMA Bollinger Scalper (v2 Hybrid)
An adaptive, high-frequency mean-reversion and trend-following strategy heavily optimized for 5-minute candles.
- **Trend Alignment**: Executes entries only when the short-term trend (EMA 30 crossing EMA 50) completely aligns with the macroeconomic trend (EMA 200).
- **Advanced Market Filtering**: Incorporates an ADX filter set to greater than 22 to bypass choppy markets, and dynamically screens Bollinger Band width (>1.5%) to avoid low-volatility whipsaws.
- **Entry Logic**: Triggers long positions upon a bullish EMA trend configuration while price breaks below the lower Bollinger Band. Triggers short positions upon a bearish trend configuration while price breaks above the upper extreme.
- **Dynamic Risk Management**: Standard Stop-Loss is placed at 2.0x ATR. Trailing logic shifts SL to breakeven when profitable, trailing at 75% of max unrealized profit. TP is set at a 1.5 R:R.
- **Capital Preservation Protocols**: Introduces a 3-bar entry cooldown after a Stop-Loss, and a circuit breaker disabling entries after three back-to-back losses. A kill switch terminates the strategy if a 10% max drawdown is sustained.

## Getting Started

### Prerequisites
- Python 3.9+
- Node.js & npm
- PostgreSQL database

### Installation
1. Clone the repository and navigate into the `synthetic_market` directory.
2. Install Python dependencies:
   ```bash
   pip install -r requirements.txt
   ```
3. Install frontend dependencies:
   ```bash
   cd auth-client
   npm install
   ```
4. Install Auth Server dependencies:
   ```bash
   cd auth-server
   npm install
   ```

### Configuration
1. Create a `.env` file in the `auth-server` directory:
   ```env
   PORT=3001
   DATABASE_URL=postgresql://username:password@localhost:5432/tradeseekho
   JWT_SECRET=your_secret_key
   GOOGLE_CLIENT_ID=your_google_id
   GOOGLE_CLIENT_SECRET=your_google_secret
   GOOGLE_REDIRECT_URI=http://localhost:3001/api/auth/google/callback
   CLIENT_URL=http://localhost:5173
   SIMULATOR_URL=http://localhost:8000
   
   # For OTP Emails
   SMTP_HOST=smtp.gmail.com
   SMTP_PORT=587
   SMTP_USER=your_email@gmail.com
   SMTP_PASS=your_google_app_password
   ```

### Running the Application

You will need to run all three services concurrently:

1. **Start the FastAPI Backend** (Simulator Engine):
   ```bash
   # From the root synthetic_market directory
   uvicorn simulator_api:app --host 0.0.0.0 --port 8000
   ```

2. **Start the Node.js Auth Server** (Database & Accounts):
   ```bash
   # From the auth-server directory
   npm run dev
   ```

3. **Start the React Frontend**:
   ```bash
   # From the auth-client directory
   npm run dev
   ```

Open your browser and navigate to the frontend URL (typically `http://localhost:5173`).
