from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from datetime import datetime
from pydantic import BaseModel
from app.core.database import get_db
from app.api.deps import require_role
from app.models.user import User, UserRole
from app.models.calendar import CalendarBlock
from app.models.mapping import RoleMapping
from app.models.cognitive import CognitiveScore
from app.models.meeting import MeetingRequest, RequestStatus, BookedMeeting, MeetingPriority, MeetingTopic
from app.models.approval import PendingApproval, ApprovalStatus
from app.services.calendar_service import create_busy_block, get_upcoming_events, create_meeting_with_meet, extract_meet_link
from app.services.cognitive_service import recompute_and_save, recompute_professor_score
from app.models.cognitive import ProfessorCognitiveScore
from app.services.email_service import send_professor_meeting_request_email
from professor_block_agent import parse_blocks

router = APIRouter()


class BlockPromptBody(BaseModel):
    prompt: str
    timezone: str = "UTC"


class BlockPreview(BaseModel):
    title: str
    start: str
    end: str


@router.post("/block/preview", response_model=list[BlockPreview])
def preview_blocks(
    body: BlockPromptBody,
    current_user: User = Depends(require_role(UserRole.PROFESSOR)),
):
    today = datetime.utcnow().strftime("%Y-%m-%d")
    blocks = parse_blocks(body.prompt, today, body.timezone)
    if not blocks:
        raise HTTPException(status_code=422, detail="Could not parse any calendar blocks from that prompt")
    # Normalise to naive ISO so the frontend always gets timezone-free strings
    return [
        {
            "title": b["title"],
            "start": datetime.fromisoformat(b["start"].replace("Z", "")).replace(tzinfo=None).isoformat(),
            "end": datetime.fromisoformat(b["end"].replace("Z", "")).replace(tzinfo=None).isoformat(),
        }
        for b in blocks
    ]


@router.post("/block/confirm")
def confirm_blocks(
    body: BlockPromptBody,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_role(UserRole.PROFESSOR)),
):
    today = datetime.utcnow().strftime("%Y-%m-%d")
    blocks = parse_blocks(body.prompt, today, body.timezone)
    if not blocks:
        raise HTTPException(status_code=422, detail="Could not parse any calendar blocks from that prompt")

    created = []
    for b in blocks:
        start_dt = datetime.fromisoformat(b["start"].replace("Z", "")).replace(tzinfo=None)
        end_dt = datetime.fromisoformat(b["end"].replace("Z", "")).replace(tzinfo=None)

        google_event_id = None
        if current_user.google_refresh_token:
            try:
                event = create_busy_block(
                    current_user.google_refresh_token,
                    b["title"],
                    start_dt,
                    end_dt,
                )
                google_event_id = event.get("id")
            except Exception:
                pass  # Calendar sync is best-effort; DB record is always created

        block = CalendarBlock(
            professor_id=current_user.id,
            title=b["title"],
            start_time=start_dt,
            end_time=end_dt,
            source_prompt=body.prompt,
            is_available=False,
            google_event_id=google_event_id,
        )
        db.add(block)
        # Use the normalised naive ISO strings so the optimistic frontend update
        # positions the event correctly (no "Z" that would cause UTC→local shift)
        created.append({
            "title": b["title"],
            "start": start_dt.isoformat(),
            "end": end_dt.isoformat(),
            "google_event_id": google_event_id,
        })

    db.commit()

    # Recompute professor cognitive score for each affected day
    affected_dates = {start_dt.date() for item in created
                      for start_dt in [datetime.fromisoformat(item["start"])]}
    for d in affected_dates:
        try:
            recompute_professor_score(db, current_user.id, d)
        except Exception:
            pass

    return {"created": created}


@router.get("/calendar")
def get_professor_calendar(
    db: Session = Depends(get_db),
    current_user: User = Depends(require_role(UserRole.PROFESSOR)),
):
    blocks = db.query(CalendarBlock).filter(
        CalendarBlock.professor_id == current_user.id
    ).order_by(CalendarBlock.start_time.asc()).all()

    # Booked meetings under this professor
    meetings = db.query(BookedMeeting).filter(
        BookedMeeting.professor_id == current_user.id
    ).order_by(BookedMeeting.start_time.asc()).all()

    booked = []
    for m in meetings:
        student = db.query(User).filter(User.id == m.student_id).first()
        ta = db.query(User).filter(User.id == m.ta_id).first()
        booked.append({
            "id": m.id,
            "type": "meeting",
            "title": f"{student.name} with {ta.name}" if student and ta else "Meeting",
            "start_time": m.start_time,
            "end_time": m.end_time,
            "google_meet_link": m.google_meet_link,
        })

    return {
        "blocks": [
            {
                "id": b.id,
                "title": b.title,
                "start_time": b.start_time,
                "end_time": b.end_time,
                "source_prompt": b.source_prompt,
                "google_event_id": b.google_event_id,
            }
            for b in blocks
        ],
        "meetings": booked,
    }


