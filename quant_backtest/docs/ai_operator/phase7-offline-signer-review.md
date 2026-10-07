# 第七批离线签名工具独立审查

## 审查范围与基线

只读审查主任务 `ai_operator/offline_review_signer.py` 与 `tests_ai_operator/test_offline_review_signer.py`。没有审查本人实现的 signed_review.py 作为独立结论，没有修改被审查源码/测试，没有派代理或重跑已通过的套件。

主任务说明已通过 11 项定向检查，其中为 6 项 signer 测试加 5 项 calendar CLI 测试；本报告不将 11 项全部计作 signer 覆盖。

初始审查原始字节 SHA256：

- `ai_operator/offline_review_signer.py`：`d1fc9be742f85237b0438a2515d2606647f4fa501b77a007a2b786410c00fe39`。
- `tests_ai_operator/test_offline_review_signer.py`：`85037a154f3decef65f27409f6a9857ded677fde1b08460250715987eb2fd146`。

以下行号对应此基线；主任务已经接受 Windows ACL 问题并正在修改，修复版本须按新哈希重新核对。

## 发现

### P2：Windows 私钥没有专属 ACL

`ai_operator/offline_review_signer.py:34` 仅使用 `os.open(..., 0o600)`，随后 37 行直接写入加密 PKCS8 私钥。Windows 上此 mode 不能限制 NTFS DACL，文件仍可能继承输出目录中其他账户/组的访问权。用户采用本人离线密钥，计划要求创建私钥时尽可能收紧权限；应在写秘密前对刚创建的空文件配置当前用户专属 ACL，设置或核验失败则拒绝并清理空文件。

已有测试 72 行仅验证加密格式、解密和公钥指纹，没有验证实际 Windows ACL，也没有权限配置失败时零秘密内容的测试。主任务确认接受此项，正在实现修复。

### P2：失败清理会删除被另一写者替换的文件

`ai_operator/offline_review_signer.py:40` 到 42 行仅凭当前路径是普通非符号链接文件就 unlink，没有确认它仍是本次排他创建的 inode/file-id。fsync 或写入失败时，另一进程可在异常清理前替换该路径；清理将删除替换者的文件，违反“只清理本次创建文件”的边界。

必要局部复现使用临时目录和非秘密 marker，给 os.fsync 注入回调：删除当前路径、写入不同替换内容、再抛 OSError。`_write_new` 清理后 `replacement_survives_cleanup=false`，明确删除了另一写者的内容。未修改源码或运行整套测试。建议绑定打开文件的身份，清理时核对原创建对象，避免按路径类型无条件删除；新增替换期间异常的回归案例。

### P2：声明读取与 Windows ADS 输出没有统一普通本地文件边界

`ai_operator/offline_review_signer.py:95` 直接把 `args.statement` 交给 CLI `_read_input`；该 helper 的 `cli.py:115` 是普通 `Path.open`，没有执行 `_local_path`、符号链接/重解析点或普通文件检查。UNC 声明路径可能使离线工具在显示和本人确认前访问网络路径，而 private-key/output 已有本地限制。

此外 `offline_review_signer.py:22` 复用的 `_local_path` 只限制 UNC/设备命名空间/null，没有拒绝文件名冒号形式的 NTFS ADS。输出 `container.bin:proof` 会成功写入隐藏流，工具不能保证 proof/private/public 是可正常检查的普通文件。

局部复现把声明读函数替换为立即停止的观测回调，无任何网络访问，确认 UNC 参数会到达读取函数。另一独立临时目录复现 `_new_path` + `_write_new` 的 ADS 路径，实际结果 `ads_write_succeeded=true`、基础 `container.bin` 大小 0、隐藏流长度 29（非秘密工程 marker）。普通 DOS 设备 `NUL` 在当前平台已经被已有存在性检查拒绝，本报告不把它列为已复现缺陷。

建议对声明也使用安全本地普通文件读取，同时保留原文 JSON 重复键/非有限数/8MiB 约束；工具自身统一拒绝 ADS 路径并加入定向测试。

### P2：无法关闭口令回显时没有拒绝

`ai_operator/offline_review_signer.py:54` 直接调用 getpass，未将 `GetPassWarning` 升级为失败。`main:134` 检查 stdin/stdout 的 isatty，不能证明 getpass 真正关闭回显。Windows 标准库 win_getpass 在 `sys.stdin is not sys.__stdin__` 时转入 fallback_getpass；后者警告并调用普通输入，可能回显密码。包装但仍 isatty 的输入流会满足本工具门槛。

