import React from "react";
import DotField from "./DotField";
import BorderGlow from "./BorderGlow";
import "./LandingPage.css";

export default function LandingPage({ onNavigate, isAuthenticated, onGoDashboard }) {
  return (
    <div className="landing-page">
      {/* Full Screen Background */}
      <div className="dotfield-background">
        <DotField
          dotRadius={2.5}
          dotSpacing={16}
          bulgeStrength={60}
          glowRadius={250}
          sparkle={true}
          waveAmplitude={0}
          gradientFrom="#8B5CF6"
          gradientTo="#D8B4FE"
          glowColor="rgba(139, 92, 246, 0.4)"
        />
      </div>

      {/* Navigation Bar */}
      <nav className="landing-nav">
        <div className="nav-logo">
          <span className="logo-icon">⬡</span>
          <span className="logo-text">SynthCrypto</span>
        </div>
        <div className="nav-actions">
          <button
            className="btn-secondary purple-outline nav-btn"
            onClick={() => onNavigate("about")}
            style={{ border: 'none', background: 'transparent' }}
          >
            About
          </button>
          {isAuthenticated ? (
            <>
              <button
                className="btn-secondary purple-outline nav-btn"
                onClick={() => onNavigate("learn")}
                style={{ border: 'none', background: 'transparent' }}
              >
                Learn Trading
              </button>
              <button
                className="btn-primary purple-btn nav-btn"
                onClick={onGoDashboard}
              >
                Portfolio
              </button>
            </>
          ) : (
            <>
              <button
                className="btn-secondary purple-outline nav-btn"
                onClick={() => onNavigate("auth")}
              >
                Log In
              </button>
              <button
                className="btn-primary purple-btn nav-btn"
                onClick={() => onNavigate("auth")}
              >
                Sign Up
              </button>
            </>
          )}
        </div>
      </nav>

      {/* Hero Section */}
      <main className="landing-main">
        <div className="landing-hero-content">
          <h1>Learn Crypto Trading Without Risking Real Money</h1>
          <p>
            Practice crypto trading with live market simulations, virtual currency, and real-time charts in a safe learning environment.
          </p>
          <div className="hero-buttons">
            {isAuthenticated ? (
              <button
                className="btn-primary purple-btn hero-btn"
                onClick={onGoDashboard}
              >
                Go to Portfolio
              </button>
            ) : (
              <>
                <button
                  className="btn-primary purple-btn hero-btn"
                  onClick={() => onNavigate("auth")}
                >
                  Start Free
                </button>
                <button
                  className="btn-secondary purple-outline hero-btn"
                  onClick={() => onNavigate("learn")}
                >
                  Learn Trading
                </button>
              </>
            )}
          </div>
        </div>

        <section className="about-section" style={{ marginTop: '120px', maxWidth: '1000px', margin: '120px auto 40px auto', textAlign: 'left' }}>
          <div style={{ marginBottom: '40px', textAlign: 'center' }}>
            <p style={{ color: '#D8B4FE', fontWeight: 900, textTransform: 'uppercase', letterSpacing: '1px', fontSize: '14px', marginBottom: '16px' }}>About SynthCrypto</p>
            <h2 style={{ color: '#f8fafc', fontSize: '36px', fontWeight: 900, marginBottom: '20px' }}>Practical crypto education through risk-free simulation</h2>
            <p style={{ color: '#94a3b8', fontSize: '18px', maxWidth: '800px', margin: '0 auto', lineHeight: 1.6 }}>
              Our platform bridges the gap between trading theory and real-world market experience with virtual trades, market movement, and learning tools.
            </p>
          </div>

          <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(300px, 1fr))', gap: '24px' }}>
            <BorderGlow
              glowColor="270 70 75"
              backgroundColor="rgba(15, 10, 25, 0.7)"
              borderRadius={16}
              glowRadius={32}
              glowIntensity={1.2}
              coneSpread={20}
              colors={['#c084fc', '#a78bfa', '#7c3aed']}
            >
              <article style={{ padding: '32px' }}>
                <span style={{ display: 'inline-block', padding: '6px 12px', background: 'rgba(139, 92, 246, 0.1)', color: '#D8B4FE', borderRadius: '8px', fontSize: '12px', fontWeight: 900, marginBottom: '20px' }}>Mission</span>
                <h3 style={{ color: '#f8fafc', fontSize: '24px', marginBottom: '16px' }}>Our Mission</h3>
                <p style={{ color: '#94a3b8', lineHeight: 1.7, fontSize: '16px' }}>
                  Our mission is to make crypto trading education simple, practical, and risk-free through real-time simulation and interactive learning. We aim to empower users with the knowledge, confidence, and experience needed to understand trading in a safe and engaging environment.
                </p>
              </article>
            </BorderGlow>

            <BorderGlow
              glowColor="270 70 75"
              backgroundColor="rgba(15, 10, 25, 0.7)"
              borderRadius={16}
              glowRadius={32}
              glowIntensity={1.2}
              coneSpread={20}
              colors={['#c084fc', '#a78bfa', '#7c3aed']}
            >
              <article style={{ padding: '32px' }}>
                <span style={{ display: 'inline-block', padding: '6px 12px', background: 'rgba(139, 92, 246, 0.1)', color: '#D8B4FE', borderRadius: '8px', fontSize: '12px', fontWeight: 900, marginBottom: '20px' }}>Vision</span>
                <h3 style={{ color: '#f8fafc', fontSize: '24px', marginBottom: '16px' }}>Why We Built This</h3>
                <p style={{ color: '#94a3b8', lineHeight: 1.7, fontSize: '16px' }}>
                  We believe crypto education should be accessible to everyone. Our platform bridges the gap between theory and real-world market experience by allowing users to practice trading strategies in a fully simulated environment.
                </p>
              </article>
            </BorderGlow>
          </div>
        </section>
      </main>
    </div>
  );
}
