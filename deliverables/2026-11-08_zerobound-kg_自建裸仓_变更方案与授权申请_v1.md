# zerobound-kg 自建裸仓 · 变更方案与授权申请

- 日期：2026-11-08 23:00
- 目标服务器：`10.205.0.118`（生产，CentOS 7.4 / git 1.8.3.1）
- 变更性质：**纯新增 + 两处追加**，不改存量逻辑
- 当前状态：**待老田批准，未执行任何写操作**

---

## 0. 为什么需要这件事

`zerobound-kg` 的 remote 是 `https://github.com/laotian/zerobound-kg.git`，实测
`Repository not found`（占位地址，从未存在）。本机无 `gh` CLI，无法命令行建仓。
今晚已把 S12 全部实现入库（`f6926ef`，56 文件），但**只落在本地 git 历史**，
能防误删、防 `git clean`，**不防磁盘损坏**（全机仅 C 盘，无外挂盘）。

已做的临时缓解：`C:\项目\_git_bundles\zerobound-kg-20261108.bundle`（6.5 MB /
353 提交 / 3 分支 / `bundle verify` = complete history）。

---

## 1. 现状核对结果（只读探测，2026-11-08 22:58）

| 项 | 现状 |
|---|---|
| 裸仓目录 | `/home/git/repos/`，owner `git:git` |
| 已有仓 | `qengine.git` 1.3M、`qengine-private.git` 5.0M、`qengine-web.git` 81M、`qresearch.git` 632K |
| 授权文件 | `/home/git/.ssh/authorized_keys`，**4 行**，608 字节 |
| 授权模式 | `restrict,command="/home/git/bin/git-ssh <仓名> rw" ssh-ed25519 <key>` |
| `git-ssh` 能力 | 支持**逗号分隔多仓**；`ro`/`rw` 两档；`pwd -P` 防 symlink 逃逸 |
| 备份脚本 | `/usr/local/bin/qgit-backup.sh`，`REPOS="qengine qengine-private qresearch"` |
| 备份策略 | `git bundle --all`，KEEP=30，轮转 glob `$DEST/$r-2*.bundle` |
| 备份调度 | `qgit-backup.timer`，每天 03:40，`User=git` |
| SELinux | `unconfined_u:object_r:user_home_t:s0`（不需 httpd 类型，除非做匿名只读） |

### 探测中发现的两个问题

**① `qengine-web.git`（81 MB）不在备份范围内。**
`REPOS` 只写死了三个仓，2026-11-08 07:55 新建的 `qengine-web.git` 从未进入
每日 bundle。`/home/git/backups/` 里只有 qengine / qengine-private / qresearch
的 bundle，没有 web 的。这是既有漏洞，与本次变更同属一行改动，建议一并修。

**② 裸仓默认 HEAD 是 `refs/heads/master`，而 zerobound-kg 本地分支是 `main`。**
git 1.8.3.1 不支持 `git init -b`，创建后必须显式
`git symbolic-ref HEAD refs/heads/main`，否则 clone 出来是空 master。

---

## 2. 五要素

### 2.1 命令全文

**阶段 A · 本机（零风险，不碰服务器）**

```bash
# A1 生成专钥（一把钥匙一个仓，沿用现有隔离模式）
ssh-keygen -t ed25519 -N '' -C 'zerobound-kg@tianfeng-local' -f ~/.ssh/qgit_kg

# A2 ~/.ssh/config 追加
cat >> ~/.ssh/config <<'EOF'

# 第五个仓：知识图谱引擎（含 S12 因子原型实现，只推不放——无匿名只读镜像）
Host qgit-kg
    HostName 10.205.0.118
    Port 22
    User git
    IdentityFile ~/.ssh/qgit_kg
    IdentitiesOnly yes
EOF
```

**阶段 B · 服务器（需批准）**

```bash
# B0 变更前备份（两处追加的前提）
cp -p /home/git/.ssh/authorized_keys /home/git/.ssh/authorized_keys.bak-20261108
cp -p /usr/local/bin/qgit-backup.sh   /usr/local/bin/qgit-backup.sh.bak-20261108

# B1 建裸仓
sudo -u git git init --bare /home/git/repos/zerobound-kg.git
# B2 修正 HEAD 指向 main（git 1.8.3.1 无 init -b）
sudo -u git git --git-dir=/home/git/repos/zerobound-kg.git symbolic-ref HEAD refs/heads/main
# B3 权限对齐现有仓（qresearch.git 为 775）
chmod 775 /home/git/repos/zerobound-kg.git
chown -R git:git /home/git/repos/zerobound-kg.git

# B4 授权：追加第 5 行（只追加，不改前 4 行）
#     新公钥正文由阶段 A 生成，此处以 <NEW_PUBKEY> 占位
echo 'restrict,command="/home/git/bin/git-ssh zerobound-kg.git rw" <NEW_PUBKEY> zerobound-kg@tianfeng-local' \
  >> /home/git/.ssh/authorized_keys

# B5 备份脚本纳入新仓（同时补上遗漏的 qengine-web）
#     精确替换，替换前后断言 count
sed -i 's|^REPOS="qengine qengine-private qresearch"$|REPOS="qengine qengine-private qresearch qengine-web zerobound-kg"|' \
  /usr/local/bin/qgit-backup.sh
```

