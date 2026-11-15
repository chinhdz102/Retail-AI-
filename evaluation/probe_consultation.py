"""Record real planner/composer outputs on development questions, with no mocks."""
import argparse
import asyncio
import json
import sys
from pathlib import Path

from backend.products.repository import get_product_repository
from backend.services.consultation import consult
from backend.services.groq import GroqService
from evaluation.audit_live_consultation import CONVERSATIONS


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
    history, previous_ids, previous_plan = [], [], None
    for question in questions:
        service = Recorder(args.model, args.effort)
        row = {"question": question}
        try:
            result = await consult(service, get_product_repository(), question,
                                   history, previous_ids, previous_plan)
            row.update(text=result.text, plan=result.plan.model_dump())
            if args.conversation:
                previous_plan = result.plan.model_dump()
                previous_ids = [p.product_id for p in result.products]
                history.extend([{"role": "user", "content": question},
                                {"role": "assistant", "content": result.text}])
        except Exception as error:
            row["error_type"] = type(error).__name__
        row["calls"] = service.outputs
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
    parser.add_argument("--output", type=Path, required=True)
    asyncio.run(run(parser.parse_args()))
