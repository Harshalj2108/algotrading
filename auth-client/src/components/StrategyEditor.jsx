/**
 * StrategyEditor.jsx
 * ──────────────────
 * Paste-and-run strategy panel for the TradeSeekho simulator.
 *
 * Props:
 *   apiBase   – e.g. "${SIMULATOR_URL}"
 *   token     – JWT bearer token from auth-server
 *   socket    – connected socket.io-client instance
 */

import { useState, useEffect, useRef, useCallback } from "react";
import StarBorder from "./StarBorder";
import { SIMULATOR_URL } from '../config';


// ─── tiny syntax-highlight tokeniser (no external dep) ────────────────────────
function tokenise(code) {
  const KW = new Set([
    "class","def","return","if","elif","else","for","while","in","not",
    "and","or","import","from","as","True","False","None","self","pass",
    "break","continue","raise","try","except","finally","with","lambda",
    "yield","async","await","is",
  ]);
  const tokens = [];
  const re = /("(?:[^"\\]|\\.)*"|'(?:[^'\\]|\\.)*'|#[^\n]*|[A-Za-z_]\w*|\d+\.?\d*|[^\w\s]|\s+)/g;
  let m;
  while ((m = re.exec(code)) !== null) {
    const t = m[0];
    let type = "plain";
    if (t.startsWith("#"))              type = "comment";
    else if (t.startsWith('"') || t.startsWith("'")) type = "string";
    else if (KW.has(t))                 type = "keyword";
    else if (/^\d/.test(t))             type = "number";
    else if (/^ind_/.test(t))           type = "builtin";
    else if (t === "np" || t === "numpy") type = "builtin";
    tokens.push({ t, type });
  }
  return tokens;
}

function HighlightedCode({ code }) {
  const tokens = tokenise(code);
  const COLOR = {
    keyword: "#c792ea",
    string:  "#c3e88d",
    comment: "#546e7a",
    number:  "#f78c6c",
    builtin: "#82aaff",
    plain:   "#d4d4d4",
  };
  return (
    <code style={{ fontFamily: "'JetBrains Mono', 'Fira Code', monospace", fontSize: 13 }}>
      {tokens.map((tok, i) => (
        <span key={i} style={{ color: COLOR[tok.type] }}>{tok.t}</span>
      ))}
    </code>
  );
}

// ─── line-number gutter ───────────────────────────────────────────────────────
function LineNumbers({ count }) {
  return (
    <div style={{
      padding: "16px 8px 16px 12px",
      textAlign: "right",
      userSelect: "none",
      color: "#3a4a5a",
      fontFamily: "'JetBrains Mono', monospace",
      fontSize: 13,
      lineHeight: "1.6",
      minWidth: 42,
      borderRight: "1px solid #1a2a3a",
    }}>
      {Array.from({ length: count }, (_, i) => (
        <div key={i}>{i + 1}</div>
      ))}
    </div>
  );
}

// ─── metrics card ──────────────────────────────────────────────────────────────
function MetricCard({ label, value, color = "#e0e0e0", sub }) {
  return (
    <div style={{
      background: "#0d1821",
      border: "1px solid #1a2a3a",
      borderRadius: 8,
      padding: "10px 14px",
      minWidth: 110,
    }}>
      <div style={{ fontSize: 11, color: "#546e7a", textTransform: "uppercase",
                    letterSpacing: "0.08em", marginBottom: 4 }}>{label}</div>
      <div style={{ fontSize: 18, fontWeight: 700, color,
                    fontFamily: "'JetBrains Mono', monospace" }}>{value}</div>
      {sub && <div style={{ fontSize: 11, color: "#546e7a", marginTop: 2 }}>{sub}</div>}
    </div>
  );
}

