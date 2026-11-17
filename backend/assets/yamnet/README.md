# YAMNet audio event verification

Upstream: Google YAMNet v1, Apache 2.0, copyright Google LLC.
https://github.com/tensorflow/models/tree/master/research/audioset/yamnet

ONNX conversion (weights unchanged, frontend included):
https://huggingface.co/audiomagic/yamnet-onnx
Pinned revision: f25b741c2f0bdc6d7e6db24b5fddda23347dbafd

SHA-256:
- yamnet.onnx: d3835ffbbd4a1bb3e777f0ca217b5007907f5171dd5d17c4236b95b2af8f908e
- yamnet_class_map.csv: cdf24d193e196d9e95912a2667051ae203e92a2ba09449218ccb40ef787c6df2

AudioSet labels are by Google, CC BY 4.0. No weights were changed here.
The original Apache license is in LICENSE.

This model vetoes confidently non-verbal sounds before cloud recognition. It
cannot identify the intended speaker or reject intelligible television speech.
See docs/reports/stt-followup-20261110.md for the measured limits and validation.
