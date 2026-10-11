"""127.0.0.1 上的 Ollama NDJSON 聊天网页。"""
import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.request import Request, urlopen
from urllib.error import HTTPError, URLError
HOST='127.0.0.1'; PORT=8899; OLLAMA_URL='http://127.0.0.1:11434/api/chat'; DEFAULT_MODEL='qwen3.5:9b'; MODELS=('qwen3.5:9b','qwen3.5:4b')
HTML='''<!doctype html><meta charset="utf-8"><title>AI 聊天</title><h1>AI 聊天</h1><label>模型 <select id="model">__MODELS__</select></label><br><textarea id="message" placeholder="输入消息"></textarea><button id="send">发送</button><pre id="out"></pre><script>send.onclick=async()=>{let m=message.value.trim();if(!m){out.textContent="请输入消息";return}out.textContent="思考中...";send.disabled=true;try{let r=await fetch("/api/chat",{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify({model:model.value,message:m})});if(!r.ok){out.textContent=await r.text();return}out.textContent="";let rd=r.body.getReader(),d=new TextDecoder(),b="";for(;;){let x=await rd.read();if(x.done)break;b+=d.decode(x.value);let ls=b.split("\\n");b=ls.pop();for(let l of ls)if(l.trim())try{let j=JSON.parse(l);out.textContent+=(j.message?.content||j.response||"")}catch{}}}catch{out.textContent="无法连接 Ollama，请确认服务已启动。"}finally{send.disabled=false}}</script>'''
def _error(s): return json.dumps({'error':s},ensure_ascii=False).encode()
def _payload(h):
 try:n=int(h.headers.get('Content-Length','0')); p=json.loads(h.rfile.read(n).decode())
 except (ValueError,UnicodeDecodeError,json.JSONDecodeError): raise ValueError('请求格式无效')
 if not isinstance(p,dict) or not isinstance(p.get('message'),str) or not p['message'].strip(): raise ValueError('message 不能为空')
 if p.get('model',DEFAULT_MODEL) not in MODELS: raise ValueError('model 不受支持')
 return {'model':p.get('model',DEFAULT_MODEL),'messages':[{'role':'user','content':p['message'].strip()}],'stream':True}
class ChatHandler(BaseHTTPRequestHandler):
 def log_message(self,*a): pass
 def do_GET(self):
  if self.path!='/': self.send_error(404); return
  b=HTML.replace('__MODELS__',''.join(f'<option>{m}</option>' for m in MODELS)).encode(); self.send_response(200); self.send_header('Content-Type','text/html; charset=utf-8'); self.send_header('Content-Length',str(len(b))); self.end_headers(); self.wfile.write(b)
 def do_POST(self):
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
 def _err(self,status,msg):
  b=_error(msg); self.send_response(status); self.send_header('Content-Type','application/json; charset=utf-8'); self.send_header('Content-Length',str(len(b))); self.end_headers(); self.wfile.write(b)
def make_server(host=HOST,port=PORT):
 if host!=HOST: raise ValueError('服务只能绑定 127.0.0.1')
 return ThreadingHTTPServer((host,port),ChatHandler)
def main():
 s=make_server(); print(f'AI 聊天服务已启动：http://{HOST}:{PORT}'); s.serve_forever()
if __name__=='__main__': main()
