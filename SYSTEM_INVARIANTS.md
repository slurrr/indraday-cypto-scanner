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
- **[RISK]** The `sent_alerts` deduplication set grows unbounded over the process lifetime.
- WebSocket message drops must be tracked and exposed via metrics.
