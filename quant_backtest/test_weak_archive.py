# -*- coding: utf-8 -*-
"""weak_track_archive（§14）快照生成与守卫验证（K35-y · 可重复跑）。

T1 快照头：标题/统计行（结案数/类型分布/A 类兑现）
T2 节结构：每案一节（emoji 色标+类型全名+源文件名），经验条目在内
T3 守卫：ScopeError 拦截（不发半截链路，scopes 识别）
T4 空态：无 close md → 空提示 rc=0（CLI 实跑）
T5 K35-aa 在案节：watch 非空补「在案观察中」，None 跳过
T6 K35-aa _ensure_folder：state 缓存（二次不调 create）/无 state 创建并写
T7 K35-aa _delete_file 容错：异常只 warn 返回 False
T8 K35-aa ScopeError 携带响应内授权 URL

隔离：T1-T3/T5-T8 fake 目录/monkeypatch；全部 PASS 退出码 0。
"""

import json
import os
import shutil
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import test_weak_learn as tl  # noqa: E402
import weak_track_summary as ws  # noqa: E402
import weak_track_archive as wa  # noqa: E402

PY = r"C:\Program Files\Python311\python.exe"
TMP = os.path.join(tempfile.gettempdir(), "weak_archive_test")
fails = []


def check(name, cond, detail=""):
    print(
        "%s %s%s"
        % (
            "PASS" if cond else "FAIL",
            name,
            "｜" + detail if detail and not cond else "",
        )
    )
    if not cond:
        fails.append(name)


def main():
    shutil.rmtree(TMP, ignore_errors=True)
    os.makedirs(TMP)
    tl.TMP = TMP  # fake 造数写进本测试目录
    tl.put(tl.BASE)
    tl.put(tl.MORE)
    cases = ws.parse_cases(TMP)
    md = wa.build_archive_md(cases, "20261003")

    check("T1 标题", md.splitlines()[0] == "# weak 案例库 20261003")
    check("T1 统计行", "结案 6 例（A 2/B 3/C 1）" in md and "33%" in md)
    check("T2 经验条目", "## 经验条目" in md and "剧本兑现" in md)
    check("T2 节数 7", md.count("## ") == 7, str(md.count("## ")))
    check("T2 色标 emoji", md.count("🟢") == 2 and "🟠" in md and "🔴" in md)
    check("T2 源文件名", "weak_close_301216_20260901.md" in md)

    # T3/T8 守卫+URL 透传：monkeypatch urlopen 抛 scope 响应，测真实 _api
    orig = wa._api
    scope_body = (
        '{"msg":"Access denied. scopes is required: '
        "[drive:file:upload, drive:file].授权链接："
        "https://open.feishu.cn/app/cli_aa3e4f1b58f85cdd/auth"
        '?q=drive:file:upload&op_from=openapi&token_type=tenant"}'
    )
    real_urlopen = wa.urllib.request.urlopen

    def fake_urlopen(req, timeout=None):
        raise wa.urllib.error.HTTPError(
            "u", 400, "scope", {}, __import__("io").BytesIO(scope_body.encode())
        )

    wa.urllib.request.urlopen = fake_urlopen
    try:
        wa._api("T", "https://open.feishu.cn/open-apis/x")
        check("T3 守卫触发", False, "no exception")
    except wa.ScopeError as e:
        check("T3 守卫触发", e.scopes == ["drive:file:upload", "drive:file"])
        check(
            "T8 URL 透传",
            e.url.startswith("https://open.feishu.cn/app/")
            and "q=drive:file:upload" in e.url,
        )
    except Exception as e:  # noqa: BLE001
        check("T3 守卫触发", False, str(e)[:80])
        check("T8 URL 透传", False, str(e)[:80])
    finally:
        wa.urllib.request.urlopen = real_urlopen

    # T5 在案节
    w = [
        {
            "code": "301216.SZ",
            "name": "万凯",
            "days_open": 30,
            "days_stall": 0,
            "task": "Ready",
            "flags": [],
        }
    ]
    md2 = wa.build_archive_md(cases, watch=w)
    check(
        "T5 在案节有",
        "## 在案观察中" in md2
        and "301216.SZ 万凯：跟踪 30 日历天·末次 0 天前·任务 Ready" in md2,
    )
    check("T5 None 跳过", "在案观察中" not in md)

    # T6 _ensure_folder：state 缓存（二次不调 create）/无 state 创建并写
    calls = []

    def fake_api2(tok, url, data=None, headers=None, ctype=None, method=None):
        calls.append(url.split("/open-apis/")[-1].split("?")[0])
        if "create_folder" in url:
            return {"code": 0, "data": {"token": "fldNEW"}}
        return {"code": 0, "data": {"token": "fldROOT"}}

    wa._api = fake_api2
    state_bak, wa.STATE = wa.STATE, os.path.join(TMP, "state.json")
    if os.path.isfile(wa.STATE):
        os.remove(wa.STATE)
    ft1 = wa._ensure_folder("T", "fldROOT")
    ft2 = wa._ensure_folder("T", "fldROOT")
    check(
        "T6 state 缓存",
        ft1 == "fldNEW"
        and ft2 == "fldNEW"
        and calls.count("drive/v1/files/create_folder") == 1,
    )
    check(
        "T6 state 落盘",
        json.load(open(wa.STATE, encoding="utf-8")) == {"folder": "fldNEW"},
    )
    wa.STATE = state_bak
    wa._api = orig

    # T7 _delete_file 容错：异常只 warn 返回 False
    def boom(*a, **k):
        raise RuntimeError("no scope")

    wa._api = boom
    ok = wa._delete_file("T", "boxX")
    check("T7 清理容错", ok is False)
    wa._api = orig

    # T4：真实 CLI（当前在案 1 案+0 结案 → 快照含在案节；全空才空态）
    r = subprocess.run(
        [PY, "-X", "utf8", os.path.join(HERE, "weak_track_archive.py")],
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=180,
    )
    check("T4 CLI rc=0", r.returncode == 0)
    check(
        "T4 形态",
        "暂无已结案案例" in r.stdout
        or ("weak 案例库" in r.stdout and "预览模式" in r.stdout),
        r.stdout[-120:],
    )

    shutil.rmtree(TMP, ignore_errors=True)
    print(
        "\n%s（%d 项失败）"
        % ("ALL PASS" if not fails else "FAILED: %s" % fails, len(fails))
    )
    return 1 if fails else 0


if __name__ == "__main__":
    raise SystemExit(main())
