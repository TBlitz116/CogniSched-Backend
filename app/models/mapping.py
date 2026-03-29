from sqlalchemy import Column, Integer, ForeignKey
from app.core.database import Base


class RoleMapping(Base):
    __tablename__ = "role_mappings"

    id = Column(Integer, primary_key=True, index=True)
    student_id = Column(Integer, ForeignKey("users.id"), nullable=True)
    ta_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    professor_id = Column(Integer, ForeignKey("users.id"), nullable=False)
