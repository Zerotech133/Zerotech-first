import io,json,threading,unittest,importlib
from http.client import HTTPConnection
from unittest.mock import patch
web_chat=importlib.import_module('AI.111111.web_chat')
class R:
 def __init__(self,b):self.b=io.BytesIO(b)
 def __enter__(self):return self
 def __exit__(self,*a):pass
 def read(self,n=-1):return self.b.read(n)
class T(unittest.TestCase):
 @classmethod
 def setUpClass(c): c.s=web_chat.make_server('127.0.0.1',0);threading.Thread(target=c.s.serve_forever,daemon=True).start();c.p=c.s.server_address[1]
 @classmethod
 def tearDownClass(c):c.s.shutdown();c.s.server_close()
 def req(self,m,p,b=None):
  x=HTTPConnection('127.0.0.1',self.p);x.request(m,p,json.dumps(b) if b is not None else None,{'Content-Type':'application/json'} if b is not None else {});r=x.getresponse();d=r.read();x.close();return r,d
 def test_page(self):r,d=self.req('GET','/');self.assertEqual(r.status,200);self.assertIn('AI 聊天',d.decode());self.assertIn('qwen3.5:9b',d.decode())
 @patch.object(web_chat,'urlopen')
 def test_stream(self,u):
  b=b'{"message":{"content":"a"}}\n{"done":true}\n';u.return_value=R(b);r,d=self.req('POST','/api/chat',{'message':'hi'});self.assertEqual(r.status,200);self.assertEqual(d,b);self.assertTrue(json.loads(u.call_args.args[0].data)['stream'])
 def test_bad(self):r,d=self.req('POST','/api/chat',{'message':''});self.assertEqual(r.status,400)
 @patch.object(web_chat,'urlopen',side_effect=OSError('secret'))
 def test_error(self,u):r,d=self.req('POST','/api/chat',{'message':'x'});self.assertEqual(r.status,502);self.assertNotIn('secret',d.decode())
 def test_bind(self):self.assertEqual(web_chat.PORT,8899);self.assertRaises(ValueError,web_chat.make_server,'0.0.0.0',0)
if __name__=='__main__':unittest.main()
