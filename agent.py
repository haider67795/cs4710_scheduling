import os
import json
from dotenv import load_dotenv
from google import genai
from google.genai import types

load_dotenv()


def ask_scheduler_agent(file_path: str, user_query: str) -> str:
    """
    Reads a context file (JSON, MD, or PDF) and queries the Gemini model natively.
    """
    api_key = os.getenv("GEMINI_API_KEY")
    if not api_key:
        raise ValueError("API Key not found. Check your .env file!")

    client = genai.Client(api_key=api_key)

    system_prompt = """You are a strict data-extraction engine for a university scheduling agent.
    Your sole purpose is to parse raw syllabus text and extract actionable academic events into a strict, machine-readable JSON array.
    DO NOT ATTEMPT TO SCHEDULE THESE EVENTS. ONLY EXTRACT THE FACTS.

    EXTRACTION RULES:
    1. Target High-Value Events: Extract ALL homework release dates, deadlines, midterms, final exams, essays, and project milestones.
    2. Ignore Noise: Ignore general lecture topics, reading assignments, office hours, and administrative policies.
    3. Time Defaults: If an exact time is missing, default to "23:59:00" for deadlines, and "09:00:00" for exams.
    4. Year: Assume all dates occur in the year 2026.
    5. Output Format: Output ONLY valid JSON. Do not include markdown tags (like ```json), conversational text, or explanations.

    REQUIRED JSON SCHEMA:
    Return a JSON array where every object contains exactly these keys:
    - "summary": A concise, clear title (e.g., "HW1 Due: Rule-based agent").
    - "due_date": ISO 8601 formatted string (e.g., "2026-01-15T23:59:00Z").
    - "estimated_hours": An integer representing how many hours a student might need to complete this (default to 2 for HW, 5 for projects, 3 for exams).
    """

    config = types.GenerateContentConfig(
        system_instruction=system_prompt,
        temperature=0.15,
    )

    try:
        # Check if the user passed a raw PDF
        if file_path.lower().endswith('.pdf'):
            print(
                f"Uploading {file_path} directly to Gemini for visual processing...")

            # Upload the file to Google's temporary storage
            gemini_file = client.files.upload(file=file_path)

            # Send the file AND your prompt to the model
            response = client.models.generate_content(
                model='gemini-2.5-flash-lite',
                contents=[gemini_file, user_query],
                config=config
            )

            # Clean up the file from Google's servers immediately
            client.files.delete(name=gemini_file.name)

        else:
            # Fallback for standard text/JSON files
            with open(file_path, 'r', encoding='utf-8') as f:
                context_data = f.read()

            response = client.models.generate_content(
                model='gemini-2.5-flash-lite',
                # Inject the text directly as we did before
                contents=[
                    f"COURSE DATA TO PARSE:\n{context_data}", user_query],
                config=config
            )

        return response.text

    except Exception as e:
        return f"Error communicating with Gemini API: {str(e)}"


# --- Execution --- lowk i think is is useless ?
if __name__ == "__main__":
    file_to_load = "ai_syl_scraped.md"

    question = "Output a JSON array containing all homework assignments and their exact due dates."

    answer = ask_scheduler_agent(file_to_load, question)
    print(answer)
