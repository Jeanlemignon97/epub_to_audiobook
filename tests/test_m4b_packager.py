from pathlib import Path
from subprocess import CalledProcessError, CompletedProcess

import pytest

from audiobook_generator.core.m4b_packager import (
    ChapterTrack,
    M4BPackager,
    PackagingError,
    natural_track_key,
)


def test_tracks_are_sorted_by_numeric_prefix(tmp_path: Path):
    tracks = [tmp_path / "0010_Ten.mp3", tmp_path / "0002_Two.mp3"]
    assert sorted(tracks, key=natural_track_key)[0].name == "0002_Two.mp3"


def test_ffmetadata_escapes_titles_and_accumulates_boundaries(tmp_path: Path):
    first = tmp_path / "0001.mp3"
    second = tmp_path / "0002.mp3"
    first.write_bytes(b"a")
    second.write_bytes(b"b")
    durations = {str(first): "10.5\n", str(second): "2.25\n"}

    def runner(command, **kwargs):
        if command[0] == "ffprobe":
            return CompletedProcess(command, 0, stdout=durations[command[-1]])
        Path(command[-1]).write_bytes(b"m4b")
        return CompletedProcess(command, 0, stdout="")

    packager = M4BPackager(runner=runner, which=lambda _: "/usr/bin/tool")
    destination = tmp_path / "book.m4b"
    result = packager.package(
        [ChapterTrack(first, "Début; test"), ChapterTrack(second, "Fin")],
        destination,
        title="Livre = test",
        author="Auteur",
    )

    assert result == destination
    assert destination.read_bytes() == b"m4b"
    metadata = packager.last_metadata
    assert "title=Livre \\= test" in metadata
    assert "title=Début\\; test" in metadata
    assert "START=0\nEND=10500" in metadata
    assert "START=10500\nEND=12750" in metadata


def test_missing_ffmpeg_keeps_source_tracks(tmp_path: Path):
    track = tmp_path / "0001.mp3"
    track.write_bytes(b"audio")
    packager = M4BPackager(which=lambda _: None)

    with pytest.raises(PackagingError, match="FFmpeg"):
        packager.package([ChapterTrack(track, "Chapitre")], tmp_path / "book.m4b", "Livre", "Auteur")

    assert track.read_bytes() == b"audio"


def test_failed_ffmpeg_does_not_replace_destination(tmp_path: Path):
    track = tmp_path / "0001.mp3"
    track.write_bytes(b"audio")

    def runner(command, **kwargs):
        if command[0] == "ffprobe":
            return CompletedProcess(command, 0, stdout="1.0\n")
        raise CalledProcessError(1, command, stderr="encoding failed")

    packager = M4BPackager(runner=runner, which=lambda _: "/usr/bin/tool")
    destination = tmp_path / "book.m4b"

    with pytest.raises(PackagingError, match="assemblage"):
        packager.package([ChapterTrack(track, "Chapitre")], destination, "Livre", "Auteur")

    assert not destination.exists()
    assert track.exists()
