# requirements/ — 分线依赖快照（O8 P2）

> 状态：**快照阶段**（2026-10-27 首次生成）。只记录"当前 venv 是什么"，**不改安装源、不安装、不升级**。
> 背景/方案：`deliverables/2026-10-27_O8_依赖与发布治理_只读盘点与方案_v1.md`；D5 的 `uv.lock` 仍只锁 dev 工具链。

## 一、分线文件

| 文件 | 对应运行线 | 来源 venv / 解释器 | 包数 |
|---|---|---|---|
| `lines/system_py311_full.lock` | portal 8091、agent_platform、legion 等共用系统解释器 | `C:\Program Files\Python311\python.exe` | 513 |
| `lines/quant_backtest.lock` | quant_backtest | `quant_backtest/.venv_czsc` | 41 |
| `lines/cowagent.lock` | 飞书 bot（CowAgent） | `CowAgent/venv` | 88 |

> 其他线（Hermes/zerobound-kg/osint_video/智策）暂未生成，按需补。

## 二、生成方式（只读）

```powershell
# 当前 venv 全量快照；不改环境
& 'C:\Program Files\Python311\python.exe' -m pip freeze
& 'C:\项目\老田的AI助手\quant_backtest\.venv_czsc\Scripts\python.exe' -m pip freeze
& 'C:\项目\老田的AI助手\CowAgent\venv\Scripts\python.exe' -m pip freeze
```

- 文件头记录 `line/generated_at/source_python`；
- 行格式 `name==version`（pip freeze 输出），`#` 开头为注释；
- **快照不是最小依赖**：包含该 venv 全部已装包（含传递依赖）；用途是"重建/对比/排障基线"，不是新的安装源。

## 三、纪律

1. **禁止**把快照直接 `pip install -r` 到现网 venv（会安装/降级无关包）；
2. 如需重建验证：在**隔离 venv** 中安装（P2 后续窗口做），不碰 `py312`/`CowAgent/venv`/`.venv_czsc`；
3. 升级依赖必须走各线原 requirements/pyproject，快照随后重新生成；
4. P3 再引入"发布满 7 天才允许升级"（参考 OpenHands），当前仅记录、不阻断；
5. 快照文件可 git diff 对比漂移（同一线两次生成 = 变化可见）。

## 四、校验

```powershell
python -X utf8 tools/req_lines_check.py           # 人读
python -X utf8 tools/req_lines_check.py --json    # 机器读
python -m pytest tools/test_req_lines_check.py -q # 单测
```

检查项：文件存在且非空、每行是 `name==version` 或注释、无重复包、包数统计。
