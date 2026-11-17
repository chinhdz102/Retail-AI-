"""Record real planner/composer outputs on development questions, with no mocks."""
import argparse
import asyncio
import json
import sys
import time
from pathlib import Path

from backend.products.repository import get_product_repository
from backend.services.consultation import consult
from backend.services.groq import GroqService
from evaluation.audit_live_consultation import CONVERSATIONS, FOLLOWUP_CONVERSATIONS, LONG_CONVERSATIONS

CONVERSATIONS = {**CONVERSATIONS, **FOLLOWUP_CONVERSATIONS, **LONG_CONVERSATIONS}
CONVERSATIONS["cross-category-recovery"] = [
    "Mình cần dầu gội dưới 150 nghìn, chọn đúng một loại để dùng thử, nói gọn lý do.",
    "Chai tối thiểu 400ml nhé, đừng chỉ chọn chai bé vì rẻ.",
    "Chuyển sang đồ uống: chọn một loại trà không đường, không có ga, dưới 20 nghìn.",
    "Không cần chai một lít nữa, chai nhỏ cũng được, chốt một loại thôi.",
    "Giờ tìm băng vệ sinh ban đêm dưới 60 nghìn, dài từ 35cm trở lên.",
    "Nếu có đúng loại đó thì chọn giúp mình một gói, đừng giới thiệu đồ khác.",
    "Nhà có mèo, mình cần cách lau nhà ít mùi. Chưa rõ nhãn hóa chất thì hướng dẫn cách làm trước.",
    "Vậy hướng dẫn gọn các bước, không cần mua thêm đồ lúc này.",
]


class Recorder(GroqService):
    def __init__(self, model=None, effort=None):
        super().__init__()
        if model:
            self.settings = self.settings.model_copy(update={
                "groq_model": model, "groq_fallback_model": "", "groq_fallback_model_2": "",
            })
        self.outputs = []
        self.effort = effort

    async def chat(self, messages, **kwargs):
        if self.effort:
            kwargs.update(reasoning_effort=self.effort, max_tokens=2000)
        try:
            answer = await super().chat(messages, **kwargs)
        except Exception as error:
            self.outputs.append({"error_type": type(error).__name__,
                                 "fallbacks": getattr(error, "fallback_trace", []),
                                 "messages": messages})
            raise
        self.outputs.append({"text": str(answer), "model": answer.model,
                             "messages": messages,
                             "usage": answer.usage, "fallbacks": answer.fallback_trace})
        return answer


async def run(args):
    sys.stdout.reconfigure(encoding="utf-8")
    report = []
    questions = ("Có dầu gội dưới 150 nghìn dùng ổn không?",
                     "Mua thịt ăn lẩu cho 4 người, tổng 200 nghìn.",
                     "Trời nóng, có nước nào uống không quá ngọt không?",
                     "Có đồ uống không có ga dưới 20 nghìn không?",
                     "Da dầu bị mụn, khoảng 100 nghìn, tư vấn cho t.")
    if args.conversation:
        questions = CONVERSATIONS[args.conversation]
    elif args.question:
        questions = [args.question]
    if args.max_turns:
        questions = questions[:args.max_turns]
    history, previous_ids, previous_plan = [], [], None
    service = Recorder(args.model, args.effort)
    for question in questions:
        call_start = len(service.outputs)
        started = time.monotonic()
        row = {"question": question}
        try:
            result = await consult(service, get_product_repository(), question,
                                   history, previous_ids, previous_plan)
            row.update(text=result.text, plan=result.plan.model_dump())
            if args.conversation:
                previous_plan = result.plan.model_dump()
                previous_ids = [p.product_id for p in result.products]
        except Exception as error:
            row["error_type"] = type(error).__name__
            # Only our locally generated validation message is safe to retain.
            # Provider exceptions may contain request bodies or credentials.
            if type(error).__name__ == "GroqProviderError" and not getattr(error, "fallback_trace", None):
                row["validation_error"] = str(error)[:500]
            if args.conversation and isinstance(getattr(error, "consultation_plan", None), dict):
                previous_plan = error.consultation_plan
                row["validated_plan"] = previous_plan
        if args.conversation:
            history.append({"role": "user", "content": question})
            if "text" in row:
                history.append({"role": "assistant", "content": row["text"]})
        row["calls"] = service.outputs[call_start:]
        row["elapsed_ms"] = round((time.monotonic() - started) * 1000)
        report.append(row)
        args.output.write_text(
            json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
        print(json.dumps(row, ensure_ascii=False), flush=True)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--model")
    parser.add_argument("--effort", choices=["none", "low", "medium", "high"])
    parser.add_argument("--question")
    parser.add_argument("--conversation", choices=list(CONVERSATIONS))
    parser.add_argument("--max-turns", type=int)
    parser.add_argument("--output", type=Path, required=True)
    asyncio.run(run(parser.parse_args()))
