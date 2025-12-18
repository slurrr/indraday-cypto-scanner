# Timeframe Boundary Contract (TBC)

## Purpose
Define what each timeframe is allowed to do, what it must output, and what it must never do.
This contract is enforced by code structure later.

## Canonical timeframes (initial)
- 1m: Execution timing only (never direction)
- 3m and 5m: State engine (IGNORE/WATCH/ACT lives here)
- 15m: Permission / sanity check (HTF bias, volatility)
- 1h: Optional later (session/macro filter)

## Shared definitions (must be consistent across TFs)
- Candle identity: (symbol, timeframe, open_time_ms)
- “Closed candle” definition: TODO: cite where candle close is determined today
- Data freshness policy:
  - realtime candle: OK for execution-only checks 
  - closed candle only: required for state transitions + HTF permission

## Allowed responsibilities by timeframe

### 1m (Execution)
Allowed:
- triggers that depend on microstructure timing ONLY (e.g., reclaim timing, break timing)
- reads HTF + state outputs
Forbidden:
- changing state (no promotions/demotions)
- establishing direction/bias
Inputs:
- state snapshot from 3m/5m
- permission snapshot from 15m
Outputs:
- execution signals (non-stateful), e.g. EXEC_* events

### 3m/5m (State Engine)
Allowed:
- evaluate “structure” and “resolution” events
- promote/demote state: IGNORE → WATCH → ACT → (decay/downgrade)
Forbidden:
- using 1m direction
Inputs:
- closed 3m/5m candles
- 15m permission snapshot
Outputs:
- state snapshots (per symbol): state, timers, reasons, active windows

### 15m (Permission / Sanity)
Allowed:
- bias / volatility regime checks
- act as gate for promotions into ACT (or for maintaining ACT)
Forbidden:
- emitting execution alerts directly
Inputs:
- closed 15m candles
Outputs:
- permission snapshot: bias, vol regime, “allowed” flags + reasons

## Event model (what flows between layers)
Event types (draft):
- STRUCTURE_* (from 3m/5m detection)
- RESOLUTION_* (from 3m/5m detection)
- PERMISSION_* (from 15m computation)
- EXEC_* (from 1m detection)
- ALERT_* (only emitted when state allows)

For each event type define:
- producer timeframe
- consumer timeframe
- required payload fields

## State snapshot schema (per symbol)
Minimum fields:
- symbol
- state: IGNORE|WATCH|ACT
- state_entered_at (ms)
- state_expires_at (ms)
- watch_reason (enum/string)
- act_reason (enum/string)
- permission_ok (bool + reason)
- active_patterns (list)
- last_updated_tf (e.g. "3m")
TODO: confirm what alert payload contains today and map fields.

## Alert emission rules (hard boundaries)
- “Pattern detected” does NOT equal “alert emitted”
- Alerts are emitted ONLY when:
  - state == ACT OR (state == WATCH AND event_type in {STRUCTURE_* only, if you choose})
  - permission_ok == True (unless explicitly overridden)
- Execution alerts (1m) only emitted when state == ACT

## Mapping current code → this contract (citations required)
Where candles are built:
- TODO: paths/functions

Where indicators are computed:
- TODO: paths/functions

Where patterns are detected:
- TODO: paths/functions

Where alerts are emitted:
- TODO: paths/functions

## Known risks from current architecture
- TODO: list globals/singletons/caches that assume 1 timeframe
- TODO: any functions that accept df but assume “current candle” is global

## Acceptance criteria (for this doc)
- Every “TODO” either has a citation or a clear question for resolution
- No contradictions (especially around realtime vs closed candle usage)
