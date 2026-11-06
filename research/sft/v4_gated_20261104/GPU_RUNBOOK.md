# GPU / 云端 QLoRA 执行手册

> 本机不执行。仅用于租到 GPU 后的一次性训练。
> 数据门后的 train 2,902 / val 350 / holdout 206。

## 一、前置条件

```
GPU: >= 24GB 显存（4090 / A5000 / 云端同档）
OS: Linux 优先（Windows 上 bitsandbytes/QLoRA 容易踩坑）
模型: Qwen/Qwen3.5-9B HF 权重
```

## 二、安装

```bash
python -m venv sft_env
source sft_env/bin/activate
pip install -r requirements-sft.txt
```

## 三、下载权重

```bash
export HF_ENDPOINT=https://hf-mirror.com
huggingface-cli download Qwen/Qwen3.5-9B --local-dir ./models/Qwen3.5-9B
```

如用本地路径，把 `training_config.yaml` 的 `base_model` 改成该目录。

## 四、训练

```bash
python train_qlora.py --config training_config.yaml
```

预计：

```
2,902 条 train
3 epochs
单卡约 1~2 小时
输出: lora_out/final
```

## 五、训练后评测

```bash
python eval_sft_holdout.py \
  --model qwen3.5:9b \
  --limit 0 \
  --out holdout_eval_after.jsonl \
  --summary holdout_eval_after_summary.json
```

base 基线：

```
holdout_eval_local20_summary.json
```

## 六、验收

```
1. base vs SFT holdout 206 指标
2. 人工抽检 30~50 条
3. 引用/来源行为不退化
4. 时间敏感/答非所问不增加
5. pairwise 胜率 >=55% 才考虑保留
```

不达标：

```
不上线
不进影子盘
不继续加训
```

## 七、禁止

```
不用 holdout 调参
不用训练集自评
不自动部署
不自动交易
```

## 八、本机已准备好的文件

```
train_chatml.jsonl
val_chatml.jsonl
holdout_chatml.jsonl（冻结）
token_stats_qwen.json
training_config.yaml
train_qlora.py（参考实现，需按 trl 版本微调）
```

## 九、风险

- Qwen3.5-9B 是 image-text-to-text 模型，训练脚本按 text-only SFT 处理；若模型结构特殊，需先确认 processor/forward 兼容性。
- `train_qlora.py` 是参考实现，未在本机执行；GPU 环境需先跑 1 step smoke test。
- 数据仍有 unknown meta；训练前已过滤 time_sensitive/short_answer/A概念操作化答案。
