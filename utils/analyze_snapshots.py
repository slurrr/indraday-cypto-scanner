import json
from pathlib import Path
import pandas as pd
from datetime import datetime

def tradingview_url(symbol: str, bar_time_ms: int, interval="3"):
    # TradingView uses seconds
    ts = int(bar_time_ms // 1000)
    tv_symbol = f"BINANCE:{symbol}"
    return (
        "https://www.tradingview.com/chart/?"
        f"symbol={tv_symbol}&interval={interval}&time={ts}"
    )

SNAPSHOT_PATH = Path("utils/event_snapshots.jsonl") 

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

                "close": candle.get("close"),
                "vwap": candle.get("vwap"),
                "atr": candle.get("atr"),
                "atr_percentile": candle.get("atr_percentile"),
                "volume": candle.get("volume"),
            })

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

print(f"\nDe-duplicated rows: {before} -> {len(df)}")

df["bar_time_hms"] = df["bar_time"].apply(
    lambda ms: datetime.utcfromtimestamp(ms / 1000).strftime("%H:%M:%S")
    if pd.notna(ms) else None
)

df["_alert_ts"] = pd.to_datetime(df["logged_at"], errors="coerce")

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

out_dir = Path("utils/review")
out_dir.mkdir(parents=True, exist_ok=True)

review_cols = [
    "alert_date", "alert_time", 
    "bar_time_hms", "symbol", "pattern",
    "flow_regime", "atr_percentile",
    "close_vs_vwap", "flow_bias",
    "volume", "close",
    "tv_link",
]

triggers = df[df["ok"] == True]

for pat, g in triggers.groupby("pattern"):
    p = out_dir / f"TRIGGERED_{pat}.csv"
    g.sort_values("_alert_ts")[review_cols].to_csv(p, index=False)
    print(f"[WRITE] {p} ({len(g)})")


# ---- flags ----
df["passed"] = df["ok"]  # pattern-level ok
df["meta_passed"] = df["pattern"].map(
    df.groupby(["logged_at", "symbol"])["ok"].max()
)

# ---- TRIGGERED (passed: true) ----
triggers = df[df["ok"] == True]

print("\n=== TRIGGERED SIGNALS ===")
print("Count:", len(triggers))

print("\nTriggered by pattern:")
print(triggers["pattern"].value_counts())

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
