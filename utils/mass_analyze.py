from pathlib import Path
import pandas as pd

review_dir = Path("utils/review")

trigger_files = sorted(review_dir.glob("TRIGGERED_*.csv"))
almost_files  = sorted(review_dir.glob("ALMOST_*.csv"))

print(f"Found {len(trigger_files)} TRIGGERED files")
print(f"Found {len(almost_files)} ALMOST files")

triggered = pd.concat((pd.read_csv(p) for p in trigger_files), ignore_index=True) if trigger_files else pd.DataFrame()
almost    = pd.concat((pd.read_csv(p) for p in almost_files),  ignore_index=True) if almost_files else pd.DataFrame()

if not triggered.empty:
    triggered["population"] = "TRIGGERED"
if not almost.empty:
    almost["population"] = "ALMOST"

df = pd.concat([triggered, almost], ignore_index=True)

print("\nMaster df shape:", df.shape)
print("Columns:", list(df.columns))
print("\nPopulation counts:\n", df["population"].value_counts(dropna=False) if not df.empty else "EMPTY")

print("\n=== Pattern counts by population ===")

import numpy as np

def infer_state(r):
    ap = r.get("atr_percentile")
    fb = r.get("flow_bias")

    # conservative defaults
    if pd.isna(ap):
        return "UNKNOWN"

    if ap < 20:
        return "DEAD"
    if ap >= 80:
        return "EXPANSION"

    # flow dominance bucket (optional)
    if fb is not None and not pd.isna(fb) and abs(fb) > 1e6:
        return "FLOW_DOMINANT"

    return "NORMAL"


def gate_action(r):
    """
    Simulated gating:
      - IGNORE: dead/unknown conditions
      - WATCH: conditions interesting but not allowed to act yet
      - ACT: allowed to act (tight gate)
    """
    pat = r["pattern"]
    state = r["state"]
    pop = r["population"]  # TRIGGERED or ALMOST

    # Global ignores
    if state in ("DEAD", "UNKNOWN"):
        return "IGNORE"

    # Pattern role assumptions (v0)
    if pat == "IGNITION":
        # ignition is a state transition signal:
        # allow it to WATCH in NORMAL/EXPANSION, ACT only if it truly triggered
        return "ACT" if pop == "TRIGGERED" and state == "EXPANSION" else "WATCH"

    if pat in ("TRAP", "VWAP_RECLAIM"):
        # confirmations: only act if already in active states
        if state in ("EXPANSION", "FLOW_DOMINANT"):
            return "ACT" if pop == "TRIGGERED" else "WATCH"
        return "WATCH"

    if pat == "PULLBACK":
        # maintenance signal: not actionable alone
        return "WATCH"

    if pat == "FAILED_BREAKOUT":
        # exhaustion signal: only worth watching unless in expansion
        if state == "EXPANSION":
            return "WATCH" if pop == "ALMOST" else "ACT"
        return "IGNORE"

    # fallback
    return "WATCH"

df["state"] = df.apply(infer_state, axis=1)
df["action"] = df.apply(gate_action, axis=1)

print("\n=== Simulated state gating: counts by pattern/action ===")
g = (
    df.groupby(["pattern", "action"])
      .size()
      .unstack(fill_value=0)
      .assign(total=lambda x: x.sum(axis=1))
      .sort_values("ACT", ascending=False)
)
print(g)

print("\n=== Overall action counts ===")
print(df["action"].value_counts())

pattern_counts = (
    df.groupby(["pattern", "population"])
      .size()
      .unstack(fill_value=0)
      .sort_values("TRIGGERED", ascending=False)
)

print("\n=== ACT distribution by pattern + state ===")

act = df[df["action"] == "ACT"]

(
    act.groupby(["pattern", "state"])
       .size()
       .unstack(fill_value=0)
       .pipe(print)
)

print("\n=== ACT population breakdown ===")
print(act["population"].value_counts())


pattern_counts["trigger_rate_%"] = (
    pattern_counts["TRIGGERED"] /
    (pattern_counts["TRIGGERED"] + pattern_counts["ALMOST"])
    * 100
).round(3)

