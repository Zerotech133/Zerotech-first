# -*- coding: utf-8 -*-
"""S6.5 归档 + 可选飞书推送。"""
import os
import shutil
import subprocess
import time

from . import config


def archive(code, name, as_of, artifacts, outdir, root=None):
    folder = "%s_%s_%s" % (as_of, code.replace(".", "_"), name)
    dest = os.path.join(root or config.DELIVER_ROOT, folder)
    os.makedirs(dest, exist_ok=True)
    for k in ("report", "h5", "manifest"):
        p = artifacts.get(k)
        if p and os.path.exists(p):
            shutil.copy2(p, dest)
    chan = os.path.join(dest, "charts")
    os.makedirs(chan, exist_ok=True)
    for p in (artifacts.get("charts") or {}).values():
        if p and os.path.exists(p):
            shutil.copy2(p, chan)
    # INDEX 追加
    idx = os.path.join(root or config.DELIVER_ROOT, "ANALYSIS_INDEX.md")
    line = "- %s ｜ %s %s ｜ 评级 %s ｜ %s\n" % (time.strftime("%F %T"), code, name,
                                               artifacts.get("rating", ""), folder)
    if not os.path.exists(idx):
        open(idx, "w", encoding="utf-8").write("# 个股全维分析索引（analysis 自动生成）\n\n")
    with open(idx, "a", encoding="utf-8") as f:
        f.write(line)
    return dest


def feishu_push(dest, code, name, rating, thesis):
    bin_dir = os.path.join(config.ROOT, "bin")
    exe = os.path.join(bin_dir, "lark-cli.exe")
    if not os.path.exists(exe):
        return {"ok": False, "error": "lark-cli 不存在"}
    uid = "ou_50a95455c7dffec1090ade400886d0d4"
    msg = "# %s %s 个股分析\n**评级：%s**\n%s\n\n研究分析，不构成投资建议。" % (name, code, rating, thesis[:300])
    env = dict(os.environ)
    env["PATH"] = bin_dir + ";" + env.get("PATH", "")
    try:
        r = subprocess.run([exe, "im", "+messages-send", "--as", "bot", "--user-id", uid,
                            "--markdown", msg], capture_output=True, text=True, timeout=90, env=env)
        out = (r.stdout or "") + (r.stderr or "")
        return {"ok": '"ok": true' in out.replace(" ", "") or '"ok":true' in out.replace(" ", ""), "raw": out[-200:]}
    except Exception as e:
        return {"ok": False, "error": str(e)[:120]}
