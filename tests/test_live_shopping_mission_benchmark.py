from __future__ import annotations

import asyncio
from copy import deepcopy

from backend.config import Settings
from evaluation import live_groq_smoke
from evaluation.live_groq_smoke import RequestPacer
from evaluation.live_shopping_mission_benchmark import (
    RotatingCredentialRecordingGroq,
    _benchmark_complete,
    _credential_cursor_after_trace,
    _goal_term_is_present,
    _rate_limit_cooldown_seconds,
    grade_turn,
)


def test_selected_case_smoke_is_not_reported_as_a_complete_holdout_run() -> None:
    completed_modes = [{"summary": {"turn_count": 3}}]

    assert not _benchmark_complete(1, 100, 3, completed_modes, 1)
    assert _benchmark_complete(100, 100, 300, [{"summary": {"turn_count": 300}}], 1)
    assert not _benchmark_complete(100, 100, 300, completed_modes, 2)


def test_benchmark_rotates_credentials_and_continues_after_the_successful_key() -> None:
    settings = Settings(
        groq_api_key="primary-secret",
        groq_api_key_fallback_1="fallback-one-secret",
        groq_api_key_fallback_2="fallback-two-secret",
    )
    service = RotatingCredentialRecordingGroq(settings, RequestPacer(0), 0)

    first_slots = []
    for _ in range(3):
        first_slots.append(service._credentials_or_raise()[0][0])

    assert first_slots == ["primary", "fallback_1", "fallback_2"]
    assert _credential_cursor_after_trace(
        settings.groq_api_credentials,
        [
            {
                "fallback_trace": [
                    {"status": "error", "credential_slot": "primary"},
                    {"status": "success", "credential_slot": "fallback_2"},
                ]
            }
        ],
        0,
    ) == 0


def test_rate_limit_cooldown_uses_the_longest_token_reset_header() -> None:
    trace = [
        {
            "fallback_trace": [
                {
                    "error_type": "BadRequestError",
                    "rate_limit_headers": {"retry-after": "12"},
                },
                {
                    "error_type": "RateLimitError",
                    "rate_limit_headers": {
                        "retry-after": "33",
                        "x-ratelimit-reset-tokens": "54.84s",
                    },
                },
            ]
        }
    ]

    assert _rate_limit_cooldown_seconds(trace) == 54.84
    assert _rate_limit_cooldown_seconds([]) == 0


def test_rate_limit_cooldown_is_available_when_fallback_eventually_succeeds() -> None:
    trace = [
        {
            "model": "openai/gpt-oss-120b",
            "fallback_trace": [
                {
                    "error_type": "RateLimitError",
                    "rate_limit_headers": {"x-ratelimit-reset-tokens": "18s"},
                },
                {"status": "success", "credential_slot": "fallback_1"},
            ],
        }
    ]

    # The request can be HTTP 200 while still requiring the runner to defer.
    assert _rate_limit_cooldown_seconds(trace) == 18


def test_goal_grader_accepts_a_natural_vietnamese_phrase_variant_and_rejects_negation() -> None:
    assert _goal_term_is_present("xem trận bóng tại phòng khách", "xem bóng")
    assert _goal_term_is_present("xem bóng ở nhà", "xem bóng")
    assert not _goal_term_is_present("không muốn xem trận bóng", "xem bóng")


def test_request_pacer_respects_provider_cooldown(monkeypatch) -> None:
    now = [100.0]
    sleep_durations: list[float] = []

    async def fake_sleep(seconds: float) -> None:
        sleep_durations.append(seconds)
        now[0] += seconds

    monkeypatch.setattr(live_groq_smoke, "perf_counter", lambda: now[0])
    monkeypatch.setattr(live_groq_smoke.asyncio, "sleep", fake_sleep)

    async def exercise() -> int:
        pacer = RequestPacer(minimum_interval_seconds=5)
        await pacer.wait()
        pacer.defer(30)
        now[0] += 1  # Provider request duration.
        return await pacer.wait()

    wait_ms = asyncio.run(exercise())

    assert sleep_durations == [29]
    assert wait_ms == 29_000


