# portal 板块研究手册（2026-11-10）

> 从全仓审计（`docs/plans/2026-11-10_零界全仓审计与新研发计划_整合版.md`）抽出 portal 单板块，格式与 `docs/JEV_研究手册.md` 一致。

## 一、定位（先钉死）
- 零界的**唯一宿主入口**：人类入口走 nginx **8734**，API 服务 **8091**（FastAPI）。
- **不是**：独立产品；它是所有子系统（JEV / m_score / 数据仓 / dataops）的对外壳。

## 二、运行形态
- 启动由计划任务 `AI工作站门户API` 托管，**每 5 分钟轮询**；脚本带 health lock，已监听则不重启。
- **改 Python 后没有自动重载**：必须走 `tools/restart_8091_controlled.ps1`（flag → schtasks → 等新 pid + health=200）；纯静态 html 由 nginx 读盘，改完即可见。
- 门禁口径：`pytest portal/tests portal/test_bot_ws_backoff.py portal/test_km_card_kind.py`；解释器一律 `C:\Program Files\Python311\python.exe`（默认 python 是 3.13 无 pandas，会造假红）。

## 三、本轮审计结论（2026-11-10）
**P0：无。** 入口全部可编译、无明文密钥。主要债在一致性层。

### 已修（本轮，全部编译通过 + 8091 重载验证）
| 位置 | 问题 | 修复 |
|---|---|---|
| `api_screen_kline.py:204` | 同日 JEV 新闻去重按 `conf` 选（反相关 AUC 0.414） | 改按 `event_type_tier`（strong 优先），conf 仅留显示 |
| `api_jev_review.py:55` | 复核队列按 `conf` 排序（决策驱动） | 改按 `queued_at`（保留 key 名防前端断裂） |
| `m_score.py` + `score_registry.json` + `score_guard.py:29` + 两测试 | `jev_conf` 反相关指标进 5 核心评分 | 全链路换 `jev_signal`（强±1/弱±0.4 方向分）；活 `/api/m/score/core` 验证 |
| `api_dataops.py` | 路径混用 `C:\aios`/`C:\项目`；nginx 端口标 8080 | QB/RUNS 改 `STATION` 派生；端口改 8734 |

### 残余债（未修，P2）
- `api_screen_kline.py:198` 附近**裸吞错 except**——异常静默，故障被掩盖。

## 四、待办
- [ ] 吞错收敛：关键路径至少 `logging.exception`（阶段 A4）
- [ ] 与 JEV 手册联动：conf 显示透传字段是否彻底摘除，由 JEV 板块判定
- [ ] 阶段 A 验收门：8091 health 常绿、conf 零决策驱动、零 `C:\aios` 写死

## 五、一句话给研究者
portal 是**外壳不是内脏**：它的债几乎全是"一致性债"（conf 残留、路径混用、端口标错、吞错），没有编译级炸弹；
研究它别看功能，看**接缝**——每个 `except` 和每个写死的路径都是潜在断点。
