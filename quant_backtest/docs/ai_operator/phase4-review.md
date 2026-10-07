# 第四批独立审查记录

审查对象：冻结计划、provenance.py、trading_calendar.py、source_inputs.py、cli.py、四份增量测试和acceptance.py。独立只读代理预检、初审和修复复审；未参与实现文件修改。

初审两项P2：行情路径未提前拒绝UNC，路径检查可能访问网络共享；组合标签未拒绝早于日历覆盖的决策，可能把跨年后的首日猜成参考交易日。均按RED→GREEN修复：行情capture/validator统一拒绝UNC和Windows设备命名空间；组合入口在中国时区的覆盖起点校验决策。相关测试验证网络文件访问前拒绝及UTC前日16:00等于中国日期起点的边界。

最终复审无剩余阻断问题。增量64项，62通过、2项真实symlink创建因Windows权限跳过；双方真实父目录junction测试通过。身份核对、receipt时间约束、来源组合结构哈希、unknown固定、完整决策重建与现有CLI/DB契约一致。

## 冻结源码SHA256

| 文件 | SHA256 |
| --- | --- |
| provenance.py | 0150042d58db0ea458b91fbb1bcc4fed43be0afad03a70f258cec2b799ddb509 |
| trading_calendar.py | 6c81dd975e778f491c33b1fb832a72f4d3bcc0ceffb6ac9977e45e7d60a6b424 |
| source_inputs.py | e5f1198805e5c00de2b8c8c0b96e732887ab504ac5d15e9f36443518a88a7b4c |
| cli.py | ec438a5eabb8c100a923bb2148a810c4cdc3c09c5528c2552aa0c647aab81b4d |

已知边界：哈希不是供应商签名，外部能重造整包再算哈希；current capture不证明历史可用或过去日历已知；来源未审核，标签仅audit_only；Parquet解压后内存无独立硬上限。当前输入为可信本地文件操作，未引入外部上传服务。
