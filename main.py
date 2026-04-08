import argparse
import json
import pathlib

# Import your custom modules
from syllabus_parser import parse_to_file
from agent import ask_scheduler_agent


def run_schedule_pipeline(pdf_filename: str):
    print(f"\n--- Starting Pipeline for {pdf_filename} ---")

    path = pathlib.Path(pdf_filename)
    if not path.exists():
        print(f"Error: Could not find the file '{pdf_filename}'")
        return None

    # Create a dynamic output filename based on the input PDF (e.g., ai_syl.json)
    json_output_path = str(path.with_suffix(".json"))

    # Step 1: Parse the PDF using your custom strip-by-strip OCR script
    print("\n[1/3] Parsing document...")
    parse_to_file(pdf_filename, json_output_path)

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
    # Set up argument parsing to take the PDF name from the command line
    parser = argparse.ArgumentParser(
        description="Extract schedule events from a syllabus PDF.")
    parser.add_argument(
        "pdf_file", help="The path to the syllabus PDF file (e.g., ai_syl.pdf)")

    args = parser.parse_args()

    # Run the entire automated pipeline using the provided filename
    calendar_events = run_schedule_pipeline(args.pdf_file)

    # Print the final Python data structure
    if calendar_events:
        print("\n--- Final Extracted Data ---")
        for event in calendar_events:
            print(f"{event.get('start_time')} | {event.get('summary')}")
