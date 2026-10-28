#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""secvault —— L1 凭据保险库（age 优先 / cryptography AES-GCM 兜底）。

背景（工单 K-DATA-20261027-SECVAULT）：
  ``C:\\项目\\_vault\\secrets`` 存放约 32 个明文凭据文件，且被生产代码直读，
  本轮只做「加密副本 + MANIFEST 校验 + exec-env 注入」的 L1 收编，**不原地加密**，
  以免断产线；原地清除明文属 Phase 2（见 docs/ops 轮换操作单）。

设计原则：
  * fail-closed：密钥/recipients/密文/MANIFEST 任一缺失或损坏 → 非 0 退出，
    绝不静默降级、绝不回退明文；
  * 不落明文：``decrypt`` 只写 stdout，``exec-env`` 只注入子进程环境，均不打印值；
  * 后端可切换：便携 ``age``/``age-keygen`` 可用时用 age 收件人加密；缺失时降级为
    ``cryptography`` 的 AES-256-GCM（本机 key 文件 + ACL 收紧），密文自带 magic，
    decrypt/verify 自动识别，MANIFEST 记录 ``cipher`` 字段。

CLI::

    python tools/secrets/vault.py init [--backend auto|age|aesgcm] [--force]
    python tools/secrets/vault.py encrypt <src>... --recursive [--skip-unchanged]
    python tools/secrets/vault.py decrypt <enc>              # 仅 stdout
    python tools/secrets/vault.py exec-env <enc.env> -- <cmd...>
    python tools/secrets/vault.py verify [--json] [--plaintext]
    python tools/secrets/vault.py scan [--repo DIR]

