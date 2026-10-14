# K-ORG-072 e2e 正验单（增量型）：e2e_report 增加 --csv 导出

> 工单=K-ORG-072；本需求自 K-ORG-071 顺延（071 为彩排轮，072 为正验轮），目标文件同为 tools/e2e_report.py。

P-1 新增 build_csv(items) 纯函数：接收 scan_e2e_traces() 的 items 列表，
返回 CSV 文本（表头 tid,time,final,steps,last_step；字段含逗号/换行时用双引号包裹转义）；
既有函数 scan_e2e_traces/parse_trace_file/_main_root 一字不动。

P-2 main() 增量修改：新增 --csv 开关，与 --json 互斥（同时给则报错退出码 2）；
--csv 时 stdout 输出 CSV 文本并退出码 0。

P-3 契约声明（增量）：新增 --csv 通道输出 CSV；无 --csv 且有 --json 时行为与金样本
逐字段一致（runs/items/time 字符串/终态 UNKNOWN 缺省）；默认无参行为不变（argparse 报错）。
