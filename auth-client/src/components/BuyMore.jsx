import { useState } from 'react';
import Orb from './Orb';
import './BuyMore.css';

const AUTH_SERVER = "http://localhost:3001";

export default function BuyMore({ onBack }) {
  const [loading, setLoading] = useState(false);
  const [message, setMessage] = useState("");
  const [sAmount, setSAmount] = useState(10000);

  // 100 S = 1 INR
  const inrCost = sAmount / 100;

  const loadRazorpayScript = () => {
    return new Promise((resolve) => {
      const script = document.createElement("script");
      script.src = "https://checkout.razorpay.com/v1/checkout.js";
      script.onload = () => resolve(true);
      script.onerror = () => resolve(false);
      document.body.appendChild(script);
    });
  };

  const handlePayment = async () => {
    setLoading(true);
    setMessage("");

    const res = await loadRazorpayScript();
    if (!res) {
      setMessage("Razorpay SDK failed to load. Are you online?");
      setLoading(false);
      return;
    }

    try {
      // 1. Create order on your backend
      const orderResponse = await fetch(`${AUTH_SERVER}/api/payments/create-order`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        credentials: "include",
        body: JSON.stringify({ sAmount }) 
      });
      const orderData = await orderResponse.json();

      if (!orderResponse.ok) {
        throw new Error(orderData.error || "Failed to create order");
      }

      // 2. Open Razorpay Checkout
      const options = {
        key: orderData.key_id, 
        amount: orderData.amount,
        currency: orderData.currency,
        name: "SynthCrypto",
        description: `${sAmount.toLocaleString()} S Virtual Currency`,
        order_id: orderData.order_id,
        handler: async function (response) {
          // 3. Verify Payment on Backend
          const verifyRes = await fetch(`${AUTH_SERVER}/api/payments/verify-payment`, {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            credentials: "include",
            body: JSON.stringify({
              razorpay_order_id: response.razorpay_order_id,
              razorpay_payment_id: response.razorpay_payment_id,
              razorpay_signature: response.razorpay_signature,
              sAmount: sAmount
            })
          });
          
          const verifyData = await verifyRes.json();
          if (verifyRes.ok) {
            setMessage(`✅ Payment Successful! ${sAmount.toLocaleString()} S added to your wallet.`);
          } else {
            setMessage("❌ Payment verification failed: " + verifyData.error);
          }
        },
        prefill: {
          name: "SynthCrypto User",
          email: "user@example.com",
          contact: "9999999999"
        },
        theme: {
          color: "#8B5CF6"
        }
      };

      const paymentObject = new window.Razorpay(options);
      paymentObject.open();

    } catch (err) {
      setMessage("Error: " + err.message);
    } finally {
      setLoading(false);
    }
  };

  return (
    <div className="buy-more-layout">
      <div className="buy-more-bg">
        <Orb hue={260} hoverIntensity={0.5} backgroundColor="#0a0a0a" />
      </div>
      
      <div className="buy-more-content">
        <button className="buy-more-back" onClick={onBack}>
          ← Back to Dashboard
        </button>

        <div className="buy-more-card">
          <h2>Top Up Simulator Wallet</h2>
          <p className="buy-more-sub">Add virtual currency to your paper trading account to test larger strategies.</p>
          
          <div className="buy-more-offer">
            <div className="offer-amount">{sAmount.toLocaleString()} <span>S</span></div>
            <div className="offer-price">₹{inrCost.toLocaleString()}</div>
          </div>

          <div className="slider-section">
            <input 
              type="range" 
              className="s-slider" 
              min="1000" 
              max="100000" 
              step="1000" 
              value={sAmount} 
              onChange={(e) => setSAmount(Number(e.target.value))} 
            />
            <div className="slider-presets">
              {[5000, 10000, 50000, 100000].map(val => (
                <button 
                  key={val}
                  className={`preset-btn ${sAmount === val ? 'active' : ''}`}
                  onClick={() => setSAmount(val)}
                >
                  {val >= 1000 ? `${val/1000}k` : val}
                </button>
              ))}
            </div>
          </div>

          <button 
            className={`buy-more-btn ${loading ? "loading" : ""}`} 
            onClick={handlePayment}
            disabled={loading}
          >
            {loading ? "Processing..." : `Pay ₹${inrCost.toLocaleString()} Securely`}
          </button>

          {message && <div className="buy-more-msg">{message}</div>}
          
          <p className="buy-more-disclaimer">
            * S is a virtual simulation currency intended solely for use within the SynthCrypto platform. It holds no real-world value.
          </p>
        </div>
      </div>
    </div>
  );
}
