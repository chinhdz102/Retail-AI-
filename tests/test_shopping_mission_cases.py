from __future__ import annotations

import json
from collections import Counter
from pathlib import Path
from types import SimpleNamespace

from backend.api.main import _should_preempt_legacy_lookup_for_shopping_mission
from backend.services.shopping_mission_routing import needs_shopping_goal_clarification
from backend.services.vietnamese_intent import has_specific_product_reference
from backend.shared.contracts import IntentType

ROOT = Path(__file__).resolve().parents[1]
DATASET_PATH = ROOT / "evaluation/cases/vietnamese_shopping_mission_heldout_v1_20261021.json"
V2_DATASET_PATH = ROOT / "evaluation/cases/vietnamese_shopping_mission_heldout_v2_20261021.json"
V3_DATASET_PATH = ROOT / "evaluation/cases/vietnamese_shopping_mission_heldout_v3_20261021.json"
CATALOG_PATH = ROOT / "data/winmart_products.json"


def _dataset() -> dict:
    return json.loads(DATASET_PATH.read_text(encoding="utf-8"))


def test_shopping_mission_holdout_has_100_product_name_free_multiturn_cases() -> None:
    dataset = _dataset()
    cases = dataset["cases"]

    assert dataset["split"] == "heldout"
    assert dataset["language_scope"] == "Vietnamese only"
    assert len(cases) >= 100
    assert len({case["case_id"] for case in cases}) == len(cases)
    assert all(case["user_never_names_product"] for case in cases)
    assert all(3 <= len(case["turns"]) <= 8 for case in cases)
    assert all(len(case["turns"]) == case["turn_count"] for case in cases)
    assert all(
        isinstance(turn.get("text"), str)
        and turn["text"].strip()
        and isinstance(turn.get("expect"), dict)
        for case in cases
        for turn in case["turns"]
    )


def test_holdout_covers_distinct_goals_and_requested_revision_behaviors() -> None:
    dataset = _dataset()
    cases = dataset["cases"]
    archetypes = Counter(case["archetype"] for case in cases)
    tags = Counter(tag for case in cases for tag in case["tags"])

    assert len(archetypes) >= 10
    assert min(archetypes.values()) >= 10
    for tag in (
        "budget_revision",
        "quantity_adjustment",
        "category_addition",
        "category_substitution",
        "existing_items",
        "missing_attribute",
        "regional_vietnamese",
        "asr_like",
        "incomplete_context",
        "no_product_names",
    ):
        assert tags[tag] > 0, tag


def test_product_name_free_claim_matches_current_snapshot() -> None:
    cases = _dataset()["cases"]
    products = json.loads(CATALOG_PATH.read_text(encoding="utf-8"))
    user_text = [turn["text"].casefold() for case in cases for turn in case["turns"]]

    for product in products:
        product_id = str(product.get("product_id", "")).casefold()
        if product_id:
            assert all(product_id not in text for text in user_text)

        product_name = " ".join(str(product.get("name", "")).casefold().split())
        if len(product_name) >= 16:
            assert all(product_name not in text for text in user_text), product_name

    accidental_product_queries = [
        (case["case_id"], turn["text"])
        for case in cases
        for turn in case["turns"]
        if has_specific_product_reference(turn["text"])
    ]
    assert accidental_product_queries == []


def test_existing_item_revisions_remove_a_need_the_customer_says_they_have() -> None:
    cases = [
        case
        for case in _dataset()["cases"]
        if "existing_items" in case["tags"] and case["archetype"] != "skincare"
    ]
    assert len(cases) >= 15

    for case in cases:
        final_turn = case["turns"][-1]
        utterance = final_turn["text"].casefold()
        expected_groups = final_turn["expect"]["need_category_groups"]
        existing_terms = final_turn["expect"].get("existing_items_any", [])
        assert existing_terms
        assert any(str(term).casefold() in utterance for term in existing_terms)
        assert len(expected_groups) < len(case["turns"][1]["expect"]["required_category_groups"])


