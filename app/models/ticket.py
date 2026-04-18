from datetime import datetime
from enum import Enum as PyEnum
from sqlalchemy import Column, Integer, String, Text, DateTime, ForeignKey, Enum, Boolean
from app.core.database import Base


class TicketStatus(str, PyEnum):
    OPEN = "OPEN"
    IN_PROGRESS = "IN_PROGRESS"
    RESOLVED = "RESOLVED"


class ActionTicket(Base):
    __tablename__ = "action_tickets"

    id = Column(Integer, primary_key=True, index=True)

    # Parties
    student_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    ta_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    professor_id = Column(Integer, ForeignKey("users.id"), nullable=False)

    # Optional link back to the meeting that generated this ticket
    booked_meeting_id = Column(Integer, ForeignKey("booked_meetings.id"), nullable=True)

    # Content
    title = Column(String, nullable=False)
    description = Column(Text, nullable=True)

    # Scope — False = TA handles it, True = escalated to professor
    shared_with_professor = Column(Boolean, default=False, nullable=False)

    # Lifecycle
    status = Column(Enum(TicketStatus), default=TicketStatus.OPEN, nullable=False)
    resolution_note = Column(Text, nullable=True)

    created_at = Column(DateTime, default=datetime.utcnow)
    resolved_at = Column(DateTime, nullable=True)