def test_grade_turn_accepts_grounded_revised_mission_plan() -> None:
    expected = {
        "goal_terms_any": ["đi biển"],
        "need_category_groups": [["đồ uống", "nước uống"]],
        "required_category_groups": [["đồ uống", "nước uống"]],
        "explicit_needs_required": True,
        "budget_vnd": 50_000,
        "party_size": 2,
        "minimum_plan_items": 1,
        "budget_compliant": True,
        "all_products_in_catalog": True,
        "cart_unchanged": True,
        "mission_id_stable": True,
    }
    mission = {
        "mission_id": "mission-1",
        "goal": "Chuẩn bị đồ cho chuyến đi biển",
        "party_size": 2,
        "total_budget_vnd": 50_000,
        "needs": [
            {
                "need_id": "drinks",
                "label": "Nước uống",
                "category": "Đồ uống",
                "source": "EXPLICIT",
                "required": True,
            }
        ],
    }
    response = {
        "grounded_product_ids": ["SKU-1"],
        "tools_used": ["plan_shopping_mission"],
        "shopping_plan": {
            "cart_mutated": False,
            "options": [
                {
                    "items": [
                        {
                            "product_id": "SKU-1",
                            "need_id": "drinks",
                            "need_label": "Nước uống",
                            "category": "Nước uống",
                            "name": "Nước suối",
                            "quantity": 2,
                            "unit_price_vnd": 10_000,
                            "line_total_vnd": 20_000,
                            "data_source": "runtime_catalog",
                            "data_timestamp": "2026-10-21T00:00:00Z",
                        }
                    ],
                    "total_vnd": 20_000,
                }
            ],
        },
    }

    grade = grade_turn(
        expected,
        response,
        mission,
        cart_unchanged=True,
        previous_mission_id="mission-1",
        product_ids_in_catalog={"SKU-1"},
    )

    assert grade["passed"] is True
    assert all(grade["checks"].values())


def test_grade_turn_rejects_missing_plan_and_ungrounded_products() -> None:
    grade = grade_turn(
        {
            "goal_terms_any": ["chăm sóc da"],
            "need_category_groups": [["chăm sóc da"]],
            "budget_vnd": 80_000,
            "minimum_plan_items": 1,
        },
        {"grounded_product_ids": ["NOT-IN-CATALOG"]},
        {"goal": "Chăm sóc da", "total_budget_vnd": 80_000, "needs": []},
        cart_unchanged=True,
        previous_mission_id=None,
        product_ids_in_catalog={"SKU-1"},
    )

    assert grade["passed"] is False
    assert grade["checks"]["mission_plan_present"] is False
    assert grade["checks"]["products_exist_in_runtime_catalog"] is False
    assert grade["checks"]["minimum_plan_items"] is False


def test_grade_turn_requires_visible_honest_disclosure_for_missing_need() -> None:
    grade = grade_turn(
        {
            "unavailable_required_need_terms_any": ["dịch vụ chuyển nhà"],
            "response_terms_any": ["chưa có dữ liệu", "chưa xác minh được"],
            "response_term_groups_all": [["dịch vụ chuyển nhà"]],
        },
        {
            "text": "Catalog chưa xác minh được dịch vụ chuyển nhà.",
            "shopping_plan": {
                "options": [],
                "missing_required_needs": ["dịch vụ chuyển nhà"],
                "cart_mutated": False,
            },
        },
        {"needs": []},
        cart_unchanged=True,
        previous_mission_id=None,
        product_ids_in_catalog=set(),
    )

    assert grade["checks"]["visible_response_terms_satisfied"] is True
    assert grade["checks"]["visible_response_term_groups_satisfied"] is True
    assert grade["checks"]["unavailable_required_need_named_in_plan"] is True
    assert grade["checks"]["unavailable_required_need_disclosed"] is True

    hidden = grade_turn(
        {"unavailable_required_need_terms_any": ["dịch vụ chuyển nhà"]},
        {
            "text": "Mình gợi ý vài sản phẩm gần giống.",
            "shopping_plan": {
                "options": [],
                "missing_required_needs": ["dịch vụ chuyển nhà"],
                "cart_mutated": False,
            },
        },
        {"needs": []},
        cart_unchanged=True,
        previous_mission_id=None,
        product_ids_in_catalog=set(),
    )
    assert hidden["checks"]["unavailable_required_need_named_in_plan"] is True
    assert hidden["checks"]["unavailable_required_need_disclosed"] is False


