# tools/secrets —— L1 凭据保险库（SOPS/age 收编）

> 工单：`K-DATA-20261027-SECVAULT`（2026-10-27）· 级别：L1 安全
> 轮换与 Phase 2 操作单：`docs/ops/2026-10-27_凭据收编_SOPS_age与TushareMCP_token轮换_操作单.md`
> 结论先行：**本轮只增密文副本，不原地加密、不改消费方** → 生产零断线；
> Token 已从两处 `.mcp.json` 移出（占位符化），真实值只存于 `secrets.enc` 与本机渲染文件。

## 1. 目录与数据布局

| 路径 | 内容 | 是否入 git | 说明 |
|---|---|---|---|
| `C:\项目\_vault\secrets\` | 明文凭据（32 个文件，约 22.7 KB） | 否（repo 外） | **保持原样**，生产代码仍直读（Phase 1 零断线） |
| `C:\项目\_vault\secrets.enc\` | **本轮新增**：全部文件的 age 密文副本 + `MANIFEST.json` | 否（repo 外） | 32 个 `.age`，密文合计 29,635 B；`vault.py verify` 全绿 |
| `C:\项目\_vault\keys\age.key` | age 身份（私钥，ACL 已收紧） | 否（repo 外） | **丢失=密文不可解，必须离线备份** |
| `C:\项目\_vault\keys\age.recipients` | age 收件人公钥（可多行=多机/多人） | 否（repo 外） | 加密只需公钥，日常加密可不碰私钥 |
| `tools/secrets/*` | 保险库工具 + 测试 + 模板 | **是** | 不含任何凭据/密钥 |
| `.mcp.json.template` / `tools/secrets/templates/parent.mcp.json.template` | MCP 配置模板（`${TUSHARE_MCP_TOKEN}` 占位） | **是** | token 已移除 |
| `.mcp.local.json` | 本机渲染出的可用配置（含 token） | 否（`.gitignore` 已加） | 仅限本机，ACL 收紧，**勿提交/勿外发** |

保险垫：`.gitignore` 已加 `_vault/keys/`、`_vault/secrets/`、`_vault/secrets.enc/`、`.mcp.local.json`、`*.bak-20261027`、`*.age`，防 `git add -A` 误收。

## 2. 便携工具落地记录（GitHub Releases，不进 git：`bin/*.exe` 已被忽略）

| 工具 | 版本 | 路径 | 字节 | SHA256 |
|---|---|---|---|---|
| age | v1.3.2 | `C:\项目\老田的AI助手\bin\age.exe` | 7,172,096 | `2821a4ed191da07372acd302e5f6feae7a7985e285e1417765ebe74025af45f0` |
| age-keygen | v1.3.2 | `C:\项目\老田的AI助手\bin\age-keygen.exe` | 4,365,824 | `1549c7049be32695594bedd09bbd352a94b6013a9d5c43364f3c6cd7a09ab61c` |
| sops | 3.13.3 | `C:\项目\老田的AI助手\bin\sops.exe` | 53,497,856 | `a4a9a398858fe8b2ef72d9686d930bf7c5cece9be74ad83ac3b53cfdd70e6b1c` |

- 来源：
  - age `https://github.com/FiloSottile/age/releases/latest` → `v1.3.2/age-v1.3.2-windows-amd64.zip`（zip 内含 `age.exe`、`age-keygen.exe`）
  - sops `https://github.com/getsops/sops/releases/latest` → `v3.13.3/sops-v3.13.3.amd64.exe`
- 完整性：sops 已对照同 release 的 `sops-v3.13.3.checksums.txt` 校验 SHA256 **通过**；age 按下载后实算值记录（GitHub 未提供独立 checksums 资产，`*.proof` 为 minisign 证明，本机无 minisign，未验签）。
- 机器可读记录：`C:\项目\老田的AI助手\bin\SECVAULT-TOOLS.json`（同样未入 git）。
- 当前用途：**age 是 `vault.py` 的实际加密后端**；sops 已就位、本轮未接入（预留给 Phase 2 的 dotenv/JSON 级多收件人加密与 `sops exec-env` 编排，见操作单 Phase 2）。
- 若 age 未落地（降级路径）：`vault.py` 自动改用 `cryptography` 的 AES-256-GCM，密钥文件头 `# SECVAULT-AESGCM-v1`，密文 magic `SVLT1`；`vault.py init --backend aesgcm` 可显式选择。降级不影响命令面（init/encrypt/decrypt/exec-env/verify）。

### 升级方式（不依赖包管理器）

1. `GET https://api.github.com/repos/{FiloSottile/age|getsops/sops}/releases/latest` 取 `browser_download_url`；
2. 下载 windows-amd64 资产 → 解压/落盘覆盖 `C:\项目\老田的AI助手\bin\` 同名 exe；
3. 校验：sops 对照官方 `checksums.txt`；age 记录实算 SHA256（如启用 minisign 可验 `*.proof`）；
4. 跑 `python -m unittest discover -s tools/secrets/tests -v`（含 age 后端用例）；
5. 更新本节表格与 `bin/SECVAULT-TOOLS.json`（同一批操作，注明升级日期）。

## 3. 命令

```powershell
$py = C:\项目\py312\python.exe
$V  = tools/secrets/vault.py

& $py $V init                                   # 生成/复用 age 身份 + recipients（幂等；已有 key 跳过）
& $py $V encrypt C:\项目\_vault\secrets --recursive  # 加密副本 → _vault/secrets.enc（不删明文）
& $py $V decrypt cainiao\cainiao_mysql_root.pw.age   # 仅 stdout，不落盘
& $py $V exec-env ROTATION_FILLBACK.env.age -- <cmd...> # 解密 .env 注入子进程环境后执行（Phase 2 消费方切换用）
& $py $V verify --json                           # 按 MANIFEST 校验全部密文 sha256（rc 0/1）
& $py $V verify --plaintext                      # 额外核对明文源 sha256（明文仍在时做变更审计）
& $py $V scan                                    # 复用 tools/scan_secrets.py 扫 tracked 文件（rc≠0=新增未豁免命中）

& $py tools/secrets/render_mcp.py                # 从模板+token 密文渲染 .mcp.local.json（不打印 token）
& $py tools/secrets/render_mcp.py --check        # 校验模板/密文/渲染一致性 + 真实配置明文 token 命中数=0
```

- `encrypt` 默认跳过内容未变的文件（`--no-skip-unchanged` 强制重加密）；每次都会原子重写 `MANIFEST.json`。
- `MANIFEST.json` 字段：`path / sha256_plain / sha256_cipher / bytes / bytes_cipher / encrypted_at / tool_version / cipher`；顶层含 `schema / backend / generated_at / file_count / total_bytes_*`。
- 退出码：`0` 成功；`1` 校验/扫描失败（verify 篡改、scan 有新增命中）；`2` 使用错误或密钥/密文/MANIFEST 缺失（**fail-closed，绝不静默降级/回退明文**）。
- 环境变量：`SECVAULT_HOME`（默认 `C:\项目\_vault`）、`SECVAULT_KEY_FILE`、`SECVAULT_RECIPIENTS`、`SECVAULT_BIN`、`SECVAULT_AGE`/`SECVAULT_AGE_KEYGEN`、`SECVAULT_REPO`。

## 4. MCP token 收编

- 两处 `.mcp.json`（`C:\项目\老田的AI助手\.mcp.json`、`C:\项目\.mcp.json`）已替换为模板内容：`tushareMcp.url` 中 `token=${TUSHARE_MCP_TOKEN}`，其余结构/服务项不变；
- 原文件已备份为 `*.bak-20261027`（含明文 token，**仅本机保留，勿提交/勿外发**）；
- 真实 token 已作为 `_vault/secrets/mcp/tushare_mcp_token` 收进密文（`secrets.enc/mcp/tushare_mcp_token.age`）；
- **重启 MCP 相关服务前**：先 `render_mcp.py`，再把 `.mcp.local.json` 复制为 `.mcp.json`（或按 Phase 2 改用 resolver）；否则客户端读到的是占位符。轮换步骤见操作单。

## 5. 红线 / 故障处置

- 本目录代码、模板、文档**永远不得**出现真实 token/密码/私钥；`decrypt`/`exec-env` 不回显值；`render_mcp.py` 只输出路径与 masked 长度。
- `age.key` 丢失 → 现有 `secrets.enc` 永久不可解（age 无后门）。请立即离线备份（U 盘/密码管理器）；轮换新 key 后需 `init --force` 并重新 `encrypt` 全量。
- `secrets.enc` 可随时删除：明文仍在 `_vault/secrets`，产线不受影响（回滚见操作单）。
- 校验红：`verify` 报 `cipher-sha256-mismatch` = 密文被改动/损坏；`cipher-missing` = 密文被删或 MANIFEST 过期（重跑 `encrypt`）。
- 测试：`python -m unittest discover -s tools/secrets/tests -v`（22 用例：AES-GCM 兜底全链路 + age 后端往返 + fail-closed + CLI）。
