# -*- coding: utf-8 -*-
"""W1（2026-10-31）：E2E 只测 release artifact + live version/hash smoke（只读）。

口径（`docs/plans/2026-10-31_零界研发计划_v2_收口与闭环.md` W1「一次登录、一个发布单元、
一条回滚路径」）：E2E 的断言对象是**生产 release artifact**（current 指针 + manifest.json
记录的文件 hash），不是主仓工作树里的源文件；另加一条 live smoke，只读 GET 8103
`/api/ide/version` 做线上↔本地对账。

三层证据互相独立、逐层收口（生产 helper 的缺陷不能自证清白，故本文件不复用
`ide_release_info.build_info`，哈希一律在本用例内独立复算）：

    1. manifest.json 记录   ↔ artifact 目录内文件实际 sha256/bytes（本机独立复算）；
    2. current.json 指针    ↔ current junction 实际指向（无 drift，Windows junction）；
    3. 8103 /api/ide/version ↔ 本机 manifest/文件（线上回报的 version/commit/sha256 必须一致）。

离线/无认证可跑：
- 8103 是 `tools/check_ports.py` 里的 OPTIONAL_OFFLINE 按需服务，未起/离线 → skip（不判失败）；
- 端点若需认证（401/403）→ skip 并注明；显式 `LEGION_IDE_LIVE_SMOKE=0` 可整体关闭 live 探测；
- release 根/current.json 缺失 = 非生产机（开发机无 _vault）→ skip；但 pointer 在而
  artifact/manifest 缺 = 发布单元坏 → **fail-closed 失败**，绝不静默回退主仓工作树。

边界：只读——不发任何写请求、不重启服务、不发布 artifact、不写 artifact/profile、无凭证。
"""
from __future__ import annotations

import hashlib
import http.client
import json
import os
import socket
import sys
from pathlib import Path

import pytest

# release 根：与 tools/release_legion_ide.py / agent_platform/ide_release_info.py 同一权威路径；
# LEGION_IDE_RELEASE_ROOT 仅供测试机指向临时根（离线 fixture）使用，生产默认不设。
RELEASE_ROOT = Path(
    os.environ.get("LEGION_IDE_RELEASE_ROOT")
    or r"C:\项目\_vault\legion_ide\release\legion-ide"
)
# 发布单元必须包含的三个前端文件（W1 验收口径点名）；其余 manifest 登记文件同样逐个核对。
REQUIRED_FILES = ("app.js", "app.css", "a0-mainchat.js")
SCHEMA = "legion-ide-release/v1"
# live smoke：8103 独立 IDE（军团独立IDE / legion_ide serve），只读版本端点。
LIVE_HOST = "127.0.0.1"
LIVE_PORT = 8103
LIVE_PATH = "/api/ide/version"
LIVE_TIMEOUT = 3.0
LIVE_SERVICE = "legion-ide-release"
# LEGION_IDE_LIVE_SMOKE=0 时只跑离线 artifact 断言（给离线流水线留开关）。
LIVE_ENABLED = str(os.environ.get("LEGION_IDE_LIVE_SMOKE", "1")).strip() != "0"


# ── 基础工具（只读）─────────────────────────────────────────────────────────

def _read_json(path: Path) -> dict:
    """读 JSON；坏文件按发布单元损坏处理（pytest.fail，不静默吞）。"""
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        pytest.fail("读取 JSON 失败：%s（%s）" % (path, exc))
    if not isinstance(data, dict) or not data:
        pytest.fail("JSON 内容为空/非对象：%s" % path)
    return data


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def _norm(path: Path | str) -> str:
    """路径归一（Windows 大小写/分隔符不敏感），用于 junction 指向比对。"""
    return os.path.normcase(os.path.normpath(os.path.realpath(str(path))))


def _release_pointer_or_skip() -> tuple[dict, Path]:
    """读 current 指针；无 release 根/无 current.json → skip（非生产机）。"""
    pointer = RELEASE_ROOT / "current.json"
    if not RELEASE_ROOT.is_dir() or not pointer.is_file():
        pytest.skip("无 release artifact（%s 不存在；非生产机/未发布）" % pointer)
    current = _read_json(pointer)
    art = Path(str(current.get("dir") or (RELEASE_ROOT / "current")))
    return current, art


def _load_release() -> tuple[dict, dict, Path]:
    """读取 (current 指针, manifest, artifact 目录)，发布单元缺失即 fail-closed。"""
    current, art = _release_pointer_or_skip()
    if not art.is_dir():
        pytest.fail("release 指针指向的 artifact 目录不存在：%s（发布单元不完整，fail-closed）" % art)
    manifest_path = art / "manifest.json"
    if not manifest_path.is_file():
        pytest.fail("artifact 缺 manifest.json：%s（发布单元不完整，fail-closed）" % art)
    manifest = _read_json(manifest_path)
    return current, manifest, art


