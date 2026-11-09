# -*- coding: utf-8 -*-
"""生成 /bt/stock 报错排查报告（一次性事故复盘，K157 文案 %/ 未转义）。"""

import os
from datetime import datetime, timezone

OUT = os.path.join(
    os.path.dirname(os.path.abspath(__file__)),
    "reports",
    "portal_stock_incident_20261005.html",
)

HTML = """<!DOCTYPE html><html lang="zh"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>事故复盘 · /bt/stock 个股页连接重置（K157 文案 %/ 未转义）</title>
<style>
body{background:#0d1117;color:#e6edf3;font:14px/1.75 -apple-system,
"Segoe UI","Microsoft YaHei",sans-serif;margin:0;padding:24px}
.wrap{max-width:1000px;margin:0 auto}
h1{font-size:20px;margin:0 0 4px}
h2{font-size:16px;margin:30px 0 8px;color:#58a6ff}
.meta{color:#8b949e;font-size:12px;margin-bottom:18px}
table{border-collapse:collapse;width:100%;font-size:13px;margin-bottom:8px}
th,td{border:1px solid #30363d;padding:6px 10px;text-align:left}
th{background:#161b22;color:#8b949e;font-weight:600}
.dim{color:#8b949e;font-size:12px}
.bad{background:#2d1417;border:1px solid #f85149;border-radius:10px;
padding:12px 16px;margin:10px 0;font-family:Consolas,monospace;
font-size:12.5px;white-space:pre-wrap;color:#f85149}
.good{background:#13261b;border:1px solid #238636;border-radius:10px;
padding:12px 16px;margin:10px 0}
code{background:#161b22;border:1px solid #30363d;border-radius:5px;
padding:1px 6px;font-size:12.5px}
pre{background:#161b22;border:1px solid #30363d;border-radius:8px;
padding:10px 14px;overflow-x:auto;font-size:12.5px;line-height:1.6}
.tag{display:inline-block;border-radius:6px;padding:1px 8px;font-size:12px;
margin-right:6px}
.sev1{background:#f85149;color:#fff}
.ok{background:#238636;color:#fff}
a{color:#58a6ff;text-decoration:none}
</style></head><body><div class="wrap">
<h1>🚨 事故复盘 · /bt/stock 个股页全量连接重置</h1>
<div class="meta">报告生成于 __GEN__ UTC · 影响窗口 2026-10-05 07:47~08:05
（约 15 分钟）· <a href="/bt-chanlun">← 缠论工作台</a> ·
<a href="/bt/stock?c=002594">受影响页面（现已恢复）</a></div>

<h2>① 事故概要</h2>
<table>
<tr><th style="width:130px">现象</th><td>07:47 起所有 <code>/bt/stock?c=…</code>
请求连接被服务端直接关闭（客户端 <code>RemoteDisconnected</code> /
<code>10054</code>），<b>无 500 错误页</b>；其它页面（/bt-chanlun、/bridge、
/bt-tbe 等）全程正常</td></tr>
<tr><th>影响面</th><td>个股全景页（四市场全标的）不可用；纯渲染层故障，
<b>无数据损毁</b>；API /api/kline 不受影响</td></tr>
<tr><th>根因</th><td><span class="tag sev1">P1</span>并行会话 K157 新增的
判据文案在 <code>%</code> 格式化模板中写了<b>裸 %</b>（<code>5.5%/9.9%</code>
写作 <code>5.5%/9.9%</code> 的转义缺失形态），<code>%/</code> 被当作格式说明符
→ 拼串时抛 ValueError → handler 中断 → 连接关闭</td></tr>
<tr><th>恢复</th><td><span class="tag ok">已恢复</span>并行会话 08:02:17
写入转义修复版；08:05:21 计划任务拉起新实例加载修复版；六标的实测全 200</td></tr>
</table>

<h2>② 报错本体（err 日志捕获）</h2>
<div class="bad">File "C:\\aios\\portal\\portal_api.py", line 15693, in do_GET
    self._bt_stock_page(qs)
File "C:\\aios\\portal\\portal_api.py", line 8369, in _bt_stock_page
    "&lt;style&gt;.kpb{background:#161b22;border:1px solid #30363d;"
ValueError: unsupported format character '/' (0x2f) at index 641</div>
<div class="dim">注：C:\\aios 为指向 C:\\项目\\老田的AI助手 的 junction
（SYSTEM-safe 路径），同一文件；客户端不掷 500 是因 BaseHTTPRequestHandler
在 handler 内异常时直接断开连接。</div>

<h2>③ 根因实锤（离线复现，index 精确吻合）</h2>
<p>从当前源码提取 8369~8705 行的 K 线模板（13,788 字符，闭合于
<code>% json.dumps(...)</code>），将现存已转义的 <code>%%</code> 还原为裸
<code>%</code>（模拟修复前形态）后做真实 % 格式化：</p>
<pre>复现结果: ValueError: unsupported format character '/' (0x2f) at index 641
与线上报错完全一致: True
index 641 上下文: '实证：s1卖后20/60日均价涨<b>5.5%/</b>9.9%勿独立减仓，b1盘整背驰双窗微'
当前版本(已转义): 格式化 OK（1 个占位 var _kc=%s）</pre>
<p>修复对比：</p>
<pre>修复前（07:4x 部署）: ...s1卖后20/60日均价涨5.5%/9.9%勿独立减仓...
修复后（08:02 现存）: ...s1卖后20/60日均价涨5.5<b>%%</b>/9.9<b>%%</b>勿独立减仓，b1盘整背驰双窗微正+0.5<b>%%</b>...</pre>
<div class="dim">机理：% 格式化中 <code>%/</code> 的 % 被读作格式引入符，
其后 <code>/</code> 非法 → ValueError；index 641 为该 % 在 13,788 字符模板中的
字节偏移。</div>

<h2>④ 时间线（task log + 文件 mtime + pid 链）</h2>
<table>
<tr><th>时间</th><th>事件</th><th>证据</th></tr>
<tr><td>07:45:23~52</td><td class="dim">计划任务三连重启（并行会话抢窗拉锯期，
code=-1）</td><td class="dim">task log</td></tr>
<tr><td>07:47:38</td><td>实例启动，加载 <b>未转义</b> 版本 →
/bt/stock 开始全量崩溃</td><td>err log ValueError ×N</td></tr>
<tr><td>08:02:17</td><td>并行会话写入<b>转义修复版</b>（% → %%）</td>
<td>portal_api.py mtime</td></tr>
<tr><td>08:02:28</td><td>崩溃实例退出（code=-1）</td><td class="dim">task log</td></tr>
<tr><td>08:05:21</td><td>新实例（pid 365652→419616）加载修复版 →
恢复</td><td class="dim">task log / listen pid</td></tr>
</table>

<h2>⑤ 排查过程（方法回放）</h2>
<table>
<tr><th style="width:220px">步骤</th><th>要点</th></tr>
<tr><td>1. 现象定性</td><td>多标的请求批量 <code>RemoteDisconnected</code>
而其它页面 200 → 排除网络/实例死亡，锁定单 handler 异常</td></tr>
<tr><td>2. err 日志</td><td>堆栈直接给出函数与行号（8369 模板串起始行）</td></tr>
<tr><td>3. 鉴别诊断</td><td>与并行会话抢窗（实例被杀→ConnectionRefused）
区分：本例实例存活、单页面稳定失败 → 排除抢窗；task log 时间线
（starting/exited + pid 变更 + 文件 mtime）交叉印证</td></tr>
<tr><td>4. 静态定位</td><td>正则扫描模板中未转义 % 模式，锁定 K157 文案
<code>5.5%%/9.9%%</code>（当前已转义态）</td></tr>
<tr><td>5. 离线复现实锤</td><td>提取 13,788 字符完整模板 → 还原 %%→%
→ 真实格式化 → <b>报错串与 index 逐字吻合</b>（推断升级为实证）</td></tr>
<tr><td>6. 回归验证</td><td>六标的（A×3/HK/US/CRYPTO）全 200；
全文件高危模式扫描无新增命中</td></tr>
</table>

<h2>⑥ 现状与验证</h2>
<table>
<tr><th>项</th><th>结果</th></tr>
<tr><td>/bt/stock 六标的实测</td><td>002594 / 000560 / 600519 / 00700 /
AAPL / BTC-USDT 全 <b>200</b></td></tr>
<tr><td>err 日志（新实例）</td><td>仅 Neo4j <code>limit_type</code> 属性缺失
WARNING（历史已知噪音，非错误）；原 ValueError 已随日志轮转清除</td></tr>
<tr><td>全文件裸 % 高危扫描</td><td>无新增未转义命中（其余命中均为
不参与 % 格式化的普通字符串）</td></tr>
</table>

<h2>⑦ 判例与预防</h2>
<table>
<tr><th style="width:220px">判例</th><th>内容</th></tr>
<tr><td>连接重置 ≠ 500</td><td>门户 handler 内抛异常表现为客户端
RemoteDisconnected（连接直断），server 端 err log 才有堆栈——「页面打不开」
先看 err log 再猜网络</td></tr>
<tr><td>文案进 % 模板必转义</td><td>任何含 <code>%</code> 的用户可见文案
进入 <code>% (...) </code> 格式化模板前必须写作 <code>%%</code>；
上线前对模板做一次真实格式化试算（离线 5 行脚本即可，本轮 index 641
复现即此法）</td></tr>
<tr><td>运行实例与文件版本错位</td><td>多会话并行改同一文件时，进程内存代码
≠ 磁盘代码：报错行号要按<b>报错实例启动时刻的版本</b>理解；修复验证以
task log「starting 时间 &gt; 文件 mtime」为准</td></tr>
<tr><td>与抢窗鉴别</td><td>实例被杀=全站 Refused；单 handler 异常=单页面
稳定断连——task log 时间线 + pid 变更是鉴别第一现场</td></tr>
</table>

<div class="good"><b>结论</b>：P1 渲染层故障，根因实锤（K157 判据文案裸 %，
离线复现 index 641 逐字吻合），已由并行会话修复并恢复，无数据影响；
预防判例入档（文案转义 + 版本错位 + 鉴别诊断三项）。</div>
</div></body></html>"""

gen = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M")
HTML = HTML.replace("__GEN__", gen)
os.makedirs(os.path.dirname(OUT), exist_ok=True)
with open(OUT, "w", encoding="utf-8") as fp:
    fp.write(HTML)
print("written:", OUT, len(HTML), "chars")