// ─── main component ────────────────────────────────────────────────────────────
export default function StrategyEditor({ apiBase = SIMULATOR_URL, token, socket }) {
  const [code, setCode]           = useState("");
  const [template, setTemplate]   = useState("");
  const [status, setStatus]       = useState("idle"); // idle | loading | loaded | error
  const [errorMsg, setErrorMsg]   = useState("");
  const [stratName, setStratName] = useState(null);
  const [enabled, setEnabled]     = useState(false);
  const [metrics, setMetrics]     = useState(null);
  const [signals, setSignals]     = useState([]);
  const [tab, setTab]             = useState("editor"); // editor | metrics | signals
  const textareaRef               = useRef(null);
  const mirrorRef                 = useRef(null);

  const authHeaders = {
    "Content-Type": "application/json",
    ...(token ? { Authorization: `Bearer ${token}` } : {}),
  };

  // fetch starter template on mount
  useEffect(() => {
    fetch(`${apiBase}/api/strategy/dynamic/template`)
      .then(r => r.json())
      .then(d => {
        setTemplate(d.template);
        if (!code) setCode(d.template);
      })
      .catch(() => {});
  }, [apiBase, code]);

  // socket updates
  useEffect(() => {
    if (!socket) return;
    const onUpdate = ({ metrics: m, error, actions }) => {
      if (m)      setMetrics(m);
      if (error)  setErrorMsg(error);
      if (actions?.length) {
        setSignals(prev => [...prev.slice(-99), ...actions.map(a => a.signal).filter(Boolean)]);
      }
    };
    const onToggled = ({ enabled: e, metrics: m }) => {
      setEnabled(e);
      if (m) setMetrics(m);
    };
    socket.on("dynamic_strategy_update",  onUpdate);
    socket.on("dynamic_strategy_toggled", onToggled);
    socket.on("dynamic_strategy_error",   ({ error }) => setErrorMsg(error));
    return () => {
      socket.off("dynamic_strategy_update",  onUpdate);
      socket.off("dynamic_strategy_toggled", onToggled);
      socket.off("dynamic_strategy_error");
    };
  }, [socket]);

  // sync textarea scroll → highlight mirror
  const syncScroll = useCallback(() => {
    if (mirrorRef.current && textareaRef.current) {
      mirrorRef.current.scrollTop  = textareaRef.current.scrollTop;
      mirrorRef.current.scrollLeft = textareaRef.current.scrollLeft;
    }
  }, []);

  // Tab-key support
  const handleKeyDown = useCallback(e => {
    if (e.key !== "Tab") return;
    e.preventDefault();
    const el    = e.target;
    const start = el.selectionStart;
    const end   = el.selectionEnd;
    const next  = code.slice(0, start) + "    " + code.slice(end);
    setCode(next);
    requestAnimationFrame(() => {
      el.selectionStart = el.selectionEnd = start + 4;
    });
  }, [code]);

  // load strategy
  const handleLoad = async () => {
    if (!code.trim()) return;
    setStatus("loading");
    setErrorMsg("");
    try {
      const res = await fetch(`${apiBase}/api/strategy/dynamic/load`, {
        method:  "POST",
        headers: authHeaders,
        body:    JSON.stringify({ source: code, capital: 10000 }),
      });
      const data = await res.json();
      if (!res.ok) {
        setStatus("error");
        setErrorMsg(data.detail || "Unknown error");
      } else {
        setStatus("loaded");
        setStratName(data.name);
        setMetrics(data.metrics);
        setEnabled(false);
        setSignals([]);
      }
    } catch (err) {
      setStatus("error");
      setErrorMsg(String(err));
    }
  };

  // toggle enable / disable
  const handleToggle = () => {
    const next = !enabled;
    setEnabled(next);
    if (socket) {
      socket.emit("toggle_dynamic_strategy", { enabled: next });
    } else {
      fetch(`${apiBase}/api/strategy/dynamic/toggle`, {
        method:  "POST",
        headers: authHeaders,
        body:    JSON.stringify({ enabled: next }),
      }).then(r => r.json()).then(d => { if (d.metrics) setMetrics(d.metrics); });
    }
  };

  // unload
  const handleUnload = async () => {
    await fetch(`${apiBase}/api/strategy/dynamic`, {
      method:  "DELETE",
      headers: authHeaders,
    });
    setStatus("idle");
    setStratName(null);
    setMetrics(null);
    setEnabled(false);
    setSignals([]);
    setErrorMsg("");
    setCode(template);
  };

  // reset to template
  const handleReset = () => {
    setCode(template);
    setStatus("idle");
    setErrorMsg("");
  };

  const lineCount  = (code.match(/\n/g) || []).length + 1;
  const pnlColor   = !metrics ? "#e0e0e0"
                   : metrics.net_pnl > 0 ? "#26a69a" : "#ef5350";

  // ── render ──────────────────────────────────────────────────────────────────
  return (
    <div style={{
      display:       "flex",
      flexDirection: "column",
      height:        "100%",
      background:    "#060f18",
      color:         "#d4d4d4",
      fontFamily:    "'JetBrains Mono', 'Fira Code', monospace",
      borderRadius:  12,
      overflow:      "hidden",
      border:        "1px solid #1a2a3a",
    }}>

      {/* ── header ── */}
      <div style={{
        display:        "flex",
        alignItems:     "center",
        gap:            12,
        padding:        "10px 16px",
        borderBottom:   "1px solid #1a2a3a",
        background:     "#080f18",
        flexShrink:     0,
      }}>
        <span style={{ fontSize: 13, color: "#546e7a", fontWeight: 600,
                       letterSpacing: "0.1em", textTransform: "uppercase" }}>
          Strategy Lab
        </span>

        {stratName && (
          <span style={{
            fontSize: 12, background: "#0d2a1a", color: "#26a69a",
            padding: "2px 10px", borderRadius: 20,
            border: "1px solid #1a4a2a",
          }}>
            {stratName}
          </span>
        )}

        <StatusBadge status={status} />

        <div style={{ flex: 1 }} />

        {/* tab pills */}
        {["editor","metrics","signals"].map(t => (
          <StarBorder as="button" key={t} onClick={() => setTab(t)} style={{
            background:  tab === t ? "#1a2a3a" : "transparent",
            border:      tab === t ? "1px solid #2a3a4a" : "1px solid transparent",
            borderRadius: 6,
            color:       tab === t ? "#82aaff" : "#546e7a",
            cursor:      "pointer",
            fontSize:    12,
            padding:     "4px 12px",
            textTransform: "capitalize",
          }}>{t}</StarBorder>
        ))}
      </div>

      {/* ── body ── */}
      <div style={{ flex: 1, overflow: "hidden", display: "flex", flexDirection: "column" }}>

        {/* EDITOR TAB */}
        {tab === "editor" && (
          <div style={{ flex: 1, display: "flex", flexDirection: "column", overflow: "hidden" }}>
            {/* error banner */}
            {errorMsg && (
              <div style={{
                background: "#1a0808", borderBottom: "1px solid #4a1a1a",
                padding: "8px 16px", fontSize: 12, color: "#ef5350",
                whiteSpace: "pre-wrap", maxHeight: 120, overflowY: "auto",
                flexShrink: 0,
              }}>
                <strong>Error:</strong> {errorMsg}
              </div>
            )}

            {/* code area */}
            <div style={{ flex: 1, overflow: "hidden", position: "relative", display: "flex" }}>
              <LineNumbers count={lineCount} />

              {/* layered: syntax highlight behind, textarea on top */}
              <div style={{ flex: 1, position: "relative", overflow: "hidden" }}>
                {/* highlight layer */}
                <div ref={mirrorRef} style={{
                  position:   "absolute", inset: 0,
                  overflow:   "hidden",
                  padding:    "16px 12px",
                  whiteSpace: "pre",
                  lineHeight: "1.6",
                  pointerEvents: "none",
                  background: "transparent",
                }}>
                  <HighlightedCode code={code} />
                </div>

                {/* input layer */}
                <textarea
                  ref={textareaRef}
                  value={code}
                  onChange={e => setCode(e.target.value)}
                  onKeyDown={handleKeyDown}
                  onScroll={syncScroll}
                  spellCheck={false}
                  style={{
                    position:    "absolute", inset: 0,
                    width:       "100%", height: "100%",
                    padding:     "16px 12px",
                    background:  "transparent",
                    color:       "transparent",
                    caretColor:  "#82aaff",
                    border:      "none",
                    outline:     "none",
                    resize:      "none",
                    fontFamily:  "'JetBrains Mono', 'Fira Code', monospace",
                    fontSize:    13,
                    lineHeight:  "1.6",
                    whiteSpace:  "pre",
                    overflowWrap: "normal",
                    overflowX:   "auto",
                    tabSize:     4,
                  }}
                />
              </div>
            </div>

            {/* toolbar */}
            <div style={{
              display:       "flex",
              gap:           8,
              padding:       "10px 16px",
              borderTop:     "1px solid #1a2a3a",
              background:    "#080f18",
              flexShrink:    0,
              alignItems:    "center",
            }}>
              <Btn onClick={handleLoad} disabled={status === "loading"}
                   variant="primary">
                {status === "loading" ? "Compiling…" : "⚡ Load Strategy"}
              </Btn>

              {status === "loaded" && (
                <>
                  <Btn onClick={handleToggle}
                       variant={enabled ? "danger" : "success"}>
                    {enabled ? "⏹ Disable" : "▶ Enable"}
                  </Btn>
                  <Btn onClick={handleUnload} variant="ghost">Unload</Btn>
                </>
              )}

              <Btn onClick={handleReset} variant="ghost">Reset Template</Btn>

              <div style={{ flex: 1 }} />
              <span style={{ fontSize: 11, color: "#3a4a5a" }}>
                {lineCount} lines · {code.length} chars
              </span>
            </div>
          </div>
        )}

        {/* METRICS TAB */}
        {tab === "metrics" && (
          <div style={{ flex: 1, overflowY: "auto", padding: 20 }}>
            {!metrics ? (
              <EmptyState icon="📊" text="Load a strategy to see metrics" />
            ) : (
              <>
                <div style={{ display: "flex", flexWrap: "wrap", gap: 10, marginBottom: 20 }}>
                  <MetricCard label="Net P&L" value={`S${metrics.net_pnl?.toFixed(2) ?? "—"}`}
                              color={pnlColor} />
                  <MetricCard label="Capital" value={`S${metrics.capital?.toFixed(2) ?? "—"}`} />
                  <MetricCard label="Trades" value={metrics.total_trades ?? 0} />
                  <MetricCard label="Win Rate"
                              value={metrics.win_rate != null ? `${metrics.win_rate}%` : "—"}
                              color={metrics.win_rate > 50 ? "#26a69a" : "#ef5350"} />
                  {metrics.profit_factor != null && (
                    <MetricCard label="Prof. Factor" value={metrics.profit_factor}
                                color={metrics.profit_factor > 1 ? "#26a69a" : "#ef5350"} />
                  )}
                  {metrics.sharpe != null && (
                    <MetricCard label="Sharpe" value={metrics.sharpe} />
                  )}
                  {metrics.max_dd != null && (
                    <MetricCard label="Max DD" value={`${metrics.max_dd}%`} color="#ef5350" />
                  )}
                </div>

                {/* raw JSON dump for any extra fields */}
                <div style={{
                  background: "#0d1821", border: "1px solid #1a2a3a",
                  borderRadius: 8, padding: 14,
                }}>
                  <div style={{ fontSize: 11, color: "#546e7a", marginBottom: 8,
                                textTransform: "uppercase", letterSpacing: "0.08em" }}>
                    Full metrics
                  </div>
                  <pre style={{ margin: 0, fontSize: 12, color: "#82aaff",
                                whiteSpace: "pre-wrap", wordBreak: "break-all" }}>
                    {JSON.stringify(metrics, null, 2)}
                  </pre>
                </div>

                {metrics.error && (
                  <div style={{
                    marginTop: 12, background: "#1a0808",
                    border: "1px solid #4a1a1a", borderRadius: 8,
                    padding: 12, color: "#ef5350", fontSize: 12,
                    whiteSpace: "pre-wrap",
                  }}>
                    <strong>Runtime error:</strong>{"\n"}{metrics.error}
                  </div>
                )}
              </>
            )}
          </div>
        )}

        {/* SIGNALS TAB */}
        {tab === "signals" && (
          <div style={{ flex: 1, overflowY: "auto" }}>
            {signals.length === 0 ? (
              <EmptyState icon="📡" text="No signals yet — enable the strategy and wait for candle closes" />
            ) : (
              <table style={{ width: "100%", borderCollapse: "collapse", fontSize: 12 }}>
                <thead>
                  <tr style={{ borderBottom: "1px solid #1a2a3a", background: "#080f18" }}>
                    {["Time","Type","Side","Price","Reason","P&L"].map(h => (
                      <th key={h} style={{ padding: "8px 14px", textAlign: "left",
                                           color: "#546e7a", fontWeight: 600,
                                           textTransform: "uppercase", letterSpacing: "0.06em" }}>
                        {h}
                      </th>
                    ))}
                  </tr>
                </thead>
                <tbody>
                  {[...signals].reverse().map((s, i) => (
                    <tr key={i} style={{ borderBottom: "1px solid #0d1821",
                                         background: i % 2 === 0 ? "transparent" : "#080f18" }}>
                      <td style={{ padding: "7px 14px", color: "#546e7a" }}>
                        {new Date(s.time * 1000).toLocaleTimeString()}
                      </td>
                      <td style={{ padding: "7px 14px" }}>
                        <span style={{
                          color:      s.type === "entry" ? "#82aaff" : "#c792ea",
                          fontWeight: 600,
                        }}>{s.type}</span>
                      </td>
                      <td style={{ padding: "7px 14px" }}>
                        <span style={{
                          color: s.side === "long" ? "#26a69a" : "#ef5350",
                          fontWeight: 700,
                        }}>{s.side?.toUpperCase()}</span>
                      </td>
                      <td style={{ padding: "7px 14px", color: "#d4d4d4", fontFamily: "monospace" }}>
                        {s.price?.toFixed(4)}
                      </td>
                      <td style={{ padding: "7px 14px", color: "#546e7a" }}>
                        {s.reason ?? "—"}
                      </td>
                      <td style={{ padding: "7px 14px",
                                   color: s.pnl == null ? "#546e7a"
                                         : s.pnl > 0    ? "#26a69a" : "#ef5350",
                                   fontFamily: "monospace" }}>
                        {s.pnl != null ? `S${s.pnl.toFixed(2)}` : "—"}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            )}
          </div>
        )}
      </div>
    </div>
  );
}

// ─── small helpers ─────────────────────────────────────────────────────────────

function StatusBadge({ status }) {
  const cfg = {
    idle:    { color: "#546e7a", bg: "#0d1821", border: "#1a2a3a", label: "idle" },
    loading: { color: "#f78c6c", bg: "#1a1208", border: "#3a2a08", label: "compiling…" },
    loaded:  { color: "#26a69a", bg: "#0d1a14", border: "#1a3a2a", label: "loaded" },
    error:   { color: "#ef5350", bg: "#1a0808", border: "#3a1a1a", label: "error" },
  }[status] || {};

  return (
    <span style={{
      fontSize: 11, padding: "2px 10px", borderRadius: 20,
      background: cfg.bg, color: cfg.color,
      border: `1px solid ${cfg.border}`,
      textTransform: "uppercase", letterSpacing: "0.08em",
    }}>
      {cfg.label}
    </span>
  );
}

function Btn({ children, onClick, disabled, variant = "ghost" }) {
  const VARIANTS = {
    primary: { bg: "#0a2a4a", color: "#82aaff", border: "#1a4a7a",
               hover: "#0d3a5a" },
    success: { bg: "#0d2a1a", color: "#26a69a", border: "#1a4a2a",
               hover: "#0d3a1a" },
    danger:  { bg: "#2a0d0d", color: "#ef5350", border: "#4a1a1a",
               hover: "#3a1212" },
    ghost:   { bg: "transparent", color: "#546e7a", border: "#1a2a3a",
               hover: "#0d1821" },
  };
  const v = VARIANTS[variant];
  const [hov, setHov] = useState(false);
  return (
    <StarBorder as="button"
      onClick={onClick}
      disabled={disabled}
      onMouseEnter={() => setHov(true)}
      onMouseLeave={() => setHov(false)}
      style={{
        background:   hov ? v.hover : v.bg,
        color:        disabled ? "#2a3a4a" : v.color,
        border:       `1px solid ${v.border}`,
        borderRadius: 7,
        cursor:       disabled ? "not-allowed" : "pointer",
        fontSize:     12,
        fontFamily:   "'JetBrains Mono', monospace",
        fontWeight:   600,
        padding:      "6px 14px",
        transition:   "background 0.15s",
      }}
    >
      {children}
    </StarBorder>
  );
}

function EmptyState({ icon, text }) {
  return (
    <div style={{
      display: "flex", flexDirection: "column", alignItems: "center",
      justifyContent: "center", height: "100%", gap: 12, opacity: 0.5,
    }}>
      <div style={{ fontSize: 36 }}>{icon}</div>
      <div style={{ fontSize: 13, color: "#546e7a", textAlign: "center",
                    maxWidth: 280, lineHeight: 1.5 }}>{text}</div>
    </div>
  );
}
