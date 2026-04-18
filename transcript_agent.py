"""
Parses a meeting transcript and extracts actionable items using Gemini.
Each item is classified by scope: "ta" (TA can handle directly) or "professor" (needs escalation).
"""
import json
import os
import google.generativeai as genai
from dotenv import load_dotenv

load_dotenv()
genai.configure(api_key=os.getenv("GEMINI_API_KEY"))


def extract_action_items(transcript: str, student_name: str) -> list[dict]:
    """
    Returns a list of {"title": str, "description": str, "scope": "ta" | "professor"}.

    scope="ta"        — TA can resolve independently (grade entry errors, late submission
                        within TA's authority, sending resources, assignment clarification)
    scope="professor" — requires professor involvement (regrade requests, extension requests,
                        policy exceptions, extra credit eligibility, accommodation decisions)
    """
    system_prompt = f"""You are an academic meeting assistant reviewing an office-hours transcript.
Student name: {student_name}

Extract every actionable item from this transcript. For each item, decide who should handle it:

- scope "ta": the TA can resolve this independently
  Examples: grade ENTRY errors (wrong score recorded, missing submission in gradebook),
  sending study resources, clarifying assignment instructions, late submission within TA's own policy

- scope "professor": requires the professor's authority
  Examples: regrade requests (disputing rubric or answer key), extension requests,
  extra credit eligibility exceptions, policy exceptions, accommodation decisions, anything needing professor sign-off

Return ONLY a valid JSON array (no markdown, no extra text):
[{{"title": "short action title", "description": "what specifically needs to happen and why", "scope": "ta" or "professor"}}]

If there are no actionable items, return: []

Transcript:
{transcript}
"""
    try:
        model = genai.GenerativeModel("gemini-2.0-flash")
        response = model.generate_content(system_prompt)
        text = response.text.strip()
        if text.startswith("```"):
            text = text.split("```")[1]
            if text.startswith("json"):
                text = text[4:]
        items = json.loads(text.strip())
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
        print(f"[transcript_agent] Gemini error: {e}", flush=True)
        return []
