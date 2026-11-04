# -*- coding: utf-8 -*-
"""§14 案例库飞书归档推送（weak 产线第 14 节 · K35-y 新增）。

「推送给人看」延伸为「归档给人查」：把全部已结案案例生成一份全量
快照 markdown，上传云空间并导入为飞书 docx《weak 案例库 YYYYMMDD》，
推送卡片附文档链接——手机随时翻案例库。

快照式设计（每次全量一篇，天然幂等）：不做文档追加编辑（块 API 面
宽、schema 脆），每月/每次跑产出一篇快照，最新一篇即全量，历史快照
自然留档。

OpenAPI 直连（send_feishu.py 判例：lark-cli 独立进程无凭证，
tenant_access_token 通道无人值守可靠）：
  root_folder/meta（已通）→ upload_all（需 drive:file:upload）
  → import_tasks md→docx（需 docs:document:import，异步轮询）
  → 卡片推送（wathet 📚）。
缺权限时打印授权直达链接并退出 rc=2（不发半截链路）。

默认预览（只打印快照 md）；--send 执行链路。空态提示退出 rc=0。

用法:
  python weak_track_archive.py                # 预览快照 md
  python weak_track_archive.py --send         # 归档+推卡片
"""

import argparse
import json
import os
import sys
import time
import urllib.error
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import weak_track_summary as wts  # noqa: E402
import weak_track_learn as wl  # noqa: E402
import logging

CASES = os.path.join(os.path.dirname(HERE), "reports", "weak_cases")
OPEN = "https://open.feishu.cn/open-apis"
APP_ID_SCOPE_URL = (
    "https://open.feishu.cn/app/cli_aa3e4f1b58f85cdd/auth"
    "?q=%s&op_from=openapi&token_type=tenant"
)
SCOPE_UPLOAD = "drive:file:upload"
SCOPE_IMPORT = "docs:document:import"

_EMOJI = {"A": "🟢", "B": "🟠", "C": "🔴"}
_CNAME = {"A": "情景A兑现", "B": "情景B立案·滚动新案", "C": "利空实证"}


def _cfg():
    cfg = json.load(
        open(os.path.expanduser("~/.lark-cli/config.json"), encoding="utf-8")
    )
    aid, sec = cfg["apps"][0]["appId"], cfg["apps"][0]["appSecret"]
    # 20261016换机：appSecret 非 str（1.0.5+ 外置引用 dict）即不可用
    if not isinstance(sec, str):
        sec = None
    return aid, sec


def _token():
    # K-STOCK-228 出口收敛：token 委托 feishu_client；drive 家族 API 与
    # ScopeError 守卫保留在本文件（授权后需新 token → use_cache=False）。
    sys.path.insert(0, os.path.join(os.path.dirname(HERE), "news_collector"))
    from feishu_client import get_tat

    a, s = _cfg()
    tok = get_tat(a, s, use_cache=False)
    if not tok:
        raise RuntimeError("tenant_access_token 获取失败（config 缺失或 HTTP 异常）")
    return tok


class ScopeError(Exception):
    """app 缺 drive/import 权限（携带 scopes 与授权直达链接）。

    通用化（K35-aa）：飞书 99991672 响应体自带 auth 直达链接，直接
    正则透传，不再维护 scope→URL 映射表。
    """

    def __init__(self, scopes, url=""):
        self.scopes = scopes
        self.url = url
        super().__init__("missing scopes: %s" % scopes)


def _api(tok, url, data=None, headers=None, ctype="application/json", method=None):
    """OpenAPI 调用：HTTPError 时识别 scope 缺失抛 ScopeError。

    99991672 响应体自带 scopes 列表与授权直达链接，正则透传。
    """
    import re

    h = {"Authorization": "Bearer " + tok}
    if ctype:
        h["Content-Type"] = ctype
    try:
        req = urllib.request.Request(
            url, data=data, headers=headers or h, method=method
        )
        return json.loads(urllib.request.urlopen(req, timeout=60).read())
    except urllib.error.HTTPError as e:
        txt = e.read().decode("utf-8", "replace")
        if " scopes is required" in txt:
            m = re.search(r"required: \[([^\]]+)\]", txt)
            scopes = (
                [s.strip() for s in m.group(1).split(",")] if m else ["drive:drive"]
            )
            m2 = re.search(r"https://open\.feishu\.cn/app/[^\"\s]+", txt)
            raise ScopeError(scopes, m2.group(0) if m2 else "")
        raise RuntimeError("%s %s %s" % (e.code, url.split("?")[0], txt[:200]))


