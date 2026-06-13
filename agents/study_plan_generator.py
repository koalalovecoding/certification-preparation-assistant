"""
Study Plan Generator Agent

Responsibilities:
- Generate a personalised weekly study plan based on:
    - adjusted_study_hours from Learning Path Curator
    - skill_modules from certifications.json (Fabric IQ simulation)
    - work activity signals from work_activity_signals.json (Work IQ simulation)
    - historical learner performance from learner_performance.json
- Flag risk if projected completion date exceeds exam date
- Support dynamic plan adjustment via persistent thread

Python layer: all calculations (hours allocation, module ordering, risk detection)
LLM layer: natural language description, reasoning explanation, timing personalisation

Public API:
  generate(payload)                            → initial plan + thread_id + agent_id
  adjust(thread_id, agent_id, user_message)    → updated plan
"""

import json
import math
import os
from datetime import datetime, timedelta
from pathlib import Path

from azure.ai.projects import AIProjectClient
from azure.ai.agents.models import MessageRole
from azure.identity import DefaultAzureCredential
from dotenv import load_dotenv

load_dotenv()

DATA_DIR = Path(__file__).parent.parent / "data"

SYSTEM_PROMPT = """
You are the Study Plan Generator for an enterprise certification preparation system.

You will receive a structured study plan calculated by the system, including:
- Target certification and total adjusted study hours
- Weekly study plan with module allocations
- Learner's work schedule and preferred learning slot
- Background familiarity summary showing weak areas
- Historical performance data if available
- Risk flag if the plan cannot be completed before the exam date

Your job is to:
1. Write a clear, motivating natural language description of the weekly plan
2. Explain WHY modules are ordered this way — reference the learner's weak areas from background_summary
3. Give specific timing suggestions based on preferred_learning_slot (e.g. "Block Tuesday and Thursday mornings")
4. If historical data shows a previous failed attempt, acknowledge it and explain how this plan addresses the weak areas
5. If risk_flag is true, issue a clear warning and suggest options (increase weekly hours or reschedule exam)

Keep the tone professional but encouraging. Be specific — reference actual module names and hours.

For the initial plan, respond in this JSON format:
{
  "status": "complete",
  "summary": "<2-3 sentence overview of the plan>",
  "weak_areas_analysis": "<explanation of weak areas and why they are prioritised>",
  "weekly_plan_description": [
    {
      "week": <int>,
      "focus": "<module name>",
      "hours": <float>,
      "description": "<what to study and when>"
    }
  ],
  "timing_recommendation": "<specific scheduling advice based on preferred_learning_slot>",
  "risk_warning": "<warning message if risk_flag is true, else null>",
  "historical_note": "<note about previous attempt if applicable, else null>"
}

For an adjustment request, respond in the same JSON format but with updated values reflecting the change. Always explain what changed and why.
"""


def _load_json(filename: str):
    with open(DATA_DIR / filename, "r") as f:
        return json.load(f)

def _save_adjusted_hours(learner_id: str, adjusted_study_hours: int) -> None:
    path = DATA_DIR / "learner_performance.json"
    with open(path) as f:
        learners = json.load(f)
    for learner in learners:
        if learner["learner_id"] == learner_id:
            learner["adjusted_study_hours"] = adjusted_study_hours
            break
    with open(path, "w") as f:
        json.dump(learners, f, indent=2)




def _create_client() -> AIProjectClient:
    return AIProjectClient(
        endpoint=os.environ["AZURE_AI_PROJECT_ENDPOINT"],
        credential=DefaultAzureCredential(),
    )


def _get_work_signals(learner_id: str) -> dict | None:
    """Load work activity signals for a learner."""
    signals = _load_json("work_activity_signals.json")
    return next((s for s in signals if s["learner_id"] == learner_id), None)


def _get_learner_history(learner_id: str) -> dict | None:
    """Load historical learner performance."""
    learners = _load_json("learner_performance.json")
    return next((l for l in learners if l["learner_id"] == learner_id), None)


def _get_certification(certification_code: str) -> dict | None:
    """Load certification details from certifications.json."""
    certifications = _load_json("certifications.json")
    return next(
        (c for c in certifications if c["certification_code"] == certification_code),
        None
    )


def _calculate_weekly_study_hours(work_signals: dict | None) -> float:
    """
    Calculate available study hours per week from work signals.
    Uses 30% of focus_hours_per_week as a conservative estimate.
    Minimum 1 hour per week.
    """
    if not work_signals:
        return 3.0  # default fallback
    focus_hours = work_signals.get("focus_hours_per_week", 10)
    return max(1.0, round(focus_hours * 0.3, 1))


def _calculate_efficiency_multiplier(history: dict | None) -> float:
    """
    Infer learning efficiency from historical performance.
    - Passed with fewer hours than recommended: 0.85x (efficient learner)
    - Failed previously: 1.2x (needs more time, especially on weak modules)
    - No history: 1.0x
    """
    if not history:
        return 1.0
    outcome = history.get("exam_outcome")
    hours_studied = history.get("hours_studied") or 0
    if outcome == "Pass" and hours_studied > 0:
        return 0.85
    elif outcome == "Fail":
        return 1.2
    return 1.0


