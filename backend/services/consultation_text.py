"""Bind product mentions outside generated prose, without writing the advice.

The model emits explanation blocks with a dedicated candidate reference.
Only the application expands those integers to names/prices. This is a wire
format; it does not choose products or write their explanations. All semantic
checks remain in the application.
"""

from __future__ import annotations

import json
import re

from backend.products.category_taxonomy import normalize_category_text
from backend.services.advice_claims import find_unsupported_absence_claim

BOUND_TEXT_POLICY = """Định dạng câu trả lời:
text (hoặc revised_text khi sửa) là mảng {candidate, explanation}.
Viết câu hội thoại tự nhiên, gắn trực tiếp mục đích của khách (tận dụng quy cách,
khuyến mãi promotion, đơn giá unit_price_display nếu có).
Với candidate là ID số nguyên: dùng đúng MỘT {product} trong explanation — ứng dụng
tự thay bằng tên, số lượng và giá thật. TUYỆT ĐỐI KHÔNG tự gõ lại tên hàng, số lượng
hay bất kỳ con số giá bán nào (như "45.000đ", "giá 32.400đ", "khoảng 50k") trong
explanation; chỉ được nêu số tiền giảm có sẵn kèm từ "giảm"/"khuyến mãi" hoặc đơn giá
quy đổi (unit_price_display). Không nhắc lại quy cách đã có trong tên hàng.
Trong block sản phẩm (candidate=ID), KHÔNG tự viết "không đường", "không cà phê",
"không cay", "không chứa ..." trừ khi CHÍNH tên sản phẩm ghi nguyên văn cụm đó; nếu cần
nhắc lại yêu cầu hạn chế/loại trừ của khách thì đặt ở block mở đầu candidate=null.
Mỗi món đã chọn xuất hiện ở đúng một block với candidate=ID tương ứng; block đầu là
món ưu tiên.
candidate=null CHỈ dùng cho câu mở đầu/kết nối hoặc tư vấn chung và TUYỆT ĐỐI KHÔNG
chứa {product}.
Tách đề xuất khách tự làm khỏi khẳng định SKU đã có đặc tính đó; không hứa độ bền,
độ mềm, no lâu, dùng lâu/dùng lâu dài, đủ dùng/đủ cho/đủ người/đủ bữa, dùng ngay,
không làm khô da, giảm mụn/trị mụn, vừa balo, hiệu năng, sức khỏe hay an toàn.
revised_text=[] nếu không sửa. Nếu cần hỏi tiếp, CHỈ đặt câu hỏi trong
pending_question.question, không viết lặp câu hỏi vào explanation. Khi giỏ có từ
3 món trở lên, mỗi block chỉ viết 1 câu thật ngắn (dưới 100 ký tự) để tổng độ dài
không vượt giới hạn 800 ký tự.
"""


def bind_text_schema(parameters: dict, field: str, indexes: list[int]) -> None:
    variants = [{"type": "null"}]
    if indexes:
        variants.append({"type": "integer", "enum": indexes})
    parameters["properties"][field] = {
        "type": "array",
        "maxItems": 7,
        "items": {
            "type": "object",
            "additionalProperties": False,
            "properties": {
                "candidate": {"anyOf": variants},
                "explanation": {"type": "string", "maxLength": 700},
            },
            "required": ["candidate", "explanation"],
        },
        "description": "Natural conversational sentences. Each selected candidate has one "
        "block with exactly one {product} marker inside explanation for the app to bind "
        "its real name and price. Null candidate means general advice without that marker. "
        "An empty revision means no edit.",
    }


def _exact_price_pattern(price: int) -> str:
    forms = {str(price), f"{price:,}", f"{price:,}".replace(",", "."),
             f"{price:,}".replace(",", " ")}
    amount = "|".join(r"\s+".join(re.escape(word) for word in value.split())
                      for value in sorted(forms, key=len, reverse=True))
    return r"(?<![\w.,])(?:" + amount + r")\s*(?:đồng|vnđ|vnd|đ|₫)(?!\w)"


