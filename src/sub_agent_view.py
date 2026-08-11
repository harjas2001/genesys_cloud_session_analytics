"""
presale_queue_analysis.py

Filters a raw Genesys chat export down to "pre-sale" journeys (identified by
a user-supplied list of lastPage values) and prints containment, escalation,
info-bot, return-chat, and queue-transfer metrics to the CLI.

This reuses the same lastPage / returnCount extraction logic as
dash_hist_final_chatbot.py (Participant Attributes Formatted parsing +
optional JSON backfill), but instead of writing an aggregated Power BI file,
it filters to a specific set of lastPage values and reports the numbers
straight to the console.

NEW in this script (not in the original): parses the 'Queue' column
(distinct from 'First Queue') to work out, per chat:
  - the queue the chat started in
  - whether it was transferred at all
  - if transferred, which queue it ultimately ended up in

Queue column semantics (semicolon-separated, in order visited):
  "Q_A"                -> not transferred. queue = "Q_A"
  "Q_A; Q_B; Q_C"       -> transferred. queue = "Q_A", transferred_to = "Q_C"
  (Any queues in between the first and last are "pass-through" queues and
  are captured in queue_path for reference, but the reported
  "transferred_to" is always the LAST queue in the list - i.e. where the
  chat ended up.)

Usage:
    python presale_queue_analysis.py <input.csv OR folder> <lastpage_values> [--json lastpage_data.json] [--export out.csv]

    <lastpage_values> can be either:
      - a comma-separated list, e.g. "nbn-sales,mobile-plans,broadband-signup"
      - a path to a text file with one lastPage value per line (# comments
        and blank lines are ignored)

Examples:
    python presale_queue_analysis.py chats.csv "nbn-sales,mobile-signup"
    python presale_queue_analysis.py ./raw_exports presale_lastpages.txt --json lastpage_data.json
    python presale_queue_analysis.py chats.csv presale_lastpages.txt --export presale_matched.csv
"""

import argparse
import glob
import json
import os
import re
import sys
from collections import Counter

import pandas as pd


# ---------------------------------------------------------------------------
# Shared helpers (mirrors dash_hist_final_chatbot.py so lastPage values line
# up exactly the same way they would in the main dashboard pipeline)
# ---------------------------------------------------------------------------

def clean_last_page_value(value):
    """Strip whitespace/quotes and fix the '[PERSON_NAME]' -> 'mesh' PII quirk."""
    value = str(value).strip()
    while len(value) >= 2 and value[0] in '"\'' and value[-1] == value[0]:
        value = value[1:-1].strip()
    value = value.replace('[PERSON_NAME]', 'mesh')
    return value


def split_last_page(last_page_value):
    """Portion before the first dot, or the whole value if there's no dot."""
    last_page_value = str(last_page_value).strip()
    if '.' in last_page_value:
        return last_page_value.split('.')[0]
    return last_page_value


def load_lastpage_lookup(json_file):
    """conversation_id -> lastPage, using the highest turn_position with a
    usable lastPage. Same logic as the main dashboard script."""
    try:
        with open(json_file, 'r', encoding='utf-8') as f:
            records = json.load(f)
    except Exception as e:
        print(f"Warning: Could not read JSON file '{json_file}': {e}")
        return {}, set()

    best_turn = {}
    best_value = {}
    all_conv_ids = set()

    for rec in records:
        nlu_result = rec.get('nlu_result') or {}
        params = nlu_result.get('parameters') or {}
        conv_id = params.get('genesysConversationId')
        last_page_val = params.get('lastPage')
        turn = rec.get('turn_position')

        if not conv_id:
            continue
        conv_id = str(conv_id)
        all_conv_ids.add(conv_id)

        if turn is None:
            continue
        if last_page_val is None or clean_last_page_value(last_page_val) == '':
            continue

        if conv_id not in best_turn or turn > best_turn[conv_id]:
            best_turn[conv_id] = turn
            best_value[conv_id] = clean_last_page_value(last_page_val)

    print(f"Loaded JSON lastPage backfill: {len(all_conv_ids)} unique conversation IDs, "
          f"{len(best_value)} with a usable lastPage.")
    return best_value, all_conv_ids


