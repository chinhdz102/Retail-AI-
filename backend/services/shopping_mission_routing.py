from __future__ import annotations

import re
import unicodedata


def _normalize(text: str) -> str:
    decomposed = unicodedata.normalize("NFD", text.casefold().replace("đ", "d"))
    unaccented = "".join(
        character for character in decomposed if unicodedata.category(character) != "Mn"
    )
    return " ".join(unaccented.split())


_OPEN_ENDED_SHOPPING_CUES = re.compile(
    r"\b(?:mua gi|nen mua|can mua|can gi|can chuan bi|chuan bi gi|"
    r"giup minh chon|giup toi chon|giup minh len ke hoach|"
    r"tu van giup (?:minh|toi|tui)? can (?:nhung )?nhom gi|"
    r"tu van (?:gi|mua|cho)|goi y (?:gi|cho)|bat dau cham soc|"
    r"can (?:nhung )?nhom gi|chua biet ten (?:mon|hang|san pham) cu the|"
    r"chua ro can (?:nhung )?nhom|chua biet can gi|chua biet nen mua gi|"
    r"khong biet can gi|can chuan bi (?:nhung )?gi|chuan bi (?:nhung )?gi|"
    r"chua biet nen bat dau|chua biet bat dau tu dau|"
    r"len phuong an|tu van tu nhu cau|tu van tu van de|"
    r"nen uu tien(?: nhu cau)? (?:nao|gi|truoc)|chon thu tu uu tien|thu tu uu tien|"
    r"chua co san pham cu the|chua biet ten hang|"
    r"what should i buy|what do i need|help me (?:shop|choose|plan|prepare)|"
    r"what to (?:buy|pack|bring)|shopping plan|shopping list)\b"
)
_BUDGET_AMOUNT_CUE = re.compile(r"\b\d[\d.,]*\s*(?:k|nghin|ngan|trieu|vnd|d|dong|₫)\b")
_EXPLICIT_PRICE_QUESTION_CUE = re.compile(
    r"\b(?:gia\s*(?:ban|la|bao nhieu|bao tien|may dong|may ngan|may nghin|"
    r"duoi|tren|cua|nay|sao|the nao|\d[\d.,]*)|price|cost|how much|bao nhieu tien|"
    r"bao tien|may dong|may ngan|may nghin|bao gia)\b"
)
_SHOPPING_ACTIVITY_CUE = re.compile(
    r"\b(?:toi nay|hom nay|cuoi tuan|den choi|co khach|sinh nhat|dam cuoi|"
    r"sap di|chuyen di|di du lich|di bien|di picnic|cam trai|di cong tac|xem phim|"
    r"da ngoai|ngoai cong vien|ve que|dua be|nam hoc|di hoc|buoi hop|hop van phong|"
    r"don vao|chuyen vao|chuyen nha|nha moi|phong moi|phong tro moi|"
    r"do thiet yeu|vat dung toi thieu|"
    r"bua sang(?: cho gia dinh| ngay mai)?|an sang|bua an|nau an|"
    r"don khach|tiep khach|khach den nha|don nguoi than|tham ong ba|"
    r"qua nha nguoi than|be qua nha nguoi than|"
    r"ve sinh (?:nha|can nha|nha moi)|lam sach nha|don dep nha|don can bep|don bep|"
    r"phong tam|hoc tap|dung cu hoc tap|truoc gio hoc|cho moi nguoi|"
    r"buoi dao tao|dao tao noi bo|don dep|sua sang|"
    r"friends visiting|friends coming|friends over|guests coming|hosting|"
    r"trip|travel|vacation|camping|picnic|movie night|party|birthday|wedding|"
    r"moving in|new apartment|back to school|school year|office meeting|"
    r"family breakfast|getting ready for|preparing for|planning a)\b"
)
_SHOPPING_PROBLEM_CUE = re.compile(
    r"\bda\b.{0,28}\b(?:kho|nhay cam|dau|mun)\b|"
    r"\b(?:dry|sensitive|oily)\s+skin\b|"
    r"\bskin\b.{0,24}\b(?:dry|sensitive|oily|irritated)\b|"
    r"\b(?:dry|oily|sensitive)\s+(?:hair|scalp)\b|"
    r"\b(?:hair|scalp)\b.{0,24}\b(?:dry|oily|sensitive|itchy)\b"
)
_NEEDS_DISCOVERY_CUE = re.compile(
    r"\b(?:cham soc da(?: mat)?|bat dau skincare|starting skincare|"
    r"cham soc rang mieng|ve sinh nha cua|don nha|household shopping|"
    r"what should i buy|what do i need|help me choose|help me plan|"
    r"chua ro can (?:nhung )?nhom|chua biet can gi|chua biet nen mua gi|"
    r"khong biet can gi|can chuan bi (?:nhung )?gi|chuan bi (?:nhung )?gi|"
    r"chua biet nen bat dau|chua biet bat dau tu dau|"
    r"len phuong an|tu van tu nhu cau|tu van tu van de|"
    r"nen uu tien(?: nhu cau)? (?:nao|gi|truoc)|chon thu tu uu tien|thu tu uu tien|"
    r"chua co san pham cu the|chua biet ten hang)\b"
)
_MISSION_LEVEL_PLANNING_CUE = re.compile(
    r"\b(?:len phuong an|len ke hoach mua sam|ke hoach mua sam|shopping plan|shopping list|"
    r"help me plan|help me prepare|tu van tu nhu cau|tu van tu van de|"
    r"tu van theo nhu cau|tu van (?:cac|nhung) nhom|goi y (?:cac|nhung) nhu cau|"
    r"tu van giup (?:minh|toi|tui)? can (?:nhung )?nhom gi|"
    r"can (?:nhung )?nhom gi|chua biet ten (?:mon|hang|san pham) cu the|"
    r"tinh phan chuan bi|cac nhom can lo|chua can chon theo ten hang|"
    r"nen uu tien(?: nhu cau)? (?:nao|gi|truoc)|chon thu tu uu tien|thu tu uu tien|"
    r"chua biet nen bat dau|chua biet bat dau tu dau|chua biet can gi|"
    r"chua biet (?:phai )?chuan bi (?:nhung )?gi|chua biet nen mua gi|"
    r"what should i buy|what do i need|what to (?:buy|pack|bring))\b"
)
_MISSION_REVISION_CUES = re.compile(
    r"\b(?:them|bo|doi|thay|thay the|giam|tang|uu tien|thich|giu lai|giu nguyen|"
    r"ha tran|giam tran|tang tran|nang tran|nang ngan sach|"
    r"ha ngan sach|ngan sach|chi tieu|con lai|mua them|so luong|"
    r"con thieu|da co|co san|mua lai|khong can mua|phuong an|ke hoach|goi y|"
    r"phuong an khac|goi y khac|loai khac|thay doi|replace|swap|"
    r"remove|add|increase|decrease|budget|remaining|another option|"
    r"different option|more options)\b"
)
_MISSION_HARD_CONSTRAINT_CUE = re.compile(
    r"\b(?:dieu kien(?: nay)?(?: la)? bat buoc|yeu cau(?: nay)?(?: la)? bat buoc|"
    r"hard requirement|hard constraint|"
    r"must be|must not|required to be)\b"
)
_ACTIVE_PARTY_SIZE_REVISION_CUE = re.compile(
    r"\b(?:them|bot|tang|giam|con|thanh|gio|bay gio|cap nhat|doi)\b.{0,55}"
    r"\b\d+\s*(?:nguoi|be|tre em|people|persons|guests)\b|"
    r"\b(?:them|bot|tang|giam)\s+(?:mot|hai|ba|bon|nam|sau|bay|tam|chin|muoi)\s+"
    r"(?:nguoi|be|tre em|people|persons|guests)\b"
)
_INCOMPLETE_SHOPPING_GOAL_CUE = re.compile(
    r"\b(?:chua noi ro (?:la )?(?:dip|muc dich|hoan canh|viec gi)|"
    r"chua biet minh can chuan bi gi|chua ro can chuan bi gi|"
    r"hoi (?:minh )?(?:mot cau )?(?:ve|de lam ro) (?:muc dich|dip|hoan canh)|"
    r"what am i preparing for|not sure what i need to prepare)\b"
)


