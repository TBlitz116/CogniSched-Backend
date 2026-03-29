import enum
from datetime import datetime
from sqlalchemy import Column, Integer, String, DateTime, Enum, ForeignKey, Text, Float
from app.core.database import Base


class MeetingPriority(int, enum.Enum):
    P1 = 1
    P2 = 2
    P3 = 3
    P4 = 4


class MeetingTopic(str, enum.Enum):
    RECOMMENDATION = "RECOMMENDATION"
    EXAM_QUESTION = "EXAM_QUESTION"
    EXAM_REFLECTION = "EXAM_REFLECTION"
    GENERAL = "GENERAL"


class RequestStatus(str, enum.Enum):
    PENDING = "PENDING"
    AWAITING_APPROVAL = "AWAITING_APPROVAL"
    SCHEDULED = "SCHEDULED"
    DECLINED = "DECLINED"


class MeetingRequest(Base):
    __tablename__ = "meeting_requests"

    id = Column(Integer, primary_key=True, index=True)
    student_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    ta_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    prompt_text = Column(Text, nullable=False)
    detected_priority = Column(Enum(MeetingPriority), nullable=True)
    detected_topic = Column(Enum(MeetingTopic), nullable=True)
    preferred_time_range = Column(String, nullable=True)
    status = Column(Enum(RequestStatus), default=RequestStatus.PENDING)
    created_at = Column(DateTime, default=datetime.utcnow)


class BookedMeeting(Base):
    __tablename__ = "booked_meetings"

    id = Column(Integer, primary_key=True, index=True)
    request_id = Column(Integer, ForeignKey("meeting_requests.id"), nullable=False)
    student_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    ta_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    professor_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    start_time = Column(DateTime, nullable=False)
    end_time = Column(DateTime, nullable=False)
    google_event_id = Column(String, nullable=True)
    google_meet_link = Column(String, nullable=True)
    cognitive_score_impact = Column(Float, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)
