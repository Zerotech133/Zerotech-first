# -*- coding: utf-8 -*-
"""
DFlash2 模型多连接下载器（ModelScope 匿名限速 ~1MB/s，多连接可叠加提速）
- 预分配目标文件，8 连接分段写入（os.pwrite 语义：seek+write），无需拼接
- 断点续传：进度状态存 .state.json，重启脚本即从断点继续
- 完成后 sha256 校验，结果写入 download_result.json
用法:
  python download_dflash2_hybrid.py            # 下载
  python download_dflash2_hybrid.py --launcher # 以分离方式拉起自身后退出
"""
import os
import sys
import json
import time
import hashlib
import urllib.request
import threading
import subprocess
from concurrent.futures import ThreadPoolExecutor

# v2 修复: 多线程共用文件句柄时 seek+write 存在竞态（v1 主文件 sha256 不匹配的根因）。
# Windows 无 os.pwrite，改为每个 worker 线程持有独立文件句柄（CreateFile 共享写），
# 各自 seek+write 只碰自己的分段区间，无共享游标

FILES = [
    {
        "name": "Qwen3.8-27B-ROCmFP4-FAST.gguf",
        "url": "https://www.modelscope.cn/models/shoujiekeji/Qwen3.8-27B-DFlash2-GGUF/resolve/master/Qwen3.8-27B-ROCmFP4-FAST.gguf",
        "size": 14562236384,
        "sha256": "fb89c78d2be91cdb68eaaaa45b1270710bf34aa721dc1f0b9e3aa7b98d2e1da9",
    },
    {
        "name": "Qwen3.8-27B-DFlash2-IQ4_XS.gguf",
        "url": "https://www.modelscope.cn/models/shoujiekeji/Qwen3.8-27B-DFlash2-GGUF/resolve/master/Qwen3.8-27B-DFlash2-IQ4_XS.gguf",
        "size": 1038313376,
        "sha256": "11c7848014bd68040a42837b381bbefff5d0acc22cf20b6055a48d560c834445",
    },
]
DEST_DIR = r"C:\LingLong\NovaStudio\NovaMax\data\models_dir\llm\shoujiekeji\Qwen3.8-27B-DFlash2-GGUF"
CONN = 8
CHUNK = 4 * 1024 * 1024
PART = 32 * 1024 * 1024
RESULT_JSON = os.path.join(DEST_DIR, "download_result.json")
OPENER = urllib.request.build_opener(urllib.request.ProxyHandler({}))  # 直连

os.makedirs(DEST_DIR, exist_ok=True)
_logf = open(os.path.join(DEST_DIR, "download.log"), "a", encoding="utf-8")
_logf.write("\n==== %s launcher pid log start ====\n" % time.strftime("%F %T"))
_logf.flush()


def log(msg):
    line = "[%s] %s" % (time.strftime("%F %T"), msg)
    _logf.write(line + "\n")
    _logf.flush()


def http_read(url, start, end):
    """发起 Range 请求返回响应对象（含重试）"""
    last = None
    for attempt in range(6):
        try:
            req = urllib.request.Request(url, headers={"Range": "bytes=%d-%d" % (start, end)})
            return OPENER.open(req, timeout=60)
        except Exception as e:
            last = e
            time.sleep(min(3 * (attempt + 1), 15))
    raise RuntimeError("range request failed: %s" % last)


