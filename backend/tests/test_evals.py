import json
from pathlib import Path
from agent.classifier import classify_intent
from db_connector.safety import SafetyChecker
from evals.metrics import recall_at_k, reciprocal_rank

EVAL_DIR = Path(__file__).parents[1] / "evals"

def _load(name):
    return json.loads((EVAL_DIR / name).read_text(encoding="utf-8"))

def test_golden_sql_intent_and_tools():
    for case in _load("golden_sql.json"):
        result = classify_intent(case["question"])
        assert result.intent == case["expected_intent"]
        assert set(case["required_tools"]).issubset(result.suggested_tools)

def test_safety_adversarial_corpus():
    checker = SafetyChecker()
    for case in _load("safety_adversarial.json"):
        if "sql_blocked" in case:
            assert checker.check(case["input"]).blocked is case["sql_blocked"]
        else:
            result = classify_intent(case["input"])
            assert (result.intent == "general_chat" and not result.suggested_tools) is case["blocked"]

def test_rag_eval_corpus_is_well_formed():
    cases = _load("rag_cases.json")
    assert cases and all(c["query"] and c["relevant_document_ids"] for c in cases)
    relevant = cases[0]["relevant_document_ids"]
    assert recall_at_k([relevant[0]], relevant) == 1.0
    assert reciprocal_rank(["irrelevant", relevant[0]], relevant) == 0.5
