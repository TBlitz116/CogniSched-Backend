from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from app.core.database import get_db
from app.api.deps import get_current_user, require_role
from app.models.user import User, UserRole
from app.models.mapping import RoleMapping
from pydantic import BaseModel

router = APIRouter()


class UserOut(BaseModel):
    id: int
    email: str
    name: str
    role: str

    class Config:
        from_attributes = True


@router.get("/my-students", response_model=list[UserOut])
def get_my_students(
    db: Session = Depends(get_db),
    current_user: User = Depends(require_role(UserRole.TA)),
):
    mappings = db.query(RoleMapping).filter(RoleMapping.ta_id == current_user.id).all()
    student_ids = [m.student_id for m in mappings if m.student_id]
    return db.query(User).filter(User.id.in_(student_ids)).all()


@router.get("/my-ta", response_model=UserOut)
def get_my_ta(
    db: Session = Depends(get_db),
    current_user: User = Depends(require_role(UserRole.STUDENT)),
):
    mapping = db.query(RoleMapping).filter(RoleMapping.student_id == current_user.id).first()
    if not mapping:
        raise HTTPException(status_code=404, detail="No TA assigned")
    return db.query(User).filter(User.id == mapping.ta_id).first()