def sha256_of(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        while True:
            b = f.read(8 * 1024 * 1024)
            if not b:
                break
            h.update(b)
    return h.hexdigest()


def download_file(meta):
    name, url, total = meta["name"], meta["url"], meta["size"]
    dest = os.path.join(DEST_DIR, name)
    state_path = dest + ".state.json"

    # 断点状态存在 → 一律续传（文件未下完时 sha 必然不匹配，不能据此清零）
    if os.path.exists(state_path):
        with open(state_path, "r", encoding="utf-8") as f:
            state = json.load(f)
        if state.get("size") != total:
            state = None
    elif os.path.exists(dest) and os.path.getsize(dest) == total:
        # 无断点状态且文件已满尺寸 → 才做完整性判定
        log("%s: exists, checking sha256..." % name)
        if sha256_of(dest) == meta["sha256"]:
            log("%s: sha256 OK, skip" % name)
            return {"name": name, "ok": True, "skipped": True}
        log("%s: sha256 MISMATCH, redownload from scratch" % name)
        if os.path.exists(dest):
            os.remove(dest)
        state = None
    else:
        state = None
    if state is None:
        parts = []
        p = 0
        while p < total:
            parts.append({"start": p, "end": min(p + PART, total) - 1, "done": 0})
            p += PART
        state = {"size": total, "url": url, "parts": parts}
        # 预分配
        with open(dest, "wb") as f:
            f.truncate(total)

    parts = state["parts"]
    lock = threading.Lock()

    def save_state():
        for attempt in range(3):
            try:
                with lock:
                    tmp = state_path + ".tmp"
                    with open(tmp, "w", encoding="utf-8") as f:
                        json.dump(state, f)
                    os.replace(tmp, state_path)
                return
            except PermissionError:
                time.sleep(0.5 * (attempt + 1))  # 索引/杀软瞬时锁，丢一次检查点无妨
            except Exception:
                return

    def worker(idx):
        part = parts[idx]
        # 韧性结构：任何瞬时异常（网络/句柄/状态文件锁）都不退出，
        # 记录后 sleep 重试，从 start+done 断点继续
        fh = None
        try:
            while part["done"] < (part["end"] - part["start"] + 1):
                try:
                    if fh is None:
                        fh = open(dest, "r+b")  # 每线程独立句柄，只写自己的分段
                    pos = part["start"] + part["done"]
                    want = min(CHUNK, part["end"] - pos + 1)
                    resp = http_read(url, pos, pos + want - 1)
                    with resp:
                        while part["done"] < (part["end"] - part["start"] + 1):
                            chunk = resp.read(min(1024 * 1024, part["end"] - (part["start"] + part["done"]) + 1))
                            if not chunk:
                                break
                            fh.seek(pos)
                            fh.write(chunk)
                            pos += len(chunk)
                            with lock:
                                part["done"] += len(chunk)
                            save_state()
                except Exception as e:
                    log("part %d transient: %r, retrying" % (idx, e))
                    time.sleep(2 + idx % 5)
                    try:
                        if fh:
                            fh.close()
                    except Exception:
                        pass
                    fh = None
        except Exception as e:
            log("part %d fatal: %r" % (idx, e))
        finally:
            try:
                if fh:
                    fh.close()
            except Exception:
                pass

    total_done = sum(p["done"] for p in parts)
    log("%s: resume at %.2f/%.2f GB (%d parts)" % (name, total_done / 1e9, total / 1e9, len(parts)))
    t0 = time.time()
    last_report = total_done

    with ThreadPoolExecutor(max_workers=CONN) as ex:
        futures = [ex.submit(worker, i) for i in range(len(parts))]
        while True:
            time.sleep(10)
            cur = sum(p["done"] for p in parts)
            speed = (cur - last_report) / 10 / 1e6
            last_report = cur
            log("%s: %.2f/%.2f GB (%.1f%%) %.1f MB/s" % (
                name, cur / 1e9, total / 1e9, cur * 100.0 / total, speed))
            if all(p["done"] >= p["end"] - p["start"] + 1 for p in parts):
                break
        for f in futures:
            f.result()

    elapsed = time.time() - t0
    log("%s: download finished in %.0fs, verifying sha256..." % (name, elapsed))

    ok = sha256_of(dest) == meta["sha256"]
    log("%s: sha256 %s" % (name, "OK" if ok else "MISMATCH"))
    return {"name": name, "ok": ok, "elapsed_s": round(elapsed)}


def main():
    results = []
    for meta in FILES:
        try:
            results.append(download_file(meta))
        except Exception as e:
            log("FATAL %s: %r" % (meta["name"], e))
            results.append({"name": meta["name"], "ok": False, "error": repr(e)})
    with open(RESULT_JSON, "w", encoding="utf-8") as f:
        json.dump({"finished_at": time.strftime("%F %T"), "results": results}, f, ensure_ascii=False, indent=1)
    log("all done, result -> " + RESULT_JSON)


if __name__ == "__main__":
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    plan_idx = None
    if "--plan" in sys.argv:
        plan_idx = sys.argv.index("--plan")
    if plan_idx is not None:
        # 计划模式: --plan <json>  ->  {"dest_dir":..., "files":[{name,url,size,sha256}]}
        plan = json.load(open(sys.argv[plan_idx + 1], encoding="utf-8"))
        DEST_DIR = plan["dest_dir"]
        FILES = plan["files"]
        RESULT_JSON = os.path.join(DEST_DIR, "download_result.json")
        os.makedirs(DEST_DIR, exist_ok=True)
        _logf = open(os.path.join(DEST_DIR, "download.log"), "a", encoding="utf-8")
    if "--launcher" in sys.argv:
        # 以 DETACHED + BREAKAWAY 方式拉起自身（脱离本会话 Job），随后立即退出
        flags = 0x00000008 | 0x08000000 | 0x00000200  # DETACHED_PROCESS | CREATE_NEW_PROCESS_GROUP | BREAKAWAY_FROM_JOB
        py = sys.executable
        subprocess.Popen([py, os.path.abspath(__file__)], creationflags=flags,
                         cwd=DEST_DIR, close_fds=True,
                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                         stdin=subprocess.DEVNULL)
        print("detached downloader launched")
        sys.exit(0)
    main()
