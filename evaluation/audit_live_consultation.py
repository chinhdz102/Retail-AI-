"""Ask the running backend real Vietnamese customer questions; retain raw answers."""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import sys
import time
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

import httpx

QUESTIONS = [
    "Anh ơi, nhà em đang thiếu mấy đồ dùng cơ bản, anh tư vấn giúp em với.",
    "Em muốn mua cái gì đó ăn sáng cho nhanh mà vẫn no, có loại nào ổn không?",
    "Dạo này trời nóng quá, có loại nước nào uống ngon mà không quá ngọt không?",
    "Nhà em có trẻ con, muốn mua ít đồ ăn vặt mà đỡ ngọt thì chọn gì nhỉ?",
    "Em định mua quà cho mẹ mà chưa biết chọn cái gì, tầm 300 nghìn thôi.",
    "Có gì ăn tiện mà không cần nấu nướng nhiều không anh?",
    "Em muốn mua đồ để dọn nhà cuối tuần, cần những gì nhỉ?",
    "Em có khoảng 200 nghìn, mua đồ ăn đủ cho hai người một bữa tối được không?",
    "Có loại dầu gội nào tầm dưới 150 nghìn mà dùng ổn không?",
    "Em muốn mua ít đồ ăn cho cả tuần, khoảng 500 nghìn thì nên mua gì?",
    "Loại nào vừa rẻ vừa dùng được lâu ấy anh?",
    "Cái này hơi đắt, có loại nào tương tự mà rẻ hơn không?",
    "Em không cần hàng xịn nhất đâu, loại nào đáng tiền là được.",
    "Hai loại sữa này khác nhau ở điểm nào vậy anh?",
    "OMO với Ariel thì loại nào giặt sạch hơn nhỉ?",
    "Loại này đắt hơn có đáng để mua không?",
    "Em thấy hai cái này gần giống nhau, sao giá chênh nhiều thế?",
    "Nếu là anh thì anh chọn loại nào?",
    "Cùng một mức giá thì loại nào dùng thích hơn?",
    "Da em khá nhạy cảm, có loại sữa tắm nào dịu nhẹ không?",
    "Mẹ em không thích đồ quá ngọt, có loại bánh nào phù hợp không?",
    "Nhà em có nuôi mèo, muốn mua đồ vệ sinh nhà mà an toàn khi dùng quanh thú cưng.",
    "Em đang hạn chế đường, có đồ uống nào phù hợp không?",
    "Em cần loại nhỏ gọn thôi, phòng em bé lắm.",
    "Em muốn loại không có mùi nồng, dùng hằng ngày ấy.",
    "Anh ơi, nước giặt ở đâu vậy?",
    "Có bán cái loại sữa hộp màu xanh xanh không nhỉ?",
    "Loại mì Hàn Quốc cay cay hôm trước em mua còn không?",
    "Em tìm mãi không thấy khăn giấy, nó nằm khu nào vậy?",
    "Cái loại 1 lít ấy, chứ không phải chai nhỏ đâu.",
    "Có sản phẩm nào tương tự không, nếu cái em cần hết hàng rồi?",
    "Anh ơi, cái loại hay quảng cáo trên TikTok ấy, bên mình có không?",
    "Em muốn mua loại tốt tốt một chút, nhưng đừng đắt quá.",
    "Có cái nào dùng được cho cả nhà không?",
    "Hôm trước em mua loại màu vàng ấy, giờ có loại nào tốt hơn không?",
]
CONVERSATIONS = {
    "hotpot": [
        "Mua thịt về ăn lẩu cho 4 người thì chọn gì?",
        "Thịt bò bình thường hoặc ba chỉ cũng được, khoảng 200 nghìn.",
        "Tư vấn giúp t chọn loại nào đi.",
    ],
    "acne": [
        "Có loại nào điều trị mụn không?",
        "Tư vấn cho t với.",
        "Da dầu, khoảng 100 nghìn, chọn gì được?",
    ],
    "shampoo": [
        "Có dầu gội dưới 150 nghìn không?",
        "Tóc t dầu, chọn giúp loại đáng tiền.",
        "Có loại nào rẻ hơn mà dung tích tương tự không?",
    ],
    "drinks": [
        "Có đồ uống nào không quá ngọt dưới 20 nghìn?",
        "Không lấy nước có ga.",
        "Loại 1 lít ấy chứ không phải chai nhỏ.",
    ],
    "dinner": [
        "Mua đồ ăn tối cho hai người, tổng khoảng 200 nghìn.",
        "Nhà có gạo rồi, lấy món dễ nấu thôi.",
        "Gợi ý cụ thể và tính tổng giúp t.",
    ],
    "cleaning": [
        "Nhà t nuôi mèo, cuối tuần muốn mua đồ lau nhà.",
        "Không có mùi nồng, tầm dưới 100 nghìn.",
        "Tư vấn nên chọn loại nào?",
    ],
    "stt-typo": [
        "Mua thịt về ăn lấu thì nên chọn gì?",
        "Ý t là ăn lẩu ấy.",
        "Bỏ bánh bao ra, muốn thịt tươi.",
    ],
}

