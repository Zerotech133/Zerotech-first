# -*- coding: utf-8 -*-
"""前端页面「看 / 测 / 回滚」闭环小工具（K-ORG-20261107-PREVIEW）。

为什么需要它：8080 上的前端不是本地 python 进程，而是 Docker 容器 ai-portal
（nginx:alpine）把宿主机 portal/ 只读挂载到 /usr/share/nginx/html。于是有个天然的
秒级闭环——**改宿主机文件 → 浏览器刷新即见**，不需要重建镜像、不需要重启容器。
但每次都要人肉算「这个 URL 对应哪个文件」「改了哪些文件」「怎么退回去」，容易错，
本工具把这三件事各收成一条命令。

用法：
    python tools/preview.py map /                      # URL → 宿主机文件（并报 no-cache）
    python tools/preview.py map http://127.0.0.1:8080/picks.html
    python tools/preview.py diff                       # portal 下当前改动（含未跟踪）
    python tools/preview.py rollback portal/home_v3.html
    python tools/preview.py rollback /picks.html       # 也接受 URL
    python tools/preview.py render portal/home_v3.html # 用 nginx 同规则判是否 no-cache

设计约束（重要）：
  * **不删文件**。未跟踪文件回滚 = 移到 portal/../.preview_trash/<时间戳>/ 下，
    符合本机「删除一律进回收站」的铁律；要彻底清理由人来做。
  * 只做 portal/ 下的静态资源；不碰 nginx.conf 的 reload（那要 docker exec，
    且 reload 失败会让 8080 全站 502，必须人确认）。
  * 不启动服务、不改容器、不动 git 索引以外的状态。

退出码：0 正常；1 目标不存在/不在 portal 下；2 参数错误。
"""
from __future__ import annotations

import os
import re
import shutil
import subprocess
import sys
from datetime import datetime
from urllib.parse import urlsplit

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PORTAL = os.path.join(ROOT, "portal")
NGINX_CONF = os.path.join(PORTAL, "nginx.conf")
TRASH = os.path.join(ROOT, ".preview_trash")

# 与容器挂载一致的 URL 前缀（8080 是宿主机端口，容器内是 80）
HOSTS = ("127.0.0.1:8080", "localhost:8080", "127.0.0.1", "localhost")


def _rel(p):
    return os.path.relpath(p, ROOT).replace(os.sep, "/")


def _no_cache_rules():
    """从 nginx.conf 里粗提「精确匹配 URL → try_files 文件」与 no-cache 位置。

    只解析 `location = /xxx {` 这种精确块，够覆盖根入口和 picks 这类单页；
    正则 location 不解析（宁可报「未知」也不猜）。
    """
    rules = {}
    if not os.path.isfile(NGINX_CONF):
        return rules
    try:
        text = open(NGINX_CONF, encoding="utf-8", errors="replace").read()
    except OSError:
        return rules
    # 用 [^}]* 而不是 .*?\n\s*}：本仓存在单行块
    # `location = /picks_index.html { ...; }`，按"换行+}"找块尾会把下一个块吞掉，
    # 导致 `location = /` 永远匹配不上（实测过：/ 被解析成 index.html 而非 home_v3.html）。
    for m in re.finditer(r"location\s*=\s*(\S+)\s*\{([^}]*)\}", text):
        url, body = m.group(1), m.group(2)
        tf = re.search(r"try_files\s+(\S+)", body)
        if tf:
            rules[url] = {
                "file": tf.group(1).lstrip("/"),
                "no_cache": "no-cache" in body,
            }
    return rules