def parse_queue_transfer(queue_value):
    """
    Parse the 'Queue' column into (initial_queue, transferred_to, is_transferred, path).

    "Q_A"               -> ("Q_A", "", False, ["Q_A"])
    "Q_A; Q_B; Q_C"      -> ("Q_A", "Q_C", True, ["Q_A", "Q_B", "Q_C"])
    "" / nan             -> ("", "", False, [])
    """
    queue_value = str(queue_value).strip()
    if not queue_value or queue_value.lower() == 'nan':
        return '', '', False, []

    parts = [p.strip() for p in queue_value.split(';') if p.strip()]
    if not parts:
        return '', '', False, []
    if len(parts) == 1:
        return parts[0], '', False, parts

    return parts[0], parts[-1], True, parts


def load_presale_lastpage_values(arg):
    """
    Accepts either a comma-separated string or a path to a text file
    (one value per line, '#' comments and blank lines ignored).
    Returns (original_list, normalized_lowercase_set).
    """
    if os.path.isfile(arg):
        with open(arg, 'r', encoding='utf-8') as f:
            values = [line.strip() for line in f
                      if line.strip() and not line.strip().startswith('#')]
    else:
        values = [v.strip() for v in arg.split(',') if v.strip()]

    values = [clean_last_page_value(v) for v in values]
    normalized = {v.lower() for v in values}
    return values, normalized


# ---------------------------------------------------------------------------
# Row-level extraction (same business rules as dash_hist_final_chatbot.py)
# ---------------------------------------------------------------------------

def extract_row(row):
    conversation_id = row.get('Conversation ID', '')
    date_raw = row.get('Date', '')

    customer_participated = str(row.get('Customer Participated', '')).strip().upper()
    is_non_interactive = customer_participated == "NO"

    successful_outcomes = str(row.get('Successful Outcomes', '')).strip()
    failed_outcomes = str(row.get('Failed Outcomes', '')).strip()
    incomplete_outcomes = str(row.get('Incomplete Outcomes', '')).strip()
    is_tobi_chat = (
        'VF_TOBi_Intent' in successful_outcomes or
        'VF_TOBi_Intent' in failed_outcomes or
        'VF_TOBi_Intent' in incomplete_outcomes
    )

    first_queue = str(row.get('First Queue', '')).strip()
    if is_tobi_chat:
        is_contained = first_queue == '' or first_queue == 'nan'
        is_escalated = not is_contained
    else:
        is_contained = False
        is_escalated = False

    flow_column = str(row.get('Flow', '')).strip()
    flows = [f.strip() for f in flow_column.split(';')] if flow_column and flow_column != 'nan' else []
    proceeded_to_info_bot = any(
        f in ['GLOBAL_ConsumerDetails_Bot', 'GLOBAL_RetailDetails_Bot_V2'] for f in flows
    )
    proceeded_to_info_bot_tobi = is_tobi_chat and proceeded_to_info_bot
    abandoned_in_info_bot = proceeded_to_info_bot and (first_queue == '' or first_queue == 'nan')

    last_page = ''
    last_page_final = ''
    return_count = ''
    is_return_chat = False
    if is_tobi_chat:
        participant_attributes = str(row.get('Participant Attributes Formatted', '')).strip()
        if participant_attributes and participant_attributes != 'nan':
            last_page_match = re.search(r'lastPage:([^;]+)', participant_attributes)
            if last_page_match:
                last_page_value = clean_last_page_value(last_page_match.group(1))
                last_page_final = last_page_value
                last_page = split_last_page(last_page_value)
            else:
                last_page = 'NO LastPage'
                last_page_final = 'NO LastPage'

            return_count_match = re.search(r'returnCount:([^;]+)', participant_attributes)
            if return_count_match:
                return_count = return_count_match.group(1).strip()
                try:
                    is_return_chat = int(return_count) >= 1
                except ValueError:
                    is_return_chat = False
            else:
                return_count = '0'
        else:
            last_page = 'UNKNOWN'
            last_page_final = 'UNKNOWN'
            return_count = 'UNKNOWN'

    # --- Queue column (separate from First Queue) ---
    queue_raw = row.get('Queue', '')
    chat_queue, transferred_to_queue, is_transferred, queue_path = parse_queue_transfer(queue_raw)

    return {
        'conversation_id': conversation_id,
        'date_raw': date_raw,
        'is_non_interactive': is_non_interactive,
        'is_tobi_chat': is_tobi_chat,
        'is_contained': is_contained,
        'is_escalated': is_escalated,
        'proceeded_to_info_bot': proceeded_to_info_bot,
        'proceeded_to_info_bot_tobi': proceeded_to_info_bot_tobi,
        'abandoned_in_info_bot': abandoned_in_info_bot,
        'last_page': last_page,
        'last_page_final': last_page_final,
        'return_count': return_count,
        'is_return_chat': is_return_chat,
        'chat_queue': chat_queue,
        'transferred_to_queue': transferred_to_queue,
        'is_transferred': is_transferred,
        'queue_path': ' -> '.join(queue_path),
    }


