# 第五批独立审查记录

只读调查、设计预检、初审与修复复审由独立代理执行，没有参与实现文件编辑。

初审发现P2：同源、无前驱的两次观察在系统时间相同时，事件完全相同，MemoryStore去重复用旧ID，第二次采集伪成功。修复采用系统生成的uuid4.hex observation_id，纳入完整payload/event双哈希；严格格式校验，调用者不能传入。receipt顶层、stream和DDL不变。

复审未发现阻断问题。61个新增模块测试通过；独立固定系统时钟、双连接、两个线程同时读取相同源，生成两个不同observation_id和event_id，得到两条events和outbox，两个完整receipt校验通过。无伪造接收时间。

已核查基础元字段与两哈希、receipt/来源身份、前驱时间顺序、durable可用时点、报告生成与落库分离、证据原始字节和语义重算。全匹配最多待人审，gate仍拒绝放行；真实来源证据不足，不升级trusted。

## 冻结源码SHA256

| 文件 | SHA256 |
| --- | --- |
| capture_journal.py | 098b163cdcccb994781e7de8cd989800375cc81bbee4fa0c3c051712c08f1873 |
| source_review.py | 284b07dd593f3de7eaefea322a0cff677f77be809e2851197aa9d66c3099e59d |
| review_gate.py | f397b799fbaca95bdca279314981ba6eeec039e9998c46bc53cecc2ebd59d973 |
| cli.py | b098156959f1a215c85cb7c881dd5cc924f7122d2ec4d32bc67a586d122f17a0 |
| acceptance.py | 907c60b5ccc29f019ed039064b5bae1921bd141f0f1d4730b0a020733c509630 |

来源调查证实文件mtime不可充当行情日期或历史available，股票缓存可能包含qfq支路，日历同源重复不是独立官方参考。完整调查另交付。

已知边界：memory事务内时钟回退可能提交完整事件后拒绝receipt，load与标签会拒绝，定向测试与文档已覆盖；管理员可以重造合法事件，哈希不认证来源。第五批修订前的中间库保留到work，最终验收需使用增加observation_id后的新契约。
