from pathlib import Path

import pytest

from common.paths import raw_dir_for, work_dir_for


@pytest.mark.parametrize("video_id", ["../escape", "a/b", r"C:\\escape", "", "a" * 129])
def test_video_id_cannot_escape_data_roots(video_id):
    with pytest.raises(ValueError):
        work_dir_for(video_id)
    with pytest.raises(ValueError):
        raw_dir_for(video_id, "2026-10")


def test_video_id_uses_isolated_directory(tmp_path, monkeypatch):
    import common.paths as paths

    monkeypatch.setattr(paths, "DATA_WORK", tmp_path / "work")
    target = work_dir_for("video-001")
    assert target == Path(tmp_path / "work" / "video-001")
    assert target.is_dir()
