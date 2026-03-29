from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session
from datetime import date, timedelta
from app.core.database import get_db
from app.api.deps import require_role
from app.models.user import User, UserRole
from app.models.cognitive import CognitiveScore
from app.models.meeting import BookedMeeting

router = APIRouter()


@router.get("/cognitive")
def get_cognitive_scores(
    db: Session = Depends(get_db),
    current_user: User = Depends(require_role(UserRole.TA)),
):
    seven_days_ago = date.today() - timedelta(days=6)
    scores = db.query(CognitiveScore).filter(
        CognitiveScore.ta_id == current_user.id,
        CognitiveScore.date >= seven_days_ago,
    ).order_by(CognitiveScore.date.asc()).all()

    return [
        {
            "date": s.date.isoformat(),
            "score": s.score,
            "burnout_risk": s.burnout_risk,
            "meeting_count": s.meeting_count,
            "total_gap_minutes": s.total_gap_minutes,
        }
        for s in scores
    ]


@router.get("/burnout")
def get_burnout_trend(
    db: Session = Depends(get_db),
    current_user: User = Depends(require_role(UserRole.TA)),
):
    thirty_days_ago = date.today() - timedelta(days=29)
    scores = db.query(CognitiveScore).filter(
        CognitiveScore.ta_id == current_user.id,
        CognitiveScore.date >= thirty_days_ago,
    ).order_by(CognitiveScore.date.asc()).all()

    if not scores:
        return {"current_risk": "LOW", "trend": []}

    latest = scores[-1]
    return {
        "current_risk": latest.burnout_risk,
        "trend": [
            {"date": s.date.isoformat(), "score": s.score, "risk": s.burnout_risk}
            for s in scores
        ],
    }


@router.get("/density")
def get_meeting_density(
    db: Session = Depends(get_db),
    current_user: User = Depends(require_role(UserRole.TA)),
):
    seven_days_ago = date.today() - timedelta(days=6)
    from datetime import datetime
    window_start = datetime.combine(seven_days_ago, datetime.min.time())

    meetings = db.query(BookedMeeting).filter(
        BookedMeeting.ta_id == current_user.id,
        BookedMeeting.start_time >= window_start,
    ).all()

    density: dict[int, int] = {h: 0 for h in range(9, 18)}
    for m in meetings:
        hour = m.start_time.hour
        if hour in density:
            density[hour] += 1

    return [{"hour": h, "count": density[h]} for h in sorted(density)]
