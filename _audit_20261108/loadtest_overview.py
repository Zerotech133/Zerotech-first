# -*- coding: utf-8 -*-
"""portal 8091 压测基线：/api/overview 冷启动对 /api/health 的连带阻塞。

测什么：
  ① 稳态：只打 /api/health，取 p50/p95/max —— 服务空闲时的正常水位；
  ② 尖峰：等 overview 缓存过期（CACHE_TTL=20s）后，同时打 6 个 overview +
     6 个 health —— 旧实现持共享 _lock 重建 12s，health 会被一起堵住；
     新实现 health 不应受影响。

用法：
  python loadtest_overview.py [--port 8091] [--threads 6] [--reps 10]
                             [--wait-ttl 22] [--out result.json]
"""
import argparse
import http.client
import json
import statistics
import threading
import time

_PORTS = {}


def _conn(port):
    c = _PORTS.get((port, threading.get_ident()))
    if c is None:
        c = http.client.HTTPConnection("127.0.0.1", port, timeout=180)
        _PORTS[(port, threading.get_ident())] = c
    return c


def _one(port, path, out, barrier=None):
    c = _conn(port)
    if barrier is not None:
        barrier.wait()
    t0 = time.perf_counter()
    try:
        c.request("GET", path)
        r = c.getresponse()
        body = r.read()
        out.append((path, r.status, time.perf_counter() - t0, len(body)))
    except Exception as exc:  # noqa: BLE001
        out.append((path, "ERR:%s" % type(exc).__name__, time.perf_counter() - t0, 0))
        global _conn_drop
        try:
            c.close()
        except Exception:
            pass
        _PORTS.pop((port, threading.get_ident()), None)


_conn_drop = 0


def _stats(rows):
    lat = sorted(r[2] for r in rows)
    if not lat:
        return {}
    def q(p):
        k = max(0, min(len(lat) - 1, int(round(p * (len(lat) - 1)))))
        return round(lat[k], 3)
    return {
        "n": len(lat),
        "status": sorted({str(r[1]) for r in rows}),
        "p50_s": q(0.50), "p95_s": q(0.95), "max_s": round(lat[-1], 3),
        "mean_s": round(statistics.fmean(lat), 3),
        "bytes": sorted({r[3] for r in rows})[:3],
    }


def steady(port, threads, reps, path):
    out, ts = [], []
    def worker():
        for _ in range(reps):
            _one(port, path, out)
    ths = [threading.Thread(target=worker) for _ in range(threads)]
    t0 = time.perf_counter()
    for t in ths:
        t.start()
    for t in ths:
        t.join()
    return _stats(out), round(time.perf_counter() - t0, 2)


def stampede(port, threads):
    """同一瞬间：一半打 overview（触发冷建），一半打 health。"""
    out = []
    barrier = threading.Barrier(threads)
    paths = ["/api/overview"] * (threads // 2) + ["/api/health"] * (threads - threads // 2)
    def worker(p):
        _one(port, p, out, barrier=barrier)
    ths = [threading.Thread(target=worker, args=(p,)) for p in paths]
    t0 = time.perf_counter()
    for t in ths:
        t.start()
    for t in ths:
        t.join()
    wall = round(time.perf_counter() - t0, 2)
    o = [r for r in out if r[0] == "/api/overview"]
    h = [r for r in out if r[0] == "/api/health"]
    return {"overview": _stats(o), "health": _stats(h), "wall_s": wall}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=8091)
    ap.add_argument("--threads", type=int, default=6)
    ap.add_argument("--reps", type=int, default=10)
    ap.add_argument("--wait-ttl", type=float, default=22.0)
    ap.add_argument("--out", default="")
    a = ap.parse_args()

    print("[1/3] 稳态 /api/health：%d 线程 x %d 次" % (a.threads, a.reps))
    s_health, wall = steady(a.port, a.threads, a.reps, "/api/health")
    print("     ", s_health, "wall=%ss" % wall)

    print("[2/3] 等 overview 缓存过期 %.0fs（CACHE_TTL=20）" % a.wait_ttl)
    time.sleep(a.wait_ttl)

    print("[3/3] 冷启动尖峰：%d 并发（overview 与 health 同时打）" % a.threads)
    sp = stampede(a.port, a.threads)
    print("      overview:", sp["overview"])
    print("      health  :", sp["health"])
    print("      墙钟:", sp["wall_s"], "s")

    res = {"port": a.port, "threads": a.threads, "reps": a.reps,
           "steady_health": s_health, "steady_wall_s": wall,
           "stampede": sp, "at": time.strftime("%Y-%m-%d %H:%M:%S")}
    if a.out:
        with open(a.out, "w", encoding="utf-8") as f:
            json.dump(res, f, ensure_ascii=False, indent=1)
        print("写入", a.out)

    h95 = sp["health"].get("p95_s")
    o_max = sp["overview"].get("max_s")
    print()
    print("判定：冷启期间 health p95 = %ss（>1s 说明被 overview 连坐）" % h95)
    print("      overview 冷启 max = %ss" % o_max)


if __name__ == "__main__":
    main()