def build_archive_md(cases, snap=None, watch=None):
    """全量案例库快照 markdown（头部统计 + 在案节 + 每案一节）。

    watch=§12 watch_cases 产物时补「在案观察中」节（案例库全貌，
    已结案+在案），空列表或 None 时跳过该节。
    """
    snap = snap or time.strftime("%Y%m%d")
    f = wl.learn_fields(cases)
    md = [
        "# weak 案例库 %s" % snap,
        "",
        "> 结案 %s 例（A %s/B %s/C %s）· A 类兑现 %s · "
        "时长均 %s 日历天（非交易日）· 超额修复 %s/%s"
        % (
            f["n"],
            f["na"],
            f["nb"],
            f["nc"],
            f["a_rate"],
            f["dur_avg"],
            f["ex_fix"],
            f["ex_tagged"],
        ),
        "",
        "## 经验条目",
        "",
        f["lessons"],
        "",
    ]
    if watch:
        md += ["## 在案观察中", ""]
        md += [
            "- %s %s：跟踪 %s 日历天·末次 %s 天前·任务 %s"
            % (
                w["code"],
                w.get("name") or "",
                w["days_open"] if w["days_open"] is not None else "—",
                w["days_stall"] if w["days_stall"] is not None else "—",
                w["task"],
            )
            for w in watch
        ]
        md.append("")
    for c in cases:
        md.append(
            "## %s %s %s（%s）"
            % (
                _EMOJI.get(c.get("type"), "⚪"),
                c["code"],
                c.get("name") or "",
                _CNAME.get(c.get("type"), "?"),
            )
        )
        md += [
            "- 结案：%s（立案 %s，跟踪 %s 日历天）"
            % (
                c["ts"],
                c.get("open") or "—",
                wl._days(c) if wl._days(c) is not None else "—",
            ),
            "- 区间涨跌：%s%%｜超额250：%s%s"
            % (
                ("%+.1f" % c["chg"]) if c["chg"] is not None else "—",
                "" if c.get("exd") else "无标签 ",
                c.get("exd") or "",
            ),
            "- 触发：共 %d 次｜源文件：%s" % (c["ntrig"], c["md"]),
            "",
        ]
    return "\n".join(md).rstrip("\n")


def _upload_md(tok, folder, name, md):
    """上传 markdown 到云空间（multipart upload_all）→ file_token。"""
    bd = md.encode("utf-8")
    b = "----weakarch%s" % int(time.time() * 1000)
    parts = []
    for k, v in [
        ("file_name", name),
        ("parent_type", "explorer"),
        ("parent_node", folder),
        ("size", str(len(bd))),
    ]:
        parts.append(
            (
                '--%s\r\nContent-Disposition: form-data; name="%s"'
                "\r\n\r\n%s\r\n" % (b, k, v)
            ).encode()
        )
    parts.append(
        (
            '--%s\r\nContent-Disposition: form-data; name="file"; '
            'filename="%s"\r\nContent-Type: text/markdown\r\n\r\n' % (b, name)
        ).encode()
    )
    parts.append(bd + b"\r\n--" + b.encode() + b"--\r\n")
    r = _api(
        tok,
        OPEN + "/drive/v1/files/upload_all",
        data=b"".join(parts),
        ctype=None,
        headers={
            "Authorization": "Bearer " + tok,
            "Content-Type": "multipart/form-data; boundary=" + b,
        },
    )
    if r.get("code") != 0:
        raise RuntimeError("upload failed: %s" % json.dumps(r)[:200])
    return r["data"]["file_token"]


def _import_docx(tok, file_token, folder, name):
    """import_tasks md→docx（异步轮询至多 180s）→ (doc_token, url)。

    K43-e 判例：导入是异步任务，30s 窗口对大 md 不够（实测超时后任务
    仍在服务端跑完），放宽到 180s；月频任务不差这几分钟。
    """
    r = _api(
        tok,
        OPEN + "/drive/v1/import_tasks",
        data=json.dumps(
            {
                "file_extension": "md",
                "file_token": file_token,
                "point": {"mount_type": 1, "mount_key": folder},
                "type": "docx",
                "file_name": name,
            }
        ).encode(),
    )
    if r.get("code") != 0:
        raise RuntimeError("import create failed: %s" % json.dumps(r)[:200])
    ticket = r["data"]["ticket"]
    for _ in range(90):
        time.sleep(2)
        j = _api(tok, OPEN + "/drive/v1/import_tasks/" + ticket)
        # K43-e 判例：GET 返回结构是 data.result.job_status（非 data.job_status），
        # 读错键=永远等不到成功，轮询耗尽误报 timeout（实际服务端秒级完成）
        st = (j.get("data") or {}).get("result") or {}
        if st.get("job_status") == 0:  # SUCCESS
            token = st["token"]
            return token, st.get("url") or ("https://feishu.cn/docx/%s" % token)
        if st.get("job_status") in (3, 4):  # FAILED/CANCEL
            raise RuntimeError("import failed: %s" % json.dumps(j)[:200])
    raise RuntimeError("import timeout (180s)")


