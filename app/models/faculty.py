from datetime import datetime
from sqlalchemy import Column, Integer, String, DateTime
from app.core.database import Base


class VerifiedFaculty(Base):
    __tablename__ = "verified_faculty"

    id = Column(Integer, primary_key=True, index=True)
    name = Column(String, nullable=False)
    email = Column(String, unique=True, index=True, nullable=True)
    title = Column(String, nullable=True)
    department = Column(String, default="Computer Science and Electrical Engineering")
    scraped_at = Column(DateTime, default=datetime.utcnow)
