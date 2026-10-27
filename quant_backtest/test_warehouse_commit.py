"""真实临时 Parquet 提交；不加载采集器或秘密配置。"""

import hashlib
import importlib
import json
import os
from pathlib import Path

import pandas as pd
import pytest

from warehouse_runs import RunStore, RunBusy


def sha(path):
    return (
        hashlib.sha256(Path(path).read_bytes()).hexdigest()
        if Path(path).exists()
        else None
    )


def frame(values=(1,)):
    return pd.DataFrame({"code": [str(i) for i in range(len(values))], "value": values})


def make_run(root, units=("a",), request="one", store=None):
    root.mkdir(parents=True, exist_ok=True)
    store = store or RunStore(root / "runs.sqlite3")
    plan = dict(
        version=1,
        source="tdx",
        resource=str(root.resolve()),
        market="cn",
        date="2026-10-09",
        selected_tables=["daily"],
        retry_of=None,
        retry_units=[],
        registry_digest="r",
        targets=[],
        operations=[],
        unsupported=[],
        baseline_digest="b",
        plan_digest="p",
        units=[
            dict(
                unit_id=u,
                table="daily",
                target="2026-10-09",
                kind="date",
                scope={},
                selection_reason="user",
            )
            for u in units
        ],
    )
    run = store.create_run(plan, entry="cli", request_id=request, now=100)
    store.claim(run, pid=os.getpid(), created_at="test-process", now=100)
    return store, run


@pytest.fixture
def commit():
    return importlib.import_module("warehouse_commit")


def do_commit(commit, store, run, target, data=None):
    return commit.commit_frame(
        data if data is not None else frame(),
        target,
        store=store,
        run_id=run,
        unit_id="a",
        primary_key=["code"],
        business_columns=["value"],
        now=101,
    )


def test_revision_is_not_new_row(commit):
    old = pd.DataFrame([{"code": "A", "date": "20261008", "value": 1}])
    new = pd.DataFrame(
        [
            {"code": "A", "date": "20261008", "value": 2},
            {"code": "B", "date": "20261008", "value": 3},
        ]
    )
    result = commit.count_changes(
        old, new, ["code", "date"], business_columns=["value"]
    )
    assert (result["new_rows"], result["revised_rows"], result["duplicate_rows"]) == (
        1,
        1,
        0,
    )


def test_technical_time_is_duplicate_and_nulls_compare_equal(commit):
    old = pd.DataFrame({"code": ["a"], "value": [None], "_fetched_at": ["old"]})
    new = pd.DataFrame({"code": ["a"], "value": [float("nan")], "_fetched_at": ["new"]})
    counts = commit.count_changes(old, new, ["code"], business_columns=["value"])
    assert (counts["new_rows"], counts["revised_rows"], counts["duplicate_rows"]) == (
        0,
        0,
        1,
    )


@pytest.mark.parametrize(
    "keys, columns",
    [(None, ["value"]), (["code"], None), ([], ["value"]), (["code"], [])],
)
def test_unknown_qualification_keeps_business_counts_unknown(commit, keys, columns):
    counts = commit.count_changes(frame(), frame(), keys, business_columns=columns)
    assert (
        counts["new_rows"] is counts["revised_rows"] is counts["duplicate_rows"] is None
    )


@pytest.mark.parametrize(
    "bad",
    [
        pd.DataFrame({"code": ["a", "a"], "value": [1, 2]}),
        pd.DataFrame({"code": [None], "value": [1]}),
        pd.DataFrame({"code": ["a"], "other": [1]}),
    ],
)
def test_invalid_keys_or_missing_business_fields_are_rejected(commit, bad):
    with pytest.raises(ValueError):
        commit.count_changes(frame(), bad, ["code"], business_columns=["value"])


