"""
DEV TOOL — resets L-TEST-* learners to a clean state before each test session.
Never touches real learner records (L-1001 etc.).
"""

import json
from pathlib import Path

DATA_DIR = Path(__file__).parent / "data"

SYSTEM_FIELDS = {
    "hours_studied": 0,
    "practice_score_avg": None,
    "exam_outcome": None,
    "skill_module_scores": None,
}


def reset_test_learners():
    path = DATA_DIR / "learner_performance.json"
    with open(path) as f:
        learners = json.load(f)

    reset_count = 0
    for learner in learners:
        if learner["learner_id"].startswith("L-TEST-"):
            for field, default in SYSTEM_FIELDS.items():
                learner[field] = default
            reset_count += 1

    with open(path, "w") as f:
        json.dump(learners, f, indent=2)

    print(f"Reset {reset_count} test learner(s).")


if __name__ == "__main__":
    reset_test_learners()