# Silero VAD v6

`silero_vad_v6.onnx` is copied unchanged from `@ricky0123/vad-web` 0.0.31,
also used by the kiosk. SHA-256:
`1a153a22f4509e292a94e67d6f9b85e8deb25b4988682b7e174c65279d8788e3`.

The v5 asset is retained for paired diagnostics. Runtime uses v6 on both browser
and server, with the same 512-sample frame and 64-sample context interface.

Upstream: https://github.com/snakers4/silero-vad. MIT license is included in
`LICENSE`. The asset is bundled so backend deployment needs no Node installation
or model download at startup. It is not trained on project diagnostic samples.
