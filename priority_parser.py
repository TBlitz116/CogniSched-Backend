"""
Priority classifier for student meeting requests.
Uses Claude Haiku 4.5 with keyword fallback.
"""
import json
import os

import anthropic
from dotenv import load_dotenv

load_dotenv()
_client = anthropic.Anthropic(api_key=os.getenv("ANTHROPIC_API_KEY"))
MODEL = "claude-haiku-4-5"

KEYWORD_MAP = {
    1: ["recommend", "letter", "reference", "lor"],
    2: ["exam", "midterm", "final", "quiz", "test", "question", "confused"],
    3: ["grade", "reflection", "feedback", "review my exam", "went over"],
}


def _keyword_classify(text: str) -> dict:
    lower = text.lower()
    for priority, keywords in KEYWORD_MAP.items():
        if any(kw in lower for kw in keywords):
            topics = {1: "RECOMMENDATION", 2: "EXAM_QUESTION", 3: "EXAM_REFLECTION"}
            return {"priority": priority, "topic": topics[priority]}
    return {"priority": 4, "topic": "GENERAL"}


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


def classify_request(prompt_text: str) -> dict:
    """
    Returns:
        {
            "priority": 1-4,
            "topic": "RECOMMENDATION" | "EXAM_QUESTION" | "EXAM_REFLECTION" | "GENERAL",
            "extracted_time_hint": str | None,
            "summary": str
        }
    """
    system_prompt = """You are a meeting request classifier for a university scheduling system.
Classify the student's request and return ONLY valid JSON (no markdown, no extra text) with these fields:
- priority: integer 1-4 (1=RECOMMENDATION letter request, 2=EXAM related question, 3=EXAM reflection/grade review, 4=GENERAL)
- topic: one of RECOMMENDATION, EXAM_QUESTION, EXAM_REFLECTION, GENERAL
- extracted_time_hint: any time preference mentioned (e.g. "next week", "Thursday") or null
- summary: one sentence summary of the request"""

    try:
        response = _client.messages.create(
            model=MODEL,
            max_tokens=512,
            system=system_prompt,
            messages=[{"role": "user", "content": f"Student request: {prompt_text}"}],
        )
        text = _strip_fences("".join(b.text for b in response.content if b.type == "text"))
        return json.loads(text)
    except Exception as e:
        print(f"[priority_parser] Claude error: {e}", flush=True)
        base = _keyword_classify(prompt_text)
        return {**base, "extracted_time_hint": None, "summary": prompt_text[:100]}
