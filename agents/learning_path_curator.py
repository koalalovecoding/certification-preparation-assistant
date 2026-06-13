"""
Learning Path Curator Agent

Two-phase flow:
  Phase 1 — Background Familiarity Check (single-turn, scoring table 0-5)
  Phase 2 — Learning path output with citations and study hours multiplier

Public API:
  start(payload)                                  → scoring table presented to learner
  continue_session(thread_id, agent_id, response) → final learning path output
"""

import json
import os
from pathlib import Path

from azure.ai.projects import AIProjectClient
from azure.ai.agents.models import MessageRole, AzureAISearchTool, AzureAISearchQueryType
from azure.identity import DefaultAzureCredential
from dotenv import load_dotenv

from agents.manager_insights_agent import get_scope

load_dotenv()

DATA_DIR = Path(__file__).parent.parent / "data"

# Known source files in the Foundry IQ knowledge base
KNOWN_SOURCES = {
    "az-104.md", "az-204.md", "az-305.md", "az-400.md",
    "az-500.md", "az-900.md", "dp-300.md", "dp-700.md",
    "sc-900.md", "azure-solutions-architect-expert.md",
    "devops-engineer-expert.md"
}

SYSTEM_PROMPT = """
You are the Learning Path Curator for an enterprise certification preparation system.

Your job runs in two phases.

## Phase 1: Background Familiarity Check
You will be given a list of recommended background topics for a certification.
Present ALL topics to the learner at once as a scoring table.

Format the table like this:
| # | Topic | Your Score (0-5) |
|---|-------|-----------------|
| 1 | <topic> | ___ |
| 2 | <topic> | ___ |
...

Scoring guide:
  0 — No experience
  1 — Heard of it
  2 — Basic understanding
  3 — Somewhat familiar
  4 — Comfortable / use occasionally
  5 — Confident / use regularly

Ask the learner to fill in a score for each topic and reply with their scores.

During Phase 1, respond in this JSON format:
{
  "status": "asking",
  "table": "<the markdown table as a string>",
  "topics": ["<topic1>", "<topic2>", ...]
}

## Phase 2: Learning Path Output
Once the learner submits their scores, generate a personalized learning path.

Rules:
- Only recommend certifications from the approved_certifications list provided
- Do not recommend certifications the learner already holds
- You MUST use the azure_ai_search tool to retrieve learning resources.
  Do NOT generate resources from memory or make up file names.
  Only include resources that are actually returned by the search tool.
- The source field must contain ONLY the exact file name (e.g. az-204.md).
  Do not include citation markers like 【】, †source, or any other formatting.
  The file name comes from the search result metadata, not from the content.
- Calculate study_hours_multiplier from the average score across all topics:
    Score 5 → 0.33x
    Score 4 → 0.50x
    Score 3 → 1.00x
    Score 2 → 1.00x
    Score 1 → 1.25x
    Score 0 → 1.50x
  Average the multipliers across all topics.
- adjusted_study_hours = recommended_study_hours × study_hours_multiplier (round up)
- Include a warning if any recommended certification is retiring soon.
- If the search tool returns fewer than 3 results, only include what was actually found.
  Do not add extra resources to fill the list.

During Phase 2, respond in this JSON format:
{
  "status": "complete",
  "recommended_certification": "<certification code>",
  "study_hours_multiplier": <float>,
  "adjusted_study_hours": <int>,
  "learning_resources": [
    {
      "title": "<resource title>",
      "description": "<brief description>",
      "source": "<exact file name returned by azure_ai_search>"
    }
  ],
  "warnings": [],
  "background_summary": {
    "<topic>": <score>
  }
}
"""


def _load_json(filename: str):
    with open(DATA_DIR / filename, "r") as f:
        return json.load(f)


def _get_learner_profile(learner_id: str) -> dict | None:
    learners = _load_json("learner_performance.json")
    return next((l for l in learners if l["learner_id"] == learner_id), None)


def _create_client() -> AIProjectClient:
    return AIProjectClient(
        endpoint=os.environ["AZURE_AI_PROJECT_ENDPOINT"],
        credential=DefaultAzureCredential(),
    )


def _validate_sources(parsed: dict) -> dict:
    """
    Validates that learning_resources sources are from known knowledge base files.
    Marks unverified sources to prevent silent hallucination.
    """
    if not parsed or parsed.get("status") != "complete":
        return parsed
    for resource in parsed.get("learning_resources", []):
        if resource.get("source") not in KNOWN_SOURCES:
            resource["source"] = f"[unverified: {resource.get('source', 'unknown')}]"
    return parsed


