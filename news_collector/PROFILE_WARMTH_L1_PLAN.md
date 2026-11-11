# Layer 1：xq 雪球 Profile 升温 / 轮换方案（R26）

> 来源：老田 2026-11-12 指令「核一下 xq_auto_login_profile 是否真·warm，并起草 Layer 1 的 profile 轮换/升温方案」。
> 角色边界：本席只做采集侧自检与方案起草，不擅自登录、不克隆生产浏览器状态、不触发扩源决策。落地需老田裁定。

---

## 一、Warmth 核查结论（2026-11-12 06:5x，只读核验）

**判定：FALLBACK PROFILE 真·WARM；PRIMARY PROFILE 缺失（代码已正确降级）。**

| 项 | 值 | 含义 |
|---|---|---|
| `tmp/xq_auto_login_profile`（PRIMARY） | **不存在**，目录为空 | 从未创建；`_launch_ctx` 中 `os.path.isdir` 判否 → `continue` 跳过，**不**会拿 cold 空 profile 顶上（这点代码是稳的） |
| `data/xueqiu/_headed_profile`（FALLBACK） | 存在且含 `Default/Network/Cookies` | 实际被 07:00 / 21:00 等所有 xq run 使用 |
| xueqiu cookie 总数 | 13 | 5 持久未过期 + 7 会话 + 1 已过期 |
| 关键登录态 | `xq_a_token`✅ `xq_r_token`✅ `u`✅ `device_id`✅ `acw_tc`✅ | 登录三件套齐全，鉴权层**不冷** |
| 主要 token 到期 | 2027-10 / 2027-11 / 2027-12 | 持久登录态，远未过期 |
| Cookie DB mtime | 2026-11-11 23:21 | 上次干净关机写入；今日 07:00 run 撞 WAF 早停未 flush → `last_access` 停在 23:21 是**假象**，非真冷 |

**核验方法**（只读，未改任何生产文件）：`sqlite3` 经 Python311 以 `mode=ro&immutable=1` 打开 `Default/Network/Cookies`，统计 `host_key LIKE '%xueqiu%'` 行数、`has_expires`/`expires_utc` 明文列、`xq_a_token`/`u` 等关键 name 是否存在。`value` 列是 DPAPI 加密读不到，但 `expires_utc` 与 `name` 足以判定暖度。

---

## 二、核查暴露的架构脆弱点（必须修）

1. **单点依赖（最高危）**：全部 xq 采集（`xq_kol` + `xq_stock_timeline` + R24 的 21:00 expanded shard）**100% 依赖唯一一个 `_headed_profile`**。一旦该会话被雪球服务端吊销或 token 到期 → 无 fallback → xq 全线停摆。
2. **PRIMARY 名存实亡**：`tmp/xq_auto_login_profile` 设计意图是「当日新收割的登录」，但从未被创建，等于空设。代码靠 fallback 续命，掩盖了单点风险。
3. **并发锁竞争**：`xq_kol` 与 `xq_stock_timeline` 共用同一 profile 路径变量（均 `from xq_kol import PROFILE_DIR, PROFILE_FALLBACK`）。若两个 run 时间重叠（如 21:00 分片与某手动 run 撞车），Chrome 对单 user-data-dir 持锁 → 一方 `_launch_ctx` 抛异常、降级到不存在的 PRIMARY → 直接 `RuntimeError 无可用登录 profile`。

---

## 三、WAF 根因再判定（修正此前假设）

- 鉴权层**不冷**（token 有效且远未过期）→ 此前「WAF 因登录态冷」的猜测**不成立**。
- 因此 WAF 挑战是**行为/频控型**（单会话高频请求触发），不是鉴权缺失型。
- 这进一步印证 **R24 的「冷却 300s + 重试 3 次 + 仅持续封锁>20% 才停」是对的方向**；Layer 1 的「轮换降频」是从源头压低单会话频次的补充杠杆。

---

## 四、Layer 1 方案（5 子项，标注可自动化 / 需人工）

### L1.1 Profile 轮换池（降频 + 冗余）— 部分需人工
- 新增 `news_collector/xq_profiles.json`：声明一个 warm profile 池及优先级 / `last_used` 轮转字段。
  ```json
  {"pool": ["data/xueqiu/_headed_profile", "data/xueqiu/_p2", "data/xueqiu/_p3"],
   "strategy": "round_robin", "last_used": {}}
  ```
