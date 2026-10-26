---
name: worktree-hygiene
description: "工作树 TTL/回收/收口（S1）"
license: internal
compatibility: Windows, Python 3.12, local-only, no-network-verify
metadata:
  owner: "ZHAOYUN"
  version: "1.0.0"
  risk: "read-only"
  legacy_capability: "worktree-hygiene"
  verify: "scripts/verify.py"
allowed-tools: [read-file, run-python]
---

# worktree-hygiene

## 是什么
工作树 TTL/回收/收口（S1）

## 怎么跑
```
git worktree list + devtask 收口提示
```

## 边界
删除类进回收站/先归档

## 验证
```
python agent_platform/skills/worktree-hygiene/scripts/verify.py
```

