from core.indicators import calculate_indicators_full, update_latest_candle
from models.types import Candle, TimeframeContext
import time

def verify_indicators():
    # 1. Create Synthetic History
    history = []
    base_ts = int(time.time() * 1000) - (60 * 60 * 1000)
    price = 100.0
    for i in range(100):
        c = Candle(
            symbol="TEST",
            timestamp=base_ts + (i * 180000),
            open=price,
            high=price + 1.0,
            low=price - 1.0,
            close=price + 0.5,
            volume=100.0,
            spot_cvd=10.0,  # Valid CVD
            perp_cvd=10.0,
            closed=True
        )
        history.append(c)
        price += 0.5

    # 2. Run Full Calculation
    print("Running calculate_indicators_full...")
    calculate_indicators_full(history)
    
    last = history[-1]
    print(f"Last Candle Indicators:")
    print(f"VWAP: {last.vwap}")
    print(f"SpotSlope: {last.spot_cvd_slope}")
    print(f"ATR%: {last.atr_percentile}") # Should be float
    
    if last.atr_percentile is None:
        print("FAIL: ATR Percentile is None")
    else:
        print("PASS: ATR Percentile is set")

    if last.spot_cvd_slope == 0.0: # Should be positive (cumulative 10+10+...)
        print("FAIL: Spot Slope is 0.0 (Expected positive)")
    else:
        print(f"PASS: Spot Slope {last.spot_cvd_slope:.4f}")

    # 3. Test Incremental Update
    print("\nTesting Incremental Update...")
    # Add new active candle
    active = Candle(
        symbol="TEST",
        timestamp=base_ts + (100 * 180000),
        open=price,
        high=price + 2.0,
        low=price - 2.0,
        close=price,
        volume=50.0,
        spot_cvd=5.0,
        perp_cvd=5.0,
        closed=False
    )
    history.append(active)
    
    update_latest_candle(history)
    tip = history[-1]
    print(f"Tip Indicators:")
    print(f"VWAP: {tip.vwap}")
    print(f"SpotSlope: {tip.spot_cvd_slope}")
    print(f"ATR%: {tip.atr_percentile}")
    
    if tip.atr_percentile is None:
        print("FAIL: Tip ATR Percentile is None")
    else:
        print("PASS: Tip ATR Percentile is set")


if __name__ == "__main__":
    verify_indicators()