def test_v3_heldout_is_new_four_turn_data_with_product_free_missions() -> None:
    dataset = json.loads(V3_DATASET_PATH.read_text(encoding="utf-8"))
    cases = dataset["cases"]
    prior_texts = {
        turn["text"].casefold()
        for path in (DATASET_PATH, V2_DATASET_PATH)
        for case in json.loads(path.read_text(encoding="utf-8"))["cases"]
        for turn in case["turns"]
    }

    assert dataset["split"] == "heldout_frozen"
    assert dataset["language_scope"] == "Vietnamese only"
    assert len(cases) == 100
    assert len({case["case_id"] for case in cases}) == 100
    assert all(len(case["turns"]) == 4 for case in cases)
    assert sum(case["user_never_names_product"] for case in cases) == 90
    assert sum("incomplete_context" in case["tags"] for case in cases) == 10
    assert all(
        turn["text"].casefold() not in prior_texts
        for case in cases
        for turn in case["turns"]
    )


def test_v3_mixed_fact_turn_preserves_the_mission_and_checks_catalog_price() -> None:
    cases = [
        case
        for case in json.loads(V3_DATASET_PATH.read_text(encoding="utf-8"))["cases"]
        if "mixed_factual_question" in case["tags"]
    ]

    assert len(cases) == 10
    for case in cases:
        fact_turn = case["turns"][2]
        followup = case["turns"][3]
        assert case["user_never_names_product"] is False
        assert fact_turn["expect"]["mission_plan_required"] is False
        assert fact_turn["expect"]["preserve_mission_state"] is True
        assert fact_turn["expect"]["fact_price_vnd"] == 15_900
        assert fact_turn["expect"]["grounded_product_ids_any"] == ["WIN_10307381"]
        assert followup["expect"]["mission_plan_required"] is True


def test_v3_incomplete_goal_cases_ask_for_context_before_creating_a_plan() -> None:
    cases = [
        case
        for case in json.loads(V3_DATASET_PATH.read_text(encoding="utf-8"))["cases"]
        if "incomplete_context" in case["tags"]
    ]

    assert len(cases) == 10
    for case in cases:
        first_turn = case["turns"][0]
        clarification_followup = case["turns"][1]
        assert first_turn["expect"]["mission_plan_required"] is False
        assert first_turn["expect"]["no_grounded_products"] is True
        assert first_turn["expect"]["clarification_terms_any"]
        assert not any(
            f"{count} người" in clarification_followup["text"].casefold()
            for count in range(1, 101)
        )
        assert "ngân sách" not in clarification_followup["text"].casefold()


def test_v3_expected_planner_and_fact_turns_follow_the_real_api_router() -> None:
    cases = json.loads(V3_DATASET_PATH.read_text(encoding="utf-8"))["cases"]
    for case in cases:
        active_before_turn = False
        for turn_index, turn in enumerate(case["turns"]):
            text = turn["text"]
            expect = turn["expect"]
            if "clarification_terms_any" in expect:
                assert needs_shopping_goal_clarification(text, active_mission=False)
                assert expect["mission_plan_required"] is False
                continue

            mission = (
                {"mission_id": "mission-test", "needs": [{"need_id": "need"}]}
                if active_before_turn
                else {}
            )
            record = SimpleNamespace(
                shopping=SimpleNamespace(
                    shopping_mission=mission,
                    constraints={},
                    current_intent=IntentType.RECOMMENDATION,
                ),
                last_grounded_product_id=None,
                last_grounded_product_name=None,
            )
            routed_to_planner = _should_preempt_legacy_lookup_for_shopping_mission(text, record)
            assert routed_to_planner is bool(expect.get("mission_plan_required", True)), (
                case["case_id"],
                turn_index + 1,
                text,
            )
            active_before_turn = True
