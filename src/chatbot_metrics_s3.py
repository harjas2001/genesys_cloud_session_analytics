import pandas as pd
import sys
import re
import os
import json
import glob
from datetime import datetime, timedelta

def standardize_date_format(date_string):
    """
    Standardize date format to prevent Excel auto-formatting issues.
    Converts various date formats to ISO format (YYYY-MM-DD HH:MM:SS)
    """
    if not date_string or date_string == 'nan' or str(date_string).strip() == '':
        return ''
    
    date_str = str(date_string).strip()
    
    # Common date formats to try parsing - ORDER MATTERS (most specific first)
    date_formats = [
        # Your specific format patterns first
        '%m/%d/%y %I:%M %p',     # 6/30/25 09:58 PM (most common in your data)
        '%m/%d/%y %H:%M',        # 7/1/25 21:58 (24-hour format, no AM/PM)
        '%m/%d/%y',              # 7/1/25 (date only)
        
        # Other common formats
        '%Y-%m-%d %H:%M:%S',     # 2024-07-01 14:30:00
        '%Y-%m-%d',              # 2024-07-01
        '%m/%d/%Y %I:%M:%S %p',  # 06/30/2025 09:58:30 PM
        '%m/%d/%Y %I:%M %p',     # 06/30/2025 09:58 PM
        '%m/%d/%Y %H:%M:%S',     # 06/30/2025 21:58:30
        '%m/%d/%Y %H:%M',        # 06/30/2025 21:58
        '%m/%d/%Y',              # 06/30/2025
        '%d/%m/%Y %H:%M:%S',     # 01/07/2024 14:30:00
        '%d/%m/%Y',              # 01/07/2024
        '%d-%m-%Y %H:%M:%S',     # 01-07-2024 14:30:00
        '%d-%m-%Y',              # 01-07-2024
        '%Y/%m/%d %H:%M:%S',     # 2024/07/01 14:30:00
        '%Y/%m/%d',              # 2024/07/01
        '%d %b %Y %H:%M:%S',     # 01 Jul 2024 14:30:00
        '%d %B %Y %H:%M:%S',     # 01 July 2024 14:30:00
        '%d %b %Y',              # 01 Jul 2024
        '%d %B %Y',              # 01 July 2024
    ]
    
    for fmt in date_formats:
        try:
            # Parse the date string
            parsed_date = datetime.strptime(date_str, fmt)
            
            # Handle 2-digit years - assume years 00-30 are 2000s, 31-99 are 1900s
            if parsed_date.year < 100:
                if parsed_date.year <= 30:
                    parsed_date = parsed_date.replace(year=parsed_date.year + 2000)
                else:
                    parsed_date = parsed_date.replace(year=parsed_date.year + 1900)
            
            # Return in ISO format (Excel won't auto-format this)
            return parsed_date.strftime('%Y-%m-%d %H:%M:%S')
        except ValueError:
            continue
    
    # If no format works, return original string with a prefix to prevent Excel formatting
    print(f"Warning: Could not parse date format: {date_str}")
    return f"DATE_{date_str}"  # Prefix prevents Excel auto-formatting

def get_week_ending_date(date_string):
    """
    Calculate the week ending date (Sunday) for a given date.
    Returns formatted string like 'DD/MM/YYYY'
    """
    if not date_string or date_string == 'nan' or str(date_string).strip() == '':
        return ''
    
    # If date starts with 'DATE_', it means it couldn't be parsed
    if str(date_string).startswith('DATE_'):
        return 'UNPARSED_DATE'
    
    try:
        # Parse the ISO formatted date
        date_obj = datetime.strptime(date_string, '%Y-%m-%d %H:%M:%S')
        
        # Calculate days until next Sunday (0=Monday, 6=Sunday)
        days_until_sunday = (6 - date_obj.weekday()) % 7
        
        # Calculate the week ending date (next Sunday)
        week_ending_date = date_obj + timedelta(days=days_until_sunday)
        
        # Format as 'DD/MM/YYYY'
        return f"{week_ending_date.strftime('%d/%m/%Y')}"
        
    except ValueError:
        print(f"Warning: Could not calculate week ending for: {date_string}")
        return 'INVALID_DATE'


