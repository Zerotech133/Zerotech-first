# master 推送与远端 HEAD 核对回执

- 日期：2026-11-11 06:55（Asia/Shanghai）
- 操作：将本地 `master` 普通推送到 GitHub `origin`，未使用 force，未推送其他分支。
- 推送目标提交：`51d9449160831cab8329a6b6fcb2cf1e1dd7add1`
- 结果：**推送成功**；`origin/master` 已指向该提交。

## 1. 推送前差距检查

```text
本地 master：51d9449160831cab8329a6b6fcb2cf1e1dd7add1
远端 master（推送前）：fe0f1a4f0c182ce3b68b2fd3e947d2533556780c
```

本地相对远端 master：

```text
5 ahead / 0 behind
```

结论：本地仅领先、未落后、未分叉，允许普通推送。

## 2. 推送

```powershell
git -c http.proxy=http://127.0.0.1:7890 `
    -c https.proxy=http://127.0.0.1:7890 `
    push origin master
```

输出：

```text
To https://github.com/Zerotech133/Zerotech-first.git
   fe0f1a4f0..51d944916  master -> master
```

- 普通 push；
- 未使用 `--force`；
- 只推 `master`，未推 `main` 或其他分支。

## 3. 推送后 `ls-remote` 结果

```text
$ git ls-remote --symref origin HEAD
ref: refs/heads/main  HEAD
fe0f1a4f0c182ce3b68b2fd3e947d2533556780c  HEAD

$ git ls-remote --heads origin master main
fe0f1a4f0c182ce3b68b2fd3e947d2533556780c  refs/heads/main
51d9449160831cab8329a6b6fcb2cf1e1dd7add1  refs/heads/master

$ git ls-remote --tags origin refs/tags/v0.9.0-internal*
4b5f2df1713affe5e3b1f50afa27c6527ac56275  refs/tags/v0.9.0-internal
7ab9e55240d2261fe55715a535c70a043edb92cd  refs/tags/v0.9.0-internal^{}
```

## 4. 分支与 tag 一致性

- tag `v0.9.0-internal` 的 peeled commit：`7ab9e55240d2261fe55715a535c70a043edb92cd`
- `7ab9e5524` 是 `master` 的祖先：✅
- `7ab9e5524` 是 `main` 的祖先：✅
- 因此 tag 已包含在 `master` 与 `main` 两条分支的历史中。

### 远端 HEAD 说明

GitHub 仓库默认分支是 `main`，不是 `master`：

```text
HEAD -> refs/heads/main -> fe0f1a4f0
refs/heads/master       -> 51d944916
```

- `origin/master` 已与本地 `master` 一致（`51d944916`）；
- GitHub 远端 **HEAD 仍指向 `main`**（`fe0f1a4f0`），比 `master` 落后 5 个提交；
- 这是“只推 master、不推其他分支”的直接结果，未擅自推送 `main`。

## 5. 结论

| 项 | 结果 |
|---|---|
| 本地 master 与推送前 origin/master | 仅领先 5，未落后/分叉 |
| 普通 push master | ✅ 成功 |
| origin/master | `51d9449160831cab8329a6b6fcb2cf1e1dd7add1` |
| origin HEAD（默认 main） | `fe0f1a4f0c182ce3b68b2fd3e947d2533556780c` |
| tag 目标 | `7ab9e55240d2261fe55715a535c70a043edb92cd` |
| tag 是否在 master/main 历史 | 两者均为祖先关系 ✅ |
| force / 其他分支 | 未使用 force；未推送其他分支 |

> 本回执文件是在上述推送完成后追加的 docs-only 提交；其自身提交会使 `master` 再前进 1 个文档提交，远端最终 HEAD 以最后一次 `git ls-remote` 为准。
