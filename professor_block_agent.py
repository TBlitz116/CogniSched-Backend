"""
Parses professor natural language into calendar blocks using Claude Haiku 4.5.
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


def parse_blocks(prompt: str, current_date: str, timezone: str = "UTC") -> list[dict]:
    """
    Returns list of {"title": str, "start": ISO8601, "end": ISO8601}.
    Empty list on parse failure or upstream error (the caller surfaces a generic
    "could not parse" message to the user; the actual error is logged here).
    """
    system_prompt = f"""Today is {current_date}. The user's timezone is {timezone}.
Extract calendar blocks from the user's request. Return ONLY a valid JSON array (no markdown, no extra text):
[{{"title": "...", "start": "ISO8601 datetime", "end": "ISO8601 datetime"}}]
If multiple blocks are mentioned, return all of them.
If no blocks can be extracted, return: []"""

    try:
        response = _client.messages.create(
            model=MODEL,
            max_tokens=1024,
            system=system_prompt,
            messages=[{"role": "user", "content": prompt}],
        )
        text = _strip_fences("".join(b.text for b in response.content if b.type == "text"))
        result = json.loads(text)
        return result if isinstance(result, list) else []
    except Exception as e:
        # Loud log so quota / auth issues are diagnosable from container logs.
        print(f"[professor_block_agent] Claude error ({type(e).__name__}): {e}", flush=True)
        return []
