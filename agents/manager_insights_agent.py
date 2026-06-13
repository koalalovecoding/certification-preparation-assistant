"""
Manager Insights Agent

Responsibilities:
1. get_scope(payload)    — Returns approved certifications for a given role/team.
                           Used by Learning Path Curator before searching.
                           Pure Python: no LLM call.

2. get_insights(payload) — Returns team-level progress insights for managers.
                           Python calculates stats; LLM generates natural language summary.
"""

import json
import os
from pathlib import Path

from azure.ai.projects import AIProjectClient
from azure.ai.agents.models import MessageRole
from azure.identity import DefaultAzureCredential
from dotenv import load_dotenv

load_dotenv()

DATA_DIR = Path(__file__).parent.parent / "data"

INSIGHTS_SYSTEM_PROMPT = """
You are the Manager Insights Agent for an enterprise certification preparation system.

You will receive team-level certification data including:
- Team name and members
- Each learner's progress percentage, hours studied, and module scores
- Risk flags for learners who may not be ready in time
- Weakest modules across the team

Your job is to:
1. Summarise the team's overall certification readiness
2. Highlight at-risk learners (without exposing sensitive personal details beyond what is necessary)
3. Identify the most common weak areas across the team
4. Provide actionable recommendations for the manager

Keep the tone professional and constructive. Do not expose individual sensitive data beyond role and progress.

Respond in this JSON format:
{
  "status": "complete",
  "team_name": "<team name>",
  "team_summary": "<2-3 sentence overview of team readiness>",
  "overall_progress_avg": <float>,
  "at_risk_count": <int>,
  "at_risk_learners": [
    {
      "learner_id": "<id>",
      "role": "<role>",
      "certification": "<cert>",
      "days_until_exam": <int or null>,
      "progress_percentage": <float>,
      "risk_reason": "<brief reason>"
    }
  ],
  "common_weak_modules": ["<module1>", "<module2>"],
  "recommendations": ["<recommendation1>", "<recommendation2>"],
  "retirement_warnings": ["<warning if any>"]
}
"""


def _load_json(filename: str) -> list | dict:
    with open(DATA_DIR / filename, "r") as f:
        return json.load(f)


def _create_client() -> AIProjectClient:
    return AIProjectClient(
        endpoint=os.environ["AZURE_AI_PROJECT_ENDPOINT"],
        credential=DefaultAzureCredential(),
    )


def _check_retirement_warnings(cert_codes: list[str], certifications: list[dict]) -> list[dict]:
    warnings = []
    cert_map = {c["certification_code"]: c for c in certifications}
    for code in cert_codes:
        cert = cert_map.get(code)
        if not cert:
            continue
        retirement = cert.get("retirement_status", {})
        if not retirement.get("is_retired") and retirement.get("retirement_date"):
            warnings.append({
                "certification": code,
                "message": f"{code} is retiring on {retirement['retirement_date']}. "
                           f"Consider alternative certifications before registering."
            })
    return warnings


def _calculate_progress(hours_studied: float | None, adjusted_study_hours: int | None) -> float:
    if not hours_studied or not adjusted_study_hours or adjusted_study_hours == 0:
        return 0.0
    return round(min(hours_studied / adjusted_study_hours * 100, 100), 1)


def _is_at_risk(learner: dict, progress: float) -> bool:
    days_until_exam = learner.get("days_until_exam")
    if not days_until_exam:
        return False
    if days_until_exam < 14 and progress < 70:
        return True
    if days_until_exam < 30 and progress < 50:
        return True
    return False