- `xq_kol._launch_ctx` 与 `xq_stock_timeline.main()` 改为**读池**而非写死的 PRIMARY/FALLBACK 两个常量；每次 run 取池内 `last_used` 最久的 profile，写回 `last_used`。
- **效果**：3 个 profile 分摊 647 只请求 → 单会话日均频次约降至 1/3，直接压低 WAF 触发率；同时任一个失效有另两个兜底。

### L1.2 Warmth 只读巡检（纯自动）
- 落地 `news_collector/check_xq_warmth.py`（即本次核验脚本的生产化版）：读池内每个 profile 的 `Network/Cookies`，输出 JSON `{profile, warm, xq_cookie_count, has_a_token, has_u, min_expiry_utc}`。
- 注册为 **06:55 预检自动化**（早于 07:00 基线 run）：任一池成员 `warm=false` → 发告警，**不**自动修（避免 Agent 盲目重试触发 WAF 螺旋）。

### L1.3 Run 前冷停闸（纯自动）
- `run_xq_daily.py` / `run_xq_daily_expanded.py` 启动 Chrome 前先跑 L1.2 巡检：池全冷 → 以 `rc=3` + 日志 `PROFILE_COLD` 退出，**省下无效请求、避免 WAF 升级**。

### L1.4 克隆 / 新建 warm profile（需人工，关键约束）
- **Agent 不能直接做**：雪球登录含验证码/SMS，且克隆 profile 会复制 `device_id` 指纹。
- 安全做法（二选一，归老田裁定）：
  - **A（推荐）独立人工登录**：在 `_p2`/`_p3` 各自用真实浏览器手动登录雪球，拿到各自独立 `device_id`/会话，规避「同指纹多会话被风控」。
  - **B 克隆 + 验证**：复制 `_headed_profile` 目录为 `_p2`，跑一次小批量（如 3 只）验证 token 是否被服务端接受；若 WAF 持续 → 退回 A。

### L1.5 登录态刷新 SOP（人工为主）
- 文档化：每 ~30 天或收到 `PROFILE_COLD` 告警时，人工开对应 profile 的 Chrome 登录雪球刷新 `xq_a_token`/`xq_r_token`。
- 未来可研：若雪球提供 refresh token 端点，可用现有登录态静默续期（标记为后续调研，非本次范围）。

---

## 五、落地步骤与分工

| 步骤 | 动作 | 自动/人工 | 落地状态 |
|---|---|---|---|
| 1 | 写 `check_xq_warmth.py`（只读巡检） | 自动 | 待裁定实现 |
| 2 | 写 `xq_profiles.json` 池 schema | 自动 | 待裁定实现 |
| 3 | 改 `_launch_ctx` + `main()` 读池 + 轮转 | 自动 | 待裁定实现 |
| 4 | `run_*` 加 L1.3 冷停闸 | 自动 | 待裁定实现 |
| 5 | 注册 06:55 预检自动化 | 自动 | 待裁定实现 |
| 6 | 人工建 `_p2`/`_p3` warm profile（L1.4 A/B） | **人工** | 阻塞于老田 |
| 7 | 30 天刷新 SOP 文档化 | 人工 | 待裁定 |

---

## 六、风险与局限

- **克隆指纹风险**：同 `device_id` 多会话可能反而抬高 WAF → 优先独立人工登录（L1.4 A）。
- **巡检不读 value**：DPAPI 加密，巡检只能验证 cookie 存在 + 未过期（`expires_utc` 明文），**不能**确认服务端未吊销会话。真·有效性最终由 run 是否撞 WAF 反证——故 L1.2 告警 + L1.3 冷停闸是「软保护」，非硬保证。
- **本次核查样本**：单点（本机 `_headed_profile` 一个 DB），未跨多机；若将来多机部署需各自巡检。
- **操作纪律**：步骤 6 涉及改写/新增生产浏览器 profile，按铁律须老田明示裁定后由人工执行；本席不代劳登录。

---

## 七、待老田裁定

1. 是否批准实现 L1.1–L1.3 + L1.5 的**自动化脚手架**（步骤 1–5），并把巡检注册为 06:55 预检？
2. 新 warm profile 走 **A（独立人工登录）** 还是 **B（克隆+验证）**？
3. 轮换池规模：先 2 个（现 1 + 新增 1）还是直接 3 个？
