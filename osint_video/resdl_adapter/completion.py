"""Safe post-submission media completion verification.

The res-downloader HTTP API acknowledges an asynchronous submission only.  This
module deliberately keeps that acknowledgement separate from media completion:
a file is marked complete only after filesystem, MIME, digest, and ffprobe
checks succeed.
"""

from __future__ import annotations

import hashlib
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Iterable, Mapping

from .security import PathPolicy, PathPolicyError


CompletionStatus = str


@dataclass(frozen=True)
class MediaVerificationResult:
    """Sanitized result of checking one candidate file.

    ``file_id`` is a digest-derived, non-path identifier.  The absolute path,
    decode key, token, and other credentials are intentionally never returned.
    """

    status: CompletionStatus
    reason: str
    file_id: str | None = None
    size: int = 0
    sha256: str | None = None
    mime: str | None = None
    extension: str | None = None

    @property
    def complete(self) -> bool:
        """Whether all completion checks passed."""
        return self.status == "complete"

    @property
    def usable(self) -> bool:
        """Alias used by callers that consume verified media."""
        return self.complete

    @property
    def verified(self) -> bool:
        """Backward-compatible alias for :attr:`complete`."""
        return self.complete


@dataclass(frozen=True)
class _FileSnapshot:
    size: int
    mtime_ns: int
    inode: int


FfprobeRunner = Callable[[Path], Any]


