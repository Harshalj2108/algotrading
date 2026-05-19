import LightRays from "./LightRays";
import heroAsset from "../assets/hero.png";
import DotField from "./DotField";
import BorderGlow from "./BorderGlow";
import LanguageSelector from "./LanguageSelector";
import { useI18n } from "../i18n/I18nContext";
import "./PublicPages.css";

const navItems = [
  { id: "home", label: "Home" },
  { id: "about", label: "About" },
];

const stats = [
  { label: "Virtual Trades", value: "1M+" },
  { label: "Learning Modules", value: "50+" },
  { label: "Supported Assets", value: "100+" },
];

const features = [
  {
    icon: "wallet",
    title: "Virtual Trading Wallet",
    description: "Every user receives virtual currency to practice trading safely.",
  },
  {
    icon: "candles",
    title: "Live Market Simulation",
    description: "Experience realistic crypto price movement using real-time market data.",
  },
  {
    icon: "graduation",
    title: "Interactive Learning",
    description: "Learn trading concepts, strategies, and technical analysis interactively.",
  },
  {
    icon: "gamepad",
    title: "Gamified Experience",
    description: "Track achievements, improve trading skills, and compete with yourself.",
  },
  {
    icon: "shield",
    title: "Risk-Free Environment",
    description: "Practice and learn without risking real money or assets.",
  },
];

const learningCategories = [
  {
    id: "crypto-basics",
    title: "Crypto Basics",
    icon: "bitcoin",
    description:
      "Cryptocurrency is a digital form of currency that operates on blockchain technology, a secure and transparent system that records transactions across a decentralized network. Unlike traditional currencies controlled by governments or banks, cryptocurrencies such as Bitcoin and Ethereum are powered by technology and market demand. Crypto trading involves buying and selling these digital assets based on price movements, making it important for users to understand market trends, risk management, and trading strategies before participating.",
  },
  {
    id: "candlestick-patterns",
    title: "Candlestick Patterns",
    icon: "candles",
    description:
      "Candlestick patterns are visual representations of price movements used by traders to analyze market trends and predict possible future price action. Each candlestick displays opening price, closing price, highest price, and lowest price within a specific time period. Patterns such as Doji, Hammer, Engulfing, and Shooting Star help traders understand market sentiment, identify potential reversals, and make informed trading decisions.",
  },
  {
    id: "futures-trading",
    title: "Futures Trading",
    icon: "trend",
    description:
      "Futures trading is a type of trading where participants agree to buy or sell an asset at a predetermined price on a future date. In the crypto market, futures allow traders to speculate on whether the price of a cryptocurrency will rise or fall without actually owning the asset. Futures trading can amplify both profits and losses due to leverage, making risk management and market understanding extremely important for every trader.",
  },
  {
    id: "spot-trading",
    title: "Spot Trading",
    icon: "coins",
    description:
      "Spot trading is the simplest form of crypto trading where users buy or sell cryptocurrencies at the current market price for immediate settlement. In spot trading, traders directly own the digital asset they purchase, such as Bitcoin or Ethereum, and profits or losses depend on market price movements. It is widely considered a beginner-friendly trading method and helps users understand market behavior, timing, and investment strategies.",
  },
  {
    id: "risk-management",
    title: "Risk Management",
    icon: "shield",
    description:
      "Risk management is one of the most important aspects of successful trading, as it helps traders protect their capital from significant losses. It involves strategies such as setting stop-loss limits, managing trade size, diversifying investments, and avoiding emotional decision-making. Effective risk management allows traders to stay disciplined, minimize losses during market volatility, and improve long-term trading performance.",
  },
  {
    id: "technical-indicators",
    title: "Technical Indicators",
    icon: "activity",
    description:
      "Technical indicators are mathematical tools used by traders to analyze market trends, price movements, and trading opportunities based on historical data. Indicators such as RSI, MACD, Moving Averages, and Bollinger Bands help traders identify momentum, market strength, overbought or oversold conditions, and potential entry or exit points. By combining multiple indicators with proper analysis, traders can make more informed and strategic trading decisions.",
  },
];