def clean_last_page_value(value):
    """
    Strip whitespace and wrapping quote characters from a lastPage value.
    Handles repeated/nested quoting (e.g. '""nbn-troubleshooting""') by
    stripping in a loop rather than a single pass, so a value doesn't
    slip through with quotes still attached.

    Also fixes a Dialogflow CX PII-redaction quirk: the word "mesh" gets
    misclassified as a person's name and replaced with the literal token
    '[PERSON_NAME]' in the JSON export - e.g. '[PERSON_NAME]-wifi' should
    really be 'mesh-wifi', '[PERSON_NAME]-wifi.support' should really be
    'mesh-wifi.support', etc. Every occurrence of that exact token gets
    swapped back to 'mesh'.
    """
    value = str(value).strip()
    while len(value) >= 2 and value[0] in '"\'' and value[-1] == value[0]:
        value = value[1:-1].strip()
    value = value.replace('[PERSON_NAME]', 'mesh')
    return value


def split_last_page(last_page_value):
    """
    Given a full lastPage value (e.g. 'prepaid-activate.fail'), return the
    portion before the first dot, or the whole value if there's no dot.
    This mirrors the original inline extraction logic so 'last_page' and
    'last_page_final' stay consistent with each other, whether the value
    came from Participant Attributes Formatted or from the JSON backfill.
    """
    last_page_value = str(last_page_value).strip()
    if '.' in last_page_value:
        return last_page_value.split('.')[0]
    return last_page_value


def load_lastpage_lookup(json_file):
    """
    Build a lookup dict of genesysConversationId -> lastPage value, using
    the record with the HIGHEST turn_position that actually has a
    non-null/non-empty lastPage for that conversation. Records with a null
    or missing lastPage (e.g. turn_position 0 in the sample data) are
    skipped so they can't "win" over a real lastPage value from an earlier
    turn.
    """
    try:
        with open(json_file, 'r', encoding='utf-8') as f:
            records = json.load(f)
    except Exception as e:
        print(f"Warning: Could not read JSON file '{json_file}': {e}")
        return {}

    best_turn = {}      # conv_id -> turn_position of the current best match
    best_value = {}     # conv_id -> lastPage value of the current best match
    all_conv_ids = set()  # every conv_id seen in the JSON, regardless of lastPage
    skipped_no_lastpage = 0

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
            skipped_no_lastpage += 1
            continue  # this turn has no usable lastPage - don't let it win

        if conv_id not in best_turn or turn > best_turn[conv_id]:
            best_turn[conv_id] = turn
            best_value[conv_id] = clean_last_page_value(last_page_val)

    print(f"Loaded JSON lastPage data: {len(records)} total records, "
          f"{len(all_conv_ids)} unique conversation IDs present, "
          f"{len(best_value)} of them have a usable lastPage "
          f"({skipped_no_lastpage} turn-records skipped for having no lastPage).")

    return best_value, all_conv_ids


