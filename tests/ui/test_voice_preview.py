from pathlib import Path

import pytest

from audiobook_generator.ui.voice_preview import MissingProviderKey, VoicePreview


class FakeSpeechResponse:
    content = b"preview-audio"


class FakeSpeech:
    def __init__(self):
        self.kwargs = None

    def create(self, **kwargs):
        self.kwargs = kwargs
        return FakeSpeechResponse()


class FakeClient:
    def __init__(self):
        self.audio = type("Audio", (), {"speech": FakeSpeech()})()


def test_openai_preview_requires_key(tmp_path: Path):
    preview = VoicePreview(tmp_path, environ={})
    with pytest.raises(MissingProviderKey, match="OPENAI_API_KEY"):
        preview.generate("openai", "gpt-4o-mini-tts", "coral")


def test_openai_preview_is_short_and_uses_narration_instructions(tmp_path: Path):
    client = FakeClient()
    preview = VoicePreview(tmp_path, environ={"OPENAI_API_KEY": "test"}, openai_client=client)
    result = preview.generate("openai", "gpt-4o-mini-tts", "coral", text="Mot " * 500)

    assert result.read_bytes() == b"preview-audio"
    request = client.audio.speech.kwargs
    assert request["voice"] == "coral"
    assert len(request["input"]) <= 700
    assert "chaleureuse" in request["instructions"]
