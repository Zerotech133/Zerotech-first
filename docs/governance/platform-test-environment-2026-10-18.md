# GOV-MIN-007｜Agent Platform 独立测试环境静态核验

> 2026-10-18；版本/缺包返回码复采样：2026-10-18 17:21:04 +08:00（命令开始时间）；首轮探测同日较早，未单独记录精确时分秒。状态：`ENV_PARTIALLY_VERIFIED / TESTS_NOT_EXECUTED / BLOCKED`。本报告只记录静态源码/配置和非执行型版本、依赖元数据命令，不是测试通过证明，也不是生产写入授权。

## 1. 树与来源边界

- 工单目标由[下一批最小工单 §GOV-MIN-007](next-minimal-workorders-2026-10-17.md#gov-min-007agent-platform-测试准备)定义；[路线图 §GOV-FIX-007](GOV-FIX-ROADMAP-2026-10-17.md#gov-fix-007agent-platform)指向独立审计根 `C:\项目\zhice-os-v2-windows-20260920\code`。本次 `ls -ld` 确认该根存在，顶层存在 `pyproject.toml`、`requirements-local.txt`、`requirements-freeze.txt`、`uv.lock`、`src/`、`tests/`、`.venv/`。但只证实路径存在/目录元数据可读，不证明该树是老田仓 `agent_platform/` 的同源或副本，不证明 Git revision、生产部署映射或读写权限授权。
- 独立树 `src/zhice_os_v2/agent_platform/` 是盯盘/学习/决策模块；老田仓 `C:\项目\老田的AI助手\agent_platform/` 的 `contracts/objects.py`、`adapters/heartbeat.py`、`engine/artifacts.py` 和 `adapters/replay_query.py` 是**另一棵代码树**。独立树静态搜索未发现这些同名统一 Artifact、Paper heartbeat、跨账本 replay 实现，不能把老田仓契约直接说成独立树已经实现。其逐项归属为 `SOURCE_UNVERIFIED`，待 Agent Platform owner 明确两个树的系统边界；不得混用两棵树的数据/数据库/账本。
- 本次**没有读取/解析** `.worktrees/tasks.json` 或任何 Agent Platform JSONL；未运行 writer、测试套件、生产服务、端口探测、依赖安装、数据库连接和配置中可能包含密钥的 `.env.local`。`REBASELINE-001` 仍为 `BLOCKED`，见[人工决策记录](rebaseline-001-decision-record-2026-10-18.md#三当前状态)及[冻结门禁](rebaseline-001-freeze-gate-2026-10-18.md)。

## 2. 环境元数据与真实探测输出

| 探测（均非测试运行） | 现场输出/结论 | 限制 |
|---|---|---|
| `ls -ld "C:/项目/zhice-os-v2-windows-20260920/code"` | `drwxr-xr-x 1 t 197121 0 Oct 18 15:35 .../code` | 路径存在，不能从 Git Bash mode 推断 Windows ACL/授权。 |
| 系统 PATH `command -v python`、`python --version`、`python -m pytest --version` | PATH 为 `/c/Users/t/.workbuddy/binaries/python/versions/3.13.12/python`（Windows 对应 `C:\Users\t\.workbuddy\binaries\python\versions\3.13.12\python.exe`）；该绝对路径 17:21 复采样和 PATH 调用均自报 `Python 3.13.14`（成功）。首轮 `python -m pytest --version` 提示 `No module named pytest`（聚合 Bash 命令未单独捕获该子命令退出码）；`py --version` → `py: command not found`。 | 会话运行时清单所列“托管 Python 3.13.12”与本次实际可执行文件输出 3.13.14 不一致：可能是会话清单/安装目录标识与运行时变化或采样时点不同，**具体漂移原因未核实**；PATH 解释器不是独立树 `.venv`。 |
| `C:/项目/zhice-os-v2-windows-20260920/code/.venv/Scripts/python.exe --version` 和 `-m pytest --version` | 17:21 复采样 `Python 3.13.14`、`pytest 9.1.1`（两条均显示预期版本）；`.venv/Scripts/python.exe` 和 `pytest.exe` 存在。 | 版本命令成功不代表测试收集、业务模块导入或写路径安全。 |
| `.../.venv/Scripts/python.exe -m pip --version`、`-m pip check` | 首轮探测 `pip 26.1.2 ...\.venv\Lib\site-packages\pip (python 3.13)`；`No broken requirements found.`；该聚合命令最终退出 0，未逐子命令采集返回码。 | 仅已安装包的声明关系一致，不保证项目依赖全装、锁定版本一致。 |
| `.../.venv/Scripts/python.exe -m pip show zhice-os-v2 pytest pytest-asyncio pytest-cov fastapi sqlalchemy pydantic prometheus-client` | 首轮输出 pytest `9.1.1`、pytest-asyncio `1.4.0`、fastapi `0.143.3`、SQLAlchemy `2.1.5`、pydantic `2.14.1`、prometheus_client `0.26.0`，Stderr `WARNING: Package(s) not found: pytest-cov, zhice-os-v2`；首轮工具显示 `Exit Code: 0`。17:21 复采样仅查两缺包项，**子命令实际返回 `1`**（另以 `result=$?` 捕获并打印 `pip_show_exit_code=1`），整个复采样 Bash 命令因最后 `printf` 成功而 `Exit Code: 0`。 | 两次参数不同：首轮混合已安装包和缺包返回 0，本次只列缺包返回 1；不能从首轮退出 0 推论所有包已安装。缺包警告与本次 `1` 是准确的观察。未做模块导入；源码能否经 PYTHONPATH/可编辑安装被 pytest 找到仍 `UNVERIFIED`。 |
| `icacls "C:/项目/zhice-os-v2-windows-20260920/code"` | 返回 0：`BUILTIN\\Administrators:(I)(OI)(CI)(F)`、`NT AUTHORITY\\SYSTEM:(I)(OI)(CI)(F)`、`BUILTIN\\Users:(I)(OI)(CI)(RX)`、`NT AUTHORITY\\Authenticated Users:(I)(M)`、`NT AUTHORITY\\Authenticated Users:(I)(OI)(CI)(IO)(M)`；汇总为“成功处理 1 个文件，失败 0 个”，终端中文显示乱码。 | 只读到**目录 ACL**，显示认证用户有继承修改权限倾向，不能证明当前 token 的有效权限、子路径拒绝 ACE、真实隔离性或管理员业务授权；没有进行写权限试探。 |

`pyproject.toml:9-49` 要求 Python `>=3.11`，声明运行依赖 fastapi、pydantic、SQLAlchemy、pandas 等，dev 包括 pytest `>=8.2.0`、pytest-asyncio `>=0.23.7`、pytest-cov `>=5.0.0`。`requirements-local.txt:105-107` 声明 pytest `9.1.1`、pytest-asyncio `1.4.0`、pytest-cov `7.1.0`，但本次已安装查询发现 `pytest-cov` 缺失，且 `requirements-local.txt:39,72,96,105` 等声明版本与当前探测 fastapi、SQLAlchemy、pydantic 有差异；具体全集未核对，不声称锁定同步。`.venv/pyvenv.cfg` 记录 `version=3.13.14`、`home=C:\Users\t\.workbuddy\binaries\python\versions\3.13.12` 和 `executable=C:\Program Files\WorkBuddy\resources\vendor\python\python.exe`；`command` 提及 `.venv_new`、实际路径 `.venv`。这些是当次文件快照中的路径/来源线索，可能涉及重命名或目录标签滞后，**原因与时间线未核验**；不能把目录名 `3.13.12` 当作运行时版本，也不能据此认定环境损坏。待安装/确认清单：`pytest-cov>=5.0.0`（若计划使用覆盖率）；`zhice-os-v2` 项目本体须确认采用隔离 `PYTHONPATH=.../code/src` 还是经审定的 editable 安装；其余 `pyproject` 与锁文件/目标虚拟环境的完整一致性 `UNVERIFIED`。**本轮没有安装任何依赖。**

## 3. 权限与副作用边界

- `pyproject.toml:55-62` 默认排除 `real_pg` 与 `e2e`，但该配置不是隔离保证：任何收集/fixture/import、显式选择 `-m e2e`、代码直连、进程级缓存、默认路径仍可能产生副作用。`tests/conftest.py:10-12,47-73` 设置 memory backend 并对未标记 `real_pg` 的 `get_session` 加守卫；其 `:239-253` fixture 清缓存。**未经测试内容逐项审查，不运行整套 pytest，亦不作无副作用断言。**
- `src/zhice_os_v2/agent_platform/config.py:19-42,80-91` 默认报告目录可落 `code/agent_platform_reports`，DB URL 可来自 settings 或回退到 localhost Postgres；`src/zhice_os_v2/agent_platform/metrics.py:107-125` 可启动监听端口；`src/zhice_os_v2/scheduler.py` 包含调度/写 heartbeat。测试设计必须完全替换这些默认路径、数据库、网络、调度及外部通知；**禁止**启动相应服务或探端口。
- 老田仓 `agent_platform/engine/artifacts.py:81-103` 默认构造可 mkdir/建 SQLite/WAL，`read_only=True` 才以 `mode=ro` 读既有库；`agent_platform/adapters/heartbeat.py:99-125` 会追加 JSONL；`adapters/replay_query.py:88-105` 会读取事件来源。不能在当前工单运行它们，更不能传 live root。静态源码阅读仅用于设计矩阵，不构成独立树运行事实。
- **未来有单独授权后**：在 OS 临时位置新建唯一 `temp root`（不得位于两仓、真实数据根或共享数据库目录），由 fixture 显式注入所有路径/SQLite `:memory:`/fake clock/fake transport；阻断所有网络、生产 DSN、真实账本、服务进程与测试外写入，审查测试 import 和 fixture 副作用。读写边界先由 owner 双签确认；即便目录 ACL 容许修改，本单也只准写两份文档。

## 4. 门槛与处置

当前是**测试环境存在且有 pytest，但权限授权、依赖完整性、代码归属与隔离审查未完成**，故 `GOV-MIN-007 = BLOCKED / TESTS_NOT_EXECUTED`，而不是“环境不存在”或“测试通过”。放行需要：(1) 独立树 owner/role、老田仓与 zhice 系统边界及审核 owner 确认；(2) 经批准的测试窗口、允许路径、临时根、拒绝路径、回滚/清理方案；(3) 所需 dev 依赖核对与安装由授权人审批，避免当前原地安装；(4) 对每一计划测试的 import/fixture/写入/网络/数据库做静态 side-effect 审计与注入替换；(5) 后续仅跑逐项隔离测试并保留命令、退出码、输出和写前后证据；(6) 如触及跨域接口、portal 投影、账本语义，需[DEC-20261017-07](manual-approval-decision-register-2026-10-17.md) 单独签批，同时 `REBASELINE-001` 不因本报告解除冻结。详见[契约测试矩阵](platform-contract-test-matrix-2026-10-18.md)。

回滚：只删除本次两份新增文档（需 owner 核准）；不存在由本工单创建的测试产物、环境/生产变更。静态核验判定：`IS_PASS: YES`（仅报告与来源边界自检），测试放行判定：`IS_PASS: NO / BLOCKED`。