def backfill_last_page_from_json(output_df, lastpage_lookup, all_conv_ids):
    """
    For any row where last_page_final is blank ('') or 'NO LastPage',
    look up the conversation_id in the JSON-derived lookup dict. If found,
    overwrite last_page_final with the JSON value and re-derive last_page
    from it. Rows with no match are left exactly as they were.

    Tracks *why* a row wasn't backfilled, split into two distinct cases:
    - the conversation_id doesn't appear in the JSON at all (coverage gap)
    - the conversation_id appears in the JSON, but none of its turns had
      a usable lastPage value

    Returns:
        (output_df, unmatched_df) - output_df is the same DataFrame passed
        in, with backfilled values applied in place. unmatched_df lists
        every row that needed backfilling but couldn't be matched, tagged
        with an 'unmatched_reason' column ('not_in_json' or
        'in_json_no_lastpage').
    """
    unmatched_columns = [
        'conversation_id', 'conversation_start_time', 'week_ending',
        'is_tobi_chat', 'last_page_final', 'unmatched_reason'
    ]

    if not lastpage_lookup:
        print("No JSON lastPage lookup data available - skipping backfill step.")
        return output_df, pd.DataFrame(columns=unmatched_columns)

    needs_backfill_mask = output_df['last_page_final'].isin(['', 'NO LastPage'])
    candidate_count = int(needs_backfill_mask.sum())
    backfilled_count = 0
    not_in_json_count = 0
    in_json_no_lastpage_count = 0
    unmatched_rows = []

    for idx in output_df[needs_backfill_mask].index:
        conv_id = str(output_df.at[idx, 'conversation_id'])
        match = lastpage_lookup.get(conv_id)
        if match:
            output_df.at[idx, 'last_page_final'] = match
            output_df.at[idx, 'last_page'] = split_last_page(match)
            backfilled_count += 1
        else:
            if conv_id in all_conv_ids:
                reason = 'in_json_no_lastpage'
                in_json_no_lastpage_count += 1
            else:
                reason = 'not_in_json'
                not_in_json_count += 1
            unmatched_rows.append({
                'conversation_id': output_df.at[idx, 'conversation_id'],
                'conversation_start_time': output_df.at[idx, 'conversation_start_time'],
                'week_ending': output_df.at[idx, 'week_ending'],
                'is_tobi_chat': output_df.at[idx, 'is_tobi_chat'],
                'last_page_final': output_df.at[idx, 'last_page_final'],
                'unmatched_reason': reason
            })

    print(f"\n--- JSON lastPage Backfill ---")
    print(f"Rows with blank/'NO LastPage': {candidate_count}")
    print(f"Backfilled from JSON: {backfilled_count}")
    print(f"Left unchanged - conversation_id not found in JSON at all: {not_in_json_count}")
    print(f"Left unchanged - found in JSON but no turn had a lastPage: {in_json_no_lastpage_count}")

    unmatched_df = pd.DataFrame(unmatched_rows, columns=unmatched_columns)

    return output_df, unmatched_df


