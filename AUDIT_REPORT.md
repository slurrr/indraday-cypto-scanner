# Intraday Crypto Scanner - Audit Findings

## Phase 1: Timeframe Plumbing Map

| Timeframe | Source | Reconciliation | Utilization | Status |
|-----------|--------|----------------|-------------|--------|
| **1m** | `DataProcessor(1m)` (intended aggregation in `on_trade`) | **None** (Implementation missing) | Intended for Execution Timing checks. | **DISCONNECTED** (Dead Code) |
| **3m** | `DataProcessor(3m)` aggregation in `on_trade`. | `main.reconcile_candle` runs async (~3m delay max). Fetches Binance API K-lines. Overwrites local history but preserves local CVD. | Core Pattern Analysis (Ignition, VWAP, TRAP). Drives State Transitions. | **ACTIVE** |
| **15m** | `DataProcessor(15m)` aggregation in `on_trade`. | `main.reconcile_candle` runs async. Updates `symbol_states[s].permission`. | Permission Gating (Bias/Volatility). Read-only. | **ACTIVE** |

**Key Finding:** The 1m execution pipeline is defined but never called in `main.on_trade`. No 1m candles are constructed, so no execution signals can ever be generated. Furthermore, the dead code in `main.analyze_1m` contains critical errors: `PatternType` and `ExecutionType` are not imported, and it incorrectly references `PatternType.EXEC` instead of `ExecutionType.EXEC`.

## Phase 2: State Transition Trace

**Normal Flow:** `IGNORE` -> `IGNITION` (Trigger) -> `WATCH` -> `PULLBACK/SETUP` (Trigger) -> `ACT` -> `Expiry/Demotion` -> `WATCH`

1. **IGNITION -> WATCH**
   - **Trigger**: 3m Candle Close with `IGNITION` (or VWAP/TRAP in current config).
   - **Mechanism**: `Analyzer` detects pattern -> Checks `state == IGNORE` -> Promotes to `WATCH`.
   - **Safety**: protected by `symbol_locks`. Idempotent because subsequent analysis sees `state == WATCH` and ignores the promotion block.

2. **WATCH -> ACT**
   - **Trigger**: 3m Candle Close with `ACT_ELIGIBLE_PATTERNS` (e.g. Pullback) AND `PermissionSnapshot.allowed` is True.
   - **Mechanism**: `Analyzer` checks pattern + permission -> Promotes to `ACT` -> Sets `act_direction`.
   - **Safety**: Permission is updated asynchronously by 15m thread. Race condition exists where permission might be 1-2 seconds stale, deemed acceptable.

3. **ACT -> WATCH (Demotion)**
   - **Triggers**: Timeout (`MAX_ACT_DURATION_MS`), Permission Revoked (15m change), or Disqualifying Pattern.
   - **Mechanism**: Checked on every 3m candle analysis.

**Restart Behavior**:
- System initializes all symbols in `WATCH` state.
- **Implication**: On restart, the system bypasses the `IGNITION` requirement. It will immediately accept `ACT` triggers (Pullbacks) without a preceding Ignition event. This appears to be a "fail-open" design choice for persistence-free restarts.

## Phase 3: Snapshot & Immutability

- **Concurrency**: `symbol_locks` effectively serializes access to `history` (List) and `state` (Object).
- **Mutable Objects**: `Candle` objects are modified in place during formation. `History` list is modified by swapping indices.
- **Risk**: `DataProcessor.active_candles` are modified in `on_trade` *inside* the lock. `Analyzer` reads `history` (closed candles) *inside* the lock.
- **Verdict**: Thread safety is robust. No obvious race conditions found given strict locking in `main.py`.

## Phase 4: Alert Guarantees

- **Deduplication**: `sent_alerts` set in `main.py` stores `(symbol, pattern, timestamp)`.
- **Effect**: Ensures a specific pattern on a specific candle is alerted only once, even if `analyze_3m` runs twice (Fast Path + Reconcile Path).
- **Memory Leak**: `sent_alerts` is never cleared. It will grow unbounded (approx ~1MB per week per symbol depending on activity).

## Phase 5: Failure Modes

1. **1m Execution Failure**: As noted, 1m logic is disconnected.
2. **Websocket Drops**: Handled by `BinanceClient` reconnection logic. Metrics tracked.
   - Risk: If many messages are dropped, local candle construction becomes inaccurate. Reconciliation fixes this after the candle closes, but "Fast Path" alerts might be based on bad data.
3. **API Rate Limits**: `fetch_latest_candle` logic in reconciliation adds load. With 20 items, acceptable.

## Phase 6: Performance

- **Analysis Cost**: `Analyzer` is O(N) or O(1) with fixed lookback. Runs fast.
- **Bottlenecks**: `reconcile_candle` does synchronous network I/O *before* acquiring lock (Good).

- **Scalability**: Python `threading` + GIL limits CPU bound tasks, but this workload is I/O bound (waiting for candles) + light math. Should scale to ~50-100 symbols before lag becomes noticeable in 3m processing.
- **Timeframe Extensibility**: The system currently uses "Hardcoded Slots" (`data_processor`, `data_processor_15m`) rather than a dynamic list. Adding a new concurrent timeframe (e.g. 5m alongside 3m) requires modifying `main.py` code to add a new processor and thread it through. However, swapping the "Primary" timeframe (e.g. 3m -> 5m) is purely config-driven via `CANDLE_TIMEFRAME_MINUTES`.
