from datetime import datetime, date, timedelta
from sqlalchemy.orm import Session
from cognitive_engine import compute_daily_score, compute_burnout_risk, score_slot, Meeting
from app.models.meeting import BookedMeeting
from app.models.cognitive import CognitiveScore, BurnoutRisk


def _load_ta_meetings_for_date(db: Session, ta_id: int, target_date: date) -> list[Meeting]:
    start = datetime.combine(target_date, datetime.min.time())
    end = datetime.combine(target_date, datetime.max.time())
    records = db.query(BookedMeeting).filter(
        BookedMeeting.ta_id == ta_id,
        BookedMeeting.start_time >= start,
        BookedMeeting.start_time <= end,
    ).all()
    return [Meeting(start=r.start_time, end=r.end_time) for r in records]


def get_or_compute_daily_score(db: Session, ta_id: int, target_date: date) -> CognitiveScore:
    record = db.query(CognitiveScore).filter(
        CognitiveScore.ta_id == ta_id,
        CognitiveScore.date == target_date,
    ).first()
    if record:
        return record

    meetings = _load_ta_meetings_for_date(db, ta_id, target_date)
    data = compute_daily_score(meetings)

    record = CognitiveScore(
        ta_id=ta_id,
        date=target_date,
        score=data["score"],
        meeting_count=data["meeting_count"],
        total_gap_minutes=data["total_gap_minutes"],
        burnout_risk=BurnoutRisk.LOW,
    )
    db.add(record)
    db.commit()
    db.refresh(record)
    return record


def recompute_and_save(db: Session, ta_id: int, target_date: date) -> CognitiveScore:
    record = db.query(CognitiveScore).filter(
        CognitiveScore.ta_id == ta_id,
        CognitiveScore.date == target_date,
    ).first()

    meetings = _load_ta_meetings_for_date(db, ta_id, target_date)
    data = compute_daily_score(meetings)

    # 7-day rolling burnout
    seven_days_ago = target_date - timedelta(days=6)
    past_scores = db.query(CognitiveScore).filter(
        CognitiveScore.ta_id == ta_id,
        CognitiveScore.date >= seven_days_ago,
        CognitiveScore.date < target_date,
    ).all()
    rolling = [s.score for s in past_scores] + [data["score"]]
    risk_str = compute_burnout_risk(rolling)
    risk = BurnoutRisk(risk_str)

    if record:
        record.score = data["score"]
        record.meeting_count = data["meeting_count"]
        record.total_gap_minutes = data["total_gap_minutes"]
        record.burnout_risk = risk
    else:
        record = CognitiveScore(
            ta_id=ta_id,
            date=target_date,
            score=data["score"],
            meeting_count=data["meeting_count"],
            total_gap_minutes=data["total_gap_minutes"],
            burnout_risk=risk,
        )
        db.add(record)

    db.commit()
    db.refresh(record)
    return record


def score_candidate_slot(
    db: Session,
    ta_id: int,
    candidate_start: datetime,
    candidate_end: datetime,
    priority: int,
) -> dict:
    target_date = candidate_start.date()
    meetings = _load_ta_meetings_for_date(db, ta_id, target_date)
    score_record = get_or_compute_daily_score(db, ta_id, target_date)
    return score_slot(candidate_start, candidate_end, meetings, priority, score_record.score)