def _bind_exact_echo(explanation: str, fact: dict, index: int, prefix: str) -> str | None:
    """Project an exact repeated fact, never repair a guessed identity or price.

    Some models repeat the supplied title instead of the wire marker. Rebinding
    that same title preserves their sentence without adding a second sales lead.
    Whitespace may differ; spelling, pack size and the numeric price may not.
    Other monetary literals remain visible to normal business validation.
    """
    name, price = fact.get("name"), fact.get("price_vnd")
    if not isinstance(name, str) or type(price) is not int or price < 0:
        return None
    words = name.split()
    if not words:
        return None
    pattern = r"(?<!\w)" + r"\s+".join(re.escape(word) for word in words) + r"(?!\w)"
    matches = list(re.finditer(pattern, explanation))
    if len(matches) != 1:
        return None
    money = re.compile(_exact_price_pattern(price), re.I)
    span = matches[0].span()
    reference = f"{prefix}{{name:{index}}}"
    # Do not let numbers inside a literal title (e.g. a pack count) be prices.
    before, after = explanation[:span[0]], explanation[span[1]:]
    before, count_before = money.subn(f"{{price:{index}}}", before)
    after, count_after = money.subn(f"{{price:{index}}}", after)
    if not count_before + count_after:
        reference += f" ({{price:{index}}})"
    return before + reference + after


