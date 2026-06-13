# TAT Reasoning Agent — CLAUDE.md

> Agents League Hackathon 2026 | Reasoning Agents Track
> Learner: Cloud Engineer preparing for AZ-204 | Team: TEAM-A

---

## Coding Rules

- **Minimum changes only.** Make the smallest edit that satisfies the request. Do not refactor surrounding code, rename variables, add error handling, or improve unrelated things unless explicitly asked.

---

## Project Overview

Enterprise learning certification management system built on **Microsoft Foundry**.
Multi-agent hub-and-spoke architecture. A Dispatcher routes user requests to five
specialist agents. A Streamlit UI replaces the original terminal interface.

---

## Architecture

```
User Input (Streamlit UI / app.py)
        │
        ▼
  [Input Guard]          ← Responsible AI scoring (pre-Dispatcher)
        │
        ▼
  [Dispatcher]           ← Routes to correct agent
        │
   ┌────┼────┬──────────────┬──────────────┐
   ▼    ▼    ▼              ▼              ▼
[LPC] [SPG] [Engagement] [Assessment] [Manager Insights]
```

**IQ Layers used:**
- **Foundry IQ** — Azure AI Search over `data/knowledge/` (11 markdown files, cert-knowledge-base-index)
- **Work IQ** — `data/work_activity_signals.json` (meeting load, focus hours, preferred slot)
- **Fabric IQ** — `data/certifications.json` module weights + `data/learner_performance.json`

---

## Environment

- Python 3.14 · venv at `.venv/` · Azure CLI via Homebrew
- Azure subscription: `40f108df-89d4-442c-b8f1-975cceb52657` (East US 2)
- Foundry project: `tat-reasoning-agent` · model: `gpt-4o`
- Azure AI Search: `tatreasoningagentsrchke45bk` (Central US)
- IAM roles required on Search resource: `Search Index Data Reader`, `Search Service Contributor`
- `.env`: `AZURE_AI_PROJECT_ENDPOINT`, `AZURE_AI_MODEL_DEPLOYMENT`, `FOUNDRY_KNOWLEDGE_BASE_CONNECTION_ID`

---

## Agent Status

| Agent | File | Status |
|-------|------|--------|
| Dispatcher | `agents/dispatcher.py` | ✅ Complete |
| Manager Insights | `agents/manager_insights_agent.py` | ✅ Complete |
| Learning Path Curator | `agents/learning_path_curator.py` | ✅ Complete |
| Study Plan Generator | `agents/study_plan_generator.py` | ✅ Complete |
| Engagement Agent | `agents/engagement_agent.py` | ✅ Complete |
| Assessment Agent | `agents/assessment_agent.py` | ✅ Complete |
| Streamlit UI | `app.py` | ✅ Complete |

---

## Agent Details

### Dispatcher
Routes user input to one of five agents. Returns structured JSON with `route`,
`reason`, and `payload` (includes `role`, `certification`, `team_id`).

Available routes: `learning_path_curator` · `study_plan_generator` ·
`engagement_agent` · `assessment_agent` · `manager_insights_agent`

### Manager Insights Agent
Two responsibilities:
- `get_scope(payload)` — pure Python, no LLM. Three-layer fallback: team config →
  role config → certifications.json `target_roles`. Returns approved certification
  list + retirement warnings (AZ-204 retiring 2026-07-31, AZ-500 retiring 2026-08-31).
- `get_insights(payload)` — LLM-based team progress summary. Called at session end.

### Learning Path Curator (LPC)
Two-phase. Phase 1: scores learner background across ≤15 topics (0–5 scale, single
submission). Phase 2: retrieves cert content from Foundry IQ, recommends certification,
calculates `adjusted_study_hours = recommended × multiplier` (0.33x at score 5,
1.5x at score 0). Validates all sources against `KNOWN_SOURCES` set.

Public API: `start(payload)` · `continue_session(thread_id, agent_id, user_response)`

### Study Plan Generator (SPG)
Two-layer: Python calculation + LLM description.
- Python layer: reads `work_activity_signals.json` (focus_hours × 0.30 = weekly
  study hours), applies efficiency multiplier (Pass 0.85x / Fail 1.20x / None 1.0x),
  orders modules by weak-area priority, detects risk if plan exceeds `days_until_exam`.
- LLM layer: generates natural language weekly plan with timing recommendations.

Public API: `generate(payload)` · `adjust(thread_id, agent_id, message, payload)`

### Engagement Agent
- Python layer: classifies workload (high ≥20 meeting hrs, medium 12–19, low <12),
  calculates progress, determines urgency (critical / at_risk / on_track).
