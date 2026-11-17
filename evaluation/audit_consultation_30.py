"""Run a 30-question Vietnamese retail consultation suite plus one long flow.

The runner records customer text, safe HTTP status/model/text and deterministic
surface flags. It deliberately does not claim that a 200 response is good
advice; the JSON is a review queue for the rubric in the accompanying report.
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import re
import time
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

import httpx


QUESTIONS = [
    ("price_low_water", "Giá thấp nhất của nước suối trong cửa hàng là bao nhiêu?", "price"),
    ("price_high_water", "Giá cao nhất của nước suối hiện tại là bao nhiêu?", "price"),
    ("price_low_shampoo", "Trong các loại dầu gội, loại nào rẻ nhất và giá bao nhiêu?", "price"),
    ("price_high_shampoo", "Loại dầu gội nào đắt nhất, giá bao nhiêu vậy?", "price"),
    ("price_low_high_meat", "Các loại thịt đang có, giá thấp nhất và cao nhất khoảng bao nhiêu?", "price"),
    ("price_range_tea", "Trà trong cửa hàng có những mức giá thấp đến cao nào?", "price"),
    ("price_exact_1l", "Chai nước 1 lít giá bao nhiêu?", "price"),
    ("compare_milk", "Hai loại sữa này khác nhau ở điểm nào và loại nào đáng tiền hơn?", "compare"),
    ("compare_detergent", "OMO với Ariel loại nào nên chọn nếu cùng ngân sách?", "compare"),
    ("recommend_breakfast", "Mình cần bữa sáng nhanh gọn, tư vấn một lựa chọn no lâu giúp mình.", "recommend"),
    ("recommend_hotpot", "Mua thịt ăn lẩu, bò thường hoặc ba chỉ đều được, chọn giúp mình.", "recommend"),
    ("recommend_gift", "Mình cần quà cho mẹ khoảng 300 nghìn, mẹ thích đồ uống không quá ngọt.", "recommend"),
    ("recommend_snack_child", "Nhà có bé 6 tuổi, chọn đồ ăn vặt bớt ngọt giúp mình.", "recommend"),
    ("recommend_shampoo", "Tóc hay bết, cần dầu gội dưới 150 nghìn, chọn một loại đáng tiền.", "recommend"),
    ("recommend_sensitive", "Da mình nhạy cảm, sữa tắm nào phù hợp để thử trước?", "recommend"),
    ("recommend_pet_cleaning", "Nhà có mèo, cần đồ lau nhà ít mùi; tư vấn cách dùng an toàn.", "recommend"),
    ("recommend_dinner_budget", "Có 200 nghìn, mua đồ ăn tối cho hai người gồm thịt và rau được không?", "recommend"),
    ("recommend_week", "Mua đồ ăn cho cả tuần khoảng 500 nghìn thì nên chia thế nào?", "recommend"),
    ("budget_cheaper", "Món này hơi đắt, tìm loại tương tự rẻ hơn giúp mình.", "budget"),
    ("budget_value", "Mình không cần loại xịn, chọn loại đáng tiền và dùng được lâu.", "budget"),
    ("constraint_no_sugar", "Mình đang hạn chế đường, chọn đồ uống nào phù hợp?", "constraint"),
    ("constraint_no_soda", "Đồ uống dưới 20 nghìn, không lấy nước có ga.", "constraint"),
    ("constraint_1l", "Mình cần đúng một chai 1 lít, không cộng hai chai nhỏ.", "constraint"),
    ("constraint_small", "Phòng nhỏ, mình cần loại gọn và không có mùi nồng.", "constraint"),
    ("constraint_exclusion", "Mua bữa tối, nhà có gạo dầu ăn gia vị rồi, đừng tính thêm.", "constraint"),
    ("substitute", "Sản phẩm mình cần hết rồi, có loại tương tự nào thay được không?", "substitute"),
    ("location_laundry", "Nước giặt ở khu nào vậy anh?", "location"),
    ("location_tissue", "Khăn giấy nằm khu nào, mình tìm mãi không thấy?", "location"),
    ("availability_color", "Loại sữa hộp màu xanh hôm trước còn không?", "availability"),
    ("ambiguous_tiktok", "Cái loại hay quảng cáo trên TikTok bên mình có không?", "clarify"),
]


LONG_FLOW = [
    "Tối nay hai người ăn ở nhà, chọn bữa tối có thịt và rau, tổng dưới 180 nghìn; nhà có gạo rồi.",
    "Không lấy thịt gà, xúc xích hay món chế biến sẵn.",
    "Mình thích thịt heo hơn thịt bò, ưu tiên phần ít mỡ.",
    "Thịt chỉ mua 300 gram, chọn thêm một loại rau dễ nấu và tính tổng.",
    "Giảm ngân sách cả bữa xuống tối đa 120 nghìn.",
    "Nhà có dầu ăn và gia vị, không tính các món đó.",
    "Cho mình xem lại đúng hai món cuối cùng, đừng thêm món phụ.",
    "Bây giờ chuyển sang đồ uống, giữ nguyên phần bữa tối nhưng không gộp hai khoản tiền.",
    "Đồ uống dưới 20 nghìn một chai, không có ga và ưu tiên trà không đường.",
    "Mình cần đúng chai 1 lít, không ghép hai chai nhỏ thành một lít.",
    "Nếu không có đúng 1 lít thì nói rõ thiếu tiêu chí nào, đừng tự chốt chai nhỏ.",
    "Bỏ yêu cầu 1 lít, chọn một chai nhỏ phù hợp các tiêu chí còn lại.",
    "Giờ đổi sang mua dầu gội dưới 150 nghìn cho tóc hay bết, chọn một loại.",
    "Không cần quảng cáo công dụng; nói một điểm cần cân nhắc thôi.",
    "Chai phải từ 400ml trở lên, không lấy gói lẻ.",
    "Thôi bỏ yêu cầu dầu gội, quay lại nhắc đúng loại đồ uống vừa chọn và giá của nó.",
    "Không giới thiệu thêm sản phẩm khác.",
    "Nếu không chắc giá hoặc tồn, nói ngắn phần nào chưa xác minh, đừng đoán.",
]


INTERNAL = re.compile(r"\b(?:catalog|snapshot|log|dữ liệu nội bộ|mã sku|sku)\b", re.I)
BOILERPLATE = re.compile(
    r"(?:chưa tìm thấy mặt hàng khớp|chưa tìm thấy món đó|danh mục hiện có|điều chỉnh tiêu chí|đổi từ khóa|thử lại sau|tìm nơi khác)",
    re.I,
)
MONEY = re.compile(r"\d[\d.,\s]*\s*(?:đ|₫|vnd|vnđ|k|nghìn|ngàn|triệu)\b", re.I)


def surface_flags(tag: str, text: str | None, status: int | None) -> list[str]:
    flags: list[str] = []
    if status != 200:
        flags.append("http_error")
        return flags
    if not text or not text.strip():
        flags.append("empty_text")
        return flags
    if INTERNAL.search(text):
        flags.append("internal_term")
    if BOILERPLATE.search(text):
        flags.append("boilerplate_refusal")
    if tag == "price" and not MONEY.search(text):
        if re.search(r"(?:đúng (?:vị|khối lượng|quy cách)|xác nhận|kích thước|size)", text, re.I):
            flags.append("price_needs_variant")
        else:
            flags.append("price_without_amount")
    if tag in {"recommend", "compare", "budget", "constraint", "substitute"} and not INTERNAL.search(text):
        if re.search(r"\b(?:bạn có muốn|có muốn mình|mình có thể (?:tìm|xem) thêm|muốn mình (?:tìm|xem) thêm)\b", text, re.I):
            flags.append("generic_sales_question")
    return flags


async def ask(client: httpx.AsyncClient, question: str, tag: str) -> dict:
    created = await client.post("/sessions", json={"kiosk_id": "consultation-30-audit"})
    created.raise_for_status()
    session_id = created.json()["session_id"]
    try:
        started = time.perf_counter()
        response = await client.post("/agent/respond", json={
            "session_id": session_id, "text": question, "language": "vi",
            "client_turn_id": str(uuid4()),
        })
        row = {"question": question, "http_status": response.status_code,
               "seconds": round(time.perf_counter() - started, 3)}
        if response.status_code == 200:
            payload = response.json()
            row.update({key: payload.get(key) for key in ("text", "model", "grounded_product_ids")})
            row["flags"] = surface_flags(tag, payload.get("text"), response.status_code)
        else:
            detail = response.json().get("detail", {})
            row["error_code"] = detail.get("code") if isinstance(detail, dict) else None
            row["flags"] = ["http_error"]
        return row
    finally:
        await client.post(f"/sessions/{session_id}/close")


async def run(args: argparse.Namespace) -> None:
    source = Path("data/winmart_products.json")
    report = {
        "started_utc": datetime.now(UTC).isoformat(), "mode": "real-http-text",
        "catalog_sha256": hashlib.sha256(source.read_bytes()).hexdigest(),
        "questions": [], "long_flow": {"turns": []}, "complete": False,
    }
    async with httpx.AsyncClient(base_url=args.base_url, timeout=args.timeout) as client:
        selected = [] if args.flow_only else QUESTIONS
        if args.ids:
            wanted = set(args.ids.split(","))
            selected = [row for row in QUESTIONS if row[0] in wanted]
        for number, (case_id, question, tag) in enumerate(selected, 1):
            row = await ask(client, question, tag)
            row.update({"id": case_id, "tag": tag, "number": number})
            report["questions"].append(row)
            print(json.dumps({"id": case_id, "status": row.get("http_status"),
                              "flags": row.get("flags"), "seconds": row.get("seconds")}, ensure_ascii=False), flush=True)
            await asyncio.sleep(args.pause_seconds)
        if not args.questions_only:
            created = await client.post("/sessions", json={"kiosk_id": "consultation-long-audit"})
            created.raise_for_status()
            session_id = created.json()["session_id"]
            try:
                for number, question in enumerate(LONG_FLOW, 1):
                    started = time.perf_counter()
                    response = await client.post("/agent/respond", json={
                        "session_id": session_id, "text": question, "language": "vi",
                        "client_turn_id": str(uuid4()),
                    })
                    row = {"number": number, "question": question,
                           "http_status": response.status_code,
                           "seconds": round(time.perf_counter() - started, 3)}
                    if response.status_code == 200:
                        payload = response.json()
                        row.update({key: payload.get(key) for key in ("text", "model", "grounded_product_ids", "state")})
                        row["flags"] = surface_flags("", payload.get("text"), response.status_code)
                    else:
                        detail = response.json().get("detail", {})
                        row["error_code"] = detail.get("code") if isinstance(detail, dict) else None
                        row["flags"] = ["http_error"]
                    report["long_flow"]["turns"].append(row)
                    print(json.dumps({"flow_turn": number, "status": row["http_status"],
                                      "flags": row["flags"], "seconds": row["seconds"]}, ensure_ascii=False), flush=True)
                    await asyncio.sleep(args.pause_seconds)
            finally:
                await client.post(f"/sessions/{session_id}/close")
    report["complete"] = True
    report["finished_utc"] = datetime.now(UTC).isoformat()
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    counts = {}
    for row in [*report["questions"], *report["long_flow"]["turns"]]:
        for flag in row.get("flags", []):
            counts[flag] = counts.get(flag, 0) + 1
    print(json.dumps({"questions": len(report["questions"]),
                      "long_turns": len(report["long_flow"]["turns"]),
                      "flags": counts}, ensure_ascii=False))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-url", default="http://127.0.0.1:8000")
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--ids")
    parser.add_argument("--questions-only", action="store_true")
    parser.add_argument("--flow-only", action="store_true")
    parser.add_argument("--pause-seconds", type=float, default=3)
    parser.add_argument("--timeout", type=float, default=110)
    asyncio.run(run(parser.parse_args()))