def test_commit_real_bytes_and_negative_net_storage(commit, tmp_path):
    store, run = make_run(tmp_path)
    target = tmp_path / "daily.parquet"
    frame(tuple(range(2000))).to_parquet(target, index=False)
    old_size = target.stat().st_size
    result = do_commit(commit, store, run, target)
    assert pd.read_parquet(target).to_dict("list") == {"code": ["0"], "value": [1]}
    assert result["bytes_written"] == target.stat().st_size
    assert result["net_storage_bytes"] == target.stat().st_size - old_size < 0
    assert result["revised_rows"] == 1 and result["new_rows"] == 0
    assert store.get_run(run)["metrics"]["bytes_written"] == result["bytes_written"]


def test_empty_response_preserves_old_file(commit, tmp_path):
    store, run = make_run(tmp_path)
    target = tmp_path / "daily.parquet"
    frame().to_parquet(target, index=False)
    before = sha(target)
    result = do_commit(commit, store, run, target, frame(()))
    assert result["execution_status"] == "source_empty"
    assert result["bytes_written"] == 0 and sha(target) == before
    assert not [e for e in store.get_run(run)["events"] if e["kind"] == "commit_intent"]


def test_staging_failure_preserves_old_file(commit, tmp_path, monkeypatch):
    store, run = make_run(tmp_path)
    target = tmp_path / "daily.parquet"
    frame().to_parquet(target, index=False)
    before = sha(target)

    def fail(*args, **kwargs):
        raise OSError("暂存磁盘失败")

    monkeypatch.setattr(pd.DataFrame, "to_parquet", fail)
    with pytest.raises(OSError):
        do_commit(commit, store, run, target, frame((2,)))
    assert sha(target) == before


def test_commit_target_outside_controlled_root_rejected(commit, tmp_path):
    root = tmp_path / "root"
    store, run = make_run(root)
    with pytest.raises(ValueError):
        do_commit(commit, store, run, tmp_path / "outside.parquet")
    assert not (tmp_path / "outside.parquet").exists()


def test_complete_failure_real_sha_recovery_is_idempotent(
    commit, tmp_path, monkeypatch
):
    store, run = make_run(tmp_path)
    target = tmp_path / "daily.parquet"
    original = store.event

    def fail(run_id, kind, payload, *, now):
        if kind == "commit_complete":
            raise OSError("完成事件失败")
        return original(run_id, kind, payload, now=now)

    monkeypatch.setattr(store, "event", fail)
    with pytest.raises(OSError):
        do_commit(commit, store, run, target)
    final_sha = sha(target)
    with pytest.raises(commit.PublicationPending):
        with commit.publication_guard(tmp_path, mode="read"):
            pytest.fail("未恢复不能读")
    monkeypatch.setattr(store, "event", original)
    store.reconcile(lambda *args: "dead", now=300)
    recovered = commit.recover_commits(store, run)
    assert recovered[0]["status"] == "recovered"
    assert commit.recover_commits(store, run)[0]["status"] == "complete"
    assert sha(target) == final_sha
    view = store.get_run(run)
    assert view["status"] == "interrupted"
    assert view["metrics"]["new_rows"] == 1
    assert view["units"][0]["result"]["verification"] == {"status": "unknown"}
    with commit.publication_guard(tmp_path, mode="read"):
        assert len(pd.read_parquet(target)) == 1


def test_recovery_rejects_tampered_target_without_rewriting(
    commit, tmp_path, monkeypatch
):
    store, run = make_run(tmp_path)
    original = store.event

    def fail(run_id, kind, payload, *, now):
        if kind == "commit_complete":
            raise OSError("登记失败")
        return original(run_id, kind, payload, now=now)

    monkeypatch.setattr(store, "event", fail)
    target = tmp_path / "daily.parquet"
    with pytest.raises(OSError):
        do_commit(commit, store, run, target)
    monkeypatch.setattr(store, "event", original)
    frame((77,)).to_parquet(target, index=False)
    before = sha(target)
    assert commit.recover_commits(store, run)[0]["status"] == "unknown"
    assert sha(target) == before
    assert store.get_run(run)["metrics"]["new_rows"] is None


