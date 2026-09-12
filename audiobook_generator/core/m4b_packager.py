from __future__ import annotations

import re
import shutil
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Iterable


class PackagingError(RuntimeError):
    pass


@dataclass(frozen=True)
class ChapterTrack:
    path: Path
    title: str


def natural_track_key(path: Path) -> tuple[int, str]:
    match = re.match(r"(\d+)", path.name)
    return (int(match.group(1)) if match else 10**9, path.name.casefold())


def _ffmetadata_escape(value: str) -> str:
    cleaned = value.replace("\r", " ").replace("\n", " ")
    return re.sub(r"([\\;#=])", r"\\\1", cleaned)


def _concat_escape(path: Path) -> str:
    return str(path.resolve()).replace("'", "'\\''")


class M4BPackager:
    def __init__(
        self,
        ffmpeg: str = "ffmpeg",
        ffprobe: str = "ffprobe",
        runner: Callable = subprocess.run,
        which: Callable = shutil.which,
    ):
        self.ffmpeg = ffmpeg
        self.ffprobe = ffprobe
        self.runner = runner
        self.which = which
        self.last_metadata = ""

    def _require_tools(self) -> None:
        if not self.which(self.ffmpeg) or not self.which(self.ffprobe):
            raise PackagingError(
                "FFmpeg et ffprobe sont nécessaires pour créer le fichier M4B."
            )

    def _duration_ms(self, path: Path) -> int:
        result = self.runner(
            [
                self.ffprobe, "-v", "error", "-show_entries", "format=duration",
                "-of", "default=noprint_wrappers=1:nokey=1", str(path),
            ],
            check=True,
            capture_output=True,
            text=True,
        )
        return max(1, round(float(result.stdout.strip()) * 1000))

    def _metadata(self, tracks: list[ChapterTrack], title: str, author: str) -> str:
        lines = [
            ";FFMETADATA1",
            f"title={_ffmetadata_escape(title)}",
            f"artist={_ffmetadata_escape(author)}",
            f"album={_ffmetadata_escape(title)}",
        ]
        start = 0
        for track in tracks:
            end = start + self._duration_ms(track.path)
            lines.extend([
                "[CHAPTER]", "TIMEBASE=1/1000", f"START={start}", f"END={end}",
                f"title={_ffmetadata_escape(track.title)}",
            ])
            start = end
        return "\n".join(lines) + "\n"

    def package(
        self,
        tracks: Iterable[ChapterTrack],
        destination: str | Path,
        title: str,
        author: str,
    ) -> Path:
        ordered = sorted(list(tracks), key=lambda track: natural_track_key(track.path))
        if not ordered:
            raise PackagingError("Aucun chapitre audio à assembler.")
        missing = [track.path for track in ordered if not track.path.is_file() or track.path.stat().st_size == 0]
        if missing:
            raise PackagingError(f"Chapitre audio absent ou vide : {missing[0]}")
        self._require_tools()

        output = Path(destination)
        output.parent.mkdir(parents=True, exist_ok=True)
        temporary_output = output.with_name(f".{output.stem}.part{output.suffix}")
        temporary_output.unlink(missing_ok=True)

        try:
            with tempfile.TemporaryDirectory(dir=output.parent) as temp_dir:
                temp = Path(temp_dir)
                concat_path = temp / "chapters.txt"
                metadata_path = temp / "metadata.txt"
                concat_path.write_text(
                    "".join(f"file '{_concat_escape(track.path)}'\n" for track in ordered),
                    encoding="utf-8",
                )
                self.last_metadata = self._metadata(ordered, title, author)
                metadata_path.write_text(self.last_metadata, encoding="utf-8")
                self.runner(
                    [
                        self.ffmpeg, "-y", "-f", "concat", "-safe", "0", "-i",
                        str(concat_path), "-i", str(metadata_path), "-map_metadata", "1",
                        "-map_chapters", "1", "-vn", "-c:a", "aac", "-b:a", "96k",
                        "-movflags", "+faststart", str(temporary_output),
                    ],
                    check=True,
                    capture_output=True,
                    text=True,
                )
            if not temporary_output.is_file() or temporary_output.stat().st_size == 0:
                raise PackagingError("FFmpeg n’a produit aucun fichier audio.")
            temporary_output.replace(output)
            return output
        except PackagingError:
            temporary_output.unlink(missing_ok=True)
            raise
        except (OSError, ValueError, subprocess.SubprocessError) as exc:
            temporary_output.unlink(missing_ok=True)
            raise PackagingError(f"Échec de l’assemblage M4B : {exc}") from exc
