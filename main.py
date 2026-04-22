import argparse
import json
import pathlib
import os
from dotenv import load_dotenv

# Import your custom modules
from syllabus_parser import parse_to_file
from agent import ask_scheduler_agent
from gcal import push_events_to_calendar, create_project_calendar, list_events, delete_event

load_dotenv()

# Optional: If you created a dedicated calendar, paste the ID here.
# Otherwise, leave it as 'primary'
TARGET_CALENDAR_ID = os.getenv("TARGET_CALENDAR_ID", "primary")


def run_schedule_pipeline(input_filename: str):
    print(f"\n--- Starting Pipeline for {input_filename} ---")

    path = pathlib.Path(input_filename)
    if not path.exists():
        print(f"Error: Could not find the file '{input_filename}'")
        return None

    # Determine file type and run OCR only if it's a PDF
    if path.suffix.lower() == ".json":
        print("\nJSON provided... skipping OCR because it is not needed.")
        json_output_path = str(path)
    elif path.suffix.lower() == ".pdf":
        json_output_path = str(path.with_suffix(".json"))
        print("\n[1/3] Parsing document...")
        parse_to_file(input_filename, json_output_path)
    else:
        print(
            f"Error: Unsupported file type '{path.suffix}'. Please provide a .pdf or .json file.")
        return None

    # Step 2: Query the LLM
    print("\n[2/3] Extracting calendar events via Gemini...")
    query = "Extract all assignments, projects, and exams into the strict Google Calendar JSON array format. No markdown, no explanations."
    raw_llm_response = ask_scheduler_agent(json_output_path, query)

    # Step 3: Parse the LLM output into Python objects
    print("\n[3/3] Validating JSON output...")
    try:
        # Clean up potential markdown formatting block wrappers from the LLM
        clean_string = raw_llm_response.strip()
        if clean_string.startswith("```json"):
            clean_string = clean_string[7:]
        if clean_string.endswith("```"):
            clean_string = clean_string[:-3]

        schedule_data = json.loads(clean_string.strip())

        print(
            f"\nSuccess! Found {len(schedule_data)} actionable calendar events.")
        return schedule_data

    except json.JSONDecodeError:
        print("\nError: The LLM did not return valid JSON. Here is the raw output:")
        print(raw_llm_response)
        return None


# --- Execution ---
if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="AI Study Scheduler CLI")

    # Create subcommands
    subparsers = parser.add_subparsers(
        dest="command", help="Available commands", required=True)

    # 1. PUSH Command
    parser_push = subparsers.add_parser(
        "push", help="Parse a syllabus and push events to Google Calendar")
    parser_push.add_argument(
        "input_file", help="Path to the .pdf or .json file")

    # 2. LIST Command
    parser_list = subparsers.add_parser(
        "list", help="Retrieve and print upcoming events from the calendar")
    parser_list.add_argument("--limit", type=int, default=15,
                             help="Max number of events to pull (default: 15)")

    # 3. DELETE Command
    parser_delete = subparsers.add_parser(
        "delete", help="Delete a specific event from the calendar")
    parser_delete.add_argument(
        "event_id", help="The exact Google Calendar Event ID to delete")

    args = parser.parse_args()

    # --- Command Routing ---
    if args.command == "push":
        calendar_events = run_schedule_pipeline(args.input_file)
        if calendar_events:
            push_events_to_calendar(
                calendar_events, calendar_id=TARGET_CALENDAR_ID)

    elif args.command == "list":
        events = list_events(calendar_id=TARGET_CALENDAR_ID,
                             max_results=args.limit)
        print("\n--- Upcoming Calendar Events ---")
        if not events:
            print("No upcoming events found.")
        for e in events:
            start_time = e['start'].get('dateTime', e['start'].get('date'))
            print(f"[{e['id']}] {start_time} | {e['summary']}")

    elif args.command == "delete":
        delete_event(args.event_id, calendar_id=TARGET_CALENDAR_ID)
