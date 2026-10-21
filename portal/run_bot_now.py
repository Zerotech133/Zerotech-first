import sys, os, threading, time

os.chdir(r'C:\aios\portal')
sys.path.insert(0, r'C:\aios\portal')
sys.path.insert(0, r'C:\aios\news_collector')
sys.path.insert(0, r'C:\aios\quant_backtest')

import feishu_bot_ws
feishu_bot_ws.main()
