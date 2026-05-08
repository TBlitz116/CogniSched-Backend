"""
Parses a meeting transcript and extracts actionable items using Claude Haiku 4.5.
Each item is classified by scope: "ta" (TA can handle directly) or "professor" (needs escalation).
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


def extract_action_items(transcript: str, student_name: str) -> list[dict]:
    """
    Returns a list of {"title": str, "description": str, "scope": "ta" | "professor"}.

    scope="ta"        — TA can resolve independently
    scope="professor" — requires professor involvement
    """
    system_prompt = """You are an academic meeting assistant reviewing an office-hours transcript.

Extract every actionable item. For each item, decide who should handle it:

- scope "ta": the TA can resolve this independently
  Examples: grade ENTRY errors (wrong score recorded, missing submission in gradebook),
  sending study resources, clarifying assignment instructions, late submission within TA's own policy

- scope "professor": requires the professor's authority
  Examples: regrade requests (disputing rubric or answer key), extension requests,
  extra credit eligibility exceptions, policy exceptions, accommodation decisions, anything needing professor sign-off

Return ONLY a valid JSON array (no markdown, no extra text):
[{"title": "short action title", "description": "what specifically needs to happen and why", "scope": "ta" or "professor"}]

If there are no actionable items, return: []"""

    user_msg = f"""Student name: {student_name}

Transcript:
{transcript}"""

    try:
        response = _client.messages.create(
            model=MODEL,
            max_tokens=4096,
            system=system_prompt,
            messages=[{"role": "user", "content": user_msg}],
        )
        text = _strip_fences("".join(b.text for b in response.content if b.type == "text"))
        items = json.loads(text)
        result = []
        for i in items:
            if not i.get("title"):
                continue
            scope = i.get("scope", "professor")
            if scope not in ("ta", "professor"):
                scope = "professor"
            result.append({
                "title": str(i["title"]).strip(),
                "description": str(i.get("description", "")).strip(),
                "scope": scope,
            })
        return result
    except Exception as e:
        print(f"[transcript_agent] Claude error ({type(e).__name__}): {e}", flush=True)
        return []