STATE = os.path.join(HERE, "weak_archive_state.json")
FOLDER_NAME = "weak案例库"


def _ensure_folder(tok, root):
    """取专属文件夹 token（state 缓存，无则创建并记住）。

    K35-aa：快照 docx+源 md 都进专属文件夹，云空间根目录不堆文件。
    state 丢失时会重名新建（飞书允许，无害），记回 state。
    """
    if os.path.isfile(STATE):
        try:
            ft = json.load(open(STATE, encoding="utf-8")).get("folder")
            if ft:
                return ft
        except (OSError, ValueError) as exc:
            logging.getLogger(__name__).warning("silent-except %s: %s", "quant_backtest/weak_track_archive.py", exc)
    r = _api(
        tok,
        OPEN + "/drive/v1/files/create_folder",
        data=json.dumps({"name": FOLDER_NAME, "folder_token": root}).encode(),
    )
    if r.get("code") != 0:
        raise RuntimeError("create_folder failed: %s" % json.dumps(r)[:200])
    ft = r["data"]["token"]
    with open(STATE, "w", encoding="utf-8") as fo:
        json.dump({"folder": ft}, fo)
    return ft


def _delete_file(tok, file_token):
    """删除云空间源 md（导入成 docx 后清理，防月月堆积）。

    非致命：失败（含无权限）只 warn，不影响归档结果——清理是锦上添花。
    """
    try:
        r = _api(
            tok, OPEN + "/drive/v1/files/%s?type=file" % file_token, method="DELETE"
        )
        return r.get("code") == 0
    except Exception as e:  # noqa: BLE001
        print("（warn）源 md 清理跳过：%s" % str(e)[:120])
        return False


def archive_send(md, snap):
    """全链路：root → 专属文件夹 → upload → import → 清理 → 卡片推送。"""
    import weak_track_card as wcard

    tok = _token()
    root = _api(tok, OPEN + "/drive/explorer/v2/root_folder/meta")["data"]["token"]
    folder = _ensure_folder(tok, root)
    name = "weak案例库_%s.md" % snap
    ft = _upload_md(tok, folder, name, md)
    doc_tok, url = _import_docx(tok, ft, folder, "weak 案例库 %s" % snap)
    _delete_file(tok, ft)
    cases = wts.parse_cases(CASES)
    f = {
        "n": len(cases),
        "na": sum(1 for c in cases if c["type"] == "A"),
        "nb": sum(1 for c in cases if c["type"] == "B"),
        "nc": sum(1 for c in cases if c["type"] == "C"),
        "trig": sum(c["ntrig"] for c in cases),
        "recent": wcard.recent_lines(cases),
        "snap": snap,
        "doc_url": url,
        "ts": time.strftime("%Y-%m-%d %H:%M"),
    }
    rcpt = wcard.send_card(wcard.archive_card(f))
    return url, rcpt


def main():
    ap = argparse.ArgumentParser(description="§14 案例库飞书归档推送")
    ap.add_argument(
        "--send", action="store_true", help="执行归档+推送（默认仅预览快照 md）"
    )
    a = ap.parse_args()
    import weak_track_watch as ww

    watch = ww.watch_cases()
    cases = wts.parse_cases(CASES)
    if not cases and not watch:
        print("暂无已结案案例且无在案（%s 空）——§9 结案后本节才有数据" % CASES)
        return 0
    snap = time.strftime("%Y%m%d")
    md = build_archive_md(cases, snap, watch=watch)
    if not a.send:
        print(md)
        print("\n（预览模式：加 --send 上传导入飞书并推卡片）")
        return 0
    try:
        url, rcpt = archive_send(md, snap)
        print("归档完成：%s\n推送：%s" % (url, rcpt))
        return 0
    except ScopeError as e:
        print("app 缺权限（%s）——请管理员点击开通后再跑：" % "、".join(e.scopes))
        if e.url:
            print("  直达：%s" % e.url)
        else:
            print(
                "  上传：%s\n  导入：%s"
                % (APP_ID_SCOPE_URL % SCOPE_UPLOAD, APP_ID_SCOPE_URL % SCOPE_IMPORT)
            )
        return 2
    except Exception as e:  # noqa: BLE001
        print("归档失败：%s" % str(e)[:300])
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