const learningBenefits = [
  "Understanding crypto markets",
  "Reading trading charts",
  "Using technical indicators",
  "Managing trading risks",
  "Practicing trading strategies",
  "Analyzing market trends",
];

function IconMark({ name }) {
  const paths = {
    wallet: (
      <>
        <path d="M4 7.5h14.2a2.8 2.8 0 0 1 2.8 2.8v7.2a2.8 2.8 0 0 1-2.8 2.8H5.8A3.8 3.8 0 0 1 2 16.5v-9A3.8 3.8 0 0 1 5.8 3.7h11.4" />
        <path d="M17 13h4" />
      </>
    ),
    candles: (
      <>
        <path d="M6 4v16" />
        <path d="M18 4v16" />
        <path d="M4 8h4v7H4z" />
        <path d="M16 10h4v5h-4z" />
        <path d="M12 6v12" />
        <path d="M10 11h4v4h-4z" />
      </>
    ),
    graduation: (
      <>
        <path d="M2.5 8.5 12 4l9.5 4.5L12 13z" />
        <path d="M6.5 10.5v5.2c1.8 1.4 3.7 2.1 5.5 2.1s3.7-.7 5.5-2.1v-5.2" />
      </>
    ),
    gamepad: (
      <>
        <path d="M8 10.5v4" />
        <path d="M6 12.5h4" />
        <path d="M15.5 12h.1" />
        <path d="M18 14h.1" />
        <path d="M7.2 7.5h9.6a4.2 4.2 0 0 1 4 3.2l1 4a3.2 3.2 0 0 1-5.4 3.1l-1.3-1.3H8.9l-1.3 1.3a3.2 3.2 0 0 1-5.4-3.1l1-4a4.2 4.2 0 0 1 4-3.2Z" />
      </>
    ),
    shield: (
      <>
        <path d="M12 2.8 20 6v5.8c0 5-3.1 8.4-8 9.8-4.9-1.4-8-4.8-8-9.8V6z" />
        <path d="m8.5 12.2 2.2 2.2 4.8-5" />
      </>
    ),
    bitcoin: (
      <>
        <path d="M10 5v14" />
        <path d="M14 5v14" />
        <path d="M8 7h5.6a3.1 3.1 0 0 1 0 6H8" />
        <path d="M8 13h6.2a3 3 0 0 1 0 6H8" />
      </>
    ),
    trend: (
      <>
        <path d="m4 17 5.2-5.2 3.5 3.5L20 8" />
        <path d="M15 8h5v5" />
      </>
    ),
    coins: (
      <>
        <ellipse cx="9" cy="7" rx="5" ry="2.7" />
        <path d="M4 7v4c0 1.5 2.2 2.7 5 2.7s5-1.2 5-2.7V7" />
        <path d="M10 17.3c.9.4 2 .7 3.2.7 2.8 0 5-1.2 5-2.7v-4" />
        <path d="M13.2 8.6c2.8 0 5 1.2 5 2.7s-2.2 2.7-5 2.7" />
      </>
    ),
    activity: (
      <>
        <path d="M3 12h4l2.2-6 4 12 2.3-6H21" />
      </>
    ),
  };

  return (
    <svg className="public-icon" viewBox="0 0 24 24" aria-hidden="true">
      {paths[name] || paths.activity}
    </svg>
  );
}

function PublicNav({ currentPage, onNavigate, onGetStarted, onSignIn, isAuthenticated, onGoDashboard }) {
  return (
    <header className="public-nav">
      <button className="public-brand" type="button" onClick={() => onNavigate("home")}>
        <span className="public-brand-mark">SC</span>
        <span>SynthCrypto</span>
      </button>

      <nav className="public-nav-links" aria-label="Public pages">
        {navItems.map((item) => (
          <button
            key={item.id}
            type="button"
            className={currentPage === item.id ? "active" : ""}
            onClick={() => onNavigate(item.id)}
          >
            {item.label}
          </button>
        ))}
      </nav>

      <div className="public-nav-actions">
        <LanguageSelector />
        {isAuthenticated ? (
          <button className="public-nav-cta" type="button" onClick={onGoDashboard}>
            Portfolio
          </button>
        ) : (
          <>
            <button className="public-nav-signin" type="button" onClick={onSignIn}>
              Sign In
            </button>
            <button className="public-nav-cta" type="button" onClick={onGetStarted}>
              Start Free
            </button>
          </>
        )}
      </div>
    </header>
  );
}

