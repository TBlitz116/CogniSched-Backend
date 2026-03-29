"""
Gemini-powered slot scheduling agent.
Takes a TA's natural language prompt + calendar context and returns
the best matching time slots.
"""

import json
import os
from datetime import datetime
from dotenv import load_dotenv
import google.generativeai as genai

load_dotenv()
genai.configure(api_key=os.getenv("GEMINI_API_KEY"))


def parse_slot_prompt(
    prompt: str,
    current_date: str,
    existing_meetings: list[dict],
    professor_blocks: list[dict],
    professor_busy: list[dict],
    priority: int,
    current_cognitive_score: float,
    burnout_risk: str,
) -> dict:
    """
    Interprets the TA's scheduling prompt and returns slot preferences.

    Returns:
        {
            "preferred_dates": ["2026-03-28", ...],
            "preferred_start_hour": 9-17 or null,
            "preferred_end_hour": 9-17 or null,
            "avoid_back_to_back": bool,
            "protect_deep_work": bool,
            "duration_minutes": 30,
            "reasoning": str
        }
    """

    meetings_summary = "\n".join(
        f"  - {m['start']} to {m['end']}"
        for m in existing_meetings
    ) or "  (no meetings scheduled)"

    blocks_summary = "\n".join(
        f"  - {b['start']} to {b['end']}"
        for b in professor_blocks
    ) or "  (no blocks)"

    busy_summary = "\n".join(
        f"  - {b['start']} to {b['end']}"
        for b in professor_busy
    ) or "  (no busy times)"

    system_prompt = f"""You are a scheduling assistant for a university TA.
Today's date is {current_date}.
Business hours are 9:00 AM to 5:00 PM, Monday through Friday.

Current context:
- Meeting priority: P{priority} (1=most urgent, 4=least urgent)
- TA's current cognitive load score: {current_cognitive_score}/100 (higher = more stressed)
- TA's burnout risk: {burnout_risk}
- Deep work hours (protected): 9:00 AM - 11:00 AM

TA's existing meetings:
{meetings_summary}

Professor's blocked times (unavailable):
{blocks_summary}

Professor's Google Calendar busy times:
{busy_summary}

The TA says: "{prompt}"

Based on the TA's request and the context above, determine the optimal scheduling preferences.
Return ONLY valid JSON with these fields:
- preferred_dates: list of date strings (YYYY-MM-DD) that match the TA's request. Include 1-5 dates.
- preferred_start_hour: earliest acceptable hour (9-17) or null if no preference
- preferred_end_hour: latest acceptable hour (9-17) or null if no preference
- avoid_back_to_back: boolean, true if the TA wants buffer time between meetings
- protect_deep_work: boolean, true if the slot should avoid 9-11am deep work window
- duration_minutes: meeting duration (default 30)
- reasoning: one sentence explaining your interpretation

Be smart about interpreting natural language:
- "tomorrow afternoon" → next day's date, start_hour=12, end_hour=17
- "sometime this week" → remaining weekdays this week
- "after lunch" → start_hour=13
- "early morning" → start_hour=9, end_hour=11
- "avoid burnout" or "light day" → protect_deep_work=true, avoid_back_to_back=true
- If burnout risk is HIGH, always set protect_deep_work=true and avoid_back_to_back=true
"""

    try:
        model = genai.GenerativeModel("gemini-2.0-flash")
        response = model.generate_content(system_prompt)
        text = response.text.strip()
        if text.startswith("```"):
            text = text.split("```")[1]
            if text.startswith("json"):
                text = text[4:]
        return json.loads(text.strip())
    except Exception as e:
        # Fallback: return generic preferences
        from datetime import timedelta
        today = datetime.strptime(current_date, "%Y-%m-%d")
        dates = []
        for i in range(1, 6):
            d = today + timedelta(days=i)
            if d.weekday() < 5:
                dates.append(d.strftime("%Y-%m-%d"))
        return {
            "preferred_dates": dates[:3],
            "preferred_start_hour": None,
            "preferred_end_hour": None,
            "avoid_back_to_back": burnout_risk == "HIGH",
            "protect_deep_work": burnout_risk in ("MEDIUM", "HIGH"),
            "duration_minutes": 30,
            "reasoning": f"Could not parse prompt, returning default preferences. Error: {str(e)}",
        }
