"""
Assessment Agent

Responsibilities:
- Generate 5 grounded, cited questions per skill module from Foundry IQ knowledge base
- Adaptive difficulty: correct answer → harder question, wrong answer → easier question
- Evaluate answers and calculate per-module scores
- Write skill_module_scores back to learner_performance.json
- Calculate weighted final score across all modules
- Determine Pass/Fail: final_score >= 80%
- Surface weakest module for Study Plan Generator feedback loop

IQ Layers:
- Foundry IQ: question generation with citations from cert knowledge base
- Fabric IQ (simulated): skill_module weights from certifications.json

Multi-turn design (single-step protocol):
- First message → agent returns first question ("asking")
- Each answer → agent returns evaluation + next question in ONE response ("evaluated")
  * This avoids the agent skipping the evaluated step and jumping to the next question
- After last answer → agent returns final summary ("complete")

Public API:
  start_module(payload)                           → first question
  next_question(thread_id, agent_id, user_answer) → evaluation + next question, or complete
  complete_module(learner_id, module_name, score) → writes skill_module_scores
  calculate_final_score(learner_id, cert_code)    → weighted total, Pass/Fail
"""

import json
import os
import re
from pathlib import Path

from azure.ai.projects import AIProjectClient
from azure.ai.agents.models import MessageRole, AzureAISearchTool, AzureAISearchQueryType
from azure.identity import DefaultAzureCredential
from dotenv import load_dotenv

load_dotenv()

DATA_DIR = Path(__file__).parent.parent / "data"

SYSTEM_PROMPT = """
You are the Assessment Agent for an enterprise certification preparation system.

Your job is to test the learner's knowledge of a specific certification skill module.

## Question Generation Rules
- You MUST use the azure_ai_search tool to retrieve content for questions.
  Do NOT generate questions from memory.
- Generate ONE question at a time in single-choice format (A/B/C/D)
- Each question must cite the source file name only (e.g. az-204.md) — no citation markers like 【】
- Adapt difficulty based on the learner's previous answer:
  * CORRECT → increase difficulty (application/analysis level)
  * WRONG → decrease difficulty (recall/comprehension level)
  * First question → medium difficulty
- Questions must be directly relevant to the module being assessed
- Do not repeat questions already asked in this session
- Do NOT reveal the correct answer inside the question response

## STRICT SINGLE-STEP PROTOCOL
When the learner submits any answer (A, B, C, or D):

CRITICAL: Return ONE JSON response that contains BOTH the evaluation of the current answer
AND the next question (if there are questions remaining). NEVER return evaluation and question
as separate messages. NEVER ask the learner to answer again or reconsider.

If there are more questions remaining, use the "evaluated" status with "next_question" embedded:
{
  "status": "evaluated",
  "question_number": <current question number>,
  "correct": <bool>,
  "correct_answer": "<A|B|C|D>",
  "encouragement": "<warm congratulation if correct (e.g. 'Excellent! You nailed it!') or brief consolation if wrong (e.g. 'No worries, this is a tricky one!')>",
  "explanation": "<one-sentence summary of the key knowledge point>",
  "knowledge_point": "<underlying concept to remember — include for both correct and incorrect>",
  "study_recommendation": "<what to review — only if incorrect, else null>",
  "source": "<source file name only, e.g. az-204.md>",
  "next_question": {
    "question_number": <next question number>,
    "max_questions": <total>,
    "question": "<next question text>",
    "options": {
      "A": "<option A>",
      "B": "<option B>",
      "C": "<option C>",
      "D": "<option D>"
    },
    "source": "<source file name only>",
    "difficulty": "<easy|medium|hard>"
  }
}

If this was the LAST question, still return "evaluated" with next_question: null and
embed the final summary in "complete_summary". This ensures every question gets per-question
feedback before the overall result is shown — consistent with all other questions.
{
  "status": "evaluated",
  "question_number": <current question number>,
  "correct": <bool>,
  "correct_answer": "<A|B|C|D>",
  "encouragement": "<warm congratulation or consolation>",
  "explanation": "<one-sentence knowledge point summary>",
  "knowledge_point": "<underlying concept>",
  "study_recommendation": "<what to review if incorrect, else null>",
  "source": "<source file name only>",
  "next_question": null,
  "complete_summary": {
    "status": "complete",
    "module_name": "<module name>",
    "questions_asked": <int>,
    "correct_answers": <int>,
    "score_percentage": <float>,
    "difficulty_progression": ["<easy|medium|hard>", ...],
    "weakest_topic": "<topic the learner struggled most with, or null>",
    "summary": "<brief, encouraging feedback on overall performance>"
  }
}

## First Question Format
For the very first question only (no evaluation yet):
{
  "status": "asking",
  "question_number": 1,
  "max_questions": <int>,
  "question": "<question text>",
  "options": {
    "A": "<option A>",
    "B": "<option B>",
    "C": "<option C>",
    "D": "<option D>"
  },
  "source": "<source file name only, e.g. az-204.md>",
  "difficulty": "medium"
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


def _get_certification(certification_code: str) -> dict | None:
    certifications = _load_json("certifications.json")
    return next(
        (c for c in certifications if c["certification_code"] == certification_code),
        None
    )


def _get_module_weight(certification_code: str, module_name: str) -> float:
    cert = _get_certification(certification_code)
    if not cert:
        return 10.0
    for module in cert.get("skill_modules", []):
        if module.get("module_name") == module_name:
            try:
                weight_str = module.get("weighting", "10-15%")
                return float(weight_str.split("-")[0].replace("%", ""))
            except (ValueError, IndexError):
                return 10.0
    return 10.0


def _build_start_prompt(payload: dict) -> str:
    certification_code = payload.get("certification_code")
    module_name = payload.get("module_name")
    max_questions = payload.get("max_questions", 5)
    prior_score = payload.get("prior_module_score")

    cert = _get_certification(certification_code)
    module_info = None
    if cert:
        for m in cert.get("skill_modules", []):
            if m.get("module_name") == module_name:
                module_info = m
                break

    context = {
        "certification_code": certification_code,
        "module_name": module_name,
        "max_questions": max_questions,
        "module_knowledge_points": module_info.get("knowledge_points", []) if module_info else [],
        "prior_module_score": prior_score,
        "instruction": (
            f"Start the assessment for the '{module_name}' module. "
            f"Generate question 1 of {max_questions}. "
            f"Use the azure_ai_search tool to retrieve relevant content first. "
            f"Start at medium difficulty. "
            f"Do NOT include the correct answer in your response."
            + (f" Note: learner previously scored {prior_score}% on this module — "
               f"adjust starting difficulty accordingly." if prior_score else "")
        )
    }

    return f"""
