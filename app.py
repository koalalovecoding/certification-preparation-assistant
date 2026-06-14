"""
Certification Preparation Assistant — Streamlit UI
Mirrors main.py session loop logic in a chat-based interface.
Run: streamlit run app.py

Changes in this version:
  [1] Logout button moved to sidebar, under user profile
  [2] Sidebar CSS injection for compact spacing
  [3] Assessment questions now open in @st.dialog modal
  [4] After answering, show eval + "Next Question →" button (no auto-advance)
  [5] "🔖 Save" button in question dialog to save questions
  [6] "🔖 Saved Questions" sidebar entry + data/saved_questions.json persistence
  [7] Difficulty label formatted as "Difficulty Level: Easy/Medium/High"
"""

import json
import datetime
from pathlib import Path

import streamlit as st

# ── Page config ────────────────────────────────────────────────────────────
st.set_page_config(
    page_title="Certification Preparation Assistant",
    page_icon="🎓",
    layout="wide",
    initial_sidebar_state="expanded",
)

# ── Constants ──────────────────────────────────────────────────────────────
# ss.learner_id is now dynamic — stored in ss.learner_id, set at login
DATA_PATH     = Path("data/learner_performance.json")
SAVED_Q_PATH  = Path("data/saved_questions.json")   # [CHANGE 6] saved questions file

SCORE_LABELS = {
    1: "1 — Heard of it",
    2: "2 — Basic understanding",
    3: "3 — Somewhat familiar",
    4: "4 — Comfortable",
    5: "5 — Confident",
}

# ── Data helpers ───────────────────────────────────────────────────────────
def load_learner() -> dict:
    with open(DATA_PATH) as f:
        raw = json.load(f)
    if isinstance(raw, list):
        return next((l for l in raw if l.get("learner_id") == ss.learner_id), {})
    return raw.get(ss.learner_id, {})

def save_learner_fields(**fields):
    with open(DATA_PATH) as f:
        raw = json.load(f)
    if isinstance(raw, list):
        for l in raw:
            if l.get("learner_id") == ss.learner_id:
                l.update(fields)
                break
    else:
        raw.setdefault(ss.learner_id, {}).update(fields)
    with open(DATA_PATH, "w") as f:
        json.dump(raw, f, indent=2)

# [CHANGE 5,6] Save a question + its evaluation to data/saved_questions.json
def save_question(q_data: dict, eval_data: dict):
    if SAVED_Q_PATH.exists():
        with open(SAVED_Q_PATH) as f:
            data = json.load(f)
    else:
        data = {}
    if ss.learner_id not in data:
        data[ss.learner_id] = []
    data[ss.learner_id].append({
        "question":   q_data,
        "evaluation": eval_data,
        "module":     st.session_state.get("current_mod", ""),
        "saved_at":   datetime.datetime.now().isoformat(),
    })
    SAVED_Q_PATH.parent.mkdir(parents=True, exist_ok=True)
    with open(SAVED_Q_PATH, "w") as f:
        json.dump(data, f, indent=2)

# [CHANGE 6] Load saved questions for the current learner
def load_saved_questions() -> list:
    if not SAVED_Q_PATH.exists():
        return []
    with open(SAVED_Q_PATH) as f:
        data = json.load(f)
    return data.get(ss.learner_id, [])

def days_until(date_str) -> int | None:
    if not date_str:
        return None
    try:
        return (datetime.date.fromisoformat(str(date_str)) - datetime.date.today()).days
    except (ValueError, TypeError):
        return None

def unique_modules(weekly_plan: list) -> list:
    seen, result = set(), []
    for w in (weekly_plan or []):
        m = w.get("focus") or w.get("module")
        if m and m not in seen:
            seen.add(m)
            result.append(m)
    return result

def _latest_score(scores: dict, mod: str) -> float:
    """
    Scores are now stored as lists (multiple attempts).
    Returns the latest attempt score, or 0 if no attempts yet.
    Also handles legacy single-float values.
    """
    v = scores.get(mod)
    if isinstance(v, list):
        return v[-1] if v else 0.0
    return v or 0.0

def all_modules_passed(unique_mods: list, skill_scores: dict | None) -> bool:
    if not skill_scores:
        return False
    # Use _latest_score to support list-format scores
    return all(_latest_score(skill_scores, m) >= 80.0 for m in unique_mods)

def check_time_limit(learner: dict, lpc_warnings: list) -> tuple[bool, str]:
    exam_days = days_until(learner.get("exam_date"))
    if exam_days is not None and exam_days <= 1:
        label = "today" if exam_days <= 0 else "tomorrow"
        return True, f"⏰ Your exam is {label}! Time to rest and do a final review."
    for w in lpc_warnings:
        msg = w.get("message", "") if isinstance(w, dict) else str(w)
        if "retiring on" in msg:
            try:
                retire_str  = msg.split("retiring on")[1].strip().split(".")[0].strip()
                retire_days = days_until(retire_str)
                if retire_days is not None and retire_days <= 1:
                    label = "today" if retire_days <= 0 else "tomorrow"
                    return True, f"⚠️ The certification retires {label}. Consider an alternative."
            except (IndexError, ValueError):
                pass
    return False, ""

def parse_json_safe(raw: str) -> dict | None:
    if not raw:
        return None
    try:
        cleaned = (
            raw.strip()
            .removeprefix("```json")
            .removeprefix("```")
            .removesuffix("```")
            .strip()
        )
        return json.loads(cleaned)
    except Exception:
        return None

def add_msg(role: str, content: str, msg_type: str = "text", data: dict = None):
    st.session_state.messages.append({
        "role":    role,
        "content": content,
        "type":    msg_type,
        "data":    data or {},
    })

# ── Session state ──────────────────────────────────────────────────────────
_DEFAULTS = {
    "logged_in":            False,
    "username":             "",
    "learner_id":           "",
    "learner":              {},
    "phase":                "login",
    "messages":             [],
    # LPC
    "lpc_thread_id":        None,
    "lpc_agent_id":         None,
    "topics":               [],
    "background_submitted": False,
    "background_scores":    {},
    "lpc_result":           None,
    "lpc_warnings":         [],
    "target_cert":          None,
    "reg_payload":          None,   # payload stored during cert_confirm / survey_confirm flow
    # Study plan
    "plan_result":          None,
    "plan_thread_id":       None,
    "plan_agent_id":        None,
    "plan_payload":         None,
    "unique_mods":          [],
    "mod_idx":              0,
    # Session loop state
    "hours_today":          0.0,
    "current_mod":          None,
    "skill_scores":         {},
    # Assessment thread
    "assess_thread_id":     None,
    "assess_agent_id":      None,
    "q_states":             {},
    # [CHANGE 3,4] Question dialog state
    "question_dialog_open":  False,   # True = dialog is open
    "current_question_data": None,    # dict: the active question
    "current_eval":          None,    # dict: evaluation result after answering
    "next_question_data":    None,    # dict: next question embedded in eval response
    "assessment_complete":   False,   # True = all questions done
    "assessment_summary":    None,    # dict: final summary from agent
    # Calendar
    "no_study_dates":       [],
    "pending_no_study":     None,
    "pending_restore_study": None,
    # Exam registration dialog
    "exam_register_open":   False,
    # Pending action
    "pending":              None,
    # Exam date
    "exam_date":            None,
}

def _init_state():
    for k, v in _DEFAULTS.items():
        if k not in st.session_state:
            st.session_state[k] = v

_init_state()
ss = st.session_state

