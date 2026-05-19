import { useState, useEffect, useRef } from 'react';
import { SIMULATOR_URL } from '../config';
import BorderGlow from './BorderGlow';
import LanguageSelector from './LanguageSelector';
import { useI18n } from '../i18n/I18nContext';
import './AssetSearch.css';

const POPULAR_CRYPTO = ['BTC/USDT', 'ETH/USDT', 'SOL/USDT', 'BNB/USDT', 'XRP/USDT', 'ADA/USDT', 'DOGE/USDT', 'AVAX/USDT'];
const POPULAR_STOCKS = ['AAPL', 'MSFT', 'GOOGL', 'AMZN', 'TSLA', 'META', 'NVDA', 'RELIANCE.NS'];

function EduIcon({ name }) {
  const paths = {
    shield: <><path d="M12 2.8 20 6v5.8c0 5-3.1 8.4-8 9.8-4.9-1.4-8-4.8-8-9.8V6z" /><path d="m8.5 12.2 2.2 2.2 4.8-5" /></>,
    target: <><circle cx="12" cy="12" r="9" /><circle cx="12" cy="12" r="5" /><circle cx="12" cy="12" r="1" /></>,
    bolt: <><path d="M13 2 3 14h9l-1 8 10-12h-9l1-8z" /></>,
    pin: <><path d="M12 17v5" /><path d="M9 2h6l-1 7h3l-5 5-5-5h3z" /></>,
    bell: <><path d="M18 8A6 6 0 0 0 6 8c0 7-3 9-3 9h18s-3-2-3-9" /><path d="M13.73 21a2 2 0 0 1-3.46 0" /></>,
    lock: <><rect x="3" y="11" width="18" height="11" rx="2" ry="2" /><path d="M7 11V7a5 5 0 0 1 10 0v4" /></>,
    trendUp: <><path d="m4 17 5.2-5.2 3.5 3.5L20 8" /><path d="M15 8h5v5" /></>,
    trendDown: <><path d="m4 7 5.2 5.2 3.5-3.5L20 16" /><path d="M15 16h5v-5" /></>,
    coins: <><ellipse cx="9" cy="7" rx="5" ry="2.7" /><path d="M4 7v4c0 1.5 2.2 2.7 5 2.7s5-1.2 5-2.7V7" /><path d="M10 17.3c.9.4 2 .7 3.2.7 2.8 0 5-1.2 5-2.7v-4" /><path d="M13.2 8.6c2.8 0 5 1.2 5 2.7s-2.2 2.7-5 2.7" /></>,
    scale: <><path d="M12 3v18" /><path d="M16 7 8 7" /><path d="m5 11 3-4 3 4" /><path d="m13 11 3-4 3 4" /><path d="M3 17h6" /><path d="M15 17h6" /></>,
  };
  return (
    <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="#a78bfa" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round">
      {paths[name] || paths.bolt}
    </svg>
  );
}

function SearchIcon() {
  return (
    <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
      <circle cx="11" cy="11" r="8" /><path d="m21 21-4.35-4.35" />
    </svg>
  );
}

function ResultTypeIcon({ type }) {
  if (type === 'crypto') {
    return (
      <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round">
        <circle cx="12" cy="12" r="10" /><path d="M10 8h3a2 2 0 0 1 0 4h-3" /><path d="M10 12h4a2 2 0 0 1 0 4h-4" /><path d="M11 6v2" /><path d="M13 6v2" /><path d="M11 16v2" /><path d="M13 16v2" />
      </svg>
    );
  }
  return (
    <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round">
      <path d="M3 3v18h18" /><path d="m7 14 4-4 4 4 5-5" />
    </svg>
  );
}

const EDU_TOPICS = [
  { term: "Stop Loss", termKey: "edu_stop_loss", iconName: "shield", tagKey: "tag_risk", descKey: "edu_stop_loss_desc" },
  { term: "Take Profit", termKey: "edu_take_profit", iconName: "target", tagKey: "tag_order", descKey: "edu_take_profit_desc" },
  { term: "Market Order", termKey: "edu_market_order", iconName: "bolt", tagKey: "tag_order", descKey: "edu_market_order_desc" },
  { term: "Limit Order", termKey: "edu_limit_order", iconName: "pin", tagKey: "tag_order", descKey: "edu_limit_order_desc" },
  { term: "Stop Market Order", termKey: "edu_stop_market", iconName: "bell", tagKey: "tag_advanced_order", descKey: "edu_stop_market_desc" },
  { term: "Stop Limit Order", termKey: "edu_stop_limit", iconName: "lock", tagKey: "tag_advanced_order", descKey: "edu_stop_limit_desc" },
  { term: "Long Position", termKey: "edu_long", iconName: "trendUp", tagKey: "tag_position", descKey: "edu_long_desc" },
  { term: "Short Position", termKey: "edu_short", iconName: "trendDown", tagKey: "tag_position", descKey: "edu_short_desc" },
  { term: "PnL", termKey: "edu_pnl", iconName: "coins", tagKey: "tag_metrics", descKey: "edu_pnl_desc" },
  { term: "Leverage", termKey: "edu_leverage", iconName: "scale", tagKey: "tag_advanced", descKey: "edu_leverage_desc" }
];

export default function AssetSearch({ assetClass, onSelect, onBack }) {
  const { t } = useI18n();
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
            {t('dash_back')}
          </button>
        )}
        <div style={{ display: 'flex', alignItems: 'center', gap: '10px' }}>
          <LanguageSelector />
          <div className="asset-search-title-badge">
            <span className="badge-dot" />
            {isCrypto ? t('live_crypto') : t('live_stocks')}
          </div>
        </div>
      </div>

      {/* ── Header ── */}
      <section className="asset-search-header">
        <p className="search-eyebrow">
          {isCrypto ? t('search_crypto_eyebrow') : t('search_stocks_eyebrow')}
        </p>
        <h1>{isCrypto ? t('search_title_crypto') : t('search_title_stocks')}</h1>
        <p className="search-subtitle">
          {isCrypto ? t('search_sub_crypto') : t('search_sub_stocks')}
        </p>
      </section>

      {/* ── Search Input ── */}
      <div className="asset-search-input-wrapper">
        <div className="asset-search-input-container">
          <span className="asset-search-input-icon"><SearchIcon /></span>
          <input
            ref={inputRef}
            id="asset-search-input"
            type="text"
            className="asset-search-input"
            placeholder={isCrypto
              ? t('search_placeholder_crypto')
              : t('search_placeholder_stocks')
            }
            value={query}
            onChange={handleQueryChange}
            autoComplete="off"
          />
          {loading && <div className="asset-search-input-spinner" />}
          {!loading && <span className="asset-search-input-hint">Ctrl+K</span>}
        </div>

        {/* Error */}
        {error && (
          <div className="asset-search-error" id="asset-search-error">
            {error}
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
                  <ResultTypeIcon type={r.type} /> {r.type}
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
            {isCrypto ? t('popular_pairs') : t('popular_tickers')}
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
          <h2>{t('trading_concepts')}</h2>
          <p>{t('trading_concepts_sub')}</p>
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
                  <EduIcon name={topic.iconName} />
                </div>
                <h3>{t(topic.termKey)}</h3>
                <p>{t(topic.descKey)}</p>
                <span className="asset-search-edu-card-tag">{t(topic.tagKey)}</span>
              </article>
            </BorderGlow>
          ))}
        </div>
      </section>
    </div>
  );
}
