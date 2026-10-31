# 七检解读：语法门拦截
> STATUS: unspecified（owner: TBD；2026-11-07 前补状态或标 superseded）



## 原始输出

```
语法门  ✗ 扫描 2893 / 错误 0 / 告警 1 WARN:quant_backtest/kg_build_p2.py:invalid escape sequence '\ '
```

## 逐项判读

| 检查 | 结果 | 判读/证据 |
|---|---|---|
| compileall | ok | 独立编译通过，但语法门更严格 |
| ports | ok | 与本失败无关 |
| secrets | ok | 与本失败无关 |
| names | ok | 与本失败无关 |
| capabilities | ok | 与本失败无关 |
| evals | ok | 与本失败无关 |
| tests | ok | 与本失败无关 |

## 结论

- 告警计为失败：3 处 invalid escape（\ 空格）导致 merge 拒绝；
- 修复：WO-SYNTAX-GATE 将 `\ ` 改为 `\\ `，复跑告警 0 后放行；
- 教训：门禁要求"错误 0 且告警 0"，不得关灯绕过。
