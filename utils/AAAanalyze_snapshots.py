import json
from pathlib import Path
import pandas as pd
from datetime import datetime
from zoneinfo import ZoneInfo
from collections import Counter

DENVER = ZoneInfo("America/Denver")

def tradingview_url(symbol: str, bar_time_ms: int, interval="3"):
    # TradingView uses seconds
    ts = int(bar_time_ms // 1000)
    tv_symbol = f"BINANCE:{symbol}"
    return (
        "https://www.tradingview.com/chart/?"
        f"symbol={tv_symbol}&interval={interval}&time={ts}"
    )

SNAPSHOT_PATH = Path("utils/event_snapshots.jsonl") 
IGNITION_LOOKAHEAD = 8          # candles
IGNITION_MIN_R = 0.75           # minimum favorable move in R
IGNITION_MAX_ADVERSE_R = -0.5   # fail if this hit first
# --- verdict contract ---
PASS = "PASS"
FAIL = "FAIL"
REVIEW = "REVIEW"
ALLOWED_VERDICTS = {PASS, FAIL, REVIEW}

def auto_verify(row):
    """
    Returns: (auto_verdict, auto_notes)
    auto_verdict ∈ {"PASS", "FAIL", "REVIEW"}
    """
    pattern = row.get("pattern")

    fn = VERIFIERS.get(pattern)
    if not fn:
        return REVIEW, f"Unknown pattern: {pattern}"

    verdict, notes = fn(row)

    # hard guard so bad returns never silently poison your CSV
    if verdict not in ALLOWED_VERDICTS:
        return REVIEW, f"Verifier bug: {pattern} returned invalid verdict={verdict!r}"

    return verdict, notes

def auto_verify_ignition(row):
    """
    IGNITION = sudden expansion in range + energy.
    Direction-agnostic. VWAP is context only.
    """

    # --- Hard FAILS ---
    if row["atr"] is None or row["atr"] <= 0:
        return "FAIL", "ATR missing"

    if row["high"] is None or row["low"] is None:
        return "FAIL", "OHLC incomplete"

    candle_range = row["high"] - row["low"]

    if candle_range < 0.5 * row["atr"]:
        return "FAIL", "Range too small for ignition"

    if row["atr_percentile"] is not None and row["atr_percentile"] < 25:
        return "FAIL", "Low volatility regime"

    # --- Context (non-blocking tags) ---
    tags = []

    tags.append("Below VWAP" if row["close_vs_vwap"] < 0 else "Above VWAP")

    # --- Review-worthy flags ---
    review_flags = []

    if row["atr_percentile"] is not None and row["atr_percentile"] > 95:
        review_flags.append("Extreme volatility")

    if abs(row["flow_bias"]) < 1e-6:
        review_flags.append("Weak flow impulse")

    if review_flags:
        return "REVIEW", "; ".join(review_flags + tags)

    # --- Clean ignition ---
    return "PASS", "; ".join(tags)

def auto_verify_vwap_reclaim(row):
    """
    VWAP_RECLAIM = price crosses VWAP (either direction) and *holds* the new side for 1 bar.
    Direction-agnostic:
      - bullish reclaim: prev below -> now above
      - bearish reclaim: prev above -> now below
    """

    # --- Hard FAILS ---
    if row.get("vwap") is None:
        return FAIL, "VWAP missing"
    if row.get("close") is None:
        return FAIL, "Close missing"
    if row.get("atr") is None or row.get("atr") <= 0:
        return FAIL, "ATR missing"

    bars = row["_bars"]
    sym = row["symbol"]
    t = row["bar_time"]

    # Build a candle-series for this symbol (dedupe bar_time because df has 1 row per pattern)
    cdf = (
        bars[bars["symbol"] == sym]
        .drop_duplicates(subset=["bar_time"], keep="last")
        .sort_values("bar_time")
        .reset_index(drop=True)
    )

    # Find current bar position
    idx_list = cdf.index[cdf["bar_time"] == t].tolist()
    if not idx_list:
        return REVIEW, "Current bar not found in series"
    i = idx_list[0]

    if i == 0:
        return REVIEW, "No previous bar to confirm reclaim"

    prev = cdf.iloc[i - 1]
    curr = cdf.iloc[i]

    prev_delta = (prev["close"] - prev["vwap"]) if pd.notna(prev["close"]) and pd.notna(prev["vwap"]) else None
    curr_delta = (curr["close"] - curr["vwap"]) if pd.notna(curr["close"]) and pd.notna(curr["vwap"]) else None

    if prev_delta is None or curr_delta is None:
        return REVIEW, "VWAP/close incomplete in series"

    # Must actually cross VWAP
    crossed_up = (prev_delta <= 0) and (curr_delta > 0)
    crossed_down = (prev_delta >= 0) and (curr_delta < 0)

    if not (crossed_up or crossed_down):
        return FAIL, "No VWAP cross"

    direction = "BULL_RECLAIM" if crossed_up else "BEAR_RECLAIM"

    # Strength filter: reclaim should be meaningfully away from VWAP (avoid tiny wiggles)
    atr = row["atr"]
    min_dist = 0.05 * atr  # tweak later if needed
    if abs(curr_delta) < min_dist:
        return REVIEW, f"Weak reclaim distance (< {min_dist:.4f})"

    # Optional volatility sanity: if you're in a dead regime, reclaim signals are often garbage
    ap = row.get("atr_percentile")
    if ap is not None and ap < 15:
        return FAIL, "Very low volatility regime"

    tags = [direction, ("Above VWAP" if curr_delta > 0 else "Below VWAP")]

    # Hold confirmation: next bar should stay on the new side (1-bar hold)
    if i + 1 >= len(cdf):
        return REVIEW, "No lookahead bar to confirm hold"

    nxt = cdf.iloc[i + 1]
    nxt_delta = (nxt["close"] - nxt["vwap"]) if pd.notna(nxt["close"]) and pd.notna(nxt["vwap"]) else None
    if nxt_delta is None:
        return REVIEW, "Next bar missing VWAP/close"

    held = (curr_delta > 0 and nxt_delta > 0) or (curr_delta < 0 and nxt_delta < 0)
    if not held:
        return REVIEW, "Crossed VWAP but did not hold next bar; likely chop/whipsaw"

    # Flow context (non-blocking): just annotate
    fb = row.get("flow_bias")
    if fb is not None:
        if direction == "BULL_RECLAIM" and fb < 0:
            tags.append("Flow contradicts (perp>spot)")
        if direction == "BEAR_RECLAIM" and fb > 0:
            tags.append("Flow contradicts (spot>perp)")

    return PASS, "; ".join(tags)


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


VERIFIERS = {
    "IGNITION": auto_verify_ignition,
    "VWAP_RECLAIM": auto_verify_vwap_reclaim,
    "TRAP": auto_verify_trap,
    "PULLBACK": auto_verify_pullback,
    "FAILED_BREAKOUT": auto_verify_failed_breakout,
}


rows = []
bad = 0
with SNAPSHOT_PATH.open("r", encoding="utf-8-sig", errors="replace") as f:
    for i, line in enumerate(f, start=1):
        line = line.strip()
        if not line:
            continue
        if not (line.startswith("{") and line.endswith("}")):
            if bad < 3:
                print(f"[WARN] Non-JSON line {i}: {line[:120]}")
            bad += 1
            continue
        try:
            s = json.loads(line)
        except json.JSONDecodeError as e:
            if bad < 3:
                print(f"[WARN] JSON error line {i}: {e}")
            bad += 1
            continue

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

        for pat_name, pat_obj in patterns.items():
            ok = pat_obj.get("ok")
            reason = pat_obj.get("reason") or ""

            rows.append({
                "logged_at": meta.get("logged_at") or s.get("logged_at"),
                "bar_time": meta.get("bar_time"),
                "symbol": meta.get("symbol"),
                "pattern": pat_name,
                "ok": ok,
                "reason": reason,

                "flow_regime": (meta.get("flow_regime")
                               or (s.get("analysis", {}) or {}).get("flow_regime", {}).get("regime")),
                "spot_cvd_slope": flow.get("spot_cvd_slope"),
                "perp_cvd_slope": flow.get("perp_cvd_slope"),

                "open": candle.get("open"),
                "high": candle.get("high"),
                "low": candle.get("low"),
                "close": candle.get("close"),
                "vwap": candle.get("vwap"),
                "atr": candle.get("atr"),
                "atr_percentile": candle.get("atr_percentile"),
                "volume": candle.get("volume"),
            })
           
print("RAW ROW COUNTS BY PATTERN:")
print(Counter(r["pattern"] for r in rows))
df = pd.DataFrame(rows)

# --- de-duplicate decisions ---
before = len(df)

df = (
    df.sort_values("logged_at")
      .drop_duplicates(
          subset=["symbol", "bar_time", "pattern"],
          keep="last"
      )
      .reset_index(drop=True)
)
print("POST-DEDUP COUNTS BY PATTERN:")
print(df["pattern"].value_counts())
print(f"\nDe-duplicated rows: {before} -> {len(df)}")

df["bar_time_hms"] = df["bar_time"].apply(
    lambda ms: datetime.utcfromtimestamp(ms / 1000).strftime("%H:%M:%S")
    if pd.notna(ms) else None
)

df["_alert_ts"] = pd.to_datetime(df["logged_at"], errors="coerce")

print("Sample logged_at raw:", df["logged_at"].dropna().iloc[0])
print("Sample _alert_ts (UTC):", df["_alert_ts"].dropna().iloc[0])

df["alert_time_denver"] = (
    df["_alert_ts"]
    .dt.tz_localize("UTC")
    .dt.tz_convert(DENVER)
    .dt.strftime("%H:%M:%S")
)

ts = pd.to_datetime(df["logged_at"], errors="coerce")

df["alert_date"] = ts.dt.strftime("%m/%d/%Y")
df["alert_time"] = ts.dt.strftime("%H:%M:%S")

# Derived helpers
df["close_vs_vwap"] = df["close"] - df["vwap"]
df["flow_bias"] = df["spot_cvd_slope"] - df["perp_cvd_slope"]

df["tv_url"] = df.apply(
    lambda r: tradingview_url(r["symbol"], r["bar_time"]),
    axis=1
)

df["tv_link"] = df["tv_url"].apply(
    lambda u: f'=HYPERLINK("{u}", "OPEN")'
)

def verify_row(r):
    row = r.to_dict()

    # 🔒 private verifier context
    row["_bars"] = df
    row["_bar_index"] = r.name

    return pd.Series(auto_verify(row))

df[["auto_verdict", "auto_notes"]] = df.apply(verify_row, axis=1)

df["verdict"] = ""        # GOOD / BAD / MAYBE
df["notes"] = ""          # quick reason

out_dir = Path("utils/review")
out_dir.mkdir(parents=True, exist_ok=True)

review_cols = [
    "auto_verdict", "auto_notes",
    "verdict", "notes",
    "alert_date", "alert_time_denver", 
    "bar_time_hms", "symbol", "pattern",
    "flow_regime", "atr_percentile",
    "close_vs_vwap", "flow_bias",
    "volume", "close",
    "tv_link",
]



# ---- TRIGGERED (passed: true) ----
#triggers = df[df["ok"] == True]

ALMOST_MASK = (
    (df["ok"] == False) &
    (df["reason"].notna()) &
    (df["reason"] != "")
)

for pat in patterns:
    pat_df = df[df["pattern"] == pat]

    triggers = pat_df[pat_df["ok"] == True].copy()
    almost = pat_df[ALMOST_MASK].copy()

    # Run your existing verifier (expects a row), don't pass extra args.
    if not triggers.empty:
        triggers[["auto_verdict", "auto_notes"]] = triggers.apply(
            verify_row, axis=1, result_type="expand"
        )
    if not almost.empty:
        almost[["auto_verdict", "auto_notes"]] = almost.apply(
            verify_row, axis=1, result_type="expand"
        )

    print(f"\n=== {pat} ===")
    print(f"Triggered: {len(triggers)} | Almost: {len(almost)}")

    if not triggers.empty:
        print("-- Triggered auto_verdict --")
        print(triggers["auto_verdict"].value_counts(dropna=False))

    if not almost.empty:
        print("-- Almost blocking reasons (top 8) --")
        print(almost["reason"].value_counts().head(8))
        print("-- Almost auto_verdict --")
        print(almost["auto_verdict"].value_counts(dropna=False))






# ---- flags ----
df["passed"] = df["ok"]  # pattern-level ok
df["meta_passed"] = df["pattern"].map(
    df.groupby(["logged_at", "symbol"])["ok"].max()
)

for pat, g in triggers.groupby("pattern"):
    p = out_dir / f"TRIGGERED_{pat}.csv"
    g.sort_values("_alert_ts")[review_cols].to_csv(p, index=False)
    print(f"[WRITE] {p} ({len(g)})")

for pat, g in almost.groupby("pattern"):
    p = out_dir / f"ALMOST{pat}.csv"
    g.sort_values("_alert_ts")[review_cols].to_csv(p, index=False)
    print(f"[WRITE] {p} ({len(g)})")

print("\n=== TRIGGERED SIGNALS ===")
print("Count:", len(triggers))

print("\nTriggered by pattern:")
print(triggers["pattern"].value_counts())




print("\n========== FAILED_BREAKOUT (TRIGGERED ONLY) ==========")

fb = triggers[triggers["pattern"] == "FAILED_BREAKOUT"]

if fb.empty:
    print("NO FAILED_BREAKOUT TRIGGERED")
else:
    print(f"Triggered FAILED_BREAKOUT count: {len(fb)}")

    print("\n-- Verdict Counts --")
    print(fb["auto_verdict"].value_counts(dropna=False))

    print("\n-- Core Stats --")
    cols = ["score", "atr", "atr_percentile"]
    cols = [c for c in cols if c in fb.columns]
    print(fb[cols].describe(percentiles=[0.25, 0.5, 0.75]).round(4))






print("\nTriggered sample (sanity view):")
cols = [
    "logged_at", "symbol", "pattern",
    "flow_regime", "atr_percentile",
    "close_vs_vwap", "flow_bias"
]
print(triggers[cols].head(20).to_string(index=False))

# ---- ALMOST (passed: false) ----
almost = df[df["ok"] == False]

print("\n=== ALMOST SIGNALS (REJECTED) ===")
print("Count:", len(almost))

print("\nTop rejection reasons:")
print(almost["reason"].value_counts().head(15))

print("\nRejections by pattern:")
print(almost.groupby("pattern")["reason"].count().sort_values(ascending=False))