def needs_shopping_goal_clarification(text: str, *, active_mission: bool) -> bool:
    """Ask about an unstated purpose before decomposing a shopping mission."""
    return not active_mission and bool(_INCOMPLETE_SHOPPING_GOAL_CUE.search(_normalize(text)))


def should_route_to_shopping_mission(
    text: str,
    *,
    active_mission: bool,
    has_factual_lookup: bool,
    has_specific_product_reference: bool,
    recommendation_requested: bool,
    recommendation_tool_ready: bool,
    ranking_requested: bool,
    has_explicit_category_request: bool = False,
    has_multi_category_request: bool = False,
) -> bool:
    """Route broad needs and active-plan edits to the dynamic mission planner.

    The cues identify shopping/plan intent, not fixed retail scenarios. Category
    decomposition and SKU selection remain model- and catalog-driven.
    """
    normalized = _normalize(text)
    hard_constraint_update = active_mission and bool(
        _MISSION_HARD_CONSTRAINT_CUE.search(normalized)
    )
    party_size_update = active_mission and bool(
        _ACTIVE_PARTY_SIZE_REVISION_CUE.search(normalized)
    )
    if party_size_update and not _EXPLICIT_PRICE_QUESTION_CUE.search(normalized):
        # Counts such as "now 12 people" can be mistaken for a product price by
        # catalog field heuristics. In an open mission, an explicit party-size
        # change is a plan revision unless the customer actually asks for price.
        return True
    if has_factual_lookup and is_budget_amount_only(text):
        # The shared catalog parser treats a bare VND amount as a possible price
        # question. In an activity/mission request it is a budget constraint.
        has_factual_lookup = False
    if ranking_requested or (has_factual_lookup and not hard_constraint_update):
        return False

    if active_mission:
        return (
            recommendation_requested
            or hard_constraint_update
            or bool(_MISSION_REVISION_CUES.search(normalized))
        )

    has_activity_cue = bool(_SHOPPING_ACTIVITY_CUE.search(normalized))
    has_existing_item_cue = bool(
        re.search(
            r"\b(?:co san|da co|mang theo|da mua|da chuan bi|already have|"
            r"already bought|already packed|bringing)\b",
            normalized,
        )
    )
    if has_activity_cue and has_existing_item_cue:
        return True
    has_mission_level_cue = bool(_MISSION_LEVEL_PLANNING_CUE.search(normalized))
    has_discovery_cue = bool(_NEEDS_DISCOVERY_CUE.search(normalized))
    has_open_ended_cue = bool(_OPEN_ENDED_SHOPPING_CUES.search(normalized))
    has_budget_or_party_size = bool(
        _BUDGET_AMOUNT_CUE.search(normalized)
        or re.search(r"\b\d+\s*(?:nguoi|be|tre em|people|persons|guests)\b", normalized)
    )
    has_party_size = bool(
        re.search(r"\b\d+\s*(?:nguoi|be|tre em|people|persons|guests)\b", normalized)
    )
    if has_mission_level_cue:
        return True
    if has_activity_cue and has_budget_or_party_size:
        # A concrete occasion plus the scale or budget is a shopping mission,
        # even when the broad-phrase parser mistakes surrounding context for a
        # product name (for example, "prepare for movie night, 5 people, 200k").
        return True
    if has_party_size and (has_open_ended_cue or has_discovery_cue) and (
        not has_explicit_category_request or has_multi_category_request
    ):
        return True
    if has_specific_product_reference:
        return False

    has_problem_cue = bool(_SHOPPING_PROBLEM_CUE.search(normalized))

    if has_explicit_category_request and not has_multi_category_request:
        if _MISSION_LEVEL_PLANNING_CUE.search(normalized) or (
            has_activity_cue and has_budget_or_party_size
        ):
            return True
        # A single category, including a need such as dry skin or oral care,
        # can be served directly from the catalog recommendation tool.
        return False

    if has_problem_cue or has_discovery_cue:
        return True
    if has_open_ended_cue:
        return True

    if has_activity_cue:
        return True

    if recommendation_requested and recommendation_tool_ready:
        return False
    if recommendation_requested:
        if recommendation_tool_ready:
            return False
        return bool(
            _OPEN_ENDED_SHOPPING_CUES.search(normalized) or _BUDGET_AMOUNT_CUE.search(normalized)
        )
    return bool(_OPEN_ENDED_SHOPPING_CUES.search(normalized))


def is_budget_amount_only(text: str) -> bool:
    """Recognize an amount used as a budget, rather than an explicit price query."""
    normalized = _normalize(text)
    return bool(_BUDGET_AMOUNT_CUE.search(normalized)) and not bool(
        _EXPLICIT_PRICE_QUESTION_CUE.search(normalized)
    )
