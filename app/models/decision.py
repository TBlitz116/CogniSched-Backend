from datetime import datetime
from enum import Enum as PyEnum
from sqlalchemy import Column, Integer, String, Text, DateTime, ForeignKey, Enum, JSON
from app.core.database import Base


class DecisionStatus(str, PyEnum):
    PENDING = "PENDING"
    RESOLVED = "RESOLVED"


class DecisionOutcome(str, PyEnum):
    APPROVED = "APPROVED"
    DENIED = "DENIED"
    ESCALATED_TO_MEETING = "ESCALATED_TO_MEETING"
    NEEDS_MORE_INFO = "NEEDS_MORE_INFO"


class DecisionCard(Base):
    """
    A decision card converts a meeting request into an async yes/no question
    for the professor. Lives separately from ActionTicket because tickets are
    post-meeting action items; decision cards are pre-meeting deflections.
    """
    __tablename__ = "decision_cards"

    id = Column(Integer, primary_key=True, index=True)

    # Source
    request_id = Column(Integer, ForeignKey("meeting_requests.id"), nullable=True)
    student_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    ta_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    professor_id = Column(Integer, ForeignKey("users.id"), nullable=False)

    # AI-drafted, TA-editable content
    question_summary = Column(String, nullable=False)  # 1 sentence
    context = Column(Text, nullable=True)              # background
    ta_recommendation = Column(Text, nullable=True)    # suggested answer + rationale
    options = Column(JSON, nullable=False, default=list)  # ["Approve", "Deny", ...]

    # Outcome
    status = Column(Enum(DecisionStatus), default=DecisionStatus.PENDING, nullable=False)
    outcome = Column(Enum(DecisionOutcome), nullable=True)
    chosen_option = Column(String, nullable=True)      # the exact option label
    professor_note = Column(Text, nullable=True)

    created_at = Column(DateTime, default=datetime.utcnow)
    resolved_at = Column(DateTime, nullable=True)
