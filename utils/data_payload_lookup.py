import sys
import json


def clean_last_page_value(value):
    """
    Strip whitespace and wrapping quote characters from a lastPage value.
    Also fixes a Dialogflow CX PII-redaction quirk where "mesh" gets
    misclassified as a person's name and replaced with the literal token
    '[PERSON_NAME]' (e.g. '[PERSON_NAME]-wifi' -> 'mesh-wifi').
    Kept identical to the logic in dash_hist_final_chatbot.py so this
    diagnostic shows exactly what the main pipeline would compute.
    """
    value = str(value).strip()
    while len(value) >= 2 and value[0] in '"\'' and value[-1] == value[0]:
        value = value[1:-1].strip()
    value = value.replace('[PERSON_NAME]', 'mesh')
    return value


def find_conversation_records(json_file, conversation_id):
    try:
        with open(json_file, 'r', encoding='utf-8') as f:
            records = json.load(f)
    except Exception as e:
        print(f"Error reading JSON file '{json_file}': {e}")
        sys.exit(1)

    matches = []
    for rec in records:
        nlu_result = rec.get('nlu_result') or {}
        params = nlu_result.get('parameters') or {}
        conv_id = params.get('genesysConversationId')
        if conv_id is not None and str(conv_id) == conversation_id:
            matches.append(rec)

    return matches


def main():
    if len(sys.argv) not in (2, 3):
        print("Usage: python check_json.py <conversation_id> [json_file]")
        print("Example: python check_json.py 76119bde-841e-4d38-917d-02bf32c2e253")
        print("Example: python check_json.py 76119bde-841e-4d38-917d-02bf32c2e253 lastpage_data.json")
        sys.exit(1)

    conversation_id = sys.argv[1]
    json_file = sys.argv[2] if len(sys.argv) == 3 else "lastpage_data.json"

    print(f"Searching '{json_file}' for genesysConversationId = '{conversation_id}'...")
    print("-" * 70)

    matches = find_conversation_records(json_file, conversation_id)

    if not matches:
        print(f"No records found for conversation_id '{conversation_id}'.")
        print("This conversation is not present in this JSON file at all -")
        print("in the main script's backfill diagnostics this would show up as 'not_in_json'.")
        sys.exit(0)

    # Sort by turn_position ascending (records missing turn_position go last)
    def turn_sort_key(rec):
        t = rec.get('turn_position')
        return (t is None, t if t is not None else 0)

    matches.sort(key=turn_sort_key)

    print(f"Found {len(matches)} turn record(s) for this conversation:\n")

    best_turn = None
    best_value = None

    for rec in matches:
        nlu_result = rec.get('nlu_result') or {}
        params = nlu_result.get('parameters') or {}
        turn = rec.get('turn_position')
        raw_last_page = params.get('lastPage')

        cleaned = clean_last_page_value(raw_last_page) if raw_last_page is not None else None
        usable = cleaned is not None and cleaned != ''

        print(f"=== turn_position: {turn} ===")
        print(f"raw lastPage:     {raw_last_page!r}")
        print(f"cleaned lastPage: {cleaned!r}  {'(usable)' if usable else '(NOT usable - null/empty)'}")
        print("Full record:")
        print(json.dumps(rec, indent=2, default=str))
        print()

        if usable and turn is not None:
            if best_turn is None or turn > best_turn:
                best_turn = turn
                best_value = cleaned

    print("-" * 70)
    if best_value is not None:
        print(f"Main script would backfill last_page_final with: '{best_value}'")
        print(f"(taken from turn_position {best_turn} - the highest turn with a usable lastPage)")
    else:
        print("Main script would NOT backfill this conversation - no turn had a usable lastPage.")
        print("In the main script's diagnostics this would show up as 'in_json_no_lastpage'.")


if __name__ == "__main__":
    main()
