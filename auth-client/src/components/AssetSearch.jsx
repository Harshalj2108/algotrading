import { useState, useEffect, useRef } from 'react';
import { SIMULATOR_URL } from '../config';
import BorderGlow from './BorderGlow';
import './AssetSearch.css';

const POPULAR_CRYPTO = ['BTC/USDT', 'ETH/USDT', 'SOL/USDT', 'BNB/USDT', 'XRP/USDT', 'ADA/USDT', 'DOGE/USDT', 'AVAX/USDT'];
const POPULAR_STOCKS = ['AAPL', 'MSFT', 'GOOGL', 'AMZN', 'TSLA', 'META', 'NVDA', 'RELIANCE.NS'];

const EDU_TOPICS = [
  {
    term: "Stop Loss",
    icon: "🛡️",
    tag: "Risk Management",
    desc: "Automatically closes a trade to limit losses when price moves against your position. Essential for protecting your capital in volatile markets."
  },
  {
    term: "Take Profit",
    icon: "🎯",
    tag: "Order Type",
    desc: "Automatically closes a trade when a target profit level is reached. Locks in gains without requiring you to monitor the market constantly."
  },
  {
    term: "Market Order",
    icon: "⚡",
    tag: "Order Type",
    desc: "Executes instantly at the best available market price. Fast but may experience slippage during high volatility."
  },
  {
    term: "Limit Order",
    icon: "📌",
    tag: "Order Type",
    desc: "Executes only at a specific price or better. Gives you precise control over your entry and exit points."
  },
  {
    term: "Stop Market Order",
    icon: "🔔",
    tag: "Advanced Order",
    desc: "Triggers a market order once a stop price is reached. Commonly used for breakout trading strategies."
  },
  {
    term: "Stop Limit Order",
    icon: "🔒",
    tag: "Advanced Order",
    desc: "Triggers a limit order after the stop price is hit. Combines the precision of limits with stop activation."
  },
  {
    term: "Long Position",
    icon: "📈",
    tag: "Position Type",
    desc: "Betting that the price will go up. You buy an asset expecting to sell it later at a higher price for profit."
  },
  {
    term: "Short Position",
    icon: "📉",
    tag: "Position Type",
    desc: "Betting that the price will go down. You sell borrowed assets expecting to buy back at a lower price."
  },
  {
    term: "PnL",
    icon: "💰",
    tag: "Metrics",
    desc: "Profit and Loss — the net gain or loss from your trading activity. Tracks both realized and unrealized returns."
  },
  {
    term: "Leverage",
    icon: "⚖️",
    tag: "Advanced",
    desc: "Using borrowed capital to increase trade exposure. Amplifies both potential profits and losses significantly."
  }
];

