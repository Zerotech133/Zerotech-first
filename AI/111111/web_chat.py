"""127.0.0.1 上的 Ollama NDJSON 聊天网页。"""
import html
import heapq
import json
import re
import sqlite3
import sys
import threading
from contextlib import closing
from datetime import datetime, timedelta, timezone
from pathlib import Path
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.request import Request
from urllib.error import HTTPError, URLError
import logging
try:
 from .rag_web_backend import RagUnavailable, RagWebService
 from .web_ui import render_chat_page
 from .model_routing import CHAT_MODELS, DEFAULT_CHAT_MODEL, ModelRoutingError, select_model
 from .chat_profiles import ProfileError, get_profile, list_profile_specs
 from .a9_supervision import A9Service, StoreError
 from .a9_brief import BriefService
except ImportError:
 from rag_web_backend import RagUnavailable, RagWebService
 from web_ui import render_chat_page
 from model_routing import CHAT_MODELS, DEFAULT_CHAT_MODEL, ModelRoutingError, select_model
 from chat_profiles import ProfileError, get_profile, list_profile_specs
 from a9_supervision import A9Service, StoreError
 from a9_brief import BriefService
# 回环禁代理：Ollama 在 127.0.0.1:11434，系统代理会接管回环请求（见 tools/safe_urlopen.py）
import sys as _sys
from pathlib import Path as _Path
for _base in _Path(__file__).resolve().parents:
    for _cand in (_base, _base / "tools"):
        if (_cand / "safe_urlopen.py").exists():
            _sys.path.insert(0, str(_cand))
            break
    else:
        continue
    break
try:
    from safe_urlopen import safe_urlopen
except ImportError:  # 作为包/仓库根导入时
    from tools.safe_urlopen import safe_urlopen

HOST='127.0.0.1'; PORT=8899; OLLAMA_URL='http://127.0.0.1:11434/api/chat'; DEFAULT_MODEL=DEFAULT_CHAT_MODEL; MODELS=CHAT_MODELS
MAX_HISTORY_CHARS = 12000
MAX_REQUEST_BYTES = 1024 * 1024
DEFAULT_KEEP_ALIVE = '30m'
RAG_SERVICE = None
NEWS_DB_PATH = Path(__file__).resolve().parents[2] / 'news_collector' / 'news.db'
NEWS_LIMIT = 25
CST = timezone(timedelta(hours=8))

def _news_time(value, reference=None):
 value = str(value or '').strip()
 try:
  parsed = datetime.fromisoformat(value.replace('Z', '+00:00'))
  return parsed.replace(tzinfo=CST) if parsed.tzinfo is None else parsed.astimezone(CST)
 except ValueError as exc:
     logging.getLogger(__name__).warning("silent-except %s: %s", "AI/111111/web_chat.py", exc)
 if re.fullmatch(r'\d{2}-\d{2} \d{2}:\d{2}', value) and reference:
  candidates = []
  for year in (reference.year - 1, reference.year, reference.year + 1):
   try: candidate = datetime.strptime(f'{year}-{value}', '%Y-%m-%d %H:%M').replace(tzinfo=CST)
   except ValueError: continue
   if abs(candidate - reference) <= timedelta(days=183): candidates.append(candidate)
  if candidates: return min(candidates, key=lambda candidate: abs(candidate - reference))
 return None

def _news_text(value, limit):
 text = re.sub(r'<[^>]*>', ' ', str(value or ''))
 return re.sub(r'\s+', ' ', html.unescape(text)).strip()[:limit]

def latest_news(db_path=None):
 path = Path(db_path) if db_path is not None else NEWS_DB_PATH
 if not path.is_file():
  return {'status': 'missing', 'message': '新闻库缺失，新闻状态未知', 'latest_at': None, 'updated_at': None, 'items': []}
 latest_stored = None
 now = datetime.now(CST)
 try:
  with closing(sqlite3.connect(path.resolve().as_uri() + '?mode=ro', uri=True, timeout=3)) as db:
   db.execute('PRAGMA query_only=ON')
   rows = db.execute('SELECT source, ts, title, content, created_at FROM news')
   def ranked():
    nonlocal latest_stored
    for index, (source, ts, title, content, created_at) in enumerate(rows):
     stored = _news_time(created_at)
     event = _news_time(ts, stored)
     if stored and (latest_stored is None or stored > latest_stored): latest_stored = stored
     item = {'source': _news_text(source, 100), 'ts': str(ts or ''),
             'title': _news_text(title, 240), 'content': _news_text(content, 280),
             'created_at': str(created_at or ''), 'future_ts': bool(event and event > now)}
     yield ((stored or datetime.min.replace(tzinfo=CST), event or datetime.min.replace(tzinfo=CST), index), item)
   newest = heapq.nlargest(NEWS_LIMIT, ranked(), key=lambda row: row[0])
 except (OSError, sqlite3.Error):
  return {'status': 'unavailable', 'message': '新闻库不可读，新闻状态未知', 'latest_at': None, 'updated_at': None, 'items': []}
 if not newest:
  return {'status': 'empty', 'message': '新闻库为空，暂无新闻记录', 'latest_at': None, 'updated_at': None, 'items': []}
 stale = latest_stored is not None and latest_stored < now - timedelta(hours=48)
 message = ('入库时间不可解析，新闻鲜度未知' if latest_stored is None else
            '最新入库已超过 48 小时，请勿视作实时新闻' if stale else
            '新闻来自本地库，不代表实时核验')
 if any(item['future_ts'] for _, item in newest):
  message += '；含预告/时间待核验条目，不可视为已发布的实时新闻'
 return {'status': 'unknown' if latest_stored is None else 'stale' if stale else 'ok',
         'message': message,
         'latest_at': latest_stored.isoformat(timespec='minutes') if latest_stored else None,
         'updated_at': latest_stored.isoformat(timespec='minutes') if latest_stored else None,
         'items': [item for _, item in newest]}

