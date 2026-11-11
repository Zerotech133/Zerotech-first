# master 推送、main 快进与远端 HEAD 核对回执

- 日期：2026-11-11 07:20（Asia/Shanghai）
- 操作：普通推送 `master`，并将 `main` 快进到同一收口提交；未使用 force，未推送其他分支。
- 最终收口提交：`5f6fcadb58e0d48599851b503ed98cbfc86901e9`

## 1. master 推送（首次）

推送前：

```text
本地 master：51d9449160831cab8329a6b6fcb2cf1e1dd7add1
远端 master：fe0f1a4f0c182ce3b68b2fd3e947d2533556780c
本地相对远端：5 ahead / 0 behind
```

推送：

```text
fe0f1a4f0..51d944916  master -> master
```

随后回执文件作为 docs-only 提交 `5f6fcadb5` 追加并再次推送到 `master`。

## 2. main 快进到 5f6fcadb5

推送前：

```text
origin/main：fe0f1a4f0c182ce3b68b2fd3e947d2533556780c
目标提交：5f6fcadb58e0d48599851b503ed98cbfc86901e9
```

`fe0f1a4f0` 是 `5f6fcadb5` 的祖先，可 fast-forward。

普通推送（无 force）：

```powershell
git push origin 5f6fcadb58e0d48599851b503ed98cbfc86901e9:main
```

结果：

```text
fe0f1a4f0..5f6fcadb5  5f6fcadb58e0d48599851b503ed98cbfc86901e9 -> main
```

## 3. 远端 `ls-remote` 最终结果

```text
$ git ls-remote --symref origin HEAD
ref: refs/heads/main  HEAD
5f6fcadb58e0d48599851b503ed98cbfc86901e9  HEAD

$ git ls-remote --heads origin main master
5f6fcadb58e0d48599851b503ed98cbfc86901e9  refs/heads/main
5f6fcadb58e0d48599851b503ed98cbfc86901e9  refs/heads/master

$ git ls-remote --tags origin refs/tags/v0.9.0-internal*
4b5f2df1713affe5e3b1f50afa27c6527ac56275  refs/tags/v0.9.0-internal
7ab9e55240d2261fe55715a535c70a043edb92cd  refs/tags/v0.9.0-internal^{}
```

- `origin/main` = `5f6fcadb5`
- `origin/master` = `5f6fcadb5`
- 默认 `HEAD` → `refs/heads/main` = `5f6fcadb5`
- **GitHub 默认 HEAD 与远端 master 已一致。**

## 4. 分支与 tag 一致性

- tag `v0.9.0-internal` 的 peeled commit：`7ab9e55240d2261fe55715a535c70a043edb92cd`
- `7ab9e5524` 是 `master` 的祖先：✅
- `7ab9e5524` 是 `main` 的祖先：✅
- tag 已包含在两条远端分支历史中。

## 5. 本地 master 的并行推进说明

执行 main 快进前后，本地 `master` 被其他并行会话继续推进：

```text
本地 master：20943bf92a133315e5acb5bf58dc7059d18b3d01
远端 master：5f6fcadb58e0d48599851b503ed98cbfc86901e9
```

- 本次**只**把 `main` 快进到 `5f6fcadb5`，未推送新的本地 `master` 提交；
- 因此 GitHub 上 `main` 与 `master` 都是 `5f6fcadb5`，但本地 `master` 已领先；
- 这属于其他会话的并行提交，不是本次推送造成的分叉。

## 6. 结论

| 项 | 结果 |
|---|---|
| 普通 push master | ✅ 成功，无 force |
| main 快进 | ✅ `fe0f1a4f0..5f6fcadb5` |
| origin/master | `5f6fcadb58e0d48599851b503ed98cbfc86901e9` |
| origin/main | `5f6fcadb58e0d48599851b503ed98cbfc86901e9` |
| origin HEAD | `refs/heads/main` → `5f6fcadb5` |
| tag 目标 | `7ab9e55240d2261fe55715a535c70a043edb92cd` |
| force / 其他分支 | 未使用 force；未推送其他分支 |

> 本回执的更新内容停留在工作区，未再提交/推送；否则会再次推进 master，破坏本次“main/master/HEAD 三者均为 5f6fcadb5”的目标状态。
