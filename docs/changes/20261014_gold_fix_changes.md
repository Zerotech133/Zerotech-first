# 2026-10-14 gold-dev：两存量测试失败修复留痕

泳道：gold-dev（只动测试与测试数据；今日改动文件备份后缀 `.bak_20261014_g`）
解释器：`C:/Program Files/Python311/python.exe`（全程唯一）

---

## 失败1：test_chanlun_signal_gold 1 failed —— 金样本字段漂移

### 根因
10-13 `chanlun_signal.py` `_pivot_signals`（chanlun_signal.py:188）在 **3买** 信号 dict 新增
`zg`（中枢上沿）/`zd`（中枢下沿）字段，金样本断言未同步。该文件无 `.bak_20261014*`，
确认非今日改动。审计结论与实测一致：`data/chanlun_samples.jsonl` 508 行无 zg/zd。

### 判定：走方向 (a)——合理新增，更新金样本
- diff 显示新增值为 `zg: 60.0, zd: 52.0`，与金样本注释「中枢 [52,60]」完全一致，
  为真实数值，非 None/NaN 污染，非 bug。
- 断言为逐字段全等（金样本快照口径），故同步期望值而非加宽容。

### 修复
- 备份：`test_chanlun_signal_gold.py.bak_20261014_g`
- 改动：test_chanlun_signal_gold.py:133 的 3买期望 dict 补 `"zg": 60.0, "zd": 52.0`
  （附注释说明来源 10-13 信号流新增）。

### 数字
| | 修复前 | 修复后 |
|---|---|---|
| pytest test_chanlun_signal_gold.py -q | 1 failed / 12 passed | **13 passed** |

### 同步检查：zg/zd 是否进了影子对账链
- `chanlun_sample.py`（`data/chanlun_samples.jsonl` 唯一写入方）按固定 schema 只取
  `type/dt/price/vv/tbe` 等字段（chanlun_sample.py:141/182/261），**不透传 signal dict
  全字段**；样本库 schema 本就不含 zg/zd → 「影子库没带」是 schema 设计而非漏带，
  **不需要产线动作**。
- ⚠️ 交主线程决策的发现：**3卖分支（chanlun_signal.py:193-201）未带 zg/zd，仅 3买带**，
  产线内部不对称。若下游评估需要 3卖的中枢区间，需补齐（属产线改动，本泳道未动）。

---

## 失败2：test_ui_drilldown 41 failed —— 存量批量失败

### 现场核实
- 审计快照出处：`20261014_cross_resilience_changes.md:89`「pytest test_ui_drilldown.py：
  41 failed / 46 passed，与改前备份对照完全一致（失败为存量环境问题，非本次引入）」。
- 本次接管后，指定解释器 Python311 下 **不可复现 41**：主树 pytest / unittest、
  父目录 cwd、K-STOCK-374 worktree 均为 87 passed。期间（今日 12:23）该文件已被
  其他泳道动过。
- 找到**可复现的同构失败**：Python312（装 numpy/pandas、无 neo4j 驱动）下
  `unittest test_ui_drilldown` → **8 errors**，全部集中在 TestStockOverview。

### 代表性 traceback（py312，3/8）
```
kg_client.py:29  → ModuleNotFoundError: No module named 'neo4j'
kg_client.py:31  → SystemExit: neo4j python driver 未装
  ↑ 经 test_ui_drilldown.py:988 _render → portal/api_backtest.py:2051 _bt_stock_page
    → kg_portal.py:123 stock → kg_portal.py:69 _client → KgClient.__init__
```
（test_jev_items_and_matrix_card / test_quote_missing_placeholder / test_quote_render_and_colors
等 8 例同栈）

### 根因归类
| 类别 | 判定 | 依据 |
|---|---|---|
| 测试缺口类（真因） | TestStockOverview._render 漏 mock `kg_portal` / `event_graph` | `_bt_stock_page` KG 块（api_backtest.py:2048-2058）`import kg_portal` 直连真实 Neo4j，违反该测试「全 mock 零网络」契约。指定解释器装了 neo4j，靠连接失败被 `except Exception` 降级**侥幸通过且实际发生真实 DB 访问**（跑一遍 71s 为证）；缺驱动的解释器里 `KgClient` 抛 `SystemExit`（BaseException），**绕过 `except Exception`** → 批量 error。审计 41 failed 高概率为审计环境解释器缺三方依赖的同类失败（该快照无法在当前机器复现） |
| 产线隐患类（只报告，未动） | api_backtest.py:2051 `except Exception` 拦不住 kg_client.py:31 的 `SystemExit` | 若希望「缺 neo4j 驱动 → 优雅降级」语义成立，建议 kg_client.py 改抛 `ImportError`（kg_client.py 无今日备份，非今日改动；本泳道纪律不动产线，交主线程决策） |
| 环境依赖类 | 排除为主因 | 指定解释器下依赖齐全、87 全绿；缺依赖场景已由测试缺口修复消解 |

### 修复（测试缺口，非 skip、非吞错）
- 备份：`test_ui_drilldown.py.bak_20261014_g`
- 改动：test_ui_drilldown.py `_render` 的 `mock.patch.dict(sys.modules, mods)` 补两个
  stub：`kg_portal.stock → None`、`event_graph.events_for_stock → {code,n,bias_stat,events}`
  （返回结构与 event_graph.py:441 docstring 一致，产线消费键 bias_stat/events/n 全覆盖），
  使 KG 块走产线既有降级分支，恢复零网络确定性。

### 数字
| 运行 | 修复前 | 修复后 |
|---|---|---|
| py311 `pytest test_ui_drilldown.py` | 87 passed（但含真实 Neo4j 访问，71s） | **87 passed（37s，零网络）** |
| py311 `unittest test_ui_drilldown` | OK | OK |
| py312 `unittest test_ui_drilldown`（旁证环境类） | 8 errors / 79 | **87 OK** |
| 审计快照（cross_resilience 泳道，环境不可复现） | 41 failed / 46 passed | — |

### skip 清单
无。未使用任何 skip 标记，未使用笼统 try/except 吞错凑绿。

---

## 纪律遵守
- 仅改 2 个测试文件，均已留 `.bak_20261014_g` 备份；产线零改动。
- 未推飞书、未注册计划任务。
- 遗留交主线程：① 3卖分支 zg/zd 不对称；② kg_client SystemExit 绕过 except Exception。