def test_recovery_new_run_owner_is_not_bypassed(commit, tmp_path, monkeypatch):
    store, run = make_run(tmp_path)
    original = store.event

    def fail(run_id, kind, payload, *, now):
        if kind == "commit_complete":
            raise OSError("登记失败")
        return original(run_id, kind, payload, now=now)

    monkeypatch.setattr(store, "event", fail)
    with pytest.raises(OSError):
        do_commit(commit, store, run, tmp_path / "daily.parquet")
    monkeypatch.setattr(store, "event", original)
    store.reconcile(lambda *args: "dead", now=300)
    make_run(tmp_path, request="two", store=store)
    with pytest.raises(RunBusy):
        commit.recover_commits(store, run)


def staged_table(root):
    files = []
    for uid, value in [("a", 10), ("b", 20)]:
        target = root / (uid + ".parquet")
        stage = root / (uid + ".staged.parquet")
        frame((1,)).to_parquet(target, index=False)
        frame((value,)).to_parquet(stage, index=False)
        files.append(
            dict(
                target=str(target),
                staged_path=str(stage),
                old_sha256=sha(target),
                new_sha256=sha(stage),
                unit_id=uid,
                primary_key=["code"],
                business_columns=["value"],
                counts=dict(
                    processed_rows=1,
                    new_rows=0,
                    revised_rows=1,
                    duplicate_rows=0,
                    rejected_rows=0,
                    bytes_written=stage.stat().st_size,
                    net_storage_bytes=stage.stat().st_size - target.stat().st_size,
                ),
            )
        )
    manifest = root / "_manifest.json"
    manifest.write_text('{"version":1}', encoding="utf-8")
    return files, dict(
        target=str(manifest), old_sha256=sha(manifest), content={"version": 2}
    )


def test_multifile_manifest_last_and_unit_counts(commit, tmp_path, monkeypatch):
    store, run = make_run(tmp_path, units=("a", "b"))
    files, manifest = staged_table(tmp_path)
    replaced = []
    real_replace = os.replace

    def observe(src, dst):
        if str(dst) in [f["target"] for f in files] + [manifest["target"]]:
            replaced.append(Path(dst).name)
        real_replace(src, dst)

    monkeypatch.setattr(os, "replace", observe)
    result = commit.commit_table(
        files, manifest, store=store, run_id=run, table="daily", now=101
    )
    assert replaced == ["a.parquet", "b.parquet", "_manifest.json"]
    assert result["revised_rows"] == 2
    assert [u["result"]["revised_rows"] for u in store.get_run(run)["units"]] == [1, 1]
    assert json.loads(Path(manifest["target"]).read_text("utf-8")) == {"version": 2}


@pytest.mark.parametrize(
    "fault", ["old_manifest", "stage_sha", "counts", "stage_directory"]
)
def test_multifile_preflight_rejects_wrong_facts(commit, tmp_path, fault):
    store, run = make_run(tmp_path, units=("a", "b"))
    files, manifest = staged_table(tmp_path)
    before = [sha(f["target"]) for f in files]
    if fault == "old_manifest":
        manifest["old_sha256"] = "0" * 64
    elif fault == "stage_sha":
        files[0]["new_sha256"] = "0" * 64
    elif fault == "counts":
        files[0]["counts"]["new_rows"] = 99
    else:
        other = tmp_path / "other"
        other.mkdir()
        stage = other / "a.parquet"
        frame((10,)).to_parquet(stage, index=False)
        files[0]["staged_path"] = str(stage)
    with pytest.raises(ValueError):
        commit.commit_table(
            files, manifest, store=store, run_id=run, table="daily", now=101
        )
    assert [sha(f["target"]) for f in files] == before
    assert not [e for e in store.get_run(run)["events"] if e["kind"] == "commit_intent"]


