# System Invariants

## State & Transition Rules
- A symbol must have exactly one active state at any given time (IGNORE, WATCH, or ACT).
- State transitions (IGNITION -> WATCH, WATCH -> ACT) must only occur based on analysis of fully closed 3m candles.
- The `ACT` state must only be entered if `PermissionSnapshot.allowed` is True.
- An `ACT` state must be demoted to `WATCH` if `PermissionSnapshot.allowed` becomes False.
- An `ACT` state must be demoted to `WATCH` after `MAX_ACT_DURATION_MS` has elapsed.
- An `ACT` state must be demoted to `WATCH` if a disqualifying pattern (Demotion Aggressor) is detected.
- State is volatile and resets to `WATCH` (default in main.py) upon process restart; previously active states are not persisted.

## Execution & 1m Timeframe
- Execution analysis (1m) must ONLY run when the symbol is in the `ACT` state.
- ExecutionType must never be evaluated by pattern analyzers or participate in state promotion logic.
- Execution signals must be emitted with `ExecutionType.EXEC`.
- Execution signals must rely on the explicit `act_direction` set during the WATCH -> ACT transition.

## Timeframe & Data Integrity
- Candle reconciliation (background) must replace the local history candle with the API version but MUST preserve locally computed CVD if the API version lacks it.
- Analysis must always be performed on a consistent snapshot of history, protected by `symbol_locks`.
- `PermissionSnapshot` is derived exclusively from the 15m timeframe.
- Pattern alerts (Ignition, VWAP, Trap, etc.) are derived exclusively from the 3m timeframe.
- Duplicate alerts for the same symbol, pattern, and candle timestamp must be suppressed.

## Resource & Performance
- In-memory candle history must be capped (e.g., 1000 bars) to prevent unbounded memory growth.
- WebSocket message drops must be tracked and exposed via metrics.

## Data Consistency & Convergence
- **Warm-Up Period (Flow)**: The scanner requires at least **15 minutes** (5x 3m bars) of uptime to establish a valid `FlowRegime` (CVD Slope). Prior to this, flow-dependent patterns (Ignition, Reclaim, Pullback) may be inaccurate or artificially suppressed/triggered due to "Zero-to-Real" slope artifacts.
- **Warm-Up Period (Volatility)**: The scanner requires at least **42 minutes** (14x 3m bars) of uptime to establish a stable `ATR` baseline. Expansion thresholds (`Range > X * ATR`) may be more sensitive/volatile during this window.
- **CVD Drift Invariant**: Cumulative Volume Delta (CVD) is **never** reconciled with the REST API. As a result, long-running instances will drift in their calculation of `FlowRegime` compared to fresh instances. This is an accepted behavior; Flow is treated as a relative, local signal rather than an absolute, global truth.