退出码：0 成功；1 校验/扫描失败；2 使用错误或密钥/密文缺失（fail-closed）。
"""

from __future__ import annotations

import argparse
import base64
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path
import logging

TOOL_VERSION = "secvault/0.1.0"
MANIFEST_SCHEMA = "secvault-manifest/v1"
MANIFEST_NAME = "MANIFEST.json"

#: 密文根目录名（与明文根 ``secrets`` 同级）
ENC_DIRNAME = "secrets.enc"

#: 默认保险库根：显式 --home > $SECVAULT_HOME > 本机固定路径 > <cwd>/_vault
DEFAULT_VAULT_HOME = Path(r"C:\项目\_vault")
#: 主仓根（便携二进制 bin/ 所在），worktree 内运行时可回落
MAIN_REPO_DEFAULT = Path(r"C:\项目\老田的AI助手")

AGE_MAGIC = b"age-encryption.org/v1"
AESGCM_MAGIC = b"SVLT1"
AESGCM_KEY_HEADER = "# SECVAULT-AESGCM-v1"

_PUBKEY_RE = re.compile(r"age1[0-9a-z]{20,}")
_AGE_PUBKEY_COMMENT_RE = re.compile(r"#\s*public key:\s*(age1[0-9a-z]+)")
_AGE_SECRET_RE = re.compile(r"^AGE-SECRET-KEY-1[0-9A-Za-z]+$", re.M)


class VaultError(RuntimeError):
    """fail-closed 业务错误（调用方转成 rc=2）。"""


# ---------------------------------------------------------------------------
# 路径与后端解析
# ---------------------------------------------------------------------------
def resolve_home(home_arg: str | None = None) -> Path:
    """按优先级解析保险库根目录（不要求存在，init 会创建）。"""
    if home_arg:
        return Path(home_arg).expanduser().resolve()
    env = os.environ.get("SECVAULT_HOME")
    if env:
        return Path(env).expanduser().resolve()
    if DEFAULT_VAULT_HOME.exists():
        return DEFAULT_VAULT_HOME.resolve()
    return (Path.cwd() / "_vault").resolve()


def resolve_key_file(home: Path, key_arg: str | None = None) -> Path:
    if key_arg:
        return Path(key_arg).expanduser().resolve()
    env = os.environ.get("SECVAULT_KEY_FILE")
    if env:
        return Path(env).expanduser().resolve()
    return home / "keys" / "age.key"


def resolve_recipients_file(home: Path) -> Path:
    env = os.environ.get("SECVAULT_RECIPIENTS")
    if env:
        return Path(env).expanduser().resolve()
    return home / "keys" / "age.recipients"


def _repo_bin_candidates(name: str) -> list[Path]:
    cands: list[Path] = []
    env = os.environ.get("SECVAULT_BIN")
    if env:
        cands.append(Path(env) / (name + ".exe"))
        cands.append(Path(env) / name)
    here = Path(__file__).resolve()
    repo_bin = here.parents[2] / "bin"  # <repo>/tools/secrets/vault.py -> <repo>
    for d in (repo_bin, MAIN_REPO_DEFAULT / "bin"):
        cands.append(d / (name + ".exe"))
        cands.append(d / name)
    which = shutil.which(name) or shutil.which(name + ".exe")
    if which:
        cands.append(Path(which))
    return cands


def find_age_bin(name: str = "age") -> Path | None:
    env_key = {"age": "SECVAULT_AGE", "age-keygen": "SECVAULT_AGE_KEYGEN"}.get(name)
    if env_key and os.environ.get(env_key):
        p = Path(os.environ[env_key]).expanduser()
        if p.is_file():
            return p
        raise VaultError(f"{env_key} 指向的 {name} 不存在: {p}")
    for c in _repo_bin_candidates(name):
        if c.is_file():
            return c
    return None


def key_backend(key_file: Path) -> str | None:
    """读取 key 文件判断后端：'age' / 'aesgcm'；不可识别返回 None。"""
    try:
        text = key_file.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return None
    if AESGCM_KEY_HEADER in text:
        return "aesgcm"
    if _AGE_SECRET_RE.search(text):
        return "age"
    return None


def _require_key_file(key_file: Path) -> tuple[str, bytes]:
    if not key_file.is_file():
        raise VaultError(f"密钥文件缺失（fail-closed）：{key_file}")
    backend = key_backend(key_file)
    if backend is None:
        raise VaultError(f"密钥文件格式不可识别（既非 age 身份也非 AESGCM key）：{key_file}")
    return backend, key_file.read_bytes()


def _age_recipients(home: Path, key_file: Path, backend: str) -> list[str]:
    """取 age 收件人：recipients 文件优先，其次从 key 文件注释推导。"""
    rpath = resolve_recipients_file(home)
    if rpath.is_file():
        out = [ln.strip() for ln in rpath.read_text(encoding="utf-8").splitlines() if ln.strip()]
        recips = [ln for ln in out if ln.startswith("age1")]
        if not recips:
            raise VaultError(f"recipients 文件内无有效 age1 收件人：{rpath}")
        return recips
    if backend == "age":
        text = key_file.read_text(encoding="utf-8", errors="replace")
        m = _AGE_PUBKEY_COMMENT_RE.search(text)
        if m:
            return [m.group(1)]
        raise VaultError(f"无 recipients 文件，且 key 文件缺少 public key 注释：{key_file}")
    raise VaultError("AESGCM 后端需要密钥文件本体，无需 recipients 文件")


def _aesgcm_raw_key(key_file: Path) -> bytes:
    text = key_file.read_text(encoding="utf-8", errors="replace")
    if AESGCM_KEY_HEADER not in text:
        raise VaultError("AESGCM key 文件头缺失")
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        try:
            raw = base64.b64decode(line, validate=True)
        except Exception as e:  # noqa: BLE001
            raise VaultError(f"AESGCM key 不是合法 base64：{e}") from e
        if len(raw) != 32:
            raise VaultError(f"AESGCM key 长度异常：{len(raw)} != 32")
        return raw
    raise VaultError("AESGCM key 文件缺少 key 行")


# ---------------------------------------------------------------------------
# 加解密原语
# ---------------------------------------------------------------------------
def cipher_of(blob_head: bytes) -> str | None:
    if blob_head.startswith(AGE_MAGIC):
        return "age"
    if blob_head.startswith(AESGCM_MAGIC):
        return "aesgcm"
    return None


def _sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def _age_encrypt_bytes(age_bin: Path, data: bytes, recipients: list[str]) -> bytes:
    with tempfile.TemporaryDirectory(prefix="secvault-age-") as td:
        src = Path(td) / "plain.bin"
        dst = Path(td) / "cipher.age"
        src.write_bytes(data)
        argv = [str(age_bin)]
        for r in recipients:
            argv += ["-r", r]
        argv += ["-o", str(dst), str(src)]
        res = subprocess.run(argv, capture_output=True, timeout=300)
        if res.returncode != 0 or not dst.is_file():
            raise VaultError(f"age 加密失败 rc={res.returncode}: {_brief(res.stderr)}")
        return dst.read_bytes()


def _age_decrypt_bytes(age_bin: Path, key_file: Path, blob: bytes) -> bytes:
    with tempfile.TemporaryDirectory(prefix="secvault-age-") as td:
        src = Path(td) / "cipher.age"
        src.write_bytes(blob)
        argv = [str(age_bin), "-d", "-i", str(key_file), str(src)]
        res = subprocess.run(argv, capture_output=True, timeout=300)
        if res.returncode != 0:
            raise VaultError(f"age 解密失败 rc={res.returncode}: {_brief(res.stderr)}")
        return res.stdout


def _aesgcm_encrypt_bytes(raw_key: bytes, data: bytes) -> bytes:
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM

    nonce = os.urandom(12)
    ct = AESGCM(raw_key).encrypt(nonce, data, AESGCM_MAGIC)
    return AESGCM_MAGIC + nonce + ct


def _aesgcm_decrypt_bytes(raw_key: bytes, blob: bytes) -> bytes:
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM

    if not blob.startswith(AESGCM_MAGIC):
        raise VaultError("密文不是 AESGCM 格式")
    body = blob[len(AESGCM_MAGIC):]
    if len(body) < 12 + 16:
        raise VaultError("AESGCM 密文长度异常")
    nonce, ct = body[:12], body[12:]
    try:
        return AESGCM(raw_key).decrypt(nonce, ct, AESGCM_MAGIC)
    except Exception as e:  # noqa: BLE001
        raise VaultError(f"AESGCM 解密失败（密文损坏或密钥不匹配）：{e}") from e


def encrypt_bytes(home: Path, key_file: Path, data: bytes) -> tuple[str, bytes]:
    """返回 (cipher, cipher_bytes)。fail-closed：密钥/收件人缺失即报错。"""
    backend, _raw = _require_key_file(key_file)
    if backend == "age":
        age_bin = find_age_bin("age")
        if not age_bin:
            raise VaultError("age 二进制未找到，且密钥是 age 格式（fail-closed，拒绝降级）")
        recips = _age_recipients(home, key_file, backend)
        return "age", _age_encrypt_bytes(age_bin, data, recips)
    return "aesgcm", _aesgcm_encrypt_bytes(_aesgcm_raw_key(key_file), data)


def decrypt_bytes(home: Path, key_file: Path, blob: bytes) -> tuple[str, bytes]:
    cipher = cipher_of(blob[:64])
    if cipher is None:
        raise VaultError("密文头不可识别（既非 age 也非 AESGCM）")
    backend, _raw = _require_key_file(key_file)
    if cipher == "age":
        if backend != "age":
            raise VaultError("密文是 age 格式，但密钥文件不是 age 身份（fail-closed）")
        age_bin = find_age_bin("age")
        if not age_bin:
            raise VaultError("age 二进制未找到，无法解密 age 密文（fail-closed）")
        return cipher, _age_decrypt_bytes(age_bin, key_file, blob)
    if backend != "aesgcm":
        raise VaultError("密文是 AESGCM 格式，但密钥文件不是 AESGCM key（fail-closed）")
    return cipher, _aesgcm_decrypt_bytes(_aesgcm_raw_key(key_file), blob)


def _brief(b: bytes, limit: int = 300) -> str:
    s = (b or b"").decode("utf-8", errors="replace").strip().replace("\r", " ")
    return s[:limit]


# ---------------------------------------------------------------------------
# ACL / 权限收紧
# ---------------------------------------------------------------------------
def tighten_acl(path: Path) -> str:
    """尽力把文件权限收紧到「当前用户 + SYSTEM + Administrators」；返回说明。"""
    path = Path(path)
    try:
        if os.name == "nt":
            icacls = shutil.which("icacls") or "icacls"
            user = os.environ.get("USERNAME") or ""
            if not user:
                return "skipped(no USERNAME)"
            argv = [
                icacls, str(path),
                "/inheritance:r",
                "/grant:r", f"{user}:F",
                "/grant:r", "SYSTEM:F",
                "/grant:r", "Administrators:F",
            ]
            res = subprocess.run(argv, capture_output=True, timeout=120)
            if res.returncode != 0:
                return f"warn(icacls rc={res.returncode})"
            return "acl-tightened(owner+SYSTEM+Administrators)"
        os.chmod(path, 0o600)
        return "chmod-600"
    except Exception as e:  # noqa: BLE001
        return f"warn({e})"


# ---------------------------------------------------------------------------
# init
# ---------------------------------------------------------------------------
def _iso_now() -> str:
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")


def init_vault(home: Path, key_file: Path, backend: str = "auto", force: bool = False) -> dict:
    """生成 age 身份 + recipients；幂等。返回可打印摘要（不含私钥）。"""
    home = Path(home)
    key_file = Path(key_file)
    keys_dir = key_file.parent
    keys_dir.mkdir(parents=True, exist_ok=True)
    rpath = resolve_recipients_file(home)

    if key_file.exists() and not force:
        existing = key_backend(key_file)
        if existing is None:
            raise VaultError(f"已存在但无法识别的 key 文件（不覆盖）：{key_file}")
        if backend in ("age", "aesgcm") and existing != backend:
            raise VaultError(
                f"已存在 {existing} 后端 key，与 --backend {backend} 不一致（fail-closed；如确需重生成请 --force）"
            )
        summary = {
            "status": "exists-skip",
            "backend": existing,
            "key_file": str(key_file),
            "recipients_file": str(rpath) if rpath.exists() else None,
        }
        if existing == "age":
            text = key_file.read_text(encoding="utf-8", errors="replace")
            m = _AGE_PUBKEY_COMMENT_RE.search(text)
            if m and not rpath.exists():
                rpath.write_text(m.group(1) + "\n", encoding="utf-8")
                summary["recipients_file"] = str(rpath)
                summary["recipients_written"] = True
            if m:
                summary["recipient"] = m.group(1)
        return summary

    # 选择后端
    chosen = backend
    if chosen == "auto":
        chosen = "age" if find_age_bin("age") and find_age_bin("age-keygen") else "aesgcm"
    if chosen == "age":
        age_keygen = find_age_bin("age-keygen")
        if not age_keygen:
            raise VaultError("--backend age 需要 age-keygen 二进制（未找到；fail-closed）")
        tmp = keys_dir / f".age.key.tmp.{os.getpid()}"
        try:
            res = subprocess.run(
                [str(age_keygen), "-o", str(tmp)], capture_output=True, timeout=120
            )
            if res.returncode != 0 or not tmp.is_file():
                raise VaultError(f"age-keygen 失败 rc={res.returncode}: {_brief(res.stderr)}")
            text = tmp.read_text(encoding="utf-8")
            m = _AGE_PUBKEY_COMMENT_RE.search(text)
            if not m:
                raise VaultError("age-keygen 输出缺少 public key 注释")
            key_file.write_text(text, encoding="utf-8")
            rpath.write_text(m.group(1) + "\n", encoding="utf-8")
            recipient = m.group(1)
        finally:
            if tmp.exists():
                try:
                    tmp.unlink()
                except OSError as exc:
                    logging.getLogger(__name__).warning("silent-except %s: %s", "tools/secrets/vault.py", exc)
    elif chosen == "aesgcm":
        raw = os.urandom(32)
        body = base64.b64encode(raw).decode("ascii")
        content = (
            f"{AESGCM_KEY_HEADER}\n"
            f"# created: {_iso_now()}\n"
            f"# cipher: AES-256-GCM (cryptography) — age 便携二进制缺失时的兜底后端\n"
            f"{body}\n"
        )
        key_file.write_text(content, encoding="utf-8")
        recipient = None
        if rpath.exists():
            rpath.unlink()
    else:
        raise VaultError(f"未知后端: {backend}")

    acl = tighten_acl(key_file)
    if recipient is None:
        return {
            "status": "created",
            "backend": chosen,
            "key_file": str(key_file),
            "recipients_file": None,
            "acl": acl,
        }
    return {
        "status": "created",
        "backend": chosen,
        "key_file": str(key_file),
        "recipients_file": str(rpath),
        "recipient": recipient,
        "acl": acl,
    }


# ---------------------------------------------------------------------------
# encrypt / decrypt
# ---------------------------------------------------------------------------
def _iter_sources(srcs: list[str], recursive: bool) -> list[Path]:
    out: list[Path] = []
    for s in srcs:
        p = Path(s).expanduser().resolve()
        if not p.exists():
            raise VaultError(f"源路径不存在：{p}")
        if p.is_dir():
            if not recursive:
                raise VaultError(f"源是目录但未给 --recursive（fail-closed）：{p}")
            for root, dirs, files in os.walk(p):
                dirs.sort()
                for fn in sorted(files):
                    out.append(Path(root) / fn)
        else:
            out.append(p)
    # 去重保序
    seen: set[str] = set()
    uniq: list[Path] = []
    for p in out:
        k = str(p).lower()
        if k not in seen:
            seen.add(k)
            uniq.append(p)
    return uniq


def _rel_for_source(home: Path, src: Path) -> str:
    plain_root = home / "secrets"
    try:
        return src.relative_to(plain_root).as_posix()
    except ValueError as exc:
        logging.getLogger(__name__).warning("silent-except %s: %s", "tools/secrets/vault.py", exc)
    try:
        return src.relative_to(home).as_posix()
    except ValueError:
        return src.name


def _cipher_path(home: Path, rel: str) -> Path:
    return home / ENC_DIRNAME / (rel + ".age")


def _load_manifest(enc_root: Path) -> dict:
    mpath = enc_root / MANIFEST_NAME
    if not mpath.is_file():
        return {}
    try:
        data = json.loads(mpath.read_text(encoding="utf-8"))
    except (OSError, ValueError) as e:
        raise VaultError(f"MANIFEST 读取/解析失败（fail-closed）：{e}") from e
    if not isinstance(data, dict):
        raise VaultError("MANIFEST 结构非法（不是对象）")
    if data.get("schema") != MANIFEST_SCHEMA:
        raise VaultError(
            f"MANIFEST schema 不匹配：{data.get('schema')!r} != {MANIFEST_SCHEMA!r}"
        )
    if not isinstance(data.get("files"), dict):
        raise VaultError("MANIFEST.files 结构非法")
    return data


def _write_manifest(enc_root: Path, files: dict) -> Path:
    enc_root.mkdir(parents=True, exist_ok=True)
    mpath = enc_root / MANIFEST_NAME
    total_plain = sum(int(e.get("bytes") or 0) for e in files.values())
    total_cipher = sum(int(e.get("bytes_cipher") or 0) for e in files.values())
    payload = {
        "schema": MANIFEST_SCHEMA,
        "tool_version": TOOL_VERSION,
        "generated_at": _iso_now(),
        "backend": sorted({e.get("cipher", "?") for e in files.values()})[0] if files else None,
        "file_count": len(files),
        "total_bytes_plain": total_plain,
        "total_bytes_cipher": total_cipher,
        "files": dict(sorted(files.items())),
    }
    tmp = mpath.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(tmp, mpath)
    return mpath


def encrypt_paths(
    home: Path,
    key_file: Path,
    srcs: list[str],
    recursive: bool = False,
    skip_unchanged: bool = True,
) -> dict:
    """加密到 <home>/secrets.enc/<rel>.age 并合并更新 MANIFEST。返回摘要。"""
    home = Path(home)
    key_file = Path(key_file)
    files = _iter_sources(srcs, recursive)
    if not files:
        raise VaultError("没有可加密的源文件（fail-closed）")
    enc_root = home / ENC_DIRNAME
    enc_root.mkdir(parents=True, exist_ok=True)
    manifest = _load_manifest(enc_root)
    entries: dict = dict(manifest.get("files") or {})

    encrypted = reused = 0
    total_plain = 0
    for src in files:
        data = src.read_bytes()
        sha_plain = _sha256_bytes(data)
        rel = _rel_for_source(home, src)
        out = _cipher_path(home, rel)
        entry = entries.get(rel)
        if (
            skip_unchanged
            and isinstance(entry, dict)
            and entry.get("sha256_plain") == sha_plain
            and out.is_file()
            and _sha256_file(out) == entry.get("sha256_cipher")
        ):
            reused += 1
            total_plain += len(data)
            continue
        cipher, blob = encrypt_bytes(home, key_file, data)
        out.parent.mkdir(parents=True, exist_ok=True)
        tmp = out.with_name(out.name + f".tmp{os.getpid()}")
        tmp.write_bytes(blob)
        os.replace(tmp, out)
        entries[rel] = {
            "path": rel,
            "sha256_plain": sha_plain,
            "sha256_cipher": _sha256_bytes(blob),
            "bytes": len(data),
            "bytes_cipher": len(blob),
            "encrypted_at": _iso_now(),
            "tool_version": TOOL_VERSION,
            "cipher": cipher,
        }
        encrypted += 1
        total_plain += len(data)

    mpath = _write_manifest(enc_root, entries)
    return {
        "status": "ok",
        "enc_root": str(enc_root),
        "manifest": str(mpath),
        "file_count": len(entries),
        "encrypted": encrypted,
        "reused": reused,
        "total_bytes_plain": total_plain,
        "backend": sorted({e.get("cipher", "?") for e in entries.values()}) if entries else [],
    }


def _resolve_enc_arg(home: Path, arg: str) -> Path:
    p = Path(arg).expanduser()
    if p.is_file():
        return p.resolve()
    cand = home / ENC_DIRNAME / arg
    if cand.is_file():
        return cand
    cand2 = home / ENC_DIRNAME / (arg + ".age")
    if cand2.is_file():
        return cand2
    raise VaultError(f"密文不存在：{arg}（也未在 {home / ENC_DIRNAME} 下找到）")


def decrypt_path(home: Path, key_file: Path, enc: str) -> bytes:
    ep = _resolve_enc_arg(home, enc)
    blob = ep.read_bytes()
    _cipher, plain = decrypt_bytes(home, key_file, blob)
    return plain


# ---------------------------------------------------------------------------
# exec-env
# ---------------------------------------------------------------------------
_ENV_LINE_RE = re.compile(r"^(?:export\s+)?([A-Za-z_][A-Za-z0-9_]*)=(.*)$")


def parse_env_text(text: str) -> dict[str, str]:
    env: dict[str, str] = {}
    for i, raw in enumerate(text.splitlines(), 1):
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line[len("export "):].strip()
        m = _ENV_LINE_RE.match(line)
        if not m:
            # 不打印行内容（可能含凭据），只报行号
            raise VaultError(f".env 解析失败：第 {i} 行不是 KEY=VALUE 形式（fail-closed，行内容不回显）")
        key, val = m.group(1), m.group(2).strip()
        if len(val) >= 2 and val[0] == val[-1] and val[0] in ("'", '"'):
            val = val[1:-1]
        env[key] = val
    return env


def run_exec_env(
    home: Path, key_file: Path, enc_env: str, cmd: list[str], capture: bool = False
) -> tuple[int, bytes, bytes]:
    if not cmd:
        raise VaultError("exec-env 缺少要执行的命令（用法：exec-env <enc.env> -- <cmd...>）")
    plain = decrypt_path(home, key_file, enc_env)
    try:
        text = plain.decode("utf-8-sig")
    except UnicodeDecodeError as e:
        raise VaultError(f".env 解密结果不是 UTF-8 文本（fail-closed）：{e}") from e
    parsed = parse_env_text(text)
    child_env = os.environ.copy()
    child_env.update(parsed)
    if capture:
        res = subprocess.run(cmd, env=child_env, capture_output=True)
        return res.returncode, res.stdout, res.stderr
    res = subprocess.run(cmd, env=child_env)
    return res.returncode, b"", b""


# ---------------------------------------------------------------------------
# verify
# ---------------------------------------------------------------------------
def verify_manifest(home: Path, check_plaintext: bool = False) -> dict:
    enc_root = home / ENC_DIRNAME
    manifest = _load_manifest(enc_root)
    if not manifest:
        raise VaultError(f"MANIFEST 不存在：{enc_root / MANIFEST_NAME}（fail-closed）")
    failures: list[dict] = []
    checked = 0
    for rel, entry in sorted((manifest.get("files") or {}).items()):
        checked += 1
        if not isinstance(entry, dict):
            failures.append({"path": rel, "reason": "manifest-entry-invalid"})
            continue
        ep = _cipher_path(home, rel)
        if not ep.is_file():
            failures.append({"path": rel, "reason": "cipher-missing", "file": str(ep)})
            continue
        blob = ep.read_bytes()
        got = _sha256_bytes(blob)
        want = entry.get("sha256_cipher")
        if got != want:
            failures.append({"path": rel, "reason": "cipher-sha256-mismatch"})
            continue
        if entry.get("sha256_plain") and entry.get("cipher") not in ("age", "aesgcm"):
            failures.append({"path": rel, "reason": "cipher-field-invalid"})
            continue
        if check_plaintext:
            src = home / "secrets" / rel
            if not src.is_file():
                failures.append({"path": rel, "reason": "plaintext-source-missing"})
            elif _sha256_file(src) != entry.get("sha256_plain"):
                failures.append({"path": rel, "reason": "plaintext-sha256-mismatch"})
    return {
        "ok": not failures,
        "checked": checked,
        "failed": len(failures),
        "manifest": str(enc_root / MANIFEST_NAME),
        "enc_root": str(enc_root),
        "backend": manifest.get("backend"),
        "file_count": manifest.get("file_count"),
        "failures": failures,
    }


# ---------------------------------------------------------------------------
# scan
# ---------------------------------------------------------------------------
def repo_root_default() -> Path:
    env = os.environ.get("SECVAULT_REPO")
    if env:
        return Path(env).expanduser().resolve()
    return Path(__file__).resolve().parents[2]


def _git_lines(repo: Path, *args: str) -> list[str]:
    res = subprocess.run(
        ["git", "-C", str(repo), *args], capture_output=True, timeout=120
    )
    if res.returncode != 0:
        return []
    return [x for x in res.stdout.decode("utf-8", errors="replace").split("\0") if x.strip()]


def run_scan(repo: Path | None = None) -> int:
    """复用 tools/scan_secrets.py 扫「将被提交」的文件（rc≠0=有未豁免命中）。

    - 主仓（``.git`` 是目录）：``--all`` 扫全部 tracked 文件；
    - worktree（``.git`` 是文件）：scan_secrets 的 ``--all`` 会因 ``.git`` 非目录而
      静默扫 0 个文件（假绿），故改为显式传入「暂存 + 未忽略未跟踪」文件清单，
      等价于 pre-commit 语义。
    """
    repo = (repo or repo_root_default()).resolve()
    scanner = repo / "tools" / "scan_secrets.py"
    if not scanner.is_file():
        raise VaultError(f"scan_secrets.py 不存在：{scanner}")
    baseline = repo / "tools" / "scan_secrets_allowlist.tsv"
    base_args = ["--baseline", str(baseline)] if baseline.is_file() else []

    if (repo / ".git").is_dir():
        argv = [sys.executable, str(scanner), "--all", "--repo", str(repo)] + base_args
        res = subprocess.run(argv, cwd=str(repo))
        print(f"[secrets.scan] mode=all repo={repo} rc={res.returncode}（输出已脱敏）")
        return res.returncode

    targets = _git_lines(repo, "diff", "--cached", "--name-only", "-z")
    targets += _git_lines(repo, "ls-files", "--others", "--exclude-standard", "-z")
    # 去重保序，且只保留真实文件
    seen: set[str] = set()
    files: list[str] = []
    for rel in targets:
        if rel in seen:
            continue
        seen.add(rel)
        if (repo / rel).is_file():
            files.append(rel)
    if not files:
        print(f"[secrets.scan] mode=worktree repo={repo} 无可扫变更文件 → rc=0（提示：先 git add）")
        return 0
    argv = [sys.executable, str(scanner), *files] + base_args
    res = subprocess.run(argv, cwd=str(repo))
    print(
        f"[secrets.scan] mode=worktree repo={repo} files={len(files)} "
        f"rc={res.returncode}（输出已脱敏）"
    )
    return res.returncode


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------
def _build_parser() -> argparse.ArgumentParser:
    # --home/--key-file 既支持写在子命令前（全局），也支持写在子命令后（子解析器）；
    # 子解析器用 SUPPRESS 默认值，避免覆盖全局已解析值。
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument(
        "--home", default=argparse.SUPPRESS, help="保险库根（默认 $SECVAULT_HOME 或 C:\\项目\\_vault）"
    )
    common.add_argument("--key-file", default=argparse.SUPPRESS, help="身份/密钥文件（默认 <home>/keys/age.key）")

    ap = argparse.ArgumentParser(
        prog="vault.py",
        description="secvault L1 凭据保险库（age 优先，AES-GCM 兜底；全部 fail-closed）",
    )
    ap.add_argument("--version", action="version", version=TOOL_VERSION)
    ap.add_argument("--home", default=None, help="保险库根（默认 $SECVAULT_HOME 或 C:\\项目\\_vault）")
    ap.add_argument("--key-file", default=None, help="身份/密钥文件（默认 <home>/keys/age.key）")
    sub = ap.add_subparsers(dest="cmd", required=True)

    p_init = sub.add_parser("init", parents=[common], help="生成 age 身份 + recipients（幂等）")
    p_init.add_argument("--backend", choices=["auto", "age", "aesgcm"], default="auto")
    p_init.add_argument("--force", action="store_true", help="已存在时强制重建（危险：旧密文将不可解）")

    p_enc = sub.add_parser("encrypt", parents=[common], help="加密到 <home>/secrets.enc/<rel>.age")
    p_enc.add_argument("src", nargs="+")
    p_enc.add_argument("--recursive", action="store_true")
    p_enc.add_argument("--no-skip-unchanged", action="store_true", help="强制全部重加密")

    p_dec = sub.add_parser("decrypt", parents=[common], help="解密到 stdout（不落盘、不打印元信息）")
    p_dec.add_argument("enc")

    p_ex = sub.add_parser("exec-env", parents=[common], help="解密 .env 注入子进程环境后执行命令")
    p_ex.add_argument("enc_env")
    p_ex.add_argument("cmd", nargs=argparse.REMAINDER)

    p_ver = sub.add_parser("verify", parents=[common], help="按 MANIFEST 校验全部密文 sha256")
    p_ver.add_argument("--json", action="store_true")
    p_ver.add_argument("--plaintext", action="store_true", help="同时校验明文源（若存在）")

    p_scan = sub.add_parser("scan", parents=[common], help="调用 tools/scan_secrets.py 扫 tracked 文件")
    p_scan.add_argument("--repo", default=None)
    return ap


def main(argv: list[str] | None = None) -> int:
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, OSError) as exc:
        logging.getLogger(__name__).warning("silent-except %s: %s", "tools/secrets/vault.py", exc)
    parser = _build_parser()
    # exec-env 的 REMAINDER 需要先剥掉 `--`
    raw = list(sys.argv[1:] if argv is None else argv)
    args = parser.parse_args(raw)
    home = resolve_home(args.home)
    key_file = resolve_key_file(home, args.key_file)

    try:
        if args.cmd == "init":
            summary = init_vault(home, key_file, backend=args.backend, force=args.force)
            print(json.dumps(summary, ensure_ascii=False, indent=2))
            return 0
        if args.cmd == "encrypt":
            summary = encrypt_paths(
                home,
                key_file,
                args.src,
                recursive=args.recursive,
                skip_unchanged=not args.no_skip_unchanged,
            )
            print(json.dumps(summary, ensure_ascii=False, indent=2))
            return 0
        if args.cmd == "decrypt":
            data = decrypt_path(home, key_file, args.enc)
            sys.stdout.flush()
            sys.stdout.buffer.write(data)
            sys.stdout.buffer.flush()
            return 0
        if args.cmd == "exec-env":
            cmd = list(args.cmd or [])
            if cmd and cmd[0] == "--":
                cmd = cmd[1:]
            rc, _out, _err = run_exec_env(home, key_file, args.enc_env, cmd)
            return rc
        if args.cmd == "verify":
            result = verify_manifest(home, check_plaintext=args.plaintext)
            if args.json:
                print(json.dumps(result, ensure_ascii=False, indent=2))
            else:
                print(f"[verify] ok={result['ok']} checked={result['checked']} failed={result['failed']}")
                print(f"[verify] manifest={result['manifest']}")
                for f in result["failures"]:
                    print(f"  FAIL {f.get('path')}: {f.get('reason')}")
            return 0 if result["ok"] else 1
        if args.cmd == "scan":
            repo = Path(args.repo).expanduser().resolve() if args.repo else None
            return run_scan(repo)
        parser.error("unknown command")
        return 2
    except VaultError as e:
        print(f"[secrets] FAIL-CLOSED: {e}", file=sys.stderr)
        return 2
    except KeyboardInterrupt:
        return 130


if __name__ == "__main__":
    sys.exit(main())