# ── Study plan dialog (unchanged) ──────────────────────────────────────────
@st.dialog("Study Plan", width="medium")
def show_plan_dialog():
    st.markdown(
        "<style>[data-testid='stDialog'] > div:first-child { max-width: 625px !important; margin-left: auto !important; margin-right: auto !important; }</style>",
        unsafe_allow_html=True,
    )

    # Show only the coffee screen while adjustment is running
    if ss.get("plan_adjusting"):
        from agents.study_plan_generator import adjust as spg_adjust
        st.markdown(
            "<div style='text-align:center;padding:4rem 0'>"
            "<div style='font-size:4rem'>☕</div>"
            "<div style='margin-top:1rem;font-size:1.1rem'>Brewing your updated study plan now...</div>"
            "</div>",
            unsafe_allow_html=True,
        )
        result = spg_adjust(ss.plan_thread_id, ss.plan_agent_id, ss.plan_adj_request, ss.plan_payload)
        ss.plan_result = result
        add_msg("assistant", f"✅ Plan adjusted: _{ss.plan_adj_request}_")
        ss.plan_adjusting = False
        ss.plan_adj_request = ""
        ss.plan_dialog_open = True
        st.rerun()
        return

    learner  = load_learner()
    plan     = ss.plan_result
    parsed   = (plan.get("parsed") or {}) if plan else {}
    pd_data  = (plan.get("plan_data") or {}) if plan else {}

    # Fallback to saved learner fields for returning users
    saved_plan = learner.get("study_plan", [])
    if not pd_data.get("total_weeks") and saved_plan:
        weeks_set = {w.get("week") for w in saved_plan if w.get("week")}
        pd_data = {
            "total_weeks":               len(weeks_set) or "—",
            "weekly_study_hours":        learner.get("weekly_study_hours", "—"),
            "projected_completion_date": learner.get("projected_completion_date", "—"),
        }
    if not parsed.get("weekly_plan_description") and saved_plan:
        parsed = {"weekly_plan_description": saved_plan, "summary": parsed.get("summary", "")}

    weekly = parsed.get("weekly_plan_description") or pd_data.get("weekly_plan") or saved_plan
    total_weeks_display = (
        max((w.get("week") or 0 for w in weekly), default=0) or pd_data.get("total_weeks", "—")
    ) if weekly else pd_data.get("total_weeks", "—")
    if isinstance(total_weeks_display, int) and total_weeks_display > 0:
        projected = (datetime.date.today() + datetime.timedelta(weeks=total_weeks_display)).isoformat()
    else:
        projected = pd_data.get("projected_completion_date", "—")

    if weekly and isinstance(total_weeks_display, int) and total_weeks_display > 0:
        # Sum hours per week number, then take the max.
        # The last week is usually partial (leftover hours), so averaging gives a
        # lower-than-actual rate. The max week = a fully-packed week = the true rate.
        from collections import defaultdict as _dd
        _hours_by_week = _dd(float)
        for w in weekly:
            _wn = w.get("week") or 0
            _h = (
                sum(m.get("hours", 0) for m in w.get("focus", []) if isinstance(m, dict))
                if isinstance(w.get("focus"), list) else (w.get("hours") or 0)
            )
            _hours_by_week[_wn] += _h
        weekly_study_hours_display = round(max(_hours_by_week.values()), 1) if _hours_by_week else pd_data.get("weekly_study_hours", "—")
    else:
        weekly_study_hours_display = pd_data.get("weekly_study_hours", "—")

    exam_date_str = ss.exam_date or learner.get("exam_date") or "—"

    # Look up retirement date for the target cert
    retirement_date_str = None
    _cert_code = ss.target_cert or learner.get("certification")
    if _cert_code:
        try:
            with open("data/certifications.json") as _f:
                _certs = json.load(_f)
            _cert = next((c for c in _certs if c.get("certification_code") == _cert_code), None)
            if _cert:
                _ret = _cert.get("retirement_status", {})
                if not _ret.get("is_retired") and _ret.get("retirement_date"):
                    retirement_date_str = _ret["retirement_date"]
        except Exception:
            pass

    # Compute days until exam for the metric
    _days_until_exam = "—"
    if exam_date_str != "—":
        try:
            _days_until_exam = (datetime.date.fromisoformat(exam_date_str) - datetime.date.today()).days
        except (ValueError, TypeError):
            pass

    c1, c2 = st.columns(2)
    c1.metric("Total Weeks",     total_weeks_display)
    c2.metric("Days Until Exam", _days_until_exam)
    c3, c4 = st.columns(2)
    c3.metric("Projected Completion", projected)
    c4.metric("Exam Date",            exam_date_str)

    # Warnings if projected completion exceeds exam or retirement date
    if projected != "—":
        try:
            projected_dt = datetime.date.fromisoformat(projected)
            if exam_date_str != "—":
                exam_dt = datetime.date.fromisoformat(exam_date_str)
                if projected_dt > exam_dt:
                    st.warning(f"⚠️ Projected completion ({projected}) is after your exam on {exam_date_str}. Consider increasing weekly study hours.")
            if retirement_date_str:
                retire_dt = datetime.date.fromisoformat(retirement_date_str)
                if projected_dt > retire_dt:
                    st.warning(f"⚠️ Projected completion ({projected}) is after {_cert_code} retires on {retirement_date_str}. You must complete preparation before this date.")
        except (ValueError, TypeError):
            pass

    if parsed.get("summary"):
        st.divider()
        st.write(parsed["summary"])

    if ss.plan_thread_id:
        st.divider()
        adj = st.text_input("Request an adjustment:")
        if st.button("Apply →") and adj.strip():
            ss.plan_adjusting = True
            ss.plan_adj_request = adj
            ss.plan_dialog_open = True
            st.rerun()

    if weekly:
        st.divider()

        # Build set of passed modules (latest assessment score ≥ 80)
        _scores = ss.skill_scores or {}
        passed_mods = {m for m in _scores if _latest_score(_scores, m) >= 80}

        # Use sidebar module order (unique_mods) so plan matches Module Progress.
        # Filter out passed modules — no need to study them again.
        _ordered_mods = [m for m in (ss.unique_mods or unique_modules(weekly))
                         if m not in passed_mods]

        # Group weekly plan entries by their string focus module.
        # List-focus entries (LLM returns multiple sub-modules in one week) are
        # kept separately and rendered after the ordered modules.
        from collections import defaultdict
        _by_mod = defaultdict(list)
        _list_focus_weeks = []
        for w in weekly:
            f = w.get("focus") or w.get("module", "")
            if isinstance(f, list):
                _list_focus_weeks.append(w)
            else:
                _by_mod[f].append(w)

        def _render_week(w):
            """Render a single week expander — extracted to avoid duplication."""
            focus = w.get("focus") or w.get("module", "")
            if isinstance(focus, list):
                sub_modules = focus
                focus_label = " + ".join(
                    m.get("module", m.get("focus", "")) if isinstance(m, dict) else str(m)
                    for m in sub_modules
                )
                total_hours = sum(m.get("hours", 0) for m in sub_modules if isinstance(m, dict)) or w.get("hours")
            else:
                sub_modules = None
                focus_label = focus
                total_hours = w.get("hours")
            hours_str = f" ({total_hours}h)" if total_hours is not None else ""
            label = f"Week {w.get('week')} — {focus_label}{hours_str}"
            with st.expander(label):
                if sub_modules:
                    for m in sub_modules:
                        if isinstance(m, dict):
                            st.markdown(f"**{m.get('module', m.get('focus', ''))}** — {m.get('hours', '')}h")
                            if m.get("description"):
                                st.write(m["description"])
                elif w.get("description"):
                    st.write(w["description"])

        # Render in sidebar order, skipping passed modules
        for mod in _ordered_mods:
            for w in _by_mod.get(mod, []):
                _render_week(w)

        # Render any list-focus weeks (edge case from LLM grouping multiple modules)
        for w in _list_focus_weeks:
            _render_week(w)


