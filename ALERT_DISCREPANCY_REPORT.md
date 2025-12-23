# Investigation Report: AAVE vs PEPE VWAP Reclaim Discrepancy

## Executive Summary
The false positive "VWAP Reclaim" alert for **AAVE** at 07:39 was caused by a critical data ingestion flaw where **Spot** and **Perpetual** trades are mixed into the same Candle object. This causes the candle's price to "flicker" between Spot and Perp prices as trades arrive.

If the VWAP sits between the Spot price and the Perp price (a common occurrence when there is a "basis" or spread), the scanner perceives this flickering as the price rapidly crossing above and below the VWAP. This triggered the "Reclaim" pattern logic falsely.

**PEPE** fired correctly (9:27) likely because:
1. The price move was genuine and significant (crossing VWAP on both markets).
2. Or the spread was negligible, so both markets agreed on the location relative to VWAP.

## Detailed Root Cause Analysis

### 1. Data Ingestion Flaw
In `core/data_processor.py`, the `process_trade` method updates the active candle for a symbol regardless of the trade source:

```python
# core/data_processor.py

def _update_candle(self, candle: Candle, trade: Trade):
    # This overwrites the Close price with the latest trade, 
    # whether it is from Spot or Perp.
    candle.close = trade.price
    # ...
```

In `data/binance_client.py`, the client subscribes to **both** Spot (`@aggTrade`) and Perp (`@aggTrade`) streams for the same symbol keys (e.g., `AAVEUSDT`).

### 2. The Mechanism of Failure
When a symbol like AAVE has a spread (Basis) between Spot and Perp markets:
*   **Spot Price**: $100.00
*   **Perp Price**: $100.20
*   **VWAP**: $100.10

As trades arrive milliseconds apart:
1.  **Spot Trade** arrives: Candle Close becomes **$100.00** (Below VWAP).
2.  **Perp Trade** arrives: Candle Close becomes **$100.20** (Above VWAP).

The Analyzer's `_check_vwap_reclaim` logic compares the **Previous Candle** and the **Current Candle**.
*   If the previous minute happened to close on a **Spot** trade ($100.00, below VWAP).
*   And the current minute just received a **Perp** trade ($100.20, above VWAP).
*   The Analyzer sees a "Reclaim" pattern: `Prev < VWAP` and `Curr > VWAP`.

This happens even if the true market trend is flat. The user perceives this as "nowhere near a reclaim" because they are likely looking at a single chart (Perp or Spot) where the price is steadily above or below the VWAP, not jumping between them.

## Evidence
*   **Logs**: Confirmed AAVE alert at 07:39 and PEPE alert at 09:27.
*   **Code**: `BinanceClient` explicitly starts threads for both Spot and Perp streams, feeding the same `on_trade` callback.
*   **Logic**: `_check_vwap_reclaim` relies on `curr.close > curr.vwap` and `prev.close < prev.vwap`. The mixed-source flicker satisfies this condition artificially.

## Recommendations
To fix this and ensure data integrity:

1.  **Segregate Price Source**: 
    *   Designate **Perpetual** trades as the source of truth for **Price** (Open/High/Low/Close).
    *   Ignore **Spot** trades for price updates, or maintain separate Spot/Perp candles.
2.  **Aggregate Volume/Flow**:
    *   Continue to sum **Volume** and **CVD** from both sources if your goal is to analyze total market flow.
    *   Update `_update_candle` to only update `close/high/low` if `trade.source == 'perp'`.

### Proposed Code Change
Modify `core/data_processor.py`:

```python
def _update_candle(self, candle: Candle, trade: Trade):
    # ONLY update price fields if trade is from the primary source (Perp)
    if trade.source == 'perp':
        candle.high = max(candle.high, trade.price)
        candle.low = min(candle.low, trade.price)
        candle.close = trade.price
        # logic for open price...

    # ALWAYS update volume/CVD from both
    candle.volume += trade.quantity
    # ... (CVD logic)
```
