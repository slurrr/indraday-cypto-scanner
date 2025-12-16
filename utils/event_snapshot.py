from datetime import datetime

def build_snapshot(
    *,
    symbol,
    pattern,
    candle,
    regime,
    score,
    passed,
    debug_data,      # <-- IMPORTANT
    failed_reason=None,
):
    return {
        "meta": {
            "symbol": symbol,
            "pattern": pattern.value,
            "bar_time": candle.timestamp,
            "logged_at": datetime.utcnow().isoformat(),
            "flow_regime": regime.value,
            "score": score,
            "passed": passed,
            "failed_reason": failed_reason,
        },

        # Raw candle (chart parity)
        "candle": {
            "open": candle.open,
            "high": candle.high,
            "low": candle.low,
            "close": candle.close,
            "volume": candle.volume,
            "vwap": candle.vwap,
            "atr": candle.atr,
            "atr_percentile": candle.atr_percentile,
        },

        # Flow inputs
        "flow": {
            "spot_cvd_slope": candle.spot_cvd_slope,
            "perp_cvd_slope": candle.perp_cvd_slope,
        },

        # EXACT analyzer internals
        "analysis": debug_data,
    }