# [CHANGE 6] Saved Questions dialog ────────────────────────────────────────
@st.dialog("🔖 Saved Questions", width="medium")
def show_saved_questions_dialog():
    # Clamp "medium" (750px) down to 625px ≈ 1.25× the old "small" (500px)
    st.markdown(
        "<style>"
        "[data-testid='stDialog'] > div:first-child { max-width: 625px !important; margin-left: auto !important; margin-right: auto !important; }"
        "[data-testid='stDialog'] details > summary { background-color: #d4edda !important; }"
        "</style>",
        unsafe_allow_html=True,
    )

    saved = load_saved_questions()
    if not saved:
        st.info("No saved questions yet. Save questions during assessments using the 🔖 button.")
        return
    st.caption(f"{len(saved)} question(s) saved")

    # Group entries by module, preserving first-save order
    groups: dict[str, list] = {}
    for entry in saved:
        mod = entry.get("module", "") or "Uncategorized"
        groups.setdefault(mod, []).append(entry)

    for mod, entries in groups.items():
        st.divider()
        st.markdown(f"**{mod}** &nbsp; `{len(entries)}`", unsafe_allow_html=True)

        for q_num, entry in enumerate(entries, start=1):
            raw_q = entry.get("question", {})

            # Handle flat format (question is a string, all fields at entry level)
            # vs nested format (question is a dict, evaluation is a sub-dict)
            if isinstance(raw_q, str):
                q = entry
                ev = {
                    "correct_answer":  entry.get("correct_answer", ""),
                    "explanation":     entry.get("explanation", ""),
                    "knowledge_point": entry.get("knowledge_point", ""),
                }
            else:
                q = raw_q
                ev = (entry.get("evaluation") or {}).get("parsed") or {}

            ts        = entry.get("saved_at", "")[:10]
            q_text    = q.get("question") or q.get("question_text", "")
            label     = f"Q{q_num}. {q_text[:55]}…"
            rev_key   = f"reveal_sq_{entry.get('saved_at', f'{mod}_{q_num}')}"
            if rev_key not in ss:
                ss[rev_key] = False

            with st.expander(f"{label}  [{ts}]"):
                diff = q.get("difficulty", "")
                if diff:
                    st.caption(f"Difficulty Level: {diff.capitalize()}")
                # Question text at +2pt (≈ 1.13em relative bump)
                st.markdown(
                    f'<p style="font-size:calc(1em + 2pt);font-weight:bold;margin:4px 0 8px 0">{q_text}</p>',
                    unsafe_allow_html=True,
                )
                # Options — no highlighting; correct answer hidden until revealed
                for letter, text in (q.get("options") or {}).items():
                    st.markdown(f"&nbsp;&nbsp;{letter}: {text}")
                st.divider()
                # Correct-answer reveal toggle
                correct_ans = ev.get("correct_answer", "")
                if not ss[rev_key]:
                    if st.button("Show correct answer", key=f"btn_{rev_key}"):
                        ss[rev_key] = True
                else:
                    if correct_ans:
                        st.success(f"✅ Correct answer: **{correct_ans}**")
                    if st.button("Hide answer", key=f"hide_{rev_key}"):
                        ss[rev_key] = False
                # Explanation and knowledge point
                if ev.get("correct") is not None:
                    if ev.get("correct"):
                        st.success(f"✅ {ev.get('encouragement', 'Correct!')}")
                    else:
                        st.error(f"❌ {ev.get('encouragement', 'Incorrect.')}")
                if ev.get("explanation"):
                    st.markdown(f"💡 {ev['explanation']}")
                if ev.get("knowledge_point"):
                    st.caption(f"📌 Key concept: {ev['knowledge_point']}")

# [CHANGE 3] Question dialog — replaces inline question rendering ────────────
@st.dialog("Assessment Question", width="large")
def show_question_dialog():
    q = ss.current_question_data
    if not q:
        return

    # Header: module name + question number
    st.caption(f"Module: **{ss.current_mod or ''}**")

    # [CHANGE 7] Difficulty label formatted as "Difficulty Level: Easy/Medium/High"
    diff = q.get("difficulty", "")
    if diff:
        st.caption(f"Difficulty Level: {diff.capitalize()}")

    # Question text
    q_text = q.get("question") or q.get("question_text", "")
    q_num  = q.get("question_number", "")
    st.markdown(f"### Q{q_num}. {q_text}")

    # Source (skip unverified placeholders)
    src = q.get("source", "")
    if src and not src.startswith("[unverified"):
        st.caption(f"Source: `{src}`")

    st.divider()

    options = q.get("options", {})

    # ── [CHANGE 4] Phase A: no eval yet — show answer buttons ─────────────
    if ss.current_eval is None:
        st.markdown("**Choose your answer:**")
        for letter, text in options.items():
            if st.button(f"**{letter}**  {text}", key=f"dlg_opt_{letter}", use_container_width=True):
                # Call next_question agent synchronously inside the dialog
                from agents.assessment_agent import next_question
                with st.spinner("Evaluating your answer..."):
                    result = next_question(ss.assess_thread_id, ss.assess_agent_id, letter)
                parsed = result.get("parsed") or {}
                status = result.get("status", "evaluated")

                # Store selected letter for display
                ss.current_eval = result
                ss.current_eval["_selected"] = letter  # track what user picked

                if status == "complete" or (
                    status == "evaluated"
                    and parsed.get("next_question") is None
                ):
                    # All questions done — store summary for "Finish →" handler
                    ss.assessment_complete = True
                    ss.assessment_summary  = parsed.get("complete_summary") or parsed
                else:
                    # More questions — store next question for "Next Question →"
                    ss.next_question_data = parsed.get("next_question")

                st.rerun()  # rerun with dialog still open (question_dialog_open = True)

    # ── [CHANGE 4] Phase B: eval received — show question + result + buttons
    else:
        ev       = ss.current_eval
        p        = ev.get("parsed") or {}
        selected = ev.get("_selected", "")

        # Replay options with selected highlighted
        for letter, text in options.items():
            if letter == selected:
                st.markdown(f"→ **{letter}: {text}** ✓")
            else:
                st.markdown(f"&nbsp;&nbsp;{letter}: {text}")

        st.divider()

        # Feedback: correct or incorrect
        if p.get("correct"):
            st.success(f"✅ {p.get('encouragement', 'Correct!')}")
        else:
            st.error(f"❌ {p.get('encouragement', 'Incorrect.')}")
            if p.get("correct_answer"):
                st.info(f"Correct answer: **{p['correct_answer']}**")

        # Explanation and study hints
        if p.get("explanation"):
            st.markdown(f"💡 {p['explanation']}")
        if p.get("knowledge_point"):
            st.caption(f"📌 Key concept: {p['knowledge_point']}")
        if p.get("study_recommendation"):
            st.caption(f"📖 Study next: {p['study_recommendation']}")
        ev_src = p.get("source", "")
        if ev_src and not ev_src.startswith("[unverified"):
            st.caption(f"Source: `{ev_src}`")

        st.divider()

        # [CHANGE 4,5] Action buttons row: Save + Next/Finish
        btn_col1, btn_col2 = st.columns([1, 2])

        # [CHANGE 5] Save button — persist question + eval to JSON
        with btn_col1:
            if st.button("🔖 Save", use_container_width=True):
                save_question(q, ev)
                st.toast("Question saved!", icon="🔖")

        if ss.assessment_complete:
            summary = ss.assessment_summary or {}
            score_pct = summary.get("score_percentage", 0)
            st.divider()
            if score_pct >= 80:
                st.success(f"🎉 Assessment complete! Score: {score_pct:.0f}% — Passed")
            else:
                st.error(f"Assessment complete! Score: {score_pct:.0f}% — Below 80% pass threshold")
            if summary.get("summary"):
                st.write(summary["summary"])
            if summary.get("weakest_topic"):
                st.caption(f"📌 Weakest area: {summary['weakest_topic']}")

        with btn_col2:
            if ss.assessment_complete:
                # Last question — show Finish button
                if st.button("Finish →", type="primary", use_container_width=True):
                    _handle_assessment_finish()
            else:
                # More questions — advance to next
                if st.button("Next Question →", type="primary", use_container_width=True):
                    _handle_next_question_advance()

# [CHANGE 4] Helper: advance to next question inside dialog
def _handle_next_question_advance():
    """Called when user clicks 'Next Question →' in the dialog."""
    # Add a brief placeholder to chat history so the session has a record
    q     = ss.current_question_data or {}
    p     = (ss.current_eval or {}).get("parsed") or {}
    q_num = q.get("question_number", "")
    correct_icon = "✅" if p.get("correct") else "❌"
    add_msg("assistant", f"📝 Q{q_num} — {correct_icon}", msg_type="text")

    # Move to next question
    ss.current_question_data = ss.next_question_data
    ss.next_question_data    = None
    ss.current_eval          = None
    st.rerun()

