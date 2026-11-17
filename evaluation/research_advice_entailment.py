"""Check whether a multilingual NLI verifier can spot observed advice errors.

Research only: XNLI accuracy is not a calibrated SKU-claim verification score.
Do not make the customer pipeline depend on these uncalibrated probabilities.
"""
import argparse
import json
import time
from pathlib import Path

import numpy as np
import onnxruntime as ort

MODEL = "MoritzLaurer/mDeBERTa-v3-base-mnli-xnli"
REVISION = "8adb042d524ecd5c26d3e3ba0e3fbcf7e2d0864c"
CASES = [
    ("actual-mince-error", "Thịt bò Úc Kiaora xay khay 200g.",
     "Thịt bò xay mỏng, dễ cắt thành lát mỏng cho lẩu.", "contradiction"),
    ("unsweetened-error", "Sữa tươi có đường 1 lít.",
     "Sản phẩm này là sữa không đường.", "contradiction"),
    ("scent-error", "Nước lau sàn hương hoa 1 lít.",
     "Sản phẩm này không mùi.", "contradiction"),
    ("size-error", "Nước uống đóng chai 500ml.",
     "Một chai có dung tích 1 lít.", "contradiction"),
    ("length-error", "Băng vệ sinh ban đêm dài 30cm.",
     "Sản phẩm này dài ít nhất 35cm.", "contradiction"),
    ("mass-volume-error", "Dầu gội Clear chai 630g.",
     "Chai dầu gội này có dung tích 630ml.", "neutral"),
    ("pet-safety-error", "Nước lau sàn hương chanh 1 lít.",
     "Sản phẩm này an toàn cho mèo.", "neutral"),
    ("acne-treatment-error", "Sữa rửa mặt Biore sạch mụn kháng khuẩn 100g.",
     "Sản phẩm này điều trị mụn sau hai tuần.", "neutral"),
    ("hypoallergenic-error", "Sữa tắm hương hoa 500ml.",
     "Sản phẩm này không gây kích ứng cho da nhạy cảm.", "neutral"),
    ("cooking-time-error", "Bánh bao nhân thịt 300g.",
     "Sản phẩm này chỉ cần hâm 30 giây là ăn được.", "neutral"),
    ("grounded-plain", "Sữa tươi không đường 1 lít.",
     "Sản phẩm này là sữa không đường.", "entailment"),
    ("grounded-size", "Nước uống đóng chai 1 lít.",
     "Một chai có dung tích 1000ml.", "entailment"),
    ("grounded-form", "Ba chỉ bò cắt lát khay 200g.",
     "Thịt đã được cắt thành lát.", "entailment"),
    ("grounded-scent", "Nước lau sàn hương chanh 1 lít.",
     "Sản phẩm này có hương chanh.", "entailment"),
    ("general-usage-not-label", "Ba chỉ bò cắt lát khay 200g.",
     "Thịt cắt lát sẵn tiện nhúng lẩu, nhúng chín từng ít một.", "neutral"),
    ("general-cleaning-not-label", "Cây lau nhà và khăn lau.",
     "Dùng khăn riêng cho bếp và nhà vệ sinh để tránh lây bẩn.", "neutral"),
]


def run(args):
    from huggingface_hub import snapshot_download
    from transformers import AutoTokenizer

    folder = Path(snapshot_download(MODEL, revision=REVISION, local_dir=args.model_dir,
        allow_patterns=["config.json", "tokenizer*.json", "special_tokens_map.json", "spm.model",
                        "README.md", "onnx/model_quantized.onnx"]))
    tokenizer = AutoTokenizer.from_pretrained(folder, trust_remote_code=False)
    options = ort.SessionOptions()
    options.intra_op_num_threads = 2
    session = ort.InferenceSession(str(folder / "onnx/model_quantized.onnx"),
                                   sess_options=options, providers=["CPUExecutionProvider"])
    labels = json.loads((folder / "config.json").read_text())["id2label"]
    inputs = {item.name for item in session.get_inputs()}
    report = {"model": MODEL, "revision": REVISION, "license": "mit",
              "production_switch": False, "rows": []}
    for case_id, premise, hypothesis, expected in CASES:
        start = time.perf_counter()
        encoded = tokenizer(premise, hypothesis, return_tensors="np", truncation=True)
        logits = session.run(None, {key: value for key, value in encoded.items() if key in inputs})[0][0]
        probabilities = np.exp(logits - logits.max())
        probabilities /= probabilities.sum()
        scores = {labels[str(index)].casefold(): float(probability)
                  for index, probability in enumerate(probabilities)}
        row = {"id": case_id, "premise": premise, "hypothesis": hypothesis,
               "expected": expected, "scores": scores, "predicted": max(scores, key=scores.get),
               "seconds": round(time.perf_counter() - start, 4)}
        report["rows"].append(row)
        print(json.dumps(row, ensure_ascii=False), flush=True)
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf8")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--model-dir", type=Path, default=Path(".tmp/advice-nli-research-20261111"))
    parser.add_argument("--output", type=Path, required=True)
    run(parser.parse_args())
