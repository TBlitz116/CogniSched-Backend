"""
Cognitive load engine for TA scheduling.
Scores daily load (0-100) and burnout risk.
"""
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Sequence


@dataclass
class Meeting:
    start: datetime
    end: datetime
    topic: str = ""


BUFFER_THRESHOLD_MINUTES = 15
DEEP_WORK_START = 9   # 9am
DEEP_WORK_END = 11    # 11am — protected morning block


def compute_daily_score(meetings: Sequence[Meeting]) -> dict:
    if not meetings:
        return {"score": 0, "meeting_count": 0, "back_to_back_pairs": 0,
                "context_switches": 0, "deep_work_violations": 0, "total_gap_minutes": 0}

    sorted_meetings = sorted(meetings, key=lambda m: m.start)
    meeting_count = len(sorted_meetings)
    back_to_back = 0
    context_switches = 0
    deep_work_violations = 0
    total_gap = 0

    for i, m in enumerate(sorted_meetings):
        # Deep work violation: meeting overlaps 9–11am
        if m.start.hour < DEEP_WORK_END and m.end.hour > DEEP_WORK_START:
            deep_work_violations += 1

        if i > 0:
            prev = sorted_meetings[i - 1]
            gap = (m.start - prev.end).total_seconds() / 60
            total_gap += max(gap, 0)
            if 0 <= gap < BUFFER_THRESHOLD_MINUTES:
                back_to_back += 1
            if prev.topic and m.topic and prev.topic != m.topic:
                context_switches += 1

    score = (
        meeting_count * 10
        + back_to_back * 15
        + context_switches * 8
        + deep_work_violations * 20
        - total_gap / 5
    )
    score = max(0, min(100, score))

    return {
        "score": score,
        "meeting_count": meeting_count,
        "back_to_back_pairs": back_to_back,
        "context_switches": context_switches,
        "deep_work_violations": deep_work_violations,
        "total_gap_minutes": int(total_gap),
    }


def compute_burnout_risk(daily_scores: Sequence[float]) -> str:
    if not daily_scores:
        return "LOW"
    avg = sum(daily_scores) / len(daily_scores)
    if avg < 40:
        return "LOW"
    if avg <= 65:
        return "MEDIUM"
    return "HIGH"


def score_slot(
    candidate_start: datetime,
    candidate_end: datetime,
    existing_meetings: Sequence[Meeting],
    priority: int,
    current_daily_score: float,
) -> dict:
    """
    Lower score = better slot.
    """
    test_meetings = list(existing_meetings) + [Meeting(start=candidate_start, end=candidate_end)]
    new_score_data = compute_daily_score(test_meetings)
    cognitive_delta = new_score_data["score"] - current_daily_score

    # Back-to-back penalty
    back_to_back_penalty = 0
    for m in existing_meetings:
        gap_before = (candidate_start - m.end).total_seconds() / 60
        gap_after = (m.start - candidate_end).total_seconds() / 60
        if 0 <= gap_before < BUFFER_THRESHOLD_MINUTES or 0 <= gap_after < BUFFER_THRESHOLD_MINUTES:
            back_to_back_penalty = 15
            break

    # Deep work penalty
    deep_work_penalty = 0
    if candidate_start.hour < DEEP_WORK_END and candidate_end.hour > DEEP_WORK_START:
        deep_work_penalty = 20

    # Density penalty: more than 3 meetings already
    density_penalty = 10 if len(existing_meetings) >= 3 else 0

    # Urgency bonus: P1/P2 get a negative adjustment (lower = better)
    urgency_bonus = {1: 20, 2: 15, 3: 5, 4: 0}.get(priority, 0)

    slot_score = cognitive_delta + back_to_back_penalty + deep_work_penalty + density_penalty - urgency_bonus

    # Compute buffer info for explanation
    buffers_before = [
        (candidate_start - m.end).total_seconds() / 60
        for m in existing_meetings
        if m.end <= candidate_start
    ]
    buffers_after = [
        (m.start - candidate_end).total_seconds() / 60
        for m in existing_meetings
        if m.start >= candidate_end
    ]

    return {
        "slot_score": round(slot_score, 2),
        "explanation": {
            "buffer_before_minutes": int(min(buffers_before)) if buffers_before else None,
            "buffer_after_minutes": int(min(buffers_after)) if buffers_after else None,
            "deep_work_safe": deep_work_penalty == 0,
            "daily_cognitive_impact": f"+{round(cognitive_delta, 1)} points",
            "back_to_back": back_to_back_penalty > 0,
            "burnout_risk_after": compute_burnout_risk([new_score_data["score"]]),
            "urgency_respected": urgency_bonus > 0,
        },
    }
