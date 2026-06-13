# TAT Reasoning Agent

An enterprise certification preparation system built for the **Agents League Hackathon 2026** (Reasoning Agents Track). It helps organisations manage employee technical certification readiness through a multi-agent AI system built on Azure AI Foundry.

> **Note:** All learner, team, and work-activity data in this repository is entirely synthetic and contains no real personally identifiable information (PII). It is provided for demonstration purposes only.

---

## Agent Architecture

The system follows a **Hub-and-Spoke** pattern. All user interactions flow through the Dispatcher, which routes to the appropriate specialist agent. Sub-agents communicate only with the Dispatcher — never with each other directly.

```
User Input
    ↓
Dispatcher (Hub)
    ├── Learning Path Curator
    ├── Study Plan Generator
    ├── Engagement Agent
    ├── Assessment Agent
    └── Manager Insights Agent
```

### Dispatcher

**File:** `agents/dispatcher.py`

The Dispatcher is the central hub. It receives the user's raw message, extracts intent, and returns a structured routing decision (`route`, `reason`, `payload`). It identifies the target certification, job role, and team from the user's message and passes this context downstream.

### Learning Path Curator

**File:** `agents/learning_path_curator.py`

Runs a two-phase session:

- **Phase 1 — Background Familiarity Check:** Presents the learner with a self-scoring table (0–5) across all recommended background topics for the target certification. This is a single-turn exchange.
- **Phase 2 — Learning Path Output:** Uses Azure AI Search (Foundry IQ) to retrieve cited resources from the knowledge base. Calculates a `study_hours_multiplier` from the learner's background scores and produces an `adjusted_study_hours` figure. Only includes resources that are actually returned by the search tool — hallucinated sources are flagged as `[unverified]`.

Calls `manager_insights_agent.get_scope()` to determine which certifications are approved for the learner's role or team before making recommendations.

### Study Plan Generator

**File:** `agents/study_plan_generator.py`

A two-layer design:

- **Python layer** (`_calculate_plan`): Loads work activity signals, historical learner performance, and skill module data from `certifications.json`. Calculates weekly available study hours (30% of focus hours), applies an efficiency multiplier based on prior exam outcomes (Pass → 0.85×, Fail → 1.2×), orders modules by priority (low background scores first, then by weighting), and builds a week-by-week allocation. Flags a risk if projected completion exceeds the exam date.
- **LLM layer:** Receives the calculated plan as structured JSON and generates a natural language description with specific timing suggestions, weak-area analysis, and historical notes.

Supports dynamic plan adjustment via a persistent thread — the learner can request changes (e.g. "I can't study on Fridays") and the agent recalculates and re-describes the plan.

### Engagement Agent

**File:** `agents/engagement_agent.py`

Keeps learners on track between study sessions:

- **Python layer:** Classifies workload level from meeting hours (High ≥ 20 hrs/week, Medium 12–19, Low < 12). Calculates progress percentage from hours studied vs. adjusted study hours. Classifies urgency (Critical: < 14 days + < 70% progress; At Risk: < 30 days + < 50% progress; On Track otherwise). Checks held certifications for upcoming retirement dates (Fundamentals certs excluded). Determines current and next module from the weekly plan.
- **LLM layer:** Generates a personalised reminder message adapted to load level and urgency, including a text-based progress bar, suggested study time, next module preview, and any renewal warnings.

### Assessment Agent

**File:** `agents/assessment_agent.py`

Evaluates whether the learner is ready for the certification exam:

- Generates questions grounded in the knowledge base via Azure AI Search (Foundry IQ) — no questions from memory.
- Adaptive difficulty: correct answer → harder question, wrong answer → easier question.
- Strict single-step protocol: each answer submission returns evaluation + next question in one response.
- Final response embeds the complete summary (`questions_asked`, `correct_answers`, `score_percentage`, `weakest_topic`) before closing.
- Module scores are written back to `learner_performance.json` via `complete_module()`. A weighted final score is calculated across all assessed modules against the pass threshold of 80%.

### Manager Insights Agent

**File:** `agents/manager_insights_agent.py`

Serves two functions:

1. **`get_scope(payload)`** — Pure Python. Returns the approved certification list for a given role or team. Used by the Learning Path Curator before searching. Lookup order: team_id → role (manager_team_config.json) → manager_role_config.json → certifications.json target_roles.
2. **`get_insights(payload)`** — Python calculates team-level stats (overall progress, at-risk learners, common weak modules, retirement warnings). LLM generates a professional summary with actionable recommendations for the manager. Individual sensitive data is not exposed beyond role and progress.

