# SFT v4 LoRA 准备说明

本目录是清洗后的 SFT v4 数据与训练配置；**尚未训练**。

## 文件

```
train_chatml.jsonl       3,169 条
val_chatml.jsonl           394 条
holdout_chatml.jsonl       206 条
split_manifest.json       切分/哈希证据
token_stats.json          长度/代理 token 统计
training_config.yaml      LoRA 配置
```

## 当前机器限制

```
无 GPU
无 trl、datasets
Ollama qwen3.5:9b 是 GGUF，不是 HF 训练权重
```

所以：

```
本机只做数据/配置/评测；
训练必须到 GPU 或云端。
```

## base_model

已指向真实模型 ID：

```yaml
model:
  base_model: "Qwen/Qwen3.5-9B"
```

GPU/云端第一次运行会下载权重；本机只缓存了 tokenizer，没有权重。

## 依赖安装状态

已尝试创建独立 venv：

``` 
C:\项目\sft_env  (--system-site-packages)
```

但 `pip install trl datasets` 在本机网络/依赖解析阶段长时间卡住，未完成；当前环境还缺：

```
trl
datasets
```

需要在 GPU/云端重新安装。

## 训练环境

```bash
pip install "torch>=2.4" "transformers>=4.45" "peft>=0.13" \
            "accelerate>=1.0" "datasets>=3.0" "trl>=0.12"
```

建议：

```
1 × 24GB+ CUDA GPU（7B/8B QLoRA）
9B 建议 24GB+，更稳妥 40GB+
```

## 训练入口（示例，不在本机执行）

```bash
# 使用你选定的 SFT trainer；配置见 training_config.yaml
python train_sft.py --config training_config.yaml
```

## 禁止

```
不用 holdout 调参
不用训练集自评
不上线
不自动进影子盘
不自动交易
```

## 数据边界

- system 已统一；
- 按 source/concept 分组切分，组重叠 0；
- 1,224 条 meta 为 unknown；
- 版权/来源需内部确认后再用于对外模型。