局部复现用测试 getpass 发出 GetPassWarning 后返回工程口令，`_password` 仍成功返回 bytes，观测 `password_accepted_after_echo_warning=true`。没有输出口令内容。建议捕获该专用 warning 并立即拒绝，而不是继续取得/使用可回显密码；补充失败时未生成或签署文件的检查。

## 已核对的正确行为

- `_write_new:34` 使用 O_EXCL，已有文件不覆盖；keygen 检查 private/public 目标不同，sign 检查 proof 目标不存在。
- keygen 使用 BestAvailableEncryption 保存 Ed25519 PKCS8，sign 拒绝明文私钥和非 Ed25519 类型。
- `_sign:98` 在显示/确认前构造完整规范声明消息，`102` 显示 authority/policy 和完整 statement，`103` 后才取得解密口令并签名；没有在确认后重新从声明文件读取不同内容。
- `_read_input` 的 object_pairs_hook 拒绝原文重复键，parse_constant 拒绝非有限数，读取有 8MiB 上限；现有 signer 测试覆盖重复 action 键。
- 正常输出没有打印私钥或口令；PEM 解密错误统一为固定错误。口令没有命令行/环境/JSON入口；公钥指纹与签名 proof 都不会安装可信身份策略。

## 验证边界

没有重跑既有 6 项 signer / 5 项 calendar CLI 套件。本报告局部复现仅针对上述可疑行为，全部使用自动清理的临时目录和非秘密 marker；没有生成用户私钥、访问网络、签署真实批准或修改 E 盘。待主任务提供修复版哈希后，需要核对实际 ACL 及修复后的这三个局部失败路径。

## 修复版独立复审

修复版源码 SHA256 `9aed12edd10430e5c4756631c70318da3dce4045a0c6a116a9258be9f12c1a46`、测试 SHA256 `e256f1543f7790bb607ab8534ed7bbbc1c20b0d13f101a0043d1d28107f6c946`，实际文件哈希与主任务提供值一致。主任务报告新 12 项 signer GREEN 2.042 秒；本审查没有重复运行该套件，只读核对新增检查及针对残留疑点做局部反例。

### 已关闭的修复项

- 路径：`offline_review_signer.py:22` 的统一 ordinary_path 拒绝 ADS；`195` 先校验声明路径，`196` 经安全 read_evidence 核验本地普通文件、链接/重解析点及打开身份，`197` 用 CLI decode_input 保留严格 JSON 重复键检查。私钥路径也在 `201` 走 ordinary_path。原 UNC/ADS 问题已关闭。
- 回显：`149` 到 155 行把 GetPassWarning 升级为错误并固定 ValueError 拒绝；标准库 warning 在 fallback 普通口令读取之前产生，故能在回显前停止。原口令回显问题已关闭。
- ACL 实际设置改用 CreateFileW 的同一句柄 SetSecurityInfo，指定当前用户 SID 和 protected DACL，在 `133` 写入秘密前执行；避免 shell 编码与按路径设置另一个对象的问题。新增测试检查实际受保护且只有当前用户 SID 的 ACL，并验证权限设置失败时空文件清理。
- 清理新增 file-id/dev 比较，修复了“进入清理之前就已被替换”这一原始复现；新增测试覆盖该路径。但以下窄窗口仍未关闭。

### P2：ACL 设置前的共享读取句柄仍可读取后续秘密

`offline_review_signer.py:115` 的 CreateFileW share_mode 为 `1`（FILE_SHARE_READ），且创建时 SecurityAttributes=None，初始空文件仍继承父目录 ACL。其他被继承 ACL 允许读取的主体可在当前用户专属 ACL 设置前打开一个 READ 句柄。Windows 后续 DACL 变更不会撤销已有句柄的访问权限，该句柄继续读到之后写入的私钥内容。

局部反例在 `_restrict_private_permissions` 执行前对空临时文件打开 read handle，再执行真实同句柄 DACL 设置和非秘密 marker 写入；旧 read handle 实际读到了完整 marker，观测 `pre_acl_read_handle_reads_after_acl=true`。此证明不使用真实私钥；已有继承 ACL 允许的另一个主体也会保留同样的已授句柄权限。

建议私钥排他创建使用 share_mode=0，直到秘密写入完并关闭后，才让新的读取者经过已收紧 DACL 的访问检查；或者 CreateFileW 创建时即传入最终专属 SecurityAttributes。测试中空文件大小可以直接用本次 descriptor 的 os.fstat 核验，不必为了读空文件开放共享读取。

### P2：身份检查与按路径 unlink 之间仍存在替换误删窗口

`offline_review_signer.py:96` 先 stat 捕获当前身份，`97` 再调用 is_symlink 并比较此前捕获的身份，`98` 按路径 unlink。stat 与最终删除不是同一对象的原子操作；另一写者在 stat 之后替换路径，保存的 current 仍匹配本次对象，非链接检查对替换普通文件也返回 False，因此 unlink 仍删除替换者文件。