@router.get("/my-load")
def get_my_load(
    db: Session = Depends(get_db),
    current_user: User = Depends(require_role(UserRole.PROFESSOR)),
):
    """Return the professor's cognitive load for today and the past 7 days."""
    from datetime import date as date_type
    today = datetime.utcnow().date()
    seven_days_ago = today - timedelta(days=6)

    scores = db.query(ProfessorCognitiveScore).filter(
        ProfessorCognitiveScore.professor_id == current_user.id,
        ProfessorCognitiveScore.date >= seven_days_ago,
    ).order_by(ProfessorCognitiveScore.date.asc()).all()

    today_score = next((s for s in scores if s.date == today), None)

    def label(score: float) -> str:
        if score <= 30: return "Light"
        if score <= 60: return "Moderate"
        return "Heavy"

    return {
        "today": {
            "score": today_score.score if today_score else 0.0,
            "block_count": today_score.block_count if today_score else 0,
            "blocked_hours": today_score.blocked_hours if today_score else 0.0,
            "label": label(today_score.score if today_score else 0.0),
        },
        "history": [
            {
                "date": s.date.isoformat(),
                "score": s.score,
                "block_count": s.block_count,
                "blocked_hours": s.blocked_hours,
                "label": label(s.score),
            }
            for s in scores
        ],
    }


@router.get("/google-calendar")
def get_google_calendar(
    current_user: User = Depends(require_role(UserRole.PROFESSOR)),
):
    if not current_user.google_refresh_token:
        raise HTTPException(status_code=400, detail="No Google Calendar access")
    try:
        events = get_upcoming_events(current_user.google_refresh_token)
        return events
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Google Calendar error: {str(e)}")


@router.get("/ta-schedule/{ta_id}")
def get_ta_schedule(
    ta_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_role(UserRole.PROFESSOR)),
):
    from app.models.meeting import BookedMeeting

    mapping = db.query(RoleMapping).filter(
        RoleMapping.ta_id == ta_id,
        RoleMapping.professor_id == current_user.id,
    ).first()
    if not mapping:
        raise HTTPException(status_code=403, detail="This TA is not assigned to you")

    ta = db.query(User).filter(User.id == ta_id).first()
    meetings = db.query(BookedMeeting).filter(
        BookedMeeting.ta_id == ta_id
    ).order_by(BookedMeeting.start_time.asc()).all()

    score = db.query(CognitiveScore).filter(
        CognitiveScore.ta_id == ta_id
    ).order_by(CognitiveScore.date.desc()).first()

    return {
        "ta": {"id": ta.id, "name": ta.name, "email": ta.email},
        "meetings": [
            {
                "id": m.id,
                "start_time": m.start_time,
                "end_time": m.end_time,
                "google_meet_link": m.google_meet_link,
            }
            for m in meetings
        ],
        "latest_burnout_risk": score.burnout_risk if score else "LOW",
        "latest_cognitive_score": score.score if score else 0,
    }


@router.get("/team")
def get_team_overview(
    db: Session = Depends(get_db),
    current_user: User = Depends(require_role(UserRole.PROFESSOR)),
):
    mappings = db.query(RoleMapping).filter(
        RoleMapping.professor_id == current_user.id,
        RoleMapping.student_id == None,
    ).all()

    result = []
    for m in mappings:
        ta = db.query(User).filter(User.id == m.ta_id).first()
        if not ta:
            continue
        score = db.query(CognitiveScore).filter(
            CognitiveScore.ta_id == ta.id
        ).order_by(CognitiveScore.date.desc()).first()
        student_count = db.query(RoleMapping).filter(
            RoleMapping.ta_id == ta.id,
            RoleMapping.student_id != None,
        ).count()
        result.append({
            "id": ta.id,
            "name": ta.name,
            "email": ta.email,
            "burnout_risk": score.burnout_risk if score else "LOW",
            "cognitive_score": score.score if score else 0,
            "student_count": student_count,
        })
    return result


@router.get("/pending-approvals")
def get_pending_approvals(
    db: Session = Depends(get_db),
    current_user: User = Depends(require_role(UserRole.PROFESSOR)),
):
    approvals = db.query(PendingApproval).filter(
        PendingApproval.professor_id == current_user.id,
        PendingApproval.status == ApprovalStatus.PENDING,
    ).order_by(PendingApproval.created_at.desc()).all()

    result = []
    for a in approvals:
        student = db.query(User).filter(User.id == a.student_id).first()
        ta = db.query(User).filter(User.id == a.ta_id).first()
        result.append({
            "id": a.id,
            "student": {"id": student.id, "name": student.name, "email": student.email},
            "ta": {"id": ta.id, "name": ta.name, "email": ta.email},
            "start_time": a.start_time,
            "end_time": a.end_time,
            "reason": a.reason,
            "created_at": a.created_at,
        })
    return result