**阶段 C · 本机推送**

```bash
cd "C:/项目/老田的AI助手/zerobound-kg"
git remote set-url origin qgit-kg:zerobound-kg.git
git push -u origin main
```

**阶段 D · 验证**

```bash
# D1 远端与本地 HEAD 一致
ssh qgit-kg 'git --git-dir=/home/git/repos/zerobound-kg.git rev-parse HEAD'
git rev-parse HEAD

# D2 手动跑一次备份，确认新仓进了 bundle
sudo -u git /usr/local/bin/qgit-backup.sh
ls -la /home/git/backups/ | grep -E 'zerobound-kg|qengine-web'

# D3 从 bundle 恢复到临时目录，验证可完整恢复
git clone /home/git/backups/zerobound-kg-<TS>.bundle /tmp/kg_restore_test
cd /tmp/kg_restore_test && git log --oneline -3 && rm -rf /tmp/kg_restore_test
```

### 2.2 影响面

| 对象 | 影响 |
|---|---|
| `/home/git/repos/` | 新增一个裸仓，约 50 MB（`/home` 2 TB，当前余量充足） |
| `authorized_keys` | 4 行 → 5 行，**前 4 行不动** |
| `qgit-backup.sh` | `REPOS` 3 → 5 个仓，每日备份体积 +约 88 MB（web 81M + kg 7M） |
| 每日备份耗时 | 增加约 3~5 秒（03:40 窗口，无业务冲突） |
| **不受影响** | 4 个现有仓的推送/拉取、nginx 静态镜像、zeroq 平台、PG/SQLite 备份、业务服务 |
| 不开放匿名只读 | 不做 `post-receive` 镜像，`/var/www/git` 不变（含策略逻辑，不外露） |

### 2.3 不可逆点

| 操作 | 可逆性 | 说明 |
|---|---|---|
| 建裸仓 | 可逆 | `mv` 到 `/home/git/_trash_20261108/` 即可 |
| authorized_keys 追加 | 可逆 | 有 `.bak-20261108`；**但改错是本方案最大风险** —— 可能导致 git 用户整体无法 SSH |
| qgit-backup.sh 改 REPOS | 可逆 | 有 `.bak-20261108`；`set -euo pipefail` 会在失败时非 0 退出，不会静默 |
| 本机 remote 改 URL | 可逆 | `set-url` 改回即可 |
| **真正不可逆** | **无** | 全部为新增或追加，无删除、无覆盖存量 |

### 2.4 替代方案

| 方案 | 为什么不选 |
|---|---|
| GitHub 私有仓 | 数据出境到第三方；且入库文件含策略逻辑，硬编码凭据扫描曾被审批拦截、尚未人工复核 |
| 维持本地 + bundle | 只能防误删，不防盘坏（本机仅 C 盘） |
| 只建仓、不改备份脚本 | 少改一处，但新仓不在自动备份里，等于异地保存只做了一半 |

### 2.5 回滚预案

```bash
# 若 authorized_keys 出问题（最紧急）
cp -p /home/git/.ssh/authorized_keys.bak-20261108 /home/git/.ssh/authorized_keys
sshd -t                       # 语法自检
# 现有 4 个仓立刻恢复可访问

# 若备份脚本出问题
cp -p /usr/local/bin/qgit-backup.sh.bak-20261108 /usr/local/bin/qgit-backup.sh
sudo -u git /usr/local/bin/qgit-backup.sh    # 立刻验证恢复

# 若裸仓要撤
mkdir -p /home/git/_trash_20261108
mv /home/git/repos/zerobound-kg.git /home/git/_trash_20261108/

# 本机兜底（与服务器无关，始终有效）
# C:\项目\_git_bundles\zerobound-kg-20261108.bundle  353 提交完整历史
```

**回滚触发条件**：阶段 B4 之后任一次 `ssh qgit-open` / `qgit-private` 失败，
立即执行 authorized_keys 回滚，不排查、不犹豫。

---

## 3. 执行顺序与检查点

