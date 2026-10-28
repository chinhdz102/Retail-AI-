import json
import threading
import time
from pathlib import Path

import pytest

import backend.services.retail_agent as retail_agent
from backend.products.catalog import ProductSnapshot, get_product_snapshot
from backend.products.repository import DemoProductRepository
from backend.products.snapshot_repository import SnapshotProductRepository
from backend.services.groq import GroqChatResult
from backend.services.retail_agent import (
    _catalog_tool_timeout_policy,
    _enforce_shopping_mission_call_semantics,
    _execute_tool_call,
    _unique_product_match_id,
    run_retail_agent,
)
from backend.services.retail_tools import ToolObservation
from backend.services.shopping_mission import build_shopping_mission_plan
from backend.shared.contracts import SessionState


class ScriptedGroq:
    def __init__(self, *results: GroqChatResult) -> None:
        self.results = list(results)
        self.calls = 0
        self.offered_tool_names: list[tuple[str, ...]] = []

    async def chat(self, messages, model=None, tools=None, tool_choice=None):
        self.calls += 1
        self.offered_tool_names.append(
            tuple(
                str(tool.get("function", {}).get("name", ""))
                for tool in tools or []
            )
        )
        return self.results.pop(0)


def tool_call(name: str, arguments: dict[str, object]) -> dict[str, object]:
    return {
        "id": f"call-{name}",
        "type": "function",
        "function": {
            "name": name,
            "arguments": json.dumps(arguments),
        },
    }


