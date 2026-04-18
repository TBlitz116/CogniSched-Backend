from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from datetime import datetime
from pydantic import BaseModel
from app.core.database import get_db
from app.api.deps import require_role
from app.models.user import User, UserRole
from app.models.ticket import ActionTicket, TicketStatus
from app.models.mapping import RoleMapping
from app.services.email_service import send_ticket_created_email, send_ticket_notification_email
from transcript_agent import extract_action_items

router = APIRouter()


# ── Request bodies ─────────────────────────────────────────────────────────────

class ExtractRequest(BaseModel):
    transcript: str
    student_id: int


class TicketItem(BaseModel):
    title: str
    description: str = ""
    shared_with_professor: bool = False   # TA decides at create-time; Gemini pre-fills


class CreateTicketsRequest(BaseModel):
    student_id: int
    booked_meeting_id: int | None = None
    items: list[TicketItem]


class UpdateStatusRequest(BaseModel):
    status: TicketStatus
    resolution_note: str | None = None


# ── TA endpoints ───────────────────────────────────────────────────────────────

@router.post("/extract")
def extract_from_transcript(
    body: ExtractRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_role(UserRole.TA)),
):
    """Parse transcript with Gemini and return classified action items (not saved)."""
    student = db.query(User).filter(User.id == body.student_id).first()
    if not student:
        raise HTTPException(status_code=404, detail="Student not found")

    mapping = db.query(RoleMapping).filter(
        RoleMapping.ta_id == current_user.id,
        RoleMapping.student_id == body.student_id,
    ).first()
    if not mapping:
        raise HTTPException(status_code=403, detail="Student not assigned to you")

    items = extract_action_items(body.transcript, student.name)
    # Convert scope to shared_with_professor bool for the frontend
    for item in items:
        item["shared_with_professor"] = item.pop("scope") == "professor"
    return {"items": items}


@router.post("/create")
def create_tickets(
    body: CreateTicketsRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_role(UserRole.TA)),
):
    """Save tickets. Professor-scoped ones are flagged and trigger an email to the professor."""
    if not body.items:
        raise HTTPException(status_code=422, detail="No items provided")

    student = db.query(User).filter(User.id == body.student_id).first()
    if not student:
        raise HTTPException(status_code=404, detail="Student not found")

    mapping = db.query(RoleMapping).filter(
        RoleMapping.ta_id == current_user.id,
        RoleMapping.student_id == body.student_id,
    ).first()
    if not mapping:
        raise HTTPException(status_code=403, detail="Student not assigned to you")

    professor = db.query(User).filter(User.id == mapping.professor_id).first()
    if not professor:
        raise HTTPException(status_code=500, detail="Professor not found")

    created = []
    for item in body.items:
        ticket = ActionTicket(
            student_id=body.student_id,
            ta_id=current_user.id,
            professor_id=mapping.professor_id,
            booked_meeting_id=body.booked_meeting_id,
            title=item.title,
            description=item.description,
            shared_with_professor=item.shared_with_professor,
            status=TicketStatus.OPEN,
        )
        db.add(ticket)
        created.append(ticket)

    db.commit()
    for t in created:
        db.refresh(t)

    # Only email professor for tickets explicitly shared with them
    for ticket in created:
        if ticket.shared_with_professor:
            try:
                send_ticket_created_email(
                    professor_email=professor.email,
                    professor_name=professor.name,
                    student_name=student.name,
                    ta_name=current_user.name,
                    ticket_title=ticket.title,
                    ticket_description=ticket.description or "",
                )
            except Exception:
                pass

    return {
        "created": [
            {
                "id": t.id,
                "title": t.title,
                "description": t.description,
                "status": t.status,
                "shared_with_professor": t.shared_with_professor,
                "created_at": t.created_at,
            }
            for t in created
        ]
    }


@router.post("/{ticket_id}/share")
def share_with_professor(
    ticket_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_role(UserRole.TA)),
):
    """Escalate a TA-only ticket to the professor."""
    ticket = db.query(ActionTicket).filter(
        ActionTicket.id == ticket_id,
        ActionTicket.ta_id == current_user.id,
    ).first()
    if not ticket:
        raise HTTPException(status_code=404, detail="Ticket not found")
    if ticket.shared_with_professor:
        raise HTTPException(status_code=400, detail="Already shared with professor")

    ticket.shared_with_professor = True
    db.commit()

    professor = db.query(User).filter(User.id == ticket.professor_id).first()
    student = db.query(User).filter(User.id == ticket.student_id).first()
    if professor:
        try:
            send_ticket_created_email(
                professor_email=professor.email,
                professor_name=professor.name,
                student_name=student.name if student else "Unknown",
                ta_name=current_user.name,
                ticket_title=ticket.title,
                ticket_description=ticket.description or "",
            )
        except Exception:
            pass

    return {"id": ticket.id, "shared_with_professor": True}


