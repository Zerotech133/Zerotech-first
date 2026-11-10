# Hermes + CowAgent 板块研究手册（2026-11-10）

> 从全仓审计抽出 Hermes + CowAgent 单板块，格式与 `docs/JEV_研究手册.md` 一致。

## 一、定位（先钉死）
- 个人 AI 助理核心：Hermes Agent（WebUI 端口 **8787**）+ CowAgent 三层中文记忆。
- 长期整合野心：**Hermes（执行/记忆）+ OpenClaw（网关/插件）+ CowAgent（记忆）+ Khoj（知识检索）** → 集成超级助理。

## 二、本轮审计结论（2026-11-10）

### P0（唯一一个全仓级 P0，未修）
| 问题 | 后果 | 状态 |
|---|---|---|
| `start_hermes_*.py:80/35` **写死机器哈希 venv 路径** | 重装 `.hermes` 即静默失效，8787 起不来 | **`start_hermes_bg.py` 被敏感内容保护锁定，本回合未读未改**；其内部 venv/agent 路径重解析需老田授权读取或主机侧应用 |

### 已修（本轮）
| 位置 | 修复 |
|---|---|
| `cowbridge/cowbridge.py:50` | `HERMES_HOME` 写死 `C:\Users\Administrator\.hermes` → 改 `os.getenv("HERMES_HOME") or Path.home()/".hermes"`（对齐 hermes-webui 约定） |
| `test_provider_plugin.py` + `verify_provider_realdata.py` | 写死 venv/agent 绝对路径 → 改 `HERMES_HOME`/`HERMES_AGENT_DIR` env 解析 |

### P1 / P2（未修）
| 严重度 | 问题 |
|---|---|
| P1 | `server.py` 无 `/health` 但探针探 8787/health → **恒失败、监控假红**（需 Hermes 服务改动，待评审） |
| P1 | `subprocess.Popen` 无 returncode 校验 |
| P1 | 直连 8787 信任 `X-Portal-User` 头（残余风险） |
| P2 | hermes-webui-next 文档端口 8788 标 8787；Py 版本不一致（3.11 vs 3.13）；大量吞错；路径混用 |

## 三、待办
- [ ] **`start_hermes_bg.py` venv 解析**：等老田授权读取（或主机侧按 env 约定改）——P0 清零的唯一缺件
- [ ] 补 `/health` 路由或对齐探针路径（阶段 A2）
- [ ] Popen returncode 校验；`X-Portal-User` 直连信任收紧
- [ ] 阶段 A 验收门：8787 health 常绿

## 四、一句话给研究者
全仓唯一的 **P0 在这个板块**：venv 路径写死机器哈希，重装即瘫。
它的债全是**可移植性债**（写死路径/端口/版本），研究它先问"换台机器还能起来吗"。
