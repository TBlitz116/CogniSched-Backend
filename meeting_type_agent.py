"""
Analyzes a student's meeting history and recommends the appropriate meeting type:
  - SIMPLE_MEETING  : TA + student only, no professor needed
  - FULL_MEETING    : professor should be involved

The recommendation is based on the student's past meeting priorities, ticket escalations,
and decision outcomes — not just the current request's priority.
"""
import json
import os
import google.generativeai as genai
from dotenv import load_dotenv

load_dotenv()
genai.configure(api_key=os.getenv("GEMINI_API_KEY"))


def recommend_meeting_type(history: dict) -> dict:
    """
    history = {
        "student_name": str,
        "past_requests": [{"priority": int, "topic": str, "status": str, "created_at": str}],
        "past_tickets": [{"title": str, "shared_with_professor": bool, "status": str}],
        "past_decisions": [{"question": str, "outcome": str | None}],
        "booked_meeting_count": int,
    }

    Returns {"recommendation": "SIMPLE_MEETING" | "FULL_MEETING", "reasoning": str}
    """
    past_requests = history.get("past_requests", [])
    past_tickets = history.get("past_tickets", [])
    past_decisions = history.get("past_decisions", [])

    requests_summary = "\n".join(
        f"  - Priority P{r['priority']}, Topic: {r['topic']}, Status: {r['status']}, Date: {r['created_at']}"
        for r in past_requests
    ) or "  None"

    tickets_summary = "\n".join(
        f"  - \"{t['title']}\" — {'shared with professor' if t['shared_with_professor'] else 'handled by TA'}, Status: {t['status']}"
        for t in past_tickets
    ) or "  None"

    decisions_summary = "\n".join(
        f"  - \"{d['question']}\" — Outcome: {d['outcome'] or 'pending'}"
        for d in past_decisions
    ) or "  None"

    prompt = f"""You are an academic scheduling assistant. Analyze the meeting history for student "{history.get('student_name', 'Unknown')}" and recommend the most appropriate type of meeting.

Past meeting requests:
{requests_summary}

Action tickets raised:
{tickets_summary}

Decision cards sent to professor:
{decisions_summary}

Total booked meetings: {history.get('booked_meeting_count', 0)}

Based on this history, decide:
- SIMPLE_MEETING: The student's issues are consistently routine and low-stakes. The TA can handle them independently. No professor involvement is needed. Typical signals: mostly P3/P4 requests, no professor escalations, tickets resolved at TA level, no significant decision cards.
- FULL_MEETING: The student's history shows repeated escalations, unresolved high-priority issues, professor involvement, or patterns that need professor awareness. Typical signals: P1/P2 requests, tickets shared with professor, decisions escalated to professor, repeated unresolved issues.

Return ONLY valid JSON (no markdown):
{{"recommendation": "SIMPLE_MEETING" or "FULL_MEETING", "reasoning": "one clear sentence explaining why"}}
"""
    try:
        model = genai.GenerativeModel("gemini-2.0-flash")
        response = model.generate_content(prompt)
        text = response.text.strip()
        if text.startswith("```"):
            text = text.split("```")[1]
            if text.startswith("json"):
                text = text[4:]
        result = json.loads(text.strip())
        rec = result.get("recommendation", "FULL_MEETING")
        if rec not in ("SIMPLE_MEETING", "FULL_MEETING"):
            rec = "FULL_MEETING"
        return {
            "recommendation": rec,
            "reasoning": str(result.get("reasoning", "")).strip(),
        }
    except Exception as e:
        print(f"[meeting_type_agent] Gemini error: {e}", flush=True)
        # Safe default: always involve professor if AI fails
        return {"recommendation": "FULL_MEETING", "reasoning": "Could not analyze history — defaulting to full meeting."}
