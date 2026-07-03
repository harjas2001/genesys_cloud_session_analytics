import pandas as pd
import sys
import re
import os
from datetime import datetime, timedelta


def standardize_date_format(date_string):
    """
    Standardize date format to prevent Excel auto-formatting issues.
    Converts various date formats to ISO format (YYYY-MM-DD HH:MM:SS)
    """
    if not date_string or date_string == 'nan' or str(date_string).strip() == '':
        return ''

    date_str = str(date_string).strip()

    date_formats = [
        '%m/%d/%y %I:%M %p',     # 6/30/25 09:58 PM
        '%m/%d/%y %H:%M',        # 7/1/25 21:58
        '%m/%d/%y',              # 7/1/25
        '%Y-%m-%d %H:%M:%S',
        '%Y-%m-%d',
        '%m/%d/%Y %I:%M:%S %p',
        '%m/%d/%Y %I:%M %p',
        '%m/%d/%Y %H:%M:%S',
        '%m/%d/%Y %H:%M',
        '%m/%d/%Y',
        '%d/%m/%Y %H:%M:%S',
        '%d/%m/%Y',
        '%d-%m-%Y %H:%M:%S',
        '%d-%m-%Y',
        '%Y/%m/%d %H:%M:%S',
        '%Y/%m/%d',
        '%d %b %Y %H:%M:%S',
        '%d %B %Y %H:%M:%S',
        '%d %b %Y',
        '%d %B %Y',
    ]

    for fmt in date_formats:
        try:
            parsed_date = datetime.strptime(date_str, fmt)
            if parsed_date.year < 100:
                if parsed_date.year <= 30:
                    parsed_date = parsed_date.replace(year=parsed_date.year + 2000)
                else:
                    parsed_date = parsed_date.replace(year=parsed_date.year + 1900)
            return parsed_date.strftime('%Y-%m-%d %H:%M:%S')
        except ValueError:
            continue

    print(f"Warning: Could not parse date format: {date_str}")
    return f"DATE_{date_str}"


def get_week_ending_date(date_string):
    """
    Calculate the week ending date (Sunday) for a given date.
    Returns formatted string like 'DD/MM/YYYY'
    """
    if not date_string or date_string == 'nan' or str(date_string).strip() == '':
        return ''

    if str(date_string).startswith('DATE_'):
        return 'UNPARSED_DATE'

    try:
        date_obj = datetime.strptime(date_string, '%Y-%m-%d %H:%M:%S')
        days_until_sunday = (6 - date_obj.weekday()) % 7
        week_ending_date = date_obj + timedelta(days=days_until_sunday)
        return f"{week_ending_date.strftime('%d/%m/%Y')}"
    except ValueError:
        print(f"Warning: Could not calculate week ending for: {date_string}")
        return 'INVALID_DATE'


def process_chat_campaign_data(input_file, output_file):
    """
    Extract chatCampaign value for chats that have a First Queue populated.
    Skips chats with no First Queue, and skips chats where chatCampaign
    isn't present in Participant Attributes Formatted.
    """
    try:
        df = pd.read_csv(input_file)
        print(f"Successfully loaded {len(df)} rows from {input_file}")
    except Exception as e:
        print(f"Error reading input file: {e}")
        return False

    output_data = []
    skipped_no_queue = 0
    skipped_no_campaign = 0

    for index, row in df.iterrows():
        # Rule 1: skip if First Queue is not populated
        first_queue = str(row.get('First Queue', '')).strip()
        if first_queue == '' or first_queue == 'nan':
            skipped_no_queue += 1
            continue

        conversation_id = row.get('Conversation ID', '')

        raw_date = row.get('Date', '')
        conversation_start_time = standardize_date_format(raw_date)
        week_ending = get_week_ending_date(conversation_start_time)

        participant_attributes = str(row.get('Participant Attributes Formatted', '')).strip()

        # Rule 2: skip if chatCampaign not found
        chat_campaign_match = re.search(r'chatCampaign:([^;]+)', participant_attributes)
        if not chat_campaign_match:
            skipped_no_campaign += 1
            continue

        chat_campaign_value = chat_campaign_match.group(1).strip().strip('"\'')

        output_row = {
            'week_ending': week_ending,
            'conversation_start_time': conversation_start_time,
            'conversation_id': conversation_id,
            'chat_campaign': chat_campaign_value,
        }
        output_data.append(output_row)

    output_df = pd.DataFrame(output_data)

    try:
        if not os.path.isfile(output_file):
            output_df.to_csv(output_file, index=False)
        else:
            output_df.to_csv(output_file, mode='a', header=False, index=False)
        print(f"Successfully saved {len(output_df)} rows to {output_file}")

        print("\n--- Processing Summary ---")
        print(f"Total rows in input: {len(df)}")
        print(f"Skipped (no First Queue): {skipped_no_queue}")
        print(f"Skipped (no chatCampaign match): {skipped_no_campaign}")
        print(f"Rows written to output: {len(output_df)}")

        return True
    except Exception as e:
        print(f"Error saving output file: {e}")
        return False


def main():
    if len(sys.argv) != 3:
        print("Usage: python chat_campaign_extraction.py <input_file.csv> <output_file.csv>")
        print("Example: python chat_campaign_extraction.py input.csv chat_campaign_output.csv")
        return

    input_file = sys.argv[1]
    output_file = sys.argv[2]

    print("Processing chat campaign data...")
    print(f"Input file: {input_file}")
    print(f"Output file: {output_file}")
    print("-" * 50)

    success = process_chat_campaign_data(input_file, output_file)

    if success:
        print("-" * 50)
        print("Processing completed successfully!")
    else:
        print("-" * 50)
        print("Processing failed. Please check the error messages above.")


if __name__ == "__main__":
    main()
