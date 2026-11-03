# 云边协同 · 千人千面本地库 v1

- 日期：2026-11-03｜触发：A0「我们走的是云边协同，未来千人千面，每个人本地库不一样；本地库读取/总结是需要的」
- 结论：**本地库先在本机读、在本机总结；云端只消费摘要与证据指针**。这是一条隐私与个性化红线，不是可选优化。

## 一、架构

```
用户本地库（每人不同）
  → local_library.py（只读、路径隔离、本地总结）
  → LocalBrief（摘要 + 文档元数据 + hash + 证据指针）
  → 用户确认（谁能上云/上什么）
  → 云端 A0/部门负责人（千人千面，不共享他人原始数据）
```

- **Edge**：用户自己的目录/DB/笔记；读取与总结在本地完成；
- **Cloud**：接收 LocalBrief/证据指针，不做原始库全量上传；
- **Identity**：`user_id` 绑定本地 root，跨用户不可读；
- **Personalization**：每个用户的本地库、偏好、角色不同，云端按 `user_id` 生成个人化 A0/部门简报。

## 二、LocalBrief 契约

```yaml
schema: hq/local_brief/v1
user_id: tf
privacy:
  mode: local_only
  upload: summary_only
  raw_uploaded: false
stats: {files: n, bytes: n, by_kind: {...}}
documents:
  - path: notes/a.md
    sha256: ...
    title: ...
    headings: [...]
    excerpt: ...      # 仅本地展示
summary: 本地库共 n 个文档；主题：...
notice: 上云需显式授权，只传 summary/evidence，不传原始文档
```

## 三、已经实现的第二步：manifest + 授权上云

每个用户 root 放 `library.json`：

```json
{
  "schema": "hq/local_library/v1",
  "user_id": "tf",
  "sources": ["notes", "research"],
  "exclude": ["notes/private", "research/raw"],
  "upload": {"mode": "summary_only", "require_confirmation": true}
}
```

- `sources` 白名单：只读这些子目录；`exclude` 排除；
- 路径逃逸/绝对路径/`..`/符号链接拒绝；
- 只有 `--authorize` 才能 `--export-cloud`；
- 上云载荷 `hq/local_brief_cloud/v1` **不含原文、excerpt、本地 path、root**，只含 summary/evidence(hash/title/headings)/stats；
- `confirmed=true` 仅在用户授权导出时写入。

```powershell
# 本地简报（不上云）
python -X utf8 tools/local_library.py --user tf --root C:\项目\_local_libraries\tf

# 用户确认后，导出可上云摘要
python -X utf8 tools/local_library.py --user tf --root C:\项目\_local_libraries\tf `
  --authorize --export-cloud C:\Temp\tf_cloud_brief.json
```

## 四、已实现

- `tools/local_library.py`
  - 只读扫描本地 root；拒绝路径逃逸、隐藏文件、符号链接、超大文件；
  - 单文件 ≤1MB、总量 ≤5MB、最多 200 文件；
  - UTF-8 读取，非文本跳过；生成元数据/hash/摘录/稳定摘要；
  - 默认输出 LocalBrief；`--out` 只写指定本地文件；
- 测试 5 项：正常读取、隐藏/非文本跳过、坏 root/user、输出文件、路径逃逸。

### 命令

```powershell
python -X utf8 tools/local_library.py `
  --user tf `
  --root C:\项目\_local_libraries\tf `
  --out C:\项目\_local_libraries\tf\local_brief.json
```

## 五、边界

- 当前只做“本地读取/本地确定性总结”，不调用云端、不写入用户库；
- 上云同步、用户授权弹窗、按用户隔离的云端存储，属下一阶段；
- 浏览器不能直接读任意本地目录；未来由本地 Agent/桌面桥调用本工具，网页只收授权后的摘要；
- 云端 A0 日报不得混入他人原始库；千人千面必须按 `user_id` 隔离。

## 六、下一步

1. 为每个用户建独立 root + `library.json`（允许目录/排除规则/上云策略）；
2. LocalBrief → 用户确认 → 云端同步（摘要 only）；
3. 云端 A0 按用户生成个人版早报；
4. 增加 `/api/hq/local-brief`（仅本地桥，不接受浏览器任意路径）。
