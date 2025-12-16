import json
from pathlib import Path
from datetime import datetime
from zoneinfo import ZoneInfo
from collections import Counter

import pandas as pd

# =============================================================================
# CONFIG
# =============================================================================

SNAPSHOT_PATH = Path("utils/event_snapshots.jsonl")
OUT_DIR = Path("utils/review")
OUT_DIR.mkdir(parents=True, exist_ok=True)

DENVER = ZoneInfo("America/Denver")

PASS = "PASS"
FAIL = "FAIL"
REVIEW = "REVIEW"
ALLOWED_VERDICTS = {PASS, FAIL, REVIEW}

# =============================================================================
# HELPERS
# =============================================================================

def tradingview_url(symbol: str, bar_time_ms: int, interval="3"):
    ts = int(bar_time_ms // 1000)
    return (
        "https://www.tradingview.com/chart/?"
        f"symbol=BINANCE:{symbol}&interval={interval}&time={ts}"
    )

# =============================================================================
# VERIFIERS (UNCHANGED SEMANTICS)
# =============================================================================

def auto_verify(row):
    pattern = row.get("pattern")
    fn = VERIFIERS.get(pattern)

    if not fn:
        return REVIEW, f"Unknown pattern: {pattern}"

    verdict, notes = fn(row)

    if verdict not in ALLOWED_VERDICTS:
        return REVIEW, f"Verifier bug: {pattern} returned {verdict!r}"

    return verdict, notes


# ---------------- IGNITION ----------------

def auto_verify_ignition(row):
    """
    IGNITION = sudden expansion in range + energy.
    Direction-agnostic. VWAP is context only.
    """
    if row["atr"] is None or row["atr"] <= 0:
        return FAIL, "ATR missing"

    if row["high"] is None or row["low"] is None:
        return FAIL, "OHLC incomplete"

    if (row["high"] - row["low"]) < 0.5 * row["atr"]:
        return FAIL, "Range too small"

    ap = row.get("atr_percentile")
    if ap is not None and ap < 25:
        return FAIL, "Low volatility regime"

    tags = ["Above VWAP" if row["close_vs_vwap"] >= 0 else "Below VWAP"]

    if ap is not None and ap > 95:
        return REVIEW, "Extreme volatility; " + "; ".join(tags)

    if abs(row.get("flow_bias", 0)) < 1e-6:
        return REVIEW, "Weak flow impulse; " + "; ".join(tags)

    return PASS, "; ".join(tags)


# ---------------- VWAP RECLAIM ----------------

def auto_verify_vwap_reclaim(row):
    """
    VWAP_RECLAIM = price crosses VWAP (either direction) and *holds* the new side for 1 bar.
    Direction-agnostic:
      - bullish reclaim: prev below -> now above
      - bearish reclaim: prev above -> now below
    """
    if row["vwap"] is None or row["close"] is None:
        return FAIL, "VWAP/close missing"

    bars = row["_bars"]
    sym = row["symbol"]
    t = row["bar_time"]

    cdf = (
        bars[bars["symbol"] == sym]
        .drop_duplicates("bar_time", keep="last")
        .sort_values("bar_time")
        .reset_index(drop=True)
    )

    idx = cdf.index[cdf["bar_time"] == t]
    if idx.empty or idx[0] == 0:
        return REVIEW, "Insufficient context"

    prev, curr = cdf.iloc[idx[0] - 1], cdf.iloc[idx[0]]

    prev_d = prev["close"] - prev["vwap"]
    curr_d = curr["close"] - curr["vwap"]

    crossed_up = prev_d <= 0 and curr_d > 0
    crossed_dn = prev_d >= 0 and curr_d < 0

    if not (crossed_up or crossed_dn):
        return FAIL, "No VWAP cross"

    direction = "BULL_RECLAIM" if crossed_up else "BEAR_RECLAIM"
    return PASS, direction

def auto_verify_trap(row):
    """
    TRAP = breaks prior bar range (high/low) then closes back inside (snap-back).
    Direction-agnostic:
      - bull trap: breaks above prev high, closes back below prev high (inside)
      - bear trap: breaks below prev low, closes back above prev low (inside)
    """

    # --- Hard FAILS ---
    if row.get("atr") is None or row["atr"] <= 0:
        return FAIL, "ATR missing"
    if row.get("high") is None or row.get("low") is None or row.get("close") is None:
        return FAIL, "OHLC incomplete"

    bars = row["_bars"]
    sym = row["symbol"]
    t = row["bar_time"]

    cdf = (
        bars[bars["symbol"] == sym]
        .drop_duplicates(subset=["bar_time"], keep="last")
        .sort_values("bar_time")
        .reset_index(drop=True)
    )

    idx_list = cdf.index[cdf["bar_time"] == t].tolist()
    if not idx_list:
        return REVIEW, "Current bar not found in series"
    i = idx_list[0]
    if i == 0:
        return REVIEW, "No previous bar to evaluate trap"

    prev = cdf.iloc[i - 1]
    curr = cdf.iloc[i]

    prev_high = prev.get("high")
    prev_low = prev.get("low")
    if pd.isna(prev_high) or pd.isna(prev_low):
        return REVIEW, "Prev bar missing OHLC"

    curr_high = curr["high"]
    curr_low = curr["low"]
    curr_close = curr["close"]

    broke_up = curr_high > prev_high
    snapped_down = curr_close < prev_high  # closed back inside range
    bull_trap = broke_up and snapped_down

    broke_down = curr_low < prev_low
    snapped_up = curr_close > prev_low     # closed back inside range
    bear_trap = broke_down and snapped_up

    if not (bull_trap or bear_trap):
        return FAIL, "No break+snap-back vs prior range"

    direction = "BULL_TRAP" if bull_trap else "BEAR_TRAP"

    # Strength filter: make sure the trap bar actually mattered (avoid micro-wiggles)
    atr = row["atr"]
    trap_excursion = (curr_high - prev_high) if bull_trap else (prev_low - curr_low)
    if trap_excursion < 0.05 * atr:
        return REVIEW, "Trap excursion too small (likely noise)"

    # Optional: low vol regime traps are extra junky
    ap = row.get("atr_percentile")
    if ap is not None and ap < 15:
        return FAIL, "Very low volatility regime"

    tags = [direction]

    # Lookahead confirmation (1 bar): next bar continues away from the broken level
    if i + 1 >= len(cdf):
        return REVIEW, "No lookahead bar to confirm follow-through"

    nxt = cdf.iloc[i + 1]
    if pd.isna(nxt.get("close")):
        return REVIEW, "Next bar close missing"

    if bull_trap:
        # after bull trap, want continuation down (next close below prev_high)
        if nxt["close"] < prev_high:
            tags.append("Confirmed next bar")
            return PASS, "; ".join(tags)
        return REVIEW, "Snap-back happened but no next-bar continuation"
    else:
        # after bear trap, want continuation up (next close above prev_low)
        if nxt["close"] > prev_low:
            tags.append("Confirmed next bar")
            return PASS, "; ".join(tags)
        return REVIEW, "Snap-back happened but no next-bar continuation"

def auto_verify_pullback(row):
    """
    PULLBACK = counter-move that respects structure after an impulse.
    We verify:
      - Pullback magnitude is reasonable (not a full reversal)
      - Structure (VWAP or prior range) is respected
      - No impulsive invalidation
    """

    # --- Hard FAILS ---
    if row.get("atr") is None or row["atr"] <= 0:
        return FAIL, "ATR missing"
    if row.get("close") is None:
        return FAIL, "Close missing"

    bars = row["_bars"]
    sym = row["symbol"]
    t = row["bar_time"]

    cdf = (
        bars[bars["symbol"] == sym]
        .drop_duplicates(subset=["bar_time"], keep="last")
        .sort_values("bar_time")
        .reset_index(drop=True)
    )

    idx_list = cdf.index[cdf["bar_time"] == t].tolist()
    if not idx_list:
        return REVIEW, "Current bar not found in series"
    i = idx_list[0]

    lookback = 6  # short, intraday
    if i < lookback:
        return REVIEW, "Insufficient lookback for pullback context"

    window = cdf.iloc[i - lookback : i + 1]
    curr = cdf.iloc[i]

    atr = row["atr"]

    # Determine impulse direction via net move
    start_close = window.iloc[0]["close"]
    end_close = window.iloc[-1]["close"]
    net_move = end_close - start_close

    if abs(net_move) < 0.3 * atr:
        return FAIL, "No meaningful impulse before pullback"

    bullish = net_move > 0

    # Pullback depth: how far did price retrace vs impulse
    high = window["high"].max()
    low = window["low"].min()

    pullback_dist = (high - curr["low"]) if bullish else (curr["high"] - low)
    impulse_dist = abs(high - low)

    if impulse_dist == 0:
        return REVIEW, "Zero impulse range"

    retrace_ratio = pullback_dist / impulse_dist

    # Too shallow → noise, too deep → reversal
    if retrace_ratio < 0.15:
        return FAIL, "Pullback too shallow (noise)"
    if retrace_ratio > 0.7:
        return FAIL, "Pullback too deep (likely reversal)"

    tags = ["Bullish pullback" if bullish else "Bearish pullback"]

    # Structure respect: VWAP or prior range
    vwap = curr.get("vwap")
    if vwap is not None:
        if bullish and curr["close"] < vwap:
            return FAIL, "Bull pullback lost VWAP"
        if not bullish and curr["close"] > vwap:
            return FAIL, "Bear pullback lost VWAP"
        tags.append("VWAP respected")

    # Invalidation check: impulsive counter candle
    prev = cdf.iloc[i - 1]
    if bullish:
        if curr["low"] < prev["low"] and (prev["high"] - prev["low"]) > atr:
            return FAIL, "Bearish impulse invalidated pullback"
    else:
        if curr["high"] > prev["high"] and (prev["high"] - prev["low"]) > atr:
            return FAIL, "Bullish impulse invalidated pullback"

    # Optional stabilization: next bar should not expand against impulse
    if i + 1 < len(cdf):
        nxt = cdf.iloc[i + 1]
        if bullish and nxt["low"] < curr["low"]:
            return REVIEW, "Pullback still expanding lower"
        if not bullish and nxt["high"] > curr["high"]:
            return REVIEW, "Pullback still expanding higher"

    return PASS, "; ".join(tags)

def auto_verify_failed_breakout(row):
    """
    FAILED_BREAKOUT =
      - Breaks recent range high/low
      - Closes back inside
      - No follow-through
    Direction-agnostic.
    """

    # --- Hard FAILS ---
    if row.get("atr") is None or row["atr"] <= 0:
        return FAIL, "ATR missing"
    if row.get("high") is None or row.get("low") is None or row.get("close") is None:
        return FAIL, "OHLC incomplete"

    bars = row["_bars"]
    sym = row["symbol"]
    t = row["bar_time"]

    cdf = (
        bars[bars["symbol"] == sym]
        .drop_duplicates(subset=["bar_time"], keep="last")
        .sort_values("bar_time")
        .reset_index(drop=True)
    )

    idx_list = cdf.index[cdf["bar_time"] == t].tolist()
    if not idx_list:
        return REVIEW, "Current bar not found in series"
    i = idx_list[0]

    lookback = 12
    if i < lookback:
        return REVIEW, "Insufficient lookback for breakout context"

    window = cdf.iloc[i - lookback : i]
    curr = cdf.iloc[i]

    range_high = window["high"].max()
    range_low = window["low"].min()

    atr = row["atr"]

    # --- Break + fail logic ---
    broke_up = curr["high"] > range_high
    failed_up = broke_up and curr["close"] < range_high

    broke_down = curr["low"] < range_low
    failed_down = broke_down and curr["close"] > range_low

    if not (failed_up or failed_down):
        return FAIL, "No break-and-fail vs recent range"

    direction = "FAILED_UP_BREAKOUT" if failed_up else "FAILED_DOWN_BREAKOUT"

    # Strength filter: avoid micro-fakes
    excursion = (
        curr["high"] - range_high if failed_up else range_low - curr["low"]
    )
    if excursion < 0.05 * atr:
        return REVIEW, "Breakout excursion too small (noise)"

    tags = [direction]

    # Volatility sanity
    ap = row.get("atr_percentile")
    if ap is not None and ap < 15:
        return FAIL, "Very low volatility regime"

    # Lookahead confirmation: next bar does NOT continue breakout direction
    if i + 1 >= len(cdf):
        return REVIEW, "No lookahead bar to confirm failure"

    nxt = cdf.iloc[i + 1]
    if pd.isna(nxt.get("close")):
        return REVIEW, "Next bar close missing"

    if failed_up:
        if nxt["close"] < range_high:
            tags.append("Confirmed failure next bar")
            return PASS, "; ".join(tags)
        return REVIEW, "Break failed but no follow-through rejection"
    else:
        if nxt["close"] > range_low:
            tags.append("Confirmed failure next bar")
            return PASS, "; ".join(tags)
        return REVIEW, "Break failed but no follow-through rejection"

# =============================================================================
# VERIFIER MAP
# =============================================================================

VERIFIERS = {
    "IGNITION": auto_verify_ignition,
    "VWAP_RECLAIM": auto_verify_vwap_reclaim,
    "TRAP": auto_verify_trap,
    "PULLBACK": auto_verify_pullback,
    "FAILED_BREAKOUT": auto_verify_failed_breakout,
}

# =============================================================================
# PIPELINE
# =============================================================================

rows = []

with SNAPSHOT_PATH.open("r", encoding="utf-8-sig", errors="replace") as f:
    for line in f:
        line = line.strip()
        if not line or not line.startswith("{"):
            continue

        s = json.loads(line)

        meta = s.get("meta", {})
        candle = s.get("candle", {})
        flow = s.get("flow", {})
        patterns = (s.get("analysis", {}) or {}).get("patterns", {}) or {}

        meta_pat = meta.get("pattern")
        if meta_pat and meta_pat not in patterns:
            patterns[meta_pat] = {
                "ok": meta.get("passed"),
                "reason": meta.get("failed_reason") or "",
            }

        for pat, obj in patterns.items():
            rows.append({
                "logged_at": meta.get("logged_at") or s.get("logged_at"),
                "bar_time": meta.get("bar_time"),
                "symbol": meta.get("symbol"),
                "pattern": pat,
                "ok": obj.get("ok"),
                "reason": obj.get("reason") or "",

                "open": candle.get("open"),
                "high": candle.get("high"),
                "low": candle.get("low"),
                "close": candle.get("close"),
                "vwap": candle.get("vwap"),
                "atr": candle.get("atr"),
                "atr_percentile": candle.get("atr_percentile"),
                "volume": candle.get("volume"),

                "spot_cvd_slope": flow.get("spot_cvd_slope"),
                "perp_cvd_slope": flow.get("perp_cvd_slope"),
            })

df = pd.DataFrame(rows)

df = (
    df.sort_values("logged_at")
      .drop_duplicates(["symbol", "bar_time", "pattern"], keep="last")
      .reset_index(drop=True)
)

df["_alert_ts"] = pd.to_datetime(df["logged_at"], errors="coerce")
df["alert_date"] = df["_alert_ts"].dt.strftime("%m/%d/%Y")
df["alert_time"] = df["_alert_ts"].dt.strftime("%H:%M:%S")

df["close_vs_vwap"] = df["close"] - df["vwap"]
df["flow_bias"] = df["spot_cvd_slope"] - df["perp_cvd_slope"]

# Build a clean candle series (1 row per symbol+bar_time)
bars = (
    df.drop_duplicates(subset=["symbol", "bar_time"], keep="last")
      .sort_values(["symbol", "bar_time"])
      .reset_index(drop=True)
)


df["tv_link"] = df.apply(
    lambda r: f'=HYPERLINK("{tradingview_url(r["symbol"], r["bar_time"])}","OPEN")',
    axis=1
)

# ---- VERIFY ONCE ----

def verify_row(r):
    row = r.to_dict()
    row["_bars"] = bars
    return pd.Series(auto_verify(row))

df[["auto_verdict", "auto_notes"]] = df.apply(verify_row, axis=1)

# =============================================================================
# EXPORT
# =============================================================================

is_triggered = df["ok"] == True
is_almost = (df["ok"] == False) & (df["reason"] != "")

review_cols = [
    "alert_date", "alert_time",
    "symbol", "pattern",
    "ok", "reason",
    "auto_verdict", "auto_notes",
    "atr_percentile",
    "close_vs_vwap",
    "flow_bias",
    "volume", "close",
    "tv_link",
]

for pat in sorted(df["pattern"].unique()):
    pat_df = df[df["pattern"] == pat]

    trg = pat_df[is_triggered]
    alm = pat_df[is_almost]

    if not trg.empty:
        p = OUT_DIR / f"TRIGGERED_{pat}.csv"
        trg.sort_values("_alert_ts")[review_cols].to_csv(p, index=False)
        print(f"[WRITE] {p} ({len(trg)})")

    if not alm.empty:
        p = OUT_DIR / f"ALMOST_{pat}.csv"
        alm.sort_values("_alert_ts")[review_cols].to_csv(p, index=False)
        print(f"[WRITE] {p} ({len(alm)})")

print("\nDONE.")
