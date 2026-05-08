"""
Analyzes a student's meeting history and recommends the appropriate meeting type:
  - SIMPLE_MEETING  : TA + student only, no professor needed
  - FULL_MEETING    : professor should be involved

Uses Claude Haiku 4.5.
"""
import json
import os

import anthropic
from dotenv import load_dotenv

load_dotenv()
_client = anthropic.Anthropic(api_key=os.getenv("ANTHROPIC_API_KEY"))
MODEL = "claude-haiku-4-5"


def _strip_fences(text: str) -> str:
    text = text.strip()
    if text.startswith("```"):
        parts = text.split("```")
        if len(parts) >= 2:
            inner = parts[1]
            if inner.startswith("json"):
                inner = inner[4:]
            text = inner
    return text.strip()


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

    system_prompt = """You are an academic scheduling assistant. Analyze a student's meeting history and recommend the most appropriate type of meeting.

Decide between:
- SIMPLE_MEETING: The student's issues are consistently routine and low-stakes. The TA can handle them independently. No professor involvement is needed. Typical signals: mostly P3/P4 requests, no professor escalations, tickets resolved at TA level, no significant decision cards.
- FULL_MEETING: The student's history shows repeated escalations, unresolved high-priority issues, professor involvement, or patterns that need professor awareness. Typical signals: P1/P2 requests, tickets shared with professor, decisions escalated to professor, repeated unresolved issues.

Return ONLY valid JSON (no markdown, no extra text):
{"recommendation": "SIMPLE_MEETING" or "FULL_MEETING", "reasoning": "one clear sentence explaining why"}"""

    user_msg = f"""Student: "{history.get('student_name', 'Unknown')}"

Past meeting requests:
{requests_summary}

Action tickets raised:
{tickets_summary}

Decision cards sent to professor:
{decisions_summary}

Total booked meetings: {history.get('booked_meeting_count', 0)}"""

    try:
        response = _client.messages.create(
            model=MODEL,
            max_tokens=512,
            system=system_prompt,
            messages=[{"role": "user", "content": user_msg}],
        )
        text = _strip_fences("".join(b.text for b in response.content if b.type == "text"))
        result = json.loads(text)
        rec = result.get("recommendation", "FULL_MEETING")
        if rec not in ("SIMPLE_MEETING", "FULL_MEETING"):
            rec = "FULL_MEETING"
        return {
            "recommendation": rec,
            "reasoning": str(result.get("reasoning", "")).strip(),
        }
    except Exception as e:
        print(f"[meeting_type_agent] Claude error: {e}", flush=True)
        return {"recommendation": "FULL_MEETING", "reasoning": "Could not analyze history — defaulting to full meeting."}
