# BLOCKERS —— 等老板拍板清单（集中收口）

> **立宪依据**：`docs/plans/2026-10-12_PRODUCT_ROADMAP_v1.md` 治理规则五——所有「等老板拍板/等外部凭证/等裁决」项**只在这里登记**，看板与日报只放引用行，不再各自散落。
>
> **维护规则**：
> - 每项必填：编号 / 标题 / 阻塞影响 / 当前状态 / 拍板选项（含推荐项）/ 提出工单号
> - 拍板后：状态改「已拍板」+ 登记拍板结果与执行工单号，**条目保留 30 天后归档删除**
> - 新增阻塞项必须当天入册，否则视为漏账

| 编号 | 标题 | 阻塞影响 | 状态 | 提出工单 |
|---|---|---|---|---|
| B-01 | 跨来源叠单语义 | judge critical 误报类 | ⏳ 等拍板 | 挂账 #7 |
| B-02 | 多组合验收口径扩展 | acceptance_check 无法判 pf_002 | ⏳ 等拍板 | 2026-10-12 对齐清单 |
| B-03 | MP 凭证索取 | 批 D 无法开工 | ⏳ 等外部 | 挂账清单 |
| B-04 | scheduler 主从判定规则 | 双实例风险 | ⏳ 等拍板 | 双实例清理拍板遗留 |
| B-05 | home.html+8094 驾驶舱退役 | 四入口分裂 | ✅ 已拍板执行（2026-10-12） | 门户重排 K-STOCK-166 盘点 |
| B-06 | webroot json 泄露面 | 9 个运行时 json 可被公网直读 | ⏳ 等 nginx 维护窗口 | 版面宪法 2026-10-12 |
| B-07 | worktree/分支回收权限缺口 | 21 个 worktree、16 个分支拒收（非 UAC 会话无法删），仓治理不闭环 | ⏳ 等管理员会话 | WO-P0-CLOSE 2026-10-27 |
| B-08 | Phoenix server 依赖冲突 | P0-6 观测只做 instrumentation，Phoenix 面板起不来 | ✅ 已起（.venvs/phoenix + arize-phoenix，6006/4317/4318 health=200，2026-10-27 17:15；自启需批准） | 诸葛P0-6 2026-10-27 |
| B-09 | SECVAULT Phase 2 + token 轮换 | 生产代码仍直读明文凭据；Tushare 旧 token 按已暴露处理待轮换 | ⏳ 等老田/维护窗口 | SECVAULT 2026-10-27 |
| B-10 | P1-1/P1-2 生效观察 | 8091 已重启（14:21），引用率 8/8=100% 已验；recall@3 观察窗口待跑 | 🟡 观察中 | WO-P0-CLOSE 2026-10-27 |

---

## B-01 跨来源叠单语义

- **实况**：600593 双批 = double_knife 3200 股（pf 主账户）+ pf_002（taishan）3700 股，**合法跨来源叠仓**，非 077q 重复下单。judge 首日将其判为 critical，属误报类。
- **阻塞影响**：多组合化后（hires.json source 字段），任何标的都可能被多个组合同时持有；judge 若不感知 pf_id 维度，叠仓 critical 会持续误报。
- **拍板选项**：
  - **A（推荐）**：叠仓按 pf_id 分桶各自计量，仅当**同一 pf_id 内**重复下单才判 critical；
  - B：叠仓总量超单票上限才判（跨组合合并计量），judge 加总量阈值；
  - C：叠仓一律放行，仅日报提示。
- **说明**：选 A 需 judge 读取 hires.json 的 source→pf_id 映射；选 B 最保守但会把四条 RiskGuard 规则里的仓位集中度阈值再拉一层。

## B-02 多组合验收口径扩展

- **实况**：现验收判据按单组合（pf 主账户）编写；pf_002（taishan）激活后，16:00 撮合验收五判据未覆盖第二组合。
- **阻塞影响**：pf_002 的成交核验无判据依据，验收通过≠全组合正确。
- **计划**：**10-13 扩展**（遵守判据冻结纪律：验收当日不动判据代码，扩展次日版本化为 acceptance_criteria_v1）。
- **待拍板点**：扩展后 pf_002 是否纳入 enforce 级（阻塞验收）还是 observe 级（只记录不拦截）首日。推荐 observe 首日 → 次日升 enforce。

## B-03 MP 凭证索取

- **实况**：批 D（MP 端）依赖外部凭证，尚未取得。
- **阻塞影响**：批 D 全线无法开工，其余批次不受影响。
- **动作**：等老板向 MP 方索取；取得当日登记于此并解锁排期。

## B-05 home.html + 8094 驾驶舱退役（✅ 已拍板执行）

- **拍板**：2026-10-12 老田选方案 A——废弃独立驾驶舱，数据面收编进门面。
- **事实修正**：盘点头注「8092」不准——home_server.py 实际端口 8094（K-STOCK-047 改），且计划任务在拉、nginx `/cai/api/` 反代在，属「活着但无站内入口」；唯一完整路径是手敲 8094。
- **执行**：cockpit 五投影（此刻条/我的钱/时间线/裁决窗/组织）移植 portal_api `/api/home`（api_misc.build_home_cockpit，全 try 降级）；index.html 门面新增「此刻区」；home.html+home_server.py 进回收站（备份 %TEMP%/lingjie_home_retire_backup_20261012）；8094 进程已停、计划任务「AI工作站零界驾驶舱8094」已禁用。
- **遗留（低危）**：nginx `location ^~ /cai/api/` 反代指向已死端口，无消费者，下次 nginx 维护窗口一并清理；菜鸟对话（answer/Ollama）未移植——门面 cainiao.js 已覆盖同职能。