export default function AssetSearch({ assetClass, onSelect, onBack }) {
  const [query, setQuery] = useState('');
  const [results, setResults] = useState([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState(null);
  const inputRef = useRef(null);

  // Auto-focus input on mount
  useEffect(() => {
    inputRef.current?.focus();
  }, []);

  useEffect(() => {
    if (!query || query.length < 2) {
      return;
    }

    const controller = new AbortController();
    const timer = setTimeout(async () => {
      setLoading(true);
      setError(null);
      try {
        const res = await fetch(
          `${SIMULATOR_URL}/api/live/search?q=${encodeURIComponent(query)}&type=${assetClass}`,
          { signal: controller.signal }
        );
        if (!res.ok) throw new Error(`Server returned ${res.status}`);
        const data = await res.json();
        setResults(data.results || []);
      } catch (e) {
        if (e.name !== 'AbortError') {
          console.error("Search error", e);
          setError("Search failed. Make sure the simulator API is running.");
        }
      } finally {
        setLoading(false);
      }
    }, 400);

    return () => {
      clearTimeout(timer);
      controller.abort();
    };
  }, [query, assetClass]);

  const handleSelect = (symbol) => {
    if (onSelect) onSelect(symbol);
  };

  const handleQueryChange = (event) => {
    const nextQuery = event.target.value;
    setQuery(nextQuery);
    if (!nextQuery || nextQuery.length < 2) {
      setResults([]);
      setError(null);
    }
  };

  const popularItems = assetClass === 'crypto' ? POPULAR_CRYPTO : POPULAR_STOCKS;
  const isCrypto = assetClass === 'crypto';

  return (
    <div className="asset-search-content">
      {/* ── Top Bar ── */}
      <div className="asset-search-topbar">
        {onBack && (
          <button className="asset-search-back-btn" onClick={onBack} id="asset-search-back">
            <svg viewBox="0 0 24 24"><path d="M19 12H5M12 19l-7-7 7-7" /></svg>
            Dashboard
          </button>
        )}
        <div className="asset-search-title-badge">
          <span className="badge-dot" />
          {isCrypto ? 'Live Crypto' : 'Live Stocks'}
        </div>
      </div>

      {/* ── Header ── */}
      <section className="asset-search-header">
        <p className="search-eyebrow">
          {isCrypto ? 'Cryptocurrency Markets' : 'Stock Markets'}
        </p>
        <h1>
          Search & Trade{' '}
          <span className="accent">{isCrypto ? 'Crypto' : 'Stocks'}</span>{' '}
          Live
        </h1>
        <p className="search-subtitle">
          {isCrypto
            ? 'Find any cryptocurrency pair and start paper trading with real-time market data. Practice risk-free.'
            : 'Look up any stock ticker and start paper trading with live market data. Learn by doing, risk-free.'
          }
        </p>
      </section>

      {/* ── Search Input ── */}
      <div className="asset-search-input-wrapper">
        <div className="asset-search-input-container">
          <span className="asset-search-input-icon">🔍</span>
          <input
            ref={inputRef}
            id="asset-search-input"
            type="text"
            className="asset-search-input"
            placeholder={isCrypto
              ? 'Search crypto pairs (e.g. BTC, ETH, SOL)...'
              : 'Search stock tickers (e.g. AAPL, TSLA, MSFT)...'
            }
            value={query}
            onChange={handleQueryChange}
            autoComplete="off"
          />
          {loading && <div className="asset-search-input-spinner" />}
          {!loading && <span className="asset-search-input-hint">⌘K</span>}
        </div>

        {/* Error */}
        {error && (
          <div className="asset-search-error" id="asset-search-error">
            ⚠ {error}
          </div>
        )}

        {/* Search Results Dropdown */}
        {results.length > 0 && (
          <div className="asset-search-results" id="asset-search-results">
            {results.map((r, i) => (
              <div
                key={i}
                className="asset-search-result-item"
                onClick={() => handleSelect(r.symbol)}
              >
                <div className="asset-search-result-info">
                  <div className="asset-search-result-symbol">
                    {r.symbol}
                    {r.exchange && (
                      <span className="asset-search-result-exchange">({r.exchange})</span>
                    )}
                  </div>
                  {r.name && r.name !== r.symbol && (
                    <div className="asset-search-result-name">{r.name}</div>
                  )}
                </div>
                <div className="asset-search-result-type">
                  {r.type === 'crypto' ? '🪙' : '📊'} {r.type}
                </div>
              </div>
            ))}
          </div>
        )}

        {/* No results */}
        {!loading && query.length >= 2 && results.length === 0 && !error && (
          <div className="asset-search-results">
            <div className="asset-search-no-results">
              No results found for "{query}"
            </div>
          </div>
        )}
      </div>

      {/* ── Popular Picks ── */}
      {query.length < 2 && (
        <section className="asset-search-popular-section" id="asset-search-popular">
          <div className="asset-search-section-label">
            🔥 Popular {isCrypto ? 'Pairs' : 'Tickers'}
          </div>
          <div className="asset-search-popular-grid">
            {popularItems.map(sym => (
              <button
                key={sym}
                className="asset-search-popular-chip"
                onClick={() => handleSelect(sym)}
              >
                {sym}
              </button>
            ))}
          </div>
        </section>
      )}

      {/* ── Educational Cards ── */}
      <section className="asset-search-edu-section" id="asset-search-edu">
        <div className="asset-search-edu-heading">
          <h2>Trading Concepts</h2>
          <p>Master these essential concepts before you start trading. Understanding the basics is the foundation of every successful trader.</p>
        </div>

        <div className="asset-search-edu-grid">
          {EDU_TOPICS.map(topic => (
            <BorderGlow
              key={topic.term}
              glowColor="270 70 75"
              backgroundColor="rgba(15, 10, 25, 0.7)"
              borderRadius={12}
              glowRadius={28}
              glowIntensity={1.1}
              coneSpread={22}
              colors={['#c084fc', '#a78bfa', '#7c3aed']}
            >
              <article className="asset-search-edu-card">
                <div className="asset-search-edu-card-icon">
                  {topic.icon}
                </div>
                <h3>{topic.term}</h3>
                <p>{topic.desc}</p>
                <span className="asset-search-edu-card-tag">{topic.tag}</span>
              </article>
            </BorderGlow>
          ))}
        </div>
      </section>
    </div>
  );
}
