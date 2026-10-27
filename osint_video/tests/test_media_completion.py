"""Pure-Python tests for asynchronous media completion verification."""

from __future__ import annotations

from pathlib import Path

from resdl_adapter import MediaCompletionVerifier, PathPolicy


def _mp4(path: Path, payload: bytes = b"payload") -> Path:
    path.write_bytes((8).to_bytes(4, "big") + b"ftyp" + b"isom" + payload)
    return path


def test_accepted_submission_does_not_mean_complete(tmp_path: Path) -> None:
    verifier = MediaCompletionVerifier(
        PathPolicy((tmp_path,)), ffprobe_runner=lambda path: True
    )
    result = verifier.verify(tmp_path / "not-yet-written.mp4")
    assert result.status == "pending"
    assert result.complete is False


def test_file_unstable_is_pending(tmp_path: Path, monkeypatch) -> None:
    path = _mp4(tmp_path / "sample.mp4")
    verifier = MediaCompletionVerifier(
        PathPolicy((tmp_path,)), ffprobe_runner=lambda candidate: True
    )
    snapshots = iter(
        [
            type("S", (), {"size": 20, "mtime_ns": 1, "inode": 1})(),
            type("S", (), {"size": 21, "mtime_ns": 2, "inode": 1})(),
        ]
    )
    monkeypatch.setattr(verifier, "_snapshot", lambda candidate: next(snapshots))
    result = verifier.verify(path)
    assert result.status == "pending"
    assert result.reason == "file_unstable"


def test_ffprobe_failure_is_degraded(tmp_path: Path) -> None:
    path = _mp4(tmp_path / "sample.mp4")
    verifier = MediaCompletionVerifier(
        PathPolicy((tmp_path,)), ffprobe_runner=lambda candidate: False
    )
    result = verifier.verify(path)
    assert result.status == "degraded"
    assert result.complete is False
    assert result.file_id is not None


def test_success_is_usable_and_has_no_raw_path(tmp_path: Path) -> None:
    path = _mp4(tmp_path / "sample.mp4")
    verifier = MediaCompletionVerifier(
        PathPolicy((tmp_path,)), ffprobe_runner=lambda candidate: {"success": True}
    )
    result = verifier.verify(path)
    assert result.status == "complete"
    assert result.usable is True
    assert not hasattr(result, "raw_path")
    assert str(path) not in repr(result)


def test_path_outside_policy_fails_without_path_disclosure(tmp_path: Path) -> None:
    allowed = tmp_path / "allowed"
    outside = tmp_path / "outside.mp4"
    outside.write_bytes(b"not media")
    verifier = MediaCompletionVerifier(
        PathPolicy((allowed,)), ffprobe_runner=lambda candidate: True
    )
    result = verifier.verify(outside)
    assert result.status == "failed"
    assert result.reason == "path_outside_allowed_root"
    assert str(outside) not in repr(result)
