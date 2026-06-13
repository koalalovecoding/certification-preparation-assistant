"""
Engagement Agent

Responsibilities:
- Ask the learner if they have completed the current study module
- Update hours_studied in learner_performance.json upon confirmation
- Calculate and display study progress percentage
- Generate personalised reminders adapted to workload and urgency
- Check for certification renewal or retirement warnings on held certifications

IQ Layer: Work IQ (simulated via work_activity_signals.json)

Python layer: progress calculation, load classification, urgency detection, file update
LLM layer: personalised reminder message generation

Public API:
  check_in(payload)                        → module completion check + reminder
  update_progress(learner_id, hours)       → updates learner_performance.json (no LLM)
"""

import json
import os
from datetime import datetime, date
from pathlib import Path

from azure.ai.projects import AIProjectClient
from azure.ai.agents.models import MessageRole
from azure.identity import DefaultAzureCredential
from dotenv import load_dotenv

load_dotenv()

DATA_DIR = Path(__file__).parent.parent / "data"

SYSTEM_PROMPT = """
You are the Engagement Agent for an enterprise certification preparation system.

Your job is to keep learners motivated and on track. You will receive:
- The learner's current study module and weekly plan
- Their work load level (high / medium / low)
- Their preferred learning slot
- Their current progress percentage
- Days until exam (if set)
- Any certification renewal or retirement warnings

Adapt your message based on load level and urgency:
- High load learner: short, supportive, no pressure. Acknowledge their busy schedule.
- Medium load learner: balanced, encouraging, specific suggestions.
- Low load learner: detailed, challenging, set clear targets.

Urgency levels:
- Critical (< 14 days to exam, progress < 70%): direct warning, clear call to action
- At risk (< 30 days to exam, progress < 50%): gentle urgency, concrete next steps
- On track: positive reinforcement, next module preview

Always include:
1. Progress bar (text-based): e.g. [████████░░] 80% complete
2. Current module focus and suggested study time based on preferred_learning_slot
3. Next module preview
4. Renewal/retirement warning if applicable

Keep the message concise. High load learners get 3-4 sentences max.

Respond in this JSON format:
{
  "status": "complete",
  "progress_bar": "[████████░░] 80% complete",
  "progress_percentage": <float>,
  "load_level": "<high|medium|low>",
  "urgency_level": "<critical|at_risk|on_track>",
  "message": "<personalised reminder message>",
  "suggested_study_time": "<e.g. Tomorrow morning, 9-10 AM>",
  "next_module": "<name of next module in plan>",
  "renewal_warnings": ["<warning if any>"]
}
"""


def _load_json(filename: str):
    with open(DATA_DIR / filename, "r") as f:
        return json.load(f)


def _save_json(filename: str, data):
    with open(DATA_DIR / filename, "w") as f:
        json.dump(data, f, indent=2)


def _create_client() -> AIProjectClient:
    return AIProjectClient(
        endpoint=os.environ["AZURE_AI_PROJECT_ENDPOINT"],
        credential=DefaultAzureCredential(),
    )


def _get_work_signals(learner_id: str) -> dict | None:
    signals = _load_json("work_activity_signals.json")
    return next((s for s in signals if s["learner_id"] == learner_id), None)


def _get_learner(learner_id: str) -> dict | None:
    learners = _load_json("learner_performance.json")
    return next((l for l in learners if l["learner_id"] == learner_id), None)


def _classify_load(work_signals: dict | None) -> str:
    """
    Classify workload based on meeting_hours_per_week.
    High: >= 20 hrs meetings
    Medium: 12-19 hrs
    Low: < 12 hrs
    """
    if not work_signals:
        return "medium"
    meeting_hours = work_signals.get("meeting_hours_per_week", 15)
    if meeting_hours >= 20:
        return "high"
    elif meeting_hours >= 12:
        return "medium"
    return "low"


def _calculate_progress(hours_studied: float | None, adjusted_study_hours: int) -> float:
    """Returns progress percentage (0-100)."""
    if not hours_studied or adjusted_study_hours == 0:
        return 0.0
    return round(min(hours_studied / adjusted_study_hours * 100, 100), 1)


def _build_progress_bar(percentage: float) -> str:
    """Builds a text-based progress bar."""
    filled = int(percentage / 10)
    empty = 10 - filled
    bar = "█" * filled + "░" * empty
    return f"[{bar}] {percentage}% complete"


def _classify_urgency(days_until_exam: int | None, progress: float) -> str:
    if not days_until_exam:
        return "on_track"
    if days_until_exam < 14 and progress < 70:
        return "critical"
    if days_until_exam < 30 and progress < 50:
        return "at_risk"
    return "on_track"


def _check_renewal_warnings(certifications_held: list[str]) -> list[str]:
    """
    Check held certifications for retirement or upcoming expiry warnings.
    Excludes Fundamentals certifications (AZ-900, SC-900) which do not expire.
    """
    certifications = _load_json("certifications.json")
    warnings = []
    cert_map = {c["certification_code"]: c for c in certifications}

    for code in certifications_held:
        cert = cert_map.get(code)
        if not cert:
            continue
        # Skip Fundamentals
        if cert.get("certification_level") == "Fundamentals":
            continue
        retirement = cert.get("retirement_status", {})
        if not retirement.get("is_retired") and retirement.get("retirement_date"):
            warnings.append(
                f"{code} is retiring on {retirement['retirement_date']}. "
                f"Renewal will not be possible after this date."
            )

    return warnings


