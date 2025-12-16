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

pattern_counts = (
    df.groupby(["pattern", "population"])
      .size()
      .unstack(fill_value=0)
      .sort_values("TRIGGERED", ascending=False)
)

pattern_counts["trigger_rate_%"] = (
    pattern_counts["TRIGGERED"] /
    (pattern_counts["TRIGGERED"] + pattern_counts["ALMOST"])
    * 100
).round(3)

print(pattern_counts)

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

