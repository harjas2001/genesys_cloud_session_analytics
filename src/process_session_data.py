"""
process_session_data.py
─────────────────────────────────────────────────────────────────────────────
Core session data processing pipeline for conversational AI analytics.

Transforms raw chat/session exports from a contact centre platform into a
structured, PowerBI-ready CSV. Applies business rules to classify sessions,
determine containment and escalation, identify abandonment points, and
calculate week-ending dates for time-series grouping.

Output columns
──────────────
  conversation_id             — unique session identifier
  conversation_start_time     — ISO-formatted date (prevents Excel auto-formatting)
  week_ending                 — week ending date for PowerBI grouping (DD/MM/YYYY)
  is_non_interactive          — customer never sent a message
  is_bot_chat                 — session routed through the virtual agent
  is_contained                — bot chat with no agent escalation
  is_escalated                — bot chat that escalated to a live agent
  proceeded_to_info_bot_both  — session entered the info-collecting bot flow
  proceeded_to_info_bot_bot   — same, filtered to bot sessions only
  abandoned_in_info_bot       — session entered and abandoned info-collecting bot
  queue_name                  — escalation queue (bot chats only)
  last_page                   — last flow page reached (prefix only)
  last_page_final             — last flow page reached (full value)
  abandoned_I_bot_question    — question the customer failed on (from question_map.json)
  return_count                — how many times the customer has returned
  is_return_chat              — True if return_count >= 1

Configuration (via .env):
  INPUT_CSV, OUTPUT_CSV, BOT_INTENT_PREFIX, CONSUMER_DETAILS_ATTR

Private config (gitignored):
  config/bot_flows.txt    — flow names that identify the info-collecting bot
  config/question_map.json — maps last-successful-step values to question labels

GCP integration (optional):
  from gcp.storage_utils import download_input, upload_output
  from gcp.bigquery_utils import push_session_metrics
─────────────────────────────────────────────────────────────────────────────
"""

import sys
import re
import os
import json
from pathlib import Path
from datetime import datetime, timedelta

import pandas as pd
from dotenv import load_dotenv

load_dotenv()

# ── Config ────────────────────────────────────────────────────────────────────
INPUT_CSV             = os.getenv("INPUT_CSV",  "data/sessions.csv")
OUTPUT_CSV            = os.getenv("OUTPUT_CSV",  "output/session_metrics.csv")
BOT_INTENT_PREFIX     = os.getenv("BOT_INTENT_PREFIX", "VirtualAgent_Intent")
CONSUMER_DETAILS_ATTR = os.getenv("CONSUMER_DETAILS_ATTR", "ConsumerDetailsLastSuccessful")

# ── Private config — bot flows ────────────────────────────────────────────────
_BOT_FLOWS_FILE = Path("config/bot_flows.txt")
if not _BOT_FLOWS_FILE.exists():
    raise FileNotFoundError(
        f"\n[process_session_data] Bot flows config not found: {_BOT_FLOWS_FILE}\n"
        f"  Copy config/bot_flows.example.txt → config/bot_flows.txt\n"
        f"  and populate it with your info-collecting bot flow names."
    )
BOT_FLOWS = {
    line.strip()
    for line in _BOT_FLOWS_FILE.read_text(encoding="utf-8").splitlines()
    if line.strip() and not line.strip().startswith("#")
}

# ── Private config — question map ─────────────────────────────────────────────
_QUESTION_MAP_FILE = Path("config/question_map.json")
if not _QUESTION_MAP_FILE.exists():
    raise FileNotFoundError(
        f"\n[process_session_data] Question map config not found: {_QUESTION_MAP_FILE}\n"
        f"  Copy config/question_map.example.json → config/question_map.json\n"
        f"  and populate it with your consumer details step → question label mappings."
    )
QUESTION_MAP = json.loads(_QUESTION_MAP_FILE.read_text(encoding="utf-8"))


# ── Date utilities ────────────────────────────────────────────────────────────
DATE_FORMATS = [
    "%m/%d/%y %I:%M %p",     # 6/30/25 09:58 PM
    "%m/%d/%y %H:%M",        # 7/1/25 21:58
    "%m/%d/%y",              # 7/1/25
    "%Y-%m-%d %H:%M:%S",     # 2024-07-01 14:30:00
    "%Y-%m-%d",              # 2024-07-01
    "%m/%d/%Y %I:%M:%S %p",  # 06/30/2025 09:58:30 PM
    "%m/%d/%Y %I:%M %p",     # 06/30/2025 09:58 PM
    "%m/%d/%Y %H:%M:%S",     # 06/30/2025 21:58:30
    "%m/%d/%Y %H:%M",        # 06/30/2025 21:58
    "%m/%d/%Y",              # 06/30/2025
    "%d/%m/%Y %H:%M:%S",     # 01/07/2024 14:30:00
    "%d/%m/%Y",              # 01/07/2024
    "%d-%m-%Y %H:%M:%S",     # 01-07-2024 14:30:00
    "%d-%m-%Y",              # 01-07-2024
    "%Y/%m/%d %H:%M:%S",     # 2024/07/01 14:30:00
    "%Y/%m/%d",              # 2024/07/01
    "%d %b %Y %H:%M:%S",     # 01 Jul 2024 14:30:00
    "%d %B %Y %H:%M:%S",     # 01 July 2024 14:30:00
    "%d %b %Y",              # 01 Jul 2024
    "%d %B %Y",              # 01 July 2024
]