# [CHANGE 4] Helper: handle assessment completion when user clicks "Finish →"
def _handle_assessment_finish():
    """Called when user clicks 'Finish →' on the last question."""
    from agents.assessment_agent import complete_module, calculate_final_score

    # Add final question placeholder to chat
    q     = ss.current_question_data or {}
    p     = (ss.current_eval or {}).get("parsed") or {}
    q_num = q.get("question_number", "")
    correct_icon = "✅" if p.get("correct") else "❌"
    add_msg("assistant", f"📝 Q{q_num} — {correct_icon}", msg_type="text")

    # Compute score from summary
    summary = ss.assessment_summary or {}
    score   = summary.get("score_percentage", summary.get("module_score", summary.get("score", 0)))
    passed  = score >= 80.0
    learner = load_learner()
    cert    = ss.target_cert or learner.get("certification", "AZ-204")

    # Persist module score — append to list to track multiple attempts
    existing = ss.skill_scores.get(ss.current_mod)
    if isinstance(existing, list):
        existing.append(score)
    else:
        ss.skill_scores[ss.current_mod] = [score]
    complete_module(ss.learner_id, ss.current_mod, score)
    final = calculate_final_score(ss.learner_id, cert)

    # Post result to chat
    result_text = (
        f"{'✅' if passed else '❌'} **{ss.current_mod}** — Score: {score:.0f}%\n\n"
        + (summary.get("summary", "") or "")
        + f"\n\n**Overall progress:** {final.get('final_score', 0):.0f}% "
        + f"({'Pass 🎉' if final.get('passed') else 'In progress'})"
    )
    add_msg("assistant", result_text)

    if passed:
        new_idx    = ss.mod_idx + 1
        ss.mod_idx = new_idx
        save_learner_fields(current_module_index=new_idx)
    else:
        # Auto-adjust plan for weakest module (mirrors main.py)
        weakest     = final.get("weakest_module", ss.current_mod)
        adj_request = f"Focus more on {weakest} — learner scored {score:.0f}% on this module."
        add_msg("assistant", f"📚 Adjusting your plan to focus on: **{weakest}**")
        if ss.plan_thread_id and ss.plan_agent_id:
            ss.pending = {"action": "plan_adjust_fail", "adj_request": adj_request}

    # Close dialog and reset dialog state
    ss.question_dialog_open  = False
    ss.current_question_data = None
    ss.current_eval          = None
    ss.next_question_data    = None
    ss.assessment_complete   = False
    ss.assessment_summary    = None

    # Prompt user to continue or stop
    add_msg("assistant", "Would you like to continue studying today?", msg_type="continue_check")
    st.rerun()

# ── Sidebar ────────────────────────────────────────────────────────────────
def render_sidebar():
    with st.sidebar:
        # [CHANGE 2] Compact sidebar CSS — reduce margins between elements
        st.markdown(
            """<style>
            [data-testid="stProgressBarTrack"] > div { background-color: #28a745 !important; }
            [data-testid^="stBaseButton-primary"] { background-color: rgb(30, 58, 95) !important; border-color: rgb(30, 58, 95) !important; }
            section[data-testid="stSidebar"] [data-testid="stBaseButton-secondary"] { font-size: 0.65rem !important; padding: 2px 2px !important; min-height: 1.8rem !important; }
            section[data-testid="stSidebar"] [data-testid="column"] button { aspect-ratio: 1 / 1 !important; padding: 0 !important; min-height: unset !important; }
            section[data-testid="stSidebar"] [data-testid="stBaseButton-secondaryFormSubmit"] { font-size: 0.65rem !important; padding: 2px 2px !important; min-height: 1.8rem !important; }
            section[data-testid="stSidebar"] hr { margin: 6px 0 !important; }
            section[data-testid="stSidebar"] .stMarkdown p { margin-bottom: 2px !important; }
            section[data-testid="stSidebar"] [data-testid="stMetric"] { padding: 2px 0 !important; }
            section[data-testid="stSidebar"] [data-testid="stProgress"] { margin: 3px 0 !important; }
            section[data-testid="stSidebar"] .stExpander { margin: 2px 0 !important; }
            [data-testid="stSlider"] [role="slider"] { background-color: rgb(30, 58, 95) !important; border-color: rgb(30, 58, 95) !important; }
            [data-testid="stSlider"] [role="progressbar"] { background-color: rgb(30, 58, 95) !important; }
            [data-testid="stSlider"] p { color: rgb(30, 58, 95) !important; }
            </style>""",
            unsafe_allow_html=True,
        )

        st.markdown("## 🎓 Certification Preparation Assistant")
        st.divider()

        # ── Account block ──────────────────────────────────────────────────
        learner = ss.learner
        if ss.username:
            st.markdown(f"**{ss.username}**")
        if learner.get("role"):
            st.caption(f"Role: {learner['role']}")
        if learner.get("team_id"):
            st.caption(f"Team: {learner['team_id']}")

        # [CHANGE 1] Logout button directly under user profile (account block)
        if st.button("Log out", use_container_width=True, key="logout_sidebar"):
            for k in list(ss.keys()):
                del ss[k]
            _init_state()
            st.rerun()

        # Managing Dashboard — only for managers
        if learner.get("is_manager"):
            if st.button("📊 Managing Dashboard", use_container_width=True, key="mgr_dashboard_btn"):
                ss.pending = {"action": "manager_insights"}
                ss.phase   = "session"
                st.rerun()

        # Compute cert early — needed for Register Exam button visibility
        cert = (
            ss.target_cert
            or (learner.get("lpc_output") or {}).get("recommended_certification")
        )

        # Register Exam Preparation — always visible, allows re-registration for new exams
        if st.button("📝 Register Exam Preparation", use_container_width=True, key="register_exam_btn"):
            add_msg(
                "assistant",
                "Let's get you registered! Please fill in your exam details below.",
                msg_type="exam_register",
            )
            st.rerun()

        st.divider()

        # ── Target certification ───────────────────────────────────────────
        st.markdown("**🎯 Target Certification**")
        if cert:
            with open("data/certifications.json") as _f:
                _certs = json.load(_f)
            _full = next((c.get("certification_name","") for c in _certs if c.get("certification_code") == cert), "")
            st.markdown(f"`{cert}` {('· ' + _full) if _full else ''}")
            lpc_out = learner.get("lpc_output") or {}
            if lpc_out.get("warnings"):
                for w in lpc_out["warnings"]:
                    st.caption(f"⚠️ {w}")
        else:
            st.caption("_Appears after learning path setup_")

        # ── Certifications held ────────────────────────────────────────────
        certs_held = learner.get("certifications_held") or []
        if certs_held:
            st.divider()
            st.markdown("**🏅 Certifications Held**")
            with open("data/certifications.json") as _f:
                _all_certs = json.load(_f)
            _cert_map     = {c["certification_code"]: c for c in _all_certs}
            _renewal_dates = learner.get("certifications_renewal_dates") or {}
            # Fundamentals certs don't require renewal
            _FUNDAMENTALS = {"AZ-900", "SC-900", "DP-900", "AI-900", "MS-900"}
            for c in certs_held:
                _info    = _cert_map.get(c, {})
                _ret     = _info.get("retirement_status", {})
                _ret_date = _ret.get("retirement_date")
                _retired  = _ret.get("is_retired", False)
                _renewal  = _renewal_dates.get(c)
                if _retired:
                    st.markdown(f"☑ `{c}` ⚠️ *Retired*")
                elif _ret_date:
                    st.markdown(f"☑ `{c}`")
                    st.caption(f"Retires {_ret_date}")
                elif c in _FUNDAMENTALS:
                    st.markdown(f"☑ `{c}`")
                    st.caption("No renewal required")
                elif _renewal:
                    st.markdown(f"☑ `{c}`")
                    st.caption(f"Renew by {_renewal}")

        # ── Background scores (collapsible, after slider submission) ───────
        if ss.background_submitted and ss.background_scores:
            st.divider()
            with st.expander("📋 Background scores", expanded=False):
                for topic, score in ss.background_scores.items():
                    st.progress(score / 5, text=f"{topic[:28]}: {score}/5")

        st.divider()

        # ── Study plan button ──────────────────────────────────────────────
        has_plan = ss.plan_result or learner.get("study_plan")
        if has_plan:
            if st.button("📅 Study Plan", use_container_width=True):
                show_plan_dialog()

        # [CHANGE 6] Saved Questions button
        if st.button("🔖 Saved Questions", use_container_width=True):
            show_saved_questions_dialog()

        # ── Exam countdown — only show after user completes registration flow ─
        # Use ss.target_cert (set in cert_confirm) or lpc_output (completed LPC).
        # Do NOT fall back to learner["certification"] alone — it may be stale test data.
        _countdown_cert = (
            ss.target_cert
            or (learner.get("lpc_output") or {}).get("recommended_certification")
        )
        _exam_date = ss.exam_date or (learner.get("exam_date") if _countdown_cert else None)
        _scheduled = [(_countdown_cert, _exam_date)] if (_countdown_cert and _exam_date) else []
        if _scheduled:
            st.markdown("**📆 Days until exam**")
            for _ec, _ed in _scheduled:
                _d = days_until(_ed)
                if _d is None:
                    pass
                elif _d > 1:
                    st.markdown(f"`{_ec}` &nbsp; **{_d}** days")
                elif _d == 1:
                    st.warning(f"`{_ec}` — Exam is **tomorrow**!")
                elif _d == 0:
                    st.error(f"`{_ec}` — Exam is **today**!")
                else:
                    st.error(f"`{_ec}` — Exam date has passed")

        # ── Module progress bars ───────────────────────────────────────────
        mods   = ss.unique_mods or unique_modules(learner.get("study_plan", []))
        scores = ss.skill_scores or (learner.get("skill_module_scores") or {})
        if mods:
            st.divider()
            st.markdown("**Module Progress**")
            for mod in mods:
                raw = scores.get(mod)
                # Normalise to list — support both legacy float and new list format
                if isinstance(raw, list):
                    attempts = raw
                elif raw is not None:
                    attempts = [raw]
                else:
                    attempts = []

                latest = attempts[-1] if attempts else None

                if latest is not None and latest >= 80:
                    # Passed — checked box
                    st.markdown(f"☑ {mod}")
                else:
                    # Not passed or never attempted — empty box
                    st.markdown(f"☐ {mod}")
                    if attempts:
                        # Show last ≤3 scores as "Assessment history: 72%, 65%, 80%"
                        recent = attempts[-3:]
                        history_str = ", ".join(f"{s:.0f}%" for s in recent)
                        st.caption(f"Assessment history: {history_str}")

        # ── Calendar ──────────────────────────────────────────────────────
        import calendar as _cal

        # Derive auto rest days from Work IQ preferred_learning_slot.
        # If the slot names specific weekdays (e.g. "Monday, Tuesday, Friday 3-5pm"),
        # all other weekdays are automatically treated as rest days.
        _DAY_NAMES = {"monday":0,"tuesday":1,"wednesday":2,"thursday":3,"friday":4,"saturday":5,"sunday":6}
        try:
            from data_utils import _noop  # won't exist — just a try block to scope imports
        except Exception:
            pass
        _work_signals = next(
            (s for s in json.load(open("data/work_activity_signals.json"))
             if s.get("learner_id") == ss.learner_id), {}
        )
        _slot = (_work_signals.get("preferred_learning_slot") or "").lower()
        # Extract day names mentioned in the slot string
        _study_weekdays = {v for k, v in _DAY_NAMES.items() if k in _slot}
        # Auto rest days = weekdays (Mon-Fri) NOT in the study schedule
        _auto_rest_weekdays = (
            {0,1,2,3,4} - _study_weekdays if _study_weekdays else set()
        )

        st.divider()
        today  = datetime.date.today()
        st.markdown(f"**📅 {today.strftime('%B %Y')}**")
        # Weekday header
        hcols = st.columns(7)
        for i, d in enumerate(["Mo","Tu","We","Th","Fr","Sa","Su"]):
            hcols[i].markdown(f"<div style='text-align:center;font-size:0.75rem;color:grey'>{d}</div>", unsafe_allow_html=True)
        # Build full month grid (padded to start on correct weekday)
        _, days_in_month = _cal.monthrange(today.year, today.month)
        first_weekday = datetime.date(today.year, today.month, 1).weekday()
        month_days = [None] * first_weekday + [
            datetime.date(today.year, today.month, d) for d in range(1, days_in_month + 1)
        ]
        # Pad to complete last row
        while len(month_days) % 7:
            month_days.append(None)
        for week_start in range(0, len(month_days), 7):
            wcols = st.columns(7)
            for i, day in enumerate(month_days[week_start:week_start + 7]):
                if day is None:
                    wcols[i].write("")
                    continue
                day_str = day.isoformat()
                is_off      = day_str in ss.no_study_dates
                is_past     = day < today
                is_weekend  = day.weekday() >= 5
                # Auto rest day: weekday not in learner's study schedule
                is_auto_rest = day.weekday() in _auto_rest_weekdays
                label = "🚫" if (is_off or is_auto_rest) else str(day.day)
                if is_past or is_weekend or is_auto_rest:
                    wcols[i].button(label, key=f"cal_past_{day_str}", disabled=True, use_container_width=True)
                elif wcols[i].button(label, key=f"cal_{day_str}", help=day.strftime("%a %b %d"), use_container_width=True):
                        if is_off:
                            ss.pending_restore_study = day_str
                            ss.pending_no_study = None
                        else:
                            ss.no_study_dates.append(day_str)
                            ss.pending_no_study = day_str
                            ss.pending_restore_study = None
                        st.rerun()

            if ss.pending_no_study:
                st.warning(f"Mark **{ss.pending_no_study}** as rest day and adjust plan?")
                ca, cb = st.columns(2)
                if ca.button("Confirm", type="primary", key="cal_confirm"):
                    if ss.plan_thread_id and ss.plan_payload:
                        day_marked = ss.pending_no_study
                        ss.plan_adj_request = (
                            f"I won't be able to study on {day_marked}. "
                            "Please shift that day's content to the following day."
                        )
                        ss.plan_adjusting   = True
                        ss.pending_no_study = None
                        show_plan_dialog()
                    else:
                        st.warning("Cannot adjust: no active plan session.")
                        ss.pending_no_study = None
                    st.rerun()
                if cb.button("Cancel", key="cal_cancel"):
                    ss.no_study_dates.remove(ss.pending_no_study)
                    ss.pending_no_study = None
                    st.rerun()

            if ss.get("pending_restore_study"):
                day_r = ss.pending_restore_study
                st.info(f"💪 Great choice! Ready to reclaim **{day_r}** as a study day? Your plan will be updated to bring that day back.")
                ra, rb = st.columns(2)
                if ra.button("Yes, let's go!", type="primary", key="cal_restore_confirm"):
                    ss.no_study_dates.remove(day_r)
                    if ss.plan_thread_id and ss.plan_payload:
                        ss.plan_adj_request = (
                            f"I'll be able to study on {day_r} after all. "
                            "Please shift the content back to that day."
                        )
                        ss.plan_adjusting        = True
                        ss.pending_restore_study = None
                        show_plan_dialog()
                    else:
                        ss.pending_restore_study = None
                    st.rerun()
                if rb.button("Cancel", key="cal_restore_cancel"):
                    ss.pending_restore_study = None
                    st.rerun()

