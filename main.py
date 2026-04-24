import argparse
import json
import pathlib
import os
from dotenv import load_dotenv

# Import your custom modules
# Notice we no longer import syllabus_parser!
from agent import ask_scheduler_agent
from gcal import (
    push_events_to_calendar,
    list_events,
    delete_event,
    load_events_from_file,
    clean_events
)
from optimizer import optimize_schedule

load_dotenv()

# Optional: If you created a dedicated calendar, paste the ID here.
# Otherwise, leave it as 'primary'
TARGET_CALENDAR_ID = os.getenv("TARGET_CALENDAR_ID", "primary")


def run_schedule_pipeline(input_filename: str, strategy: str):
    print(f"\n--- Starting Intelligent Pipeline for {input_filename} ---")

    path = pathlib.Path(input_filename)
    if not path.exists():
        print(f"Error: Could not find the file '{input_filename}'")
        return None

    # Step 1: User Context
    print("\n[Optional] Provide context to help the AI (e.g., 'The year is 2026. Class meets Mon/Wed at 10am.').")
    user_context = input("Context (press Enter to skip): ").strip()

    # Step 2: Native Multimodal Extraction via Gemini
    print("\n[1/3] Reading document natively and extracting Deadlines Manifest...")
    query = f"""
    Extract all assignments, projects, and exams into the strict JSON array format specified in your system instructions. 
    Absolutely no markdown, no explanations, just raw JSON.

    USER CONTEXT AND OVERRIDES:
    {user_context if user_context else "None provided. Infer times as best as you can."}
    """

    # Pass the exact file path directly to the agent (PDF or JSON)
    raw_llm_response = ask_scheduler_agent(str(path), query)

    # Step 3: Validate and Save the Manifest
    print("\n[2/3] Validating and Saving JSON Manifest...")
    try:
        clean_string = raw_llm_response.strip()
        if clean_string.startswith("```json"):
            clean_string = clean_string[7:]
        if clean_string.endswith("```"):
            clean_string = clean_string[:-3]

        new_deadlines = json.loads(clean_string.strip())

        # --- MERGE LOGIC ---
        manifest_path = pathlib.Path("assignments.json")
        master_manifest = []

        # Load existing deadlines if the file already exists
        if manifest_path.exists():
            with open(manifest_path, "r") as f:
                try:
                    master_manifest = json.load(f)
                except json.JSONDecodeError:
                    pass  # If the file is corrupted, just start fresh

        # Add the new class deadlines to the master list
        master_manifest.extend(new_deadlines)

        # Save the updated master list
        with open(manifest_path, "w") as f:
            json.dump(master_manifest, f, indent=4)

        print(f"Success! Found {len(new_deadlines)} new deadlines.")
        print(
            f"assignments.json updated. (Total tracked deadlines: {len(master_manifest)})")

        # We still only pass the *new* deadlines to the optimizer so it doesn't double-schedule old work
        deadlines_to_optimize = new_deadlines

    except json.JSONDecodeError:
        print("\nError: The LLM did not return valid JSON. Here is the raw output:")
        print(raw_llm_response)
        return None

    # Step 4: Run the Hill Climbing Optimizer
    print(f"\n[3/3] Running Logic Engine (Strategy: {strategy})...")

    # Load existing calendar state so the algorithm dodges busy times
    existing_events = load_events_from_file("my_schedule.json")

    # Run the math using the newly extracted deadlines
    optimized_schedule = optimize_schedule(
        deadlines_to_optimize, existing_events, strategy=strategy)

    return optimized_schedule


# --- Execution ---
if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="AI Study Scheduler CLI")
    subparsers = parser.add_subparsers(
        dest="command", help="Available commands", required=True)

    # 1. PUSH Command
    parser_push = subparsers.add_parser(
        "push", help="Parse a syllabus natively, optimize, and push events to Google Calendar")
    parser_push.add_argument(
        "input_file", help="Path to the .pdf or .json file")
    parser_push.add_argument("--strategy",
                             choices=["balanced", "weekend_warrior",
                                      "night_owl", "sprinter", "procrastinator"],
                             default="balanced",
                             help="Optimization heuristic to use")

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

    # 4. CLEAN Command
    parser_clean = subparsers.add_parser(
        "clean", help="Remove events from the calendar")
    group = parser_clean.add_mutually_exclusive_group(required=True)
    group.add_argument("--all", action="store_true",
                       help="Delete EVERY event on the calendar")
    group.add_argument("--past", action="store_true",
                       help="Delete only events that have already ended")

    args = parser.parse_args()

    # --- Command Routing ---
    if args.command == "push":
        calendar_events = run_schedule_pipeline(
            args.input_file, strategy=args.strategy)
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
            # Safely grab the start time
            start_time = e['start'].get('dateTime', e['start'].get('date'))
            print(f"[{e['id']}] {start_time} | {e['summary']}")

    elif args.command == "delete":
        delete_event(args.event_id, calendar_id=TARGET_CALENDAR_ID)

    elif args.command == "clean":
        clean_events(calendar_id=TARGET_CALENDAR_ID, delete_all=args.all)
