# _template 策略包模板（规格 v1）

复制本模板 = 加一个新策略的开始。三步：

## 第 1 步：复制改名
把 `_template/` 整个目录复制为 `strategies/<你的策略名>/`，
全局替换 `_template` → 你的策略名（signal.py、policy.json、
MANIFEST.json 三处）。

## 第 2 步：填三处实现
1. `signal.py`：填 `load_features`（按 feeder.yaml 注入装载数据）
   和 `signals`（特征+policy → 信号集）。**纯函数**：无 IO、
   无全局态、无时钟、无随机数；
2. `policy.json`：替换 `signals`/`thresholds` 为本策略实际参数；
3. `feeder.yaml`：改 `source` 与 `fields` 为本策略实际依赖
   （策略不自取数）。

## 第 3 步：最小测试 + 冻结
- `pytest tests/ -q` 通过（输出契约 + 判据边界）；
- 影子期启动首日：填 `pre_registration`（promote_rule/eval_days/
  source），生成 `config_sha256` 并注册进 registry——此后 policy
  只读，改判据=升版重新冻结（规格 §2 冻结三规则）；
- MANIFEST.status 保持 `dev`，影子批启动时改 `shadow` 并在
  gate_history 记 shadow_start。

之后的日子交给底座 SOP 七步（影子期→预注册→落数→机械判定→拍板
→上线→进化接管），见《策略包规格v1_2026-10-15.md》§6。
底座零改动。
