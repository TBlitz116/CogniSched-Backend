"""TA Workflow: Claude-style chat that drives a right-panel canvas.

Powered by Claude Haiku 4.5 via the Anthropic SDK with native tool use.
Read-only tools execute server-side in a short loop (up to 3 rounds).
Mutating actions (book/decline) are returned as `proposed_action` so the TA
confirms via the existing /ta/book or /ta/decline endpoints — the LLM never
books on its own.

Prompt caching: tools render before system, so a `cache_control` breakpoint
on the last system block caches BOTH tools and system together. The system
prompt is frozen (no timestamps / per-request IDs) and tool order is fixed.
"""
from __future__ import annotations

import json
import os
from typing import Any

import anthropic
from dotenv import load_dotenv
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.api.deps import require_role
from app.core.database import get_db
from app.models.decision import DecisionCard
from app.models.mapping import RoleMapping
from app.models.meeting import BookedMeeting, MeetingRequest, RequestStatus
from app.models.ticket import ActionTicket
from app.models.user import User, UserRole
from app.services.slot_service import generate_suggestions
from meeting_type_agent import recommend_meeting_type

load_dotenv()

# Single client instance — the SDK handles connection pooling and retries.
_client = anthropic.Anthropic(api_key=os.getenv("ANTHROPIC_API_KEY"))
MODEL = "claude-haiku-4-5"

router = APIRouter()


# ── Request / response shapes ─────────────────────────────────────────────────


class ChatMessage(BaseModel):
    role: str  # "user" | "assistant"
    content: str


class ChatBody(BaseModel):
    messages: list[ChatMessage]


# ── Tool implementations (server-side, scoped to current TA) ──────────────────


def _list_pending_requests(db: Session, ta_id: int) -> list[dict]:
    rows = (
        db.query(MeetingRequest)
        .filter(MeetingRequest.ta_id == ta_id, MeetingRequest.status == RequestStatus.PENDING)
        .order_by(MeetingRequest.detected_priority.asc(), MeetingRequest.created_at.asc())
        .all()
    )
    out = []
    for r in rows:
        student = db.query(User).filter(User.id == r.student_id).first()
        out.append({
            "request_id": r.id,
            "student_id": r.student_id,
            "student_name": student.name if student else "Unknown",
            "student_email": student.email if student else None,
            "priority": int(r.detected_priority) if r.detected_priority else None,
            "topic": str(r.detected_topic) if r.detected_topic else None,
            "prompt_text": r.prompt_text,
            "preferred_time_range": r.preferred_time_range,
            "created_at": r.created_at.isoformat(),
        })
    return out


def _get_student_history(db: Session, ta_id: int, student_id: int) -> dict:
    mapping = (
        db.query(RoleMapping)
        .filter(RoleMapping.ta_id == ta_id, RoleMapping.student_id == student_id)
        .first()
    )
    if not mapping:
        return {"error": "Student not assigned to you"}

    student = db.query(User).filter(User.id == student_id).first()
    if not student:
        return {"error": "Student not found"}

    past_requests = (
        db.query(MeetingRequest)
        .filter(MeetingRequest.student_id == student_id, MeetingRequest.ta_id == ta_id)
        .order_by(MeetingRequest.created_at.desc())
        .limit(20)
        .all()
    )
    past_tickets = (
        db.query(ActionTicket)
        .filter(ActionTicket.student_id == student_id, ActionTicket.ta_id == ta_id)
        .order_by(ActionTicket.created_at.desc())
        .limit(20)
        .all()
    )
    past_decisions = (
        db.query(DecisionCard)
        .filter(DecisionCard.student_id == student_id, DecisionCard.ta_id == ta_id)
        .order_by(DecisionCard.created_at.desc())
        .limit(10)
        .all()
    )
    booked_count = (
        db.query(BookedMeeting)
        .filter(BookedMeeting.student_id == student_id, BookedMeeting.ta_id == ta_id)
        .count()
    )

    payload = {
        "student": {"id": student.id, "name": student.name},
        "booked_meeting_count": booked_count,
        "past_requests": [
            {
                "priority": int(r.detected_priority) if r.detected_priority else 4,
                "topic": str(r.detected_topic) if r.detected_topic else "GENERAL",
                "status": str(r.status),
                "created_at": r.created_at.strftime("%Y-%m-%d"),
            }
            for r in past_requests
        ],
        "past_tickets": [
            {"title": t.title, "shared_with_professor": t.shared_with_professor, "status": str(t.status)}
            for t in past_tickets
        ],
        "past_decisions": [
            {"question": d.question_summary, "outcome": d.outcome.value if d.outcome else None}
            for d in past_decisions
        ],
    }

    try:
        ai = recommend_meeting_type(payload)
        payload["recommendation"] = ai.get("recommendation")
        payload["reasoning"] = ai.get("reasoning")
    except Exception:
        payload["recommendation"] = None
        payload["reasoning"] = None

    return payload


