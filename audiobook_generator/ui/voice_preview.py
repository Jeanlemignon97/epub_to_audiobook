from __future__ import annotations

import asyncio
import os
import uuid
from pathlib import Path


NARRATION_INSTRUCTIONS = (
    "Parlez avec une voix chaleureuse, posée et humaine. Gardez une articulation "
    "claire, un rythme naturel et des pauses souples, comme une narration de livre "
    "audio. Évitez tout ton publicitaire ou robotique."
)

DEFAULT_PREVIEW_TEXT = (
    "Il ouvrit le livre avec curiosité. Après quelques lignes, le bruit du monde "
    "s’effaça doucement, laissant toute la place à l’histoire."
)


class MissingProviderKey(RuntimeError):
    pass


class VoicePreview:
    def __init__(self, output_dir: str | Path, environ=None, openai_client=None):
        self.output_dir = Path(output_dir)
        self.output_dir.mkdir(parents=True, exist_ok=True)
        self.environ = dict(os.environ if environ is None else environ)
        self.openai_client = openai_client

    def generate(self, provider: str, model: str, voice: str, text: str = DEFAULT_PREVIEW_TEXT) -> Path:
        excerpt = (text or DEFAULT_PREVIEW_TEXT).strip()[:700]
        destination = self.output_dir / f"preview-{uuid.uuid4().hex}.mp3"
        temporary = destination.with_name(f".{destination.stem}.part.mp3")
        if provider == "openai":
            if not self.environ.get("OPENAI_API_KEY"):
                raise MissingProviderKey("La variable OPENAI_API_KEY est absente.")
            client = self.openai_client
            if client is None:
                from openai import OpenAI
                client = OpenAI(api_key=self.environ["OPENAI_API_KEY"])
            response = client.audio.speech.create(
                model=model or "gpt-4o-mini-tts", voice=voice or "coral",
                input=excerpt, instructions=NARRATION_INSTRUCTIONS,
                response_format="mp3", speed=1.0,
            )
            temporary.write_bytes(response.content)
        elif provider == "edge":
            import edge_tts

            async def save_edge():
                communication = edge_tts.Communicate(excerpt, voice or "fr-FR-DeniseNeural")
                await communication.save(str(temporary))

            asyncio.run(save_edge())
        else:
            raise ValueError("L’aperçu est disponible avec OpenAI ou Edge.")
        if not temporary.is_file() or temporary.stat().st_size == 0:
            raise RuntimeError("L’aperçu vocal est vide.")
        temporary.replace(destination)
        return destination