def test_terminal_recovery_after_complete_but_missing_unit(
    commit, tmp_path, monkeypatch
):
    store, run = make_run(tmp_path)
    target = tmp_path / "daily.parquet"
    original = store.record_unit

    def fail(*args, **kwargs):
        raise OSError("单元结果失败")

    monkeypatch.setattr(store, "record_unit", fail)
    with pytest.raises(OSError):
        do_commit(commit, store, run, target)
    before = sha(target)
    original_complete = next(
        e["payload"]
        for e in store.get_run(run)["events"]
        if e["kind"] == "commit_complete"
    )
    monkeypatch.setattr(store, "record_unit", original)
    store.reconcile(lambda *args: "dead", now=300)
    assert commit.recover_commits(store, run)[0]["status"] == "recovered"
    assert sha(target) == before
    view = store.get_run(run)
    assert view["status"] == "interrupted" and view["metrics"]["new_rows"] == 1
    assert (
        next(e["payload"] for e in view["events"] if e["kind"] == "commit_complete")
        == original_complete
    )
    assert len([e for e in view["events"] if e["kind"] == "commit_recovery"]) == 1
    commit.recover_commits(store, run)
    assert store.get_run(run)["metrics"]["new_rows"] == 1


def test_completed_unit_cannot_publish_again(commit, tmp_path):
    store, run = make_run(tmp_path)
    target = tmp_path / "daily.parquet"
    do_commit(commit, store, run, target)
    before = sha(target)
    with pytest.raises(ValueError):
        do_commit(commit, store, run, target, frame((99,)))
    assert sha(target) == before
    with commit.publication_guard(tmp_path, mode="read"):
        assert pd.read_parquet(target)["value"].tolist() == [1]


def test_recover_old_complete_does_not_block_newer_valid_version(commit, tmp_path):
    store, run = make_run(tmp_path)
    target = tmp_path / "daily.parquet"
    do_commit(commit, store, run, target)
    store.finish(run, "succeeded", exit_code=0, now=102)
    store, newer = make_run(tmp_path, request="newer", store=store)
    do_commit(commit, store, newer, target, frame((9,)))
    assert commit.recover_commits(store, run)[0]["status"] == "complete"
    with commit.publication_guard(tmp_path, mode="read"):
        assert pd.read_parquet(target)["value"].tolist() == [9]


def test_multifile_actual_bytes_include_manifest(commit, tmp_path):
    store, run = make_run(tmp_path, units=("a", "b"))
    files, manifest = staged_table(tmp_path)
    before = (
        sum(Path(f["target"]).stat().st_size for f in files)
        + Path(manifest["target"]).stat().st_size
    )
    result = commit.commit_table(
        files, manifest, store=store, run_id=run, table="daily", now=101
    )
    after = (
        sum(Path(f["target"]).stat().st_size for f in files)
        + Path(manifest["target"]).stat().st_size
    )
    assert result["bytes_written"] == after
    assert result["net_storage_bytes"] == after - before
    assert store.get_run(run)["metrics"]["bytes_written"] == after


def test_failed_intent_does_not_leave_data_or_pending(commit, tmp_path, monkeypatch):
    store, run = make_run(tmp_path)
    target = tmp_path / "daily.parquet"
    frame().to_parquet(target, index=False)
    before = sha(target)
    original = store.event

    def fail(run_id, kind, payload, *, now):
        if kind == "commit_intent":
            raise OSError("意图失败")
        return original(run_id, kind, payload, now=now)

    monkeypatch.setattr(store, "event", fail)
    with pytest.raises(OSError):
        do_commit(commit, store, run, target, frame((9,)))
    assert sha(target) == before
    with commit.publication_guard(tmp_path, mode="read"):
        assert len(pd.read_parquet(target)) == 1


