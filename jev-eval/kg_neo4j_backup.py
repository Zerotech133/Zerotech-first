# -*- coding: utf-8 -*-
r"""kg_neo4j_backup.py — neo4j-kg 周备停库 dump（K-STOCK-075 案 A 变体，2026-10-10）

背景：neo4j-kg 为 5.26.31 community（无在线 dump），named volume 无法
文件级热拷。拍板方案：每周六 05:30 写入空窗停库 dump，窗口 5-10 分钟。

流程：
  1. docker stop neo4j-kg（超时 120s 强杀保护）
  2. 临时容器（同镜像 neo4j:5-community）挂 volume 执行
     neo4j-admin database dump neo4j --to-path=/backups
  3. docker start neo4j-kg（无论 dump 成败都启动——库在优先）
  4. 轮转保留最近 KEEP 份
日志：C:\Temp\kg_neo4j_backup.log（追加）
告警：成败均推飞书（复用 news_collector.news_ai.push_message，失败静默）
退出码：0=成功；1=dump 失败；2=stop/start 异常
"""
import subprocess
import sys
from datetime import datetime

CONTAINER = "neo4j-kg"
IMAGE = "neo4j:5-community"
VOLUME = "neo4j-kg-data"
BACKUP_DIR = r"E:\KGStockAnalysis\backups\neo4j"
LOG = r"C:\Temp\kg_neo4j_backup.log"
KEEP = 4
NEWS_COLLECTOR = r"E:\老田的AI助手\news_collector"


def log(msg):
    line = "%s  %s" % (datetime.now().strftime("%F %T"), msg)
    try:
        open(LOG, "a", encoding="utf-8").write(line + "\n")
    except OSError:
        pass
    print(line)


def sh(args, timeout=600):
    return subprocess.run(args, capture_output=True, text=True,
                          timeout=timeout, encoding="utf-8", errors="replace")


def push(msg):
    """飞书告警（失败静默，不影响退出码）。"""
    try:
        sys.path.insert(0, NEWS_COLLECTOR)
        import news_ai
        news_ai.push_message(msg)
    except Exception as e:
        log("push FAIL %s" % str(e)[:120])


def main():
    date_tag = datetime.now().strftime("%Y%m%d")
    log("=== start ===")
    # 1. stop
    r = sh(["docker", "stop", "-t", "120", CONTAINER], timeout=180)
    if r.returncode != 0:
        log("FATAL stop rc=%d %s" % (r.returncode, r.stderr[:200]))
        push("【Neo4j 周备失败】%s stop 失败 rc=%d，库可能未停，请立即检查"
             % (CONTAINER, r.returncode))
        return 2
    log("stopped")
    # 2. dump（临时容器挂 volume，同镜像）
    r = sh(["docker", "run", "--rm", "-v", "%s:/data" % VOLUME,
            "-v", "%s:/backups" % BACKUP_DIR, IMAGE,
            "neo4j-admin", "database", "dump", "neo4j",
            "--to-path=/backups"], timeout=900)
    dump_ok = r.returncode == 0
    log("dump rc=%d %s" % (r.returncode, (r.stdout or r.stderr)[-200:]))
    # 3. start（无论成败，库优先）
    r2 = sh(["docker", "start", CONTAINER], timeout=180)
    if r2.returncode != 0:
        log("FATAL start rc=%d %s" % (r2.returncode, r2.stderr[:200]))
        push("【Neo4j 周备严重】%s 启动失败 rc=%d，图服务不可用，请立即处理！"
             % (CONTAINER, r2.returncode))
        return 2
    log("restarted")
    if not dump_ok:
        push("【Neo4j 周备失败】dump rc=%d（库已恢复运行），详见 %s"
             % (r.returncode, LOG))
        return 1
    # 4. 重命名（--to-path 固定输出 neo4j.dump）+ 轮转保留最近 KEEP 份
    import os
    raw = os.path.join(BACKUP_DIR, "neo4j.dump")
    target = os.path.join(BACKUP_DIR, "neo4j-%s.dump" % date_tag)
    if os.path.exists(raw):
        if os.path.exists(target):
            os.remove(target)
        os.rename(raw, target)
    dumps = sorted(
        (f for f in os.listdir(BACKUP_DIR) if f.endswith(".dump")),
        reverse=True)
    for old in dumps[KEEP:]:
        try:
            os.remove(os.path.join(BACKUP_DIR, old))
            log("rotated %s" % old)
        except OSError as e:
            log("rotate FAIL %s %s" % (old, str(e)[:100]))
    size = os.path.getsize(target) if os.path.exists(target) else 0
    log("OK %s (%.1f MB), kept %d" % (target, size / 1048576, min(len(dumps), KEEP)))
    push("【Neo4j 周备 OK】%s (%.1f MB)，库已恢复" % (date_tag, size / 1048576))
    return 0


if __name__ == "__main__":
    sys.exit(main())
