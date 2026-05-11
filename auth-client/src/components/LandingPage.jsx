
import LightRays from './LightRays';
import CircularText from './CircularText';
import StarBorder from './StarBorder';
import './LandingPage.css';

export default function LandingPage({ onGetStarted }) {
  return (
    <div className="landing-page">
      <LightRays
        raysOrigin="top-center"
        raysColor="#26a69a"
        raysSpeed={1.5}
        lightSpread={0.8}
        rayLength={1.2}
        followMouse={true}
        mouseInfluence={0.1}
        noiseAmount={0.1}
        distortion={0.05}
        className="landing-rays"
      />
      <div className="top-left-brand">
        <div className="top-left-logo">⬡</div>
        <CircularText
          text="SYNTHCRYPTO*SIMULATOR*"
          onHover="speedUp"
          spinDuration={20}
          className="brand-circular-text"
        />
      </div>
      <div className="landing-content">
        <div className="brand-icon">⬡</div>
        <h1>SynthCrypto <span className="brand-tag">v3</span></h1>
        <h2>Phase 2 Live Market Simulator</h2>
        <p>Experience realistic market dynamics with our advanced simulation engines.</p>
        <StarBorder
          as="button"
          className="get-started-btn"
          onClick={onGetStarted}
          color="#26a69a"
        >
          Get Started
        </StarBorder>
      </div>
    </div>
  );
}