class MediaCompletionVerifier:
    """Verify that an asynchronously submitted media file is complete.

    Args:
        path_policy: Allowlist constraining every candidate path.
        ffprobe_runner: Optional injectable runner.  It receives a validated
            ``Path`` and may return bool, a mapping with ``success``/``ok`` or
            a subprocess-like object with ``returncode``.  A false/error result
            is represented as ``degraded`` rather than raising.
        stability_delay: Delay between the two stat calls.  Tests may use zero;
            production callers can use a small positive value.
        digest_algorithm: ``sha256`` by default; ``md5`` is retained for
            compatibility with older download manifests.
    """

    _EXTENSION_MIME: Mapping[str, str] = {
        ".mp4": "video/mp4",
        ".m4v": "video/mp4",
        ".mov": "video/quicktime",
        ".webm": "video/webm",
        ".mkv": "video/x-matroska",
    }
    _ALLOWED_EXTENSIONS = frozenset(_EXTENSION_MIME)

    def __init__(
        self,
        path_policy: PathPolicy,
        *,
        ffprobe_runner: FfprobeRunner | None = None,
        stability_delay: float = 0.0,
        digest_algorithm: str = "sha256",
    ) -> None:
        if not isinstance(path_policy, PathPolicy):
            raise TypeError("path_policy must be a PathPolicy")
        if stability_delay < 0:
            raise ValueError("stability_delay must be non-negative")
        algorithm = digest_algorithm.lower().strip()
        if algorithm not in {"sha256", "md5"}:
            raise ValueError("digest_algorithm must be sha256 or md5")
        self._path_policy = path_policy
        self._ffprobe_runner = ffprobe_runner or self._default_ffprobe
        self._stability_delay = stability_delay
        self._digest_algorithm = algorithm

    def verify(self, candidate: str | Path) -> MediaVerificationResult:
        """Verify one candidate without exposing its raw path or raising success errors."""
        try:
            candidate_path = Path(candidate).expanduser()
            # Inspect the lexical candidate before PathPolicy resolves it.  A
            # symlink may otherwise resolve to an in-root target and evade the
            # no-link rule.
            if candidate_path.is_symlink():
                return MediaVerificationResult(status="failed", reason="symlink_not_allowed")
            path = self._path_policy.validate(candidate_path)
        except (PathPolicyError, OSError, ValueError):
            return MediaVerificationResult(status="failed", reason="path_outside_allowed_root")

        # A candidate is never allowed to be a directory or a symlink.  The
        # latter avoids validating a path that can be swapped outside the root.
        try:
            if path.is_symlink() or not path.is_file():
                return MediaVerificationResult(status="pending", reason="file_not_ready")
            first = self._snapshot(path)
            if first is None or first.size <= 0:
                return MediaVerificationResult(status="pending", reason="file_not_ready")
            if self._stability_delay:
                time.sleep(self._stability_delay)
            second = self._snapshot(path)
        except OSError:
            return MediaVerificationResult(status="pending", reason="file_not_ready")
        if second is None or first != second:
            return MediaVerificationResult(status="pending", reason="file_unstable")

        extension = path.suffix.lower()
        expected_mime = self._EXTENSION_MIME.get(extension)
        if expected_mime is None:
            return MediaVerificationResult(status="failed", reason="unsupported_extension", size=second.size)
        try:
            mime = self._detect_mime(path, extension)
        except OSError:
            return MediaVerificationResult(status="pending", reason="file_not_ready", size=second.size)
        if mime != expected_mime:
            return MediaVerificationResult(
                status="failed", reason="mime_extension_mismatch", size=second.size,
                mime=mime, extension=extension,
            )

        try:
            digest = self._digest(path)
        except OSError:
            return MediaVerificationResult(status="pending", reason="file_not_ready", size=second.size)
        file_id = f"{self._digest_algorithm}:{digest[:16]}"
        probe_status = self._run_ffprobe(self._ffprobe_runner, path)
        if probe_status is not True:
            return MediaVerificationResult(
                status="degraded", reason="ffprobe_failed", file_id=file_id,
                size=second.size, sha256=digest if self._digest_algorithm == "sha256" else None,
                mime=mime, extension=extension,
            )
        return MediaVerificationResult(
            status="complete", reason="verified", file_id=file_id, size=second.size,
            sha256=digest if self._digest_algorithm == "sha256" else None,
            mime=mime, extension=extension,
        )

    def verify_candidates(self, candidates: Iterable[str | Path]) -> list[MediaVerificationResult]:
        """Verify candidates in order, returning one sanitized result each."""
        return [self.verify(candidate) for candidate in candidates]

    # Common names used by worker integrations.
    check = verify

    @staticmethod
    def _snapshot(path: Path) -> _FileSnapshot | None:
        try:
            stat_result = path.stat()
        except OSError:
            return None
        return _FileSnapshot(stat_result.st_size, stat_result.st_mtime_ns, stat_result.st_ino)

    @classmethod
    def _detect_mime(cls, path: Path, extension: str) -> str | None:
        with path.open("rb") as handle:
            header = handle.read(64)
        # ISO-BMFF (MP4/MOV) requires an ftyp box at offset four.  The brand
        # lets us distinguish MP4-family files from arbitrary renamed files.
        if len(header) >= 12 and header[4:8] == b"ftyp":
            return "video/quicktime" if extension == ".mov" else "video/mp4"
        if header.startswith(b"\x1a\x45\xdf\xa3"):
            return "video/webm" if extension == ".webm" else "video/x-matroska"
        # Do not trust an extension or ``mimetypes`` alone: a renamed text
        # file must never pass completion.  Unknown/short headers are rejected
        # even when an injected ffprobe runner is permissive.
        return None

    def _digest(self, path: Path) -> str:
        digest = hashlib.new(self._digest_algorithm)
        with path.open("rb") as handle:
            for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                digest.update(chunk)
        return digest.hexdigest()

    @staticmethod
    def _default_ffprobe(path: Path) -> bool:
        """Probe media without shell interpolation or credential exposure."""
        try:
            completed = subprocess.run(
                ["ffprobe", "-v", "error", "-show_entries", "format=format_name", "-of", "default=nw=1", str(path)],
                stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=False,
                timeout=15,
            )
        except (OSError, subprocess.SubprocessError):
            return False
        return completed.returncode == 0

    @staticmethod
    def _run_ffprobe(runner: FfprobeRunner, path: Path) -> bool:
        try:
            value = runner(path)
        except Exception:  # injected runners are untrusted boundaries
            return False
        if isinstance(value, bool):
            return value
        if isinstance(value, Mapping):
            status = value.get("success", value.get("ok", value.get("returncode")))
            return status is True or status == 0
        return getattr(value, "returncode", 1) == 0


class MediaCompletionWatcher:
    """Watch candidate output files and expose the verifier's safe results."""

    def __init__(self, verifier: MediaCompletionVerifier) -> None:
        self._verifier = verifier

    def check(self, candidates: Iterable[str | Path]) -> list[MediaVerificationResult]:
        """Check candidates without changing queue state or submitting work."""
        return self._verifier.verify_candidates(candidates)

    verify = check


__all__ = [
    "CompletionStatus",
    "FfprobeRunner",
    "MediaCompletionVerifier",
    "MediaCompletionWatcher",
    "MediaVerificationResult",
]
