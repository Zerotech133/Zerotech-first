# -*- coding: utf-8 -*-
"""修复 fastembed 快照目录：hub symlink 在 Windows 无权限时静默失败，
blobs 有真身但 snapshots 空/损坏。本脚本按 repo 文件清单手动建立副本。"""
import os
import shutil
import sys

CACHE = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                     ".models", "fastembed")
REPO_DIR = os.path.join(CACHE, "models--Qdrant--bge-small-zh-v1.5")

# 先以环境变量方式取文件清单（避免硬编码），失败则用内置最小清单
NEEDED = [
    "config.json",
    "model_optimized.onnx",
    "ort_config.json",
    "special_tokens_map.json",
    "tokenizer.json",
    "tokenizer_config.json",
    "vocab.txt",
]


def blob_by_size():
    """{size: blob_path}——用大小匹配（各文件大小互异时可靠）。"""
    blobs = os.path.join(REPO_DIR, "blobs")
    out = {}
    for f in os.listdir(blobs):
        p = os.path.join(blobs, f)
        if os.path.isfile(p):
            out.setdefault(os.path.getsize(p), p)
    return out


def main():
    from huggingface_hub import HfApi
    api = HfApi(endpoint=os.environ.get("HF_ENDPOINT",
                                        "https://hf-mirror.com"))
    info = api.model_info("Qdrant/bge-small-zh-v1.5",
                          files_metadata=True)
    # filename -> expected size
    want = {s.rfilename: s.size for s in info.siblings if s.size}
    snap_root = os.path.join(REPO_DIR, "snapshots")
    snaps = [d for d in os.listdir(snap_root)
             if os.path.isdir(os.path.join(snap_root, d))]
    snap = os.path.join(snap_root, snaps[0]) if snaps else None
    if snap is None:
        # 无快照目录则按 refs/main 的 commit 建一个
        ref = open(os.path.join(REPO_DIR, "refs", "main")
                   ).read().strip()
        snap = os.path.join(snap_root, ref)
        os.makedirs(snap, exist_ok=True)
    sizes = blob_by_size()
    ok, bad = [], []
    for fname in NEEDED:
        size = want.get(fname)
        dst = os.path.join(snap, fname)
        if os.path.isfile(dst) and os.path.getsize(dst) == size:
            ok.append(fname + " (已有)")
            continue
        src = sizes.get(size)
        if src is None:
            bad.append("%s (blobs 无 %s 字节对应物)" % (fname, size))
            continue
        # 覆盖损坏的 0 字节文件/断链
        if os.path.lexists(dst):
            os.remove(dst)
        shutil.copyfile(src, dst)
        ok.append("%s (%d B)" % (fname, size))
    print("SNAPSHOT:", snap)
    for line in ok:
        print("  ok -", line)
    for line in bad:
        print("  BAD -", line)
    if bad:
        sys.exit(1)
    print("快照副本修复完成")


if __name__ == "__main__":
    main()