def _calculate_team_stats(team_id: str) -> dict:
    """
    Pure Python: calculates team-level stats from learner_performance.json.
    """
    learners = _load_json("learner_performance.json")
    certifications = _load_json("certifications.json")
    manager_config = _load_json("manager_team_config.json")

    # Get team info
    team = next((t for t in manager_config if t["team_id"] == team_id), None)
    team_name = team["team_name"] if team else team_id
    approved_certs = team["approved_certifications"] if team else []

    # Filter learners in this team
    team_learners = [l for l in learners if l.get("team_id") == team_id]

    if not team_learners:
        return {
            "team_name": team_name,
            "team_learners": [],
            "overall_progress_avg": 0.0,
            "at_risk_learners": [],
            "common_weak_modules": [],
            "retirement_warnings": [],
        }

    # Calculate per-learner stats
    learner_stats = []
    all_weak_modules = []
    at_risk = []
    total_progress = 0.0

    for learner in team_learners:
        cert_code = learner.get("certification")
        cert = next((c for c in certifications if c["certification_code"] == cert_code), None)
        adjusted_hours = learner.get("adjusted_study_hours") or (cert.get("recommended_study_hours", 20) if cert else 20)
        hours_studied = learner.get("hours_studied") or 0.0
        progress = _calculate_progress(hours_studied, adjusted_hours)
        total_progress += progress

        # Find weakest module
        module_scores = learner.get("skill_module_scores") or {}
        weakest = min(module_scores, key=module_scores.get) if module_scores else None
        if weakest:
            all_weak_modules.append(weakest)

        stat = {
            "learner_id": learner["learner_id"],
            "role": learner.get("role"),
            "certification": cert_code,
            "hours_studied": hours_studied,
            "adjusted_study_hours": adjusted_hours,
            "progress_percentage": progress,
            "days_until_exam": learner.get("days_until_exam"),
            "weakest_module": weakest,
        }
        learner_stats.append(stat)

        if _is_at_risk(learner, progress):
            days = learner.get("days_until_exam")
            at_risk.append({
                "learner_id": learner["learner_id"],
                "role": learner.get("role"),
                "certification": cert_code,
                "days_until_exam": days,
                "progress_percentage": progress,
                "risk_reason": (
                    f"{days} days until exam but only {progress}% complete"
                    if days else "Low progress"
                ),
            })

    # Find most common weak modules
    from collections import Counter
    module_counts = Counter(all_weak_modules)
    common_weak = [m for m, _ in module_counts.most_common(3)]

    overall_avg = round(total_progress / len(team_learners), 1) if team_learners else 0.0

    # Retirement warnings for approved certs
    retirement_warnings = [
        w["message"] for w in _check_retirement_warnings(approved_certs, certifications)
    ]

    return {
        "team_name": team_name,
        "team_learners": learner_stats,
        "overall_progress_avg": overall_avg,
        "at_risk_learners": at_risk,
        "common_weak_modules": common_weak,
        "retirement_warnings": retirement_warnings,
    }


def get_scope(payload: dict) -> dict:
    """
    Returns the approved certification scope for the user's role/team.

    Lookup order:
    1. Match by team_id if provided in payload
    2. Match by role against primary_roles in manager_team_config.json
    3. Fallback to manager_role_config.json
    4. Fallback to certifications.json target_roles
    """
    role = payload.get("role")
    team_id = payload.get("team_id")

    manager_config = _load_json("manager_team_config.json")
    certifications = _load_json("certifications.json")

    matched_team = None

    # Step 1: match by team_id
    if team_id:
        matched_team = next(
            (t for t in manager_config if t["team_id"] == team_id), None
        )

    # Step 2: match by role
    if not matched_team and role:
        matched_team = next(
            (t for t in manager_config if role in t.get("primary_roles", [])), None
        )

    # Step 3: fallback to manager_role_config.json
    if not matched_team:
        role_config = _load_json("manager_role_config.json")
        matched_role = next(
            (r for r in role_config if r["role"] == role), None
        ) if role else None

        if matched_role:
            approved = matched_role["recommended_certifications"]
            warnings = _check_retirement_warnings(approved, certifications)
            return {
                "team_id": None,
                "team_name": None,
                "approved_certifications": approved,
                "warnings": warnings,
                "fallback_used": "role"
            }

        # Step 4: fallback to certifications.json target_roles
        fallback_certs = [
            c["certification_code"]
            for c in certifications
            if role in c.get("target_roles", [])
        ] if role else [c["certification_code"] for c in certifications]

        warnings = _check_retirement_warnings(fallback_certs, certifications)
        return {
            "team_id": None,
            "team_name": None,
            "approved_certifications": fallback_certs,
            "warnings": warnings,
            "fallback_used": "target_roles"
        }

    approved = matched_team["approved_certifications"]
    warnings = _check_retirement_warnings(approved, certifications)
    return {
        "team_id": matched_team["team_id"],
        "team_name": matched_team["team_name"],
        "approved_certifications": approved,
        "warnings": warnings,
        "fallback_used": False
    }


def get_insights(payload: dict) -> dict:
    """
    Returns team-level certification progress insights for managers.
    Python calculates stats; LLM generates natural language summary.

    Args:
        payload: must include 'team_id'

    Returns:
        raw, parsed, stats
    """
    team_id = payload.get("team_id")
    if not team_id:
        return {
            "status": "error",
            "message": "team_id is required for get_insights"
        }

    # Python layer: calculate all stats
    stats = _calculate_team_stats(team_id)

    client = _create_client()
    model = os.environ.get("AZURE_AI_MODEL_DEPLOYMENT", "gpt-4o")

    agent = client.agents.create_agent(
        model=model,
        name="manager_insights_agent",
        instructions=INSIGHTS_SYSTEM_PROMPT,
    )

    thread = client.agents.threads.create()

    client.agents.messages.create(
        thread_id=thread.id,
        role=MessageRole.USER,
        content=f"""
Generate team insights for the manager based on the following data.

Team Stats:
{json.dumps(stats, indent=2)}
""",
    )

    run = client.agents.runs.create_and_process(
        thread_id=thread.id,
        agent_id=agent.id,
    )

    if run.status == "failed":
        client.agents.delete_agent(agent.id)
        raise RuntimeError(f"Manager Insights Agent failed: {run.last_error}")

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
        "stats": stats,
    }