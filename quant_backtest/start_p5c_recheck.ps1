# one-shot Feishu reminder launcher for P5c recheck day (ASCII only, junction path)
# Task P5cRecheck fires once at 2026-11-02 09:30, pushes pre-written SOP message.
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8
$env:PYTHONUTF8 = '1'
& 'C:\Program Files\Python311\python.exe' -X utf8 `
    C:\aios\news_collector\send_feishu.py `
    C:\aios\quant_backtest\outbox\p5c_recheck_msg.txt `
    *>> C:\Temp\p5c_recheck.log
exit 0
