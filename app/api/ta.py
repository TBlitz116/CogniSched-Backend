from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from datetime import datetime
from pydantic import BaseModel
from app.core.database import get_db
from app.api.deps import require_role
from app.models.user import User, UserRole
from app.models.meeting import MeetingRequest, RequestStatus, BookedMeeting
from app.models.mapping import RoleMapping
from app.models.cognitive import CognitiveScore
from app.services.slot_service import generate_suggestions, generate_soonest_suggestions, generate_prompt_suggestions
from app.models.approval import PendingApproval, ApprovalStatus
from app.services.cognitive_service import recompute_and_save
from app.services.calendar_service import create_meeting_with_meet, extract_meet_link, get_busy_slots
from app.core.redis_client import cache_get, cache_set, cache_delete
from app.models.ticket import ActionTicket
from app.models.decision import DecisionCard
from meeting_type_agent import recommend_meeting_type

router = APIRouter()


class PromptSuggestBody(BaseModel):
    request_id: int
    prompt: str


class BookSlotBody(BaseModel):
    request_id: int
    start_time: datetime
    end_time: datetime
    simple: bool = False  # True = TA + student only, no professor on calendar


class BookSoonestBody(BaseModel):
    request_id: int
    start_time: datetime
    end_time: datetime


class BookedMeetingOut(BaseModel):
    id: int
    start_time: datetime
    end_time: datetime
    google_meet_link: str | None
    cognitive_score_impact: float | None

    class Config:
        from_attributes = True


@router.get("/notifications")
def get_notifications(
    db: Session = Depends(get_db),
    current_user: User = Depends(require_role(UserRole.TA)),
):
    requests = db.query(MeetingRequest).filter(
        MeetingRequest.ta_id == current_user.id,
        MeetingRequest.status == RequestStatus.PENDING,
    ).order_by(MeetingRequest.detected_priority.asc(), MeetingRequest.created_at.asc()).all()

    result = []
    for req in requests:
        student = db.query(User).filter(User.id == req.student_id).first()
        result.append({
            "id": req.id,
            "student": {"id": student.id, "name": student.name, "email": student.email},
            "prompt_text": req.prompt_text,
            "detected_priority": int(req.detected_priority) if req.detected_priority else None,
            "detected_topic": req.detected_topic,
            "preferred_time_range": req.preferred_time_range,
            "created_at": req.created_at,
        })
    return result


@router.get("/suggestions/{request_id}")
def get_suggestions(
    request_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_role(UserRole.TA)),
):
    request = db.query(MeetingRequest).filter(MeetingRequest.id == request_id).first()
    if not request or request.ta_id != current_user.id:
        raise HTTPException(status_code=404, detail="Request not found")
    return generate_suggestions(db, request_id)


@router.post("/suggest-by-prompt")
def suggest_by_prompt(
    body: PromptSuggestBody,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_role(UserRole.TA)),
):
    request = db.query(MeetingRequest).filter(MeetingRequest.id == body.request_id).first()
    if not request or request.ta_id != current_user.id:
        raise HTTPException(status_code=404, detail="Request not found")
    return generate_prompt_suggestions(db, body.request_id, body.prompt)


@router.get("/soonest/{request_id}")
def get_soonest(
    request_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_role(UserRole.TA)),
):
    request = db.query(MeetingRequest).filter(MeetingRequest.id == request_id).first()
    if not request or request.ta_id != current_user.id:
        raise HTTPException(status_code=404, detail="Request not found")
    return generate_soonest_suggestions(db, request_id)


@router.post("/book-soonest")
def book_soonest(
    body: BookSoonestBody,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_role(UserRole.TA)),
):
    """Book a soonest slot — creates a pending approval for the professor."""
    request = db.query(MeetingRequest).filter(MeetingRequest.id == body.request_id).first()
    if not request or request.ta_id != current_user.id:
        raise HTTPException(status_code=404, detail="Request not found")
    if request.status != RequestStatus.PENDING:
        raise HTTPException(status_code=400, detail="Request is no longer pending")

    mapping = db.query(RoleMapping).filter(RoleMapping.ta_id == current_user.id).first()
    if not mapping:
        raise HTTPException(status_code=400, detail="No professor mapping found")

    approval = PendingApproval(
        request_id=request.id,
        ta_id=current_user.id,
        professor_id=mapping.professor_id,
        student_id=request.student_id,
        start_time=body.start_time,
        end_time=body.end_time,
        reason=request.prompt_text,
    )
    db.add(approval)
    request.status = RequestStatus.AWAITING_APPROVAL
    db.commit()
    db.refresh(approval)

    return {"id": approval.id, "status": "PENDING", "message": "Sent to professor for approval"}


