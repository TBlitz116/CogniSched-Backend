"""
Priority classifier for student meeting requests.
Uses Gemini with keyword fallback.
"""
import json
import os
import google.generativeai as genai
from dotenv import load_dotenv

load_dotenv()
genai.configure(api_key=os.getenv("GEMINI_API_KEY"))

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
Classify the student's request and return ONLY valid JSON with these fields:
- priority: integer 1-4 (1=RECOMMENDATION letter request, 2=EXAM related question, 3=EXAM reflection/grade review, 4=GENERAL)
- topic: one of RECOMMENDATION, EXAM_QUESTION, EXAM_REFLECTION, GENERAL
- extracted_time_hint: any time preference mentioned (e.g. "next week", "Thursday") or null
- summary: one sentence summary of the request

Student request: """ + prompt_text

    try:
        model = genai.GenerativeModel("gemini-2.0-flash")
        response = model.generate_content(system_prompt)
        text = response.text.strip()
        if text.startswith("```"):
            text = text.split("```")[1]
            if text.startswith("json"):
                text = text[4:]
        return json.loads(text.strip())
    except Exception:
        base = _keyword_classify(prompt_text)
        return {
            **base,
            "extracted_time_hint": None,
            "summary": prompt_text[:100],
        }
