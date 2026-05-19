const express = require('express');
const Razorpay = require('razorpay');
const crypto = require('crypto');
const db = require('../db');
const { requireAuth } = require('../middleware/auth');

const router = express.Router();

// Initialize Razorpay with fallback dummy keys if not in .env yet
const razorpay = new Razorpay({
  key_id: process.env.RAZORPAY_KEY_ID || 'YOUR_RAZORPAY_KEY_ID',
  key_secret: process.env.RAZORPAY_SECRET || 'YOUR_RAZORPAY_SECRET',
});

// Route 1: Create Order
router.post('/create-order', requireAuth, async (req, res) => {
  try {
    const { sAmount } = req.body;

    if (!sAmount || typeof sAmount !== 'number' || sAmount <= 0) {
      return res.status(400).json({ error: 'Invalid S Amount' });
    }

    // Cap the maximum order amount to prevent abuse
    const MAX_ORDER_AMOUNT = 1000000; // 10,000 INR in paise
    if (sAmount > MAX_ORDER_AMOUNT) {
      return res.status(400).json({ error: 'Amount exceeds maximum allowed' });
    }

    // 100 S = 1 INR. Razorpay expects paise (1 INR = 100 paise).
    // INR = sAmount / 100
    // Paise = INR * 100 = (sAmount / 100) * 100 = sAmount
    // Thus, sAmount is exactly equal to the amount in paise!
    const options = {
      amount: Math.floor(sAmount), // Ensure integer paise
      currency: 'INR',
      receipt: `receipt_order_${Date.now()}`,
    };

    const order = await razorpay.orders.create(options);

    if (!order) {
      return res.status(500).json({ error: 'Failed to create Razorpay order' });
    }

    res.json({
      key_id: process.env.RAZORPAY_KEY_ID || 'YOUR_RAZORPAY_KEY_ID',
      amount: order.amount,
      currency: order.currency,
      order_id: order.id,
    });
  } catch (error) {
    console.error('Razorpay Create Order Error:', error);
    res.status(500).json({ error: 'Server error while creating order' });
  }
});

// Route 2: Verify Payment Signature and Update DB
router.post('/verify-payment', requireAuth, async (req, res) => {
  try {
    const { razorpay_order_id, razorpay_payment_id, razorpay_signature } = req.body;
    const userId = req.user.id;

    // Validate required fields
    if (!razorpay_order_id || !razorpay_payment_id || !razorpay_signature) {
      return res.status(400).json({ error: 'Missing payment verification fields' });
    }

    // Create expected signature using secret
    const secret = process.env.RAZORPAY_SECRET || 'YOUR_RAZORPAY_SECRET';
    const body = razorpay_order_id + '|' + razorpay_payment_id;
    const expectedSignature = crypto
      .createHmac('sha256', secret)
      .update(body.toString())
      .digest('hex');

    // Timing-safe comparison to prevent timing attacks
    const sigBuffer = Buffer.from(razorpay_signature, 'utf8');
    const expectedBuffer = Buffer.from(expectedSignature, 'utf8');
    const isAuthentic = sigBuffer.length === expectedBuffer.length &&
      crypto.timingSafeEqual(sigBuffer, expectedBuffer);

    if (isAuthentic) {
      // CRITICAL: Fetch the order from Razorpay to get the server-verified amount
      // Never trust the client-provided sAmount for balance updates
      let amountToAdd;
      try {
        const order = await razorpay.orders.fetch(razorpay_order_id);
        if (order.status !== 'paid') {
          return res.status(400).json({ error: 'Order is not marked as paid' });
        }
        // order.amount is in paise, which equals our S-amount
        amountToAdd = order.amount;
      } catch (fetchErr) {
        console.error('Failed to fetch Razorpay order:', fetchErr);
        return res.status(500).json({ error: 'Could not verify order amount' });
      }

      await db.query(
        'UPDATE portfolios SET balance = balance + $1 WHERE user_id = $2',
        [amountToAdd, userId]
      );

      res.json({ success: true, message: 'Payment verified successfully', amount: amountToAdd });
    } else {
      res.status(400).json({ error: 'Invalid payment signature' });
    }
  } catch (error) {
    console.error('Razorpay Verification Error:', error);
    res.status(500).json({ error: 'Server error while verifying payment' });
  }
});

module.exports = router;
