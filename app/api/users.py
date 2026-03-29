from fastapi import APIRouter, Depends
from app.api.deps import get_current_user
from app.models.user import User
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
