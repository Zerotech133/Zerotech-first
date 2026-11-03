# 本地桥 + 云端 A0 个人早报 v1

- 日期：2026-11-03｜承接：云边协同/千人千面本地库四项计划
- 状态：**本地桥（stdio）与个人 A0 早报已实现；网页消费授权摘要待接。**

## 一、每用户本地库（已建）

```
C:\项目\_local_libraries\
  tf\
    library.json
    notes\
    research\
  _template\library.json
```

`tf/library.json`：

```json
{
  "schema": "hq/local_library/v1",
  "user_id": "tf",
  "display_name": "老田(tf)",
  "sources": ["notes", "research"],
  "exclude": ["notes/private", "research/raw"],
  "upload": {"mode": "summary_only", "require_confirmation": true}
}
```

新用户复制 `_template` 到 `<user>/`，改 `user_id` 即可。

## 二、本地桥（stdio，无端口）

`tools/local_bridge.py` 不新开端口、不暴露网络；桌面 Agent 通过 stdin/stdout JSON 行调用：

```json
{"id":"1","action":"users"}
{"id":"2","action":"brief","user":"tf"}
{"id":"3","action":"cloud_brief","user":"tf","authorize":true}
```

- 只允许 `_local_libraries/<user>/`，不接受任意 path；
- `cloud_brief` 必须 `authorize:true`；
- 返回的 cloud_brief 不含原文/path/root，只有 summary/evidence；
- 无授权 → `authorize_required`，rc=2。

命令行快速验证：

```powershell
python -X utf8 tools/local_bridge.py --action users
python -X utf8 tools/local_bridge.py --action brief --user tf
```

## 三、云端 A0 个人早报

`tools/hq_personal_brief.py` 只接受 `hq/local_brief_cloud/v1`：

```powershell
python -X utf8 tools/hq_personal_brief.py `
  --cloud-brief C:\Temp\tf_cloud_brief.json `
  --user tf
```

输出：`deliverables/personal_briefs/<user>/<date>.json`

- 仅使用授权摘要/证据；
- 校验 `user_id` 匹配、`privacy.raw_uploaded=false`；
- 不读取本地原文、不跨用户；
- 真实调用 A0 云端模型；binding/降级可见。

## 四、验证

- 本地库：9 tests（manifest/路径/授权导出）
- 本地桥/个人 A0：5 tests（users/brief/授权/越权/个人输出）
- 实际 tf 库：`users` 返回 `tf`；未授权 cloud_brief 返回 `authorize_required` rc=2

## 五、网页入口（已接）

`/briefing` 新增「我的本地库 · 千人千面」：

1. 选择本地库文件夹（浏览器本地读取，原文不发送）；
2. 本地扫描 md/txt/json/yaml/csv/log，≤1MB/文件、≤5MB/总量、≤200 文件；
3. 勾选「我同意将摘要/证据上云」才可生成；
4. 点「生成个人版早报」：只把 summary/evidence 发给 A0，原文/路径不发送；
5. 结果显示 A0 binding/是否云端。

Playwright 实测：本地 1 文档、授权前按钮禁用、授权后生成成功，发送体不含原文 secret。

## 六、下一步

1. 桌面 Agent 接 stdio 桥，把“用户确认”做成 UI 按钮；
2. 网页只接收 `cloud_brief` 的 summary/evidence；
3. 云端按 `user_id` 生成个人版早报并进入 `/briefing`；
4. 严禁跨用户共享本地原始库。
