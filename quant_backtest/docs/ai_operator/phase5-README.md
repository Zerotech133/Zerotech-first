# 第五批运行说明

本批将第四批现场接收持久保存到既有独立审计库，并把来源证据绑定到具体接收事件。每次接收是一次新的系统观察，重跑产生新记录；没有常驻采集调度，不重建伪造的历史接收时间。

## 命令

在 `E:\老田的AI助手\quant_backtest` 中按需运行，使用已通过init初始化的专用审计库：

```powershell
& 'C:\Program Files\Python311\python.exe' -B -m ai_operator capture-journal --db '<专用审计库.sqlite>' --input '<接收请求.json>'
& 'C:\Program Files\Python311\python.exe' -B -m ai_operator capture-history --db '<专用审计库.sqlite>' --input '<历史链请求.json>'
& 'C:\Program Files\Python311\python.exe' -B -m ai_operator source-review --db '<专用审计库.sqlite>' --input '<证据登记请求.json>'
& 'C:\Program Files\Python311\python.exe' -B -m ai_operator review-gate --db '<专用审计库.sqlite>' --input '<门槛查询请求.json>'
& 'C:\Program Files\Python311\python.exe' -B -m ai_operator journal-outcome --db '<专用审计库.sqlite>' --input '<影子标签请求.json>'
```

成功返回0，输入/文件/时点错误返回2并输出JSON错误。写命令只写传入的已有审计库，先只读预检，不创建或接管外部数据库。源文件仅只读，JSON请求仍限制8MiB。

### 接收与历史

接收请求为 `{source_request:<第四批行情请求>,calendar_request:<第四批日历请求>,previous_event_id:null}`。previous_event_id可以省略；续接时填上次返回的capture_event_id。两个快照在一个source.capture事件中整体提交，复用原有events/outbox，不增表。

返回完整receipt，包含capture_event_id、stream_id、previous_event_id、recorded_available_at、行情/日历快照和完整事件。持久可用时间不会早于实际接收或落库时间；所有时间仍不证明真实供应商的历史到达时间。

事件payload中的observation_id由系统生成；即使两次实际读取取得相同时间戳，也记录为独立观察，不用伪造更晚时间绕过去重。调用者不能传入这个ID。

历史请求为 `{capture_event_id,as_of,limit:20}`，limit可省，允许1至100。结果为指定前驱链最近limit项，旧到新；不是整个库的所有来源，也不验证限额以外的前驱。它会拒绝跨来源/证券/价格口径的前驱、未来落库记录和时间倒退。修订声明可以改变，但会形成新事件，不覆盖旧观察。

### 证据登记

```json
{
  "capture_event_id": "填写已落库的接收事件ID",
  "reviewer": "审核记录作者",
  "evidence": {
    "supplier_identity": null,
    "price_basis": null,
    "revision": null,
    "official_calendar": null
  }
}
```

缺失文档保留null；存在文档用 `{path:"本地证据文件绝对路径",url:"HTTPS声明来源"}`。文档为严格UTF8 JSON，单个最多1MiB，记录完整原始文本、字节数和SHA256，日后删除或修改源文件也能离线复验。

四类文件结构如下：

```json
{"source_id":"local-ashare-index-file","provider":"供应商名称"}
{"code":"000001.SH","price_basis":"raw","adjustment_reference":null}
{"revision":"修订声明","source_sha256":"行情源文件64位SHA256"}
{"exchange":"SSE","rows":[{"cal_date":"2026-01-05","is_open":1}]}
```

声明与receipt逐项匹配；官方日历需覆盖每个自然日且与开闭市一致，URL域名对应sse.com.cn或szse.cn，合法子域也允许。域名和URL只是声明，不自动下载或认证官方文件。复权口径qfq_snapshot/hfq_snapshot即使声明一致，也保留adjustment_factors缺口。

状态：needs_evidence为缺文档或覆盖，mismatch为不一致，ready_for_human_review为文档匹配但仍需人审和真实下载/来源证明。自行按数据生成一致文档不能充当独立官方核验。

### 门槛与标签

门槛查询请求为 `{capture_event_id,review_event_id,as_of}`。返回blocked_missing_evidence、blocked_mismatch或blocked_pending_human_review，均eligible=false、production_eligible=false。查询按接收及审核事件的实际落库时间校验，不能让后到的报告出现在历史时点。

影子标签请求为 `{capture_event_id,pack,decision,evaluated_at,horizons:[5,20]}`，horizons可省。从库中读取receipt，核对完整证券代码/周期和日历覆盖，再绑定落库证明生成标签。未审核来源保持unknown，标签audit_only、目标null；不提供trusted开关，不进入训练。

## 真实验收位置

E盘源码和测试已接入，并完成目标全量复验：317项，313通过，4个既有Windows符号链接权限跳过。`2026-10-07-第五批目标目录验收.json`及`第五批真实文件验收/目标目录复验/`保存目标目录实际运行证据。

`第五批真实文件验收/` 保存实际请求/结果、专用SQLite库及完整outbox/events.jsonl。两个真实接收形成链，三事件（两次接收、一次缺口审核）落库与导出；源文件和两份旧蒸馏样本SHA前后不变。当前行情仍仅300根，最新为2026-04-01，不是实时行情。

## 限制

内容哈希不是签名，有库权限者可以重造完整数据。来源供应商、下载执行链、价格口径/修订、复权算法/因子及独立官方日历凭证尚未核验；本批没有人工批准/撤销或资格历史回放。

参数、源对齐和前驱错误在写入前拒绝；SQLite事务失败会回滚整个事件和outbox。提交瞬间系统时钟回退可能保存完整事件后才被receipt拒绝，后续读取与标签会拒绝它；保留审计记录，不能声称任何失败都零写入。本批不修改共享memory/DDL或生产库。
