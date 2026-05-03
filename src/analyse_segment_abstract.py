"""
analyse_segment_abandonment.py
─────────────────────────────────────────────────────────────────────────────
Segment-specific abandonment analysis for the info-collecting bot flow.

Reads raw session data and identifies which question customers in a specific
segment (e.g. new customers) abandoned on, based on the last successful step
recorded in participant attributes.

Useful for isolating abandonment patterns within a customer cohort —
e.g. "new customers are dropping off disproportionately at question 2."

Configuration (via .env):
  SEGMENT_INPUT_CSV    — raw session export CSV
  SEGMENT_ATTR_KEY     — participant attribute key tracking last successful step
─────────────────────────────────────────────────────────────────────────────
"""

import re
import os

import pandas as pd
from dotenv import load_dotenv

load_dotenv()

SEGMENT_INPUT_CSV = os.getenv("SEGMENT_INPUT_CSV", "data/sessions.csv")
SEGMENT_ATTR_KEY  = os.getenv("SEGMENT_ATTR_KEY",  "ConsumerDetailsLastSuccessful")

# ── Step values that indicate this segment ────────────────────────────────────
# These are the participant attribute values that identify a session as
# belonging to the target segment AND indicate abandonment at that point.
# Update these to match your platform's attribute values.
SEGMENT_STEP_VALUES = {
    "step_one",   # customer abandoned at the first question
    "step_two",   # customer abandoned at the second question
}


def process_sessions(input_file: str) -> None:
    """
    Analyse abandonment patterns for the configured segment.
    Prints counts and percentages for each abandonment step.
    """
    try:
        df = pd.read_csv(input_file)
        print(f"Loaded {len(df):,} rows from {input_file}")
    except Exception as e:
        print(f"Error loading file: {e}")
        return

    step_counts = {step: 0 for step in SEGMENT_STEP_VALUES}
    total_abandoned = 0

    for _, row in df.iterrows():
        participant_attributes = str(row.get("Participant Attributes Formatted", "")).strip()

        if not participant_attributes or participant_attributes.lower() == "nan":
            continue

        match = re.search(
            rf"{re.escape(SEGMENT_ATTR_KEY)}:([^;]+)", participant_attributes
        )
        if not match:
            continue

        step_value = match.group(1).strip().strip("\"'")

        if step_value in SEGMENT_STEP_VALUES:
            step_counts[step_value] += 1
            total_abandoned += 1

    # ── Results ───────────────────────────────────────────────────────────────
    print(f"\n── Segment Abandonment Analysis ─────────────────────────────")
    print(f"Total abandoned sessions in segment: {total_abandoned:,}")

    if total_abandoned == 0:
        print("No abandonment events found for the configured segment steps.")
        return

    for step, count in step_counts.items():
        pct = (count / total_abandoned) * 100
        print(f"  {step}: {count:,}  ({pct:.1f}%)")


def main():
    process_sessions(SEGMENT_INPUT_CSV)


if __name__ == "__main__":
    main()
