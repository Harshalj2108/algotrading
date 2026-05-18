import { useState, useEffect, useRef } from 'react';
import { SIMULATOR_URL } from '../config';

const POPULAR_CRYPTO = ['BTC/USDT', 'ETH/USDT', 'SOL/USDT', 'BNB/USDT', 'XRP/USDT', 'ADA/USDT', 'DOGE/USDT', 'AVAX/USDT'];
const POPULAR_STOCKS = ['AAPL', 'MSFT', 'GOOGL', 'AMZN', 'TSLA', 'META', 'NVDA', 'RELIANCE.NS'];

export default function AssetSearch({ assetClass, onSelect }) {
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

  const eduTopics = [
    { term: "Stop Loss", desc: "Automatically closes a trade to limit losses when price moves against the position." },
    { term: "Take Profit (TP)", desc: "Automatically closes a trade when a target profit level is reached." },
    { term: "Market Order", desc: "Executes instantly at the best available market price." },
    { term: "Limit Order", desc: "Executes only at a specific price or better." },
    { term: "Stop Market Order", desc: "Triggers a market order once a stop price is reached." },
    { term: "Stop Limit Order", desc: "Triggers a limit order after the stop price is hit." },
    { term: "Long Position", desc: "Betting that the price will go up." },
    { term: "Short Position", desc: "Betting that the price will go down." },
    { term: "Leverage", desc: "Using borrowed capital to increase trade exposure." },
    { term: "PnL", desc: "Profit and Loss of a trade or portfolio." }
  ];

  return (
    <div style={{ display: 'flex', flexDirection: 'row', flexWrap: 'wrap', gap: '40px', padding: '0 40px 40px', maxWidth: '1200px', margin: '0 auto', color: '#d1d4dc' }}>
      {/* Left Column: Search */}
      <div style={{ flex: '1 1 400px', maxWidth: '600px' }}>
        <h2 style={{ fontSize: '24px', marginBottom: '8px', color: '#fff', fontWeight: 700 }}>
          {assetClass === 'crypto' ? '₿ Live Crypto' : '📈 Live Stocks'}
        </h2>
        <p style={{ color: '#787b86', fontSize: '13px', marginBottom: '20px' }}>
          Search for a {assetClass === 'crypto' ? 'cryptocurrency pair' : 'stock ticker'} to view live charts
        </p>

        <div style={{ position: 'relative' }}>
          <input
            ref={inputRef}
            type="text"
            style={{
              width: '100%',
              fontSize: '18px',
              padding: '16px 50px 16px 16px',
              background: 'rgba(19, 23, 34, 0.9)',
              border: '1px solid rgba(255,255,255,0.1)',
              borderRadius: '12px',
              color: '#d1d4dc',
              outline: 'none',
              transition: 'border-color 0.2s, box-shadow 0.2s',
              boxSizing: 'border-box',
            }}
            onFocus={e => {
              e.target.style.borderColor = 'rgba(38,166,154,0.5)';
              e.target.style.boxShadow = '0 0 0 3px rgba(38,166,154,0.1)';
            }}
            onBlur={e => {
              e.target.style.borderColor = 'rgba(255,255,255,0.1)';
              e.target.style.boxShadow = 'none';
            }}
            placeholder={assetClass === 'crypto' ? 'Search (e.g. BTC, ETH, SOL)...' : 'Search (e.g. AAPL, TSLA, MSFT)...'}
            value={query}
            onChange={handleQueryChange}
          />
          <div style={{ position: 'absolute', right: '16px', top: '50%', transform: 'translateY(-50%)', color: '#787b86', fontSize: '14px' }}>
            {loading ? '⏳' : '🔍'}
          </div>
        </div>

        {/* Error state */}
        {error && (
          <div style={{ marginTop: '12px', padding: '12px', background: 'rgba(239,83,80,0.1)', border: '1px solid rgba(239,83,80,0.2)', borderRadius: '8px', color: '#ef5350', fontSize: '13px' }}>
            {error}
          </div>
        )}

        {/* Search results */}
        {results.length > 0 && (
          <div style={{
            marginTop: '10px',
            background: 'rgba(19, 23, 34, 0.95)',
            border: '1px solid rgba(255,255,255,0.08)',
            borderRadius: '12px',
            overflow: 'hidden',
            maxHeight: '400px',
            overflowY: 'auto',
          }}>
            {results.map((r, i) => (
              <div
                key={i}
                onClick={() => handleSelect(r.symbol)}
                style={{
                  padding: '14px 16px',
                  borderBottom: i < results.length - 1 ? '1px solid rgba(255,255,255,0.04)' : 'none',
                  cursor: 'pointer',
                  transition: 'background 0.15s',
                  display: 'flex',
                  alignItems: 'center',
                  justifyContent: 'space-between',
                }}
                onMouseOver={e => e.currentTarget.style.background = 'rgba(38,166,154,0.08)'}
                onMouseOut={e => e.currentTarget.style.background = 'transparent'}
              >
                <div style={{ display: 'flex', flexDirection: 'column' }}>
                  <div style={{ fontWeight: 600, fontSize: '15px', color: '#fff' }}>
                    {r.symbol} {r.exchange ? <span style={{ fontSize: '12px', color: '#787b86', marginLeft: '6px' }}>({r.exchange})</span> : null}
                  </div>
                  {r.name && r.name !== r.symbol && (
                    <div style={{ color: '#787b86', fontSize: '12px', marginTop: '2px' }}>{r.name}</div>
                  )}
                </div>
                <div style={{ color: '#787b86', fontSize: '12px' }}>{r.type === 'crypto' ? '🪙' : '📊'} {r.type}</div>
              </div>
            ))}
          </div>
        )}

        {/* No results */}
        {!loading && query.length >= 2 && results.length === 0 && !error && (
          <div style={{ marginTop: '20px', color: '#787b86', textAlign: 'center', padding: '20px' }}>
            No results found for "{query}"
          </div>
        )}

        {/* Popular picks (shown when no query) */}
        {query.length < 2 && (
          <div style={{ marginTop: '24px' }}>
            <div style={{ color: '#787b86', fontSize: '12px', textTransform: 'uppercase', letterSpacing: '1px', marginBottom: '12px' }}>
              Popular {assetClass === 'crypto' ? 'Pairs' : 'Tickers'}
            </div>
            <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fill, minmax(120px, 1fr))', gap: '8px' }}>
              {popularItems.map(sym => (
                <div
                  key={sym}
                  onClick={() => handleSelect(sym)}
                  style={{
                    padding: '12px',
                    background: 'rgba(19, 23, 34, 0.8)',
                    border: '1px solid rgba(255,255,255,0.06)',
                    borderRadius: '10px',
                    cursor: 'pointer',
                    textAlign: 'center',
                    fontSize: '13px',
                    fontWeight: 600,
                    color: '#d1d4dc',
                    transition: 'all 0.15s',
                  }}
                  onMouseOver={e => {
                    e.currentTarget.style.background = 'rgba(38,166,154,0.1)';
                    e.currentTarget.style.borderColor = 'rgba(38,166,154,0.3)';
                    e.currentTarget.style.color = '#26a69a';
                  }}
                  onMouseOut={e => {
                    e.currentTarget.style.background = 'rgba(19, 23, 34, 0.8)';
                    e.currentTarget.style.borderColor = 'rgba(255,255,255,0.06)';
                    e.currentTarget.style.color = '#d1d4dc';
                  }}
                >
                  {sym}
                </div>
              ))}
            </div>
          </div>
        )}
      </div>

      {/* Right Column: Educational */}
      <div style={{ flex: '1 1 400px' }}>
        <h2 style={{ fontSize: '20px', marginBottom: '16px', color: '#fff', fontWeight: 600 }}>Trading Basics</h2>
        <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fill, minmax(250px, 1fr))', gap: '16px', maxHeight: '70vh', overflowY: 'auto', paddingRight: '8px' }}>
          {eduTopics.map(topic => (
            <div key={topic.term} style={{ background: 'rgba(19, 23, 34, 0.6)', border: '1px solid rgba(255,255,255,0.06)', borderRadius: '12px', padding: '16px' }}>
              <h3 style={{ fontSize: '15px', color: '#26a69a', marginBottom: '8px', fontWeight: 600 }}>{topic.term}</h3>
              <p style={{ fontSize: '13px', color: '#787b86', lineHeight: 1.5, margin: 0 }}>{topic.desc}</p>
            </div>
          ))}
        </div>
      </div>
    </div>
  );
}