def gather_csv_files(input_path):
    if os.path.isdir(input_path):
        files = sorted(glob.glob(os.path.join(input_path, "*.csv")))
        if not files:
            print(f"No CSV files found in folder: {input_path}")
        return files
    return [input_path]


# ---------------------------------------------------------------------------
# Main pipeline
# ---------------------------------------------------------------------------

def run(input_path, lastpage_arg, json_file=None, export_path=None):
    presale_values, presale_normalized = load_presale_lastpage_values(lastpage_arg)
    print(f"Pre-sale lastPage values ({len(presale_values)}): {presale_values}")

    lastpage_lookup, all_conv_ids = ({}, set())
    if json_file:
        lastpage_lookup, all_conv_ids = load_lastpage_lookup(json_file)

    csv_files = gather_csv_files(input_path)
    if not csv_files:
        return False

    total_rows_scanned = 0
    matched_rows = []
    unmatched_lastpage_counter = Counter()  # diagnostic: what lastPage values DIDN'T match

    for csv_file in csv_files:
        try:
            df = pd.read_csv(csv_file)
        except Exception as e:
            print(f"Error reading {csv_file}: {e}")
            continue

        print(f"Loaded {len(df)} rows from {os.path.basename(csv_file)}")
        total_rows_scanned += len(df)

        for _, row in df.iterrows():
            rec = extract_row(row)

            # JSON backfill (only relevant for Tobi chats with blank/NO LastPage)
            if lastpage_lookup and rec['last_page_final'] in ('', 'NO LastPage'):
                match = lastpage_lookup.get(str(rec['conversation_id']))
                if match:
                    rec['last_page_final'] = match
                    rec['last_page'] = split_last_page(match)

            lp = rec['last_page'].strip().lower()
            lpf = rec['last_page_final'].strip().lower()

            is_presale = lp in presale_normalized or lpf in presale_normalized
            if is_presale:
                matched_value = rec['last_page_final'] if lpf in presale_normalized else rec['last_page']
                rec['matched_lastpage_value'] = matched_value
                matched_rows.append(rec)
            elif rec['is_tobi_chat'] and rec['last_page_final'] not in ('', 'NO LastPage', 'UNKNOWN'):
                unmatched_lastpage_counter[rec['last_page_final']] += 1

    print(f"\nTotal rows scanned across {len(csv_files)} file(s): {total_rows_scanned}")
    print(f"Pre-sale chats matched: {len(matched_rows)} "
          f"({100 * len(matched_rows) / total_rows_scanned:.1f}% of all rows)" if total_rows_scanned else "")

    if not matched_rows:
        print("\nNo rows matched the given lastPage values. Nothing further to report.")
        print("\nTop 20 lastPage values seen in the data that did NOT match (check spelling/casing):")
        for val, cnt in unmatched_lastpage_counter.most_common(20):
            print(f"  '{val}': {cnt}")
        return True

    matched_df = pd.DataFrame(matched_rows)

    if export_path:
        matched_df.to_csv(export_path, index=False)
        print(f"\nExported {len(matched_df)} matched pre-sale rows to {export_path}")

    print_metrics(matched_df, presale_values, unmatched_lastpage_counter)
    return True


def pct(n, d):
    return f"{100 * n / d:.1f}%" if d else "0.0%"


