from datetime import datetime
from sqlalchemy import Column, Integer, String, DateTime, Boolean, ForeignKey, Text
from app.core.database import Base


class CalendarBlock(Base):
    __tablename__ = "calendar_blocks"

    id = Column(Integer, primary_key=True, index=True)
    professor_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    title = Column(String, nullable=False)
    start_time = Column(DateTime, nullable=False)
    end_time = Column(DateTime, nullable=False)
    source_prompt = Column(Text, nullable=True)
    is_available = Column(Boolean, default=False)
    google_event_id = Column(String, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)
