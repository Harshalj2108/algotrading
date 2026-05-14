import React from "react";
import DotField from "./DotField";
import "./LandingPage.css";

export default function LandingPage({ onNavigate }) {
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
          </div>
        </div>
      </main>
    </div>
  );
}
