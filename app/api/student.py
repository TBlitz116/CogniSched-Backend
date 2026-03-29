from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from app.core.database import get_db
from app.api.deps import require_role
from app.models.user import User, UserRole
from app.models.meeting import MeetingRequest, RequestStatus, MeetingPriority, MeetingTopic
from app.models.mapping import RoleMapping
from app.services.priority_service import classify
from pydantic import BaseModel
from datetime import datetime

router = APIRouter()


class NewRequestBody(BaseModel):
    prompt_text: str


class RequestOut(BaseModel):
    id: int
    prompt_text: str
    detected_priority: int | None
    detected_topic: str | None
    preferred_time_range: str | None
    status: str
    created_at: datetime
    summary: str | None = None

    class Config:
        from_attributes = True


@router.post("/new", response_model=RequestOut)
def submit_request(
    body: NewRequestBody,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_role(UserRole.STUDENT)),
):
    mapping = db.query(RoleMapping).filter(RoleMapping.student_id == current_user.id).first()
    if not mapping:
        raise HTTPException(status_code=400, detail="You are not assigned to a TA yet")

    classification = classify(body.prompt_text)

    request = MeetingRequest(
        student_id=current_user.id,
        ta_id=mapping.ta_id,
        prompt_text=body.prompt_text,
        detected_priority=MeetingPriority(classification["priority"]),
        detected_topic=MeetingTopic(classification["topic"]),
        preferred_time_range=classification.get("extracted_time_hint"),
        status=RequestStatus.PENDING,
    )
    db.add(request)
    db.commit()
    db.refresh(request)

    result = RequestOut.model_validate(request)
    result.summary = classification.get("summary")
    return result


@router.get("/mine", response_model=list[RequestOut])
def my_requests(
    db: Session = Depends(get_db),
    current_user: User = Depends(require_role(UserRole.STUDENT)),
):
    return db.query(MeetingRequest).filter(
        MeetingRequest.student_id == current_user.id
    ).order_by(MeetingRequest.created_at.desc()).all()