HOLDOUT = [
    "Tối nay nấu canh chua cá, nhà có cà chua rồi. Đồ mua thêm tổng dưới 120 nghìn.",
    "Nhà có bé 18 tháng, cần mua sữa dùng hằng ngày, chọn giúp tôi.",
    "Tôi cần thức ăn cho mèo trưởng thành, ngân sách khoảng 100 nghìn.",
    "Mua nước rửa chén để rửa bình sữa cho bé thì chọn loại nào?",
    "Tôi ăn chay, cần món đủ no mang đi làm, không dùng thịt cá.",
    "Tìm băng vệ sinh ban đêm dưới 60 nghìn, dài từ 35 cm trở lên.",
    "Cần pin cho điều khiển, không nhớ AA hay AAA, mua sao cho đúng?",
    "Mua nước giặt cho máy cửa trước, không thích mùi nồng, dưới 150 nghìn.",
]

LONG_CONVERSATIONS = {
    "long-dinner": [
        "Tối nay hai người ăn ở nhà, chọn đồ nấu một bữa tối tổng không quá 180 nghìn. Nhà có gạo rồi.",
        "Không lấy thịt gà, xúc xích hay đồ chế biến sẵn nhé.",
        "Mình thích thịt heo hơn thịt bò.",
        "Chọn giúp mình món thịt và một loại rau dễ nấu, tính tổng luôn.",
        "Mỡ nhiều quá thì không thích, ưu tiên phần nạc hơn.",
        "Giảm tiền xuống tối đa 120 nghìn cho toàn bộ bữa nhé.",
        "Nhà có dầu ăn, gia vị rồi, đừng tính thêm mấy thứ đó.",
        "Mua thịt 300 gram thôi, còn lại chọn rau.",
        "Cho mình xem lại phương án cuối với tổng tiền.",
        "Giờ chuyển sang mua nước uống, phần đồ ăn giữ nguyên, đừng gộp giá. Mình muốn trà không đường, không có ga.",
        "Dưới 20 nghìn một chai thôi.",
        "Mình cần loại một lít, không phải cộng hai chai nhỏ thành một lít.",
        "Nếu không có đúng cỡ thì nói giúp mình lựa chọn nào gần nhất, đừng tự chốt chai nhỏ.",
        "Thôi bỏ yêu cầu một lít, chọn một chai nhỏ phù hợp hai tiêu chí còn lại.",
        "Nếu là bạn thì chọn đúng một loại nào?",
        "Nhắc lại giúp mình các tiêu chí của chai vừa chọn, không cần giới thiệu thêm món khác.",
    ],
    "long-household": [
        "Mình cần dầu gội cho người lớn, dưới 150 nghìn, tóc hay bết dầu.",
        "Không cần quảng cáo công dụng, chọn một loại đáng tiền và nói điểm cần cân nhắc.",
        "Chai nhỏ quá, có loại tối thiểu 400ml vẫn trong ngân sách không?",
        "Không lấy gói lẻ, chỉ chai hoặc túi từ 400ml trở lên.",
        "Đắt quá, đổi ngân sách xuống dưới 100 nghìn nhé.",
        "Nếu không đủ tiêu chí thì nói đúng điều còn thiếu, đừng tự đổi sang chai nhỏ.",
        "Thôi cho phép chai nhỏ hơn, vẫn giữ dưới 100 nghìn và không lấy gói lẻ.",
        "Chọn chốt một loại giúp mình.",
        "Giờ hỏi chuyện khác: nhà có mèo, cần nước lau sàn không có mùi nồng, tầm 80 nghìn.",
        "Có thể dùng lúc mèo đang đi lại trong phòng không?",
        "Vậy nếu chưa xác định được an toàn thì mình nên vệ sinh như nào trước?",
        "Mình chỉ cần cách chọn và cách dùng chung, không cần mua thêm sản phẩm.",
    ],
}

