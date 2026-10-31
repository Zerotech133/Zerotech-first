# 验收判据：席位入编（WO-HUANGZHONG-20261028）
> STATUS: frozen（黄忠验收夹具/样例文件，非计划；2026-10-31 W2 收口）



| # | 判据 | 证据路径/命令 | 通过线 |
|---|---|---|---|
| 1 | 黄忠卡片已入编且角色正确 | `portal/agent_ide_bundle.json` → agents.HUANGZHONG | cn_name=黄忠；role_cn=交付验收总长；dept=academy |
| 2 | charter 在位且含观察模式 | `docs/plans/2026-10-28_黄忠验收守门官_charter_v1.md` | 含"只验不改/只退不批/观察模式" |
| 3 | 技能包可自检 | `python agent_platform/skills/huangzhong/scripts/verify.py` | ok=True；failed=[] |
| 4 | 验收单可产出 | `python tools/huangzhong_accept.py --json` | verdict∈{PASS,REJECT,OBSERVE}；blocking=false |
| 5 | 授权仅只读 | `agent_platform/registry/org.json` → HUANGZHONG.tools | 6 项只读工具；无写权限 |

## 回归项（修复后不误伤）

- 重跑 `pytest agent_platform/tests/test_ide_enrich.py tools/test_huangzhong_accept.py`：全部通过。
- 观察模式不阻断 merge：`blocking=false` 在验收单中可见。
