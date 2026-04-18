from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from datetime import datetime
from pydantic import BaseModel
from app.core.database import get_db
from app.api.deps import require_role
from app.models.user import User, UserRole
from app.models.decision import DecisionCard, DecisionStatus, DecisionOutcome
from app.models.meeting import MeetingRequest, RequestStatus
from app.models.mapping import RoleMapping
from app.services.email_service import send_ticket_notification_email
from decision_agent import draft_decision_card

router = APIRouter()


# ── Request bodies ─────────────────────────────────────────────────────────────

class DraftRequest(BaseModel):
    request_id: int
    ta_note: str | None = None


class CreateRequest(BaseModel):
    request_id: int
    question_summary: str
    context: str = ""
    ta_recommendation: str = ""
    options: list[str]


class ResolveRequest(BaseModel):
    outcome: DecisionOutcome
    chosen_option: str | None = None
    professor_note: str | None = None


# ── TA endpoints ───────────────────────────────────────────────────────────────

@router.post("/draft")
def draft_from_request(
    body: DraftRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_role(UserRole.TA)),
):
    """Ask Gemini to draft a decision card from a pending meeting request."""
    request = db.query(MeetingRequest).filter(MeetingRequest.id == body.request_id).first()
    if not request or request.ta_id != current_user.id:
        raise HTTPException(status_code=404, detail="Request not found")
    if request.status != RequestStatus.PENDING:
        raise HTTPException(status_code=400, detail="Request is no longer pending")

    student = db.query(User).filter(User.id == request.student_id).first()
    if not student:
        raise HTTPException(status_code=404, detail="Student not found")

    draft = draft_decision_card(
        prompt_text=request.prompt_text,
        student_name=student.name,
        ta_note=body.ta_note,
    )
    return draft


@router.post("/create")
def create_decision(
    body: CreateRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_role(UserRole.TA)),
):
    """TA confirms (optionally edited) draft. Closes the meeting request."""
    request = db.query(MeetingRequest).filter(MeetingRequest.id == body.request_id).first()
    if not request or request.ta_id != current_user.id:
        raise HTTPException(status_code=404, detail="Request not found")
    if request.status != RequestStatus.PENDING:
        raise HTTPException(status_code=400, detail="Request is no longer pending")

    mapping = db.query(RoleMapping).filter(RoleMapping.ta_id == current_user.id).first()
    if not mapping:
        raise HTTPException(status_code=400, detail="No professor mapping found")

    options = [str(o).strip() for o in body.options if str(o).strip()]
    if not options:
        raise HTTPException(status_code=422, detail="At least one option is required")

    card = DecisionCard(
        request_id=request.id,
        student_id=request.student_id,
        ta_id=current_user.id,
        professor_id=mapping.professor_id,
        question_summary=body.question_summary.strip(),
        context=body.context.strip(),
        ta_recommendation=body.ta_recommendation.strip(),
        options=options,
        status=DecisionStatus.PENDING,
    )
    db.add(card)
    # Meeting request is deflected — mark it DECLINED so it drops out of the pending queue.
    # The DecisionCard now carries the lifecycle.
    request.status = RequestStatus.DECLINED
    db.commit()
    db.refresh(card)

    return _serialize_card(db, card, include_parties=True)


@router.get("/mine")
def list_my_decisions(
    db: Session = Depends(get_db),
    current_user: User = Depends(require_role(UserRole.TA)),
):
    """Decisions this TA has drafted (all statuses)."""
    cards = db.query(DecisionCard).filter(
        DecisionCard.ta_id == current_user.id,
    ).order_by(DecisionCard.created_at.desc()).all()
    return [_serialize_card(db, c, include_parties=True) for c in cards]


# ── Professor endpoints ────────────────────────────────────────────────────────

@router.get("/inbox")
def professor_inbox(
    db: Session = Depends(get_db),
    current_user: User = Depends(require_role(UserRole.PROFESSOR)),
):
    """Pending decision cards waiting on this professor."""
    cards = db.query(DecisionCard).filter(
        DecisionCard.professor_id == current_user.id,
        DecisionCard.status == DecisionStatus.PENDING,
    ).order_by(DecisionCard.created_at.asc()).all()
    return [_serialize_card(db, c, include_parties=True) for c in cards]