function PublicShell({ currentPage, onNavigate, onGetStarted, onSignIn, isAuthenticated, onGoDashboard, children }) {
  return (
    <div className="public-shell">
      <div className="public-dotfield-bg">
        <DotField
          dotRadius={2}
          dotSpacing={16}
          bulgeStrength={60}
          glowRadius={220}
          sparkle={true}
          waveAmplitude={0}
          gradientFrom="#8B5CF6"
          gradientTo="#D8B4FE"
          glowColor="rgba(139, 92, 246, 0.4)"
        />
      </div>
      <div className="public-shell-content">
        <PublicNav 
          currentPage={currentPage} 
          onNavigate={onNavigate} 
          onGetStarted={onGetStarted} 
          onSignIn={onSignIn} 
          isAuthenticated={isAuthenticated}
          onGoDashboard={onGoDashboard}
        />
        {children}
      </div>
    </div>
  );
}

function MarketVisual() {
  const candles = [
    ["up", 36, 54, 74],
    ["up", 30, 48, 62],
    ["down", 46, 66, 52],
    ["up", 28, 56, 78],
    ["down", 44, 76, 58],
    ["up", 22, 50, 88],
    ["up", 18, 46, 68],
    ["down", 40, 72, 50],
    ["up", 26, 60, 80],
  ];

  return (
    <div className="market-visual" aria-label="Animated crypto market preview">
      <div className="market-toolbar">
        <span>BTC/USD</span>
        <strong>S68,423.50</strong>
        <em>+2.34%</em>
      </div>
      <div className="market-grid">
        <div className="market-line market-line-1" />
        <div className="market-line market-line-2" />
        <div className="market-line market-line-3" />
        <div className="market-line market-line-4" />
        <div className="market-candles">
          {candles.map(([type, top, height, wick], index) => (
            <span
              className={`market-candle ${type}`}
              style={{
                "--top": `${top}px`,
                "--height": `${height}px`,
                "--wick": `${wick}px`,
                "--delay": `${index * 90}ms`,
              }}
              key={`${type}-${index}`}
            />
          ))}
        </div>
      </div>
      <img className="market-asset" src={heroAsset} alt="" aria-hidden="true" />
    </div>
  );
}

