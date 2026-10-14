# -*- coding: utf-8 -*-
"""K-STOCK-368 换绑验证：fallback creds + 新 app 发消息探测"""
import subprocess, sys, json

PY = r"C:\Program Files\Python311\python.exe"
WT = r"E:\老田的AI助手\.worktrees\K-STOCK-368\jev-eval"

# 1) fallback（无 env → 老 app）
code_fb = (
    "import sys; sys.path.insert(0, r'" + WT + "'); "
    "import jev_common as jc; c = jc._load_bot_creds(); "
    "print('fallback app_id:', c[1] if c else None)"
)
p = subprocess.run([PY, "-X", "utf8", "-c", code_fb], capture_output=True, text=True, encoding="utf-8", timeout=60)
print((p.stdout or p.stderr).strip())

# 2) env 覆盖 → 新 app
code_env = (
    "import os, sys; os.environ['JEV_CARD_APP_ID']='cli_aa469ee72af85bc9'; "
    "os.environ['JEV_CARD_APP_SECRET']='bgzAZ9IxySu7PfXJRLRnHbyPXIiTqQM5'; "
    "sys.path.insert(0, r'" + WT + "'); "
    "import jev_common as jc; c = jc._load_bot_creds(); "
    "print('env-override app_id:', c[1] if c else None)"
)
p = subprocess.run([PY, "-X", "utf8", "-c", code_env], capture_output=True, text=True, encoding="utf-8", timeout=60)
print((p.stdout or p.stderr).strip())

# 3) 新 app 发消息探测（union_id；权限是否已开）
code_send = """
import sys, json, urllib.request
sys.path.insert(0, r'E:\\老田的AI助手\\news_collector')
from feishu_client import get_tat
tat = get_tat('cli_aa469ee72af85bc9', 'bgzAZ9IxySu7PfXJRLRnHbyPXIiTqQM5')
body = {'receive_id': 'on_73852db6352a7f834ba0802060ae5aee', 'msg_type': 'text',
        'content': json.dumps({'text': '【告警卡片助手】换绑链路测试（K-STOCK-368）'})}
req = urllib.request.Request('https://open.feishu.cn/open-apis/im/v1/messages?receive_id_type=union_id',
    data=json.dumps(body).encode(), headers={'Authorization': 'Bearer ' + tat, 'Content-Type': 'application/json'}, method='POST')
try:
    r = json.loads(urllib.request.urlopen(req, timeout=15).read())
    print('SEND code:', r.get('code'), '| message_id:', (r.get('data') or {}).get('message_id'))
except urllib.error.HTTPError as e:
    print('SEND HTTP', e.code, json.loads(e.read()).get('msg', '')[:120])
"""
p = subprocess.run([PY, "-X", "utf8", "-c", code_send], capture_output=True, text=True, encoding="utf-8", timeout=60)
print((p.stdout or p.stderr).strip()[:300])