def _build_initial_context(payload: dict, scope: dict) -> str:
    """
    Builds the initial message sent to the agent with all relevant context.
    Only passes responsibilities and proficient_in from recommended_background
    to keep the scoring table focused.
    """
    certifications = _load_json("certifications.json")

    certification_code = payload.get("certification")
    role = payload.get("role")
    learner_id = payload.get("learner_id")

    # Get learner profile if available
    learner_profile = _get_learner_profile(learner_id) if learner_id else None
    certifications_held = learner_profile.get("certifications_held", []) if learner_profile else []

    # Determine target certification
    if certification_code:
        target_cert = next(
            (c for c in certifications if c["certification_code"] == certification_code),
            None
        )
    else:
        # Pick first approved cert not already held by learner
        target_code = next(
            (c for c in scope["approved_certifications"] if c not in certifications_held),
            None
        )
        target_cert = next(
            (c for c in certifications if c["certification_code"] == target_code),
            None
        ) if target_code else None

    # Only include responsibilities and proficient_in for the scoring table
    background = {}
    if target_cert and target_cert.get("recommended_background"):
        raw_bg = target_cert["recommended_background"]
        if raw_bg.get("responsibilities"):
            background["responsibilities"] = raw_bg["responsibilities"]
        if raw_bg.get("proficient_in"):
            background["proficient_in"] = raw_bg["proficient_in"]

    context = {
        "role": role,
        "approved_certifications": scope["approved_certifications"],
        "scope_warnings": scope["warnings"],
        "certifications_held": certifications_held,
        "target_certification": {
            "code": target_cert["certification_code"],
            "name": target_cert["certification_name"],
            "recommended_study_hours": target_cert["recommended_study_hours"],
            "recommended_background": background,
        } if target_cert else None,
    }

    return f"""
Please help this learner prepare for their certification.

Context:
{json.dumps(context, indent=2)}

Start Phase 1 now. Present the full background familiarity scoring table.
"""


def start(payload: dict) -> dict:
    """
    Starts a new Learning Path Curator session.
    Creates a persistent thread and returns the scoring table for the learner.

    Returns:
        raw         — agent's raw response text (scoring table)
        thread_id   — persist this to continue the session
        agent_id    — persist this to continue the session
        scope       — approved certification scope used
    """
    scope = get_scope(payload)

    client = _create_client()
    model = os.environ.get("AZURE_AI_MODEL_DEPLOYMENT", "gpt-4o")
    knowledge_base_connection_id = os.environ.get("FOUNDRY_KNOWLEDGE_BASE_CONNECTION_ID")

    # Attach Foundry IQ knowledge base tool if connection ID is configured
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
        name="learning_path_curator",
        instructions=SYSTEM_PROMPT,
        tools=tools,
        tool_resources=tool_resources,
    )

    thread = client.agents.threads.create()

    client.agents.messages.create(
        thread_id=thread.id,
        role=MessageRole.USER,
        content=_build_initial_context(payload, scope),
    )

    run = client.agents.runs.create_and_process(
        thread_id=thread.id,
        agent_id=agent.id,
    )

    if run.status == "failed":
        client.agents.delete_agent(agent.id)
        raise RuntimeError(f"Learning Path Curator failed: {run.last_error}")

    messages_list = list(client.agents.messages.list(thread_id=thread.id))
    response_text = next(
        m.content[0].text.value for m in messages_list if m.role == "assistant"
    )

    return {
        "raw": response_text,
        "thread_id": thread.id,
        "agent_id": agent.id,
        "scope": scope,
    }


def continue_session(thread_id: str, agent_id: str, user_response: str) -> dict:
    """
    Continues the session with the learner's scores.
    Since Phase 1 is now single-turn, this should return status "complete" directly.

    Returns:
        raw     — agent's raw response text
        parsed  — parsed and source-validated JSON if valid, else None
        status  — "asking" or "complete"
    """
    client = _create_client()

    client.agents.messages.create(
        thread_id=thread_id,
        role=MessageRole.USER,
        content=user_response,
    )

    run = client.agents.runs.create_and_process(
        thread_id=thread_id,
        agent_id=agent_id,
    )

    if run.status == "failed":
        raise RuntimeError(f"Learning Path Curator failed: {run.last_error}")

    messages_list = list(client.agents.messages.list(thread_id=thread_id))
    response_text = next(
        m.content[0].text.value for m in messages_list if m.role == "assistant"
    )

    # Parse response and check completion status
    parsed = None
    status = "asking"
    try:
        cleaned = (
            response_text.strip()
            .removeprefix("```json")
            .removeprefix("```")
            .removesuffix("```")
            .strip()
        )
        parsed = json.loads(cleaned)
        status = parsed.get("status", "asking")
    except json.JSONDecodeError:
        pass

    # Validate sources against known knowledge base files
    if parsed:
        parsed = _validate_sources(parsed)

    # Clean up remote agent once session is complete
    if status == "complete":
        client.agents.delete_agent(agent_id)

    return {
        "raw": response_text,
        "parsed": parsed,
        "status": status,
        "thread_id": thread_id,
        "agent_id": agent_id,
    }