## B-06 webroot json 泄露面（⏳ 等 nginx 维护窗口）

- **实况**：nginx 404 拦截后缀为 `py|pyc|ps1|md|conf|enc|key|token|env`，**不含 json**——portal/ 即 webroot，9 个运行时 json（ai_market/algo_market/audit_ai_config/licenses/mixin_baseline/qd_replay_verify/qd_scan_cache/route_baseline/strategies）均可被 `GET /xxx.json` 直读。全部有活代码引用，不能挪文件。
- **影响**：中低敏（市场缓存/扫描缓存/基线），非密钥类，但架构上不该暴露。
- **修法（一个维护窗口内完成）**：拦截正则加 `json` + 给 `/static/` 加 `^~` 前缀 location（china_geo.json 等静态资产豁免直出）→ `nginx -t` 验证 → Docker nginx reload → 全量 9 json 复测 404 + static 资产复测 200。
- **风险**：reload 期间 8080 短暂闪断；配置错会让门面挂——必须先 `nginx -t`。

## B-04 scheduler 主从判定规则

- **实况**：双实例清理已拍板执行（留单实例），但「若再次出现双实例，以什么规则判主」未定版。临时口径：日间 CPU 时间累计高者为存活主。
- **阻塞影响**：巡检遇双实例时处理动作不统一，有误杀主实例风险（10-12 连锁死教训：无监听≠可安全杀）。
- **拍板选项**：
  - **A（推荐）**：写死规则入 N1Check——端口 8091 归属进程为唯一主，其余一律报障等人工，不自动杀；
  - B：CPU 时间判主自动清理（有误杀风险）；
  - C：双实例即告警不处置。

## B-07 策略注册表 live 条目缺 since（⏳ 等老板回填日期）

- **实况**：portal/strategies.json 6 个 live 条目（ninedim/weak/linkage/cross/qpzm/strong）全部缺 `since` 字段，违反 362 定稿「上架必带 since（YYYY-MM-DD），为 60 交易日考核起算」。契约回归 test_dashboard_contracts.cjs 已设 WARN 档（不阻塞、不沉默），回填后升硬断言。
- **阻塞影响**：60 交易日转正考核无法起算；shadow_to_live 晋升材料（315 五臂表）缺时间锚。
- **需要老板**：各条目实际上架日期（或授权按首次信号入档日期回填）；回填后 pipeline 字段（E5）与记分卡考核链即可接上。
- **发现渠道**：E9 契约回归（2026-10-19 E 批开工日）。

---

## 2026-10-27 14:40 状态更新（老田指令「执行① B-01/02/03/04/06」后）

> 依据：`docs/plans/2026-10-27_B系列裁决与执行记录_v1.md`。原则：已拍板项标「已拍板」，不需再等；仍阻塞项标原因。

| 编号 | 更新后状态 | 依据/执行单 |
|---|---|---|
| B-01 跨来源叠单语义 | ✅ **已拍板：选 A**（叠仓按 pf_id 分桶；仅同一 pf_id 内重复下单判 critical） | 裁决记录 §1；实现转投研/交易系统线（judge 不在主仓） |
| B-02 多组合验收口径 | ✅ **已按推荐生效：pf_002 = observe**（不进总判）；升级 enforce 需 v2 + 老田拍板 | `acceptance_criteria_v1.md` §二；建议 11-03 复盘后决策 |
| B-03 MP 凭证 | ⏳ **等外部凭证**（无变化） | 无动作 |
| B-04 scheduler 主从判定 | ✅ **已拍板：选 A**（8091 归属进程为唯一主；其余报障等人工不自动杀）；锁序修复已完成（JEVLOCK，测试 4 passed）；N1 双实例检测实现列下一小单 | 裁决记录 §4 |
| B-06 webroot json 泄露面 | ⏳ **等 nginx 维护窗口**（执行包已出：拦截加 json + /static/ ^~ 豁免 + nginx -t + reload + 9 json 复测） | 裁决记录 §5；建议 22:00 后 10 分钟窗口 |

## 2026-10-27 14:40 新增已拍板记录（入册）

| 编号 | 事项 | 拍板/状态 |
|---|---|---|
| MCP-01 | MCP 双面口径：**平台允许**（servers.json tushare-http enabled=true，token 经 SOPS 注入环境）/ **IDE 不需要**（.mcp.json 不启用、不落 token） | ✅ 已定版（`docs/plans/2026-10-27_B系列裁决与执行记录_v1.md` §6） |
| A3-01 | A3 凭据轮换：**已裁定 B（接受风险）**（老田 2026-10-27 05:05）；canary 每日任务已装（A3RotationCanary 07:00 S4U，smoke rc=1 预期） | ✅ 监控常态化；对外表述「已泄露未轮换（2026-10-27 接受风险）」 |
