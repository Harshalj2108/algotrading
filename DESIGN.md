---
design_tokens:
  colors:
    background:
      primary: "#0a0e17"
      card: "rgba(26, 29, 43, 0.72)"
      input: "rgba(19, 23, 34, 0.85)"
      input_focus: "rgba(19, 23, 34, 1)"
      toolbar: "#1e222d"
      trade_panel: "#1a1d2b"
    text:
      primary: "#d1d4dc"
      secondary: "#787b86"
      muted: "#4c5166"
      label: "#5d606b"
      inverse: "#ffffff"
    borders:
      subtle: "rgba(255, 255, 255, 0.06)"
      input: "rgba(42, 46, 57, 0.8)"
      input_hover: "#363a45"
      input_focus: "#2962ff"
    accents:
      teal: "#26a69a"
      teal_light: "#2bbfb3"
      blue: "#2962ff"
      purple: "#7c3aed"
      red: "#ef5350"
      orange: "#f38720"
    gradients:
      teal: "linear-gradient(135deg, #26a69a, #2bbfb3)"
      google: "linear-gradient(135deg, #4285f4, #5a9af4)"
      text: "linear-gradient(135deg, #d1d4dc, #787b86)"
      glow: "radial-gradient(circle, rgba(38, 166, 154, 0.3), transparent 70%)"
  typography:
    fonts:
      sans: "'Inter', -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif"
      mono: "'Courier New', monospace"
    sizes:
      xs: "10px"
      sm: "12px"
      base: "13px"
      md: "14px"
      lg: "16px"
      xl: "20px"
      xxl: "28px"
      hero: "48px"
    weights:
      light: 300
      regular: 400
      medium: 500
      semibold: 600
      bold: 700
      extrabold: 800
      black: 900
    letter_spacing:
      tight: "-1px"
      snug: "-0.5px"
      normal: "0px"
      wide: "0.6px"
      widest: "2px"
  radii:
    xs: "8px"
    sm: "10px"
    md: "12px"
    lg: "20px"
    pill: "9999px"
  shadows:
    card: "0 20px 60px rgba(0, 0, 0, 0.5)"
    teal: "0 8px 25px rgba(38, 166, 154, 0.35)"
    blue: "0 8px 25px rgba(41, 98, 255, 0.3)"
  effects:
    glass:
      card_blur: "blur(28px) saturate(1.6)"
      landing_blur: "blur(10px)"
      orb_blur: "blur(80px)"
  motion:
    durations:
      fast: "0.12s"
      normal: "0.2s"
      slow: "0.6s"
      ambient: "8s"
    animations:
      card_fade: "0.6s ease-out"
      icon_pulse: "3s ease-in-out infinite"
      orb_float_base: "8s ease-in-out infinite"
---

# Look and Feel

The application embodies a **Dark Mode Glassmorphic and Cyber-Financial** aesthetic. It strikes a balance between rigorous, highly data-dense trading interfaces and soft, ethereal visuals for an engaging user experience. 

## Glassmorphism & Depth
Cards and primary containers sit as glass-like panels over an infinite void. Utilizing heavily blurred backdrop filters (e.g., `blur(28px) saturate(1.6)`) layered over animated, glowing radial "orbs," the interface creates an atmospheric sense of deep space and suspended layers. Elements lack heavy borders; instead, ultra-thin subtle boundaries (`1px solid rgba(255, 255, 255, 0.05)`) paired with inset box-shadows keep components crisp and defined without feeling contained or boxed in.

## Color and Lighting
The core palette relies on deep abyssal blues and blacks (`#0a0e17` and variable low-opacity `#131722`) contrasted starkly by saturated colors that signify market states:
- **Teal / Green:** Signifies positive movement, action, and serves as the primary brand identity.
- **Red:** Used for negative price bounds, stoppages, and warnings.
- **Electric Blue:** Highlights interactive hover states and active button toggles.

Rather than entirely wrapping headers or logos in solid background colors, gradients are heavily utilized and often clipped to the text to create a metallic, lit-from-within effect. The ambient background "orbs" function as diffused lighting that constantly shifts.

## Typography
- **Inter** is the backbone for the entire interface, prized for legibility at the tiny sizes required by complex trading tools (often dipping to 9px or 10px on badges and toolbars). Font weights push extremes: hero titles use `extrabold (800)` for impact, while secondary text sits comfortably at `medium (500)`.
- **Monospace (Courier New)** is strictly reserved for data—current price feeds, OHLC readouts, and numerical values. This ensures tabular alignment, making the rapidly changing data easily scannable so numbers carry a distinct identity separated from UI text.

## Motion & Interaction
Motion is split into two distinct categories to handle the psychological demands of a trading simulator:
1. **Ambient Motion:** Slow, looping, relaxed background animations (8s-15s float loops) applied to ambient orbs and icon pulses. This keeps the environment feeling "living" and fluid even when the market is slow.
2. **Interactive Snappiness:** Micro-interactions on buttons, input fields, toolbars, and layout shifts are exceptionally fast (0.12s - 0.2s). The interface snaps to the user's intent immediately, mimicking the instant reflexes required in algorithmic and manual trading logic.

## Information Density & Layout
The layout changes states dramatically dependent on user scope:
- **Landing / Auth:** Cinematic, heavily centered setups with wide tracking, huge fonts, and expansive margins designed for impact and intrigue.
- **Dashboard / Simulator:** Switches to purely functional, high-density dashboard layouts. Uses strict Flexbox grid-packing, split column wrappers (like the 720px static left container combined with fluid right elements), narrow button groups, and tightly consolidated toolbars. Everything maximizes screen real-estate to ensure charts and numbers become the hero of the experience.