@router.get("/history")
def professor_history(
    db: Session = Depends(get_db),
    current_user: User = Depends(require_role(UserRole.PROFESSOR)),
):
    """Resolved decisions — for the 'cleared today' count in the UI."""
    cards = db.query(DecisionCard).filter(
        DecisionCard.professor_id == current_user.id,
        DecisionCard.status == DecisionStatus.RESOLVED,
    ).order_by(DecisionCard.resolved_at.desc()).limit(50).all()
    return [_serialize_card(db, c, include_parties=True) for c in cards]


@router.post("/{card_id}/resolve")
def resolve_decision(
    card_id: int,
    body: ResolveRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_role(UserRole.PROFESSOR)),
):
    """Professor picks an outcome. Notifies student + TA."""
    card = db.query(DecisionCard).filter(
        DecisionCard.id == card_id,
        DecisionCard.professor_id == current_user.id,
    ).first()
    if not card:
        raise HTTPException(status_code=404, detail="Decision not found")
    if card.status != DecisionStatus.PENDING:
        raise HTTPException(status_code=400, detail="Already resolved")

    card.outcome = body.outcome
    card.chosen_option = body.chosen_option
    card.professor_note = body.professor_note
    card.status = DecisionStatus.RESOLVED
    card.resolved_at = datetime.utcnow()

    # If the professor decides this actually needs a meeting, re-open the original
    # request so the TA can book it through the normal scheduler flow.
    if body.outcome == DecisionOutcome.ESCALATED_TO_MEETING and card.request_id:
        req = db.query(MeetingRequest).filter(MeetingRequest.id == card.request_id).first()
        if req:
            req.status = RequestStatus.PENDING

    db.commit()

    student = db.query(User).filter(User.id == card.student_id).first()
    ta = db.query(User).filter(User.id == card.ta_id).first()
    label = body.chosen_option or body.outcome.value

    for recipient, role_label in [(student, "student"), (ta, "ta")]:
        if recipient:
            try:
                send_ticket_notification_email(
                    to_email=recipient.email,
                    recipient_name=recipient.name,
                    role=role_label,
                    student_name=student.name if student else "Unknown",
                    ta_name=ta.name if ta else "Unknown",
                    ticket_title=f"Decision: {card.question_summary}",
                    new_status=label,
                    resolution_note=body.professor_note,
                )
            except Exception:
                pass

    return _serialize_card(db, card, include_parties=True)


# ── Student endpoints ──────────────────────────────────────────────────────────

@router.get("/for-me")
def decisions_for_student(
    db: Session = Depends(get_db),
    current_user: User = Depends(require_role(UserRole.STUDENT)),
):
    cards = db.query(DecisionCard).filter(
        DecisionCard.student_id == current_user.id,
    ).order_by(DecisionCard.created_at.desc()).all()
    return [_serialize_card(db, c, include_parties=True, hide_recommendation=True) for c in cards]


# ── Serialization ──────────────────────────────────────────────────────────────

def _serialize_card(
    db: Session,
    card: DecisionCard,
    include_parties: bool = False,
    hide_recommendation: bool = False,
) -> dict:
    data = {
        "id": card.id,
        "request_id": card.request_id,
        "question_summary": card.question_summary,
        "context": card.context,
        "options": card.options or [],
        "status": card.status.value if card.status else None,
        "outcome": card.outcome.value if card.outcome else None,
        "chosen_option": card.chosen_option,
        "professor_note": card.professor_note,
        "created_at": card.created_at,
        "resolved_at": card.resolved_at,
    }
    if not hide_recommendation:
        data["ta_recommendation"] = card.ta_recommendation

    if include_parties:
        student = db.query(User).filter(User.id == card.student_id).first()
        ta = db.query(User).filter(User.id == card.ta_id).first()
        data["student"] = {"id": student.id, "name": student.name, "email": student.email} if student else None
        data["ta"] = {"id": ta.id, "name": ta.name, "email": ta.email} if ta else None

    return data