def _order_modules_by_priority(
    skill_modules: list[dict],
    background_summary: dict
) -> list[dict]:
    """
    Order skill modules by priority:
    1. Modules with low background scores come first
    2. Within same priority, higher weight modules come first

    Maps background topic scores to modules by checking if any topic
    keyword appears in the module name.
    """
    def module_priority(module: dict) -> tuple:
        module_name = module.get("module_name", "").lower()  # 改 name → module_name
        # Find the lowest background score for topics related to this module
        related_scores = [
            score for topic, score in background_summary.items()
            if any(word in module_name for word in topic.lower().split())
        ]
        min_score = min(related_scores) if related_scores else 3  # default medium
        weight_str = module.get("weighting", "10-15%")  # 改 weight_range → weighting
        # Parse weight as negative for descending sort
        try:
            weight = float(weight_str.split("-")[0].replace("%", ""))
        except (ValueError, IndexError):
            weight = 10.0
        return (min_score, -weight)  # low score first, high weight first

    return sorted(skill_modules, key=module_priority)

def _build_weekly_plan(
    adjusted_study_hours: float,
    weekly_study_hours: float,
    ordered_modules: list[dict]
) -> list[dict]:
    total_weight = sum(
        float(m.get("weighting", "10-15%").split("-")[0].replace("%", ""))
        for m in ordered_modules
    )

    module_hours = []
    for module in ordered_modules:
        try:
            weight = float(
                module.get("weighting", "10-15%").split("-")[0].replace("%", "")
            )
        except (ValueError, IndexError):
            weight = 10.0
        allocated = round((weight / total_weight) * adjusted_study_hours, 1)
        module_hours.append({
            "name": module.get("module_name", "Unknown Module"),
            "allocated_hours": allocated,
            "weighting": module.get("weighting", ""),
        })

    weekly_plan = []
    week = 1
    remaining_week_hours = weekly_study_hours

    for module in module_hours:
        hours_left = module["allocated_hours"]
        while hours_left > 0:
            hours_this_week = min(hours_left, remaining_week_hours)
            if hours_this_week > 0:
                if hours_this_week < 0.5 and weekly_plan:
                    weekly_plan[-1]["hours"] = round(weekly_plan[-1]["hours"] + hours_this_week, 1)
                else:
                    weekly_plan.append({
                        "week": week,
                        "focus": module["name"],
                        "hours": round(hours_this_week, 1),
                    })
            hours_left = round(hours_left - hours_this_week, 1)
            remaining_week_hours = round(remaining_week_hours - hours_this_week, 1)
            if remaining_week_hours <= 0:
                week += 1
                remaining_week_hours = weekly_study_hours

    return weekly_plan




def _check_risk(weekly_plan: list[dict], days_until_exam: int | None) -> bool:
    """
    Returns True if the projected completion date exceeds the exam date.
    """
    if not days_until_exam:
        return False
    total_weeks = max(entry["week"] for entry in weekly_plan) if weekly_plan else 0
    projected_days = total_weeks * 7
    return projected_days > days_until_exam


def _calculate_plan(payload: dict) -> dict:
    """
    Pure Python calculation layer.
    Returns structured plan data to be passed to LLM for description.
    """
    learner_id = payload.get("learner_id")
    certification_code = payload.get("recommended_certification")
    adjusted_study_hours = payload.get("adjusted_study_hours", 20)
    background_summary = payload.get("background_summary", {})

    # Load supporting data
    work_signals = _get_work_signals(learner_id) if learner_id else None
    history = _get_learner_history(learner_id) if learner_id else None
    certification = _get_certification(certification_code) if certification_code else None

    # Calculate weekly available hours
    weekly_study_hours = _calculate_weekly_study_hours(work_signals)

    # Apply efficiency multiplier from historical data
    efficiency_multiplier = _calculate_efficiency_multiplier(history)
    #adjusted_study_hours = math.ceil(adjusted_study_hours * efficiency_multiplier)
    adjusted_study_hours = math.ceil((adjusted_study_hours or 20) * efficiency_multiplier)

    # Get skill modules and order by priority
    skill_modules = []
    if certification:
        skill_modules = certification.get("skill_modules", [])

    ordered_modules = _order_modules_by_priority(skill_modules, background_summary)

    # Build weekly plan
    weekly_plan = _build_weekly_plan(
        adjusted_study_hours, weekly_study_hours, ordered_modules
    )

    # Check risk
    days_until_exam = history.get("days_until_exam") if history else None
    risk_flag = _check_risk(weekly_plan, days_until_exam)

    # Projected completion
    total_weeks = max(entry["week"] for entry in weekly_plan) if weekly_plan else 0
    projected_completion = (
        datetime.today() + timedelta(weeks=total_weeks)
    ).strftime("%Y-%m-%d")

    return {
        "certification": certification_code,
        "adjusted_study_hours": adjusted_study_hours,
        "weekly_study_hours": weekly_study_hours,
        "efficiency_multiplier": efficiency_multiplier,
        "total_weeks": total_weeks,
        "projected_completion_date": projected_completion,
        "risk_flag": risk_flag,
        "days_until_exam": days_until_exam,
        "weekly_plan": weekly_plan,
        "background_summary": background_summary,
        "preferred_learning_slot": (
            work_signals.get("preferred_learning_slot") if work_signals else None
        ),
        "historical_outcome": history.get("exam_outcome") if history else None,
        "historical_hours": history.get("hours_studied") if history else None,
    }