@pytest.mark.asyncio
async def test_shopping_mission_planner_uses_one_extended_tool_attempt(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    assert _catalog_tool_timeout_policy("plan_shopping_mission") == (15.0, 0)
    assert _catalog_tool_timeout_policy("search_products") == (4.0, 0)
    assert _catalog_tool_timeout_policy("recommend_products") == (5.0, 0)

    monkeypatch.setattr(retail_agent, "SHOPPING_MISSION_TOOL_TIMEOUT_SECONDS", 0.01)
    calls = 0

    def slow_mission_tool(name, arguments, previous_mission=None):
        nonlocal calls
        calls += 1
        time.sleep(0.05)
        return ToolObservation(content={"ok": True}, tool_name=name)

    monkeypatch.setattr(retail_agent, "execute_catalog_tool", slow_mission_tool)
    name, observation = await _execute_tool_call(
        tool_call("plan_shopping_mission", {"needs": []}),
        calls_executed=0,
    )

    assert name == "plan_shopping_mission"
    assert calls == 1
    assert observation.status == "TEMPORARY_FAILURE"
    assert observation.content["failure_type"] == "TimeoutError"


@pytest.mark.asyncio
async def test_search_products_times_out_truthfully_without_retry(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(retail_agent, "CATALOG_SEARCH_TIMEOUT_SECONDS", 0.01)
    calls = 0
    started = threading.Event()

    def slow_search_tool(name, arguments, **kwargs):
        nonlocal calls
        calls += 1
        started.set()
        time.sleep(0.05)
        return ToolObservation(content={"ok": True}, tool_name=name)

    monkeypatch.setattr(retail_agent, "execute_catalog_tool", slow_search_tool)
    name, observation = await _execute_tool_call(
        tool_call("search_products", {"query": "nước rửa tay"}),
        calls_executed=0,
        current_user_text="Tìm nước rửa tay.",
    )

    assert started.wait(timeout=0.5)
    assert name == "search_products"
    assert calls == 1
    assert observation.status == "TEMPORARY_FAILURE"
    assert observation.content["failure_type"] == "TimeoutError"


def test_mission_edit_preserves_existing_skus_and_softens_spice_preference() -> None:
    call = tool_call(
        "plan_shopping_mission",
        {
            "goal": "Help prepare snacks and drinks",
            "rejected_product_ids": ["SKU-OLD"],
            "hard_requirements": ["spicy=false", "non_spicy"],
            "exclusions": ["spicy=false", "non_spicy"],
            "needs": [
                {
                    "need_id": "snack",
                    "label": "Snack",
                    "category": "snack",
                    "product_query": "snack",
                    "source": "EXPLICIT",
                    "required": True,
                    "spicy": False,
                    "unverified_constraints": ["không cay", "spicy=false", "non_spicy"],
                    "preference_terms": [],
                },
                {
                    "need_id": "drink",
                    "label": "Đồ uống",
                    "category": "drink",
                    "source": "EXPLICIT",
                    "required": True,
                    "unverified_constraints": ["không cay", "non_spicy"],
                    "preference_terms": [],
                },
            ],
        },
    )
    previous = {
        "rejected_product_ids": ["SKU-REJECTED-EARLIER"],
        "proposed_items": [{"product_id": "SKU-OLD"}],
    }

    calls = _enforce_shopping_mission_call_semantics(
        [call],
        "Ưu tiên món không cay, đừng ép gợi ý thêm thành bắt buộc.",
        previous,
    )
    arguments = json.loads(calls[0]["function"]["arguments"])

    assert arguments["rejected_product_ids"] == ["SKU-REJECTED-EARLIER"]
    assert arguments["hard_requirements"] == []
    assert arguments["exclusions"] == []
    assert arguments["needs"][0]["spicy"] is None
    assert arguments["needs"][0]["preference_terms"] == ["không cay"]
    assert arguments["needs"][0]["unverified_constraints"] == []
    assert arguments["needs"][0]["source"] == "INFERRED"
    assert arguments["needs"][0]["required"] is False
    assert arguments["needs"][1]["preference_terms"] == []
    assert arguments["needs"][1]["spicy"] is None
    assert arguments["needs"][1]["unverified_constraints"] == []

    plan = build_shopping_mission_plan(arguments, DemoProductRepository())
    assert plan.content["options"]
    assert plan.content["missing_required_needs"] == []


def test_explicit_needs_are_required_and_remain_explicit_when_budget_is_revised() -> None:
    previous = {
        "mission_id": "mission-1",
        "needs": [
            {
                "need_id": "need-snack",
                "source": "EXPLICIT",
                "required": True,
            },
            {
                "need_id": "need-drink",
                "source": "EXPLICIT",
                "required": True,
            },
        ],
    }
    call = tool_call(
        "plan_shopping_mission",
        {
            "needs": [
                {
                    "need_id": "need-snack",
                    "label": "Đồ ăn nhẹ",
                    "category": "snack",
                    "source": "INFERRED",
                    "required": False,
                },
                {
                    "need_id": "need-drink",
                    "label": "Đồ uống",
                    "category": "drink",
                    "source": "INFERRED",
                    "required": False,
                },
            ]
        },
    )

    explicit = _enforce_shopping_mission_call_semantics(
        [call],
        (
            "Thêm vào kế hoạch đồ ăn nhẹ để chia nhau và đồ uống; "
            "đây là các nhu cầu mình muốn tính đến."
        ),
        previous,
    )
    revised = _enforce_shopping_mission_call_semantics(
        explicit,
        "Tăng ngân sách lên 180.000đ để có thêm lựa chọn.",
        {**previous, "needs": json.loads(explicit[0]["function"]["arguments"])["needs"]},
    )
    needs = json.loads(revised[0]["function"]["arguments"])["needs"]

    assert [(need["source"], need["required"]) for need in needs] == [
        ("EXPLICIT", True),
        ("EXPLICIT", True),
    ]


def test_confirmed_groups_are_required_but_other_existing_suggestions_stay_optional() -> None:
    text = (
        "Để đủ phần chính, mình cần đồ ăn nhẹ để chia nhau, đồ uống và giấy ăn. "
        "Các nhóm này mình xác nhận là cần; thứ bạn tự gợi ý thêm thì để tùy chọn."
    )
    needs = [
        {
            "need_id": "need-snack",
            "label": "Snack ăn vặt",
            "category": "Thực phẩm > Bánh kẹo > Snack",
            "source": "INFERRED",
            "required": False,
        },
        {
            "need_id": "need-drink",
            "label": "Đồ uống",
            "category": "Thực phẩm > Nước uống không cồn",
            "source": "INFERRED",
            "required": False,
        },
        {
            "need_id": "need-paper",
            "label": "Giấy ăn",
            "category": "Phi thực phẩm > Giấy ăn, giấy vệ sinh",
            "source": "INFERRED",
            "required": False,
        },
        {
            "need_id": "need-appetizer",
            "label": "Đồ ăn kèm",
            "category": "Thực phẩm > Thịt nguội",
            "product_query": "thịt nguội ăn kèm",
            "source": "EXPLICIT",
            "required": True,
        },
        {
            "need_id": "need-fruit",
            "label": "Trái cây",
            "category": "Thực phẩm > Trái cây nội địa",
            "source": "INFERRED",
            "required": False,
        },
    ]
    previous = {"needs": [{**need} for need in needs]}
    call = tool_call("plan_shopping_mission", {"needs": needs})

    result = _enforce_shopping_mission_call_semantics([call], text, previous)
    output = json.loads(result[0]["function"]["arguments"])

    assert {need["need_id"]: (need["source"], need["required"]) for need in output["needs"]} == {
        "need-snack": ("EXPLICIT", True),
        "need-drink": ("EXPLICIT", True),
        "need-paper": ("EXPLICIT", True),
        "need-appetizer": ("INFERRED", False),
        "need-fruit": ("INFERRED", False),
    }


def test_broad_snack_intent_does_not_promote_inferred_candy_to_required() -> None:
    text = (
        "Mình xác nhận cần có món nhâm nhi để chia nhau và thức uống; "
        "thứ gì bạn gợi ý thêm thì cứ để tùy chọn."
    )
    needs = [
        {
            "need_id": "snack",
            "label": "Món nhâm nhi",
            "category": "Thực phẩm > Bánh kẹo > Snack",
            "source": "INFERRED",
            "required": False,
        },
        {
            "need_id": "drink",
            "label": "Thức uống",
            "category": "Thực phẩm > Nước uống không cồn và hoa quả",
            "source": "INFERRED",
            "required": False,
        },
        {
            "need_id": "candy",
            "label": "Kẹo/chocolate",
            "category": "Thực phẩm > Bánh kẹo > Kẹo & Chocolate",
            "product_query": "kẹo chocolate",
            "source": "INFERRED",
            "required": False,
        },
    ]
    previous = {"needs": [{**need} for need in needs]}
    call = tool_call("plan_shopping_mission", {"needs": needs})

    result = _enforce_shopping_mission_call_semantics([call], text, previous)
    output = json.loads(result[0]["function"]["arguments"])

    assert {need["need_id"]: (need["source"], need["required"]) for need in output["needs"]} == {
        "snack": ("EXPLICIT", True),
        "drink": ("EXPLICIT", True),
        "candy": ("INFERRED", False),
    }

    explicit_candy = _enforce_shopping_mission_call_semantics(
        [tool_call("plan_shopping_mission", {"needs": needs})],
        "Mình xác nhận món nhâm nhi, thức uống và kẹo chocolate bắt buộc; "
        "gợi ý thêm thì cứ để tùy chọn.",
        previous,
    )
    explicit_needs = json.loads(explicit_candy[0]["function"]["arguments"])["needs"]
    assert next(need for need in explicit_needs if need["need_id"] == "candy")["required"] is True


def test_baby_context_does_not_turn_an_unmentioned_item_group_into_a_requirement() -> None:
    text = (
        "Em bé đi cùng gia đình trong chuyến thăm ông bà. Có khoảng 3 người và ngân sách 220.000đ."
    )
    call = tool_call(
        "plan_shopping_mission",
        {
            "needs": [
                {
                    "need_id": "baby_paper",
                    "label": "Khăn giấy/chăm sóc bé",
                    "category": "Phi thực phẩm > Hoá mỹ phẩm > "
                    "Mỹ phẩm, chăm sóc cá nhân > Đồ dùng chăm sóc mẹ và bé",
                    "product_query": "khăn giấy cho bé",
                    "source": "EXPLICIT",
                    "required": True,
                }
            ]
        },
    )

    result = _enforce_shopping_mission_call_semantics([call], text, None)
    need = json.loads(result[0]["function"]["arguments"])["needs"][0]

    assert need["source"] == "INFERRED"
    assert need["required"] is False


def test_open_ended_routine_keeps_inferred_components_optional_but_named_group_required() -> None:
    text = (
        "Tư vấn chăm sóc da mặt cơ bản; mình chưa biết cần những nhóm nào. "
        "Riêng bông tẩy trang thì mình xác nhận cần có."
    )
    needs = [
        {
            "need_id": "cleanser",
            "label": "Sữa rửa mặt",
            "category": "Phi thực phẩm > Hoá mỹ phẩm > Mỹ phẩm, chăm sóc cá nhân > Chăm sóc da",
            "product_query": "sữa rửa mặt",
            "source": "EXPLICIT",
            "required": True,
        },
        {
            "need_id": "moisturizer",
            "label": "Kem dưỡng ẩm",
            "category": "Phi thực phẩm > Hoá mỹ phẩm > Mỹ phẩm, chăm sóc cá nhân > Chăm sóc da",
            "product_query": "kem dưỡng ẩm da mặt",
            "source": "EXPLICIT",
            "required": True,
        },
        {
            "need_id": "cotton_pads",
            "label": "Miếng bông tẩy trang",
            "category": (
                "Phi thực phẩm > Hoá mỹ phẩm > Mỹ phẩm, chăm sóc cá nhân > Phụ kiện > "
                "Bông tẩy trang"
            ),
            "product_query": "bông tẩy trang",
            "source": "INFERRED",
            "required": False,
        },
    ]
    result = _enforce_shopping_mission_call_semantics(
        [tool_call("plan_shopping_mission", {"needs": needs})],
        text,
        None,
    )
    output = json.loads(result[0]["function"]["arguments"])["needs"]

    assert {need["need_id"]: (need["source"], need["required"]) for need in output} == {
        "cleanser": ("INFERRED", False),
        "moisturizer": ("INFERRED", False),
        "cotton_pads": ("EXPLICIT", True),
    }


def test_skincare_group_confirmation_does_not_make_sunscreen_required_until_named() -> None:
    needs = [
        {
            "need_id": "need_face_wash",
            "label": "Sữa rửa mặt",
            "category": "Phi thực phẩm > Hoá mỹ phẩm > Mỹ phẩm, chăm sóc cá nhân > Chăm sóc da",
            "product_query": "sữa rửa mặt",
            "source": "INFERRED",
            "required": False,
        },
        {
            "need_id": "need_moisturizer",
            "label": "Kem dưỡng ẩm",
            "category": "Phi thực phẩm > Hoá mỹ phẩm > Mỹ phẩm, chăm sóc cá nhân > Chăm sóc da",
            "product_query": "kem dưỡng ẩm da mặt",
            "source": "INFERRED",
            "required": False,
        },
        {
            "need_id": "need_sunscreen",
            "label": "Kem chống nắng",
            "category": "Phi thực phẩm > Hoá mỹ phẩm > Mỹ phẩm, chăm sóc cá nhân > Chăm sóc da",
            "product_query": "kem chống nắng da mặt",
            "source": "INFERRED",
            "required": False,
        },
        {
            "need_id": "need_cotton_pads",
            "label": "Miếng bông tẩy trang",
            "category": "Phụ kiện > Bông tẩy trang",
            "product_query": "bông tẩy trang",
            "source": "INFERRED",
            "required": False,
        },
    ]
    previous = {"needs": [dict(need) for need in needs]}
    confirmation = _enforce_shopping_mission_call_semantics(
        [tool_call("plan_shopping_mission", {"needs": needs})],
        (
            "Mình xác nhận cần có đồ làm sạch da mặt, sản phẩm dưỡng da mặt và "
            "miếng bông tẩy trang. Những nhóm này là bắt buộc; thứ gì bạn gợi ý thêm "
            "thì cứ để tùy chọn."
        ),
        previous,
    )
    confirmed_needs = json.loads(confirmation[0]["function"]["arguments"])["needs"]
    confirmed = {need["need_id"]: (need["source"], need["required"]) for need in confirmed_needs}
    assert confirmed == {
        "need_face_wash": ("EXPLICIT", True),
        "need_moisturizer": ("EXPLICIT", True),
        "need_sunscreen": ("INFERRED", False),
        "need_cotton_pads": ("EXPLICIT", True),
    }

    sunscreen_addition = _enforce_shopping_mission_call_semantics(
        confirmation,
        "Thêm bảo vệ da khi ra ngoài ban ngày vào nữa; các phần trước vẫn cần. "
        "Mức chi có thể tăng lên 240.000đ.",
        {"needs": confirmed_needs, "total_budget_vnd": 220_000},
    )
    revised_arguments = json.loads(sunscreen_addition[0]["function"]["arguments"])
    revised = {
        need["need_id"]: (need["source"], need["required"]) for need in revised_arguments["needs"]
    }
    assert revised["need_sunscreen"] == ("EXPLICIT", True)
    assert revised_arguments["total_budget_vnd"] == 240_000


@pytest.mark.parametrize("include_product_queries", [False, True])
def test_confirming_broad_skincare_keeps_inferred_steps_optional(
    include_product_queries: bool,
) -> None:
    category = (
        "Phi thực phẩm > Hoá mỹ phẩm > Mỹ phẩm, chăm sóc cá nhân > Chăm sóc da"
    )
    needs = [
        {
            "need_id": "broad",
            "label": "Chăm sóc da mặt",
            "category": category,
            "product_query": "chăm sóc da mặt" if include_product_queries else "",
            "source": "INFERRED",
            "required": False,
        },
        {
            "need_id": "cleanser",
            "label": "Sữa rửa mặt",
            "category": category,
            "product_query": "sữa rửa mặt" if include_product_queries else "",
            "source": "INFERRED",
            "required": False,
        },
        {
            "need_id": "moisturizer",
            "label": "Kem dưỡng ẩm",
            "category": category,
            "product_query": "kem dưỡng ẩm da mặt" if include_product_queries else "",
            "source": "INFERRED",
            "required": False,
        },
        {
            "need_id": "sunscreen",
            "label": "Kem chống nắng",
            "category": category,
            "product_query": "kem chống nắng da mặt" if include_product_queries else "",
            "source": "INFERRED",
            "required": False,
        },
    ]

    result = _enforce_shopping_mission_call_semantics(
        [tool_call("plan_shopping_mission", {"needs": needs})],
        "Mình xác nhận cần chăm sóc da mặt; các nhóm riêng bạn tự gợi ý thêm thì để tùy chọn.",
        None,
    )
    output = json.loads(result[0]["function"]["arguments"])["needs"]

    assert {need["need_id"]: need["required"] for need in output} == {
        "broad": True,
        "cleanser": False,
        "moisturizer": False,
        "sunscreen": False,
    }


def test_english_activity_only_plan_keeps_model_suggested_groups_optional() -> None:
    needs = [
        {
            "need_id": "snack",
            "label": "Picnic snacks",
            "category": "Thực phẩm > Bánh kẹo > Snack",
            "product_query": "snacks for picnic",
            "source": "EXPLICIT",
            "required": True,
        },
        {
            "need_id": "drink",
            "label": "Picnic drinks",
            "category": "Thực phẩm > Nước uống không cồn",
            "product_query": "drinks for picnic",
            "source": "EXPLICIT",
            "required": True,
        },
        {
            "need_id": "paper",
            "label": "Napkins for picnic",
            "category": "Phi thực phẩm > Giấy ăn",
            "product_query": "napkins for picnic",
            "source": "EXPLICIT",
            "required": True,
        },
    ]

    result = _enforce_shopping_mission_call_semantics(
        [tool_call("plan_shopping_mission", {"needs": needs})],
        "Plan supplies for a family picnic for four.",
        None,
    )
    output = json.loads(result[0]["function"]["arguments"])["needs"]

    assert all(need["source"] == "INFERRED" and not need["required"] for need in output)


def test_group_size_does_not_imply_shared_use() -> None:
    call = tool_call(
        "plan_shopping_mission",
        {
            "party_size": 3,
            "needs": [
                {
                    "need_id": "cleanser",
                    "label": "Sữa rửa mặt",
                    "category": (
                        "Phi thực phẩm > Hoá mỹ phẩm > Mỹ phẩm, chăm sóc cá nhân > Chăm sóc da"
                    ),
                    "product_query": "sữa rửa mặt",
                    "rationale": "Bước làm sạch cơ bản; dùng chung cho cả nhóm.",
                    "preference_terms": ["dùng được cho nhiều người"],
                    "source": "INFERRED",
                    "required": False,
                }
            ],
        },
    )

    result = _enforce_shopping_mission_call_semantics(
        [call], "Tư vấn chăm sóc da cho 3 người, ngân sách 220.000đ.", None
    )
    need = json.loads(result[0]["function"]["arguments"])["needs"][0]

    assert "dùng chung" not in need["rationale"].casefold()
    assert "dùng được cho nhiều người" not in need["preference_terms"]


def test_routine_context_is_not_promoted_to_unverifiable_product_filter() -> None:
    call = tool_call(
        "plan_shopping_mission",
        {
            "needs": [
                {
                    "need_id": "cleanser",
                    "label": "Sữa rửa mặt",
                    "category": (
                        "Phi thực phẩm > Hoá mỹ phẩm > Mỹ phẩm, chăm sóc cá nhân > Chăm sóc da"
                    ),
                    "product_query": "sữa rửa mặt",
                    "unverified_constraints": [
                        "dùng cho da mặt",
                        "dùng mỗi ngày",
                        "không chứa paraben",
                    ],
                    "source": "INFERRED",
                    "required": False,
                }
            ]
        },
    )

    result = _enforce_shopping_mission_call_semantics(
        [call], "Tư vấn chăm sóc da mặt cơ bản mỗi ngày.", None
    )
    need = json.loads(result[0]["function"]["arguments"])["needs"][0]

    assert need["unverified_constraints"] == ["không chứa paraben"]
    assert "dùng cho da mặt" in need["preference_terms"]
    assert "dùng mỗi ngày" in need["preference_terms"]


def test_optional_suggestions_instruction_works_in_english() -> None:
    call = tool_call(
        "plan_shopping_mission",
        {
            "needs": [
                {
                    "need_id": "snack",
                    "label": "Snack",
                    "category": "snack",
                    "source": "EXPLICIT",
                    "required": True,
                },
                {
                    "need_id": "fruit",
                    "label": "Fresh fruit",
                    "category": "fruit",
                    "product_query": "fresh fruit",
                    "source": "EXPLICIT",
                    "required": True,
                },
            ]
        },
    )

    result = _enforce_shopping_mission_call_semantics(
        [call],
        "Snacks are required; any other recommendations should stay optional.",
        None,
    )
    needs = json.loads(result[0]["function"]["arguments"])["needs"]

    assert [(need["source"], need["required"]) for need in needs] == [
        ("EXPLICIT", True),
        ("INFERRED", False),
    ]


def test_lexically_named_catalog_need_becomes_required_even_without_intent_alias() -> None:
    call = tool_call(
        "plan_shopping_mission",
        {
            "needs": [
                {
                    "need_id": "need-paper",
                    "label": "Giấy dùng khi đi đường",
                    "category": "Phi thực phẩm > Giấy ăn, giấy vệ sinh",
                    "product_query": "giấy ăn, giấy vệ sinh dùng khi đi đường",
                    "source": "INFERRED",
                    "required": False,
                }
            ]
        },
    )

    result = _enforce_shopping_mission_call_semantics(
        [call],
        "Thêm vào kế hoạch giấy dùng khi đi đường; đây là nhu cầu mình muốn tính đến.",
        None,
    )
    need = json.loads(result[0]["function"]["arguments"])["needs"][0]

    assert need["source"] == "EXPLICIT"
    assert need["required"] is True


@pytest.mark.parametrize(
    ("text", "label", "category", "product_query"),
    [
        (
            "Mình xác nhận cần có miếng bông tẩy trang; nhóm này là bắt buộc.",
            "Bông tẩy trang",
            "Phi thực phẩm > Hoá mỹ phẩm > Mỹ phẩm, chăm sóc cá nhân > Phụ kiện > Bông tẩy trang",
            "bông tẩy trang",
        ),
        (
            "Mình xác nhận cần khăn lau miệng; nhóm này là bắt buộc.",
            "Khăn lau miệng",
            "Phi thực phẩm > Hoá mỹ phẩm > Giấy và bông > Giấy ăn, giấy vệ sinh",
            "khăn lau miệng",
        ),
    ],
)
def test_exact_product_group_mentions_are_preserved_as_required(
    text: str,
    label: str,
    category: str,
    product_query: str,
) -> None:
    call = tool_call(
        "plan_shopping_mission",
        {
            "needs": [
                {
                    "need_id": "requested-group",
                    "label": label,
                    "category": category,
                    "product_query": product_query,
                    "source": "INFERRED",
                    "required": False,
                }
            ]
        },
    )

    result = _enforce_shopping_mission_call_semantics([call], text, None)
    need = json.loads(result[0]["function"]["arguments"])["needs"][0]

    assert need["source"] == "EXPLICIT"
    assert need["required"] is True


def test_activity_words_in_model_need_label_do_not_make_inferred_category_required() -> None:
    call = tool_call(
        "plan_shopping_mission",
        {
            "needs": [
                {
                    "need_id": "need-snack",
                    "label": "Snack xem bóng",
                    "category": "Thực phẩm > Bánh kẹo > Snack",
                    "product_query": "snack xem bóng đá",
                    "source": "EXPLICIT",
                    "required": True,
                }
            ]
        },
    )

    result = _enforce_shopping_mission_call_semantics(
        [call],
        ("Tư vấn giúp mình cho buổi xem bóng, khoảng 5 người; mình chưa biết cần những nhóm gì."),
        None,
    )
    need = json.loads(result[0]["function"]["arguments"])["needs"][0]

    assert need["source"] == "INFERRED"
    assert need["required"] is False


def test_existing_explicit_category_is_recorded_and_removed_from_purchase_plan() -> None:
    call = tool_call(
        "plan_shopping_mission",
        {
            "needs": [
                {
                    "need_id": "need-snack",
                    "label": "Đồ ăn nhẹ",
                    "category": "snack",
                    "source": "EXPLICIT",
                    "required": True,
                },
                {
                    "need_id": "need-drink",
                    "label": "Đồ uống",
                    "category": "drink",
                    "source": "EXPLICIT",
                    "required": True,
                },
            ]
        },
    )

    result = _enforce_shopping_mission_call_semantics(
        [call],
        "Mình đã có đồ ăn nhẹ, ghi nhận là có sẵn và bỏ khỏi danh sách mua.",
        None,
    )
    arguments = json.loads(result[0]["function"]["arguments"])

    assert arguments["existing_items"] == ["Đồ ăn nhẹ"]
    assert arguments["needs"][0]["satisfied_by_existing"] is True
    assert arguments["needs"][1].get("satisfied_by_existing", False) is False


def test_plan_revision_preserves_budget_and_party_size_until_user_changes_them() -> None:
    previous = {
        "mission_id": "mission-stable",
        "total_budget_vnd": 150_000,
        "party_size": 7,
        "needs": [],
    }
    call = tool_call(
        "plan_shopping_mission",
        {"total_budget_vnd": 900_000, "party_size": 9, "needs": []},
    )

    preserved = _enforce_shopping_mission_call_semantics(
        [call], "Thêm giấy dùng khi đi đường vào phương án.", previous
    )
    preserved_arguments = json.loads(preserved[0]["function"]["arguments"])
    assert preserved_arguments["total_budget_vnd"] == 150_000
    assert preserved_arguments["party_size"] == 7

    changed = _enforce_shopping_mission_call_semantics(
        preserved,
        "Tăng ngân sách lên 210.000đ và nhóm có 9 người.",
        previous,
    )
    changed_arguments = json.loads(changed[0]["function"]["arguments"])
    assert changed_arguments["total_budget_vnd"] == 210_000
    assert changed_arguments["party_size"] == 9


def test_explicit_soft_preference_is_recovered_when_model_omits_it() -> None:
    previous = {
        "total_budget_vnd": 200_000,
        "party_size": 4,
        "preferences": [],
        "needs": [],
    }
    call = tool_call(
        "plan_shopping_mission",
        {"preferences": [], "needs": [], "hard_requirements": []},
    )

    result = _enforce_shopping_mission_call_semantics(
        [call],
        "Ưu tiên món nào dễ chia cho mọi người với giữ trong ngân sách đó nha.",
        previous,
    )
    arguments = json.loads(result[0]["function"]["arguments"])

    assert arguments["preferences"] == ["chia cho moi nguoi"]
    assert arguments["total_budget_vnd"] == 200_000
    assert arguments["party_size"] == 4
    assert arguments["hard_requirements"] == []
    assert arguments["needs"] == []


def test_soft_preference_fallback_keeps_a_different_natural_preference() -> None:
    call = tool_call(
        "plan_shopping_mission",
        {"preferences": [], "needs": [], "hard_requirements": []},
    )

    result = _enforce_shopping_mission_call_semantics(
        [call],
        "Mình thích loại gọn nhẹ nha.",
        None,
    )

    assert json.loads(result[0]["function"]["arguments"])["preferences"] == ["gon nhe"]


def test_budget_revision_does_not_become_a_soft_product_preference() -> None:
    call = tool_call("plan_shopping_mission", {"preferences": [], "needs": []})

    result = _enforce_shopping_mission_call_semantics(
        [call],
        "Tăng ngân sách lên 180.000đ để có thêm lựa chọn.",
        {"preferences": [], "needs": []},
    )

    assert json.loads(result[0]["function"]["arguments"])["preferences"] == []


def test_explicit_alternative_request_rejects_prior_plan_skus() -> None:
    call = tool_call("plan_shopping_mission", {"needs": []})
    previous = {
        "rejected_product_ids": [],
        "proposed_items": [{"product_id": "SKU-OLD"}],
    }

    calls = _enforce_shopping_mission_call_semantics(
        [call], "Cho mình lựa chọn khác nhé.", previous
    )

    assert json.loads(calls[0]["function"]["arguments"])["rejected_product_ids"] == ["SKU-OLD"]


@pytest.mark.asyncio
async def test_terminal_snapshot_skips_redundant_final_llm_call() -> None:
    groq = ScriptedGroq(
        GroqChatResult(
            "",
            "qwen/qwen3.8-27b",
            [
                tool_call("search_products", {"query": "Coca Zero"}),
            ],
        ),
        GroqChatResult(
            "",
            "qwen/qwen3.8-27b",
            [
                tool_call(
                    "get_product_snapshot",
                    {
                        "product_id": "COKE_ZERO_330",
                        "fields": ["price_vnd"],
                    },
                ),
            ],
        ),
        GroqChatResult("This final call should not happen.", "qwen/qwen3.8-27b"),
    )

    result = await run_retail_agent(
        groq,
        [{"role": "user", "content": "Coca Zero price?"}],
        SessionState.BROWSING,
        candidate_query="coca zero",
    )

    assert groq.calls == 2
    assert result.text == ""
    assert result.observations[-1].response_mode == "DETERMINISTIC_FACT"
    assert result.observations[-1].terminal is True


@pytest.mark.asyncio
async def test_timestamp_snapshot_round_trips_through_agent_tool_message() -> None:
    groq = ScriptedGroq(
        GroqChatResult(
            "",
            "qwen/qwen3.8-27b",
            [tool_call("search_products", {"query": "Coca Zero"})],
        ),
        GroqChatResult(
            "",
            "qwen/qwen3.8-27b",
            [
                tool_call(
                    "get_product_snapshot",
                    {
                        "product_id": "COKE_ZERO_330",
                        "fields": ["data_timestamp"],
                    },
                )
            ],
        ),
    )

    result = await run_retail_agent(
        groq,
        [{"role": "user", "content": "Coca Zero price?"}],
        SessionState.BROWSING,
        candidate_query="coca zero",
    )

    assert groq.calls == 2
    assert result.observations[-1].content["product"]["data_timestamp"] == (
        "2026-08-31T00:00:00+00:00"
    )
    assert result.observations[-1].terminal is True


@pytest.mark.asyncio
async def test_agent_calls_product_knowledge_after_grounded_search() -> None:
    groq = ScriptedGroq(
        GroqChatResult(
            "",
            "qwen/qwen3.8-27b",
            [tool_call("search_products", {"query": "Coca Zero"})],
        ),
        GroqChatResult(
            "",
            "qwen/qwen3.8-27b",
            [
                tool_call(
                    "get_product_knowledge",
                    {
                        "product_id": "COKE_ZERO_330",
                        "attributes": ["benefits", "ingredients"],
                    },
                )
            ],
        ),
        GroqChatResult(
            "Catalog verifies the ingredients; it does not verify benefits.",
            "qwen/qwen3.8-27b",
        ),
    )

    result = await run_retail_agent(
        groq,
        [{"role": "user", "content": "Coca Zero có công dụng gì, thành phần ra sao?"}],
        SessionState.BROWSING,
        candidate_query="coca zero",
    )

    assert groq.calls == 3
    assert "get_product_knowledge" in groq.offered_tool_names[0]
    assert [observation.tool_name for observation in result.observations] == [
        "search_products",
        "get_product_knowledge",
    ]
    facts = {fact["attribute"]: fact for fact in result.observations[-1].content["facts"]}
    assert facts["benefits"]["status"] == "UNKNOWN"
    assert facts["ingredients"]["status"] == "VERIFIED"
    assert result.observations[-1].terminal is False
    assert result.text == "Catalog verifies the ingredients; it does not verify benefits."


def test_product_match_requires_specific_words_that_select_one_variant() -> None:
    candidates = (
        ProductSnapshot(
            product_id="LAY_110",
            name="Snack khoai tây Lay's Stax vị tự nhiên lon 110g",
            category="snack",
            price_vnd=35_000,
            stock=4,
        ),
        ProductSnapshot(
            product_id="LAY_150",
            name="Khoai tây Lay's vị tự nhiên classic 150g",
            category="snack",
            price_vnd=33_000,
            stock=4,
        ),
    )

    assert (
        _unique_product_match_id(
            "snack khoai tay lay s stax vi tu nhien lon 110g",
            candidates,
        )
        == "LAY_110"
    )
    assert _unique_product_match_id("lay s", candidates) is None
    assert _unique_product_match_id("lay s", (candidates[0],)) is None
    assert (
        _unique_product_match_id(
            "lay s stax 110g",
            (candidates[0],),
        )
        == "LAY_110"
    )


def test_product_match_uses_verified_unit_when_name_uses_packaging_abbreviation() -> None:
    candidates = (
        ProductSnapshot(
            product_id="ZERO_600",
            name="Nước Giải Khát Coca-Cola Zero Pet 600ml",
            category="drink",
            price_vnd=9_900,
            stock=5,
            uom="Chai",
        ),
        ProductSnapshot(
            product_id="ZERO_1500",
            name="Coca-Cola Zero 1500ml",
            category="drink",
            price_vnd=15_900,
            stock=5,
            uom="Chai",
        ),
    )
    assert _unique_product_match_id("coca zero chai 600ml", candidates) == "ZERO_600"
    assert _unique_product_match_id("coca zero chai", candidates) is None


def test_repeated_bim_bim_phrase_can_match_a_single_verified_snack() -> None:
    candidates = (
        ProductSnapshot(
            product_id="BIMBIM_280",
            name="Bim bim Poppedcorn quẩy soắn 280g",
            category="Bánh Snack",
            price_vnd=57_500,
            stock=3,
        ),
    )

    assert _unique_product_match_id("bim bim", candidates) == "BIMBIM_280"


def test_product_match_uses_product_alias_with_a_flavor_modifier() -> None:
    snack = get_product_snapshot("SNACK01")

    assert snack is not None
    assert _unique_product_match_id("bim bim vi tu nhien", (snack,)) == "SNACK01"


@pytest.mark.asyncio
async def test_snapshot_tool_cannot_read_an_unsearched_or_unmatched_product() -> None:
    groq = ScriptedGroq(
        GroqChatResult(
            "",
            "qwen/qwen3.8-27b",
            [
                tool_call(
                    "get_product_snapshot",
                    {"product_id": "COKE_ZERO_330", "fields": ["price_vnd"]},
                )
            ],
        )
    )

    result = await run_retail_agent(
        groq,
        [{"role": "user", "content": "Coca Zero price?"}],
        SessionState.BROWSING,
        candidate_query="pepsi zero",
    )

    assert result.observations[0].status == "DENIED"
    assert result.products == ()
    assert result.tools_used == ()


@pytest.mark.asyncio
async def test_empty_search_result_is_terminal_without_followup_llm_call() -> None:
    groq = ScriptedGroq(
        GroqChatResult(
            "",
            "qwen/qwen3.8-27b",
            [
                tool_call("search_products", {"query": "not-a-real-retail-product-9382"}),
            ],
        ),
        GroqChatResult("This followup call should not happen.", "qwen/qwen3.8-27b"),
    )

    result = await run_retail_agent(
        groq,
        [{"role": "user", "content": "Find an unknown product."}],
        SessionState.BROWSING,
    )

    assert groq.calls == 1
    assert result.observations[-1].content["count"] == 0
    assert result.observations[-1].response_mode == "DETERMINISTIC_ERROR"
    assert result.observations[-1].terminal is True


@pytest.mark.asyncio
async def test_recommendation_is_sellable_and_terminal_in_one_llm_call() -> None:
    groq = ScriptedGroq(
        GroqChatResult(
            "",
            "qwen/qwen3.8-27b",
            [
                tool_call("recommend_products", {"query": "MILK01"}),
            ],
        ),
        GroqChatResult("This followup call should not happen.", "qwen/qwen3.8-27b"),
    )

    result = await run_retail_agent(
        groq,
        [{"role": "user", "content": "Gợi ý sản phẩm MILK01 cho tôi."}],
        SessionState.BROWSING,
    )

    assert groq.calls == 1
    observation = result.observations[-1]
    assert observation.content["count"] == 0
    assert observation.response_mode == "DETERMINISTIC_RECOMMENDATION"
    assert observation.terminal is True


@pytest.mark.asyncio
async def test_explicit_natural_category_is_enforced_if_model_omits_it() -> None:
    groq = ScriptedGroq(
        GroqChatResult(
            "",
            "qwen/qwen3.8-27b",
            [
                tool_call(
                    "recommend_products",
                    {
                        "price_constraint": {"operator": "LT", "value_vnd": 20000},
                        "spicy": False,
                        "limit": 3,
                    },
                ),
            ],
        )
    )

    result = await run_retail_agent(
        groq,
        [{"role": "user", "content": "Tìm món ăn vặt không cay dưới hai chục nghìn."}],
        SessionState.BROWSING,
    )

    assert [product.product_id for product in result.products] == ["SNACK01"]
    assert "category" in result.observations[0].content["items"][0]["matched_constraints"]


@pytest.mark.asyncio
async def test_lookup_tool_choice_is_not_overridden_by_message_substrings() -> None:
    groq = ScriptedGroq(
        GroqChatResult(
            "",
            "qwen/qwen3.8-27b",
            [
                tool_call("search_products", {"query": "MILK01", "in_stock": False}),
            ],
        ),
        GroqChatResult("The product exists in the catalog.", "qwen/qwen3.8-27b"),
    )

    result = await run_retail_agent(
        groq,
        [{"role": "user", "content": "Không cần gợi ý, chỉ tra mặt hàng hết hạn MILK01."}],
        SessionState.BROWSING,
    )

    assert groq.calls == 2
    assert result.observations[0].tool_name == "search_products"
    assert [product.product_id for product in result.observations[0].products] == ["MILK01"]


@pytest.mark.asyncio
@pytest.mark.parametrize("tool_name", ["search_products", "recommend_products"])
async def test_agent_passes_original_turn_so_tool_enforces_omitted_budget(
    monkeypatch: pytest.MonkeyPatch,
    tool_name: str,
) -> None:
    repository = SnapshotProductRepository(
        Path(__file__).resolve().parents[1] / "data" / "winmart_products.json"
    )
    execute = retail_agent.execute_catalog_tool

    def execute_with_test_snapshot(name, arguments, **kwargs):
        return execute(name, arguments, repository=repository, **kwargs)

    monkeypatch.setattr(retail_agent, "execute_catalog_tool", execute_with_test_snapshot)
    query = "Mình cần hai chai nước rửa tay, tổng cộng không quá một trăm hai chục nghìn."
    groq = ScriptedGroq(
        GroqChatResult(
            "",
            "qwen/qwen3.8-27b",
            [tool_call(tool_name, {"query": "nước rửa tay", "limit": 5})],
        ),
        GroqChatResult("Mình tìm thấy vài lựa chọn trong ngân sách.", "qwen/qwen3.8-27b"),
    )

    result = await run_retail_agent(
        groq,
        [{"role": "user", "content": query}],
        SessionState.BROWSING,
    )

    observation = result.observations[-1]
    assert observation.status == "SUCCESS"
    assert observation.products
    assert all(product.price_vnd * 2 <= 120_000 for product in observation.products)
    assert observation.content[
        "budget_constraint" if tool_name == "search_products" else "constraints"
    ]


@pytest.mark.asyncio
async def test_agent_stops_and_asks_when_multi_item_budget_scope_is_ambiguous(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repository = SnapshotProductRepository(
        Path(__file__).resolve().parents[1] / "data" / "winmart_products.json"
    )
    execute = retail_agent.execute_catalog_tool

    def execute_with_test_snapshot(name, arguments, **kwargs):
        return execute(name, arguments, repository=repository, **kwargs)

    monkeypatch.setattr(retail_agent, "execute_catalog_tool", execute_with_test_snapshot)
    groq = ScriptedGroq(
        GroqChatResult(
            "",
            "qwen/qwen3.8-27b",
            [tool_call("recommend_products", {"query": "nước rửa tay", "limit": 5})],
        ),
        GroqChatResult("This followup call should not happen.", "qwen/qwen3.8-27b"),
    )

    result = await run_retail_agent(
        groq,
        [
            {
                "role": "user",
                "content": "Mình cần hai chai nước rửa tay dưới sáu chục nghìn.",
            }
        ],
        SessionState.BROWSING,
    )

    assert groq.calls == 1
    observation = result.observations[-1]
    assert observation.status == "DATA_UNAVAILABLE"
    assert observation.content["clarification_code"] == "budget_scope"
    assert observation.products == ()