def _recommend_slots(db: Session, ta_id: int, request_id: int) -> dict:
    req = db.query(MeetingRequest).filter(MeetingRequest.id == request_id).first()
    if not req or req.ta_id != ta_id:
        return {"error": "Request not found"}
    student = db.query(User).filter(User.id == req.student_id).first()
    suggestions = generate_suggestions(db, request_id)
    return {
        "request_id": request_id,
        "student": {"id": student.id, "name": student.name} if student else None,
        "prompt_text": req.prompt_text,
        "priority": int(req.detected_priority) if req.detected_priority else None,
        "suggestions": suggestions,
    }


# ── Tool definitions (frozen — must be deterministic for prompt caching) ──────

TOOLS: list[dict] = [
    {
        "name": "list_pending_requests",
        "description": "List all pending meeting requests assigned to the current TA, sorted by priority then age. Returns request_id, student, priority, topic, and prompt text for each. Use this when the TA asks about the queue, what's outstanding, or needs to find a specific student's request.",
        "input_schema": {"type": "object", "properties": {}, "required": []},
    },
    {
        "name": "get_student_history",
        "description": "Fetch a student's past requests, tickets, decisions, and an AI-generated meeting-type recommendation. Use when the TA wants context on a student before booking. Requires the student_id (call list_pending_requests first if you only have a name).",
        "input_schema": {
            "type": "object",
            "properties": {
                "student_id": {"type": "integer", "description": "Numeric student user ID"},
            },
            "required": ["student_id"],
        },
    },
    {
        "name": "recommend_slots",
        "description": "Generate ranked optimal meeting slots for a specific pending request, factoring in TA cognitive load, professor availability, urgency, and burnout risk. Returns a list of suggestions with scores and explanations. Requires the request_id.",
        "input_schema": {
            "type": "object",
            "properties": {
                "request_id": {"type": "integer", "description": "Pending meeting request ID"},
            },
            "required": ["request_id"],
        },
    },
]


SYSTEM_PROMPT = """You are a TA scheduling co-pilot in a workflow workspace.
The TA chats with you on the LEFT panel; your job is to populate the RIGHT panel ("canvas") with the right artifact for the conversation.

You have three read-only tools: `list_pending_requests`, `get_student_history`, `recommend_slots`. Use them whenever you need data — do not guess IDs or invent student details. If the TA names a student, call `list_pending_requests` first to find the matching student_id and request_id.

When you have what you need, finish your turn with a short chat reply AND a final JSON object on its own line wrapped in <canvas>...</canvas> tags. The JSON must match this schema exactly:

{
  "canvas": null | {
    "type": "requests" | "history" | "slots",
    "data": <the FULL tool result you just received>
  },
  "proposed_action": null | {
    "kind": "book" | "decline",
    "request_id": int,
    "student_name": string,
    "start_time": "ISO 8601 string (book only)",
    "end_time": "ISO 8601 string (book only)",
    "simple": false,
    "summary": "one-line rationale"
  }
}

Rules:
- For canvas type "slots", set data to the FULL recommend_slots result (request_id, student, prompt_text, priority, suggestions[]).
- For canvas type "history", set data to the FULL get_student_history result.
- For canvas type "requests", set data to {"items": <the list returned by list_pending_requests>}.
- For booking or declining: NEVER claim it's done. Populate proposed_action with a concrete slot from the most recent recommend_slots result, set canvas to null (or keep the slots canvas), and tell the TA to confirm on the right panel.
- If the TA's message doesn't need any tool, reply briefly with canvas=null and proposed_action=null inside <canvas>...</canvas>.
- Be concise. The canvas does the heavy lifting; chat is the conversation."""