def print_metrics(df, presale_values, unmatched_lastpage_counter):
    n = len(df)

    print("\n" + "=" * 70)
    print("PRE-SALE JOURNEY METRICS")
    print("=" * 70)
    print(f"Total pre-sale chats: {n}")

    print("\n--- Breakdown by matched lastPage value ---")
    for val, cnt in df['matched_lastpage_value'].value_counts().items():
        print(f"  {val}: {cnt} ({pct(cnt, n)})")

    print("\n--- Chat Type ---")
    tobi_n = int(df['is_tobi_chat'].sum())
    print(f"Tobi chats: {tobi_n} ({pct(tobi_n, n)})")
    print(f"Non-Tobi chats: {n - tobi_n} ({pct(n - tobi_n, n)})")

    tobi_df = df[df['is_tobi_chat']]
    if len(tobi_df):
        print("\n--- Containment / Escalation (Tobi chats only) ---")
        contained_n = int(tobi_df['is_contained'].sum())
        escalated_n = int(tobi_df['is_escalated'].sum())
        print(f"Contained: {contained_n} ({pct(contained_n, len(tobi_df))})")
        print(f"Escalated: {escalated_n} ({pct(escalated_n, len(tobi_df))})")

    print("\n--- Info Bot ---")
    proceeded_n = int(df['proceeded_to_info_bot'].sum())
    proceeded_tobi_n = int(df['proceeded_to_info_bot_tobi'].sum())
    abandoned_n = int(df['abandoned_in_info_bot'].sum())
    print(f"Proceeded to info bot (all): {proceeded_n} ({pct(proceeded_n, n)})")
    print(f"Proceeded to info bot (Tobi only): {proceeded_tobi_n} ({pct(proceeded_tobi_n, n)})")
    print(f"Abandoned in info bot: {abandoned_n} ({pct(abandoned_n, proceeded_n)} of those who proceeded)")

    print("\n--- Return Chats ---")
    return_n = int(df['is_return_chat'].sum())
    print(f"Return chats: {return_n} ({pct(return_n, n)})")
    numeric_returns = pd.to_numeric(df['return_count'], errors='coerce').dropna()
    if len(numeric_returns):
        print(f"Average return count (where known): {numeric_returns.mean():.2f}")

    print("\n--- Queue Analysis (from 'Queue' column) ---")
    has_queue_n = int((df['chat_queue'] != '').sum())
    no_queue_n = n - has_queue_n
    print(f"Chats with queue data: {has_queue_n} ({pct(has_queue_n, n)})")
    print(f"Chats with no queue data: {no_queue_n} ({pct(no_queue_n, n)})")

    queued_df = df[df['chat_queue'] != '']
    if len(queued_df):
        transferred_n = int(queued_df['is_transferred'].sum())
        not_transferred_n = len(queued_df) - transferred_n
        print(f"Transferred: {transferred_n} ({pct(transferred_n, len(queued_df))} of chats with queue data)")
        print(f"Not transferred: {not_transferred_n} ({pct(not_transferred_n, len(queued_df))} of chats with queue data)")

        print("\nTop initial queues:")
        for q, cnt in queued_df['chat_queue'].value_counts().head(15).items():
            print(f"  {q}: {cnt}")

        transferred_df = queued_df[queued_df['is_transferred']]
        if len(transferred_df):
            print("\nTop 'transferred to' queues (final queue in the chain):")
            for q, cnt in transferred_df['transferred_to_queue'].value_counts().head(15).items():
                print(f"  {q}: {cnt}")

            print("\nTop full transfer paths:")
            for path, cnt in transferred_df['queue_path'].value_counts().head(15).items():
                print(f"  {path}: {cnt}")

    if unmatched_lastpage_counter:
        print("\n--- Diagnostic: other Tobi lastPage values seen (NOT counted as pre-sale) ---")
        print("(Use this to sanity-check your pre-sale lastPage list for typos/missing values)")
        for val, cnt in unmatched_lastpage_counter.most_common(15):
            print(f"  '{val}': {cnt}")


def main():
    parser = argparse.ArgumentParser(description="Filter chats to pre-sale journeys and print metrics.")
    parser.add_argument('input', help="Input CSV file or folder of CSV files")
    parser.add_argument('lastpage_values', help="Comma-separated lastPage values, or path to a text file (one per line)")
    parser.add_argument('--json', dest='json_file', default=None, help="Optional JSON file for lastPage backfill")
    parser.add_argument('--export', dest='export_path', default=None, help="Optional path to export matched rows as CSV")
    args = parser.parse_args()

    ok = run(args.input, args.lastpage_values, args.json_file, args.export_path)
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
