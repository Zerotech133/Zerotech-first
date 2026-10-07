# 第七批实施计划：官方日历独立对照与离线公钥审核

用户授权继续按计划团队实施，并选择本人公钥签名、离线审核。目标是完成2025-01-02至2026-04-01的455天独立日历参考和差异报告，交付Ed25519认证适配器与本人操作的签名工具；没有真人公钥与授权策略登记时，CLI人工审核继续关闭。

## 架构与边界

工作目录work/ai_operator_phase7是第六批冻结副本，375项基线已在E盘验收。Python3.11；cryptography用于Ed25519，BeautifulSoup用于HTML结构解析，两者已确认安装。无Git仓库，沿用隔离副本及哈希验收。不改memory.py/DDL、生产行情/共享数据库/账本/凭据，不训练/交易/启动常驻服务。所有实际验收使用outputs专用库。

A拥有evidence/与reports/evidence.md：补官方交易周依据和目标区间临时休市/变更检索，保存原文及真实下载回执；不从本地日历反推参考。完成后独立审查B/C/CLI/签名工具，禁止自己派代理。

B拥有ai_operator/signed_review.py、tests_ai_operator/test_signed_review.py、reports/signed_review.md：Ed25519身份/授权策略适配器，按系统received_at重验历史策略。私钥只在测试临时目录；不生成用户真实私钥、不签真实批准。

C拥有ai_operator/calendar_reference.py、tests_ai_operator/test_calendar_reference.py、reports/calendar_reference.md：严格解析官方正文所载日期与交易周规则，生成可重验日历参考并与已接收日历逐日对照。A的材料取得可并行，C先工程夹具测试，后用真实材料联调。

主代理拥有CLI、CLI测试、offline_review_signer.py、签名工具测试、acceptance.py、outputs及进度。真实身份接线在本人完成公钥登记和授权策略独立核实后另行实施；本批不提供请求级策略/公钥安装入口。

## B 公钥策略契约

`SignedReviewVerifier(bundle, expected_bundle_sha256)`：bundle由可信应用代码提供，expected摘要在请求以外固定。严格JSON，规范UTF8最多256KiB，摘要SHA256匹配；深拷贝，不允许构造后的调用方修改策略。模块顶层延迟导入cryptography，缺库抛ValueError；不得手写签名算法。

bundle严格 `{schema_version:1,authority_id,policies:[...]}`。非空authority_id；1至64份完整策略按effective_at严格递增且policy_id唯一。每份策略严格 `{policy_id,effective_at,principals:[...]}`；1至64个主体。主体严格 `{principal_id,key_id,public_key_hex,role,valid_from,valid_until,revoked_at,enrollment_reference}`。ID/登记引用非空；role限source_reviewer/source_admin；public_key_hex为32字节小写hex，key_id等于公钥原始字节SHA256；策略内不得重用相同key或key_id。valid_from、valid_until及非null revoked_at为规范UTC时间，valid_until>valid_from；策略effective_at为可信登记可用时间。enrollment_reference只是可信管理员登记引用，本模块不自动验证人与密钥绑定；摘要本身也不证明管理员或真人身份。

`signing_message(statement, authority_id, policy_id) -> bytes`：复用第六批statement规范化，消息为canonical_json的UTF8：`{domain:'ai_operator.human_review.ed25519.v1',authority_id,policy_id,statement}`。域、authority、policy和完整声明绑定签名，返回标准字节。

`verify(statement,proof,*,received_at) -> principal`：严格proof固定 `{schema_version:1,algorithm:'Ed25519',authority_id,policy_id,key_id,signature_hex}`，签名64字节小写hex。按系统received_at选择effective_at<=received的最新策略，只接受该policy_id和authority；不能用旧policy绕过后续撤销。策略/主体尚未生效、权限过期或revoked_at<=received均拒绝；received_at不可在未来。Ed25519验证消息，返回第六批固定四字段principal（principal_id/authority_id/policy_id/role）。历史查询仍传原事件received_at，因此今天新增策略不能抹掉过去合法批准。证明中的签名/权限不依赖自填姓名，source_verified/production_eligible仍不由此产生。

测试先RED再GREEN：真实库工程capture/review+Ed25519 key，不仅stub；篡改statement/域/authority/policy/key/signature、旧策略重放、有效期/撤销迟到、历史原时点、跨主体角色、重复键/私钥误入/超限、调用方策略突变、无依赖、并发只读及record/load/撤销目标接线。

## C 日历参考契约

`build_calendar_reference(documents, coverage_start, coverage_end) -> dict`；`validate_calendar_reference(reference) -> dict`；`compare_calendar_reference(reference, calendar_capture) -> dict`。

