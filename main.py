import argparse
import json
import pathlib
import os
import datetime
from zoneinfo import ZoneInfo
from dotenv import load_dotenv

# Import your custom modules
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

TARGET_CALENDAR_ID = os.getenv("TARGET_CALENDAR_ID", "primary")
LOCAL_TZ = ZoneInfo("America/New_York")


def parse_iso_local(iso_str):
    """Bulletproof helper to parse bad LLM dates and guarantee timezone awareness."""
    # 1. If LLM just gave a date ("2026-02-24"), append a default time
    if "T" not in iso_str:
        iso_str += "T09:00:00"

    # 2. Handle missing or 'Z' timezones
    if iso_str.endswith('Z'):
        dt = datetime.datetime.fromisoformat(iso_str.replace('Z', '+00:00'))
    elif "+" not in iso_str[-6:] and "-" not in iso_str[-6:]:
        # If there's no offset at the end, force East Coast time
        dt = datetime.datetime.fromisoformat(iso_str + "-04:00")
    else:
        dt = datetime.datetime.fromisoformat(iso_str)

    return dt.astimezone(LOCAL_TZ)


def clean_manifest_file(wipe_all=False):
    """Removes past deadlines from assignments.json, or empties it entirely."""
    manifest_path = pathlib.Path("assignments.json")
    if not manifest_path.exists():
        return

    # If --all was used, just overwrite it with an empty list
    if wipe_all:
        with open(manifest_path, "w") as f:
            json.dump([], f)
        print("🗑️  Wiped assignments.json clean.")
        return

    # If --past was used, filter out old deadlines
    with open(manifest_path, "r") as f:
        try:
            master_manifest = json.load(f)
        except json.JSONDecodeError:
            return

    now = datetime.datetime.now(LOCAL_TZ)
    updated_manifest = []

    for task in master_manifest:
        try:
            task_due = parse_iso_local(task['due_date'])
            # Only keep tasks that are due in the future
            if task_due >= now:
                updated_manifest.append(task)
        except Exception:
            # If the date is corrupted, toss it out
            continue

    with open(manifest_path, "w") as f:
        json.dump(updated_manifest, f, indent=4)

    removed_count = len(master_manifest) - len(updated_manifest)
    print(
        f"🧹 Cleaned assignments.json: Removed {removed_count} past deadlines. {len(updated_manifest)} remaining.")


def align_start_date_with_rrule(dt, recurrence_list):
    """Forces the start date to snap to the correct day of the week based on the RRULE."""
    if not recurrence_list:
        return dt

    rrule_str = recurrence_list[0]
    # Map RRULE day codes to Python's internal weekday numbers (Mon=0, Sun=6)
    day_map = {"MO": 0, "TU": 1, "WE": 2, "TH": 3, "FR": 4, "SA": 5, "SU": 6}

    if "BYDAY=" in rrule_str:
        # Extract the 'TU,TH' part from the string
        days_str = rrule_str.split("BYDAY=")[1].split(";")[0]
        allowed_weekdays = [day_map[day]
                            for day in days_str.split(",") if day in day_map]

        if allowed_weekdays:
            # Keep adding 1 day until we land on an allowed day
            while dt.weekday() not in allowed_weekdays:
                dt += datetime.timedelta(days=1)

    return dt


