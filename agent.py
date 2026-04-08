import os
import json
from dotenv import load_dotenv
from google import genai
from google.genai import types

load_dotenv()


def ask_scheduler_agent(context_file_path: str, user_query: str) -> str:
    """
    Reads a context file (JSON or MD) and queries the Gemini model.
    """
    api_key = os.getenv("GEMINI_API_KEY")
    if not api_key:
        raise ValueError("API Key not found. Check your .env file!")

    client = genai.Client(api_key=api_key)

    try:
        with open(context_file_path, 'r', encoding='utf-8') as f:

            context_data = f.read()
    except FileNotFoundError:
        return f"Error: Could not find the file at {context_file_path}"

    # 2. Build the System Prompt

    system_prompt = f"""You are a strict data-extraction engine for a university scheduling agent.
    Your sole purpose is to parse raw syllabus text and extract actionable academic events into a strict, machine-readable JSON array optimized for the Google Calendar API.

    EXTRACTION RULES:
    1. Target High-Value Events: Extract ALL homework release dates, homework deadlines, midterms, final exams, essays, project milestones, and presentations.
    2. Ignore Noise: Completely ignore general lecture topics, reading assignments, office hours, and administrative policies.
    3. Time Defaults: If an exact time is missing, default to "23:59:00" for deadlines, and "09:00:00" for exams/releases.
    4. Duration: For deadlines, make the start and end time identical. For exams, assume a 2-hour duration if an end time is not provided.
    5. Year: Assume all dates occur in the year 2026.
    6. Output Format: Output ONLY valid JSON. Do not include markdown tags (like ```json), conversational text, or explanations.

    REQUIRED JSON SCHEMA:
    Return a JSON array where every object contains exactly these keys:
    - "summary": A concise, clear title for the calendar event (e.g., "HW1 Due: Rule-based agent", "CS 4710 Midterm 1").
    - "description": A brief categorization or extra detail (e.g., "Project Milestone", "Final Exam").
    - "start_time": ISO 8601 formatted string (e.g., "2026-01-15T23:59:00").
    - "end_time": ISO 8601 formatted string (e.g., "2026-01-15T23:59:00").

    COURSE DATA TO PARSE:
    {context_data}
    """

    # 3. Call the Model
    response = client.models.generate_content(
        model='gemini-2.5-flash',
        contents=user_query,
        config=types.GenerateContentConfig(
            system_instruction=system_prompt,
            temperature=0.15,
        )
    )

    return response.text


# --- Execution ---
if __name__ == "__main__":
    file_to_load = "ai_syl_scraped.md"

    question = "Output a JSON array containing all homework assignments and their exact due dates."

    answer = ask_scheduler_agent(file_to_load, question)
    print(answer)