def process_chat_data(input_file, output_file, json_file=None, lastpage_lookup=None, all_conv_ids=None):
    """
    Process chat conversation data according to specified business rules,
    optionally backfill missing lastPage values from a JSON turn-level
    dataset, then aggregate rows by unique dimension combinations (same
    approach as the voicebot script) to reduce row count for Power BI.

    Args:
        input_file (str): Path to input CSV file
        output_file (str): Path to output CSV file
        json_file (str, optional): Path to JSON file containing turn-level
            records with genesysConversationId / turn_position / lastPage,
            used to backfill blank last_page_final values. Ignored if
            lastpage_lookup is provided (see below).
        lastpage_lookup (dict, optional): A pre-loaded lookup dict (as
            returned by load_lastpage_lookup). Pass this when calling
            process_chat_data repeatedly (e.g. from process_folder) so the
            JSON file is only read and parsed once instead of per file.
        all_conv_ids (set, optional): The matching all_conv_ids set from
            load_lastpage_lookup, required alongside lastpage_lookup.
    """
    # Read the input CSV
    try:
        df = pd.read_csv(input_file)
        print(f"Successfully loaded {len(df)} rows from {input_file}")
    except Exception as e:
        print(f"Error reading input file: {e}")
        return False
    
    # Initialize output dataframe
    output_data = []
    
    # Process each row
    for index, row in df.iterrows():
        # Extract basic information
        conversation_id = row.get('Conversation ID', '')
        
        # Standardize date format to prevent Excel auto-formatting
        raw_date = row.get('Date', '')
        conversation_start_time = standardize_date_format(raw_date)
        
        # Calculate week ending date for PowerBI grouping
        week_ending = get_week_ending_date(conversation_start_time)

        customer_participated = str(row.get('Customer Participated', '')).strip().upper()
        is_non_interactive = customer_participated == "NO"

        # Determine if it's a Tobi chat
        successful_outcomes = str(row.get('Successful Outcomes', '')).strip()
        failed_outcomes = str(row.get('Failed Outcomes', '')).strip()
        incomplete_outcomes = str(row.get('Incomplete Outcomes', '')).strip()
        is_tobi_chat = (
            'VF_TOBi_Intent' in successful_outcomes or
            'VF_TOBi_Intent' in failed_outcomes or
            'VF_TOBi_Intent' in incomplete_outcomes
        )
        
        # Determine containment and escalation (only for Tobi chats)
        first_queue = str(row.get('First Queue', '')).strip()
        if is_tobi_chat:
            is_contained = first_queue == '' or first_queue == 'nan'
            is_escalated = not is_contained
        else:
            is_contained = False
            is_escalated = False
        
        # Check if chat proceeded to info bot
        flow_column = str(row.get('Flow', '')).strip()
        flows = [flow.strip() for flow in flow_column.split(';')] if flow_column and flow_column != 'nan' else []
        proceeded_to_info_bot = any(
            flow in ['GLOBAL_ConsumerDetails_Bot', 'GLOBAL_RetailDetails_Bot_V2']
            for flow in flows
        )
        
        # Split info bot metrics by Tobi vs non-Tobi
        proceeded_to_info_bot_tobi = is_tobi_chat and proceeded_to_info_bot

        # Determine if abandoned in info bot
        abandoned_in_info_bot = (
            proceeded_to_info_bot and
            (first_queue == '' or first_queue == 'nan')
        )
        
        # Extract queue name (only for Tobi chats)
        queue_name = first_queue if is_tobi_chat and first_queue != '' and first_queue != 'nan' else ''

        # Extract lastPage value (only for Tobi chats)
        abandoned_question = '' #Determine which question customer abandons on if they do abandon on info-collecting bot
        last_page = ''
        last_page_final = ''  # Complete lastPage value
        return_count = ''
        is_return_chat = False
        if is_tobi_chat:
            participant_attributes = str(row.get('Participant Attributes Formatted', '')).strip()

            if participant_attributes and participant_attributes != 'nan' and participant_attributes != '':
                # Look for lastPage: pattern
                last_page_match = re.search(r'lastPage:([^;]+)', participant_attributes)
                if last_page_match:
                    last_page_value = clean_last_page_value(last_page_match.group(1))
                    
                    # Store the complete value in last_page_final
                    last_page_final = last_page_value
                    
                    # Extract value before the first dot for last_page, or whole value if no dot
                    last_page = split_last_page(last_page_value)
                else:
                    last_page = 'NO LastPage'
                    last_page_final = 'NO LastPage'
                
                # Look for returnCount: pattern
                return_count_match = re.search(r'returnCount:([^;]+)', participant_attributes)
                if return_count_match:
                    return_count = return_count_match.group(1).strip()
                    try:
                        is_return_chat = int(return_count) >= 1
                    except:
                        is_return_chat = False
                else:
                    return_count = '0'  # Has data but no returnCount = 0
                    is_return_chat = False

                #Abandoned in info bot question analysis
                if is_tobi_chat:
                    if abandoned_in_info_bot:
                        consumer_details_match = re.search(r'ConsumerDetailsLastSuccessful:([^;]+)', participant_attributes)
                        if consumer_details_match:
                            consumer_details_value = consumer_details_match.group(1).strip()
                            consumer_details_value = consumer_details_value.strip('"\'"')

                            #Map last successful step to failed question
                            if consumer_details_value == 'none':
                                abandoned_question = 'existing customer question'
                            elif consumer_details_value == 'name':
                                abandoned_question = 'date of birth'
                            elif consumer_details_value == 'dob':
                                abandoned_question = 'phone number (existing)'
                            elif consumer_details_value == 'firstName':
                                abandoned_question = 'phone number (new)'
                            elif consumer_details_value in ['existingPhoneNumber', 'newPhoneNumber']:
                                abandoned_question = 'DATA NOT MATCHING' #shouldnt be abandoned if they completed the ibot process
                            else:
                                abandoned_question = 'different question'
                        else:
                            abandoned_question = 'missing value' # No ConsumerDetailsLastSuccessful found for abandoned chat

            else:
                last_page = 'UNKNOWN'
                last_page_final = 'UNKNOWN'
                return_count = 'UNKNOWN'  # No data at all = BLANK
                is_return_chat = False
                if is_tobi_chat:
                    if abandoned_in_info_bot:
                        abandoned_question = 'missing value'

        # Create output row
        output_row = {
            'conversation_id': conversation_id,
            'conversation_start_time': conversation_start_time,
            'week_ending': week_ending,
            'is_non_interactive': is_non_interactive,
            'is_tobi_chat': is_tobi_chat,
            'is_contained': is_contained,
            'is_escalated': is_escalated,
            'proceeded_to_info_bot_both': proceeded_to_info_bot,
            'proceeded_to_info_bot_tobi': proceeded_to_info_bot_tobi,
            'abandoned_in_info_bot': abandoned_in_info_bot,
            'queue_name': queue_name,
            'last_page': last_page,
            'last_page_final': last_page_final,
            'return_count': return_count, 
            'is_return_chat': is_return_chat,
            'abandoned_I_bot_question': abandoned_question
        }
        output_data.append(output_row)
    
    # Create output DataFrame (still one row per chat at this point)
    output_df = pd.DataFrame(output_data)

    print(f"\n--- Before JSON Backfill / Aggregation ---")
    print(f"Total rows (individual chats): {len(output_df)}")

    # ------------------------------------------------------------------
    # JSON lastPage backfill - MUST happen before aggregation, since it
    # relies on conversation_id (which aggregation collapses away)
    # ------------------------------------------------------------------
    unmatched_columns = [
        'conversation_id', 'conversation_start_time', 'week_ending',
        'is_tobi_chat', 'last_page_final', 'unmatched_reason'
    ]
    unmatched_lastpage_df = pd.DataFrame(columns=unmatched_columns)

    if lastpage_lookup is not None:
        # Pre-loaded lookup passed in (e.g. from process_folder) - reuse it
        # instead of re-reading the JSON file from disk.
        output_df, unmatched_lastpage_df = backfill_last_page_from_json(
            output_df, lastpage_lookup, all_conv_ids or set()
        )
    elif json_file:
        lastpage_lookup, all_conv_ids = load_lastpage_lookup(json_file)
        output_df, unmatched_lastpage_df = backfill_last_page_from_json(output_df, lastpage_lookup, all_conv_ids)
    else:
        print("\nNo JSON file provided - skipping lastPage backfill step.")

    # ------------------------------------------------------------------
    # Aggregate by unique combinations of dimensions (same pattern as
    # iinet_voicebot_final.py) to dramatically reduce row count while
    # keeping every metric available via chat_count.
    # ------------------------------------------------------------------
    dimension_columns = [
        'week_ending', 'is_non_interactive', 'is_tobi_chat', 'is_contained', 'is_escalated',
        'proceeded_to_info_bot_both', 'proceeded_to_info_bot_tobi', 'abandoned_in_info_bot',
        'queue_name', 'last_page', 'last_page_final', 'return_count', 'is_return_chat',
        'abandoned_I_bot_question'
    ]

    aggregated_df = output_df.groupby(dimension_columns, dropna=False).size().reset_index(name='chat_count')

    # Add a sample conversation_start_time for each group (first occurrence)
    # so date filtering still works in Power BI
    first_times = output_df.groupby(dimension_columns, dropna=False)['conversation_start_time'].first().reset_index()
    aggregated_df = aggregated_df.merge(first_times, on=dimension_columns, how='left')

    # Add a clean nullable-numeric version of return_count so Power BI can do
    # numeric aggregations (AVERAGE, SUM) without choking on 'UNKNOWN'. The
    # original text 'return_count' column is left untouched for filtering/audit.
    aggregated_df['return_count_numeric'] = pd.to_numeric(
        aggregated_df['return_count'], errors='coerce'
    ).astype('Int64')

    # Reorder columns: week_ending, conversation_start_time, chat_count, then dimensions
    # (return_count_numeric sits right after return_count)
    dims_with_numeric = []
    for c in dimension_columns:
        if c == 'week_ending':
            continue
        dims_with_numeric.append(c)
        if c == 'return_count':
            dims_with_numeric.append('return_count_numeric')
    column_order = ['week_ending', 'conversation_start_time', 'chat_count'] + dims_with_numeric
    aggregated_df = aggregated_df[column_order]

    print(f"\n--- After Aggregation ---")
    print(f"Total rows (aggregated): {len(aggregated_df)}")
    print(f"Reduction: {len(output_df)} → {len(aggregated_df)} rows "
          f"({100 * (1 - len(aggregated_df) / len(output_df)):.1f}% reduction)")

    # Save with explicit date formatting to prevent Excel auto-formatting
    try:
        # Convert boolean columns to strings to prevent formatting issues
        bool_columns = ['is_tobi_chat', 'is_contained', 'is_escalated', 
                       'proceeded_to_info_bot_both', 'proceeded_to_info_bot_tobi', 
                       'abandoned_in_info_bot', 'is_non_interactive', 'is_return_chat']
        
        for col in bool_columns:
            aggregated_df[col] = aggregated_df[col].astype(str)
        
        # Save to CSV with specific settings
        if not os.path.isfile(output_file):
            aggregated_df.to_csv(output_file, index=False, date_format='%Y-%m-%d %H:%M:%S')
        else:
            aggregated_df.to_csv(output_file, mode='a', header=False, index=False, date_format='%Y-%m-%d %H:%M:%S')
        print(f"\nSuccessfully saved {len(aggregated_df)} rows to {output_file}")

        # ------------------------------------------------------------------
        # Save the unmatched-lastpage rows (not aggregated - one row per
        # chat) to their own CSV so you can investigate coverage gaps.
        # Same append-on-rerun behavior as the main output file.
        # ------------------------------------------------------------------
        if not unmatched_lastpage_df.empty:
            unmatched_lastpage_df = unmatched_lastpage_df.copy()
            unmatched_lastpage_df['is_tobi_chat'] = unmatched_lastpage_df['is_tobi_chat'].astype(str)

            base, ext = os.path.splitext(output_file)
            unmatched_file = f"{base}_unmatched_lastpage{ext or '.csv'}"

            if not os.path.isfile(unmatched_file):
                unmatched_lastpage_df.to_csv(unmatched_file, index=False, date_format='%Y-%m-%d %H:%M:%S')
            else:
                unmatched_lastpage_df.to_csv(unmatched_file, mode='a', header=False, index=False, date_format='%Y-%m-%d %H:%M:%S')

            not_in_json = int((unmatched_lastpage_df['unmatched_reason'] == 'not_in_json').sum())
            no_lastpage = int((unmatched_lastpage_df['unmatched_reason'] == 'in_json_no_lastpage').sum())
            print(f"Saved {len(unmatched_lastpage_df)} unmatched-lastpage rows to {unmatched_file} "
                  f"({not_in_json} not_in_json, {no_lastpage} in_json_no_lastpage)")
        
        # Print summary statistics (based on chat_count, since rows are now aggregated)
        print("\n--- Processing Summary ---")
        print(f"Total conversations processed: {aggregated_df['chat_count'].sum()}")
        print(f"Tobi chats: {aggregated_df[aggregated_df['is_tobi_chat'] == 'True']['chat_count'].sum()}")
        print(f"Non-Tobi chats: {aggregated_df[aggregated_df['is_tobi_chat'] == 'False']['chat_count'].sum()}")
        print(f"Contained chats: {aggregated_df[aggregated_df['is_contained'] == 'True']['chat_count'].sum()}")
        print(f"Escalated chats: {aggregated_df[aggregated_df['is_escalated'] == 'True']['chat_count'].sum()}")
        print(f"Proceeded to info bot (all): {aggregated_df[aggregated_df['proceeded_to_info_bot_both'] == 'True']['chat_count'].sum()}")
        print(f"Proceeded to info bot (Tobi only): {aggregated_df[aggregated_df['proceeded_to_info_bot_tobi'] == 'True']['chat_count'].sum()}")
        print(f"Abandoned in info bot: {aggregated_df[aggregated_df['abandoned_in_info_bot'] == 'True']['chat_count'].sum()}")
        print(f"Chats with queue names: {aggregated_df[aggregated_df['queue_name'] != '']['chat_count'].sum()}")

        # Show unique week endings
        unique_weeks = sorted(aggregated_df['week_ending'].unique())
        print(f"\n--- Week Endings in Dataset ({len(unique_weeks)} weeks) ---")
        for week in unique_weeks:
            count = aggregated_df[aggregated_df['week_ending'] == week]['chat_count'].sum()
            rows = len(aggregated_df[aggregated_df['week_ending'] == week])
            print(f"{week}: {count} chats ({rows} aggregated rows)")

        # Show last_page vs last_page_final breakdown (post-backfill) for Tobi chats
        print(f"\n--- Last Page Breakdown (Tobi chats, post-backfill) ---")
        tobi_pages = aggregated_df[aggregated_df['is_tobi_chat'] == 'True'].groupby(
            ['last_page', 'last_page_final'])['chat_count'].sum().sort_values(ascending=False)
        for (lp, lpf), count in tobi_pages.head(20).items():
            print(f"'{lp}' → '{lpf}': {count} chats")
        if len(tobi_pages) > 20:
            print(f"... and {len(tobi_pages) - 20} more unique last_page combinations")

        # Show sample aggregated rows
        print(f"\n--- Sample Aggregated Rows (Top 10 by chat_count) ---")
        sample_rows = aggregated_df.nlargest(10, 'chat_count')
        print(sample_rows.to_string(index=False))

        return True
    except Exception as e:
        print(f"Error saving output file: {e}")
        return False