def run_schedule_pipeline(input_filename: str, strategy: str):
    print(f"\n--- Starting Intelligent Pipeline for {input_filename} ---")

    path = pathlib.Path(input_filename)
    if not path.exists():
        print(f"Error: Could not find the file '{input_filename}'")
        return None

    print("\n[Optional] Provide context (e.g., 'CS 3130. Meets Mon/Wed 11am-12pm').")
    user_context = input("Context (press Enter to skip): ").strip()

    today_str = datetime.datetime.now(LOCAL_TZ).strftime("%Y-%m-%d")

    # Step 2: Native Multimodal Extraction via Gemini
    print("\n[1/3] Reading document natively and extracting Schedule Data...")

    query = f"""
    Extract all assignments, projects, exams, AND regular class meeting times.
    Absolutely no markdown, no explanations, just raw JSON.

    CRITICAL RULES:
    1. SEPARATE FIELDS: Put the Class Code (e.g., "CS 4710") in "class_code". Put the name of the assignment/exam in "title".
    2. EVENT TYPES (STRICT): 
       - "fixed" = Lectures, Exams, Midterms, and Finals. (Exams are ALWAYS fixed events).
       - "deadline" = Homework and Projects ONLY.
    3. TIMEZONE: Assume timezone is Eastern Time (EDT). All ISO dates MUST end with `-04:00` (e.g., `T11:00:00-04:00`).
    4. EXAM TIMES: If an exact time for an exam is not stated in the syllabus, ASSUME it takes place during the regular class meeting time provided in the User Context. Do NOT default to 9:00 AM.
    5. ENFORCE YEAR 2026: The syllabus is for Spring 2026. ALL dates must be in the year 2026.
    6. RECURRING CLASSES: If context says when class meets, create ONE "fixed" event. Set "start_time" to the first class on or after {today_str}. Include the "recurrence" array AND append an UNTIL rule based on the last day of class found in the syllabus (e.g., ["RRULE:FREQ=WEEKLY;BYDAY=TU,TH;UNTIL=20260428T235959Z"]).

    USER CONTEXT AND OVERRIDES:
    {user_context if user_context else "None provided."}

    REQUIRED JSON SCHEMA:
    - Fixed Events: {{"type": "fixed", "class_code": "...", "title": "...", "start_time": "...", "end_time": "...", "recurrence": ["..."]}}
    - Deadlines: {{"type": "deadline", "class_code": "...", "title": "...", "due_date": "...", "estimated_hours": 3}}
    """

    raw_llm_response = ask_scheduler_agent(str(path), query)

    # Step 3: Validate and Split the JSON Data
    print("\n[2/3] Validating and Routing JSON Data...")
    try:
        clean_string = raw_llm_response.strip()
        if clean_string.startswith("```json"):
            clean_string = clean_string[7:]
        if clean_string.endswith("```"):
            clean_string = clean_string[:-3]

        extracted_items = json.loads(clean_string.strip())

        fixed_events = [
            item for item in extracted_items if item.get("type") == "fixed"]
        new_deadlines = [
            item for item in extracted_items if item.get("type") == "deadline"]

        print(
            f"Found {len(fixed_events)} Fixed Events and {len(new_deadlines)} Flexible Deadlines.")

        manifest_path = pathlib.Path("assignments.json")
        master_manifest = []
        if manifest_path.exists():
            with open(manifest_path, "r") as f:
                try:
                    master_manifest = json.load(f)
                except json.JSONDecodeError:
                    pass

        master_manifest.extend(new_deadlines)
        with open(manifest_path, "w") as f:
            json.dump(master_manifest, f, indent=4)

    except json.JSONDecodeError:
        print("\nError: The LLM did not return valid JSON. Here is the raw output:")
        print(raw_llm_response)
        return None

    # Step 4: Execute the Two-Part Push
    print(f"\n[3/3] Generating Final Schedule (Strategy: {strategy})...")
    final_calendar_payload = []
    now = datetime.datetime.now(LOCAL_TZ)

    # Part A: Filter and format Fixed Events
    if fixed_events:
        print("Prepping Class Lectures and Fixed Exams...")
        for event in fixed_events:
            try:
                event_start = parse_iso_local(event["start_time"])
                event_end = parse_iso_local(event["end_time"]) if "end_time" in event else (
                    event_start + datetime.timedelta(hours=1))
            except Exception as e:
                print(f"⚠️ Could not parse date for {event.get('title')}: {e}")
                continue

            is_recurring = bool(event.get("recurrence"))

            # 1. THE MERCILESS PAST FILTER: Kill one-off past events immediately
            if not is_recurring and event_start < now:
                print(f"⏭️ Skipping past fixed event: {event.get('title')}")
                continue

            # 2. THE PHANTOM ANCHOR FIX: Force recurring events into the present
            if is_recurring and event_start < now:
                duration = event_end - event_start
                event_start = now  # Snap the anchor to today
                event_end = event_start + duration

            # 3. Align to the correct day of the week
            if is_recurring:
                duration = event_end - event_start
                event_start = align_start_date_with_rrule(
                    event_start, event["recurrence"])
                event_end = event_start + duration

            code = event.get("class_code", "CLASS")
            title = event.get("title", "Lecture")

            formatted_event = {
                "summary": f"🏫 [{code}] {title}",
                "start_time": event_start.isoformat(),
                "end_time": event_end.isoformat()
            }

            if is_recurring:
                formatted_event["recurrence"] = event["recurrence"]

            final_calendar_payload.append(formatted_event)

    # Part B: Run the Optimizer
    if new_deadlines:
        print("Running Optimizer for Study Blocks...")
        existing_events = load_events_from_file("my_schedule.json")

        # 4. THE OVERLAP FIX: Inject the newly created lectures into the "busy" list
        for new_class in final_calendar_payload:
            # We mock the Google Calendar format so the optimizer knows to dodge it
            existing_events.append({
                "start": {"dateTime": new_class["start_time"]},
                "end": {"dateTime": new_class["end_time"]}
            })

        optimized_study_blocks = optimize_schedule(
            new_deadlines, existing_events, strategy=strategy)
        final_calendar_payload.extend(optimized_study_blocks)

    return final_calendar_payload


# --- Execution ---
if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="AI Study Scheduler CLI")
    subparsers = parser.add_subparsers(
        dest="command", help="Available commands", required=True)

    parser_push = subparsers.add_parser(
        "push", help="Parse natively, optimize, and push events")
    parser_push.add_argument(
        "input_file", help="Path to the .pdf or .json file")
    parser_push.add_argument("--strategy", choices=["balanced", "weekend_warrior", "night_owl",
                             "sprinter", "procrastinator"], default="balanced", help="Optimization heuristic to use")

    parser_list = subparsers.add_parser(
        "list", help="Retrieve and print upcoming events")
    parser_list.add_argument(
        "--limit", type=int, default=15, help="Max number of events to pull")

    parser_delete = subparsers.add_parser(
        "delete", help="Delete a specific event")
    parser_delete.add_argument("event_id", help="Event ID to delete")

    parser_clean = subparsers.add_parser(
        "clean", help="Remove events from the calendar")
    group = parser_clean.add_mutually_exclusive_group(required=True)
    group.add_argument("--all", action="store_true", help="Delete EVERY event")
    group.add_argument("--past", action="store_true",
                       help="Delete only past events")

    args = parser.parse_args()

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
            start_time = e['start'].get('dateTime', e['start'].get('date'))
            print(f"[{e['id']}] {start_time} | {e['summary']}")

    elif args.command == "delete":
        delete_event(args.event_id, calendar_id=TARGET_CALENDAR_ID)

    elif args.command == "clean":
        clean_events(calendar_id=TARGET_CALENDAR_ID, delete_all=args.all)
        clean_manifest_file(wipe_all=args.all)
