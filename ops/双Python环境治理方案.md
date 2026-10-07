# 双 Python 环境治理方案

- **日期**：2026-10-02
- **背景**：整改报告后续建议第 2 条（card-handler 修复中发现的「双解释器装包错位」判例）
- **目标**：产线运行时统一为单一解释器，裸 `python` 调用点全部显式化，杜绝「装了包还报未装」类事故
- **状态**：**已执行完毕（2026-10-02 21:22）**——阶段 2/3/4 全部落地并验证，终态见文末「执行结果」；阶段 5（3.12 处置）进入观察期

---

## 一、现状摸底（2026-10-02 实测）

### 1.1 本机共 4 套 Python，其中 2 套产线相关

| 解释器 | 版本 | 角色 | 活进程 |
|---|---|---|---|
| `C:\Program Files\Python311\python.exe` | 3.11.9 | **产线惯例**（计划任务与 start_*.ps1 显式引用） | 8 个 |
| `C:\Users\Administrator\AppData\Local\Programs\Python\Python312\python.exe` | 3.12.10 | 用户安装，**PATH 第一优先**——裸 `python` 的实际解析目标 | 2 个（Jev 组件） |
| `E:\zhice-os-v2-windows-20260920\code\.venv\` | — | 智策 OS v2 专用 venv（自带 $Py 变量，已隔离） | 2 个，**不受影响** |
| `C:\Users\Administrator\.workbuddy\binaries\python\` | 3.13.12 | WorkBuddy 自带，**不受影响** | 2 个，**不受影响** |

### 1.2 裸 `python` 调用点（治理对象，共 6 处）

| # | 位置 | 说明 |
|---|---|---|
| 1 | zhice.ps1 L63 `Start-Process python`（jev_gate） | Jev 网关 |
| 2 | zhice.ps1 L67 `Start-Process python`（signal_review_gate） | 评审门 |
| 3 | zhice.ps1 L71 `Start-Process python`（jev_qd_bridge） | QD 桥接 |
| 4 | zhice.ps1 L75 `Start-Process python`（lark_card_handler） | 卡片回调 |
| 5 | rotate_tushare_token.ps1 L120 `Start-Process python`（signal_review_gate 重启） | token 轮换时的重启通路（L100 文档示例同款） |
| 6 | 计划任务 **「AI盘前分析」** Execute=`python`（裸命令） | 53 任务中唯一裸引用——此前未被发现的隐藏雷 |

### 1.3 已规范部分（不动）

- 计划任务 MorningReport、PG-Robot：显式 3.11 ✓
- 全部 start_*.ps1（news_collector×6 / portal / gzh / quant_backtest×4）：显式 3.11 ✓

## 二、治理方向：统一到 3.11

理由四条：
1. **既成事实**：计划任务层 + 十几个 start_*.ps1 已显式 3.11，反向统一到 3.12 改动面大十倍；
2. **系统级安装**：3.11 在 `C:\Program Files`，任何账户上下文可用；3.12 是用户级（Administrator 个人），任务上下文依赖用户环境不可靠；
3. **依赖零障碍**（已实测）：3.11 上 psycopg2 / prometheus_client / requests 全部就绪，Jev 四件套顶层 import 全为标准库 + jev_common；
4. **运维惯例**：README/文档/排障速查全部以 3.11 为 `$PY` 口径。

## 三、具体步骤

### 阶段 1：摸底与依赖核验 ✅（已完成，本文件第一节 + 3.11 三包实测 True）

### 阶段 2：调用点显式化（改 3 个文件 + 1 个任务，约 10 分钟）

**2.1 zhice.ps1 —— 定义产线变量，4 处替换**

在 `$Py = "$Code\.venv\Scripts\python.exe"`（L10，智策 venv 专用，不动）附近新增：

```powershell
$JevPy = "C:\Program Files\Python311\python.exe"   # Jev 四件套产线解释器（双 Python 治理判例）
```

L63 / L67 / L71 / L75 四处 `Start-Process python` → `Start-Process $JevPy`。

**2.2 rotate_tushare_token.ps1 —— 2 处**

L120 `Start-Process python -ArgumentList "$PSScriptRoot\jev-eval\signal_review_gate.py",'--loop'` → 同款 `$JevPy`（该脚本顶部需同样定义变量）；L100 文档 here-string 内示例同步改字面量，保持文档与代码一致（判例：文档与代码同步防漂移）。

**2.3 计划任务「AI盘前分析」—— Execute 显式化**

```powershell
$t = Get-ScheduledTask -TaskName 'AI盘前分析'
"改前: $($t.Actions[0].Execute) $($t.Actions[0].Arguments)"   # 留证
$t.Actions[0].Execute = 'C:\Program Files\Python311\python.exe'
$t | Set-ScheduledTask
"改后: $($t.Actions[0].Execute) $($t.Actions[0].Arguments)"   # 确认 Argument 未丢失
```

### 阶段 3：切换与验证（Jev 四件套 3.12 → 3.11，约 10 分钟）

1. **前置**：确认无进行中的 Jev 评审/卡片操作（非交易时段最稳）；
2. 杀掉 3.12 上的 Jev 进程（按 CommandLine 匹配 jev_gate / signal_review_gate / jev_qd_bridge / lark_card_handler）；
3. 跑 `powershell -File zhice.ps1 watchdog` → 新路径拉起四件套；
4. **验证三环**：
   - 四件套进程 ExecutablePath 全部 = 3.11；
   - 日志推进正常（card: 「事件监听就绪」+「指标端点已启动 port=9104」）；
   - `ops/health_check.py` 全绿 rc=0（五服务 + 53 任务 + 数据链）。

**回退**：zhice.ps1 的 `$JevPy` 变量改回字面量 `python` 一行即回滚（这就是用变量而非散改 4 处的原因）。

### 阶段 4：护栏固化（约 10 分钟）

1. **README §六 排障速查第 7 条改写**：从「两边都装（治标）」升级为「产线唯一解释器=3.11，产线依赖只装 3.11；3.12 仅限个人交互实验」；
2. **README §四 目录地图或新增「部署约定」行**：新脚本/新任务一律显式 `C:\Program Files\Python311\python.exe`，禁止裸 `python`；
3. **（可选）health_check 增强**：巡检 Jev 四件套进程 ExecutablePath，非 3.11 输出 warn——防止未来某次改动把裸 python 再带回来。

### 阶段 5：3.12 处置（观察期，无动作）

- 保留 3.12 安装（用户手动交互/实验用），但阶段 2-4 完成后产线已无任何引用；
- 观察一个月无异常后，可择期卸载或永久保留——**不作为本方案必做项**。

## 四、风险评估

| 风险 | 概率 | 缓解 |
|---|---|---|
| 3.11 缺 Jev 依赖 | 已排除 | 三包实测 True；四件套全标准库 |
| 切换期 Jev 服务中断 | 分钟级 | 选非交易时段；watchdog 5 分钟自愈兜底；回退一行 |
| 任务 Action 改坏 | 低 | 改前留证 + 改后核对 Argument；Set-ScheduledTask 用对象属性法保留其余字段 |
| _TAT_CACHE 进程缓存失效 | 无影响 | 新进程重新取 token，7200s 有效期充裕 |

## 五、总工作量与收益

- **改动面**：2 个 ps1（6 处）+ 1 个计划任务 + README 2 处，合计约 30 分钟（含验证）；
- **收益**：产线单解释器，「装包错位」类故障（本轮 card-handler 第三层根因）从结构上消除；巡检口径统一（所有 python 进程一眼可判归属）；
- **归档**：执行完成后写 INTEL_RD_PLAN.md K35-s。

---

## 六、执行结果（2026-10-02 21:22 补记）

| 阶段 | 结果 |
|---|---|
| 阶段 2 调用点显式化 | ✅ zhice.ps1 实改 **6 处**（方案 6+执行中发现 L42 vite 缓存清理 1 处）；rotate 3 处；「AI盘前分析」Execute 已显式化，Argument `-X utf8 premarket.py --push` 完整保留（对象属性法），premarket.py 3.11 编译+依赖核验过 |
| 阶段 3 切换验证 | ✅ **零手工切换**——watchdog 计划任务 20:53 轮自动完成四件套换血（card-handler 36568@3.12 → 51512@3.11），3.12 无 Jev 残留 |
| 阶段 4 护栏固化 | ✅ README 部署约定 + 排障第 7 条升级；health_check 新增第 6 服务项「Jev解释器(3.11)」巡检 |
| 阶段 5 3.12 处置 | 观察期（保留作个人实验，产线已无引用） |

**执行中事故与修复（新增判例）**：编辑写回剥离 .ps1 的 UTF-8 BOM → PowerShell 5.1 按 GBK 解析出满屏 ParserError、ZhiceWatchdog rc=1 被 health_check 抓到 → python 三行补 BOM 修复，watchdog rc=0、Last Result 归零。判例：凡编辑带 BOM 的 .ps1 必须验 BOM；ps1 改动验证闭环=语法解析+干跑+Last Result 三查。

**终态**：health_check 六服务全绿 rc=0（门户/nginx/前端/QD桥/卡片回调/**Jev解释器(3.11)**），四件套全 3.11，53 计划任务异常 0。
