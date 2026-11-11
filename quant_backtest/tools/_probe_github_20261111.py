# -*- coding: utf-8 -*-
"""分层探测 GitHub 可用性：TCP → TLS → git smart-http（receive-pack 匿名探测）。"""
import socket
import ssl
import time

t = time.time()
try:
    s = socket.create_connection(("github.com", 443), timeout=8)
    print("TCP OK %.0fms" % ((time.time() - t) * 1000))
    ctx = ssl.create_default_context()
    ss = ctx.wrap_socket(s, server_hostname="github.com")
    print("TLS OK", ss.version())
    req = (
        b"GET /Zerotech133/Zerotech-first.git/info/refs?service=git-receive-pack "
        b"HTTP/1.1\r\nHost: github.com\r\nUser-Agent: git/2.0\r\n"
        b"Connection: close\r\n\r\n"
    )
    ss.send(req)
    data = ss.recv(300)
    first = data.split(b"\r\n")[0]
    print("HTTP:", first.decode("latin-1"))
    # 401 = 需要认证（链路通）；200/302 也算通；空/超时 = 断
    ss.close()
except Exception as e:
    print("FAIL:", type(e).__name__, str(e)[:150])