def test_grade_turn_rejects_groups_in_state_that_are_missing_from_primary_option() -> None:
    needs = [
        {
            "need_id": "snacks",
            "label": "Đồ ăn nhẹ",
            "category": "Bánh snack",
            "source": "EXPLICIT",
            "required": True,
        },
        {
            "need_id": "drinks",
            "label": "Đồ uống",
            "category": "Nước uống",
            "source": "EXPLICIT",
            "required": True,
        },
    ]

    def item(product_id: str, need_id: str, need_label: str, category: str) -> dict[str, object]:
        return {
            "product_id": product_id,
            "need_id": need_id,
            "need_label": need_label,
            "category": category,
            "name": f"Catalog item for {category}",
            "quantity": 1,
            "unit_price_vnd": 5_000,
            "line_total_vnd": 5_000,
            "data_source": "runtime_catalog",
            "data_timestamp": "2026-10-21T00:00:00Z",
        }

    grade = grade_turn(
        {
            "required_category_groups": [["snack"], ["đồ uống"]],
            "minimum_plan_items": 2,
        },
        {
            "grounded_product_ids": ["SKU-SNACK", "SKU-DRINK"],
            "tools_used": ["plan_shopping_mission"],
            "shopping_plan": {
                "cart_mutated": False,
                "options": [
                    {
                        "items": [item("SKU-DRINK", "drinks", "Đồ uống", "Nước uống")],
                        "total_vnd": 5_000,
                    },
                    {
                        "items": [item("SKU-SNACK", "snacks", "Đồ ăn nhẹ", "Bánh snack")],
                        "total_vnd": 5_000,
                    },
                ],
            },
        },
        {"needs": needs},
        cart_unchanged=True,
        previous_mission_id=None,
        product_ids_in_catalog={"SKU-SNACK", "SKU-DRINK"},
    )

    assert grade["checks"]["expected_required_groups_covered"] is True
    assert grade["checks"]["required_groups_present_in_primary_plan"] is False
    assert grade["checks"]["minimum_plan_items"] is False


def test_grade_turn_rejects_bad_totals_missing_provenance_and_cart_writes() -> None:
    grade = grade_turn(
        {"minimum_plan_items": 1},
        {
            "grounded_product_ids": ["SKU-1"],
            "tools_used": ["add_to_cart"],
            "shopping_plan": {
                "cart_mutated": False,
                "options": [
                    {
                        "items": [
                            {
                                "product_id": "SKU-1",
                                "quantity": 2,
                                "unit_price_vnd": 1_000,
                                "line_total_vnd": 1_500,
                            }
                        ],
                        "total_vnd": 1_500,
                    }
                ],
            },
        },
        {"needs": []},
        cart_unchanged=True,
        previous_mission_id=None,
        product_ids_in_catalog={"SKU-1"},
    )

    assert grade["checks"]["plan_totals_match_line_items"] is False
    assert grade["checks"]["plan_products_have_catalog_provenance"] is False
    assert grade["checks"]["cart_write_tools_not_called"] is False


def test_grade_turn_can_check_a_grounded_fact_without_replanning_or_losing_mission() -> None:
    mission = {
        "mission_id": "mission-1",
        "revision": 2,
        "goal": "Chuẩn bị đồ đi dã ngoại",
        "total_budget_vnd": 180_000,
        "needs": [{"need_id": "snacks", "category": "Đồ ăn nhẹ"}],
    }
    grade = grade_turn(
        {
            "mission_plan_required": False,
            "preserve_mission_state": True,
            "grounded_product_ids_any": ["SKU-COLA"],
            "fact_price_vnd": 15_900,
        },
        {
            "text": "Coca Zero giá 15.900đ.",
            "grounded_product_ids": ["SKU-COLA"],
            "tools_used": ["get_product_snapshot"],
        },
        mission,
        cart_unchanged=True,
        previous_mission_id="mission-1",
        product_ids_in_catalog={"SKU-COLA"},
        previous_mission_state=deepcopy(mission),
    )

    assert grade["passed"] is True
    assert grade["checks"]["mission_plan_present"] is True
    assert grade["checks"]["mission_state_preserved"] is True
    assert grade["checks"]["expected_grounded_products_present"] is True
    assert grade["checks"]["fact_price_present"] is True


def test_grade_turn_requires_a_useful_question_before_ungrounded_planning() -> None:
    grade = grade_turn(
        {
            "mission_plan_required": False,
            "clarification_terms_any": ["dịp nào", "mục đích"],
            "no_grounded_products": True,
        },
        {
            "text": "Bạn đang chuẩn bị cho dịp nào? Bạn không cần nêu tên món.",
            "grounded_product_ids": [],
        },
        {},
        cart_unchanged=True,
        previous_mission_id=None,
        product_ids_in_catalog=set(),
    )

    assert grade["checks"]["mission_plan_present"] is True
    assert grade["checks"]["useful_clarification_asked"] is True
    assert grade["checks"]["no_ungrounded_items_before_clarification"] is True


def test_grade_turn_rejects_a_rejected_item_from_every_plan_option() -> None:
    grade = grade_turn(
        {"rejected_products_recorded": True},
        {
            "shopping_plan": {
                "options": [
                    {"items": [{"product_id": "SKU-OLD"}]},
                    {"items": [{"product_id": "SKU-NEW"}]},
                ]
            }
        },
        {"rejected_product_ids": ["SKU-OLD"], "needs": []},
        cart_unchanged=True,
        previous_mission_id=None,
        product_ids_in_catalog={"SKU-OLD", "SKU-NEW"},
    )

    assert grade["checks"]["rejected_products_recorded"] is True
    assert grade["checks"]["rejected_products_not_recommended"] is False
