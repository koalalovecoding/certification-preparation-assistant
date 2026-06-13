"""
Entry point — session-based learning loop.

Each run is one session. The loop continues until:
  1. All modules are passed (final_score >= 80% across all modules)
  2. Exam date is tomorrow or today (time's up)
  3. Certification retirement date is tomorrow or today
  4. User chooses to exit for the day

LPC + Study Plan Generator run only on first session (no saved plan).
Subsequent sessions load the saved plan and resume from the current module.

Use L-TEST-* IDs for development; run dev_reset.py to restore clean state.
"""

import json
import questionary
from datetime import date as Date
from pathlib import Path
from agents.dispatcher import dispatch
from agents.manager_insights_agent import get_scope, get_insights
from agents.learning_path_curator import start, continue_session
from agents.study_plan_generator import generate, adjust as adjust_plan
from agents.engagement_agent import check_in
from agents.assessment_agent import start_module, next_question, complete_module, calculate_final_score

DATA_DIR = Path(__file__).parent / "data"
LEARNER_ID = "L-TEST-001"


# ── Data helpers ───────────────────────────────────────────────────────────

def load_learner(learner_id: str) -> dict | None:
    with open(DATA_DIR / "learner_performance.json") as f:
        learners = json.load(f)
    return next((l for l in learners if l["learner_id"] == learner_id), None)


def load_work_signals(learner_id: str) -> dict | None:
    with open(DATA_DIR / "work_activity_signals.json") as f:
        signals = json.load(f)
    return next((s for s in signals if s["learner_id"] == learner_id), None)


def save_learner_fields(learner_id: str, **fields) -> None:
    """
    Updates one or more fields in the learner's record in learner_performance.json.
    Called to persist session state (exam_date, study_plan, current_module_index, etc.).
    """
    with open(DATA_DIR / "learner_performance.json") as f:
        learners = json.load(f)
    for learner in learners:
        if learner["learner_id"] == learner_id:
            learner.update(fields)
            break
    with open(DATA_DIR / "learner_performance.json", "w") as f:
        json.dump(learners, f, indent=2)


# ── Date helpers ───────────────────────────────────────────────────────────

def _days_until(date_str: str | None) -> int | None:
    """Returns days from today to date_str (ISO format). None if not set."""
    if not date_str:
        return None
    return (Date.fromisoformat(date_str) - Date.today()).days


def _check_time_limit(profile: dict, lpc_warnings: list) -> tuple[bool, str]:
    """
    Checks whether exam date or certification retirement date has been reached.
    Returns (time_is_up: bool, reason: str).

    Triggers when:
    - Today >= exam_date - 1 day (exam is tomorrow or today)
    - Today >= retirement_date - 1 day for the target certification
    """
    exam_days = _days_until(profile.get("exam_date"))
    if exam_days is not None and exam_days <= 1:
        label = "today" if exam_days <= 0 else "tomorrow"
        return True, f"⏰ Your exam is {label}! Time to rest and do a final review."

    # Check retirement deadline from LPC warnings
    for warning in lpc_warnings:
        # Warning format: {"certification": "AZ-204", "message": "... retiring on YYYY-MM-DD ..."}
        msg = warning.get("message", "") if isinstance(warning, dict) else str(warning)
        if "retiring on" in msg:
            try:
                retire_date_str = msg.split("retiring on")[1].strip().split(".")[0].strip()
                retire_days = _days_until(retire_date_str)
                if retire_days is not None and retire_days <= 1:
                    label = "today" if retire_days <= 0 else "tomorrow"
                    return True, f"⚠️  The certification retires {label}. No point continuing — consider an alternative."
            except (IndexError, ValueError):
                pass

    return False, ""


# ── Module helpers ─────────────────────────────────────────────────────────

