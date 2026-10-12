"""127.0.0.1 上的 Ollama NDJSON 聊天网页。"""
import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.request import Request, urlopen
from urllib.error import HTTPError, URLError
try:
 from .rag_web_backend import RagUnavailable, RagWebService
 from .web_ui import render_chat_page
 from .model_routing import CHAT_MODELS, DEFAULT_CHAT_MODEL, ModelRoutingError, select_model
except ImportError:
 from rag_web_backend import RagUnavailable, RagWebService
 from web_ui import render_chat_page
 from model_routing import CHAT_MODELS, DEFAULT_CHAT_MODEL, ModelRoutingError, select_model
HOST='127.0.0.1'; PORT=8899; OLLAMA_URL='http://127.0.0.1:11434/api/chat'; DEFAULT_MODEL=DEFAULT_CHAT_MODEL; MODELS=CHAT_MODELS
RAG_SERVICE = None
def _error(s): return json.dumps({'error':s},ensure_ascii=False).encode()
def _payload(h):
 try:n=int(h.headers.get('Content-Length','0')); p=json.loads(h.rfile.read(n).decode())
 except (ValueError,UnicodeDecodeError,json.JSONDecodeError): raise ValueError('请求格式无效')
 if not isinstance(p,dict) or not isinstance(p.get('message'),str) or not p['message'].strip(): raise ValueError('message 不能为空')
 try: model=select_model('chat', p.get('model',DEFAULT_MODEL)).model
 except ModelRoutingError as e: raise ValueError(str(e)) from e
 return {'model':model,'messages':[{'role':'user','content':p['message'].strip()}],'stream':True}
def _rag_payload(h):
 try:n=int(h.headers.get('Content-Length','0')); p=json.loads(h.rfile.read(n).decode())
 except (ValueError,UnicodeDecodeError,json.JSONDecodeError): raise ValueError('请求格式无效')
 if not isinstance(p,dict) or not isinstance(p.get('message'),str) or not p['message'].strip(): raise ValueError('message 不能为空')
 top_k=p.get('top_k')
 if top_k is not None and (isinstance(top_k,bool) or not isinstance(top_k,int) or not 1 <= top_k <= 8): raise ValueError('top_k 必须是 1 到 8 的整数')
 return {'message':p['message'].strip(),'top_k':top_k}
def _rag_service():
 global RAG_SERVICE
 if RAG_SERVICE is None: RAG_SERVICE=RagWebService()
 return RAG_SERVICE
class ChatHandler(BaseHTTPRequestHandler):
 def log_message(self,*a): pass
 def do_GET(self):
  if self.path!='/': self.send_error(404); return
  b=render_chat_page(MODELS).encode(); self.send_response(200); self.send_header('Content-Type','text/html; charset=utf-8'); self.send_header('Content-Length',str(len(b))); self.end_headers(); self.wfile.write(b)
 def do_POST(self):
  if self.path=='/api/rag': self._do_rag(); return
  if self.path!='/api/chat': self._err(404,'路径不存在'); return
  try:p=_payload(self)
  except ValueError as e:self._err(400,str(e)); return
  response_started=False
  try:
   with urlopen(Request(OLLAMA_URL,data=json.dumps(p).encode(),headers={'Content-Type':'application/json'},method='POST'),timeout=120) as r:
    self.send_response(200); response_started=True; self.send_header('Content-Type','application/x-ndjson'); self.send_header('Cache-Control','no-cache'); self.end_headers()
    while c:=r.read(8192): self.wfile.write(c); self.wfile.flush()
  except (HTTPError,URLError,OSError,TimeoutError):
   if not response_started: self._err(502,'无法连接 Ollama，请确认服务已启动')
 def _do_rag(self):
  try:p=_rag_payload(self)
  except ValueError as e:self._err(400,str(e)); return
  response_started=False
  try:
   for event in _rag_service().stream(p['message'], p['top_k']):
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
