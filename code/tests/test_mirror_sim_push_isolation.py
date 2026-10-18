"""硬约束：跟单子系统绝不调用 record_fill/route_subscription_signal/route_fill/notify_fill。"""
from __future__ import annotations

from pathlib import Path

_FORBIDDEN = ("route_subscription_signal", "route_fill(", "notify_fill", "record_fill")


def test_mirror_service_and_repo_do_not_touch_push():
    root = Path(__file__).resolve().parents[1]
    svc = (root / "src/zhice_os_v2/application/services/mirror_sim_service.py").read_text("utf-8")
    repo = (root / "src/zhice_os_v2/infrastructure/repositories/mirror_sim_repository.py").read_text("utf-8")
    for token in _FORBIDDEN:
        assert token not in svc, f"mirror_sim_service 不应出现 {token}"
        assert token not in repo, f"mirror_sim_repository 不应出现 {token}"


def test_mirror_job_isolated_from_push():
    root = Path(__file__).resolve().parents[1]
    jobs = (root / "src/zhice_os_v2/scheduler_jobs.py").read_text("utf-8")
    start = jobs.index("def mirror_sim_job")
    nxt = jobs.find("\ndef ", start + 1)
    body = jobs[start: nxt if nxt != -1 else len(jobs)]
    for token in ("route_subscription_signal", "notify_fill", "record_fill"):
        assert token not in body
