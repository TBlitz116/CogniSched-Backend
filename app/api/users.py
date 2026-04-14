from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session
from app.core.database import get_db
from app.api.deps import get_current_user
from app.models.user import User, UserRole
from app.models.mapping import RoleMapping
from pydantic import BaseModel

router = APIRouter()


class UserOut(BaseModel):
    id: int
    email: str
    name: str
    role: str
    timezone: str

    class Config:
        from_attributes = True


@router.get("/me", response_model=UserOut)
def get_me(current_user: User = Depends(get_current_user)):
    return current_user


@router.get("/me/account")
def get_account(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Returns user profile + team relationships based on role."""
    result = {
        "id": current_user.id,
        "name": current_user.name,
        "email": current_user.email,
        "role": current_user.role,
        "timezone": current_user.timezone,
        "has_google_calendar": current_user.google_refresh_token is not None,
    }

    if current_user.role == UserRole.PROFESSOR:
        # Get all TAs under this professor
        ta_mappings = db.query(RoleMapping).filter(
            RoleMapping.professor_id == current_user.id,
            RoleMapping.student_id == None,
        ).all()
        tas = []
        for m in ta_mappings:
            ta = db.query(User).filter(User.id == m.ta_id).first()
            if not ta:
                continue
            student_count = db.query(RoleMapping).filter(
                RoleMapping.ta_id == ta.id,
                RoleMapping.student_id != None,
            ).count()
            tas.append({
                "id": ta.id,
                "name": ta.name,
                "email": ta.email,
                "student_count": student_count,
            })
        result["tas"] = tas

    elif current_user.role == UserRole.TA:
        # Get professor
        mapping = db.query(RoleMapping).filter(
            RoleMapping.ta_id == current_user.id,
            RoleMapping.student_id == None,
        ).first()
        if mapping:
            professor = db.query(User).filter(User.id == mapping.professor_id).first()
            if professor:
                result["professor"] = {
                    "id": professor.id,
                    "name": professor.name,
                    "email": professor.email,
                }
        # Get students
        student_mappings = db.query(RoleMapping).filter(
            RoleMapping.ta_id == current_user.id,
            RoleMapping.student_id != None,
        ).all()
        students = []
        for m in student_mappings:
            student = db.query(User).filter(User.id == m.student_id).first()
            if student:
                students.append({
                    "id": student.id,
                    "name": student.name,
                    "email": student.email,
                })
        result["students"] = students

    elif current_user.role == UserRole.STUDENT:
        # Get TA
        mapping = db.query(RoleMapping).filter(
            RoleMapping.student_id == current_user.id
        ).first()
        if mapping:
            ta = db.query(User).filter(User.id == mapping.ta_id).first()
            if ta:
                result["ta"] = {
                    "id": ta.id,
                    "name": ta.name,
                    "email": ta.email,
                }
            professor = db.query(User).filter(User.id == mapping.professor_id).first()
            if professor:
                result["professor"] = {
                    "id": professor.id,
                    "name": professor.name,
                    "email": professor.email,
                }

    return result
