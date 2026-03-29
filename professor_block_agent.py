"""
Parses professor natural language into calendar blocks using Gemini.
"""
import json
import os
import google.generativeai as genai
from datetime import datetime
from dotenv import load_dotenv

load_dotenv()
genai.configure(api_key=os.getenv("GEMINI_API_KEY"))


def parse_blocks(prompt: str, current_date: str, timezone: str = "UTC") -> list[dict]:
    """
    Returns list of {"title": str, "start": ISO8601, "end": ISO8601}
    """
    system_prompt = f"""Today is {current_date}. The user's timezone is {timezone}.
Extract calendar blocks from this request. Return ONLY a valid JSON array:
[{{"title": "...", "start": "ISO8601 datetime", "end": "ISO8601 datetime"}}]
If multiple blocks are mentioned, return all of them.
Request: "{prompt}"
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
        print(f"[professor_block_agent] Gemini error: {e}", flush=True)
        return []