documents为非空列表，每项严格 `{kind,url,raw_utf8,sha256}`，kind为week_rule/annual_notice/supplement；URL为SSE无凭据HTTPS真域，原文UTF8最多1MiB，SHA绑定原文。URL与hash仅为原件/文本来源声明，不是网络或签名认证。使用BeautifulSoup从官方HTML正文提取结构化段落，拒绝仅title/nav/comment/script夹带规则；纯文本从PDF提取时需标记为文本证据并保存原PDF下载及提取链在A报告，不能声称UTF8摘要等于PDF字节。

标准交易周依据必须在正文明确每周一至周五以及公告休市例外。缺失时拒绝构造完整参考，不猜周末规则。年度通知正文明确年份；窄格式解析月/日/星期、休市区间、重开日、另外周末休市，校验真实日期与星期、区间方向、同年跨度、重开日与生成日历一致；覆盖每个目标年份，不能默默漏掉未识别休市条款。supplement只接受已支持且可严格解释的休市/重开格式，未知更正、开放冲突或临时规则拒绝或明确阻断，不能取最后一条当权威。

输出含schema_version=1、exchange='SSE'、coverage_start/end、compiled_at、documents完整原文、解析条款与来源引用、每个自然日的 `{session,is_open,basis}`、reference_id（除自身全对象hash）、exception_completeness_verified=false、source_authenticated=false、production_eligible=false。规则与节假日依据可以导出完整455天推导参考，但完整临时休市检索仍是独立待审核项；不得把未找到公告写成“证明没有临时休市”。validate从原文重建语义（保留compiled_at重算）并核对hash，不仅相信调用方days/parsed条款；篡改后重hash的假开市必须拒绝。

compare先重验参考与第四批calendar_capture，并核对SSE身份、逐日覆盖。返回match/mismatch/incomplete的描述性对照、compared_days、差异session/expected/actual/basis、reference_id/capture_id，所有来源/生产/训练资格false。相同本地分片不当作独立参考；只对reference覆盖区间比较，参考以外不冒充核验。

测试先RED再GREEN：单日/跨月休市、春节/调休周末、不把补班周末判开市、合法星期、错年/错星期、缺年份/周规则、未知文本、重开冲突、修订/篡改重hash、证券交易所错配/日历缺日、逐日匹配与差异、多份材料冲突/来源重复。实际参考取A已归档原文，逐日与本地455天对照，不改真实日历。

## 本人签名工具与CLI

独立offline_review_signer.py用cryptography生成加密Ed25519 PKCS8 PEM私钥，公钥32字节与SHA指纹可供登记。keygen/sign必须交互TTY、本人确认并输入口令，拒绝通过命令行/环境/JSON传口令；不覆盖已有文件，创建私钥时使用排他创建并尽可能restrict权限。不得自动生成或签署真实身份材料，主代理只运行help与工程临时夹具。

sign读取规范statement，打印关键capture/review/action/expiry由本人确认；按固定域消息签名，输出标准proof，不输出口令或私钥。工具自身不认证公钥对应老田、不安装信任策略、不改变来源资格。申请签名的声明即使内容一致也须实际证据和业务授权。

CLI新增calendar-reference --input（documents/coverage_start/end）与calendar-compare --input（reference/calendar_capture），均只读对象计算；human-review默认关闭不改。主代理先测试再接CLI，沿用8MiB严格JSON契约。

## 验收与台账

- [x] 核对第六批冻结接口、375项基线及用户离线公钥通道选择。
- [x] A官方交易周依据、年度/临时公告材料和检索范围归档（完整性缺口保留）。
- [x] B Ed25519策略适配器先RED后实现、定向GREEN。
- [x] C严格参考与逐日对照先RED后实现、真实455天联调。
- [x] 本人签名工具/CLI、真实缺口验收与完整隔离回归。
- [x] 独立复审、修复及源码冻结。
- [x] 限定目录接入、目标复验、文档与总体进度编制。

工程验收：439项中435通过、4既有权限跳过；目标真实验收通过。离线工具14项、策略20项、日历25项、CLI5项新增共64项全部通过。最终交付包与文档同步按冻结清单执行；本人公钥登记、规则条件生效佐证和临时停市完整检索仍是明确待办，不计为工程测试已解决。

独立验收库复用真实300根行情与11日历分片，仍3事件完整导出、H5/H20 audit_only/null。官方推导参考逐日比较以实际结果报告，保留exception完整性和身份登记缺口。真实私钥/登记策略不生成，可信真人审核与来源放行不计为完成。E盘接入仍只用既有三目录，写前核对CLI与总体进度基线并追加协定。