def standardize_date(date_string: str) -> str:
    """
    Normalise date strings to ISO format (YYYY-MM-DD HH:MM:SS).
    Prevents Excel / PowerBI from auto-reformatting date columns.
    Returns DATE_<original> if no format matches.
    """
    if not date_string or str(date_string).strip() in ("", "nan"):
        return ""

    date_str = str(date_string).strip()

    for fmt in DATE_FORMATS:
        try:
            parsed = datetime.strptime(date_str, fmt)
            if parsed.year < 100:
                parsed = parsed.replace(year=parsed.year + (2000 if parsed.year <= 30 else 1900))
            return parsed.strftime("%Y-%m-%d %H:%M:%S")
        except ValueError:
            continue

    print(f"Warning: Could not parse date: {date_str}")
    return f"DATE_{date_str}"


def get_week_ending(iso_date: str) -> str:
    """
    Return the Sunday week-ending date for a given ISO datetime string.
    Format: DD/MM/YYYY — used as a grouping key in PowerBI.
    """
    if not iso_date or iso_date.startswith("DATE_") or iso_date.strip() in ("", "nan"):
        return ""

    try:
        date_obj = datetime.strptime(iso_date, "%Y-%m-%d %H:%M:%S")
        days_until_sunday = (6 - date_obj.weekday()) % 7
        week_end = date_obj + timedelta(days=days_until_sunday)
        return week_end.strftime("%d/%m/%Y")
    except ValueError:
        print(f"Warning: Could not calculate week ending for: {iso_date}")
        return "INVALID_DATE"


# ── Core processing ───────────────────────────────────────────────────────────
def resolve_abandoned_question(consumer_details_value: str) -> str:
    """
    Map the last successful step value to the question the customer abandoned on.
    Mapping loaded from config/question_map.json (gitignored).
    """
    if consumer_details_value in QUESTION_MAP:
        return QUESTION_MAP[consumer_details_value]
    return QUESTION_MAP.get("_default", "different question")