Begin the assessment for this module.

Context:
{json.dumps(context, indent=2)}
"""


def _clean_source(source: str) -> str:
    """
    Removes citation markers (e.g. 【3:1†source】) and noise from source field.
    Keeps only the file name portion.
    """
    cleaned = re.sub(r'【[^】]*】', '', source)
    cleaned = re.sub(r'\(Synthetic\)', '', cleaned)
    return cleaned.strip()


def _clean_sources_recursive(obj: dict) -> dict:
    """
    Recursively cleans source fields in a parsed response dict.
    Handles nested structures like next_question.
    """
    if isinstance(obj, dict):
        for key, value in obj.items():
            if key == "source" and isinstance(value, str):
                obj[key] = _clean_source(value)
            elif isinstance(value, dict):
                _clean_sources_recursive(value)
    return obj


def _parse_response(response_text: str) -> dict | None:
    """
    Parses JSON from agent response, stripping markdown fences if present.
    Also cleans citation markers from all source fields (including nested next_question).
    """
    try:
        cleaned = (
            response_text.strip()
            .removeprefix("```json")
            .removeprefix("```")
            .removesuffix("```")
            .strip()
        )
        parsed = json.loads(cleaned)
        _clean_sources_recursive(parsed)
        return parsed
    except json.JSONDecodeError:
        return None


def start_module(payload: dict) -> dict:
    """
    Starts a module assessment session.

    Args:
        payload:
            - certification_code: e.g. "AZ-204"
            - module_name: e.g. "Implement Azure security"
            - learner_id: optional
            - max_questions: 3-5 (default 5)
            - prior_module_score: float or None

    Returns:
        raw, parsed, status, thread_id, agent_id
    """
    client = _create_client()
    model = os.environ.get("AZURE_AI_MODEL_DEPLOYMENT", "gpt-4o")
    knowledge_base_connection_id = os.environ.get("FOUNDRY_KNOWLEDGE_BASE_CONNECTION_ID")

    tools = []
    tool_resources = None
    if knowledge_base_connection_id:
        search_tool = AzureAISearchTool(
            index_connection_id=knowledge_base_connection_id,
            index_name="cert-knowledge-base-index",
            query_type=AzureAISearchQueryType.SIMPLE,
        )
        tools = search_tool.definitions
        tool_resources = search_tool.resources

    agent = client.agents.create_agent(
        model=model,
        name="assessment_agent",
        instructions=SYSTEM_PROMPT,
        tools=tools,
        tool_resources=tool_resources,
    )

    thread = client.agents.threads.create()

    client.agents.messages.create(
        thread_id=thread.id,
        role=MessageRole.USER,
        content=_build_start_prompt(payload),
    )

    run = client.agents.runs.create_and_process(
        thread_id=thread.id,
        agent_id=agent.id,
    )

    if run.status == "failed":
        client.agents.delete_agent(agent.id)
        raise RuntimeError(f"Assessment Agent failed: {run.last_error}")

    messages_list = list(client.agents.messages.list(thread_id=thread.id))
    response_text = next(
        m.content[0].text.value for m in messages_list if m.role == "assistant"
    )

    parsed = _parse_response(response_text)

    return {
        "raw": response_text,
        "parsed": parsed,
        "status": parsed.get("status", "asking") if parsed else "asking",
        "thread_id": thread.id,
        "agent_id": agent.id,
    }


def next_question(thread_id: str, agent_id: str, user_answer: str) -> dict:
    """
    Submits the learner's answer.
    Returns evaluation + next question ("evaluated") or final summary ("complete").
    The "evaluated" response contains next_question embedded — no separate "next" call needed.
    """
    client = _create_client()

    client.agents.messages.create(
        thread_id=thread_id,
        role=MessageRole.USER,
        content=user_answer,
    )

    run = client.agents.runs.create_and_process(
        thread_id=thread_id,
        agent_id=agent_id,
    )

    if run.status == "failed":
        raise RuntimeError(f"Assessment Agent failed: {run.last_error}")

    messages_list = list(client.agents.messages.list(thread_id=thread_id))
    response_text = next(
        m.content[0].text.value for m in messages_list if m.role == "assistant"
    )

    parsed = _parse_response(response_text)
    status = parsed.get("status", "asking") if parsed else "asking"

    if status == "complete":
        client.agents.delete_agent(agent_id)

    return {
        "raw": response_text,
        "parsed": parsed,
        "status": status,
        "thread_id": thread_id,
        "agent_id": agent_id,
    }


def complete_module(learner_id: str, module_name: str, score_percentage: float) -> dict:
    """
    Appends the module score to learner_performance.json.
    Scores are stored as a list to support multiple attempts:
      "skill_module_scores": { "Module A": [72.0, 85.0] }
    Old single-float values are migrated to a list on first write.
    Pure Python — no LLM call.
    """
    learners = _load_json("learner_performance.json")
    for learner in learners:
        if learner["learner_id"] == learner_id:
            if learner.get("skill_module_scores") is None:
                learner["skill_module_scores"] = {}
            existing = learner["skill_module_scores"].get(module_name)
            if isinstance(existing, list):
                # Normal case: append new score to existing list
                existing.append(round(score_percentage, 1))
            elif existing is not None:
                # Migrate legacy single-float to list
                learner["skill_module_scores"][module_name] = [existing, round(score_percentage, 1)]
            else:
                # First attempt for this module
                learner["skill_module_scores"][module_name] = [round(score_percentage, 1)]
            _save_json("learner_performance.json", learners)
            return learner
    raise ValueError(f"Learner {learner_id} not found")


def calculate_final_score(learner_id: str, certification_code: str) -> dict:
    """
    Calculates the weighted final score across all assessed modules.
    Pass threshold: final_score >= 80.0
    Pure Python — no LLM call.
    """
    learners = _load_json("learner_performance.json")
    learner = next((l for l in learners if l["learner_id"] == learner_id), None)
    if not learner:
        raise ValueError(f"Learner {learner_id} not found")

    skill_module_scores = learner.get("skill_module_scores") or {}
    cert = _get_certification(certification_code)

    if not cert or not skill_module_scores:
        return {
            "final_score": 0.0,
            "passed": False,
            "weakest_module": None,
            "module_scores": skill_module_scores,
        }

    # Helper: extract latest score from either list [72.0, 85.0] or legacy float 85.0
    def _latest(v):
        return v[-1] if isinstance(v, list) else (v or 0.0)

    # Calculate weighted average using the latest attempt for each module
    total_weight = 0.0
    weighted_sum = 0.0
    for module in cert.get("skill_modules", []):
        module_name = module.get("module_name")
        if module_name in skill_module_scores:
            weight = _get_module_weight(certification_code, module_name)
            weighted_sum += _latest(skill_module_scores[module_name]) * weight
            total_weight += weight

    final_score = round(weighted_sum / total_weight, 1) if total_weight > 0 else 0.0
    passed = final_score >= 80.0

    # Weakest module: compare by latest attempt score
    weakest_module = (
        min(skill_module_scores, key=lambda m: _latest(skill_module_scores[m]))
        if skill_module_scores else None
    )

    # Update learner record
    for l in learners:
        if l["learner_id"] == learner_id:
            l["practice_score_avg"] = final_score
            l["exam_outcome"] = "Pass" if passed else "Fail"
            break
    _save_json("learner_performance.json", learners)

    return {
        "final_score": final_score,
        "pass_threshold": 80.0,
        "passed": passed,
        "weakest_module": weakest_module,
        "module_scores": skill_module_scores,
    }