def _build_prompt(calculated_plan: dict) -> str:
    return f"""
Please generate a personalised study plan description based on the following calculated data.

Calculated Plan:
{json.dumps(calculated_plan, indent=2)}

Generate the natural language study plan now.
"""


def generate(payload: dict) -> dict:
    """
    Generates an initial study plan.

    Args:
        payload: must include fields from Learning Path Curator output:
            - recommended_certification
            - adjusted_study_hours
            - background_summary
            - learner_id (optional but recommended)

    Returns:
        raw         — agent's raw response
        parsed      — parsed JSON plan if valid, else None
        plan_data   — Python-calculated plan data
        thread_id   — persist for dynamic adjustment
        agent_id    — persist for dynamic adjustment
    """
    calculated_plan = _calculate_plan(payload)
    if payload.get("learner_id"):
        _save_adjusted_hours(payload["learner_id"], calculated_plan["adjusted_study_hours"])

    client = _create_client()
    model = os.environ.get("AZURE_AI_MODEL_DEPLOYMENT", "gpt-4o")

    agent = client.agents.create_agent(
        model=model,
        name="study_plan_generator",
        instructions=SYSTEM_PROMPT,
    )

    thread = client.agents.threads.create()

    client.agents.messages.create(
        thread_id=thread.id,
        role=MessageRole.USER,
        content=_build_prompt(calculated_plan),
    )

    run = client.agents.runs.create_and_process(
        thread_id=thread.id,
        agent_id=agent.id,
    )

    if run.status == "failed":
        client.agents.delete_agent(agent.id)
        raise RuntimeError(f"Study Plan Generator failed: {run.last_error}")

    messages_list = list(client.agents.messages.list(thread_id=thread.id))
    response_text = next(
        m.content[0].text.value for m in messages_list if m.role == "assistant"
    )

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
        "plan_data": calculated_plan,
        "thread_id": thread.id,
        "agent_id": agent.id,
    }


def adjust(thread_id: str, agent_id: str, user_message: str, payload: dict) -> dict:
    """
    Dynamically adjusts the study plan based on a user message.
    Recalculates the plan with updated context and asks LLM to redescribe.

    Args:
        thread_id:    existing session thread
        agent_id:     existing session agent
        user_message: the adjustment request from the learner
        payload:      updated payload (caller should modify relevant fields)

    Returns:
        raw         — agent's raw response
        parsed      — parsed JSON plan if valid, else None
        plan_data   — recalculated plan data
    """
    # Recalculate plan with updated payload
    calculated_plan = _calculate_plan(payload)

    client = _create_client()

    # Exclude weekly_plan from adjustment context — sending the original week structure
    # causes the LLM to reset to week 1 instead of building on its previous response.
    adjustment_context = {k: v for k, v in calculated_plan.items() if k != "weekly_plan"}

    adjustment_prompt = f"""
The learner has requested an adjustment: "{user_message}"

Rules for applying adjustments:
- If the learner cannot study in a specific week, shift that week's content to the next available week. Never condense content into an earlier week.
- If the learner wants to reduce daily/weekly study hours, spread the same total content over more weeks.
- If the learner wants to increase daily/weekly study hours, compress the same total content into fewer weeks.
- If the learner mentions a daily change (e.g. "half every day", "double every day", "1 hour per day"), convert to weekly: assume 5 study days per week. Halving daily = halving weekly load (more weeks). Doubling daily = doubling weekly load (fewer weeks). Apply proportionally.
- Always preserve the total study hours — do not drop content.
- Assign week numbers starting from 1. Skip unavailable weeks in the numbering (e.g. week 2 unavailable → use weeks 1, 3, 4, ...).

Updated context (use your previous plan as the current state, apply the change on top of it):
{json.dumps(adjustment_context, indent=2)}

Please update the study plan description to reflect this change.
Explain clearly what changed and why.
"""

    client.agents.messages.create(
        thread_id=thread_id,
        role=MessageRole.USER,
        content=adjustment_prompt,
    )

    run = client.agents.runs.create_and_process(
        thread_id=thread_id,
        agent_id=agent_id,
    )

    if run.status == "failed":
        raise RuntimeError(f"Study Plan Generator adjustment failed: {run.last_error}")

    messages_list = list(client.agents.messages.list(thread_id=thread_id))
    response_text = next(
        m.content[0].text.value for m in messages_list if m.role == "assistant"
    )

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
        "plan_data": calculated_plan,
        "thread_id": thread_id,
        "agent_id": agent_id,
    }