---

## Microsoft IQ Layers

| Layer | Role in this system |
|---|---|
| **Foundry IQ** | Azure AI Search index over `data/knowledge/` markdown files. Powers cited question generation (Assessment Agent) and cited resource retrieval (Learning Path Curator). |
| **Fabric IQ** | Simulated via `certifications.json`. Provides skill module structure, weightings, recommended study hours, and pass thresholds — the semantic foundation for plan generation and scoring. |
| **Work IQ** | Simulated via `work_activity_signals.json`. Provides meeting load, focus hours, and preferred learning slots. Used by Study Plan Generator and Engagement Agent to make schedules realistic. |

---

## Project Structure

```
tat-reasoning-agent/
├── app.py                          # Streamlit UI — main entry point
├── main.py                         # CLI entry point for quick testing
├── dev_reset.py                    # Resets learner_performance.json to baseline
├── requirements.txt
├── agents/
│   ├── dispatcher.py
│   ├── learning_path_curator.py
│   ├── study_plan_generator.py
│   ├── engagement_agent.py
│   ├── assessment_agent.py
│   └── manager_insights_agent.py
└── data/
    ├── certifications.json         # Fabric IQ simulation — cert catalogue with skill modules
    ├── learner_performance.json    # Learner records, scores, and progress
    ├── work_activity_signals.json  # Work IQ simulation — meeting load, focus hours
    ├── manager_team_config.json    # Team composition and approved certification scope
    ├── manager_role_config.json    # Role-based certification recommendations
    ├── saved_questions.json        # Questions bookmarked by the learner during assessment
    └── knowledge/                  # Foundry IQ knowledge base (uploaded to Azure AI Search)
        ├── az-104.md
        ├── az-204.md
        ├── az-305.md
        ├── az-400.md
        ├── az-500.md
        ├── az-900.md
        ├── azure-solutions-architect-expert.md
        ├── devops-engineer-expert.md
        ├── dp-300.md
        ├── dp-700.md
        └── sc-900.md
```

---

## Setup

### Prerequisites

- Python 3.11+
- Azure subscription with an Azure AI Foundry project
- Azure AI Search index named `cert-knowledge-base-index` populated with files from `data/knowledge/`

### Install

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
pip install streamlit
```

### Environment Variables

Create a `.env` file in the project root:

```
AZURE_AI_PROJECT_ENDPOINT=https://<your-project>.services.ai.azure.com/api/projects/<project-id>
AZURE_AI_MODEL_DEPLOYMENT=gpt-4o
FOUNDRY_KNOWLEDGE_BASE_CONNECTION_ID=<azure-ai-search-connection-id>
```

Authentication uses `DefaultAzureCredential`. Run `az login` before starting the app.

### Run

```bash
streamlit run app.py
```

---

## Learner Session Flow

1. Sign in with a learner ID (e.g. `L-TEST-001`)
2. **Learning Path Curator** — self-score background topics, receive cited learning resources
3. **Study Plan Generator** — view a personalised weekly study plan; adjust via natural language
4. **Engagement Agent** — check in on progress, receive workload-aware reminders
5. **Assessment Agent** — attempt a graded module assessment with adaptive questions; bookmark questions for later review
6. Pass (≥ 80%) → recommended for the next certification. Fail → looped back to Study Plan Generator with weak areas highlighted.
7. **Manager view** — managers can check team readiness, at-risk learners, and common weak modules at any time.

---

## Responsible AI

- **Input filtering** is enforced at the Dispatcher — requests unrelated to certification preparation are not routed to specialist agents.
- **Source grounding** — the Assessment Agent and Learning Path Curator are instructed to use Azure AI Search and must not generate content from memory. Unverifiable sources are flagged as `[unverified]`.
- **No PII** — all learner identifiers are synthetic (L-TEST-001, EMP-001). The Manager Insights Agent aggregates data and does not surface individual sensitive details.
- **Retirement warnings** — learners and managers are proactively warned when a target certification has a known retirement date.

---

## Dev Utilities

```bash
# Reset learner_performance.json to baseline (wipes scores and progress)
python dev_reset.py

# CLI smoke test — runs the full agent chain without the UI
python main.py
```

---

Built for the Agents League Hackathon 2026 — Reasoning Agents Track (Battle #2).