```
A1 生钥 → A2 配别名（本机，无风险）
   ↓
B0 备份两文件 → B1 建仓 → B2 修 HEAD → B3 权限
   ↓
B4 追加授权  ← ⚠ 最高风险点，改完立刻测 4 个现有仓
   ↓
检查点①：ssh qgit-open / qgit-private / qgit-research / qgit-web 四个别名全部可 ls-remote
   ↓
B5 改备份脚本 → 检查点②：bash -n 语法检查 + 手动跑一次
   ↓
C 推送 → 检查点③：远端 HEAD == 本地 HEAD
   ↓
D3 bundle 恢复演练 → 检查点④：恢复出的仓库 log 正常
```

任一检查点失败即停，按 2.5 回滚，不继续下一步。

---

## 4. 本次**不做**的事

- 不开匿名只读 HTTP 镜像（`/var/www/git` 不动）
- 不装 Gitea
- 不碰 nginx、firewalld、SELinux 策略
- 不碰 `qengine*` / `qresearch` 四个仓的任何内容
- 不重启任何服务（`authorized_keys` 改动对 sshd 即时生效，无需 reload）
- 不动 `zeroq-backup.timer`（那个只备 PG + SQLite，与本次无关）

---

## 5. 授权确认

- [ ] 批准，按 2.1 全部阶段执行
- [ ] 批准，但**不改备份脚本**（只建仓 + 推送，web 遗漏问题另案处理）
- [ ] 不批准

签字：__________ 日期：__________

---

## 6. 执行回执（2026-11-08 23:22 完成）

老田批准：**「批准，按方案全部执行」**（2026-11-08 23:15 前后）。

| 阶段 | 实测结果 |
|---|---|
| A1 生钥 | `~/.ssh/qgit_kg`，指纹 `SHA256:O8Y4T3u5vhRlHAoPKESPW9T4rhSM8/uFAFLd3fCZAkk`（ED25519） |
| A2 别名 | `~/.ssh/config` 增 `Host qgit-kg`，现共 5 个别名，一钥一仓 |
| B0 备份 | `authorized_keys.bak-20261108-kg`、`qgit-backup.sh.bak-20261108-kg` |
| B1~B3 建仓 | `zerobound-kg.git` 创建成功，`HEAD -> refs/heads/main`，权限 `drwxrwxr-x git:git` |
| B4 授权 | 4 → 5 行；**前 4 行 md5 `43e4a75ea66223ab69ba791766fb4f33` 与备份逐字节一致（ASSERT OK）**；`sshd -t OK` |
| 检查点① | qgit-open 4 refs / qgit-private 2 / qgit-research 2 / qgit-web 2 —— **四个现有仓全部正常**；qgit-kg 连接 rc=0 |
| B5 备份脚本 | `REPOS` 3 → 5 仓，`diff` 仅 1 行，`bash -n OK`，旧值断言消失 |
| C 推送 | `* [new branch] main -> main`，`origin/main` 已建立跟踪 |
| D1 一致性 | 本地 == 远端 == `a86c487c473c1dce2c520d9e599f6ead89741405` |
| D2 备份验证 | 手动跑 6 秒，退出码 0；5 个仓全部出 bundle（kg 6.5M / web 13M / private 848K / open 480K / research 448K）；heads 指纹中 `zerobound-kg a86c487…` 与远端 HEAD 一致 |
| D3 恢复演练 | 从 bundle 克隆出 **353 个提交**，HEAD `a86c487`；`select_asof.py` 212 行 / `rerun_asof_when_ready.py` 344 行 / `APPROVAL_REQUEST_20261108.md` 74 行（含 2 处「补签声明」）/ `.gitignore` 37 行全部在位；`daily_delta.parquet` 与 `_rerun_asof.log` 正确排除 |

### 顺带修复的既有问题

`qengine-web.git`（81 MB，2026-11-08 07:55 建）此前**从未进入每日备份** ——
`REPOS` 写死三个仓导致遗漏。本次一并补入，D2 已确认其 bundle（13 MB）正常生成。

### 本次未做（有意为之）

- **task 分支未推**：本地另有 `task/K-INTEL-20261018-KG-SOURCES`、
  `task/K-KG-20261020-WMRETRY` 两个分支未推。内容是 10 月旧任务，已并入 main。
  若需全量镜像可补 `git push origin --all`。
- **未开匿名只读镜像**：不做 `post-receive` → `/var/www/git`，理由同 qengine-web
  —— 含策略实现，不对公网暴露。
- 未装 Gitea、未碰 nginx / firewalld / SELinux / zeroq-backup.timer。

### 回滚方式（保留有效）

```bash
cp -p /home/git/.ssh/authorized_keys.bak-20261108-kg /home/git/.ssh/authorized_keys
cp -p /usr/local/bin/qgit-backup.sh.bak-20261108-kg   /usr/local/bin/qgit-backup.sh
mkdir -p /home/git/_trash_20261108 && mv /home/git/repos/zerobound-kg.git /home/git/_trash_20261108/
```