def _error(s): return json.dumps({'error':s},ensure_ascii=False).encode()
def _read_json(h):
 try:
  n = int(h.headers.get('Content-Length', '0'))
 except (TypeError, ValueError):
  raise ValueError('请求格式无效')
 if n < 0 or n > MAX_REQUEST_BYTES:
  raise ValueError('请求体过大')
 try:
  return json.loads(h.rfile.read(n).decode())
 except (UnicodeDecodeError, json.JSONDecodeError):
  raise ValueError('请求格式无效')
def _trim_history(history):
 kept=[]; used=0
 for item in reversed(history):
  size=len(item['content'])
  if kept and used + size > MAX_HISTORY_CHARS: break
  kept.append(item); used += size
 return list(reversed(kept))
def _payload(h):
 p = _read_json(h)
 if not isinstance(p,dict) or not isinstance(p.get('message'),str) or not p['message'].strip(): raise ValueError('message 不能为空')
 try: role=get_profile(p.get('profile'))
 except ProfileError as e: raise ValueError(str(e)) from e
 history=p.get('history', [])
 if history is None: history=[]
 if not isinstance(history,list) or len(history)>40: raise ValueError('history 必须是最多 40 条消息的数组')
 clean_history=[]
 for item in history:
  if not isinstance(item,dict) or item.get('role') not in {'user','assistant'}:
   raise ValueError('history 只允许 user/assistant 消息')
  content=item.get('content')
  if not isinstance(content,str) or not content.strip() or len(content)>12000:
   raise ValueError('history 消息内容无效')
  clean_history.append({'role':item['role'],'content':content.strip()})
 clean_history = _trim_history(clean_history)
 think = p.get('think', False)
 if not isinstance(think, bool): raise ValueError('think 必须是布尔值')
 try: model=select_model('chat', p.get('model') or role['model']).model
 except ModelRoutingError as e: raise ValueError(str(e)) from e
 return {'model':model,'messages':[{'role':'system','content':role['system_prompt']},*clean_history,{'role':'user','content':p['message'].strip()}],'stream':True,'think':think,'keep_alive':DEFAULT_KEEP_ALIVE}
def _rag_payload(h):
 p = _read_json(h)
 if not isinstance(p,dict) or not isinstance(p.get('message'),str) or not p['message'].strip(): raise ValueError('message 不能为空')
 try: profile=get_profile(p.get('profile'))['id']
 except ProfileError as e: raise ValueError(str(e)) from e
 top_k=p.get('top_k')
 if top_k is not None and (isinstance(top_k,bool) or not isinstance(top_k,int) or not 1 <= top_k <= 8): raise ValueError('top_k 必须是 1 到 8 的整数')
 return {'message':p['message'].strip(),'top_k':top_k,'profile':profile}
def _rag_service():
 global RAG_SERVICE
 if RAG_SERVICE is None: RAG_SERVICE=RagWebService()
 return RAG_SERVICE
class A9HTTPServer(ThreadingHTTPServer):
 def __init__(self, address, handler, a9, interval, brief=None):
  if interval <= 0: raise ValueError('interval 必须为正数')
  super().__init__(address, handler)
  self.a9 = a9
  self.brief = brief if brief is not None else BriefService()
  self._a9_interval = interval
  self._a9_stop = threading.Event()
  self._a9_error = threading.Event()
  self._a9_worker = threading.Thread(target=self._check_a9, daemon=True)
  self._a9_worker.start()

 def _check_a9(self):
  while not self._a9_stop.wait(self._a9_interval):
   try:
    self.a9.check_agents()
    self.a9.list()
   except Exception as exc:
    if not self._a9_error.is_set():
     print(f'A9 后台巡检失败: {type(exc).__name__}', file=sys.stderr)
    self._a9_error.set()
   else:
    self._a9_error.clear()

 def a9_ready(self):
  if self._a9_error.is_set(): raise StoreError('A9 后台巡检失败，请检查服务日志和存储状态')

 def shutdown(self):
  self._a9_stop.set()
  super().shutdown()
  self._a9_worker.join()

 def server_close(self):
  self._a9_stop.set()
  self._a9_worker.join()

