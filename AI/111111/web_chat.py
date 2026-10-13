"""127.0.0.1 上的 Ollama NDJSON 聊天网页。"""
import json
from pathlib import Path
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.request import Request, urlopen
from urllib.error import HTTPError, URLError
try:
 from .rag_web_backend import RagUnavailable, RagWebService
 from .web_ui import render_chat_page
 from .model_routing import CHAT_CONTEXT_CHAR_BUDGET, CHAT_MODELS, CLOUD_CHAT_MODELS, DEFAULT_CHAT_MODEL, ModelRoutingError, select_model
 from .chat_profiles import ProfileError, get_profile, list_profile_specs
 from .cloud_provider import ProviderAuthError, ProviderNotConfigured, ProviderProtocolError, ProviderUnavailable, ZhipuProvider
except ImportError:
 from rag_web_backend import RagUnavailable, RagWebService
 from web_ui import render_chat_page
 from model_routing import CHAT_CONTEXT_CHAR_BUDGET, CHAT_MODELS, CLOUD_CHAT_MODELS, DEFAULT_CHAT_MODEL, ModelRoutingError, select_model
 from chat_profiles import ProfileError, get_profile, list_profile_specs
 from cloud_provider import ProviderAuthError, ProviderNotConfigured, ProviderProtocolError, ProviderUnavailable, ZhipuProvider
HOST='127.0.0.1'; PORT=8899; OLLAMA_URL='http://127.0.0.1:11434/api/chat'; DEFAULT_MODEL=DEFAULT_CHAT_MODEL; MODELS=CHAT_MODELS + CLOUD_CHAT_MODELS
STATIC_ROOT = Path(__file__).resolve().parents[2] / 'portal' / 'static'
STATIC_ASSETS = {
    '/static/tokens.css': (STATIC_ROOT / 'tokens.css', 'text/css; charset=utf-8'),
    '/static/nav.js': (STATIC_ROOT / 'nav.js', 'application/javascript; charset=utf-8'),
    '/static/shortscreen.js': (STATIC_ROOT / 'shortscreen.js', 'application/javascript; charset=utf-8'),
}
RAG_SERVICE = None
def _error(s): return json.dumps({'error':s},ensure_ascii=False).encode()
def _payload(h):
 try:n=int(h.headers.get('Content-Length','0')); p=json.loads(h.rfile.read(n).decode())
 except (ValueError,UnicodeDecodeError,json.JSONDecodeError): raise ValueError('请求格式无效')
 if not isinstance(p,dict) or not isinstance(p.get('message'),str) or not p['message'].strip(): raise ValueError('message 不能为空')
 think=p.get('think', False)
 if not isinstance(think,bool): raise ValueError('think 必须是布尔值')
 try: role=get_profile(p.get('profile'))
 except ProfileError as e: raise ValueError(str(e)) from e
 history=p.get('history', [])
 if history is None: history=[]
 if not isinstance(history,list): raise ValueError('history 必须是消息数组')
 clean_history=[]
 for item in history:
  if not isinstance(item,dict) or item.get('role') not in {'user','assistant'}:
   raise ValueError('history 只允许 user/assistant 消息')
  content=item.get('content')
  if not isinstance(content,str) or not content.strip() or len(content)>12000:
   raise ValueError('history 消息内容无效')
  clean_history.append({'role':item['role'],'content':content.strip()})
 bounded=[]; total=0
 for item in reversed(clean_history):
  size=len(item['content'])
  if bounded and total + size > CHAT_CONTEXT_CHAR_BUDGET: break
  if not bounded and size > CHAT_CONTEXT_CHAR_BUDGET:
   item=dict(item, content=item['content'][-CHAT_CONTEXT_CHAR_BUDGET:]); size=len(item['content'])
  bounded.append(item); total += size
 clean_history=list(reversed(bounded))
 requested = p.get('model') or role['model']
 provider = 'ollama'
 if requested in CLOUD_CHAT_MODELS:
  provider, model = 'zhipu', requested.split('/', 1)[1]
 else:
  try: model=select_model('chat', requested).model
  except ModelRoutingError as e: raise ValueError(str(e)) from e
 return {'provider':provider,'model':model,'messages':[{'role':'system','content':role['system_prompt']},*clean_history,{'role':'user','content':p['message'].strip()}],'stream':True,'think':think,'keep_alive':'30m'}
def _rag_payload(h):
 try:n=int(h.headers.get('Content-Length','0')); p=json.loads(h.rfile.read(n).decode())
 except (ValueError,UnicodeDecodeError,json.JSONDecodeError): raise ValueError('请求格式无效')
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
class ChatHandler(BaseHTTPRequestHandler):
 def log_message(self,*a): pass
 def do_GET(self):
  if self.path in STATIC_ASSETS:
   path, content_type = STATIC_ASSETS[self.path]
   try:
    b = path.read_bytes()
   except OSError:
    self.send_error(404); return
   self.send_response(200); self.send_header('Content-Type', content_type); self.send_header('Content-Length', str(len(b))); self.end_headers(); self.wfile.write(b); return
  if self.path!='/': self.send_error(404); return
  b=render_chat_page(MODELS, list_profile_specs(), default_model=DEFAULT_MODEL).encode(); self.send_response(200); self.send_header('Content-Type','text/html; charset=utf-8'); self.send_header('Content-Length',str(len(b))); self.end_headers(); self.wfile.write(b)
 def do_POST(self):
  if self.path=='/api/rag': self._do_rag(); return
  if self.path!='/api/chat': self._err(404,'路径不存在'); return
  try:p=_payload(self)
  except ValueError as e:self._err(400,str(e)); return
  response_started=False
  try:
   if p['provider'] == 'zhipu':
    provider=ZhipuProvider()
    if not provider.configured: raise ProviderNotConfigured('智谱未配置，请设置 GLM_API_KEY 或 ZHIPU_API_KEY')
    events=provider.stream(p['messages'], model=p['model'], think=p['think'])
    self.send_response(200); response_started=True; self.send_header('Content-Type','application/x-ndjson; charset=utf-8'); self.send_header('Cache-Control','no-cache'); self.end_headers()
    for event in events:
     self.wfile.write((json.dumps(event,ensure_ascii=False)+'\n').encode()); self.wfile.flush()
    return
   ollama_payload=dict(p); ollama_payload.pop('provider', None)
   with urlopen(Request(OLLAMA_URL,data=json.dumps(ollama_payload).encode(),headers={'Content-Type':'application/json'},method='POST'),timeout=120) as r:
    self.send_response(200); response_started=True; self.send_header('Content-Type','application/x-ndjson'); self.send_header('Cache-Control','no-cache'); self.end_headers()
    while c:=r.read(8192): self.wfile.write(c); self.wfile.flush()
  except ProviderNotConfigured:
   if not response_started: self._err(503,'智谱未配置，请设置 GLM_API_KEY 或 ZHIPU_API_KEY')
  except ProviderAuthError:
   if not response_started: self._err(502,'智谱鉴权失败，请检查 API key')
  except (ProviderUnavailable,ProviderProtocolError):
   if not response_started: self._err(502,'智谱服务暂不可用')
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
def make_server(host=HOST,port=PORT):
 if host!=HOST: raise ValueError('服务只能绑定 127.0.0.1')
 return ThreadingHTTPServer((host,port),ChatHandler)
def main():
 s=make_server(); print(f'AI 聊天服务已启动：http://{HOST}:{PORT}'); s.serve_forever()
if __name__=='__main__': main()