- LLM layer: personalised message adapted to load level and urgency.
- Work IQ: `work_activity_signals.json`

Public API: `check_in(payload)` · `update_progress(learner_id, hours_to_add)`

### Assessment Agent
Single-step protocol: one A/B/C/D answer → one `evaluated` response containing
evaluation + embedded `next_question` (or `complete_summary` on last question).
Adaptive difficulty: correct → harder, wrong → easier. 5 questions per module.
Pass threshold: 80%. Final score = weighted average using `certifications.json` weights.
Source cleaning: strips `【...】` citation markers via regex.

Public API: `start_module(payload)` · `next_question(thread_id, agent_id, answer)`
· `complete_module(learner_id, module_name, score)` · `calculate_final_score(learner_id, cert_code)`

---

## Data Files

| File | Purpose |
|------|---------|
| `data/certifications.json` | 11 certifications, skill modules, weights, retirement status |
| `data/knowledge/*.md` | 11 markdown files indexed in Foundry knowledge base |
| `data/manager_team_config.json` | 5 teams, approved certifications per team |
| `data/manager_role_config.json` | 5 roles, fallback certification lists |
| `data/learner_performance.json` | 8 learners (L-1001–L-1008) + L-TEST-001 |
| `data/work_activity_signals.json` | 8 employees, meeting load, focus hours, preferred slot |
| `data/saved_questions.json` | Saved assessment questions per learner (created at runtime) |

**Fields written at runtime to `learner_performance.json`:**
`exam_date` · `study_plan` · `lpc_output` · `current_module_index` ·
`adjusted_study_hours` · `skill_module_scores` · `practice_score_avg` · `exam_outcome`

---

## Session Loop

**First session (no saved plan):**
1. Run LPC (background scoring → learning path)
2. Run SPG (initial plan + optional adjustment)
3. Save `study_plan`, `lpc_output`, `current_module_index: 0` to JSON
4. Enter session loop

**Returning session (saved plan exists):**
1. Load saved state, call `spg_generate` to restore plan thread
2. Enter session loop directly (skip LPC and SPG)

**Per-module loop:**
```
1. Check exit conditions (all passed / exam tomorrow / cert retired)
2. Show module + days until exam
3. Ask hours studied today → update_progress
4. Check-in: "Have you completed [module]?"
   ├─ No  → offer plan adjustment
   └─ Yes → run Assessment (5 questions in dialog)
             ├─ Pass ≥80% → advance module index, save
             └─ Fail <80% → auto-adjust plan (weakest module focus)
5. "Continue today?" → Yes: next module / No: get_insights → end session
```

---

## Streamlit UI (app.py)

Run: `streamlit run app.py`

**Layout:** Sidebar (fixed) + Chat area (right)

**Sidebar sections:**
- Account block: username · role · team · Log out button
- Target certification (filled after LPC)
- Background scores (collapsible, after submission)
- Study Plan button (opens `@st.dialog`)
- Saved Questions button (opens `@st.dialog`, reads `data/saved_questions.json`)
- Exam countdown / date picker
- Module progress bars
- 7-day calendar (mark rest days → confirm → `spg_adjust`)

**Chat message types:**
- `text` — standard assistant/user message
- `slider_form` — background scoring (0–5 per topic, single submission)
- `hours_input` — number input for hours studied today
- `completion_check` — Yes / Not yet buttons for module completion
- `continue_check` — Continue / Stop for today buttons
- `question` — placeholder only (actual question in dialog)

**Assessment dialog (`@st.dialog("Assessment Question")`):**
- Shows question, difficulty level (formatted as "Difficulty Level: Easy/Medium/High"), options
- Phase A: A/B/C/D buttons → calls `next_question` → stays open
- Phase B: shows selected answer + evaluation (✅/❌ + explanation + knowledge point)
- Buttons: "🔖 Save" (writes to `saved_questions.json`) + "Next Question →" / "Finish →"

---

## Known Issues (Pending)

| Issue | Description |
|-------|-------------|
| Issue 1 | Dynamic adjustment uses original Work IQ payload, not re-read signals |
| Issue 5 | Assessment agent occasionally returns options only, no question text |
| Issue 7 | Certification mismatch: assessment tests plan cert, not registered cert |

**Resolved:** Issues 2, 3, 4, 6, 8, 9, 10

---

## Dev Utilities

```bash
python dev_reset.py        # Resets L-TEST-* records in learner_performance.json
streamlit run app.py       # Launch UI
```

Test learner: `L-TEST-001` · Cloud Engineer · TEAM-A · AZ-204 · exam date 2026-07-31