def resolve(target):
    """URL 或路径 → (host 绝对路径, 说明)。找不到返回 (None, 原因)。"""
    rules = _no_cache_rules()
    t = target.strip()
    if t.startswith("http://") or t.startswith("https://"):
        sp = urlsplit(t)
        t = sp.path or "/"
    if not t.startswith("/"):
        # 当作仓库相对路径
        p = os.path.abspath(os.path.join(ROOT, t))
        if not p.startswith(PORTAL + os.sep) and p != PORTAL:
            return None, "不在 portal/ 下，容器不会服务它"
        if not os.path.isfile(p):
            return None, "文件不存在：" + _rel(p)
        return p, "path"

    if t in rules:
        p = os.path.join(PORTAL, rules[t]["file"])
        note = "nginx location = %s 显式指向%s" % (
            t, "，且 no-cache" if rules[t]["no_cache"] else "")
        return (p if os.path.isfile(p) else None), note

    rel = t.lstrip("/")
    if rel == "":
        rel = "index.html"
    p = os.path.join(PORTAL, rel)
    if os.path.isfile(p):
        return p, "按 URL 直接映射（是否 no-cache 取决于该 location 配置）"
    if os.path.isdir(p):
        idx = os.path.join(p, "index.html")
        if os.path.isfile(idx):
            return idx, "目录 → index.html"
    return None, "容器内无此文件（会走 nginx 的 404/兜底规则）"


def cmd_map(target):
    p, note = resolve(target)
    rules = _no_cache_rules()
    print("url/path : %s" % target)
    print("resolved : %s" % note)
    if not p:
        print("结果     : 未找到")
        return 1
    st = os.stat(p)
    print("file     : %s" % _rel(p))
    print("size     : %d B   mtime=%s" % (
        st.st_size, datetime.fromtimestamp(st.st_mtime).strftime("%Y-%m-%d %H:%M:%S")))
    url = target if target.startswith("/") else "/" + os.path.basename(p)
    hit = rules.get(url)
    if hit and hit["no_cache"]:
        print("cache    : 该入口 no-cache，刷新即见")
    elif hit:
        print("cache    : 该入口未配 no-cache，必要时 Ctrl+F5")
    else:
        print("cache    : 未在 nginx 里找到精确 location，可能吃缓存（必要时 Ctrl+F5）")
    print("loop     : 改这个文件 → 浏览器刷新即见（容器 bind-mount 只读，无需重启）")
    print("rollback : python tools/preview.py rollback %s" % _rel(p))
    return 0


def _git(*args):
    return subprocess.run(["git", "-C", ROOT] + list(args),
                          capture_output=True, text=True, encoding="utf-8",
                          errors="replace")


def cmd_diff():
    """只看 portal 静态资源的改动，避免被全仓噪音淹没。"""
    r = _git("status", "--porcelain", "--", "portal")
    lines = [ln for ln in (r.stdout or "").splitlines() if ln.strip()]
    if not lines:
        print("portal/ 下无改动（工作区与该目录一致）")
        return 0
    print("portal/ 下改动 %d 项：" % len(lines))
    for ln in lines:
        print("  " + ln)
    print("\n回滚单个：python tools/preview.py rollback <上面路径去掉状态前缀>")
    return 0


def cmd_rollback(target):
    p, note = resolve(target)
    if not p:
        print("拒绝：%s" % note)
        return 1
    rel = _rel(p)
    tracked = _git("ls-files", "--error-unmatch", rel)
    if tracked.returncode == 0:
        r = _git("checkout", "--", rel)
        if r.returncode != 0:
            print("git checkout 失败：%s" % (r.stderr or "").strip())
            return 1
        print("已回滚（git checkout -- %s）→ 浏览器刷新即见" % rel)
        return 0
    # 未跟踪：不删除，移入回收目录
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    dst_dir = os.path.join(TRASH, stamp)
    os.makedirs(dst_dir, exist_ok=True)
    dst = os.path.join(dst_dir, os.path.basename(p))
    shutil.move(p, dst)
    print("未跟踪文件，已移出（不是删除）：")
    print("  %s -> %s" % (rel, _rel(dst)))
    print("确认不要了再自己清理 .preview_trash/%s" % stamp)
    return 0


def main(argv):
    if len(argv) < 2:
        print(__doc__)
        return 2
    cmd = argv[1]
    rest = argv[2:]
    if cmd == "map":
        if len(rest) != 1:
            print("用法：preview.py map <url|path>")
            return 2
        return cmd_map(rest[0])
    if cmd == "diff":
        return cmd_diff()
    if cmd == "rollback":
        if len(rest) != 1:
            print("用法：preview.py rollback <url|path>")
            return 2
        return cmd_rollback(rest[0])
    if cmd == "render":
        if len(rest) != 1:
            print("用法：preview.py render <url|path>")
            return 2
        return cmd_map(rest[0])
    print("未知子命令：%s" % cmd)
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv))
