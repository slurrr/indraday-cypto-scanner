# Intraday Flow Scanner — MVP Specification

---

## 1. Purpose & Philosophy

**Goal**  
Build a real-time, intraday decision-support system that tells the trader **where to look and when**, using price structure, VWAP, volatility, and *flow intelligence (spot vs perp)*.

This system is **not**:

- A trade execution engine  
- A backtesting platform  
- A signal-to-trade or auto-trading system  

It **is**:

- A live attention allocator  
- A trap and false-move detector  
- A flow-aware context engine for discretionary intraday trading  

**Design Principle**

> Fewer signals, higher confidence, stronger context.

The system must reduce cognitive load, not increase it.

---

## 2. MVP Scope (Hard Constraints)

### In Scope ✅

- Intraday monitoring only  
- Timeframes: **1m–15m**  
- Real-time or near-real-time operation  
- Small-to-medium universe (**~50–150 perpetual symbols**)  
- Alert-first design  
- Desktop usage (seat-time tool for active traders)

### Explicitly Out of Scope ❌

- Trade execution or order management  
- Backtesting or historical strategy evaluation  
- Long-term data storage or analytics  
- Advanced UI polish or charting  
- Strategy optimization or automation

---

## 3. Data Sources (MVP)

### Primary Exchange

**Binance**

**Spot Data**

- Trade stream → Spot CVD

**Perpetuals Data**

- Trade stream → Perp CVD  
- Funding rate  
- Open interest (level + change)

**Rationale**  
Binance is selected for MVP due to free access, high liquidity, and low-latency websocket availability.

---

## 4. Data Update & Aggregation Model

- Trade ingestion: **websocket, real-time**  
- Aggregations: **rolling windows**  
- Canonical bar resolution: **1-minute**

All indicators and patterns operate on a **primary timeframe** (default: 1m or 3m), with optional **higher-intraday context inputs** (5m / 15m) for confirmation only.

---

## 5. Core Continuous Computations

### Price & Volatility

- Session VWAP  
- VWAP slope  
- ATR  
- ATR percentile (single rolling window)  
- Session high / low  
- Opening range (5m and 15m)

### Flow & Volume

- Spot CVD  
- Perp CVD  
- Spot CVD slope  
- Perp CVD slope  
- Price vs CVD divergence  
- **Spot vs Perp CVD divergence**

### Derivatives Context

- Open interest change  
- Funding rate (absolute + delta)

---

## 6. Flow Regime Abstraction (Critical Shared Layer)

A single, centralized interpretation layer used by **all patterns**.

### FlowRegime Enum

- `FLOW_CONSENSUS` — Spot ↑, Perp ↑  
- `FLOW_PERP_DOMINANT` — Perp ↑, Spot → or ↓  
- `FLOW_SPOT_DOMINANT` — Spot ↑, Perp → or ↓  
- `FLOW_CONFLICT` — Opposing slopes  
- `FLOW_NEUTRAL` — Low activity / chop

### Flow Regime Derivation Inputs

- Spot CVD slope  
- Perp CVD slope  
- Spot vs Perp divergence magnitude  
- **Volatility gate (ATR percentile)**

**Volatility Gating Rule (MVP)**  
If ATR percentile is below a minimum threshold, flow regime defaults to `FLOW_NEUTRAL`, regardless of CVD behavior.

---

## 7. MVP Pattern Set (Fixed)

Only the following patterns may emit alerts in MVP.

### 1. VWAP Reclaim / Rejection

**Intent**  
Identify acceptance or rejection around session VWAP.

**Inputs**

- VWAP position  
- VWAP slope  
- Volume expansion  
- Flow regime (must not be `FLOW_NEUTRAL`)

---

### 2. Post-Impulse Pullback (VWAP / MA)

**Intent**  
Continuation setup after strong directional move.

**Required Conditions**

- Impulse range exceeds threshold  
- ATR contraction following impulse  
- Pullback into rising VWAP or short MA  
- Flow regime not distributing or conflicting

---

### 3. Trap Detection (Top / Bottom)

**Intent**  
Detect stop-runs and false breakouts.

**Defining Characteristics**

- Sweep of session high / low  
- Price vs CVD divergence  
- Spot vs Perp CVD disagreement  
- Flow regime often `FLOW_CONFLICT` or `FLOW_PERP_DOMINANT`

---

### 4. Ignition / Volatility Expansion

**Intent**  
Highlight compression before directional expansion.

**Signals**

- ATR percentile at lows  
- Volume compression  
- Price near VWAP  
- Flow regime transitioning from neutral

---

### 5. Failed Breakout

**Intent**  
Identify structural breakout failure *without explicit stop-run*.

**Characteristics**

- Break beyond key level  
- Immediate rejection back into range  
- Weak or absent flow follow-through

---

## 8. Alert Scoring Model (MVP-Simple)

```
alert_score =
  pattern_base_score
+ flow_alignment_bonus
+ volatility_bonus
+ session_context_bonus
- chop_penalty
```

### MVP Constraints

- Bonuses are coarse-grained
- Chop penalty is binary
- Scoring complexity intentionally capped

Only alerts above threshold or top-N per cycle are surfaced.

---

## 9. Alert Hygiene Rules (MVP)

- Pattern cooldown per symbol
- No alerts in `FLOW_NEUTRAL`
- Optional no-trade / stand-down state for conflicting flow + low volatility

---

## 10. Alert Output (Human-Readable)

Examples:

- `SOLUSDT | VWAP RECLAIM | FLOW_SPOT_DOMINANT`
- `BTCUSDT | HOD TRAP | PERP BUYING UNCONFIRMED`
- `ETHUSDT | IGNITION | FLOW_CONSENSUS`

---

## 11. MVP UI

### Primary

- Console-based live table
- Sorted by alert score
- Color-coded by pattern and severity

### Optional

- Minimal web dashboard
- Auto-refresh

**UI Rule:** Speed > beauty > features

---

## 12. Success Criteria

- Attention narrowed to 1–3 symbols
- Early identification of traps and failed moves
- Calmer, more selective decision-making

---

# Planned for Future (Not MVP)

## A. Data Enhancements

- Glassnode aggregated flows
- CryptoQuant / Coinalyze
- Cross-exchange normalization

## B. Flow & Regime Intelligence

- Multi-tier volatility regimes
- Regime-weighted patterns
- Dynamic thresholds

## C. Pattern Expansion

- Trend-day classification
- Session behavior models
- Liquidation awareness

## D. Alert Intelligence

- Adaptive cooldowns
- Pattern confidence decay

## E. UX & Visualization

- Web dashboard
- Alert history & replay
- TradingView integration

## F. Research & Validation

- Forward outcome logging
- Conditional performance analysis

---

## Final Guiding Principle

> Build the tool you want open while actively trading — and aggressively exclude everything else.

