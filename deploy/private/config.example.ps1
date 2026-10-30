# 零界个股分析 · 客户配置模板（复制为 config.ps1 后填写，勿提交到版本库）
# activate.ps1 会读取本文件中的变量

# 安装目录（install.ps1 创建）
$env:ZB_INSTALL_DIR = "$env:LOCALAPPDATA\ZeroBoundStockAnalysis"

# 租户/客户标识（与 license 中的 tenant 一致）
$env:ZB_TENANT = "客户ID"

# 数据目录（客户自备数据：curated parquet / 自有适配器输出目录）
$env:LOCAL_DATA_DIR = "$env:ZB_INSTALL_DIR\data"

# 工作目录（缓存/运行账/队列/DB）
$env:ANALYSIS_WORK = "$env:ZB_INSTALL_DIR\work"

# 商业数据根（license/租户/配额/审计，默认在 work 下）
$env:COMMERCIAL_HOME = "$env:ZB_INSTALL_DIR\work\commercial"

# license（安装时已复制到安装目录；也可指向其他路径）
$env:COMMERCIAL_LICENSE_KEY_FILE = "$env:ZB_INSTALL_DIR\license.key"

# ⚠️ 校验密钥：由产品方在交付时提供（不要提交到版本库/聊天）
# $env:COMMERCIAL_LICENSE_SECRET = "<由产品方提供>"

# 可选：LLM 通道（不配置则用 --no-llm 纯规则报告）
# $env:LLM_BASE_URL = "http://127.0.0.1:11434/v1"
# $env:LLM_MODEL = "qwen3.5:9b"

# 可选：飞书推送（企业版）
# $env:FEISHU_BOT_WEBHOOK = ""