# ── Message renderer ───────────────────────────────────────────────────────
def render_messages():
    for idx, msg in enumerate(ss.messages):
        with st.chat_message(msg["role"]):

            if msg["type"] == "text":
                st.markdown(msg["content"])

            elif msg["type"] == "slider_form":
                st.markdown(msg["content"])
                if not ss.background_submitted:
                    with st.form("bg_form"):
                        scores = {}
                        for topic in ss.topics:
                            scores[topic] = st.select_slider(
                                topic,
                                options=list(SCORE_LABELS.keys()),
                                value=3,
                                format_func=lambda x: SCORE_LABELS[x],
                            )
                        if st.form_submit_button("Submit scores →", type="primary"):
                            ss.background_scores    = scores
                            ss.background_submitted = True
                            ss.pending              = {"action": "lpc_continue"}
                            add_msg("user", "✅ Background scores submitted.")
                            st.rerun()
                else:
                    st.success("✅ Background scores submitted — see sidebar.")

            elif msg["type"] == "hours_input":
                state = ss.q_states.get(f"hours_{idx}", {"answered": False, "value": 0.0})
                st.markdown(msg["content"])
                if not state["answered"]:
                    with st.form(f"hours_form_{idx}"):
                        h = st.number_input(
                            "Hours studied today:", min_value=0.0, max_value=24.0, step=0.5, value=0.0
                        )
                        if st.form_submit_button("Submit →", type="primary"):
                            state = {"answered": True, "value": h}
                            ss.q_states[f"hours_{idx}"] = state
                            ss.hours_today = h
                            ss.pending = {"action": "engagement_checkin"}
                            add_msg("user", f"I studied {h} hours today.")
                            st.rerun()
                else:
                    st.caption(f"Hours submitted: {state['value']}h")

            elif msg["type"] == "completion_check":
                state = ss.q_states.get(f"comp_{idx}", {"answered": False})
                st.markdown(msg["content"])
                if not state["answered"]:
                    c1, c2 = st.columns(2)
                    if c1.button("✅ Yes, completed", key=f"comp_yes_{idx}", type="primary"):
                        ss.q_states[f"comp_{idx}"] = {"answered": True, "completed": True}
                        add_msg("user", "Yes, I completed the module.")
                        ss.pending = {"action": "start_assessment"}
                        st.rerun()
                    if c2.button("🔄 Not yet", key=f"comp_no_{idx}"):
                        ss.q_states[f"comp_{idx}"] = {"answered": True, "completed": False}
                        add_msg("user", "Not yet — I need more time.")
                        ss.pending = {"action": "offer_adjustment"}
                        st.rerun()
                else:
                    completed = state.get("completed", False)
                    st.caption("Completed ✅" if completed else "Not yet completed 🔄")

            elif msg["type"] == "continue_check":
                state = ss.q_states.get(f"cont_{idx}", {"answered": False})
                st.markdown(msg["content"])
                if not state["answered"]:
                    c1, c2 = st.columns(2)
                    if c1.button("▶ Continue studying", key=f"cont_yes_{idx}", type="primary"):
                        ss.q_states[f"cont_{idx}"] = {"answered": True, "continue": True}
                        add_msg("user", "I'll continue studying.")
                        ss.pending = {"action": "engagement"}
                        st.rerun()
                    if c2.button("👋 Stop for today", key=f"cont_no_{idx}"):
                        ss.q_states[f"cont_{idx}"] = {"answered": True, "continue": False}
                        add_msg("user", "I'll stop for today.")
                        ss.pending = {"action": "manager_insights"}
                        st.rerun()
                else:
                    st.caption("Continuing ▶" if state.get("continue") else "Stopped for today 👋")

            elif msg["type"] == "exam_register":
                state = ss.q_states.get(f"reg_{idx}", {"answered": False})
                st.markdown(msg["content"])
                if not state["answered"]:
                    with st.form(f"exam_reg_form_{idx}"):
                        exam_date_input = st.date_input(
                            "Target exam date",
                            value=None,
                            min_value=datetime.date.today(),
                        )
                        cert_intent = st.text_input(
                            "What certification are you preparing for?",
                            placeholder="e.g. I am a Data Scientist and I want to prepare for DP-300",
                        )
                        if st.form_submit_button("Submit →", type="primary"):
                            if not cert_intent.strip():
                                st.error("Please describe the certification you want to pursue.")
                            else:
                                # Save exam date
                                if exam_date_input:
                                    ss.exam_date = exam_date_input.isoformat()
                                    save_learner_fields(exam_date=ss.exam_date)
                                # Dispatch to get cert + role from user's message
                                from agents.dispatcher import dispatch
                                from agents.manager_insights_agent import get_scope
                                learner = load_learner()
                                result  = dispatch(cert_intent)
                                parsed  = parse_json_safe(result.get("raw", "")) or {}
                                dp      = parsed.get("payload", {})
                                payload = {
                                    "role":         dp.get("role") or learner.get("role"),
                                    "certification":dp.get("certification") or learner.get("certification"),
                                    "team_id":      dp.get("team_id") or learner.get("team_id"),
                                    "learner_id":   ss.learner_id,
                                }
                                target_cert = payload.get("certification", "")
                                # Build recommendation message from certifications.json
                                with open("data/certifications.json") as _cf:
                                    _all_certs = json.load(_cf)
                                _cert_info = next(
                                    (c for c in _all_certs if c.get("certification_code") == target_cert),
                                    None,
                                )
                                if _cert_info:
                                    _roles_str = ", ".join(_cert_info.get("target_roles", []))
                                    _hours     = _cert_info.get("recommended_study_hours", "—")
                                    _validity  = _cert_info.get("validity_period", "—")
                                    _rec_msg = (
                                        f"Based on your goal, I recommend **{target_cert} — "
                                        f"{_cert_info['certification_name']}**.\n\n"
                                        f"This is a **{_cert_info.get('certification_level', '')}**-level "
                                        f"certification designed for: {_roles_str}. "
                                        f"It covers CI/CD pipelines, infrastructure as code, source control "
                                        f"automation, and continuous delivery with Azure.\n\n"
                                        f"**Recommended study hours:** {_hours}h · "
                                        f"**Validity:** {_validity}\n\n"
                                        f"Would you like to prepare for **{target_cert}**?"
                                    )
                                else:
                                    _rec_msg = (
                                        f"Based on your goal, I recommend preparing for **{target_cert}**.\n\n"
                                        f"Would you like to proceed with this certification?"
                                    )
                                # Check retirement warnings for this specific cert
                                scope = get_scope(payload)
                                for w in scope.get("warnings", []):
                                    if not target_cert or w.get("certification") == target_cert:
                                        add_msg("assistant", f"⚠️ {w.get('message', w)}")
                                ss.q_states[f"reg_{idx}"] = {"answered": True}
                                add_msg("user", cert_intent)
                                ss.reg_payload = payload
                                add_msg("assistant", _rec_msg, msg_type="cert_confirm",
                                        data={"cert_code": target_cert})
                                st.rerun()
                else:
                    st.caption("Exam registration submitted ✓")

            elif msg["type"] == "cert_confirm":
                state    = ss.q_states.get(f"cconf_{idx}", {"answered": False})
                data     = msg.get("data") or {}
                cert_code = data.get("cert_code", "")
                st.markdown(msg["content"])
                if not state["answered"]:
                    c1, c2 = st.columns(2)
                    if c1.button(f"✅ Yes, I want to prepare for {cert_code}",
                                 key=f"cconf_yes_{idx}", type="primary"):
                        ss.q_states[f"cconf_{idx}"] = {"answered": True, "confirmed": True}
                        save_learner_fields(certification=cert_code)
                        ss.target_cert = cert_code
                        add_msg("user", f"Yes, I want to prepare for {cert_code}.")
                        add_msg(
                            "assistant",
                            "Great! Would you like to do a quick background knowledge survey? "
                            "It helps personalise your study hours based on what you already know.",
                            msg_type="survey_confirm",
                        )
                        st.rerun()
                    if c2.button("🔄 Choose a different cert", key=f"cconf_no_{idx}"):
                        ss.q_states[f"cconf_{idx}"] = {"answered": True, "confirmed": False}
                        add_msg("user", "I'd like to choose a different certification.")
                        add_msg("assistant",
                                "No problem! Click **📝 Register Exam Preparation** in the sidebar "
                                "to start over with a different certification.")
                        st.rerun()
                else:
                    if state.get("confirmed"):
                        st.caption(f"Confirmed: {cert_code} ✅")
                    else:
                        st.caption("Cancelled 🔄")

            elif msg["type"] == "survey_confirm":
                state = ss.q_states.get(f"sconf_{idx}", {"answered": False})
                st.markdown(msg["content"])
                if not state["answered"]:
                    c1, c2 = st.columns(2)
                    if c1.button("📊 Yes, let's personalise", key=f"sconf_yes_{idx}", type="primary"):
                        ss.q_states[f"sconf_{idx}"] = {"answered": True, "survey": True}
                        add_msg("user", "Yes, I'd like to do the background survey.")
                        ss.pending = {"action": "lpc_start", "payload": ss.reg_payload}
                        ss.phase   = "lpc"
                        st.rerun()
                    if c2.button("⏭ Skip for now", key=f"sconf_no_{idx}"):
                        ss.q_states[f"sconf_{idx}"] = {"answered": True, "survey": False}
                        add_msg("user", "I'll skip the survey for now.")
                        # Build a default LPC output so SPG can proceed without background scores
                        with open("data/certifications.json") as _cf2:
                            _all_certs2 = json.load(_cf2)
                        _ci = next(
                            (c for c in _all_certs2
                             if c.get("certification_code") == ss.target_cert), {}
                        )
                        default_lpc = {
                            "status": "complete",
                            "recommended_certification": ss.target_cert,
                            "study_hours_multiplier": 1.0,
                            "adjusted_study_hours": _ci.get("recommended_study_hours", 40),
                            "background_summary": {},
                            "learning_resources": [],
                            "warnings": [],
                        }
                        ss.pending = {"action": "spg_generate", "parsed": default_lpc}
                        ss.phase   = "lpc"
                        st.rerun()
                else:
                    st.caption("Survey done 📊" if state.get("survey") else "Survey skipped ⏭")

            elif msg["type"] == "question":
                # [CHANGE 3] Questions now open in dialog — show placeholder only
                # This branch handles any legacy question messages in history
                q     = msg["data"] if isinstance(msg["data"], dict) else {}
                q_num = q.get("question_number", "?")
                st.caption(f"📝 Q{q_num} — (see dialog)")

