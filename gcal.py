import datetime
import os.path
import json

from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError

# If modifying these scopes, delete the file token.json.
SCOPES = ["https://www.googleapis.com/auth/calendar.events",
          "https://www.googleapis.com/auth/calendar"]

# ==========================================
# 1. AUTHENTICATION
# ==========================================


def get_gcal_service():
    creds = None
    if os.path.exists("token.json"):
        creds = Credentials.from_authorized_user_file("token.json", SCOPES)
    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            creds.refresh(Request())
        else:
            flow = InstalledAppFlow.from_client_secrets_file(
                "credentials.json", SCOPES)
            creds = flow.run_local_server(port=0)
        with open("token.json", "w") as token:
            token.write(creds.to_json())

    try:
        service = build("calendar", "v3", credentials=creds)
        return service
    except HttpError as error:
        print(f"An error occurred: {error}")
        return None

# ==========================================
# 2. LOCAL JSON STORAGE
# ==========================================


def save_events_to_file(events: list[dict], filename="my_schedule.json"):
    """Serializes a list of calendar events and saves them to a local JSON file."""
    try:
        with open(filename, "w") as file:
            json.dump(events, file, indent=4)
        print(f"Successfully saved {len(events)} events to {filename}")
    except Exception as e:
        print(f"Error saving events: {e}")


def load_events_from_file(filename="my_schedule.json"):
    """Reads serialized calendar events from a local JSON file."""
    if not os.path.exists(filename):
        print(f"No saved schedule found at {filename}.")
        return []

    try:
        with open(filename, "r") as file:
            events = json.load(file)
        return events
    except json.JSONDecodeError:
        print("Error: The schedule file is corrupted or empty.")
        return []

# ==========================================
# 3. GOOGLE CALENDAR OPERATIONS
# ==========================================


def create_project_calendar(calendar_name="AI Study Scheduler"):
    """Creates a new secondary calendar and returns its ID."""
    service = get_gcal_service()
    calendar = {
        'summary': calendar_name,
        'timeZone': 'America/New_York'
    }

    try:
        created_calendar = service.calendars().insert(body=calendar).execute()
        print(f"New calendar created: {calendar_name}")
        print(f"ID: {created_calendar['id']}")
        return created_calendar['id']
    except HttpError as error:
        print(f"An error occurred: {error}")
        return None


def list_events(calendar_id='primary', max_results=50, filename="my_schedule.json"):
    """Retrieves upcoming events from Google and saves a local backup."""
    service = get_gcal_service()
    now = datetime.datetime.utcnow().isoformat() + "Z"

    print(f"Retrieving the upcoming {max_results} events...")
    events_result = service.events().list(
        calendarId=calendar_id,
        timeMin=now,
        maxResults=max_results,
        singleEvents=True,
        orderBy='startTime'
    ).execute()

    events = events_result.get('items', [])
    if events:
        save_events_to_file(events, filename)

    return events


def delete_event(event_id, calendar_id='primary', filename="my_schedule.json"):
    """Deletes an event from Google Calendar AND the local JSON file."""
    service = get_gcal_service()
    try:
        service.events().delete(calendarId=calendar_id, eventId=event_id).execute()
        print(f"Event {event_id} deleted from Google Calendar successfully.")

        events = load_events_from_file(filename)
        original_count = len(events)
        events = [e for e in events if e.get('id') != event_id]

        if len(events) < original_count:
            save_events_to_file(events, filename)
            print("Event removed from local JSON storage.")

        return True

    except HttpError as error:
        print(f"Failed to delete event: {error}")
        return False


def push_events_to_calendar(events: list[dict], calendar_id='primary', filename="my_schedule.json"):
    """Pushes new events to Google and updates the local backup."""
    gcal_service = get_gcal_service()

    for event in events:
        print("Adding " + event["summary"] + " to calendar.")
        gcal_event = {
            "summary": event["summary"],
            "description": event.get("description", ""),
            "start": {
                "dateTime": event["start_time"],
                "timeZone": "America/New_York",
            },
            "end": {
                "dateTime": event["end_time"],
                "timeZone": "America/New_York",
            },
        }
        result = gcal_service.events().insert(
            calendarId=calendar_id, body=gcal_event).execute()
        print(f"  {event['summary']} → {result.get('htmlLink')}")

    print("\nRefreshing local backup with new event IDs...")
    list_events(calendar_id=calendar_id, max_results=50, filename=filename)


def clean_events(calendar_id='primary', filename="my_schedule.json", delete_all=False):
    """
    Deletes events from Google and local storage.
    If delete_all is True, it clears everything. 
    Otherwise, it only clears events that ended before now.
    """
    service = get_gcal_service()
    now = datetime.datetime.utcnow().isoformat() + "Z"

    try:
        if delete_all:
            print("Searching for ALL events to remove...")
            # No time constraints = everything
            events_result = service.events().list(calendarId=calendar_id).execute()
        else:
            print("Searching for past events to remove...")
            events_result = service.events().list(
                calendarId=calendar_id,
                timeMax=now,
                singleEvents=True
            ).execute()

        events_to_delete = events_result.get('items', [])

        if not events_to_delete:
            print("No events found to clean.")
            return

        print(f"Found {len(events_to_delete)} events. Starting cleanup...")
        for event in events_to_delete:
            service.events().delete(calendarId=calendar_id,
                                    eventId=event['id']).execute()
            print(f"  Deleted: {event.get('summary', 'Untitled')}")

        # Sync local storage
        if delete_all:
            save_events_to_file([], filename)
        else:
            local_events = load_events_from_file(filename)
            past_ids = {e['id'] for e in events_to_delete}
            updated_local = [
                e for e in local_events if e.get('id') not in past_ids]
            save_events_to_file(updated_local, filename)

        print("Cleanup complete.")

    except HttpError as error:
        print(f"An error occurred: {error}")
