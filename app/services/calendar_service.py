"""
Google Calendar API wrapper.
All methods take a refresh_token and build a per-user service.
"""
import uuid
from datetime import datetime
from google.oauth2.credentials import Credentials
from google.auth.transport.requests import Request
from googleapiclient.discovery import build
from app.core.config import settings

SCOPES = ["https://www.googleapis.com/auth/calendar"]


def _get_service(refresh_token: str):
    creds = Credentials(
        token=None,
        refresh_token=refresh_token,
        token_uri="https://oauth2.googleapis.com/token",
        client_id=settings.GOOGLE_CLIENT_ID,
        client_secret=settings.GOOGLE_CLIENT_SECRET,
        scopes=SCOPES,
    )
    creds.refresh(Request())
    return build("calendar", "v3", credentials=creds, cache_discovery=False)


def create_meeting_with_meet(
    organizer_refresh_token: str,
    student_email: str,
    ta_email: str,
    professor_email: str,
    start_time: datetime,
    end_time: datetime,
    summary: str = "Scheduled Meeting",
) -> dict:
    """
    Creates a Google Calendar event with a Meet link.
    Invites student, TA, and professor. Returns the created event dict.
    """
    service = _get_service(organizer_refresh_token)
    event = {
        "summary": summary,
        "start": {"dateTime": start_time.isoformat() + "Z", "timeZone": "UTC"},
        "end": {"dateTime": end_time.isoformat() + "Z", "timeZone": "UTC"},
        "attendees": [
            {"email": student_email},
            {"email": ta_email},
            {"email": professor_email},
        ],
        "conferenceData": {
            "createRequest": {
                "requestId": str(uuid.uuid4()),
                "conferenceSolutionKey": {"type": "hangoutsMeet"},
            }
        },
        "reminders": {
            "useDefault": False,
            "overrides": [{"method": "email", "minutes": 30}, {"method": "popup", "minutes": 10}],
        },
    }
    result = service.events().insert(
        calendarId="primary",
        body=event,
        conferenceDataVersion=1,
        sendUpdates="all",
    ).execute()
    return result


def create_busy_block(
    refresh_token: str,
    title: str,
    start_time: datetime,
    end_time: datetime,
) -> dict:
    """
    Creates a busy block on the professor's calendar (no attendees, opaque).
    """
    service = _get_service(refresh_token)
    event = {
        "summary": title,
        "start": {"dateTime": start_time.isoformat() + "Z", "timeZone": "UTC"},
        "end": {"dateTime": end_time.isoformat() + "Z", "timeZone": "UTC"},
        "transparency": "opaque",
        "visibility": "private",
    }
    return service.events().insert(calendarId="primary", body=event).execute()


def delete_event(refresh_token: str, event_id: str) -> None:
    service = _get_service(refresh_token)
    service.events().delete(calendarId="primary", eventId=event_id).execute()


def get_upcoming_events(refresh_token: str, days: int = 14) -> list[dict]:
    """
    Fetches upcoming events from the user's primary Google Calendar.
    """
    from datetime import timezone
    service = _get_service(refresh_token)
    now = datetime.utcnow().replace(tzinfo=timezone.utc).isoformat()
    end = (datetime.utcnow().replace(tzinfo=timezone.utc) +
           __import__("datetime").timedelta(days=days)).isoformat()
    result = service.events().list(
        calendarId="primary",
        timeMin=now,
        timeMax=end,
        singleEvents=True,
        orderBy="startTime",
        maxResults=50,
    ).execute()
    events = []
    for e in result.get("items", []):
        start = e.get("start", {})
        end_ = e.get("end", {})
        events.append({
            "id": e.get("id"),
            "title": e.get("summary", "(No title)"),
            "start": start.get("dateTime") or start.get("date"),
            "end": end_.get("dateTime") or end_.get("date"),
            "meet_link": extract_meet_link(e),
        })
    return events


def get_busy_slots(refresh_token: str, days: int = 14) -> list[dict]:
    """
    Fetches only busy/free time slots from a user's calendar.
    Returns start/end times only — no event titles or details.
    """
    from datetime import timezone, timedelta
    service = _get_service(refresh_token)
    now = datetime.utcnow().replace(tzinfo=timezone.utc)
    end = now + timedelta(days=days)

    body = {
        "timeMin": now.isoformat(),
        "timeMax": end.isoformat(),
        "items": [{"id": "primary"}],
    }
    result = service.freebusy().query(body=body).execute()

    busy_slots = []
    for slot in result.get("calendars", {}).get("primary", {}).get("busy", []):
        busy_slots.append({
            "start": slot["start"],
            "end": slot["end"],
        })
    return busy_slots


def extract_meet_link(event: dict) -> str | None:
    conf = event.get("conferenceData", {})
    for ep in conf.get("entryPoints", []):
        if ep.get("entryPointType") == "video":
            return ep.get("uri")
    return None
