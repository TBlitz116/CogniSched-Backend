"""
Generates candidate 30-minute slots for a meeting request
and ranks them using the cognitive engine.
Supports both auto-generation and prompt-based generation via Gemini.
"""
from datetime import datetime, timedelta
from sqlalchemy.orm import Session
from app.models.meeting import MeetingRequest, BookedMeeting
from app.models.calendar import CalendarBlock
from app.models.mapping import RoleMapping
from app.models.cognitive import CognitiveScore
from app.models.user import User
from app.services.cognitive_service import score_candidate_slot
from app.services.calendar_service import get_busy_slots
from slot_prompt_agent import parse_slot_prompt
from cognitive_engine import compute_daily_score, Meeting
from app.core.redis_client import cache_get, cache_set

SLOT_DURATION_MINUTES = 30
BUSINESS_START_HOUR = 9
BUSINESS_END_HOUR = 17
PROFESSOR_RECOVERY_MINUTES = 45  # Buffer after professor blocks

PRIORITY_WINDOW_DAYS = {1: 2, 2: 3, 3: 5, 4: 7}

# Cache TTLs
_TTL_SLOTS = 5 * 60         # 5 min — slot suggestions
_TTL_PROF_COG = 15 * 60     # 15 min — professor daily cognitive score


def _get_professor_daily_score(db: Session, professor_id: int, target_date) -> dict:
    """Compute the professor's cognitive load for a day based on their calendar blocks.
    Result is cached in Redis for 15 minutes, shared across all TAs in the same team."""
    if isinstance(target_date, datetime):
        d = target_date.date()
    else:
        d = target_date

    cache_key = f"prof:cog:{professor_id}:{d.isoformat()}"
    cached = cache_get(cache_key)
    if cached is not None:
        return cached

    day_start = datetime.combine(d, datetime.min.time())
    day_end = datetime.combine(d, datetime.max.time())

    blocks = db.query(CalendarBlock).filter(
        CalendarBlock.professor_id == professor_id,
        CalendarBlock.start_time >= day_start,
        CalendarBlock.start_time <= day_end,
    ).all()

    meetings = [Meeting(start=b.start_time, end=b.end_time, topic=b.title) for b in blocks]
    data = compute_daily_score(meetings)
    score = data["score"]

    if score <= 30:
        label = "Light"
    elif score <= 60:
        label = "Moderate"
    else:
        label = "Heavy"

    result = {"score": round(score, 1), "label": label}
    cache_set(cache_key, result, _TTL_PROF_COG)
    return result


def _overlaps(start: datetime, end: datetime, records: list, start_attr: str, end_attr: str) -> bool:
    for r in records:
        if getattr(r, start_attr) < end and getattr(r, end_attr) > start:
            return True
    return False


def _in_professor_recovery(start: datetime, professor_blocks: list) -> bool:
    """Check if a slot falls within the professor's recovery window after a block."""
    for block in professor_blocks:
        block_end = block.end_time
        block_duration = (block.end_time - block.start_time).total_seconds() / 60
        # Only apply recovery buffer for blocks longer than 60 minutes
        if block_duration > 60:
            recovery_end = block_end + timedelta(minutes=PROFESSOR_RECOVERY_MINUTES)
            if block_end <= start < recovery_end:
                return True
    return False


