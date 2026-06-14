"""
Dispatcher Agent — Hub of the multi-agent system.

Receives user input, determines intent, and routes to the appropriate sub-agent.
"""

import os

from azure.ai.projects import AIProjectClient
from azure.ai.agents.models import MessageRole
from azure.identity import DefaultAzureCredential

from dotenv import load_dotenv

load_dotenv()

SYSTEM_PROMPT = """
You are the Dispatcher for an enterprise certification preparation system.

Your job is to:
1. Understand what the user needs (learning path, study plan, assessment, or manager insights).
2. Route the request to the correct specialist agent.
3. Return a clear, structured response.

Available agents:
- learning_path_curator: Recommends learning resources for a certification goal.
- study_plan_generator: Builds a study schedule based on workload and learning path.
- engagement_agent: Sends reminders adapted to the learner's work rhythm.
- assessment_agent: Evaluates readiness with grounded practice questions.
- manager_insights_agent: Summarises team-level certification progress.

Known certifications (map names OR codes to the code):
- AZ-900: Azure Fundamentals
- AZ-104: Azure Administrator Associate
- AZ-204: Azure Developer Associate
- AZ-305: Azure Solutions Architect Expert
- AZ-400: DevOps Engineer Expert
- AZ-500: Azure Security Engineer Associate
- SC-900: Security, Compliance, and Identity Fundamentals
- DP-300: Azure Database Administrator Associate
- DP-700: Microsoft Fabric Data Engineer Associate
- DP-900: Azure Data Fundamentals
- SC-200: Microsoft Security Operations Analyst

Respond in this JSON format:
{
  "route": "<agent_name>",
  "reason": "<one sentence explaining why>",
  "payload": {
    "user_input": "<original user message>",
    "extracted_intent": "<what the user wants>",
    "certification": "<certification code if the user mentioned a cert name or code, else null>",
    "role": "<job role if mentioned, else null>",
    "team_id": "<team ID if mentioned e.g. TEAM-A, TEAM-B, else null>"
  }
}
"""


def create_client() -> AIProjectClient:
    endpoint = os.environ["AZURE_AI_PROJECT_ENDPOINT"]
    return AIProjectClient(
        endpoint=endpoint,
        credential=DefaultAzureCredential(),
    )


def dispatch(user_input: str) -> dict:
    """
    Takes raw user input, returns a routing decision with extracted payload.
    """
    client = create_client()
    model = os.environ.get("AZURE_AI_MODEL_DEPLOYMENT", "gpt-4o")

    agent = client.agents.create_agent(
        model=model,
        name="dispatcher",
        instructions=SYSTEM_PROMPT,
    )

    thread = client.agents.threads.create()

    client.agents.messages.create(
        thread_id=thread.id,
        role=MessageRole.USER,
        content=user_input,
    )

    run = client.agents.runs.create_and_process(
        thread_id=thread.id,
        agent_id=agent.id,
    )

    print("Run status:", run.status)
    if run.last_error:
        print("Run error:", run.last_error)

    if run.status == "failed":
        client.agents.delete_agent(agent.id)
        raise RuntimeError(f"Dispatcher run failed: {run.last_error}")

    messages_list = list(client.agents.messages.list(thread_id=thread.id))

    response_text = next(
        m.content[0].text.value
        for m in messages_list
        if m.role == "assistant"
    )
    # Clean up remote resources
    client.agents.delete_agent(agent.id)

    response_text = response_text.strip().removeprefix("```json").removeprefix("```").removesuffix("```").strip()

    return {"raw": response_text, "thread_id": thread.id}