def _unique_modules(weekly_plan: list[dict]) -> list[str]:
    """
    Returns ordered list of unique module names from the weekly plan.
    The weekly plan can have multiple entries per module (split across weeks),
    so we deduplicate while preserving order.
    """
    seen = []
    for entry in weekly_plan:
        if entry["focus"] not in seen:
            seen.append(entry["focus"])
    return seen


def _all_modules_passed(unique_mods: list[str], skill_module_scores: dict | None) -> bool:
    """
    Returns True if every unique module has a score >= 80%.
    Used to detect overall completion.
    """
    if not skill_module_scores:
        return False
    return all(
        skill_module_scores.get(mod, 0) >= 80.0
        for mod in unique_mods
    )


# ── Print helpers (unchanged from previous version) ────────────────────────

def _print_question(parsed: dict) -> None:
    """
    Prints a question in human-readable format.
    Called whenever status == "asking".
    """
    q_num = parsed.get("question_number", "?")
    max_q = parsed.get("max_questions", "?")
    difficulty = parsed.get("difficulty", "").upper()
    question = parsed.get("question", "")
    options = parsed.get("options", {})
    source = parsed.get("source", "")

    print(f"\n{'─'*60}")
    print(f"  Question {q_num}/{max_q}  [Difficulty Level: {difficulty}]")
    print(f"{'─'*60}")
    print(f"\n  {question}\n")
    for key in ["A", "B", "C", "D"]:
        if key in options:
            print(f"  {key}) {options[key]}")
    if source:
        print(f"\n  Source: {source}")


def _print_evaluation(parsed: dict) -> None:
    """
    Prints human-readable feedback after each answer using fields returned by assessment_agent:
    - Correct: encouragement + one-line explanation (knowledge summary)
    - Incorrect: encouragement (consolation) + correct answer + explanation +
                 knowledge_point + study_recommendation
    Pauses with Enter before moving to the next question.
    """
    correct = parsed.get("correct", False)
    correct_answer = parsed.get("correct_answer", "")
    encouragement = parsed.get("encouragement", "")
    explanation = parsed.get("explanation", "")
    knowledge_point = parsed.get("knowledge_point")
    study_recommendation = parsed.get("study_recommendation")
    source = parsed.get("source", "")

    print()
    if correct:
        print(f"  ✅ {encouragement or 'Correct! Great job.'}")
        if explanation:
            print(f"  💡 {explanation}")
    else:
        print(f"  ❌ {encouragement or 'Not quite — keep going!'}")
        print(f"     The correct answer is {correct_answer}.")
        if explanation:
            print(f"  💡 Why: {explanation}")
        if knowledge_point:
            print(f"  📌 Key concept: {knowledge_point}")
        if study_recommendation:
            print(f"  📖 Study next: {study_recommendation}")
        elif source:
            print(f"  📖 Review: {source}")

    input("\n  Press Enter for the next question...")


def _print_final_summary(parsed: dict) -> None:
    """
    Prints the module assessment summary with a clear pass/fail result and advice.
    Called when status == "complete".
    """
    module_name = parsed.get("module_name", "")
    questions_asked = parsed.get("questions_asked", 0)
    correct_answers = parsed.get("correct_answers", 0)
    score = parsed.get("score_percentage", 0.0)
    weakest = parsed.get("weakest_topic")
    summary = parsed.get("summary", "")
    passed = score >= 80.0

    print(f"\n{'='*60}")
    print(f"  MODULE COMPLETE: {module_name}")
    print(f"{'='*60}")
    print(f"\n  Score: {score}%  ({correct_answers}/{questions_asked} correct)\n")

    if summary:
        print(f"  {summary}\n")

    if passed:
        print("  🎉 Passed! You're ready to move on to the next module.")
    else:
        print("  📚 Not passed (threshold: 80%).")
        if weakest:
            print(f"  Weakest area: {weakest}")
        print("  Recommendation: review the weak areas above and retake this module before moving on.")


# ── Assessment sub-flow (extracted for reuse in loop) ─────────────────────