@router.patch("/{ticket_id}/ta-status")
def ta_update_ticket_status(
    ticket_id: int,
    body: UpdateStatusRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_role(UserRole.TA)),
):
    """TA updates status on their own unshared tickets."""
    ticket = db.query(ActionTicket).filter(
        ActionTicket.id == ticket_id,
        ActionTicket.ta_id == current_user.id,
        ActionTicket.shared_with_professor == False,
    ).first()
    if not ticket:
        raise HTTPException(status_code=404, detail="Ticket not found or already shared with professor")

    ticket.status = body.status
    if body.resolution_note is not None:
        ticket.resolution_note = body.resolution_note
    if body.status == TicketStatus.RESOLVED:
        ticket.resolved_at = datetime.utcnow()
    db.commit()

    # Notify student when TA resolves
    student = db.query(User).filter(User.id == ticket.student_id).first()
    if student and body.status == TicketStatus.RESOLVED:
        try:
            send_ticket_notification_email(
                to_email=student.email,
                recipient_name=student.name,
                role="student",
                student_name=student.name,
                ta_name=current_user.name,
                ticket_title=ticket.title,
                new_status=body.status.value,
                resolution_note=body.resolution_note,
            )
        except Exception:
            pass

    return {
        "id": ticket.id,
        "status": ticket.status,
        "resolution_note": ticket.resolution_note,
        "resolved_at": ticket.resolved_at,
    }


@router.get("/mine")
def get_my_tickets(
    db: Session = Depends(get_db),
    current_user: User = Depends(require_role(UserRole.TA)),
):
    """All tickets created by this TA."""
    tickets = db.query(ActionTicket).filter(
        ActionTicket.ta_id == current_user.id,
    ).order_by(ActionTicket.created_at.desc()).all()

    result = []
    for t in tickets:
        student = db.query(User).filter(User.id == t.student_id).first()
        result.append({
            "id": t.id,
            "title": t.title,
            "description": t.description,
            "status": t.status,
            "shared_with_professor": t.shared_with_professor,
            "resolution_note": t.resolution_note,
            "created_at": t.created_at,
            "resolved_at": t.resolved_at,
            "student": {"id": student.id, "name": student.name, "email": student.email} if student else None,
        })
    return result


# ── Professor endpoints ────────────────────────────────────────────────────────

@router.get("/incoming")
def get_incoming_tickets(
    db: Session = Depends(get_db),
    current_user: User = Depends(require_role(UserRole.PROFESSOR)),
):
    """Tickets explicitly shared with this professor."""
    tickets = db.query(ActionTicket).filter(
        ActionTicket.professor_id == current_user.id,
        ActionTicket.shared_with_professor == True,
    ).order_by(ActionTicket.created_at.desc()).all()

    result = []
    for t in tickets:
        student = db.query(User).filter(User.id == t.student_id).first()
        ta = db.query(User).filter(User.id == t.ta_id).first()
        result.append({
            "id": t.id,
            "title": t.title,
            "description": t.description,
            "status": t.status,
            "resolution_note": t.resolution_note,
            "created_at": t.created_at,
            "resolved_at": t.resolved_at,
            "student": {"id": student.id, "name": student.name, "email": student.email} if student else None,
            "ta": {"id": ta.id, "name": ta.name, "email": ta.email} if ta else None,
        })
    return result


@router.patch("/{ticket_id}/status")
def update_ticket_status(
    ticket_id: int,
    body: UpdateStatusRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_role(UserRole.PROFESSOR)),
):
    """Professor updates status on a shared ticket and notifies all parties."""
    ticket = db.query(ActionTicket).filter(
        ActionTicket.id == ticket_id,
        ActionTicket.professor_id == current_user.id,
        ActionTicket.shared_with_professor == True,
    ).first()
    if not ticket:
        raise HTTPException(status_code=404, detail="Ticket not found")

    ticket.status = body.status
    if body.resolution_note is not None:
        ticket.resolution_note = body.resolution_note
    if body.status == TicketStatus.RESOLVED:
        ticket.resolved_at = datetime.utcnow()
    db.commit()

    student = db.query(User).filter(User.id == ticket.student_id).first()
    ta = db.query(User).filter(User.id == ticket.ta_id).first()

    for recipient, role_label in [(ta, "ta"), (student, "student"), (current_user, "professor")]:
        if recipient:
            try:
                send_ticket_notification_email(
                    to_email=recipient.email,
                    recipient_name=recipient.name,
                    role=role_label,
                    student_name=student.name if student else "Unknown",
                    ta_name=ta.name if ta else "Unknown",
                    ticket_title=ticket.title,
                    new_status=body.status.value,
                    resolution_note=body.resolution_note,
                )
            except Exception:
                pass

    return {
        "id": ticket.id,
        "status": ticket.status,
        "resolution_note": ticket.resolution_note,
        "resolved_at": ticket.resolved_at,
    }


# ── Student endpoints ──────────────────────────────────────────────────────────

@router.get("/for-me")
def get_tickets_for_student(
    db: Session = Depends(get_db),
    current_user: User = Depends(require_role(UserRole.STUDENT)),
):
    """All tickets raised for this student."""
    tickets = db.query(ActionTicket).filter(
        ActionTicket.student_id == current_user.id,
    ).order_by(ActionTicket.created_at.desc()).all()

    result = []
    for t in tickets:
        ta = db.query(User).filter(User.id == t.ta_id).first()
        result.append({
            "id": t.id,
            "title": t.title,
            "description": t.description,
            "status": t.status,
            "shared_with_professor": t.shared_with_professor,
            "resolution_note": t.resolution_note,
            "created_at": t.created_at,
            "resolved_at": t.resolved_at,
            "ta": {"id": ta.id, "name": ta.name} if ta else None,
        })
    return result