class ChatHandler(BaseHTTPRequestHandler):
 def log_message(self,*a): pass
 def _json(self, status, value):
  b=json.dumps(value,ensure_ascii=False).encode(); self.send_response(status); self.send_header('Content-Type','application/json; charset=utf-8'); self.send_header('Content-Length',str(len(b))); self.end_headers(); self.wfile.write(b)
 def do_GET(self):
  if self.path == '/api/news/latest':
   self._json(200, latest_news()); return
  if self.path == '/api/brief/daily':
   try: self._json(200, self.server.brief.build())
   except (OSError, ValueError): self._err(500, '简报生成失败')
   return
  if self.path in ('/api/reminders', '/api/agents'):
   try:
    self.server.a9_ready()
    result=self.server.a9.list() if self.path=='/api/reminders' else self.server.a9.agents()
    self._json(200,result)
   except StoreError as e: self._err(500,str(e))
   return
  if self.path == '/command':
   try: b=(Path(__file__).with_name('command_page.html')).read_bytes()
   except OSError: self._err(500,'参谋台页面不可用'); return
  elif self.path == '/':
   b=render_chat_page(MODELS, list_profile_specs(), default_model=DEFAULT_MODEL).encode()
  else:
   self._err(404,'路径不存在'); return
  self.send_response(200); self.send_header('Content-Type','text/html; charset=utf-8'); self.send_header('Content-Length',str(len(b))); self.end_headers(); self.wfile.write(b)
 def do_POST(self):
  origin=f'http://127.0.0.1:{self.server.server_port}'
  if self.headers.get('Host') != f'127.0.0.1:{self.server.server_port}' or self.headers.get('Origin') not in (None, origin):
   self._err(403,'请求来源不允许'); return
  if self.path in ('/api/reminders', '/api/reminders/ack', '/api/agents/check'):
   try:
    payload=_read_json(self)
    self.server.a9_ready()
    if not isinstance(payload,dict): raise ValueError('请求必须是 JSON 对象')
    if self.path=='/api/reminders': result=self.server.a9.create(payload); status=201
    elif self.path=='/api/reminders/ack':
     if set(payload)!={'id'}: raise ValueError('仅允许 id 字段')
     result=self.server.a9.ack(payload['id']); status=200
    else:
     if payload: raise ValueError('请求必须是空对象')
     result=self.server.a9.check_agents(); status=200
    self._json(status,result)
   except ValueError as e: self._err(400,str(e))
   except KeyError: self._err(404,'提醒不存在')
   except StoreError as e: self._err(500,str(e))
   return
  if self.path=='/api/rag': self._do_rag(); return
  if self.path!='/api/chat': self._err(404,'路径不存在'); return
  try:p=_payload(self)
  except ValueError as e:self._err(400,str(e)); return
  response_started=False
  try:
   with safe_urlopen(Request(OLLAMA_URL,data=json.dumps(p).encode(),headers={'Content-Type':'application/json'},method='POST'),timeout=120) as r:
    self.send_response(200); response_started=True; self.send_header('Content-Type','application/x-ndjson'); self.send_header('Cache-Control','no-cache'); self.end_headers()
    while c:=r.read(8192): self.wfile.write(c); self.wfile.flush()
  except (HTTPError,URLError,OSError,TimeoutError):
   if not response_started: self._err(502,'无法连接 Ollama，请确认服务已启动')
 def _do_rag(self):
  try:p=_rag_payload(self)
  except ValueError as e:self._err(400,str(e)); return
  response_started=False
  try:
    service=_rag_service()
    try:
     events=service.stream(p['message'], p['top_k'], profile=p['profile'])
    except TypeError as e:
     if 'profile' not in str(e): raise
     events=service.stream(p['message'], p['top_k'])
    for event in events:
     if not response_started:
      self.send_response(200); response_started=True; self.send_header('Content-Type','application/x-ndjson; charset=utf-8'); self.send_header('Cache-Control','no-cache'); self.end_headers()
     self.wfile.write((json.dumps(event,ensure_ascii=False)+'\n').encode()); self.wfile.flush()
  except RagUnavailable as e:
   if not response_started:
    status=getattr(e,'status_code',502); msg='知识库暂不可用' if status==503 else '上游模型暂不可用'; self._err(status,msg)
  except (OSError,TimeoutError):
   if not response_started: self._err(502,'上游模型暂不可用')
 def _err(self,status,msg):
  b=_error(msg); self.send_response(status); self.send_header('Content-Type','application/json; charset=utf-8'); self.send_header('Content-Length',str(len(b))); self.end_headers(); self.wfile.write(b)
def make_server(host=HOST,port=PORT, a9=None, interval=30, brief=None):
 if host!=HOST: raise ValueError('服务只能绑定 127.0.0.1')
 return A9HTTPServer((host,port), ChatHandler, a9 if a9 is not None else A9Service(), interval, brief)
def main():
 s=make_server(); print(f'AI 聊天服务已启动：http://{HOST}:{PORT}'); s.serve_forever()
if __name__=='__main__': main()