# ── Helpers ───────────────────────────────────────────────────────────────────


def _execute_tool(db: Session, ta_id: int, name: str, args: dict) -> Any:
    if name == "list_pending_requests":
        return _list_pending_requests(db, ta_id)
    if name == "get_student_history":
        sid = args.get("student_id")
        if not isinstance(sid, int):
            return {"error": "student_id (int) required"}
        return _get_student_history(db, ta_id, sid)
    if name == "recommend_slots":
        rid = args.get("request_id")
        if not isinstance(rid, int):
            return {"error": "request_id (int) required"}
        return _recommend_slots(db, ta_id, rid)
    return {"error": f"unknown tool {name}"}


def _parse_final(text: str) -> tuple[str, dict | None, dict | None]:
    """Split assistant text into (chat_text, canvas, proposed_action).

    The model emits a trailing <canvas>{...}</canvas> block. Anything before it
    is shown to the TA; the JSON drives the right panel.
    """
    open_tag, close_tag = "<canvas>", "</canvas>"
    i = text.rfind(open_tag)
    if i == -1:
        return text.strip(), None, None
    j = text.find(close_tag, i)
    if j == -1:
        return text[:i].strip(), None, None

    chat = text[:i].strip()
    payload_str = text[i + len(open_tag) : j].strip()
    try:
        payload = json.loads(payload_str)
    except json.JSONDecodeError:
        return chat, None, None
    return chat, payload.get("canvas"), payload.get("proposed_action")


# ── Endpoint ──────────────────────────────────────────────────────────────────


@router.post("/chat")
def workflow_chat(
    body: ChatBody,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_role(UserRole.TA)),
):
    if not body.messages:
        raise HTTPException(status_code=400, detail="messages required")
    if not os.getenv("ANTHROPIC_API_KEY"):
        raise HTTPException(status_code=503, detail="ANTHROPIC_API_KEY not configured")

    # Build the conversation. The frontend sends plain {role, content} pairs —
    # convert to the shape Anthropic expects.
    messages: list[dict] = [{"role": m.role, "content": m.content} for m in body.messages]

    # System prompt with prompt caching. Tools render before system in the
    # rendered prefix, so a breakpoint on the last system block caches BOTH
    # tools and system together. ~600 tokens of system + tool definitions —
    # comfortably above Haiku 4.5's 4096-token cache minimum once it's in
    # context with the (small) conversation prefix.
    system = [
        {
            "type": "text",
            "text": SYSTEM_PROMPT,
            "cache_control": {"type": "ephemeral"},
        },
    ]

    # Tool-execution loop. 3 rounds is plenty: typical flow is 1 tool call
    # then finalize, occasionally 2 (lookup student → recommend slots).
    for _ in range(3):
        try:
            response = _client.messages.create(
                model=MODEL,
                max_tokens=2048,
                system=system,
                tools=TOOLS,
                messages=messages,
            )
        except anthropic.APIStatusError as e:
            raise HTTPException(status_code=502, detail=f"Claude API error: {e.message}")

        if response.stop_reason == "tool_use":
            # Append the assistant turn (preserving tool_use blocks) and
            # collect tool_result blocks for every tool_use.
            messages.append({"role": "assistant", "content": response.content})
            tool_results: list[dict] = []
            for block in response.content:
                if block.type == "tool_use":
                    result = _execute_tool(db, current_user.id, block.name, block.input or {})
                    tool_results.append({
                        "type": "tool_result",
                        "tool_use_id": block.id,
                        "content": json.dumps(result, default=str),
                    })
            messages.append({"role": "user", "content": tool_results})
            continue

        # Terminal — extract the assistant text and parse the canvas block.
        text = "".join(b.text for b in response.content if b.type == "text")
        chat, canvas, proposed_action = _parse_final(text)
        return {
            "assistant_message": chat or "(no reply)",
            "canvas": canvas,
            "proposed_action": proposed_action,
        }

    return {
        "assistant_message": "I needed too many lookups to answer that — can you narrow it down?",
        "canvas": None,
        "proposed_action": None,
    }