def _run_assessment(learner_id: str, cert_code: str, module_name: str) -> float | None:
    """
    Runs the full assessment for one module.
    Returns the module score (float) if completed, or None if something went wrong.

    Extracted from main() so the session loop can call it cleanly per module.
    """
    print(f"\n{'='*60}")
    print("ASSESSMENT AGENT")
    print(f"{'='*60}")
    print(f"\nAssessing module: {module_name}")

    assessment_payload = {
        "certification_code": cert_code,
        "module_name": module_name,
        "learner_id": learner_id,
        "max_questions": 5,
    }

    assessment = start_module(assessment_payload)
    if assessment["parsed"]:
        _print_question(assessment["parsed"])
    else:
        print(f"\n{assessment['raw']}")

    answer_choices = ["A", "B", "C", "D"]
    thread_id = assessment["thread_id"]
    agent_id = assessment["agent_id"]
    status = assessment["status"]
    result_q = assessment

    # Single-step protocol: after each answer, agent returns "evaluated" containing
    # both the evaluation feedback AND the next question (in next_question field).
    # No separate "next" message needed — evaluation and next question arrive together.
    while status == "asking":
        answer = questionary.select("Your answer:", choices=answer_choices).ask()
        result_q = next_question(thread_id, agent_id, answer)
        status = result_q["status"]
        thread_id = result_q["thread_id"]
        agent_id = result_q["agent_id"]

        if status == "evaluated" and result_q["parsed"]:
            # Show per-question evaluation feedback
            _print_evaluation(result_q["parsed"])

            # Last question: evaluated contains complete_summary instead of next_question
            summary = result_q["parsed"].get("complete_summary")
            if summary:
                _print_final_summary(summary)
                result_q["parsed"]["status"] = "complete"
                result_q["parsed"]["score_percentage"] = summary.get("score_percentage", 0)
                status = "complete"
            else:
                # Non-last question: show next question embedded in response
                nq = result_q["parsed"].get("next_question")
                if nq:
                    _print_question(nq)
                    status = "asking"
                else:
                    print(f"\n{result_q['raw']}")
                    break

        elif status == "complete" and result_q["parsed"]:
            # Fallback: agent returned complete directly
            _print_final_summary(result_q["parsed"])

        else:
            print(f"\n{result_q['raw'] if not result_q['parsed'] else json.dumps(result_q['parsed'], indent=2)}")

    # Write score if complete
    if result_q["parsed"] and result_q["parsed"].get("status") == "complete":
        module_score = result_q["parsed"].get("score_percentage", 0)
        complete_module(learner_id, module_name, module_score)
        print(f"\nModule score written: {module_name} = {module_score}%")
        return module_score

    return None


# ── Main ───────────────────────────────────────────────────────────────────

