# KB 索引切换流程（确认后执行）

> 候选索引已建，尚未切换。
> 必须等 206 全量 + 人工抽检确认后，由人执行。

## 候选

```
C:\项目\知识库\50_RAG\rag_index_corpus_v2_20261104.json
records = 6,826
sha256 = 7c89fc1c12ce42bbe368b709024a1ecd2a29464730f6f8c578ad8fa2bb5c2d36
```

canonical：

```
C:\项目\知识库\50_RAG\rag_index_corpus.json
records = 4,926
```

## 确认条件

```
1. 206 全量：RAG >= 裸模型（char/bigram/number 综合）
2. 人工抽检 40 条：RAG 不差于裸模型，引用可追溯
3. 没有明显时间敏感/答非所问增加
4. canonical 备份完成
5. 人类明确批准
```

## 执行（确认后）

```powershell
$kb='C:\项目\知识库\50_RAG'
$ts=Get-Date -Format 'yyyyMMddTHHmmss'
Copy-Item "$kb\rag_index_corpus.json" "$kb\rag_index_corpus.bak-$ts.json"
Copy-Item "$kb\rag_index_corpus_v2_20261104.json" "$kb\rag_index_corpus.json"
# 验证
(Get-Content "$kb\rag_index_corpus.json" -Raw | ConvertFrom-Json).records.Count
```

## 回滚

```powershell
Copy-Item "$kb\rag_index_corpus.bak-<ts>.json" "$kb\rag_index_corpus.json" -Force
```

## 禁止

```
不自动执行
不自动重启生产 RAG 服务
不自动上线
```

## 服务侧

如生产 RAG 服务缓存了索引，切换后需按既有部署流程重启/刷新；本流程不自动重启。