def process_folder(input_folder, output_file, json_file=None):
    """
    Process every top-level CSV file found in input_folder (not recursive),
    running each one through process_chat_data and appending all results
    into the same output_file / *_unmatched_lastpage.csv pair.

    The JSON lastPage lookup is loaded and parsed ONCE and reused across
    every file in the batch, rather than being re-read per file.

    One bad/malformed file will not stop the batch - it's logged as
    failed and the rest continue.
    """
    base, ext = os.path.splitext(output_file)
    unmatched_file = f"{base}_unmatched_lastpage{ext or '.csv'}"

    # Don't accidentally re-process the output files if they happen to
    # live inside the same folder as the inputs
    exclude_paths = {os.path.abspath(output_file), os.path.abspath(unmatched_file)}
    csv_files = sorted(
        f for f in glob.glob(os.path.join(input_folder, "*.csv"))
        if os.path.abspath(f) not in exclude_paths
    )

    if not csv_files:
        print(f"No CSV files found in folder: {input_folder}")
        return False

    print(f"Found {len(csv_files)} CSV file(s) in {input_folder}:")
    for f in csv_files:
        print(f"  - {os.path.basename(f)}")

    # Load the JSON lookup ONCE and reuse it for every file in the batch
    if json_file:
        lastpage_lookup, all_conv_ids = load_lastpage_lookup(json_file)
    else:
        lastpage_lookup, all_conv_ids = {}, set()
        print("\nNo JSON file provided - skipping lastPage backfill step for all files.")

    succeeded = []
    failed = []

    for i, csv_file in enumerate(csv_files, start=1):
        print("\n" + "=" * 70)
        print(f"[{i}/{len(csv_files)}] Processing {os.path.basename(csv_file)}")
        print("=" * 70)
        try:
            ok = process_chat_data(
                csv_file, output_file,
                lastpage_lookup=lastpage_lookup, all_conv_ids=all_conv_ids
            )
        except Exception as e:
            print(f"Unexpected error processing {csv_file}: {e}")
            ok = False

        if ok:
            succeeded.append(csv_file)
        else:
            failed.append(csv_file)

    print("\n" + "=" * 70)
    print("BATCH SUMMARY")
    print("=" * 70)
    print(f"Succeeded: {len(succeeded)}/{len(csv_files)}")
    for f in succeeded:
        print(f"  \u2705 {os.path.basename(f)}")
    if failed:
        print(f"Failed: {len(failed)}/{len(csv_files)}")
        for f in failed:
            print(f"  \u274c {os.path.basename(f)}")

    return len(succeeded) > 0