def generate_suggestions(db: Session, request_id: int, count: int = 3) -> list[dict]:
    cache_key = f"slot:rec:{request_id}"
    cached = cache_get(cache_key)
    if cached is not None:
        return cached

    request = db.query(MeetingRequest).filter(MeetingRequest.id == request_id).first()
    if not request:
        return []

    priority = request.detected_priority or 4
    window_days = PRIORITY_WINDOW_DAYS.get(int(priority), 7)

    now = datetime.utcnow()
    window_end = now + timedelta(days=window_days)

    # TA's already-booked meetings
    booked = db.query(BookedMeeting).filter(
        BookedMeeting.ta_id == request.ta_id,
        BookedMeeting.start_time >= now,
        BookedMeeting.start_time <= window_end,
    ).all()

    # Professor's blocked times — hard unavailable, never bookable
    mapping = db.query(RoleMapping).filter(RoleMapping.ta_id == request.ta_id).first()
    professor_blocks = []
    if mapping:
        professor_blocks = db.query(CalendarBlock).filter(
            CalendarBlock.professor_id == mapping.professor_id,
            CalendarBlock.is_available == False,
            CalendarBlock.start_time >= now,
            CalendarBlock.start_time <= window_end,
        ).all()

    candidates = []
    # Recommended slots start from 24h from now — soonest slots cover the urgent window
    search_start = now.replace(minute=0, second=0, microsecond=0) + timedelta(hours=24)
    current = search_start
    professor_id = mapping.professor_id if mapping else None

    while current < window_end and len(candidates) < count * 5:
        if current.weekday() < 5 and BUSINESS_START_HOUR <= current.hour < BUSINESS_END_HOUR:
            slot_end = current + timedelta(minutes=SLOT_DURATION_MINUTES)
            if slot_end.hour <= BUSINESS_END_HOUR:
                ta_free = not _overlaps(current, slot_end, booked, "start_time", "end_time")
                prof_free = not _overlaps(current, slot_end, professor_blocks, "start_time", "end_time")
                in_recovery = _in_professor_recovery(current, professor_blocks)
                if ta_free and prof_free and not in_recovery:
                    prof_daily: dict = {}
                    if professor_id is not None:
                        prof_daily = _get_professor_daily_score(db, professor_id, current)
                    score_data = score_candidate_slot(
                        db, request.ta_id, current, slot_end, int(priority),
                        professor_load_score=prof_daily.get("score") or 0.0,
                    )
                    candidates.append({
                        "slot": current.isoformat() + "Z",
                        "duration_minutes": SLOT_DURATION_MINUTES,
                        "score": score_data["slot_score"],
                        "explanation": {
                            **score_data["explanation"],
                            "professor_cognitive_score": prof_daily.get("score"),
                            "professor_load_label": prof_daily.get("label"),
                        },
                    })
        current += timedelta(minutes=30)

    candidates.sort(key=lambda x: x["score"])
    for i, c in enumerate(candidates[:count]):
        c["rank"] = i + 1
    result = candidates[:count]
    cache_set(cache_key, result, _TTL_SLOTS)
    return result


def generate_soonest_suggestions(db: Session, request_id: int, count: int = 3) -> list[dict]:
    """Generate slot suggestions sorted by earliest available time."""
    cache_key = f"slot:soonest:{request_id}"
    cached = cache_get(cache_key)
    if cached is not None:
        return cached

    request = db.query(MeetingRequest).filter(MeetingRequest.id == request_id).first()
    if not request:
        return []

    priority = request.detected_priority or 4
    window_days = PRIORITY_WINDOW_DAYS.get(int(priority), 7)

    now = datetime.utcnow()
    window_end = now + timedelta(days=window_days)

    booked = db.query(BookedMeeting).filter(
        BookedMeeting.ta_id == request.ta_id,
        BookedMeeting.start_time >= now,
        BookedMeeting.start_time <= window_end,
    ).all()

    mapping = db.query(RoleMapping).filter(RoleMapping.ta_id == request.ta_id).first()
    professor_blocks = []
    if mapping:
        professor_blocks = db.query(CalendarBlock).filter(
            CalendarBlock.professor_id == mapping.professor_id,
            CalendarBlock.is_available == False,
            CalendarBlock.start_time >= now,
            CalendarBlock.start_time <= window_end,
        ).all()

    candidates = []
    # Soonest slots are the earliest available — cap search at 48 hours
    soonest_end = now + timedelta(hours=48)
    current = now.replace(minute=0, second=0, microsecond=0) + timedelta(hours=1)
    professor_id = mapping.professor_id if mapping else None

    while current < soonest_end and len(candidates) < count:
        if current.weekday() < 5 and BUSINESS_START_HOUR <= current.hour < BUSINESS_END_HOUR:
            slot_end = current + timedelta(minutes=SLOT_DURATION_MINUTES)
            if slot_end.hour <= BUSINESS_END_HOUR:
                ta_free = not _overlaps(current, slot_end, booked, "start_time", "end_time")
                prof_free = not _overlaps(current, slot_end, professor_blocks, "start_time", "end_time")
                in_recovery = _in_professor_recovery(current, professor_blocks)
                if ta_free and prof_free and not in_recovery:
                    prof_daily: dict = {}
                    if professor_id is not None:
                        prof_daily = _get_professor_daily_score(db, professor_id, current)
                    score_data = score_candidate_slot(
                        db, request.ta_id, current, slot_end, int(priority),
                        professor_load_score=prof_daily.get("score") or 0.0,
                    )
                    candidates.append({
                        "slot": current.isoformat() + "Z",
                        "duration_minutes": SLOT_DURATION_MINUTES,
                        "score": score_data["slot_score"],
                        "explanation": {
                            **score_data["explanation"],
                            "professor_cognitive_score": prof_daily.get("score"),
                            "professor_load_label": prof_daily.get("label"),
                        },
                        "rank": len(candidates) + 1,
                    })
        current += timedelta(minutes=30)

    # Already sorted by time since we iterate chronologically
    cache_set(cache_key, candidates, _TTL_SLOTS)
    return candidates