export function HomePage({ onNavigate, onGetStarted, onSignIn }) {
  const { t } = useI18n();
  return (
    <PublicShell currentPage="home" onNavigate={onNavigate} onGetStarted={onGetStarted} onSignIn={onSignIn}>
      <main className="public-main">
        <section className="public-hero">
          <LightRays
            raysOrigin="top-center"
            raysColor="#22c55e"
            raysSpeed={1.1}
            lightSpread={0.7}
            rayLength={1}
            followMouse={true}
            mouseInfluence={0.06}
            noiseAmount={0.08}
            distortion={0.04}
            className="public-rays"
          />

          <div className="public-hero-copy">
            <p className="public-eyebrow">EdTech meets FinTech simulation</p>
            <h1>{t('hero_title')}</h1>
            <p className="public-hero-subtitle">
              {t('hero_sub')}
            </p>
            <div className="public-hero-actions">
              <button className="public-primary-btn" type="button" onClick={onGetStarted}>
                {t('btn_start_free')}
              </button>
              <button className="public-secondary-btn" type="button" onClick={() => onNavigate("learn")}>
                {t('btn_learn')}
              </button>
            </div>
          </div>

          <MarketVisual />
        </section>

        <section className="public-stats" aria-label="Platform stats">
          {stats.map((stat) => (
            <BorderGlow
              key={stat.label}
              glowColor="270 70 75"
              backgroundColor="rgba(15, 10, 25, 0.7)"
              borderRadius={12}
              glowRadius={32}
              glowIntensity={1.2}
              coneSpread={20}
              colors={['#c084fc', '#a78bfa', '#7c3aed']}
            >
              <div className="public-stat">
                <strong>{stat.value}</strong>
                <span>{stat.label}</span>
              </div>
            </BorderGlow>
          ))}
        </section>

        <section className="public-section">
          <div className="public-section-heading">
            <p className="public-eyebrow">{t('section_eyebrow')}</p>
            <h2>{t('section_title')}</h2>
            <p>
              {t('section_desc')}
            </p>
          </div>

          <div className="feature-grid">
            {features.map((feature) => (
              <BorderGlow
                key={feature.title}
                glowColor="270 70 75"
                backgroundColor="rgba(15, 10, 25, 0.7)"
                borderRadius={12}
                glowRadius={28}
                glowIntensity={1.1}
                coneSpread={22}
                colors={['#c084fc', '#a78bfa', '#7c3aed']}
              >
                <article className="feature-panel">
                  <IconMark name={feature.icon} />
                  <h3>{feature.title}</h3>
                  <p>{feature.description}</p>
                </article>
              </BorderGlow>
            ))}
          </div>
        </section>

        <section className="public-section-heading" style={{ marginTop: '80px' }}>
          <p className="public-eyebrow">{t('about_eyebrow')}</p>
          <h2>{t('about_title')}</h2>
          <p>
            {t('about_desc')}
          </p>
        </section>

        <section className="about-grid">
          <BorderGlow
            glowColor="270 70 75"
            backgroundColor="rgba(15, 10, 25, 0.7)"
            borderRadius={12}
            glowRadius={32}
            glowIntensity={1.2}
            coneSpread={20}
            colors={['#c084fc', '#a78bfa', '#7c3aed']}
          >
            <article className="about-panel">
              <span className="about-label">{t('mission_label')}</span>
              <h2>{t('mission_title')}</h2>
              <p>
                {t('mission_desc')}
              </p>
            </article>
          </BorderGlow>

          <BorderGlow
            glowColor="270 70 75"
            backgroundColor="rgba(15, 10, 25, 0.7)"
            borderRadius={12}
            glowRadius={32}
            glowIntensity={1.2}
            coneSpread={20}
            colors={['#c084fc', '#a78bfa', '#7c3aed']}
          >
            <article className="about-panel">
              <span className="about-label">{t('vision_label')}</span>
              <h2>{t('vision_title')}</h2>
              <p>
                {t('vision_desc')}
              </p>
            </article>
          </BorderGlow>
        </section>

        <BorderGlow
          glowColor="0 70 65"
          backgroundColor="rgba(30, 20, 20, 0.8)"
          borderRadius={12}
          glowRadius={28}
          glowIntensity={1.0}
          coneSpread={20}
          colors={['#fca5a5', '#f87171', '#ef4444']}
        >
          <section className="disclaimer-section">
            <h2>{t('disclaimer_title')}</h2>
            <p>
              {t('disclaimer_text')}
            </p>
          </section>
        </BorderGlow>
      </main>
    </PublicShell>
  );
}

export function AboutUsPage({ onNavigate, onGetStarted, onSignIn, isAuthenticated, onGoDashboard }) {
  return (
    <PublicShell currentPage="about" onNavigate={onNavigate} onGetStarted={onGetStarted} onSignIn={onSignIn} isAuthenticated={isAuthenticated} onGoDashboard={onGoDashboard}>
      <main className="public-main public-page-main">
        <section className="public-page-header">
          <p className="public-eyebrow">About SynthCrypto</p>
          <h1>Practical crypto education through risk-free simulation</h1>
          <p>
            Our platform bridges the gap between trading theory and real-world market experience with virtual trades, market movement, and learning tools.
          </p>
        </section>

        <section className="about-grid">
          <BorderGlow
            glowColor="270 70 75"
            backgroundColor="rgba(15, 10, 25, 0.7)"
            borderRadius={12}
            glowRadius={32}
            glowIntensity={1.2}
            coneSpread={20}
            colors={['#c084fc', '#a78bfa', '#7c3aed']}
          >
            <article className="about-panel">
              <span className="about-label">Mission</span>
              <h2>Our Mission</h2>
              <p>
                Our mission is to make crypto trading education simple, practical, and risk-free through real-time simulation and interactive learning. We aim to empower users with the knowledge, confidence, and experience needed to understand trading in a safe and engaging environment.
              </p>
            </article>
          </BorderGlow>

          <BorderGlow
            glowColor="270 70 75"
            backgroundColor="rgba(15, 10, 25, 0.7)"
            borderRadius={12}
            glowRadius={32}
            glowIntensity={1.2}
            coneSpread={20}
            colors={['#c084fc', '#a78bfa', '#7c3aed']}
          >
            <article className="about-panel">
              <span className="about-label">Vision</span>
              <h2>Why We Built This</h2>
              <p>
                We believe crypto education should be accessible to everyone. Our platform bridges the gap between theory and real-world market experience by allowing users to practice trading strategies in a fully simulated environment.
              </p>
            </article>
          </BorderGlow>
        </section>

        <BorderGlow
          glowColor="0 70 65"
          backgroundColor="rgba(30, 20, 20, 0.8)"
          borderRadius={12}
          glowRadius={28}
          glowIntensity={1.0}
          coneSpread={20}
          colors={['#fca5a5', '#f87171', '#ef4444']}
        >
          <section className="disclaimer-section">
            <h2>Disclaimer</h2>
            <p>
              This platform is designed solely for educational and simulation purposes. No real money or actual cryptocurrency trading is involved on the platform. All trades are virtual and intended to help users learn and practice trading concepts in a safe environment. The content, tools, and simulations provided do not constitute financial, investment, or trading advice.
            </p>
          </section>
        </BorderGlow>
      </main>
    </PublicShell>
  );
}

