"""Real HTTP follow-ups for clarification, constraint scope and topic changes.

These are diagnostic dialogues, not a blind holdout or an automatic quality score.
"""

import argparse
import asyncio
from pathlib import Path

from evaluation import audit_live_consultation as audit


CONVERSATIONS = {
    "gift-preferences": [
        "Mình cần quà cho mẹ, khoảng 300 nghìn. Gợi ý giúp mình.",
        "Mẹ thích đồ uống, không thích đồ ngọt.",
        "Mẹ uống trà, không lấy cà phê. Chọn một loại thôi, đừng lấy đồ mỹ phẩm.",
    ],
    "topic-and-budget": [
        "Chọn giúp một chai dầu gội dưới 150 nghìn, dung tích tối thiểu 400ml.",
        "Giờ chuyển sang mua đồ uống, ngân sách phần này dưới 20 nghìn, mình không lấy nước có ga.",
        "Thích trà không đường, chọn một chai nhỏ thôi.",
        "Nhắc lại loại vừa chọn và giá giúp mình.",
    ],
}


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-url", default="http://127.0.0.1:8000")
    parser.add_argument("--ids")
    parser.add_argument("--pause-seconds", type=float, default=15)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    args.suite = "followup"
    args.holdout = False
    audit.FOLLOWUP_CONVERSATIONS = CONVERSATIONS
    asyncio.run(audit.run(args))