# ── Pending action executor ────────────────────────────────────────────────
def execute_pending():
    action = ss.pending
    if not action:
        return
    ss.pending = None
    act = action["action"]

    # ── Restore plan thread (returning user) ───────────────────────────────
    if act == "restore_plan_thread":
        from agents.study_plan_generator import generate as spg_generate
        learner    = load_learner()
        lpc_output = learner.get("lpc_output") or {}
        payload = {
            "learner_id":                ss.learner_id,
            "recommended_certification": lpc_output.get("recommended_certification") or learner.get("certification"),
            "adjusted_study_hours":      lpc_output.get("adjusted_study_hours") or learner.get("adjusted_study_hours"),
            "background_summary":        lpc_output.get("background_summary", {}),
        }
        ss.plan_payload = payload
        with st.spinner("Restoring study plan session..."):
            result = spg_generate(payload)
        ss.plan_result    = result
        ss.plan_thread_id = result["thread_id"]
        ss.plan_agent_id  = result["agent_id"]
        ss.lpc_warnings   = lpc_output.get("warnings", [])
        ss.pending        = {"action": "engagement"}
        st.rerun()

    # ── LPC phase 1 start ──────────────────────────────────────────────────
    elif act == "lpc_start":
        from agents.learning_path_curator import start as lpc_start
        payload = action["payload"]
        with st.spinner("Starting Learning Path Curator..."):
            result = lpc_start(payload)
        ss.lpc_thread_id = result["thread_id"]
        ss.lpc_agent_id  = result["agent_id"]
        parsed           = parse_json_safe(result.get("raw", ""))
        ss.topics        = (parsed or {}).get("topics", [])
        add_msg(
            "assistant",
            "Please rate your familiarity with each topic (0–5). "
            "This helps personalise your study hours.",
            msg_type="slider_form",
        )
        st.rerun()

    # ── LPC phase 2 (after slider submission) ─────────────────────────────
    elif act == "lpc_continue":
        from agents.learning_path_curator import continue_session as lpc_continue
        with st.spinner("Building your learning path..."):
            result = lpc_continue(ss.lpc_thread_id, ss.lpc_agent_id, json.dumps(ss.background_scores))
        ss.lpc_result   = result
        parsed          = result.get("parsed") or {}
        cert            = parsed.get("recommended_certification")
        if cert:
            ss.target_cert = cert
        ss.lpc_warnings = parsed.get("warnings", [])
        for w in ss.lpc_warnings:
            msg = w.get("message", w) if isinstance(w, dict) else str(w)
            add_msg("assistant", f"⚠️ {msg}")

        adj_hours  = parsed.get("adjusted_study_hours", "—")
        multiplier = parsed.get("study_hours_multiplier", 1.0)
        resources  = parsed.get("learning_resources", [])
        summary    = (
            f"**Recommended certification:** `{cert}`\n\n"
            f"**Adjusted study hours:** {adj_hours}h (×{multiplier:.2f})\n\n"
        )
        if resources:
            summary += "**Learning resources:**\n"
            for r in resources:
                summary += f"- **{r.get('title')}**: {r.get('description', '')}\n"
        add_msg("assistant", summary)
        ss.pending = {"action": "spg_generate", "parsed": parsed}
        st.rerun()

    # ── Study plan generation ──────────────────────────────────────────────
    elif act == "spg_generate":
        from agents.study_plan_generator import generate as spg_generate
        lpc_parsed = action.get("parsed") or (ss.lpc_result or {}).get("parsed") or {}
        payload = {
            "learner_id":                ss.learner_id,
            "recommended_certification": lpc_parsed.get("recommended_certification"),
            "adjusted_study_hours":      lpc_parsed.get("adjusted_study_hours"),
            "background_summary":        lpc_parsed.get("background_summary", {}),
        }
        ss.plan_payload = payload
        with st.spinner("Generating your study plan..."):
            result = spg_generate(payload)
        ss.plan_result    = result
        ss.plan_thread_id = result["thread_id"]
        ss.plan_agent_id  = result["agent_id"]
        plan_parsed       = result.get("parsed") or {}
        plan_data         = result.get("plan_data") or {}
        weekly_plan       = plan_data.get("weekly_plan") or plan_parsed.get("weekly_plan_description", [])
        ss.unique_mods    = unique_modules(weekly_plan)
        save_learner_fields(
            study_plan=weekly_plan,
            lpc_output=lpc_parsed,
            current_module_index=0,
        )
        add_msg(
            "assistant",
            f"✅ Study plan ready!\n\n"
            f"**{plan_data.get('total_weeks')} weeks** · "
            f"**{plan_data.get('weekly_study_hours')}h/week** · "
            f"Completion: **{plan_data.get('projected_completion_date')}**\n\n"
            "View the full plan in the sidebar. Let's start your first module.",
        )
        ss.phase   = "session"
        ss.mod_idx = 0
        ss.pending = {"action": "engagement"}
        st.rerun()

    # ── Engagement: start of each module ──────────────────────────────────
    elif act == "engagement":
        learner = load_learner()
        mods    = ss.unique_mods or unique_modules(learner.get("study_plan", []))
        if not mods:
            add_msg("assistant", "No study plan found. Please complete setup first.")
            return

        # Exit: all modules passed
        skill_scores = learner.get("skill_module_scores") or {}
        ss.skill_scores = skill_scores
        if all_modules_passed(mods, skill_scores):
            add_msg("assistant", "🎉 All modules passed! You're ready for the exam.")
            ss.pending = {"action": "manager_insights"}
            st.rerun()
            return

        # Exit: time limit
        time_up, time_reason = check_time_limit(learner, ss.lpc_warnings)
        if time_up:
            add_msg("assistant", time_reason)
            ss.pending = {"action": "manager_insights"}
            st.rerun()
            return

        # Retirement warning (mirrors main.py)
        for w in ss.lpc_warnings:
            msg = w.get("message", w) if isinstance(w, dict) else str(w)
            if msg and "retiring" in msg.lower():
                add_msg("assistant", f"⚠️ Reminder: {msg}")

        mod_idx = learner.get("current_module_index", ss.mod_idx)
        ss.mod_idx = mod_idx
        if mod_idx >= len(mods):
            add_msg("assistant", "🎉 All modules complete!")
            ss.pending = {"action": "manager_insights"}
            st.rerun()
            return

        mod           = mods[mod_idx]
        ss.current_mod = mod
        exam_d        = days_until(ss.exam_date or learner.get("exam_date"))
        day_str       = f" ({exam_d} days until exam)" if exam_d is not None else ""

        add_msg(
            "assistant",
            f"**Module {mod_idx + 1}/{len(mods)}: {mod}**{day_str}\n\n"
            "How many hours did you study this module today?",
            msg_type="hours_input",
        )
        st.rerun()

    # ── After hours input: run engagement check-in ─────────────────────────
    elif act == "engagement_checkin":
        from agents.engagement_agent import check_in
        learner = load_learner()
        payload = {
            "learner_id":           ss.learner_id,
            "adjusted_study_hours": (
                ss.plan_payload.get("adjusted_study_hours") if ss.plan_payload
                else learner.get("adjusted_study_hours", 20)
            ),
            "weekly_plan":      learner.get("study_plan", []),
            "module_completed": False,
            "module_hours":     ss.hours_today,
        }
        with st.spinner("Checking in..."):
            result = check_in(payload)
        parsed  = result.get("parsed") or {}
        eng_msg = parsed.get("message") or result.get("raw", "")
        if eng_msg:
            add_msg("assistant", eng_msg)

        add_msg(
            "assistant",
            f"Have you completed the **{ss.current_mod}** module?",
            msg_type="completion_check",
        )
        st.rerun()

    # ── Offer plan adjustment (module not completed) ───────────────────────
    elif act == "offer_adjustment":
        add_msg(
            "assistant",
            "No problem! Type an adjustment request if you'd like to change your plan "
            "(e.g. *'I need more time on this module'*), or say **continue** to keep going.",
        )
        st.rerun()

    # ── Start assessment — [CHANGE 3] now opens dialog instead of chat msg ─
    elif act == "start_assessment":
        from agents.assessment_agent import start_module
        learner = load_learner()
        cert    = ss.target_cert or learner.get("certification", "AZ-204")
        mod     = ss.current_mod
        # Extract latest single score for the assessment agent (expects float or None)
        prior   = _latest_score(ss.skill_scores or {}, mod) or None
        payload = {
            "certification_code": cert,
            "module_name":        mod,
            "learner_id":         ss.learner_id,
            "max_questions":      5,
            "prior_module_score": prior,
        }
        with st.spinner(f"Loading assessment: {mod}..."):
            result = start_module(payload)

        ss.assess_thread_id = result["thread_id"]
        ss.assess_agent_id  = result["agent_id"]
        parsed              = result.get("parsed") or {}

        # [CHANGE 3] Store question in state and open dialog (no chat message)
        ss.current_question_data = parsed
        ss.current_eval          = None
        ss.assessment_complete   = False
        ss.assessment_summary    = None
        ss.question_dialog_open  = True

        # Add one text message to chat as an anchor
        add_msg("assistant", f"📝 Starting assessment: **{mod}**")
        st.rerun()

    # ── Auto-adjust plan after failed assessment ───────────────────────────
    elif act == "plan_adjust_fail":
        from agents.study_plan_generator import adjust as spg_adjust, generate as spg_generate
        adj_request = action["adj_request"]
        with st.spinner("Adjusting plan..."):
            if ss.plan_thread_id and ss.plan_payload:
                result = spg_adjust(ss.plan_thread_id, ss.plan_agent_id, adj_request, ss.plan_payload)
            else:
                result = spg_generate(ss.plan_payload or {"learner_id": ss.learner_id})
        ss.plan_result = result
        plan_data      = result.get("plan_data") or {}
        weekly_plan    = plan_data.get("weekly_plan", [])
        if weekly_plan:
            save_learner_fields(study_plan=weekly_plan)
            ss.unique_mods = unique_modules(weekly_plan)
        add_msg("assistant", "✅ Plan adjusted to focus on your weak areas.")
        add_msg("assistant", "Would you like to continue studying today?", msg_type="continue_check")
        st.rerun()

    # ── Manager insights (end of session) ─────────────────────────────────
    elif act == "manager_insights":
        from agents.manager_insights_agent import get_insights
        learner = load_learner()
        team_id = learner.get("team_id")
        if team_id:
            with st.spinner("Loading manager insights..."):
                insights = get_insights({"team_id": team_id})
            parsed = insights.get("parsed") or {}
            if parsed:
                summary = parsed.get("summary") or json.dumps(parsed, indent=2)
                add_msg("assistant", f"**Manager Insights — {team_id}**\n\n{summary}")
            else:
                add_msg("assistant", f"**Manager Insights — {team_id}**\n\n{insights.get('raw', '')}")
        add_msg("assistant", "Progress saved. See you next session! 👋")
        st.rerun()

