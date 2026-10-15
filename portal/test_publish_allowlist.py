# -*- coding: utf-8 -*-
# K-STOCK-411 守卫测试：公网发布白名单（防敏感件回归发布链）。
# 断言：①SINGLE_FILES 三件白名单且零敏感交集；②SENSITIVE_REMOVAL 四件在册；
# ③快照目录（主仓）无敏感残留；④移档函数幂等（目标缺失=no-op）。
# 运行：python portal/test_publish_allowlist.py（③在 worktree 内自动 SKIP）
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
FAIL = []

SENSITIVE = {"ledger.html", "data" + os.sep + "ledger.json",
             "data" + os.sep + "pick_details", "server.py"}


def check(name, cond):
    print(("PASS " if cond else "FAIL ") + name)
    if not cond:
        FAIL.append(name)


import sync_publish as SP  # noqa: E402

# ① 白名单恰三件且零敏感交集
wl = {rel_dst for _, rel_dst in SP.SINGLE_FILES}
check("白名单=picks/tokens/todays 三件",
      wl == {"picks.html", os.path.join("static", "tokens.css"),
             os.path.join("data", "todays_picks.json")})
check("白名单零敏感交集", not (wl & SENSITIVE))
# 白名单源件存在性：picks.html/tokens.css 在 git（worktree 可验）；
# todays_picks.json 为运行时数据（gitignored，worktree 无属正常，只验主仓态时才有）
check("白名单 git 源文件存在",
      all(os.path.exists(os.path.join(SP.SRC, s)) for s in
          ("picks.html", os.path.join("static", "tokens.css"))))

# ② 敏感移档清单在册
sr = {SP.SENSITIVE_REMOVAL[i] for i in range(len(SP.SENSITIVE_REMOVAL))}
check("移档清单=四件敏感", sr == SENSITIVE)

# ③ 主仓快照无敏感残留（worktree 无 picks_public，SKIP）
DST = os.path.join(HERE, "picks_public")
if os.path.isdir(DST):
    residue = [r for r in SENSITIVE if os.path.exists(os.path.join(DST, r))]
    if residue:
        print("FAIL 快照敏感残留: %s" % residue)
        FAIL.append("快照敏感残留")
    else:
        check("快照无敏感残留", True)
else:
    print("SKIP 快照检查（worktree 内无 picks_public/，merge 后主仓生效）")

# ④ 移档幂等：目标全缺时 no-op 返回空
check("移档幂等（目标缺失=空表）", SP.remove_sensitive(dry_run=True) == [])

print("FAILURES: %d" % len(FAIL))
sys.exit(1 if FAIL else 0)