局部反例将替换动作精确放在最终 is_symlink 调用中（真实 rename 原对象并写入新对象），再继续 `_remove_our_file`。观测 `replacement_between_stat_and_unlink_survives=false`，新对象仍被删除。此反例只用临时目录的非秘密 marker，不修改源码。

建议 Windows 异常时用具有 DELETE 权限的本次创建句柄标记删除，保持对象身份绑定；或失败时安全保留文件并报告清理未完成，避免可竞争的按路径删除。若保留文件，私钥权限设置失败处仍不得写入任何秘密，已有 descriptor 顺序能够支持这一保证。

### 当前结论

此次冻结版没有关闭所有问题：UNC/ADS 与回显两项已关闭，原 ACL 和清理问题分别仍有上述可复现窄窗口。完整声明确认、PKCS8 加密、排他创建和秘密不输出未见新增回归。此结论仅限已核对的新哈希，不将主任务 12 项 GREEN 等同于这些额外并发反例通过。

## 最终限定复审结论

最终版本原始字节 SHA256 已实际核对：

- 源码 `ai_operator/offline_review_signer.py`：`d31e41054e8e5e7b46cada221e302b2aa517cca30078cd6c7049fc64bb841db3`。
- 测试 `tests_ai_operator/test_offline_review_signer.py`：`f6e9fae15b16562ccc42d9cbed6f441df4aeb4ae6caa2dc2bf24714acedbcafa`。

主任务报告 14 项 signer GREEN 2.191 秒。本次没有重新运行定向套件，只复审前述两项并发 P2 的修复差异和必要的临时 marker 反例。

### 两项残留 P2 已关闭

- `offline_review_signer.py:120` 的原生 CreateFileW 使用 CREATE_NEW、share_mode=0，并具备 GENERIC_WRITE/READ_CONTROL/WRITE_DAC/DELETE 权限。空文件到秘密完成写入的整个句柄生命周期保持独占，`135` 在同一句柄上设置专属 protected DACL 后才在 `136` 写字节；不会再有 ACL 设置前的外部 read handle 保留读取权限。
- `94` 到 105 行通过 SetFileInformationByHandle/FileDispositionInfo 标记原对象删除；`139` 到 142 行在原独占私钥句柄仍打开时处理失败。删除绑定原句柄，不再以路径 stat/unlink 清理。API 返回失败时保留本次文件，避免误删他人对象。
- 源码已删除所有路径清理。非秘密 proof/public 写失败可留故障文件；`185` 后若公钥输出失败，受保护的加密私钥保留给本人处理。这是明确保守失败边界，不能再宣称 keygen 多文件完全原子或失败绝对零文件。
- 新测试 `219` 验证 ACL 收紧前连当前用户都不能额外 open 读取；`231` 的公钥失败测试禁止任何 Path.unlink，确认受保护加密私钥保留。此前实际 ACL 和设置失败零秘密测试继续存在。

### 针对性复现结果

使用自动清理临时目录和非秘密 marker，直接调用被冻结模块，没有生成任何真实私钥、访问网络或运行全套：

1. 在真实 `_restrict_private_permissions` 执行前尝试另开 read handle：`pre_acl_read_blocked=true`；继续真实 ACL 设置和写入，关闭后 `protected_write_completed=true`。
2. 注入权限失败并用 fstat 确认尚未写字节，同时禁止 Path.unlink：`acl_failure_before_any_bytes=true`、`original_failed_private_removed_by_handle=true`。
3. 非秘密写 fsync 失败时关闭写 descriptor 后真实 rename 原对象并写入替换 marker，禁止 Path.unlink：`replacement_created=true`、`replacement_preserved_without_path_cleanup=true`。最初的反例助手尝试在 CRT 写句柄仍打开时 rename，Windows 拒绝该 rename，没有形成替换；调整为与既有回归测试一致的关闭后替换再核验，本次结果明确保留替换文件。

### 最终判断

在上述最终哈希和限定差异范围内，原四项 P2 及后续两个并发窗口均已关闭，未发现新的确定缺陷。此判断取代上节旧哈希的“两个 P2 未关闭”结论。正常秘密不输出、完整声明本人确认、严格原文重复键、加密 PKCS8 和不安装身份信任的边界未见修复引入回归。

测试和审查仍只是工程验证，不证明公钥已登记为真人身份，也不提供真实批准、来源真实性或训练/生产资格。主任务保留完整回归和最终包验收责任；本任务没有扩大审查到自行实现的 signed_review.py，也没有修改被审源码或派代理。