# ── Login page ─────────────────────────────────────────────────────────────
def render_login():
    st.markdown(
        "<style>[data-testid^='stBaseButton-primary'] { background-color: rgb(30, 58, 95) !important; border-color: rgb(30, 58, 95) !important; }</style>",
        unsafe_allow_html=True,
    )
    _, col, _ = st.columns([1, 1.5, 1])
    with col:
        st.markdown("<br><br>", unsafe_allow_html=True)
        st.markdown("## 🎓 Certification Preparation Assistant")
        st.caption("Enterprise Learning Management · Your Personal Certification Coach")
        st.markdown("<br>", unsafe_allow_html=True)

        learner_id_input = st.text_input("Learner ID", placeholder="e.g. L-TEST-001")
        password         = st.text_input("Password", type="password", placeholder="Enter any password")
        st.markdown("<br>", unsafe_allow_html=True)

        if st.button("Sign in →", type="primary", use_container_width=True):
            if not learner_id_input.strip():
                st.error("Please enter your Learner ID.")
                return
            # Set learner_id before calling load_learner() — it reads from ss.learner_id
            ss.learner_id   = learner_id_input.strip()
            learner         = load_learner()
            ss.logged_in    = True
            # Display name comes from the learner's JSON record, fallback to learner ID
            ss.username     = learner.get("name") or learner_id_input.strip()
            ss.learner      = learner
            ss.exam_date    = learner.get("exam_date")
            ss.skill_scores = learner.get("skill_module_scores") or {}

            if learner.get("study_plan"):
                ss.unique_mods = unique_modules(learner.get("study_plan", []))
                ss.mod_idx     = learner.get("current_module_index", 0)
                ss.phase       = "session"
                ss.target_cert = (
                    (learner.get("lpc_output") or {}).get("recommended_certification")
                    or learner.get("certification")
                )
                add_msg(
                    "assistant",
                    f"Welcome back, **{ss.username}**! ✅ Resuming your study plan.\n\n"
                    f"**Certification:** `{ss.target_cert}` · "
                    f"**Module {ss.mod_idx + 1}/{len(ss.unique_mods)}:** "
                    f"*{ss.unique_mods[ss.mod_idx] if ss.mod_idx < len(ss.unique_mods) else 'All done!'}*",
                )
                ss.pending = {"action": "restore_plan_thread"}
            else:
                ss.phase = "chat"
                add_msg(
                    "assistant",
                    f"Welcome, **{ss.username}**! 👋 I'm here to help you prepare for your Azure certification.\n\n"
                    "Tell me your role and the certification you'd like to pursue — for example:\n"
                    "*\"I'm a Cloud Engineer and I want to prepare for AZ-204.\"*",
                )
            st.rerun()