def main():
    print(f"\n{'='*60}")
    print("ENTERPRISE LEARNING CERTIFICATION SYSTEM")
    print(f"{'='*60}")

    learner_id = LEARNER_ID
    profile = load_learner(learner_id)
    if not profile:
        print(f"Learner '{learner_id}' not found in learner_performance.json.")
        return

    signals = load_work_signals(learner_id)

    print(f"\nWelcome back, {profile['role']}!")
    print(f"  Team        : {profile.get('team_id') or 'None'}")
    print(f"  Certification: {profile.get('certification') or 'auto'}")
    if signals:
        print(f"  Meetings/wk : {signals['meeting_hours_per_week']} hrs")
        print(f"  Focus/wk    : {signals['focus_hours_per_week']} hrs")
        print(f"  Pref. slot  : {signals['preferred_learning_slot']}")

    # ── First-run only: ask for exam date ──────────────────────────────────
    # exam_date is persisted to learner_performance.json so it's only asked once.
    if not profile.get("exam_date"):
        exam_date_str = input(
            "\nEnter your target exam date (YYYY-MM-DD), or press Enter to skip: "
        ).strip()
        if exam_date_str:
            save_learner_fields(learner_id, exam_date=exam_date_str)
            profile["exam_date"] = exam_date_str

    exam_days = _days_until(profile.get("exam_date"))
    if exam_days is not None:
        print(f"\n  📅 Today: {Date.today()}  |  Exam in {exam_days} day(s): {profile['exam_date']}")

    # ── First-run only: LPC + Study Plan Generator ─────────────────────────
    # On subsequent sessions, the saved plan is loaded from learner_performance.json.
    # This avoids re-running expensive LPC/SPG calls every session.
    weekly_plan = profile.get("study_plan")
    lpc_output = profile.get("lpc_output") or {}
    cert_code = profile.get("certification")
    plan_thread_id = None   # only valid within this session (not persisted)
    plan_agent_id = None    # only valid within this session (not persisted)
    study_payload = None

    if not weekly_plan:
        # ── Phase 1: Learning Path Curator — background scoring ────────────
        payload = {
            "learner_id": learner_id,
            "role": profile["role"],
            "team_id": profile.get("team_id"),
            "certification": profile.get("certification"),
        }

        choices = [
            "0 — No experience",
            "1 — Heard of it",
            "2 — Basic understanding",
            "3 — Somewhat familiar",
            "4 — Comfortable / use occasionally",
            "5 — Confident / use regularly",
        ]

        session = start(payload)
        print(f"\nAgent:\n{session['raw']}")

        topics = []
        try:
            cleaned = (
                session["raw"].strip()
                .removeprefix("```json")
                .removeprefix("```")
                .removesuffix("```")
                .strip()
            )
            topics = json.loads(cleaned).get("topics", [])
        except json.JSONDecodeError:
            pass

        if not topics:
            print("Could not parse topics. Enter scores manually.")
            user_response = input("Learner: ")
        else:
            print("\nRate your familiarity with each topic:\n")
            scores = {}
            for topic in topics:
                answer = questionary.select(topic, choices=choices).ask()
                scores[topic] = int(answer[0])
            user_response = json.dumps(scores)
            print(f"\nSubmitted scores: {user_response}")

        # ── Phase 2: Learning Path Curator — learning path output ──────────
        lpc_result = continue_session(session["thread_id"], session["agent_id"], user_response)
        if lpc_result["parsed"]:
            print(f"\nLearning Path:\n{json.dumps(lpc_result['parsed'], indent=2)}")
        else:
            print(f"\nLearning Path:\n{lpc_result['raw']}")

        if not (lpc_result["status"] == "complete" and lpc_result["parsed"]):
            print("\n--- Learning Path incomplete. Cannot continue. ---")
            return

        lpc_output = lpc_result["parsed"]
        cert_code = lpc_output.get("recommended_certification") or cert_code

        # ── Phase 3: Study Plan Generator ─────────────────────────────────
        study_payload = {
            "learner_id": learner_id,
            "recommended_certification": cert_code,
            "adjusted_study_hours": lpc_output.get("adjusted_study_hours"),
            "background_summary": lpc_output.get("background_summary", {}),
        }
        if signals:
            study_payload["work_signals"] = signals

        print(f"\n{'='*60}")
        print("STUDY PLAN GENERATOR")
        print(f"{'='*60}")

        plan = generate(study_payload)
        if plan["parsed"]:
            print(f"\nStudy Plan:\n{json.dumps(plan['parsed'], indent=2)}")
        else:
            print(f"\nStudy Plan:\n{plan['raw']}")

        # Keep thread alive for in-session adjustments
        plan_thread_id = plan["thread_id"]
        plan_agent_id = plan["agent_id"]

        # ── Phase 4: Initial dynamic adjustment (optional) ─────────────────
        adjustment = input("\nRequest an adjustment (or press Enter to skip): ").strip()
        if adjustment:
            result_adj = adjust_plan(plan_thread_id, plan_agent_id, adjustment, study_payload)
            if result_adj["parsed"]:
                print(f"\nAdjusted Plan:\n{json.dumps(result_adj['parsed'], indent=2)}")
            else:
                print(f"\nAdjusted Plan:\n{result_adj['raw']}")
            # Update plan data after adjustment
            if result_adj.get("plan_data"):
                plan = result_adj

        weekly_plan = plan["plan_data"].get("weekly_plan", [])

        # Persist plan and LPC output so subsequent sessions skip LPC/SPG
        save_learner_fields(
            learner_id,
            study_plan=weekly_plan,
            lpc_output=lpc_output,
            current_module_index=0,
        )
        profile["current_module_index"] = 0

    else:
        # Subsequent session: reconstruct study_payload from saved data
        study_payload = {
            "learner_id": learner_id,
            "recommended_certification": cert_code,
            "adjusted_study_hours": lpc_output.get("adjusted_study_hours") or profile.get("adjusted_study_hours"),
            "background_summary": lpc_output.get("background_summary", {}),
        }
        if signals:
            study_payload["work_signals"] = signals
        print(f"\n✅ Resuming from saved study plan.")

    if not weekly_plan:
        print("No study plan available. Cannot continue.")
        return

    unique_mods = _unique_modules(weekly_plan)
    lpc_warnings = lpc_output.get("warnings", [])

    # ── SESSION LOOP ───────────────────────────────────────────────────────
    # Each iteration is one module's Engagement + Assessment cycle.
    # The user can exit at the end of any iteration ("continue today?" = No).
    while True:

        # ── Exit condition 1: time's up ────────────────────────────────────
        time_up, time_reason = _check_time_limit(profile, lpc_warnings)
        if time_up:
            print(f"\n{time_reason}")
            break

        # 每次 session 开始时显示 retirement 警告，提醒用户截止日期
        for warning in lpc_warnings:
            msg = warning.get("message", "") if isinstance(warning, dict) else str(warning)
            if msg:
                print(f"\n⚠️  {msg}")

        # ── Exit condition 2: all modules passed ───────────────────────────
        profile = load_learner(learner_id)  # reload to get latest scores
        if _all_modules_passed(unique_mods, profile.get("skill_module_scores")):
            print("\n🎉 All modules passed! You're ready for the exam. Good luck!")
            break

        # ── Get current module ─────────────────────────────────────────────
        current_index = profile.get("current_module_index", 0)
        if current_index >= len(unique_mods):
            print("\n🎉 All modules complete!")
            break

        current_module = unique_mods[current_index]
        exam_days = _days_until(profile.get("exam_date"))

        print(f"\n{'='*60}")
        print(f"SESSION — Module {current_index + 1}/{len(unique_mods)}: {current_module}")
        if exam_days is not None:
            print(f"Days until exam: {exam_days}")
        print(f"{'='*60}")

        # ── Ask hours studied today (always, regardless of completion) ─────
        # engagement_agent.py will record these hours even if module is not done.
        hours_input = input(
            f"\nHow many hours did you study '{current_module}' today? (Enter 0 if none): "
        ).strip()
        try:
            hours_today = float(hours_input)
        except ValueError:
            hours_today = 0.0

        # ── Engagement check-in ────────────────────────────────────────────
        print(f"\n{'='*60}")
        print("ENGAGEMENT AGENT")
        print(f"{'='*60}")

        completed = questionary.confirm(
            f"Have you completed the '{current_module}' module?"
        ).ask()

        engagement_payload = {
            "learner_id": learner_id,
            "adjusted_study_hours": study_payload.get("adjusted_study_hours"),
            "weekly_plan": weekly_plan,
            "module_completed": completed,
            "module_hours": hours_today,  # always pass today's hours, not just on completion
        }
        if signals:
            engagement_payload["work_signals"] = signals

        engagement_result = check_in(engagement_payload)
        if engagement_result["parsed"]:
            print(f"\nEngagement:\n{json.dumps(engagement_result['parsed'], indent=2)}")
        else:
            print(f"\nEngagement:\n{engagement_result['raw']}")

        # ── Module completion gate ─────────────────────────────────────────
        # Assessment is only available if the learner confirmed completing the module.
        # Otherwise, offer plan adjustment and move to the "continue today?" prompt.
        if not completed:
            print("\n--- Module not yet completed. Assessment skipped. ---")
            wants_adjustment = questionary.confirm(
                "Would you like to adjust your study plan (e.g. more time, different schedule)?"
            ).ask()
            if wants_adjustment:
                adjustment_request = input("Describe your adjustment: ").strip()
                if adjustment_request:
                    if plan_thread_id and plan_agent_id:
                        # Within same session: use existing thread for adjustment
                        result_adj = adjust_plan(
                            plan_thread_id, plan_agent_id, adjustment_request, study_payload
                        )
                    else:
                        # New session (thread expired): re-generate plan with failure context
                        result_adj = generate(study_payload)

                    if result_adj["parsed"]:
                        print(f"\nAdjusted Plan:\n{json.dumps(result_adj['parsed'], indent=2)}")
                        # Save updated plan
                        updated_plan = result_adj.get("plan_data", {}).get("weekly_plan")
                        if updated_plan:
                            weekly_plan = updated_plan
                            unique_mods = _unique_modules(weekly_plan)
                            save_learner_fields(learner_id, study_plan=weekly_plan)
                    else:
                        print(f"\nAdjusted Plan:\n{result_adj['raw']}")

        else:
            # ── Assessment ─────────────────────────────────────────────────
            module_score = _run_assessment(learner_id, cert_code, current_module)

            if module_score is not None:
                final = calculate_final_score(learner_id, cert_code)

                if module_score >= 80.0:
                    # Pass: advance to next module
                    current_index += 1
                    save_learner_fields(learner_id, current_module_index=current_index)
                    profile["current_module_index"] = current_index
                    print(f"\n✅ Module passed! Moving to module {current_index + 1}/{len(unique_mods)}.")
                else:
                    # Fail: offer plan adjustment focused on weak areas
                    weakest = final.get("weakest_module", current_module)
                    print(f"\n📚 Let's strengthen your plan around: {weakest}")
                    adj_request = f"Focus more on {weakest} — learner failed this module."

                    if plan_thread_id and plan_agent_id:
                        # Within same session: adjust via existing thread
                        result_adj = adjust_plan(
                            plan_thread_id, plan_agent_id, adj_request, study_payload
                        )
                    else:
                        # New session: re-generate plan (historical failure is in learner JSON)
                        result_adj = generate(study_payload)

                    if result_adj["parsed"]:
                        print(f"\nAdjusted Plan:\n{json.dumps(result_adj['parsed'], indent=2)}")
                        updated_plan = result_adj.get("plan_data", {}).get("weekly_plan")
                        if updated_plan:
                            weekly_plan = updated_plan
                            unique_mods = _unique_modules(weekly_plan)
                            save_learner_fields(learner_id, study_plan=weekly_plan)
                    else:
                        print(f"\nAdjusted Plan:\n{result_adj['raw']}")

        # ── Continue today? ────────────────────────────────────────────────
        # User exit condition: learner decides to stop for the day.
        # Progress is already saved; next session will resume from current_module_index.
        continue_today = questionary.confirm(
            "\nWould you like to continue studying today?"
        ).ask()
        if not continue_today:
            print("\nProgress saved. See you next session! 👋")
            break

    # ── Manager Insights (always shown at end of session) ──────────────────
    team_id = profile.get("team_id")
    if team_id:
        print(f"\n{'='*60}")
        print(f"MANAGER INSIGHTS — {team_id}")
        print(f"{'='*60}")
        insights = get_insights({"team_id": team_id})
        if insights.get("parsed"):
            print(json.dumps(insights["parsed"], indent=2))
        else:
            print(insights.get("raw", insights))


if __name__ == "__main__":
    main()