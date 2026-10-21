# WO-LEGION-S20 诸葛亮线落地：OpenClaw 安装 + GPT6.0 通道配置

| 项 | 内容 |
|---|---|
| 工单号 | WO-LEGION-S20 |
| 立单时间 | 2026-10-21 10:52 |
| 放行依据 | 老田 10:51 裁决「坚持最开始思路，走不同模板：诸葛亮=ChatGPT 6.0，田丰=智谱，赵云未来=DeepSeek」 |
| 归口 | 田丰席（主会话凭单直施） |

## 范围（IN）

1. OpenClaw 安装（npm 全局，托管 Node 22.22.2）+ doctor 自检
2. 诸葛亮 agent 骨架：OpenClaw 侧建 zhuge 配置（provider=openai，model=gpt-6.0），人格按《总定稿V2》§三（谋士人格：先求败因再论胜机，专提"你没想到的三件事"；零执行权，只有发言权与质询权）
3. 密钥位留空待老田注入（OPENAI_API_KEY 本机不存在——唯一外部依赖）

## 范围外（OUT）

- 不动田丰（tianfeng/glm 维持 10-18 拍板）
- 不动赵云 profile（DeepSeek 未来另立单）
- 不接 WhatsApp/Telegram 渠道、不装 daemon 常驻（骨架先行）
- 不改 agent_platform 生产代码（AI1 org 迁移待诸葛亮通道跑通后另立单）

## 闭合记录（2026-10-21 11:25，主会话凭单直施）

### 1. OpenClaw 安装 ✅
- **OpenClaw 2026.10.5**（1b58cb9）npm 全局安装，系统 Node 24.20.0（满足 ≥24.16 <25；托管 Node 22 不满足要求，已换系统 Node）
- 坑：默认安装拦构建脚本 → `npm config set allow-scripts=@google/genai,esbuild,koffi,protobufjs,openclaw --location=user` 后完成
- `gateway.mode=local` 已设；doctor 通过（reg.exe 探测被沙箱拦截不影响功能）
- CLI 可用性验证：version/doctor/config schema/agents list/agents add/set-identity 全部工作

### 2. 诸葛亮 agent（zhuge）✅
- `openclaw agents add zhuge --role reviewer --workspace C:/项目/老田的AI助手/legion_agents/zhuge/workspace`
- 身份已设：诸葛亮（Zhuge）🎯 careful, adversarial review（`agents set-identity --from-identity` 同步）
- workspace 四件套在位：SOUL.md（重写为军师人格：反向质询/pre-mortem/书面抗辩/零执行权铁律/与田丰异族隔离论证/与赵云出题-答题分工）+ IDENTITY.md + AGENTS.md + USER.md
- **模型绑定：`agents.entries.zhuge.model = {primary: "openai/gpt-6.0"}`**（config validate 通过）
  - 坑：agent 定义不在顶层 `agents.<id>`（Unrecognized key），在 `agents.entries.<id>`；auth.order 须 record 类型非 array——两处已修正
- auth profile `openai-zhuge`（provider=openai, mode=api_key）已预置——**key 值空缺，待老田注入 OPENAI_API_KEY**

### 3. 遗留（待老田）
- **OPENAI_API_KEY 未注入**（本机 env 仅有 GLM/LLM/ITOKEN key）——注入后 `openclaw agent --agent zhuge --local -m "测试"` 即可首跑
- 注入方式：环境变量 OPENAI_API_KEY，或 `openclaw configure` 交互式录入（走 openai-zhuge profile）
- 赵云 DeepSeek 通道：未来另立单（老田 10-51 裁决「未来可能会走」）
- AI1 org.json 迁移（9b→OpenClaw/GPT6.0 档）：待通道跑通验证后另立单