def process_session_data(input_file: str, output_file: str) -> bool:
    """
    Process raw chat session export and write structured metrics CSV.
    """
    try:
        df = pd.read_csv(input_file)
        print(f"Loaded {len(df):,} rows from {input_file}")
    except Exception as e:
        print(f"Error reading input file: {e}")
        return False

    output_data = []

    for _, row in df.iterrows():
        conversation_id         = row.get("Conversation ID", "")
        raw_date                = row.get("Date", "")
        conversation_start_time = standardize_date(raw_date)
        week_ending             = get_week_ending(conversation_start_time)

        customer_participated = str(row.get("Customer Participated", "")).strip().upper()
        is_non_interactive    = customer_participated == "NO"

        # ── Bot session detection ─────────────────────────────────────────────
        successful_outcomes  = str(row.get("Successful Outcomes",  "")).strip()
        failed_outcomes      = str(row.get("Failed Outcomes",      "")).strip()
        incomplete_outcomes  = str(row.get("Incomplete Outcomes",  "")).strip()
        is_bot_chat = any(
            BOT_INTENT_PREFIX in col
            for col in [successful_outcomes, failed_outcomes, incomplete_outcomes]
        )

        # ── Containment / escalation ──────────────────────────────────────────
        first_queue  = str(row.get("First Queue", "")).strip()
        if is_bot_chat:
            is_contained = first_queue in ("", "nan")
            is_escalated = not is_contained
        else:
            is_contained = False
            is_escalated = False

        # ── Info bot flow detection ───────────────────────────────────────────
        flow_column = str(row.get("Flow", "")).strip()
        flows = [f.strip() for f in flow_column.split(";")] if flow_column and flow_column != "nan" else []
        proceeded_to_info_bot     = any(flow in BOT_FLOWS for flow in flows)
        proceeded_to_info_bot_bot = is_bot_chat and proceeded_to_info_bot

        abandoned_in_info_bot = proceeded_to_info_bot and first_queue in ("", "nan")

        queue_name    = first_queue if is_bot_chat and first_queue not in ("", "nan") else ""
        abandoned_question = ""
        last_page     = ""
        last_page_final = ""
        return_count  = ""
        is_return_chat = False

        # ── Participant attribute parsing (bot sessions only) ─────────────────
        if is_bot_chat:
            participant_attributes = str(row.get("Participant Attributes Formatted", "")).strip()

            if participant_attributes and participant_attributes.lower() != "nan":
                # Last page reached
                last_page_match = re.search(r"lastPage:([^;]+)", participant_attributes)
                if last_page_match:
                    last_page_value = last_page_match.group(1).strip()
                    last_page_final = last_page_value
                    last_page = last_page_value.split(".")[0] if "." in last_page_value else last_page_value
                else:
                    last_page = last_page_final = "NO LastPage"

                # Return count
                return_count_match = re.search(r"returnCount:([^;]+)", participant_attributes)
                if return_count_match:
                    return_count = return_count_match.group(1).strip()
                    try:
                        is_return_chat = int(return_count) >= 1
                    except (ValueError, TypeError):
                        is_return_chat = False
                else:
                    return_count  = "0"
                    is_return_chat = False

                # Abandoned question mapping
                if abandoned_in_info_bot:
                    consumer_match = re.search(
                        rf"{re.escape(CONSUMER_DETAILS_ATTR)}:([^;]+)", participant_attributes
                    )
                    if consumer_match:
                        step_value = consumer_match.group(1).strip().strip("\"'")
                        abandoned_question = resolve_abandoned_question(step_value)
                    else:
                        abandoned_question = QUESTION_MAP.get("_missing", "missing value")
            else:
                last_page = last_page_final = "UNKNOWN"
                return_count  = "UNKNOWN"
                is_return_chat = False
                if abandoned_in_info_bot:
                    abandoned_question = QUESTION_MAP.get("_missing", "missing value")

        output_data.append({
            "conversation_id":              conversation_id,
            "conversation_start_time":      conversation_start_time,
            "week_ending":                  week_ending,
            "is_non_interactive":           is_non_interactive,
            "is_bot_chat":                  is_bot_chat,
            "is_contained":                 is_contained,
            "is_escalated":                 is_escalated,
            "proceeded_to_info_bot_both":   proceeded_to_info_bot,
            "proceeded_to_info_bot_bot":    proceeded_to_info_bot_bot,
            "abandoned_in_info_bot":        abandoned_in_info_bot,
            "queue_name":                   queue_name,
            "last_page":                    last_page,
            "last_page_final":              last_page_final,
            "abandoned_I_bot_question":     abandoned_question,
            "return_count":                 return_count,
            "is_return_chat":               is_return_chat,
        })

    output_df = pd.DataFrame(output_data)

    # Serialise booleans to strings to prevent PowerBI type conflicts
    bool_cols = [
        "is_bot_chat", "is_contained", "is_escalated",
        "proceeded_to_info_bot_both", "proceeded_to_info_bot_bot",
        "abandoned_in_info_bot", "is_non_interactive", "is_return_chat",
    ]
    for col in bool_cols:
        output_df[col] = output_df[col].astype(str)

    try:
        Path(output_file).parent.mkdir(parents=True, exist_ok=True)
        output_df.to_csv(output_file, index=False, date_format="%Y-%m-%d %H:%M:%S")
        print(f"Saved {len(output_df):,} rows → {output_file}")
        _print_summary(output_df)
        return True
    except Exception as e:
        print(f"Error saving output: {e}")
        return False


def _print_summary(df: pd.DataFrame) -> None:
    """Print processing summary to console."""
    print("\n── Processing Summary ───────────────────────────────────────")
    print(f"Total sessions processed   : {len(df):,}")
    print(f"Bot sessions               : {(df['is_bot_chat'] == 'True').sum():,}")
    print(f"Non-bot sessions           : {(df['is_bot_chat'] == 'False').sum():,}")
    print(f"Contained                  : {(df['is_contained'] == 'True').sum():,}")
    print(f"Escalated                  : {(df['is_escalated'] == 'True').sum():,}")
    print(f"Proceeded to info bot      : {(df['proceeded_to_info_bot_both'] == 'True').sum():,}")
    print(f"Abandoned in info bot      : {(df['abandoned_in_info_bot'] == 'True').sum():,}")
    print(f"Return sessions            : {(df['is_return_chat'] == 'True').sum():,}")

    unique_weeks = sorted(df["week_ending"].unique())
    print(f"\n── Week Endings ─────────────────────────────────────────────")
    for week in unique_weeks:
        count = (df["week_ending"] == week).sum()
        print(f"  {week}: {count:,} sessions")


def main():
    if len(sys.argv) == 3:
        input_file, output_file = sys.argv[1], sys.argv[2]
    else:
        input_file, output_file = INPUT_CSV, OUTPUT_CSV

    print(f"Input  : {input_file}")
    print(f"Output : {output_file}")
    print("-" * 50)

    success = process_session_data(input_file, output_file)

    if success:
        print("-" * 50)
        print("Processing complete.")
        print("Dates standardised to YYYY-MM-DD HH:MM:SS — safe for Excel and PowerBI.")

        # ── Optional: push to GCS / BigQuery ─────────────────────────────────
        # from gcp.storage_utils import upload_output
        # upload_output(output_file)
        #
        # import pandas as pd
        # from gcp.bigquery_utils import push_session_metrics
        # push_session_metrics(pd.read_csv(output_file))
    else:
        print("Processing failed. Check error messages above.")


if __name__ == "__main__":
    main()