@router.get("/rejected-bookings")
def get_rejected_bookings(
    db: Session = Depends(get_db),
    current_user: User = Depends(require_role(UserRole.TA)),
):
    """Get soonest bookings that were rejected by the professor."""
    rejected = db.query(PendingApproval).filter(
        PendingApproval.ta_id == current_user.id,
        PendingApproval.status == ApprovalStatus.REJECTED,
    ).order_by(PendingApproval.resolved_at.desc()).all()

    result = []
    for r in rejected:
        student = db.query(User).filter(User.id == r.student_id).first()
        result.append({
            "id": r.id,
            "request_id": r.request_id,
            "student": {"id": student.id, "name": student.name, "email": student.email},
            "start_time": r.start_time,
            "end_time": r.end_time,
            "reason": r.reason,
            "resolved_at": r.resolved_at,
        })
    return result


@router.post("/book", response_model=BookedMeetingOut)
def book_slot(
    body: BookSlotBody,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_role(UserRole.TA)),
):
    request = db.query(MeetingRequest).filter(MeetingRequest.id == body.request_id).first()
    if not request or request.ta_id != current_user.id:
        raise HTTPException(status_code=404, detail="Request not found")
    if request.status != RequestStatus.PENDING:
        raise HTTPException(status_code=400, detail="Request is no longer pending")

    mapping = db.query(RoleMapping).filter(RoleMapping.ta_id == current_user.id).first()
    professor_id = mapping.professor_id if mapping else current_user.id
    professor = db.query(User).filter(User.id == professor_id).first()
    student = db.query(User).filter(User.id == request.student_id).first()

    # Create Google Calendar event with Meet link
    google_event_id = None
    meet_link = None
    topic_label = str(request.detected_topic).replace("_", " ").title() if request.detected_topic else "Meeting"
    summary = f"[P{int(request.detected_priority)}] {topic_label} — {student.name}"

    # For simple meetings (TA + student only), professor is not invited
    invite_professor_email = (professor.email if professor else current_user.email) if not body.simple else None

    # Create on TA's calendar (primary organizer)
    if current_user.google_refresh_token:
        try:
            event = create_meeting_with_meet(
                organizer_refresh_token=current_user.google_refresh_token,
                student_email=student.email,
                ta_email=current_user.email,
                professor_email=invite_professor_email,
                start_time=body.start_time,
                end_time=body.end_time,
                summary=summary,
            )
            google_event_id = event.get("id")
            meet_link = extract_meet_link(event)
        except Exception:
            pass  # Google Calendar is best-effort; booking still proceeds

    # Mirror on professor's calendar only for full meetings
    if not body.simple and professor and professor.google_refresh_token:
        try:
            create_meeting_with_meet(
                organizer_refresh_token=professor.google_refresh_token,
                student_email=student.email,
                ta_email=current_user.email,
                professor_email=professor.email,
                start_time=body.start_time,
                end_time=body.end_time,
                summary=summary,
            )
        except Exception:
            pass

    # Capture score before adding the meeting
    score_before = db.query(CognitiveScore).filter(
        CognitiveScore.ta_id == current_user.id,
        CognitiveScore.date == body.start_time.date(),
    ).first()
    score_before_val = score_before.score if score_before else 0.0

    booked = BookedMeeting(
        request_id=request.id,
        student_id=request.student_id,
        ta_id=current_user.id,
        professor_id=professor_id,
        start_time=body.start_time,
        end_time=body.end_time,
        google_event_id=google_event_id,
        google_meet_link=meet_link,
    )
    db.add(booked)
    request.status = RequestStatus.SCHEDULED
    db.commit()
    db.refresh(booked)

    updated_score = recompute_and_save(db, current_user.id, body.start_time.date())
    booked.cognitive_score_impact = round(updated_score.score - score_before_val, 2)
    db.commit()
    db.refresh(booked)

    # Invalidate slot caches for this request — they're now stale
    cache_delete(f"slot:rec:{body.request_id}", f"slot:soonest:{body.request_id}")

    return booked