# ── Chat input handler ─────────────────────────────────────────────────────
def handle_chat_input(user_input: str):
    add_msg("user", user_input)
    txt = user_input.strip().lower()

    if ss.phase == "chat":
        from agents.dispatcher import dispatch
        from agents.manager_insights_agent import get_scope
        with st.spinner("Routing your request..."):
            result = dispatch(user_input)
        parsed  = parse_json_safe(result.get("raw", "")) or {}
        route   = parsed.get("route", "learning_path_curator")
        reason  = parsed.get("reason", "")
        dp      = parsed.get("payload", {})
        payload = {
            "role":          dp.get("role") or ss.learner.get("role"),
            "certification": dp.get("certification") or ss.learner.get("certification"),
            "team_id":       dp.get("team_id") or ss.learner.get("team_id"),
            "learner_id":    ss.learner_id,
        }
        add_msg("assistant", f"Routing to **{route.replace('_', ' ').title()}** — {reason}")

        if route in ("learning_path_curator", "study_plan_generator"):
            scope = get_scope(payload)
            for w in scope.get("warnings", []):
                add_msg("assistant", f"⚠️ {w.get('message', w)}")
            ss.pending = {"action": "lpc_start", "payload": payload}

        elif route == "manager_insights_agent":
            scope = get_scope(payload)
            certs = scope.get("approved_certifications", [])
            add_msg("assistant", f"**Approved certifications for your team:** {', '.join(certs) or 'None found'}")

        else:
            add_msg("assistant", f"Route `{route}` not yet connected. Please try again.")

    elif ss.phase == "session" and txt in ("yes", "y", "retry"):
        ss.pending = {"action": "start_assessment"}

    elif ss.phase == "session" and "continue" in txt:
        ss.pending = {"action": "engagement"}

    elif ss.phase == "session" and any(kw in txt for kw in ("adjust", "change", "more time", "reschedule")):
        if ss.plan_thread_id and ss.plan_payload:
            from agents.study_plan_generator import adjust as spg_adjust
            with st.spinner("Adjusting plan..."):
                result = spg_adjust(ss.plan_thread_id, ss.plan_agent_id, user_input, ss.plan_payload)
            ss.plan_result = result
            add_msg("assistant", f"✅ Plan adjusted: _{user_input}_")
        else:
            add_msg("assistant", "No active plan session to adjust.")

    elif ss.phase == "session":
        add_msg(
            "assistant",
            "Type **yes** to start the assessment, **continue** for the next module, "
            "or describe an adjustment to your plan.",
        )

# ── Main render ────────────────────────────────────────────────────────────
if not ss.logged_in:
    render_login()
else:
    # Execute any pending agent action before rendering UI
    if ss.pending:
        execute_pending()

    render_sidebar()
    st.markdown("---")
    render_messages()

    # [CHANGE 3] Open question dialog if assessment is in progress
    if ss.question_dialog_open:
        show_question_dialog()

    if ss.get("plan_dialog_open"):
        ss.plan_dialog_open = False
        show_plan_dialog()

    user_input = st.chat_input("Type a message...")
    if user_input:
        handle_chat_input(user_input)
        st.rerun()