FOLLOWUP_CONVERSATIONS = {
    "owned-food": [
        "Hai người ăn cơm tối, nhà có gạo và dầu ăn rồi. Chọn thịt heo với một loại rau, tổng tối đa 120 nghìn.",
        "Không lấy thịt xay, chọn miếng thịt nạc để xào và rau luộc thôi.",
        "Thịt chỉ mua 300 gram, tính tổng hai món giúp mình.",
        "Giờ bỏ giới hạn tiền, vẫn chỉ cần đúng hai món ăn với cơm. Chọn chốt giúp mình, đừng giới thiệu thêm.",
    ],
    "one-shampoo": [
        "Có loại dầu gội nào dưới 150 nghìn không, chọn giúp mình đúng một loại.",
        "Đừng quảng cáo công dụng, nói một điểm nên cân nhắc thôi.",
        "Chỉ cần hướng dẫn chọn dầu gội cho tóc dầu, không cần mua thêm sản phẩm.",
    ],
}


async def run(args):
    sys.stdout.reconfigure(encoding="utf-8")
    root = Path(__file__).resolve().parents[1]
    source = root / "data/winmart_products.json"
    report = {
        "started_utc": datetime.now(UTC).isoformat(),
        "mode": "real-http-text",
        "base_url": args.base_url,
        "catalog_sha256": hashlib.sha256(source.read_bytes()).hexdigest(),
        "stt_used": False,
        "pause_seconds": args.pause_seconds,
        "complete": False,
        "cases": [],
    }
    definitions = [(f"q{i:02}", [text]) for i, text in enumerate(QUESTIONS, 1)]
    definitions += list(CONVERSATIONS.items())
    if args.suite == "questions":
        definitions = [(f"q{i:02}", [text]) for i, text in enumerate(QUESTIONS, 1)]
    elif args.suite == "long":
        definitions = list(LONG_CONVERSATIONS.items())
    elif args.suite == "full":
        definitions = [(f"q{i:02}", [text]) for i, text in enumerate(QUESTIONS, 1)]
        definitions += list(LONG_CONVERSATIONS.items())
    elif args.suite == "all":
        definitions += list(LONG_CONVERSATIONS.items())
    elif args.suite == "followup":
        definitions = list(FOLLOWUP_CONVERSATIONS.items())
    if args.holdout:
        definitions = [(f"h{i:02}", [text]) for i, text in enumerate(HOLDOUT, 1)]
    if args.ids:
        definitions = [
            (key, turns) for key, turns in definitions if key in set(args.ids.split(","))
        ]
    args.output.parent.mkdir(parents=True, exist_ok=True)
    async with httpx.AsyncClient(base_url=args.base_url, timeout=110) as client:
        for key, turns in definitions:
            session = await client.post("/sessions", json={"kiosk_id": "live-text-audit"})
            session.raise_for_status()
            session_id = session.json()["session_id"]
            case = {"id": key, "turns": []}
            report["cases"].append(case)
            try:
                for question in turns:
                    started = time.monotonic()
                    row = {"question": question}
                    try:
                        response = await client.post(
                            "/agent/respond",
                            json={
                                "session_id": session_id,
                                "text": question,
                                "language": "vi",
                                "client_turn_id": str(uuid4()),
                            },
                        )
                        row["http_status"] = response.status_code
                        if response.status_code == 200:
                            row["response"] = response.json()
                        else:
                            row["error_type"] = "HTTPError"
                            try:
                                detail = response.json().get("detail", {})
                                if isinstance(detail, dict) and detail.get("code") in {
                                    "assistant_busy", "assistant_unavailable",
                                }:
                                    row["error_code"] = detail["code"]
                            except ValueError:
                                pass
                    except httpx.HTTPError as error:
                        row["error_type"] = type(error).__name__
                    row["seconds"] = round(time.monotonic() - started, 3)
                    case["turns"].append(row)
                    args.output.write_text(
                        json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
                    )
                    payload = row.get("response", {})
                    print(
                        json.dumps(
                            {
                                "case": key,
                                "turn": len(case["turns"]),
                                "question": question,
                                "text": payload.get("text"),
                                "model": payload.get("model"),
                                "seconds": row["seconds"],
                                "status": row.get("http_status"),
                                "error_code": row.get("error_code"),
                            },
                            ensure_ascii=False,
                        ),
                        flush=True,
                    )
                    if args.pause_seconds:
                        await asyncio.sleep(args.pause_seconds)
            finally:
                await client.post(f"/sessions/{session_id}/close")
    report["complete"] = len(report["cases"]) == len(definitions)
    report["finished_utc"] = datetime.now(UTC).isoformat()
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-url", default="http://127.0.0.1:8000")
    parser.add_argument("--ids")
    parser.add_argument("--suite", choices=["core", "questions", "long", "full", "all", "followup"], default="core")
    parser.add_argument("--holdout", action="store_true")
    parser.add_argument("--pause-seconds", type=float, default=12)
    parser.add_argument("--output", type=Path, required=True)
    asyncio.run(run(parser.parse_args()))
