"""
Drafts an async "decision card" from a student's meeting request. The idea:
most professor meetings are really just yes/no decisions in disguise
(extensions, topic changes, policy exceptions). Rather than put them on the
calendar, we convert them into a one-screen card the professor can resolve
in seconds — reducing the professor's synchronous cognitive load.
"""
import json
import os
import google.generativeai as genai
from dotenv import load_dotenv

load_dotenv()
genai.configure(api_key=os.getenv("GEMINI_API_KEY"))


def _fallback_draft(prompt_text: str, student_name: str) -> dict:
    summary = prompt_text.strip().split("\n")[0][:140] or f"Request from {student_name}"
    return {
        "question_summary": summary,
        "context": prompt_text.strip(),
        "ta_recommendation": "TA review pending — please read the full request and choose an outcome.",
        "options": ["Approve", "Deny", "Escalate to meeting", "Request more info"],
    }


def draft_decision_card(
    prompt_text: str,
    student_name: str,
    ta_note: str | None = None,
) -> dict:
    """
    Returns a dict shaped like:
    {
      "question_summary": "One-sentence distilled ask",
      "context": "Short paragraph — why they're asking, what's been tried",
      "ta_recommendation": "TA's suggested answer + rationale (1-2 sentences)",
      "options": ["Approve 2-day extension", "Deny", "Escalate to meeting", "Request more info"]
    }
    `options` is tailored to the specific request so the professor sees concrete
    choices, not generic buttons.
    """
    ta_section = f"\nTA's note to professor: {ta_note}" if ta_note else ""

    system_prompt = f"""You are an assistant helping a university professor clear low-stakes
decisions from their inbox WITHOUT a meeting. A TA is converting a student's
meeting request into a decision card.

Student: {student_name}
Student request: {prompt_text}{ta_section}

Produce a compact decision card. Rules:
- question_summary: ONE sentence, no more than 20 words, stating the core ask.
- context: 2-3 sentences of background the professor needs — course policy, what TA already checked, any relevant deadlines. Do not repeat the summary.
- ta_recommendation: 1-2 sentences stating what the TA thinks the professor should do AND the key reason. Be direct ("Recommend approving because..."), not hedging.
- options: 2 to 4 concrete, action-oriented button labels tailored to THIS request. Prefer specific labels like "Approve 2-day extension" over generic "Approve". ALWAYS include at least one "Deny" variant AND one "Escalate to meeting" option.

Return ONLY valid JSON (no markdown fences, no extra text):
{{"question_summary": "...", "context": "...", "ta_recommendation": "...", "options": ["...", "...", "...", "..."]}}
"""
    try:
        model = genai.GenerativeModel("gemini-2.0-flash")
        response = model.generate_content(system_prompt)
        text = response.text.strip()
        if text.startswith("```"):
            text = text.split("```")[1]
            if text.startswith("json"):
                text = text[4:]
        data = json.loads(text.strip())

        # Shape validation — fall back if anything's missing
        if not data.get("question_summary") or not isinstance(data.get("options"), list):
            return _fallback_draft(prompt_text, student_name)
        options = [str(o).strip() for o in data["options"] if str(o).strip()]
        if not options:
            options = ["Approve", "Deny", "Escalate to meeting", "Request more info"]
        return {
            "question_summary": str(data["question_summary"]).strip(),
            "context": str(data.get("context", "")).strip(),
            "ta_recommendation": str(data.get("ta_recommendation", "")).strip(),
            "options": options[:4],
        }
    except Exception as e:
        print(f"[decision_agent] Gemini error: {e}", flush=True)
        return _fallback_draft(prompt_text, student_name)