export function LearnTradingPage({ onNavigate, onGetStarted, onSignIn, isAuthenticated, onGoDashboard }) {
  const { t } = useI18n();
  return (
    <PublicShell currentPage="learn" onNavigate={onNavigate} onGetStarted={onGetStarted} onSignIn={onSignIn} isAuthenticated={isAuthenticated} onGoDashboard={onGoDashboard}>
      <main className="public-main public-page-main">
        <section className="public-page-header">
          <p className="public-eyebrow">{t('learn_eyebrow')}</p>
          <h1>{t('learn_title')}</h1>
          <p>
            {t('learn_sub')}
          </p>
        </section>

        <section className="learn-grid">
          {learningCategories.map((category) => (
            <BorderGlow
              key={category.id}
              glowColor="270 70 75"
              backgroundColor="rgba(15, 10, 25, 0.7)"
              borderRadius={12}
              glowRadius={28}
              glowIntensity={1.1}
              coneSpread={22}
              colors={['#c084fc', '#a78bfa', '#7c3aed']}
            >
              <article className="learn-panel">
                <IconMark name={category.icon} />
                <h2>{category.title}</h2>
                <p>{category.description}</p>
              </article>
            </BorderGlow>
          ))}
        </section>

        <BorderGlow
          glowColor="270 70 75"
          backgroundColor="rgba(15, 10, 25, 0.7)"
          borderRadius={12}
          glowRadius={32}
          glowIntensity={1.2}
          coneSpread={20}
          colors={['#c084fc', '#a78bfa', '#7c3aed']}
        >
          <section className="learning-benefits">
            <div>
              <p className="public-eyebrow">{t('learn_benefits_eyebrow')}</p>
              <h2>{t('learn_benefits_title')}</h2>
            </div>
            <ul>
              {learningBenefits.map((benefit) => (
                <li key={benefit}>{benefit}</li>
              ))}
            </ul>
          </section>
        </BorderGlow>

        <BorderGlow
          glowColor="270 70 75"
          backgroundColor="rgba(15, 10, 25, 0.7)"
          borderRadius={12}
          glowRadius={32}
          glowIntensity={1.2}
          coneSpread={20}
          colors={['#c084fc', '#a78bfa', '#7c3aed']}
        >
          <section className="learn-cta">
            <div>
              <h2>{t('learn_cta_title')}</h2>
              <p>{t('learn_cta_desc')}</p>
            </div>
            {isAuthenticated ? (
              <button className="public-primary-btn" type="button" onClick={onGoDashboard}>
                {t('btn_go_portfolio')}
              </button>
            ) : (
              <button className="public-primary-btn" type="button" onClick={onGetStarted}>
                {t('btn_start_free')}
              </button>
            )}
          </section>
        </BorderGlow>
      </main>
    </PublicShell>
  );
}
