# 20261014 告警闭环改造（alert-loop P0-2 + P1）

目标：任何「正确的拦截」不再静默。判例：今晨 StrongEod 06:30
freshness_guard 正确拦截（10-13 TdxSync 崩溃→板块仓停 10-12），exit 3
仅落 stdout——守卫行为对，告警缺位。cross 韧性改造
（20261014_cross_resilience_changes.md）已写好 source_health 检查与告警
函数，但未挂每日批，本此一并挂载。

涉及文件：新增 `quant_backtest/guard_alert.py`、
`quant_backtest/test_guard_alert.py`；修改 `quant_backtest/strong_scan.py`、
`quant_backtest/rps_resonance.py`。tdx_sync.py / qpzm_backtest.py 经
grep 核实无同款守卫出口，零改动；cross_poll.py 零改动（只调用）。
未注册任何计划任务；未动 positions.json 与生产数据；未推真飞书。

## 一、告警链路图

```
【守卫拦截告警（任务A）】
strong_scan.py main()
  └─ freshness_guard() 未过（strong_scan.py:238 出口）
       └─ try/except 注记式调用 guard_alert.guard_alert()
            ├─ 内容四要素拼装：任务名 / 拦截原因 / 双数据锚 / 建议动作
            ├─ 键 = "guard_" + sha1(任务名|原因)[:16]
            │    （原因文本含锚日期，日期变了即新告警）
            └─ guard_alert.alert_once()
                 ├─ data/guard_alert_state.json 去重：同 key 12h 内只发 1 次
                 │    （原子写 tmp+os.replace；损坏/缺失 fail-open 照发）
                 ├─ runner.push_feishu(text) 真推（runner.py:46 通道）
                 └─ 推送失败只落 logs/guard_alert.log，不炸主流程，
                    不写状态（下轮拦截自动重试）

【cross 源缺失告警（任务B）】
rps_resonance.run_daily()（每日 16:00 批，start_rps_daily.ps1 启动）
  └─ qpzm_daily_batch 归档/回测之后，try/except 注记式：
       ├─ cross_poll.source_health() 四源三态巡检
       ├─ cross_poll.push_source_health_alert(h, send=False) 拼装文本
       │    （全源 ok → 文本为空 → 不告警，正确静默）
       ├─ 键 = "cross_src_" + 坏源清单（如 "9dim:missing,sig4:stale"）
       │    （状态变化即新键新告警；恢复后复发可见；前缀与守卫告警区分）
       └─ guard_alert.alert_once(key, text, send=True)
            → 同一去重状态文件 + 同一推送通道
       巡检自身异常只打 "SRC-HEALTH alert fail"，不影响日批 rc
```

## 二、挂载点清单

| # | 文件:位置 | 出口 | 调用 | 备份 |
|---|----------|------|------|------|
| 1 | strong_scan.py main() 拦截出口（原 `return 3` 前） | freshness_guard 未过 | guard_alert("StrongEod", msg, anchors={板块锚/个股锚}, suggest=核查 TdxSync+补数后 --force 重扫)，send=not a.dry（dry 不真推） | strong_scan.py.bak_20261014_alert |
| 2 | rps_resonance.py run_daily() 末尾（qpzm_daily_batch 块之后） | cross 每日批产出口（rps_latest.json 已落） | source_health 巡检 + alert_once 挂 cross_src_ 键 | rps_resonance.py.bak_20261014_alert |

守卫出口普查结论：仓库内 freshness_guard 仅 strong_scan.py 一处
（tdx_sync.py / qpzm_backtest.py grep "guard|return 3|exit 3" 零命中），
故任务A 仅一个挂载点。

## 三、去重与降级规则

· 窗口：同 key 12 小时内只发 1 次（guard_alert.DEDUP_WINDOW_H）；
· 状态：data/guard_alert_state.json，原子写；损坏/缺失 fail-open
  （视为空状态照发——宁重发不静默）；
· 推送失败：只落 logs/guard_alert.log，不写状态，下轮出口触发自动重试，
  任何异常不上抛（告警不能反噬守卫拦截与日批 rc）；
· dry 路径：send=False 只拼装文本，不推不写状态。

## 四、验证输出

1. 单测 `pytest test_guard_alert.py`：**4 passed**
   （去重窗口 11h 内跳过/13h 后重发；状态损坏 fail-open 照发；
   推送失败不炸不写状态；内容四要素齐全且 dry 零推送零落盘）。
2. 回归 `pytest test_cross_resilience.py`：13 passed（cross_poll 零改动
   无损）。
3. 四源健康现状（2026-10-14 15:19 dry 巡检）：

```
源健康: qpzm ok(2026-10-13) | sig4 ok(2026-10-13) | chanlun ok(2026-10-13)
        | 9dim ok(20261013) | 可用源 4/4
```

   全源 ok → dry 不告警（正确静默，未触碰 outbox）。
4. 合成坏源端到端（sig4 stale + 9dim missing，打桩推送+临时状态文件）：
   告警文本含各源锚日期与「可用源 2<3，k=3 共振不可达」；键
   `cross_src_9dim:missing,sig4:stale`；第一次发送=True、同键第二次
   去重跳过、真实推送恰 1 次。SYNTHETIC_E2E_OK。
5. strong_scan.py / rps_resonance.py / guard_alert.py 均过
   py_compile；freshness_guard 当前实盘为通过态，exit 3 路径由单测
   合成覆盖（不真跑全池扫描）。

## 五、回退方法

```bash
cd /e/老田的AI助手/quant_backtest
cp strong_scan.py.bak_20261014_alert strong_scan.py
cp rps_resonance.py.bak_20261014_alert rps_resonance.py
rm guard_alert.py test_guard_alert.py
rm -f data/guard_alert_state.json logs/guard_alert.log   # 可选：清告警痕迹
```

两份备份均为改前原样字节拷贝；无数据库/计划任务/positions.json 变更；
cross_poll.py 未改故无需回退。

## 六、遗留风险

1. **每日批挂载点唯一**：cross 源告警仅挂 rps_resonance 16:00 日批——
   若该批本身崩溃（如 RPS no data 提前 return），巡检同样不执行；
   run_daily 提前 return 的路径（closes 空）在巡检挂载点之前，属既有
   launcher alert_fail 覆盖范围（rc≠0 已有任务失败告警兜底）。
2. **cross_src_ 键粒度**：键=坏源组合，12h 内同一组合只发 1 次；
   若某源在窗口内反复抖动（ok↔stale 交替），最多每次状态翻转一条，
   不会风暴；但连续多日同组合卡 12h 窗口意味着每日最多 1-2 条，
   属预期降噪。
3. **freshness_guard 失败路径锚日期兜底**：板块锚无K线等极端路径 msg
   无日期，卡片锚行显示「见拦截原因」/「无」，仍满足四要素结构。
4. **守卫拦截告警依赖出口被执行**：strong_scan 若在守卫之前崩溃
   （如 import 失败），走的是 launcher alert_fail 路径而非本模块，
   两层告警互补、无重叠缺口。
