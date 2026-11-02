# /quant P1 same-origin 流水线构建回执 v1

- 日期：2026-11-02 14:58
- 目标：用可复现 clean build 流水线产出 `/quant/` 页面包（含 VERSION.json 审计），替代手工构建
- 结果：**PASS**；流水线 same-origin 模式落地，新包已发布到 `portal/quant`；L1 **18/18**

## 一、流水线增强（P1）

`tools/hermes_acad1_build.py`（+298/−55）：
- 新增 `--same-origin`；`--api-base` 为相对路径时自动启用；
- 同源 patch：`://${location.host}/api/v1` → `://${location.host}/quant-api/api/v1`；裸 `/api/v1` → `/quant-api/api/v1`（防重复）；logo → `/quant/logo*.png`；
- 断言：裸前缀残留=0、WS host 残留=0、`127.0.0.1:8105/5175`/`autoSession`/`zhice_auto_password`=0、`/quant-api/api/v1` 存在；
- 绝对 origin 模式行为不变；新增 9 个测试（`tools/tests/test_hermes_acad1_build_same_origin.py`），全绿。

## 二、真构建（2026-11-02 14:55:11）

- version `20261102_145511_f8bfac91`；typecheck=pass、vite_rc=0；耗时 17.4s；
- 源：外部仓 HEAD `f8bfac91`，`dirty_count=0`；
- VERSION.json `same_origin.clean = {dirty_count:0, auto_session_hits:0, legacy_port_hits:0, bare_api_remaining:0, api_prefix_hits:18}`；
- env：`VITE_BASE=/quant/`、`VITE_API_BASE=/quant-api/api/v1`；
- 独立 clean_build §4.1 断言：`CLEAN_BUILD_ASSERTS_OK`；`--verify-only --same-origin` rc=0。

## 三、发布与验证

- 旧包备份：`C:\项目\backups\portal_quant_prev_20261102_145732`；
- 新包：`portal/quant/`（8 文件 + VERSION.json；JS sha256 `2cf6c176…`）；
- 8734 `/quant/` → **302**（登录跳转）；包内 `autoSession` / 5175 / 8102 / 8105 字面量 **0**；
- **L1 `l1_acceptance.py` → 18/18 PASS，rc=0**。

## 四、附带处置（并行会话提交）

- 并行提交 `37b896f4f`（health retired，声明“8091重启生效”）导致 8091 代码 mtime > 进程启动时间，L1 一度 PENDING=1；
- 按已授权 L2 做受控换血：pid **29552 → 3996**（14:58:43），`/health` 200；L1 恢复 18/18。

## 五、限制/后续

- 二次构建复现对比未做（可重现性以 VERSION sha256 + verify-only 对账）；
- 浏览器登录态同源联调待 `/quant/`+`/quant-api/` nginx 路由（已 live 验证 302/401 三态）；
- 回滚：恢复 `C:\项目\backups\portal_quant_prev_20261102_145732` 到 `portal/quant`；工具回滚 `git checkout -- tools/hermes_acad1_build.py`。

## 六、证据

- VERSION：`portal/quant/VERSION.json`
- 构建日志：`deliverables/2026-11-02_quant_rebuild_build.log`
- 流水线证据：`C:\项目\deliverables\2026-11-02_same_origin_build_证据_v1.md`
- 交付清单：`C:\项目\deliverables\2026-11-02_L1_quant页面包\`