def decode_bound_text(
    raw: str,
    field: str,
    indexes: list[int],
    *,
    basket: bool,
    selected_quantities: dict[int, int] | None = None,
    product_facts: dict[int, dict] | None = None,
    unresolved_needs: bool = False,
) -> str:
    payload = json.loads(raw)
    if not isinstance(payload, dict):
        raise ValueError("Bound response must be an object")
    # Missing optional fields retain their declared local default. The strict
    # provider schema still requires them; default-only recovery owns omission.
    if field not in payload:
        return raw
    parts = payload[field]
    if not isinstance(parts, list) or len(parts) > 7:
        raise ValueError("Bound response must contain explanation blocks")
    picks = payload.get("picks", [])
    quantities = selected_quantities or {
        p["candidate"]: p.get("quantity", 1)
        for p in picks
        if isinstance(p, dict) and type(p.get("candidate")) is int
    }
    fragments = []
    mentioned = set()
    for part in parts:
        if not isinstance(part, dict) or set(part) != {"candidate", "explanation"}:
            raise ValueError("Invalid bound response block")
        index, explanation = part["candidate"], part["explanation"]
        if not isinstance(explanation, str) or re.search(r"\{(?:name|price):", explanation):
            raise ValueError("Invalid bound response explanation")
        explanation = explanation.strip()
        if type(index) is int and index in indexes and index not in mentioned:
            if product_facts is not None:
                facts = product_facts.get(index, {})
                sources = [str(facts.get("name", "")), *(
                    str(fact.get("value", "")) for fact in facts.get("evidence", [])
                    if isinstance(fact, dict)
                )]
                bad_absence = find_unsupported_absence_claim(explanation, sources)
                if bad_absence:
                    raise ValueError(
                        "Unsupported ingredient-absence claim in this product's explanation "
                        f"(bỏ cụm '{bad_absence}' khỏi block sản phẩm; đặt câu nhắc yêu cầu khách "
                        "ở block candidate=null và chỉ nêu tên, quy cách, đơn giá hoặc khuyên "
                        "kiểm tra nhãn)"
                    )
            mentioned.add(index)
            quantity = quantities.get(index, 1)
            prefix = f"{quantity} × " if basket else ""
            reference = f"{prefix}{{name:{index}}} ({{price:{index}}})"
            # Repair bare {price}/{name} markers emitted inside an indexed product block.
            explanation = explanation.replace("{price}", f"{{price:{index}}}")
            explanation = explanation.replace("{name}", f"{{name:{index}}}")
            if "{product}" in explanation:
                if explanation.count("{product}") != 1:
                    raise ValueError("A bound product marker must occur exactly once")
                def deduplicate_quantity(match):
                    if int(match[1]) != quantity:
                        raise ValueError("Written quantity disagrees with selected quantity")
                    return "{product}"

                explanation = re.sub(
                    r"(?<!\w)(\d+)\s*[×x]\s*\{product\}",
                    deduplicate_quantity, explanation,
                )
                price = (product_facts or {}).get(index, {}).get("price_vnd")
                if type(price) is int and price >= 0 and not re.search(
                    r"\b(?:thi truong|trung binh|gia si|niem yet|tong|ca gio)\b",
                    normalize_category_text(explanation),
                ):
                    # One block has one bound product. An exact unit-price echo
                    # elsewhere in that block is the same known fact, not a
                    # guessed price. Market/list/total claims or amounts right
                    # after a discount verb stay unbound.
                    echoes = 0

                    def _rebind_exact_price(match: re.Match[str]) -> str:
                        nonlocal echoes
                        local_prefix = normalize_category_text(
                            explanation[max(0, match.start() - 32) : match.start()]
                        )
                        if re.search(
                            r"\b(?:giam(?:\s+gia)?|khuyen mai|uu dai|tiet kiem|gia goc)\s*$",
                            local_prefix,
                        ):
                            return match.group(0)
                        echoes += 1
                        return f"{{price:{index}}}"

                    explanation = re.sub(
                        _exact_price_pattern(price),
                        _rebind_exact_price,
                        explanation, flags=re.I,
                    )
                    if echoes:
                        reference = f"{prefix}{{name:{index}}}"
                fragments.append(explanation.replace("{product}", reference))
                continue
            echoed = _bind_exact_echo(
                explanation, (product_facts or {}).get(index, {}), index, prefix,
            )
            if echoed is not None:
                fragments.append(echoed)
                continue
            # Compatibility for saved drafts and integrations using the older
            # explanation-only blocks. New prompts place the reference inside
            # an LLM-written sentence, avoiding application-authored sales copy.
            lead = "Mình ưu tiên " if not fragments and not basket else "Bạn cũng có thể chọn "
            if basket:
                lead = ""
            fact = lead + reference
            fragments.append(fact + (". " + explanation if explanation else "."))
        elif index is None:
            if "{product}" in explanation:
                raise ValueError(
                    "A general block cannot bind a product marker "
                    "(block chứa {product} BẮT BUỘC đặt candidate=ID số nguyên của sản phẩm đó, "
                    "không để candidate=null)"
                )
            fragments.append(explanation)
        else:
            # bools, floats, unknown IDs and generated pseudo-references cannot
            # acquire the application's authority by coercion or fuzzy repair.
            raise ValueError("Invalid bound response reference")
    # Drop a null-candidate intro fragment when a later product fragment
    # repeats the exact same prose (normalized without placeholders).
    if len(fragments) >= 2:
        deduped: list[str] = []
        for frag in fragments:
            frag_plain = normalize_category_text(re.sub(r"\{[^{}]+\}", "", frag))
            if (
                deduped
                and "{name:" not in deduped[-1]
                and "{name:" in frag
                and len(frag_plain) >= 30
            ):
                prev_plain = normalize_category_text(deduped[-1])
                prev_words = set(prev_plain.split())
                frag_words = set(frag_plain.split())
                if prev_words and len(prev_words & frag_words) / len(prev_words) >= 0.75:
                    deduped[-1] = frag
                    continue
            deduped.append(frag)
        fragments = []
        for idx_frag, frag in enumerate(deduped):
            if idx_frag > 0:
                stripped_da = re.sub(r"^Dạ(?:,\s*|\s+)", "", frag)
                if stripped_da and stripped_da != frag:
                    frag = stripped_da[0].upper() + stripped_da[1:]
            fragments.append(frag)
    text = " ".join(fragments).strip()
    text = re.sub(r"(\d)để\b", r"\1đ để", text)
    if field == "text" and picks and not text:
        raise ValueError(
            "Selected products need conversational advice explaining the customer's choice; "
            "an empty explanation must not become an automatic catalog listing"
        )
    if basket and mentioned and "{total}" not in text:
        text += (" Tạm tính các món đã chọn: {total}."
                 if unresolved_needs or payload.get("unfulfilled_searches")
                 else " Tổng là {total}.")
    if len(text) > 800:
        raise ValueError(
            "Bound response exceeds the prose limit "
            "(rút ngắn toàn bộ câu trả lời dưới 700 ký tự; mỗi món chỉ viết 1 câu ngắn)"
        )
    payload[field] = text
    return json.dumps(payload, ensure_ascii=False)