def _get_current_and_next_module(
    weekly_plan: list[dict],
    hours_studied: float
) -> tuple[str, str]:
    """
    Determines current module based on hours studied so far,
    and returns the next module in the plan.
    """
    cumulative = 0.0
    current = weekly_plan[0]["focus"] if weekly_plan else "Unknown"
    next_module = "None — plan complete"

    for i, entry in enumerate(weekly_plan):
        cumulative += entry["hours"]
        if hours_studied < cumulative:
            current = entry["focus"]
            next_module = weekly_plan[i + 1]["focus"] if i + 1 < len(weekly_plan) else "None — plan complete"
            break

    return current, next_module


def update_progress(learner_id: str, hours_to_add: float) -> dict:
    """
    Adds hours_to_add to the learner's hours_studied in learner_performance.json.
    Pure Python — no LLM call.

    Returns updated learner record.
    """
    learners = _load_json("learner_performance.json")
    for learner in learners:
        if learner["learner_id"] == learner_id:
            current = learner.get("hours_studied") or 0.0
            learner["hours_studied"] = round(current + hours_to_add, 1)
            _save_json("learner_performance.json", learners)
            return learner
    raise ValueError(f"Learner {learner_id} not found")


def check_in(payload: dict) -> dict:
    """
    Main entry point for the Engagement Agent.

    Args:
        payload: must include:
            - learner_id
            - adjusted_study_hours (from Study Plan Generator)
            - weekly_plan (list of weekly entries from Study Plan Generator)
            - module_completed (bool): whether learner confirmed completing current module
            - module_hours (float): hours for the completed module (to update progress)

    Returns:
        raw         — agent's raw response
        parsed      — parsed JSON if valid, else None
        progress    — calculated progress data
    """
    learner_id = payload.get("learner_id")
    adjusted_study_hours = payload.get("adjusted_study_hours", 20)
    weekly_plan = payload.get("weekly_plan", [])
    module_completed = payload.get("module_completed", False)
    module_hours = payload.get("module_hours", 0.0)

    # Update progress if module was completed
    if learner_id and module_hours > 0:
        update_progress(learner_id, module_hours)

    # Load fresh learner data after potential update
    learner = _get_learner(learner_id) if learner_id else None
    work_signals = _get_work_signals(learner_id) if learner_id else None

    hours_studied = (learner.get("hours_studied") or 0.0) if learner else 0.0
    days_until_exam = learner.get("days_until_exam") if learner else None
    certifications_held = learner.get("certifications_held", []) if learner else []

    # Calculate progress
    progress = _calculate_progress(hours_studied, adjusted_study_hours)
    progress_bar = _build_progress_bar(progress)
    load_level = _classify_load(work_signals)
    urgency = _classify_urgency(days_until_exam, progress)
    renewal_warnings = _check_renewal_warnings(certifications_held)
    current_module, next_module = _get_current_and_next_module(weekly_plan, hours_studied)
    preferred_slot = work_signals.get("preferred_learning_slot", "Morning") if work_signals else "Morning"

    context = {
        "learner_id": learner_id,
        "current_module": current_module,
        "next_module": next_module,
        "progress_percentage": progress,
        "progress_bar": progress_bar,
        "hours_studied": hours_studied,
        "adjusted_study_hours": adjusted_study_hours,
        "load_level": load_level,
        "urgency_level": urgency,
        "days_until_exam": days_until_exam,
        "preferred_learning_slot": preferred_slot,
        "module_just_completed": module_completed,
        "renewal_warnings": renewal_warnings,
    }

    client = _create_client()
    model = os.environ.get("AZURE_AI_MODEL_DEPLOYMENT", "gpt-4o")

    agent = client.agents.create_agent(
        model=model,
        name="engagement_agent",
        instructions=SYSTEM_PROMPT,
    )

    thread = client.agents.threads.create()

    client.agents.messages.create(
        thread_id=thread.id,
        role=MessageRole.USER,
        content=f"""
Generate a personalised engagement message for this learner.

Context:
{json.dumps(context, indent=2)}
""",
    )

    run = client.agents.runs.create_and_process(
        thread_id=thread.id,
        agent_id=agent.id,
    )

    if run.status == "failed":
        client.agents.delete_agent(agent.id)
        raise RuntimeError(f"Engagement Agent failed: {run.last_error}")

    messages_list = list(client.agents.messages.list(thread_id=thread.id))
    response_text = next(
        m.content[0].text.value for m in messages_list if m.role == "assistant"
    )

    client.agents.delete_agent(agent.id)

    parsed = None
    try:
        cleaned = (
            response_text.strip()
            .removeprefix("```json")
            .removeprefix("```")
            .removesuffix("```")
            .strip()
        )
        parsed = json.loads(cleaned)
    except json.JSONDecodeError:
        pass

    return {
        "raw": response_text,
        "parsed": parsed,
        "progress": {
            "percentage": progress,
            "bar": progress_bar,
            "hours_studied": hours_studied,
            "adjusted_study_hours": adjusted_study_hours,
        },
    }