def _local_release_or_none():
    """尽力读本机 release（live 对账用）；读不到返回 None，不 skip 也不 fail。"""
    pointer = RELEASE_ROOT / "current.json"
    if not pointer.is_file():
        return None
    try:
        current = json.loads(pointer.read_text(encoding="utf-8"))
        if not isinstance(current, dict) or not current:
            return None
        art = Path(str(current.get("dir") or (RELEASE_ROOT / "current")))
        manifest = json.loads((art / "manifest.json").read_text(encoding="utf-8"))
    except (OSError, ValueError, TypeError):
        return None
    if not isinstance(manifest, dict) or not manifest:
        return None
    return current, manifest, art


# ── 1. artifact 文件 ↔ manifest 哈希 ───────────────────────────────────────

def test_release_artifact_files_match_manifest():
    """release artifact 内每个文件的 sha256/bytes 必须与 manifest.json 记录一致。

    app.js / app.css / a0-mainchat.js 三个前端文件是 W1 点名口径，必须出现在
    manifest 与 artifact 目录内；README.md 等其余登记文件一并核对（逐项全绿）。
    """
    current, manifest, art = _load_release()

    assert manifest.get("schema") == SCHEMA, (
        "manifest.schema 非 %s：%r" % (SCHEMA, manifest.get("schema")))
    version = str(current.get("version") or manifest.get("version") or "")
    assert version, "current.json/manifest 均无 version，无法标识发布单元"
    assert str(manifest.get("version") or "") == version, (
        "指针版本与 manifest 版本不一致：%r != %r"
        % (current.get("version"), manifest.get("version")))
    assert art.name == version, (
        "artifact 目录名应等于发布版本号：目录=%r version=%r" % (art.name, version))
    commit = str(manifest.get("commit") or "")
    assert len(commit) == 40 and all(c in "0123456789abcdef" for c in commit), (
        "manifest.commit 不是 40 位十六进制 sha1：%r" % commit)
    assert str(manifest.get("built_at") or ""), "manifest 缺 built_at（构建时间戳）"

    files = manifest.get("files") or {}
    assert isinstance(files, dict) and files, "manifest.files 为空，无法对照 artifact 文件"
    missing = [name for name in REQUIRED_FILES if name not in files]
    assert not missing, "manifest 未登记必需前端文件：%s（现有：%s）" % (
        missing, sorted(files))

    mismatches = []
    for name in sorted(files):
        meta = files.get(name) or {}
        fp = art / str(name)
        if not fp.is_file():
            mismatches.append("%s: artifact 文件缺失" % name)
            continue
        got_sha = _sha256(fp)
        got_bytes = fp.stat().st_size
        exp_sha = str(meta.get("sha256") or "")
        if not exp_sha:
            mismatches.append("%s: manifest 未记录 sha256" % name)
        elif got_sha != exp_sha:
            mismatches.append("%s: sha256=%s != manifest=%s" % (name, got_sha, exp_sha))
        exp_bytes = meta.get("bytes")
        if exp_bytes is not None:
            try:
                exp_bytes = int(exp_bytes)
            except (TypeError, ValueError):
                mismatches.append("%s: manifest bytes 非整数：%r" % (name, exp_bytes))
            else:
                if exp_bytes != got_bytes:
                    mismatches.append("%s: bytes=%d != manifest=%d"
                                      % (name, got_bytes, exp_bytes))
    assert not mismatches, "artifact 与 manifest 不一致（发布单元损坏）：\n  " + \
        "\n  ".join(mismatches)


# ── 2. current.json 指针 ↔ current junction（无 drift）─────────────────────

def test_current_junction_targets_pointer_dir():
    """current junction 实际指向必须等于 current.json 指针（禁止「指针变了、目录没切」）。"""
    current, art = _release_pointer_or_skip()
    link = RELEASE_ROOT / "current"
    if not link.exists():
        pytest.fail("current junction 缺失：%s（指针已存在时发布单元必须含 current 链接）" % link)
    got = _norm(link)
    want = _norm(art)
    assert got == want, (
        "current junction drift：current 实际指向 %s，current.json 指针指向 %s"
        % (got, want))


# ── 3. live smoke：8103 /api/ide/version ↔ 本机 artifact ───────────────────

def _http_get(port: int, path: str, timeout: float = LIVE_TIMEOUT):
    """无重定向只读 GET，返回 (status, content_type, body)。"""
    conn = http.client.HTTPConnection(LIVE_HOST, port, timeout=timeout)
    try:
        conn.request("GET", path, headers={"Connection": "close"})
        resp = conn.getresponse()
        body = resp.read()
        return resp.status, resp.getheader("Content-Type") or "", body
    finally:
        conn.close()