def main():
    """
    Main function to handle command line arguments and execute processing.
    Accepts either a single input CSV file OR a folder of CSV files.
    """
    if len(sys.argv) not in (3, 4):
        print("Usage: python dash_hist_final_chatbot.py <input_file.csv OR input_folder> <output_file.csv> [lastpage_data.json]")
        print("Example (single file): python dash_hist_final_chatbot.py input.csv output.csv")
        print("Example (with JSON backfill): python dash_hist_final_chatbot.py input.csv output.csv lastpage_data.json")
        print("Example (whole folder): python dash_hist_final_chatbot.py ./raw_exports output.csv lastpage_data.json")
        return
    
    input_path = sys.argv[1]
    output_file = sys.argv[2]
    json_file = sys.argv[3] if len(sys.argv) == 4 else None
    
    is_batch = os.path.isdir(input_path)

    print(f"Processing chat data with date standardization, JSON lastPage backfill, and aggregation...")
    print(f"Input path: {input_path} ({'folder - batch mode' if is_batch else 'single file'})")
    print(f"Output file: {output_file}")
    print(f"JSON lastPage file: {json_file if json_file else '(none provided - backfill step skipped)'}")
    print("-" * 70)
    
    if is_batch:
        success = process_folder(input_path, output_file, json_file)
    else:
        success = process_chat_data(input_path, output_file, json_file)
    
    if success:
        print("-" * 70)
        print("Processing completed successfully!")
        print("\nNOTE: All dates have been standardized to YYYY-MM-DD HH:MM:SS format")
        print("Week ending dates have been added in 'DD/MM/YYYY' format for PowerBI grouping.")
        print("Blank/'NO LastPage' values were backfilled from the JSON dataset where a match was found.")
        print("Rows have been aggregated by unique dimension combinations, with a 'chat_count' column.")
        print("\n📊 IMPORTANT: Update your Power BI measures to use SUM instead of COUNTROWS:")
        print("   Total Chats = SUM('output'[chat_count])")
        print("   Tobi Chats = CALCULATE(SUM('output'[chat_count]), 'output'[is_tobi_chat] = TRUE())")
        print("   Contained Chats = CALCULATE(SUM('output'[chat_count]), 'output'[is_contained] = TRUE())")
        print("   etc.")
    else:
        print("-" * 70)
        print("Processing failed. Please check the error messages above.")

if __name__ == "__main__":
    main()
