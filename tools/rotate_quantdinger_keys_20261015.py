# -*- coding: utf-8 -*-
"""quantdinger 密钥轮换（P0 收口 2026-10-15，用户拍板）。

backend.env 中 SECRET_KEY / CREDENTIAL_ENCRYPTION_KEY / ADMIN_PASSWORD
三个键的实值替换为新随机值（secrets 模块）。旧文件整体备份至 %TEMP%。
全程不打印任何旧/新值——新值只在文件里，使用时自行查看。

用法：python tools/rotate_quantdinger_keys_20261015.py
回退：用 %TEMP% 下备份覆盖回去（仅当轮换破坏了某个在跑服务时才需要；
该项目零部署，预期无连带）。
"""
import base64
import os
import re
import secrets
import shutil
import sys
import tempfile
import time

ENV_FP = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                      "..", "quantdinger", "backend.env")
ENV_FP = os.path.normpath(ENV_FP)
BACKUP_DIR = os.path.join(tempfile.gettempdir(),
                          "quantdinger_key_backup_20261015")


def new_secret(key_name):
    if key_name == "CREDENTIAL_ENCRYPTION_KEY":
        # Fernet 兼容：32 字节 urlsafe-base64
        return base64.urlsafe_b64encode(os.urandom(32)).decode("ascii")
    if key_name == "ADMIN_PASSWORD":
        return secrets.token_urlsafe(16)
    return secrets.token_hex(32)          # SECRET_KEY 等通用


def main():
    if not os.path.exists(ENV_FP):
        print("FATAL: env not found: %s" % ENV_FP)
        return 2
    os.makedirs(BACKUP_DIR, exist_ok=True)
    snap = os.path.join(BACKUP_DIR, "backend.env.%s"
                        % time.strftime("%Y%m%d_%H%M%S"))
    shutil.copy2(ENV_FP, snap)
    print("backup -> %s (%d bytes)" % (snap, os.path.getsize(snap)))

    with open(ENV_FP, encoding="utf-8") as f:
        lines = f.read().splitlines(keepends=True)

    targets = ("SECRET_KEY", "CREDENTIAL_ENCRYPTION_KEY", "ADMIN_PASSWORD")
    replaced = []
    pat = re.compile(r"^(\s*)(SECRET_KEY|CREDENTIAL_ENCRYPTION_KEY|"
                     r"ADMIN_PASSWORD)(\s*=\s*)([^\r\n]+)(\r?\n?)$")
    for i, line in enumerate(lines):
        m = pat.match(line)
        if not m or m.group(2) not in targets:
            continue
        indent, key, sep, _, eol = m.groups()
        lines[i] = indent + key + sep + new_secret(key) + (eol or "\n")
        replaced.append("line %d: %s" % (i + 1, key))

    if len(replaced) < len(targets):
        missing = set(targets) - {r.split(": ")[1] for r in replaced}
        print("FATAL: not all keys found, missing=%s, no write." % missing)
        return 3

    tmp = ENV_FP + ".tmp_rotate"
    with open(tmp, "w", encoding="utf-8") as f:
        f.write("".join(lines))
    os.replace(tmp, ENV_FP)
    for r in replaced:
        print("rotated %s" % r)
    print("OK: %d keys rotated, plaintext backup kept at %s"
          % (len(replaced), BACKUP_DIR))
    return 0


if __name__ == "__main__":
    sys.exit(main())
