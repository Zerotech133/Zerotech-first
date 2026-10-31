# Docker Desktop 商业化评估 · 底层研究报告（K-STOCK-050）
> STATUS: unspecified（owner: TBD；2026-11-07 前补状态或标 superseded）



> 2026-10-09 23:05 · 老田令：「菜鸟所有代码/数据库/历史数据，未来商业化，Docker Desktop 是不是最优解？研究底层」
> 结论先行：**开发机可以继续用，商业化部署不靠它；真正要紧的是把数据资产从它的虚拟盘里救出来。**

---

## 一、底层架构事实（它到底是什么）

### 1.1 Docker Desktop ≠ Docker

```
您的 Windows 宿主（当前实跑 Docker Desktop 29.2.1）
  │
  ├─ docker CLI（Windows 进程）
  │
  ▼  WSL2 轻量 Linux 虚拟机（docker-desktop）
  │
  ├─ dockerd 守护进程（Linux）
  ├─ 19 个容器全住这里（nginx/MySQL/PG×3/Neo4j×2/Redis×2/quantdinger 全家桶…）
  └─ 全部命名卷 = ext4.vhdx 虚拟磁盘文件（宿主资源管理器里看不见内容）
```

- 容器**不是**原生跑在 Windows 上——中间隔着一整层 WSL2 VM
- 跨文件系统 bind mount（如 `E:\老田的AI助手\portal → ai-portal`）走 VM 通道协议，I/O 密集场景比 Linux 原生慢一个量级；命名卷在 VM 内 ext4，性能好但**数据被封进 vhdx**
- vhdx **只增不减**：删容器/卷不自动还空间给 Windows，要手工 compact

### 1.2 官方定位（原文）

- 「Docker Desktop **not supported on Windows Server**（2019/2022）」——服务器产品明确不支持
- 官方口径：Desktop 是 **developer workstation** 工具；生产环境 = Linux 服务器跑 Docker Engine（免费开源，Apache 2.0 / Moby 项目）

---

## 二、许可证事实（商业化直接相关，2026-08-26 最新 DSSA 已核）

| 事项 | 事实 |
|---|---|
| 免费条件 | 员工 <250 人 **且** 年营收 <$10M（**两条件须同时满足**，超任一即收费） |
| 超线价格 | Team $15/人/月、Business $24/人/月，按**实际使用 Desktop 的开发者**计 seat |
| 算例 | 10 开发者超线 ≈ $1,800/年；30 人 ≈ $5,400-8,640/年；100 人 ≈ $18,000-28,800/年 |
| 关键区别 | 收费的只是 Desktop 这个封装；**Docker Engine（Moby）Apache 2.0 永久免费**，无任何 seat 概念 |
| 现状合规 | 您当前个人/小团队使用：免费、合法、不用动 |

**注意 2026 新趋势**：DSSA 把「User」定义扩到了「or AI Agent for AI Features」——AI Agent 用 Docker 服务的计费边界在收紧，商业化后更要和 Desktop 解耦。

---

## 三、结合本项目的实测诊断（严重度编码）

### 🔴 P0 · 数据资产单点风险（比许可证紧迫得多）

实测 19 容器在跑，**菜鸟 MySQL（cainiao-mysql-local）、知识图谱 Neo4j（neo4j-kg-data）、新量化 PG（newquantsystem_v2_postgres_data_v2）、智策 PG（zhice-os-v2-pgdata）、quantdinger PG/Redis** 等核心数据库全部用**命名卷**——数据物理上躺在 `ext4.vhdx` 虚拟磁盘里：

- 宿主备份脚本备不到它（不在 NTFS 文件系统里）
- WSL2 虚拟盘损坏 / Docker Desktop 升级事故 / vhdx 文件被误删 ⇒ **菜鸟数据库、历史数据、图谱全灭**
- 迁移时必须走 `docker run --volumes-from` 或 dump 通道，路径绕

**已踩过的坑佐证 Desktop 脆弱性**：nginx.conf 注释原文——「Docker Desktop 升级后宿主机网关 IP 变化导致全部探测失效」，为此专门加了 resolver 动态解析。升级脆弱性不是理论，发生过了。

### 🟡 P1 · 单机承担了生产职责

8080 门户（nginx 容器）+ 菜鸟认证库 + 图谱库都在这台开发机上，7×24 跑批依赖它——开发机工具承载了生产负载，Desktop 重启/升级=全站闪断。

### 🟢 无碍 · 当前许可

<250 人且 <$10M，免费合规；商业化初期到Linux 服务器部署后更与 Desktop 无关。

---

## 四、答案：是不是最优解？

**分三层，不同答案：**

| 层 | Docker Desktop 是不是最优 | 应该是什么 |
|---|---|---|
| 开发机（这台 Windows） | 🟡 够用非最优 | 可继续用（免费期）；替代=WSL2 内直装 docker-ce（免费无 Desktop）或 Rancher Desktop/Podman Desktop，但收益有限、非急务 |
| **商业化部署（关键）** | 🔴 **不是，且官方明确不支持服务器** | **Linux 服务器 + Docker Engine（免费）+ docker compose**。镜像开发机构建 → 推 registry → 服务器只装 Engine。零许可费、无 VM 性能损耗、与客户环境一致 |
| 交付给客户 | ❌ 不能要求客户装 Desktop | 交付物 = compose 文件/镜像包 + 部署文档；客户 Linux 机器 `docker compose up` 即起 |

**您的独特优势**：ARM 服务器公司 + 国产算力产业链 ⇒ 产品天然可以做成「**自家 ARM 服务器整机 + Docker Engine（ARM64 原生）+ 菜鸟 OS 镜像包**」的一体机交付形态——客户开机即用、您零许可成本、软硬一体正好对上「能力平台」定位。Docker Engine 对 ARM64 是一等公民支持。

---

## 五、行动路线（三步）

### 第一步（本周，P0 救数据）
1. **三库 dump 脚本**：`mysqldump`(菜鸟库) + `pg_dump`×3(新量化/智策/quantdinger) + Neo4j `neo4j-admin dump`，输出到宿主 `E:\老田的AI助手\backups\db\`（NTFS 可见处）
2. 挂进现有每日备份 automation（backup_daily 加一段）
3. 卷清单建档：19 容器 ↔ 命名卷 ↔ 用途对照表落 docs

### 第二步（商业化启动时）
1. 一台 Linux 机器（云 VPS 或自家 ARM 服务器）装 Docker Engine
2. 现有 compose 文件直接搬过去试跑（compose 天然跨环境，改 host 映射即可）
3. 8080 门户同法迁移，Desktop 只留开发用途

### 第三步（产品化）
1. 菜鸟 OS 做成标准交付物：`docker compose` 一键起全栈 + ARM64 镜像构建（buildx 多架构）
2. 一体机/私有化部署两条交付线；SaaS 线则自选云上 K8s
3. 开发机 Desktop 到超线前夕再决定去留（换 WSL2+docker-ce 半天工作量）

---

## 六、一句话回答

> **Docker Desktop 是开发工具不是生产方案，商业化部署的答案是 Linux + Docker Engine（免费）；而您现在最该担心的不是许可证，是菜鸟数据库和历史数据正躺在 WSL2 虚拟磁盘这个单点里——本周把它们 dump 出来落到宿主备份，这一步最值钱。**

（附：wsl.exe 在本会话沙箱被安全策略拦截，WSL 发行版清单未直接核对；不影响上述结论——容器/卷/版本均经 docker API 实测。）