print(pattern_counts)

print("\n=== State distribution (overall) ===")
print(df["state"].value_counts())

print("\n=== State distribution by pattern ===")
(
    df.groupby(["pattern", "state"])
      .size()
      .unstack(fill_value=0)
      .pipe(print)
)

def infer_htf_bias(r):
    """
    Cheap HTF proxy:
    Uses atr_percentile + flow as a stand-in for HTF trend.
    Replace later with real HTF data.
    """
    ap = r.get("atr_percentile")
    fb = r.get("flow_bias")

    if ap is None or pd.isna(ap):
        return "UNKNOWN"

    if ap > 70 and fb is not None:
        return "BULL" if fb > 0 else "BEAR"

    return "NEUTRAL"


def gate_action_v2(r):
    """
    Adds HTF confirmation to ACT logic.
    """
    base = r["action"]
    if base != "ACT":
        return base

    htf = r["htf_bias"]
    pat = r["pattern"]

    # directional patterns require HTF alignment
    if pat in ("TRAP", "VWAP_RECLAIM"):
        if htf == "NEUTRAL":
            return "WATCH"
    return "ACT"

df["htf_bias"] = df.apply(infer_htf_bias, axis=1)
df["action_v2"] = df.apply(gate_action_v2, axis=1)

print("\n=== ACT vs ACT_v2 (with HTF proxy) ===")
print(df.groupby(["action", "action_v2"]).size())

print("\n=== ACT_v2 by pattern ===")
(
    df[df["action_v2"] == "ACT"]
      .groupby("pattern")
      .size()
      .pipe(print)
)

print("\n=== ACT quality check: survived vs downgraded ===")

act_all = df[df["action"] == "ACT"]
survived = act_all[act_all["action_v2"] == "ACT"]
downgraded = act_all[act_all["action_v2"] == "WATCH"]

print("\nSurvived ACT count:", len(survived))
print("Downgraded ACT count:", len(downgraded))

for col in ["atr_percentile", "flow_bias", "close_vs_vwap", "volume"]:
    print(f"\n--- {col} ---")
    print("Survived:")
    print(survived[col].describe())
    print("Downgraded:")
    print(downgraded[col].describe())

print("\n=== Top ALMOST reasons by pattern (top 5 each) ===")

for pat in pattern_counts.index:
    sub = df[
        (df["pattern"] == pat) &
        (df["population"] == "ALMOST")
    ]

    if sub.empty:
        continue

    print(f"\n--- {pat} ---")
    print(
        sub["reason"]
        .value_counts()
        .head(5)
    )

STRUCTURAL_KEYWORDS = [
    "did not form",
    "no valid",
    "conditions did not",
    "no sweep",
]

def classify_reason(reason: str):
    if not isinstance(reason, str):
        return "UNKNOWN"
    r = reason.lower()
    for k in STRUCTURAL_KEYWORDS:
        if k in r:
            return "STRUCTURAL"
    return "FILTER"

df["failure_type"] = df["reason"].apply(classify_reason)

print("\n=== ALMOST failure type breakdown by pattern ===")

(
    df[df["population"] == "ALMOST"]
      .groupby(["pattern", "failure_type"])
      .size()
      .unstack(fill_value=0)
      .sort_index()
      .pipe(print)
)

print("\n=== IGNITION: FILTER failure diagnostics ===")

ign = df[
    (df["pattern"] == "IGNITION") &
    (df["population"] == "ALMOST")
]

print("Count:", len(ign))
print("\nTop reasons:")
print(ign["reason"].value_counts().head(10))

print("\n=== IGNITION: Distribution comparison ===")

ign_trg = df[
    (df["pattern"] == "IGNITION") &
    (df["population"] == "TRIGGERED")
]

ign_alm = df[
    (df["pattern"] == "IGNITION") &
    (df["population"] == "ALMOST")
]

for col in ["atr_percentile", "close_vs_vwap", "flow_bias", "volume"]:
    print(f"\n--- {col} ---")
    print("TRIGGERED:")
    print(ign_trg[col].describe())
    print("ALMOST:")
    print(ign_alm[col].describe())