def generate_prompt_suggestions(db: Session, request_id: int, prompt: str, count: int = 3) -> list[dict]:
    """Generate slot suggestions based on TA's natural language prompt."""
    request = db.query(MeetingRequest).filter(MeetingRequest.id == request_id).first()
    if not request:
        return []

    priority = request.detected_priority or 4
    now = datetime.utcnow()
    current_date = now.strftime("%Y-%m-%d")

    # Gather context: TA's meetings
    booked = db.query(BookedMeeting).filter(
        BookedMeeting.ta_id == request.ta_id,
        BookedMeeting.start_time >= now,
    ).all()
    existing_meetings = [
        {"start": m.start_time.isoformat(), "end": m.end_time.isoformat()}
        for m in booked
    ]

    # Professor's blocks
    mapping = db.query(RoleMapping).filter(RoleMapping.ta_id == request.ta_id).first()
    prof_blocks_list = []
    prof_busy_list = []
    if mapping:
        blocks = db.query(CalendarBlock).filter(
            CalendarBlock.professor_id == mapping.professor_id,
            CalendarBlock.is_available == False,
            CalendarBlock.start_time >= now,
        ).all()
        prof_blocks_list = [
            {"start": b.start_time.isoformat(), "end": b.end_time.isoformat()}
            for b in blocks
        ]
        # Professor's Google Calendar busy slots (cached 10 min)
        professor = db.query(User).filter(User.id == mapping.professor_id).first()
        if professor and professor.google_refresh_token:
            gcal_key = f"gcal:busy:{mapping.professor_id}"
            prof_busy_list = cache_get(gcal_key) or []
            if not prof_busy_list:
                try:
                    prof_busy_list = get_busy_slots(professor.google_refresh_token)
                    cache_set(gcal_key, prof_busy_list, 10 * 60)
                except Exception:
                    pass

    # Current cognitive state
    latest_score = db.query(CognitiveScore).filter(
        CognitiveScore.ta_id == request.ta_id,
    ).order_by(CognitiveScore.date.desc()).first()
    current_cog_score = latest_score.score if latest_score else 0.0
    burnout_risk = latest_score.burnout_risk if latest_score else "LOW"

    # Ask Gemini to interpret the prompt
    prefs = parse_slot_prompt(
        prompt=prompt,
        current_date=current_date,
        existing_meetings=existing_meetings,
        professor_blocks=prof_blocks_list,
        professor_busy=prof_busy_list,
        priority=int(priority),
        current_cognitive_score=current_cog_score,
        burnout_risk=str(burnout_risk),
    )

    # Generate slots filtered by Gemini's interpreted preferences
    preferred_dates = prefs.get("preferred_dates", [])
    pref_start = prefs.get("preferred_start_hour")
    pref_end = prefs.get("preferred_end_hour")
    avoid_b2b = prefs.get("avoid_back_to_back", False)
    protect_dw = prefs.get("protect_deep_work", False)
    duration = prefs.get("duration_minutes", 30)

    # Convert professor blocks + busy into a combined unavailable list for overlap checks
    prof_block_records = []
    if mapping:
        prof_block_records = db.query(CalendarBlock).filter(
            CalendarBlock.professor_id == mapping.professor_id,
            CalendarBlock.is_available == False,
            CalendarBlock.start_time >= now,
        ).all()

    start_hour = pref_start if pref_start else BUSINESS_START_HOUR
    end_hour = pref_end if pref_end else BUSINESS_END_HOUR

    candidates = []
    professor_id = mapping.professor_id if mapping else None

    for date_str in preferred_dates:
        try:
            target_date = datetime.strptime(date_str, "%Y-%m-%d")
        except ValueError:
            continue

        current = target_date.replace(hour=start_hour, minute=0, second=0, microsecond=0)
        day_end = target_date.replace(hour=end_hour, minute=0, second=0, microsecond=0)

        while current < day_end and len(candidates) < count * 5:
            slot_end = current + timedelta(minutes=duration)
            if slot_end.hour > end_hour or (slot_end.hour == end_hour and slot_end.minute > 0 and end_hour == BUSINESS_END_HOUR):
                current += timedelta(minutes=30)
                continue

            # Skip deep work window if requested
            if protect_dw and current.hour < 11 and current.hour >= 9:
                current += timedelta(minutes=30)
                continue

            # Check TA availability
            ta_free = not _overlaps(current, slot_end, booked, "start_time", "end_time")
            # Check professor availability
            prof_free = not _overlaps(current, slot_end, prof_block_records, "start_time", "end_time")

            # Check professor Google busy slots
            google_conflict = False
            for busy in prof_busy_list:
                busy_start = datetime.fromisoformat(busy["start"].replace("Z", ""))
                busy_end = datetime.fromisoformat(busy["end"].replace("Z", ""))
                if busy_start < slot_end and busy_end > current:
                    google_conflict = True
                    break

            in_recovery = _in_professor_recovery(current, prof_block_records)
            if ta_free and prof_free and not google_conflict and not in_recovery:
                # Check back-to-back if avoidance requested
                if avoid_b2b:
                    too_close = False
                    for m in booked:
                        gap_before = (current - m.end_time).total_seconds() / 60
                        gap_after = (m.start_time - slot_end).total_seconds() / 60
                        if 0 <= gap_before < 15 or 0 <= gap_after < 15:
                            too_close = True
                            break
                    if too_close:
                        current += timedelta(minutes=30)
                        continue

                prof_daily: dict = {}
                if professor_id is not None:
                    prof_daily = _get_professor_daily_score(db, professor_id, current)
                score_data = score_candidate_slot(
                    db, request.ta_id, current, slot_end, int(priority),
                    professor_load_score=prof_daily.get("score") or 0.0,
                )
                candidates.append({
                    "slot": current.isoformat() + "Z",
                    "duration_minutes": duration,
                    "score": score_data["slot_score"],
                    "explanation": {
                        **score_data["explanation"],
                        "professor_cognitive_score": prof_daily.get("score"),
                        "professor_load_label": prof_daily.get("label"),
                    },
                })
            current += timedelta(minutes=30)

    candidates.sort(key=lambda x: x["score"])
    for i, c in enumerate(candidates[:count]):
        c["rank"] = i + 1

    result = candidates[:count]

    # Attach Gemini's reasoning
    if result:
        result[0]["prompt_reasoning"] = prefs.get("reasoning", "")

    return result
