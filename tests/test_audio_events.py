import numpy as np
import pytest

from backend.services import audio_events


@pytest.mark.parametrize("speech,other,accepted", [
    (.82, .93, False), (.91, .99, False),
    (.98, .99, True),  # Real speech with a competing background event.
    (.78, .03, True),  # A brief reply must not need 95% speech confidence.
    (.4, .4, True),  # This verifier vetoes; it is not another strict VAD.
])
def test_acoustic_veto_keeps_short_speech_and_speech_with_background_noise(
    monkeypatch, speech, other, accepted,
):
    class Model:
        def run(self, names, inputs):
            scores = np.zeros((2, 521), dtype=np.float32)
            scores[0, 0] = speech
            scores[0, 42] = other
            scores[1, 494] = 1  # Silent padding is neutral.
            return [scores]

    monkeypatch.setattr(audio_events, "_model_session", Model)
    result, _ = audio_events.verify_speech_events(np.zeros(16000, dtype=np.float32))
    assert result is accepted


def test_invalid_event_model_is_not_silently_bypassed(monkeypatch):
    class Model:
        def run(self, names, inputs):
            return [np.full((2, 521), np.nan)]

    monkeypatch.setattr(audio_events, "_model_session", Model)
    with pytest.raises(RuntimeError):
        audio_events.verify_speech_events(np.zeros(16000, dtype=np.float32))


def test_loud_noise_in_another_window_does_not_veto_later_quiet_speech(monkeypatch):
    class Model:
        def run(self, names, inputs):
            scores = np.zeros((4, 521), dtype=np.float32)
            scores[0, 81] = .99  # A nonverbal event before the customer speaks.
            scores[0, 0] = .01
            scores[2, 0] = .92
            scores[2, 81] = .02
            return [scores]

    monkeypatch.setattr(audio_events, "_model_session", Model)
    accepted, quality = audio_events.verify_speech_events(np.zeros(48000, dtype=np.float32))
    assert accepted
    assert quality["event_supported_speech_windows"] == 1
