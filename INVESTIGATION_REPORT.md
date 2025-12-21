# Investigation Report: Performance & Crash Analysis

## Executive Summary
The scanner's "bogged down" behavior and subsequent crashes are primarily caused by a **critical concurrency bottleneck** where heavy O(N) data processing is performed inside a thread lock that blocks the WebSocket thread. This prevents the application from processing incoming trades in real-time, leading to message accumulation, lag, and potential memory exhaustion.

## Critical Findings

### 1. Concurrency Bottleneck (High Severity)
- **Mechanism**: The `on_trade` callback (WebSocket thread) and `reconcile_candle` (Worker thread) share a `symbol_lock`.
- **The Issue**: `reconcile_candle` performs a **Full History Recalculation** (`update_indicators(history)`) while holding this lock.
- **Impact**: Calculating indicators for 1000 candles (converting to Pandas DataFrame, computing VWAP/ATR, assigning back) is CPU-intensive (~10-50ms per symbol).
- **Result**: During this ~50ms, the WebSocket thread cannot process ANY trades for that symbol or potentially others sharing the connection (blocking the socket loop). If multiple symbols reconcile simultaneously (which happens at minute boundaries), the WebSocket thread stalls significantly.

### 2. Unbounded Executor Queue (Medium Severity)
- **Mechanism**: `ThreadPoolExecutor` is initialized with `max_workers=40` but has an unbounded task queue.
- **The Issue**: If the rate of incoming reconciliation tasks (from `on_trade`) exceeds the processing rate (workers + network latency), the queue grows indefinitely.
- **Impact**: With the Concurrency Bottleneck slowing down workers, the queue is likely to grow during high-load periods, consuming memory until the process crashes or is killed by the OS.

### 3. Memory Inefficiency (Medium Severity)
- **Mechanism**: `Candle` objects use standard Python `__dict__` storage.
- **The Issue**: Each `Candle` consumes significant overhead (~150-500 bytes + dict overhead).
- **Measurement**: 1000 candles * 3 timeframes * N symbols. For 20 symbols, this is manageable (~60k objects). For 100+ symbols, this becomes hundreds of megabytes.
- **Impact**: Increased GC pressure and higher baseline memory usage.

### 4. Excessive History Retention (Low Severity)
- **Mechanism**: Code retains `1000` candles (50 hours for 3m) for *all* timeframes.
- **The Issue**: Intraday logic (VWAP, ATR) typically requires only the current session or ~14-50 periods.
- **Impact**: Wasted memory and CPU cycles during the O(N) recalculations.

## Recommended Optimizations

### Phase 1: Fix the Bottleneck (Critical)
1.  **Refactor `update_indicators`**: Implement **Incremental Updates**. Instead of converting the entire history to a DataFrame every minute, only calculate the indicators for the *newest* candle using the previous candle's state.
2.  **Optimize Locking**: Move `update_indicators` OUTSIDE the symbol lock if possible, or ensure it is O(1) and extremely fast (<1ms) so the WebSocket thread is not blocked.

### Phase 2: System Stability
1.  **Data Structure Optimization**: Add `__slots__` to the `Candle` dataclass to reduce memory per object by ~50-60%.
2.  **Reduce History**: Lower `lookback_bars` from 1000 to ~300 (or config based), significantly reducing memory and processing time.
3.  **Bound the Queue**: Use a bounded queue or semaphore/logic to prevent scheduling duplicate reconciliation tasks if one is already pending for a symbol.

### Phase 3: Defensive Coding
1.  **Executor Shutdown**: Ensure `ThreadPoolExecutor` is properly shut down on exit to avoid dangling threads (though daemon threads usually handle this).
2.  **Log Rotation**: Ensure logs don't grow indefinitely (Rich logging seems okay, but file handler needs rotation).