@router.post("/approve/{approval_id}")
def approve_booking(
    approval_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_role(UserRole.PROFESSOR)),
):
    approval = db.query(PendingApproval).filter(
        PendingApproval.id == approval_id,
        PendingApproval.professor_id == current_user.id,
    ).first()
    if not approval:
        raise HTTPException(status_code=404, detail="Approval not found")
    if approval.status != ApprovalStatus.PENDING:
        raise HTTPException(status_code=400, detail="Already resolved")

    request = db.query(MeetingRequest).filter(MeetingRequest.id == approval.request_id).first()
    ta = db.query(User).filter(User.id == approval.ta_id).first()
    student = db.query(User).filter(User.id == approval.student_id).first()

    # Create Google Calendar event
    google_event_id = None
    meet_link = None
    if ta.google_refresh_token:
        try:
            topic_label = str(request.detected_topic).replace("_", " ").title() if request.detected_topic else "Meeting"
            summary = f"[P{int(request.detected_priority)}] {topic_label} — {student.name}"
            event = create_meeting_with_meet(
                organizer_refresh_token=ta.google_refresh_token,
                student_email=student.email,
                ta_email=ta.email,
                professor_email=current_user.email,
                start_time=approval.start_time,
                end_time=approval.end_time,
                summary=summary,
            )
            google_event_id = event.get("id")
            meet_link = extract_meet_link(event)
        except Exception:
            pass

    # Create the booked meeting
    booked = BookedMeeting(
        request_id=approval.request_id,
        student_id=approval.student_id,
        ta_id=approval.ta_id,
        professor_id=current_user.id,
        start_time=approval.start_time,
        end_time=approval.end_time,
        google_event_id=google_event_id,
        google_meet_link=meet_link,
    )
    db.add(booked)

    # Update statuses
    approval.status = ApprovalStatus.ACCEPTED
    approval.resolved_at = datetime.utcnow()
    request.status = RequestStatus.SCHEDULED
    db.commit()
    db.refresh(booked)

    # Recompute cognitive score
    recompute_and_save(db, approval.ta_id, approval.start_time.date())

    return {"status": "approved", "meeting_id": booked.id, "google_meet_link": meet_link}


class InitiateMeetingBody(BaseModel):
    student_id: int
    ta_id: int
    reason: str
    ticket_id: int | None = None


@router.post("/initiate-meeting")
def initiate_meeting(
    body: InitiateMeetingBody,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_role(UserRole.PROFESSOR)),
):
    """Professor requests a TA to schedule a meeting with a specific student.

    Creates a high-priority MeetingRequest that appears in the TA's queue,
    then emails the TA to act on it urgently.
    """
    # Verify the TA is under this professor
    mapping = db.query(RoleMapping).filter(
        RoleMapping.ta_id == body.ta_id,
        RoleMapping.professor_id == current_user.id,
    ).first()
    if not mapping:
        raise HTTPException(status_code=403, detail="This TA is not assigned to you")

    # Verify the student is under this TA
    student_mapping = db.query(RoleMapping).filter(
        RoleMapping.ta_id == body.ta_id,
        RoleMapping.student_id == body.student_id,
    ).first()
    if not student_mapping:
        raise HTTPException(status_code=403, detail="This student is not assigned to the selected TA")

    ta = db.query(User).filter(User.id == body.ta_id).first()
    student = db.query(User).filter(User.id == body.student_id).first()
    if not ta or not student:
        raise HTTPException(status_code=404, detail="User not found")

    prompt = f"[Professor-initiated] {body.reason}"

    meeting_request = MeetingRequest(
        student_id=body.student_id,
        ta_id=body.ta_id,
        prompt_text=prompt,
        detected_priority=MeetingPriority.P1,
        detected_topic=MeetingTopic.GENERAL,
        status=RequestStatus.PENDING,
    )
    db.add(meeting_request)
    db.commit()
    db.refresh(meeting_request)

    try:
        send_professor_meeting_request_email(
            ta_email=ta.email,
            ta_name=ta.name,
            professor_name=current_user.name,
            student_name=student.name,
            reason=body.reason,
        )
    except Exception:
        pass  # Email is best-effort; meeting request is already saved

    return {
        "id": meeting_request.id,
        "student": {"id": student.id, "name": student.name},
        "ta": {"id": ta.id, "name": ta.name},
        "status": meeting_request.status,
        "created_at": meeting_request.created_at,
    }


@router.post("/reject/{approval_id}")
def reject_booking(
    approval_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_role(UserRole.PROFESSOR)),
):
    approval = db.query(PendingApproval).filter(
        PendingApproval.id == approval_id,
        PendingApproval.professor_id == current_user.id,
    ).first()
    if not approval:
        raise HTTPException(status_code=404, detail="Approval not found")
    if approval.status != ApprovalStatus.PENDING:
        raise HTTPException(status_code=400, detail="Already resolved")

    approval.status = ApprovalStatus.REJECTED
    approval.resolved_at = datetime.utcnow()

    # Set request back to PENDING so TA can rebook
    request = db.query(MeetingRequest).filter(MeetingRequest.id == approval.request_id).first()
    if request:
        request.status = RequestStatus.PENDING

    db.commit()
    return {"status": "rejected"}
