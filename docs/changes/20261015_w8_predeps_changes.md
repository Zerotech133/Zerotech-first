# W8 议题前置双落地：宽度口径并行对照 + HMM 影子列进周报（2026-10-15 04:15）

出处：W8 评审三议题决策材料"评审前置依赖"两条可先行项（议题一需
qpzm 一周并行对照、议题二需 HMM 影子列进周报），用户"下一步"触发。

## 一、宽度口径并行对照（议题一前置）

- 新增 `breadth_parallel.py`：日批尾采集 qpzm 自口径（rps_latest.json
  A股 env.breadth + latest 锚）vs 湖口径（qd_breadth_series 最新有效锚，
  跳 null）→ `data/breadth_parallel.jsonl`，同日幂等覆盖，
  tmp+os.replace 原子写；任一侧缺失照记 None 不炸；
- 挂载：qpzm_daily_batch.py 日批尾（gate_shadow/breadth-mon 同位，
  注记式 try/except，失败不影响产线）；
- **首行数据即见价值**：lake 32.64%（锚 20261013）vs qpzm 23.3%
  （锚 20261014），diff=9.34pp，`anchor_match:false`——两产物锚位
  错一天也如实记录（湖序列滞后一锚），评审时可判漂移 vs 背离。

## 二、HMM 影子列进周报（议题二前置）

- 新增 `hmm_shadow.py`：复用 hmm_gate_research 纯 numpy HMM 与全部
  判据（BIC 选 K、"标准化宽度均值最低=风险态"、迟滞 0.6/0.4），
  滚动窗 504 交易日重拟合（W8"滚动拟合攒影子"口径），输出当前
  filtered 风险态后验（无未来函数）与迟滞门状态；单次 8.6s，周五
  18:00 周报频率无压力；
- 接线：shadow_weekly_report.market_path_status() 内 try/except 调用，
  payload 增 `market_path.hmm`；render_market_path_md 市况节加一行
  "HMM 影子（研究口径，非交易信号）……两口径背离即 W8 议题二核心
  观察点"；失败降级为一行"不可用"；
- **今日影子读数**：K=3、风险态后验 99.47%、迟滞门 ON（宽度 32.64%
  处于研究口径风险态）——硬阈值"深度弱势"与 HMM"风险态"同向，
  无背离。

## 三、测试与回归

- 新增 test_breadth_parallel.py（4）+ test_hmm_shadow.py（3，合成
  两段式序列、null 锚、短序列拒拟合）；
- 首跑 3 失败全为测试自身预期错（null 跳过后锚位、4 位舍入容差、
  helper float(None)），修后 7/7 绿；
- 影子链路回归 34 passed（含 breadth-mon 8 + gate-shadow + metrics）；
- 周报 --json-only 实跑验出新列，payload/MD 双确认。

## 回退

- 删 qpzm_daily_batch.py 尾 breadth_parallel 挂载段即停对照采集
  （jsonl 保留可分析）；
- 删 shadow_weekly_report.py 两处 hmm 接线（market_path_status try
  块 + render 小行）即摘除 HMM 影子列。

## 复现

    "C:/Program Files/Python311/python.exe" breadth_parallel.py
    "C:/Program Files/Python311/python.exe" hmm_shadow.py
    "C:/Program Files/Python311/python.exe" shadow_weekly_report.py --json-only
