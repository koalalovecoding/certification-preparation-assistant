"""
HTTP entry point for Azure Container Apps hosted deployment.

Key design decision: uses Azure OpenAI chat completions API directly
instead of Foundry Agent Service (client.agents.create_agent).
This avoids the "agents/write" permission issue in Container Apps
while producing identical routing output to dispatcher.py.

dispatcher.py is unchanged and continues to work locally via DefaultAzureCredential.
"""

import os
import json

from fastapi import FastAPI
from pydantic import BaseModel
from azure.ai.projects import AIProjectClient
from azure.identity import ClientSecretCredential, DefaultAzureCredential
from dotenv import load_dotenv

load_dotenv()

app = FastAPI()

# Identical system prompt to dispatcher.py — same routing behaviour
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

Respond in this JSON format:
{
  "route": "<agent_name>",
  "reason": "<one sentence explaining why>",
  "payload": {
    "user_input": "<original user message>",
    "extracted_intent": "<what the user wants>",
    "certification": "<certification code if mentioned, else null>",
    "role": "<job role if mentioned, else null>",
    "team_id": "<team ID if mentioned e.g. TEAM-A, TEAM-B, else null>"
  }
}
"""


def get_credential():
    """
    In container: AZURE_CLIENT_ID/SECRET/TENANT_ID are set → ClientSecretCredential.
    Locally: those vars are absent → DefaultAzureCredential (Azure CLI).
    dispatcher.py is unaffected — it always uses DefaultAzureCredential.
    """
    client_id     = os.environ.get("AZURE_CLIENT_ID")
    client_secret = os.environ.get("AZURE_CLIENT_SECRET")
    tenant_id     = os.environ.get("AZURE_TENANT_ID")
    if client_id and client_secret and tenant_id:
        return ClientSecretCredential(tenant_id, client_id, client_secret)
    return DefaultAzureCredential()


class UserRequest(BaseModel):
    message: str


@app.get("/")
def root():
    return {"status": "Certification Coach Agent — hosted dispatcher endpoint active"}


@app.post("/chat")
def chat(req: UserRequest):
    """
    Routes user message using chat completions API (not Foundry Agent Service).
    Produces same JSON output format as dispatcher.dispatch().
    """
    endpoint = os.environ["AZURE_AI_PROJECT_ENDPOINT"]
    model    = os.environ.get("AZURE_AI_MODEL_DEPLOYMENT", "gpt-4o")

    client = AIProjectClient(
        endpoint=endpoint,
        credential=get_credential(),
    )

    # Use chat completions — does not require agents/write permission
    openai_client = client.inference.get_azure_openai_client(api_version="2024-12-01-preview")
    response = openai_client.chat.completions.create(
        model=model,
        messages=[
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user",   "content": req.message},
        ],
        temperature=0,
    )

    raw = response.choices[0].message.content.strip()
    raw = raw.removeprefix("```json").removeprefix("```").removesuffix("```").strip()

    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError:
        parsed = {}

    return {"raw": raw, "parsed": parsed}