@router.get("/calendar")
def get_ta_calendar(
    db: Session = Depends(get_db),
    current_user: User = Depends(require_role(UserRole.TA)),
):
    from app.models.calendar import CalendarBlock

    meetings = db.query(BookedMeeting).filter(
        BookedMeeting.ta_id == current_user.id
    ).order_by(BookedMeeting.start_time.asc()).all()

    # Professor's blocks visible to TA (read-only)
    mapping = db.query(RoleMapping).filter(RoleMapping.ta_id == current_user.id).first()
    professor_blocks = []
    if mapping:
        professor_blocks = db.query(CalendarBlock).filter(
            CalendarBlock.professor_id == mapping.professor_id
        ).order_by(CalendarBlock.start_time.asc()).all()

    result_meetings = []
    for m in meetings:
        student = db.query(User).filter(User.id == m.student_id).first()
        result_meetings.append({
            "id": m.id,
            "type": "student_meeting",
            "start_time": m.start_time,
            "end_time": m.end_time,
            "google_meet_link": m.google_meet_link,
            "student": {"id": student.id, "name": student.name, "email": student.email},
            "cognitive_score_impact": m.cognitive_score_impact,
        })

    result_blocks = [
        {
            "id": b.id,
            "type": "professor_block",
            "title": b.title,
            "start_time": b.start_time,
            "end_time": b.end_time,
        }
        for b in professor_blocks
    ]

    # Fetch professor's Google Calendar busy slots (cached 10 min)
    professor_busy = []
    if mapping:
        professor = db.query(User).filter(User.id == mapping.professor_id).first()
        if professor and professor.google_refresh_token:
            gcal_key = f"gcal:busy:{mapping.professor_id}"
            professor_busy = cache_get(gcal_key) or []
            if not professor_busy:
                try:
                    professor_busy = get_busy_slots(professor.google_refresh_token)
                    cache_set(gcal_key, professor_busy, 10 * 60)
                except Exception:
                    pass  # Best-effort — don't break TA calendar if professor token is stale

    return {"meetings": result_meetings, "professor_blocks": result_blocks, "professor_busy": professor_busy}


@router.get("/student-history/{student_id}")
def get_student_history(
    student_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_role(UserRole.TA)),
):
    """Return a student's meeting history + Gemini-powered meeting type recommendation.

    Result is cached for 10 minutes (keyed by ta_id + student_id) to avoid
    redundant Gemini calls when the TA flips between requests.
    """
    mapping = db.query(RoleMapping).filter(
        RoleMapping.ta_id == current_user.id,
        RoleMapping.student_id == student_id,
    ).first()
    if not mapping:
        raise HTTPException(status_code=403, detail="Student not assigned to you")

    student = db.query(User).filter(User.id == student_id).first()
    if not student:
        raise HTTPException(status_code=404, detail="Student not found")

    cache_key = f"student_history:{current_user.id}:{student_id}"
    cached = cache_get(cache_key)
    if cached:
        return cached

    # Gather history
    past_requests = db.query(MeetingRequest).filter(
        MeetingRequest.student_id == student_id,
        MeetingRequest.ta_id == current_user.id,
    ).order_by(MeetingRequest.created_at.desc()).limit(20).all()

    past_tickets = db.query(ActionTicket).filter(
        ActionTicket.student_id == student_id,
        ActionTicket.ta_id == current_user.id,
    ).order_by(ActionTicket.created_at.desc()).limit(20).all()

    past_decisions = db.query(DecisionCard).filter(
        DecisionCard.student_id == student_id,
        DecisionCard.ta_id == current_user.id,
    ).order_by(DecisionCard.created_at.desc()).limit(10).all()

    booked_count = db.query(BookedMeeting).filter(
        BookedMeeting.student_id == student_id,
        BookedMeeting.ta_id == current_user.id,
    ).count()

    history_payload = {
        "student_name": student.name,
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
            {
                "title": t.title,
                "shared_with_professor": t.shared_with_professor,
                "status": str(t.status),
            }
            for t in past_tickets
        ],
        "past_decisions": [
            {
                "question": d.question_summary,
                "outcome": d.outcome.value if d.outcome else None,
            }
            for d in past_decisions
        ],
        "booked_meeting_count": booked_count,
    }

    ai_result = recommend_meeting_type(history_payload)

    result = {
        "student": {"id": student.id, "name": student.name},
        "booked_meeting_count": booked_count,
        "past_requests": history_payload["past_requests"],
        "past_tickets": history_payload["past_tickets"],
        "past_decisions": history_payload["past_decisions"],
        "recommendation": ai_result["recommendation"],
        "reasoning": ai_result["reasoning"],
    }

    cache_set(cache_key, result, 10 * 60)
    return result


@router.post("/decline/{request_id}")
def decline_request(
    request_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_role(UserRole.TA)),
):
    request = db.query(MeetingRequest).filter(MeetingRequest.id == request_id).first()
    if not request or request.ta_id != current_user.id:
        raise HTTPException(status_code=404, detail="Request not found")
    if request.status != RequestStatus.PENDING:
        raise HTTPException(status_code=400, detail="Request is no longer pending")
    request.status = RequestStatus.DECLINED
    db.commit()
    return {"status": "declined"}