def test_live_version_endpoint_smoke():
    """live version/hash smoke：GET 8103 /api/ide/version，校验 all_match 与三元对账。

    只读 GET（无凭证、不重启、不发布）。服务未起/离线 → skip；401/403 需认证 → skip；
    200 → 断言 ok/all_match/service/schema/必需文件 hash，并与本机 manifest 对账。
    """
    if not LIVE_ENABLED:
        pytest.skip("LEGION_IDE_LIVE_SMOKE=0 显式关闭 live 探测（仅跑离线 artifact 断言）")
    try:
        status, _ctype, body = _http_get(LIVE_PORT, LIVE_PATH)
    except OSError as exc:
        pytest.skip("8103 不可达（OPTIONAL_OFFLINE 按需服务未起/离线）：%s" % exc)
    if status in (401, 403):
        pytest.skip("8103 /api/ide/version 需认证（HTTP %d），按 W1 口径跳过" % status)
    assert status == 200, (
        "8103 /api/ide/version 期望 HTTP 200，实得 %s；body=%r" % (status, body[:500]))
    try:
        payload = json.loads(body.decode("utf-8"))
    except (UnicodeDecodeError, ValueError) as exc:
        pytest.fail("8103 返回非 JSON（%s）：%r" % (exc, body[:400]))
    assert isinstance(payload, dict) and payload, "8103 返回 JSON 非对象/为空"
    detail = json.dumps(payload, ensure_ascii=False)[:1500]
    assert payload.get("ok") is True, "live 端点 ok != true：%s" % detail
    assert payload.get("service") == LIVE_SERVICE, (
        "8103 端点 service 非 %s（端口被其它服务占用？）：%r" % (LIVE_SERVICE, payload.get("service")))
    assert payload.get("manifest_schema") == SCHEMA, (
        "live manifest_schema 非 %s：%r" % (SCHEMA, payload.get("manifest_schema")))
    assert payload.get("all_match") is True, (
        "live all_match != true（线上 artifact 与 manifest 不一致）：%s" % detail)

    live_files = payload.get("files") or {}
    assert isinstance(live_files, dict) and live_files, "live 端点未回报 files 明细"
    missing = [name for name in REQUIRED_FILES if name not in live_files]
    assert not missing, "live 端点未回报必需前端文件：%s（现有：%s）" % (
        missing, sorted(live_files))
    bad = []
    for name in sorted(live_files):
        info = live_files.get(name) or {}
        if info.get("exists") is not True:
            bad.append("%s: exists=%r" % (name, info.get("exists")))
        if info.get("match") is not True:
            bad.append("%s: match=%r（sha256=%s）"
                       % (name, info.get("match"), info.get("sha256")))
        if not str(info.get("sha256") or ""):
            bad.append("%s: 未回报 sha256" % name)
    assert not bad, "live 端点文件校验未全绿：\n  " + "\n  ".join(bad)

    # 与本机 artifact 对账：线上回报的 version/commit/hash 必须等于本机 manifest/文件实际值。
    local = _local_release_or_none()
    if local is None:
        return  # 非生产机只校验 live 自洽（上面的 all_match/ok 已覆盖）
    current, manifest, art = local
    local_version = str(current.get("version") or manifest.get("version") or "")
    assert payload.get("version") == local_version, (
        "live version %r != 本机指针 %r（线上/本地不是同一发布单元）"
        % (payload.get("version"), local_version))
    assert payload.get("commit") == str(manifest.get("commit") or ""), (
        "live commit %r != 本机 manifest %r" % (payload.get("commit"), manifest.get("commit")))
    live_dir = str(payload.get("artifact_dir") or "")
    if live_dir:
        assert _norm(live_dir) == _norm(art), (
            "live artifact_dir %r != 本机 artifact %r" % (live_dir, str(art)))
    local_files = manifest.get("files") or {}
    for name in REQUIRED_FILES:
        meta = local_files.get(name) or {}
        exp_sha = str(meta.get("sha256") or "")
        live_sha = str((live_files.get(name) or {}).get("sha256") or "")
        if exp_sha:
            assert live_sha == exp_sha, (
                "%s: live sha256=%s != 本机 manifest=%s" % (name, live_sha, exp_sha))
        fp = art / name
        if fp.is_file():
            assert live_sha == _sha256(fp), (
                "%s: live sha256=%s != artifact 文件实际 hash=%s"
                % (name, live_sha, _sha256(fp)))


def test_live_smoke_skips_when_service_offline(monkeypatch):
    """8103 未起（离线）时必须 skip 而非失败——锁死「测试可离线跑」口径。"""
    # 取一个刚释放的临时端口当「无服务」目标，避免碰生产 8103（只读、不重启任何服务）。
    sock = socket.socket()
    try:
        sock.bind((LIVE_HOST, 0))
        dead_port = sock.getsockname()[1]
    finally:
        sock.close()
    monkeypatch.setattr(sys.modules[__name__], "LIVE_PORT", dead_port)
    monkeypatch.setattr(sys.modules[__name__], "LIVE_ENABLED", True)  # 不受外部 env 影响
    with pytest.raises(pytest.skip.Exception) as excinfo:
        test_live_version_endpoint_smoke()
    assert "不可达" in str(excinfo.value)