def test_multifile_partial_result_recovery_keeps_first_unit(
    commit, tmp_path, monkeypatch
):
    store, run = make_run(tmp_path, units=("a", "b"))
    files, manifest = staged_table(tmp_path)
    original = store.record_unit

    def fail(run_id, unit_id, result, *, now):
        if unit_id == "b":
            raise OSError("第二单元结果失败")
        return original(run_id, unit_id, result, now=now)

    monkeypatch.setattr(store, "record_unit", fail)
    with pytest.raises(OSError):
        commit.commit_table(
            files, manifest, store=store, run_id=run, table="daily", now=101
        )
    first = store.get_run(run)["units"][0]["result"]
    monkeypatch.setattr(store, "record_unit", original)
    store.reconcile(lambda *args: "dead", now=300)
    assert commit.recover_commits(store, run)[0]["status"] == "recovered"
    view = store.get_run(run)
    assert view["units"][0]["result"] == first
    assert view["units"][1]["result"]["verification"] == {"status": "unknown"}
    assert view["metrics"]["revised_rows"] == 2
    assert (
        view["metrics"]["bytes_written"]
        == sum(Path(f["target"]).stat().st_size for f in files)
        + Path(manifest["target"]).stat().st_size
    )


def test_source_receipt_preserved_without_invented_source_time(commit, tmp_path):
    store, run = make_run(tmp_path)
    store.event(
        run,
        "source_receipt",
        dict(
            receipt_id="r-a",
            run_id=run,
            unit_id="a",
            table="daily",
            provider="tdx",
            channel="gateway",
            source_id="daily-api",
            api_name="daily",
            request_scope={"code": "0"},
            requested_at="2026-10-09T10:00:00+08:00",
            received_at="2026-10-09T10:00:01+08:00",
            source_time=None,
            result_rows=1,
            result_kind="authorized_data",
            error_kind=None,
        ),
        now=100,
    )
    result = do_commit(commit, store, run, tmp_path / "daily.parquet")
    assert result["receipt_ids"] == ["r-a"] and result["provider"] == "tdx"
    receipt = next(
        e["payload"]
        for e in store.get_run(run)["events"]
        if e["kind"] == "source_receipt"
    )
    assert receipt["source_time"] is None
    assert receipt["received_at"] == "2026-10-09T10:00:01+08:00"


def test_recovery_public_intent_id_cannot_unlink_outside_root(commit, tmp_path):
    root = tmp_path / "root"
    store, run = make_run(root)
    target = root / "daily.parquet"
    frame().to_parquet(target, index=False)
    victim = tmp_path / "outside.json"
    victim.write_text("保留", encoding="utf-8")
    intent = dict(
        commit_id="../../../outside",
        unit_id="a",
        table="daily",
        receipt_ids=[],
        target=str(target),
        staged_path=str(root / "unused.parquet"),
        old_sha256=None,
        new_sha256=sha(target),
        counts=dict(
            processed_rows=1,
            new_rows=1,
            revised_rows=0,
            duplicate_rows=0,
            rejected_rows=0,
            bytes_written=target.stat().st_size,
            net_storage_bytes=target.stat().st_size,
        ),
    )
    store.event(run, "commit_intent", intent, now=101)
    assert commit.recover_commits(store, run)[0]["status"] == "recovered"
    assert victim.read_text("utf-8") == "保留"


def test_existing_recovered_complete_missing_unit_respects_new_owner(
    commit, tmp_path, monkeypatch
):
    store, run = make_run(tmp_path)
    original_event = store.event
    original_result = store.record_unit

    def fail_event(run_id, kind, payload, *, now):
        if kind == "commit_complete":
            raise OSError("首次完成登记失败")
        return original_event(run_id, kind, payload, now=now)

    monkeypatch.setattr(store, "event", fail_event)
    with pytest.raises(OSError):
        do_commit(commit, store, run, tmp_path / "daily.parquet")
    monkeypatch.setattr(store, "event", original_event)
    store.reconcile(lambda *args: "dead", now=300)

    def fail_result(*args, **kwargs):
        raise OSError("恢复单元登记失败")

    monkeypatch.setattr(store, "record_unit", fail_result)
    with pytest.raises(OSError):
        commit.recover_commits(store, run)
    monkeypatch.setattr(store, "record_unit", original_result)
    make_run(tmp_path, request="new-owner", store=store)
    with pytest.raises(RunBusy):
        commit.recover_commits(store, run)
    assert store.get_run(run)["units"][0]["result"] is None
