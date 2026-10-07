"""本人终端运行的加密密钥与离线声明签名工具，不安装身份信任。"""

import argparse
import getpass
import hashlib
import json
import os
from pathlib import Path
import sys
import warnings

from .cli import _decode_input
from .human_review import _statement
from .provenance import _local_path
from .source_review import _read_evidence, _reject_links


def _emit(value):
    print(json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False))


def _ordinary_path(value):
    _local_path(value)
    drive, tail = os.path.splitdrive(value)
    if ":" in tail:
        raise ValueError("离线文件不能使用Windows隐藏数据流路径")
    return value


def _new_path(value):
    _ordinary_path(value)
    path = Path(os.path.abspath(value))
    _local_path(str(path))
    if not path.parent.is_dir():
        raise ValueError("输出目录不存在")
    _reject_links(path.parent)
    if os.path.lexists(path):
        raise ValueError("输出文件已存在，拒绝覆盖")
    return path


def _restrict_private_permissions(descriptor):
    if os.name != "nt":
        os.fchmod(descriptor, 0o600)
        return
    import ctypes
    from ctypes import wintypes as wt
    import msvcrt

    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    security = ctypes.WinDLL("advapi32", use_last_error=True)
    kernel.GetCurrentProcess.restype = wt.HANDLE
    kernel.CloseHandle.argtypes = [wt.HANDLE]
    kernel.LocalFree.argtypes = [wt.LPVOID]
    security.OpenProcessToken.argtypes = [wt.HANDLE, wt.DWORD, ctypes.POINTER(wt.HANDLE)]
    security.GetTokenInformation.argtypes = [wt.HANDLE, ctypes.c_int, wt.LPVOID, wt.DWORD, ctypes.POINTER(wt.DWORD)]
    security.ConvertSidToStringSidW.argtypes = [wt.LPVOID, ctypes.POINTER(wt.LPWSTR)]
    security.ConvertStringSecurityDescriptorToSecurityDescriptorW.argtypes = [wt.LPCWSTR, wt.DWORD, ctypes.POINTER(wt.LPVOID), ctypes.POINTER(wt.DWORD)]
    security.GetSecurityDescriptorDacl.argtypes = [wt.LPVOID, ctypes.POINTER(wt.BOOL), ctypes.POINTER(wt.LPVOID), ctypes.POINTER(wt.BOOL)]
    security.SetSecurityInfo.argtypes = [wt.HANDLE, ctypes.c_int, wt.DWORD, wt.LPVOID, wt.LPVOID, wt.LPVOID, wt.LPVOID]
    security.SetSecurityInfo.restype = wt.DWORD
    token, sid_string, descriptor_pointer = wt.HANDLE(), wt.LPWSTR(), wt.LPVOID()
    try:
        if not security.OpenProcessToken(kernel.GetCurrentProcess(), 0x0008, ctypes.byref(token)):
            raise OSError("token")
        size = wt.DWORD()
        security.GetTokenInformation(token, 1, None, 0, ctypes.byref(size))
        token_user = ctypes.create_string_buffer(size.value)
        if not security.GetTokenInformation(token, 1, token_user, size, ctypes.byref(size)):
            raise OSError("token user")
        sid = ctypes.cast(token_user, ctypes.POINTER(wt.LPVOID))[0]
        if not security.ConvertSidToStringSidW(sid, ctypes.byref(sid_string)):
            raise OSError("SID")
        if not security.ConvertStringSecurityDescriptorToSecurityDescriptorW(
                "D:P(A;;FA;;;" + sid_string.value + ")", 1, ctypes.byref(descriptor_pointer), None):
            raise OSError("SDDL")
        present, defaulted, dacl = wt.BOOL(), wt.BOOL(), wt.LPVOID()
        if not security.GetSecurityDescriptorDacl(descriptor_pointer, ctypes.byref(present), ctypes.byref(dacl), ctypes.byref(defaulted)) or not present:
            raise OSError("DACL")
        # 对空文件的既有句柄设置当前用户专属、禁止继承的 DACL。
        if security.SetSecurityInfo(msvcrt.get_osfhandle(descriptor), 1, 0x80000004, None, None, dacl, None):
            raise OSError("set DACL")
    except OSError:
        raise ValueError("Windows私钥访问权限设置失败，拒绝生成") from None
    finally:
        if descriptor_pointer:
            kernel.LocalFree(descriptor_pointer)
        if sid_string:
            kernel.LocalFree(ctypes.cast(sid_string, wt.LPVOID))
        if token:
            kernel.CloseHandle(token)


def _delete_open_private(descriptor):
    if os.name != "nt":
        return
    import ctypes
    from ctypes import wintypes as wt
    import msvcrt

    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.SetFileInformationByHandle.argtypes = [wt.HANDLE, ctypes.c_int, wt.LPVOID, wt.DWORD]
    disposition = ctypes.c_ubyte(1)
    # 只标记本次独占创建的原对象删除；不按可被替换的路径清理。
    kernel.SetFileInformationByHandle(msvcrt.get_osfhandle(descriptor), 4, ctypes.byref(disposition), 1)


def _open_new(path, *, private):
    if os.name != "nt" or not private:
        return os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_BINARY", 0), 0o600)
    import ctypes
    from ctypes import wintypes as wt
    import msvcrt

    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.CreateFileW.argtypes = [wt.LPCWSTR, wt.DWORD, wt.DWORD, wt.LPVOID, wt.DWORD, wt.DWORD, wt.HANDLE]
    kernel.CreateFileW.restype = wt.HANDLE
    kernel.CloseHandle.argtypes = [wt.HANDLE]
    # 禁止共享；WRITE_DAC 收紧权限，DELETE 用于原句柄上的失败清理。
    handle = kernel.CreateFileW(str(path), 0x40070000, 0, None, 1, 0x80, None)
    if handle == ctypes.c_void_p(-1).value:
        raise ctypes.WinError(ctypes.get_last_error())
    try:
        return msvcrt.open_osfhandle(handle, os.O_WRONLY | os.O_BINARY)
    except BaseException:
        kernel.CloseHandle(handle)
        raise


def _write_new(path, raw, *, private=False):
    descriptor = _open_new(path, private=private)
    with os.fdopen(descriptor, "wb") as output:
        try:
            if private:
                _restrict_private_permissions(descriptor)
            output.write(raw)
            output.flush()
            os.fsync(output.fileno())
        except BaseException:
            if private:
                _delete_open_private(output.fileno())
            raise


def _public(key):
    from cryptography.hazmat.primitives import serialization

    raw = key.public_key().public_bytes(serialization.Encoding.Raw, serialization.PublicFormat.Raw)
    return {"algorithm": "Ed25519", "public_key_hex": raw.hex(), "key_id": hashlib.sha256(raw).hexdigest()}


def _password(prompt):
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("error", getpass.GetPassWarning)
            password = getpass.getpass(prompt)
    except getpass.GetPassWarning:
        raise ValueError("无法关闭口令回显，拒绝读取口令") from None
    if type(password) is not str or len(password) < 12:
        raise ValueError("口令至少需要12个字符")
    try:
        return password.encode("utf-8")
    except UnicodeError:
        raise ValueError("口令编码非法") from None


def _keygen(args):
    private_path, public_path = _new_path(args.private_key), _new_path(args.public_key)
    if private_path == public_path:
        raise ValueError("私钥和公钥文件必须不同")
    _emit({"private_key_path": str(private_path), "public_key_path": str(public_path),
           "identity_binding_verified": False})
    if input("输入'生成'确认创建本人离线密钥：") != "生成":
        raise ValueError("本人未确认密钥生成")
    password = _password("加密私钥口令：")
    if password != _password("再次输入口令："):
        raise ValueError("两次口令不一致")
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

    key = Ed25519PrivateKey.generate()
    private_raw = key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
                                    serialization.BestAvailableEncryption(password))
    public = _public(key)
    _write_new(private_path, private_raw, private=True)
    # 公钥输出失败时保留加密私钥，由本人处理；不按路径删除闭合句柄后的文件。
    _write_new(public_path, json.dumps(public, ensure_ascii=False, indent=2).encode("utf-8"))
    return {"ok": True, "public": public, "identity_binding_verified": False,
            "trust_policy_installed": False}


def _sign(args):
    output_path = _new_path(args.output)
    _ordinary_path(args.statement)
    _, statement_raw = _read_evidence(args.statement)
    statement = _statement(_decode_input(statement_raw))
    from .signed_review import signing_message

    message = signing_message(statement, args.authority_id, args.policy_id)
    _ordinary_path(args.private_key)
    _, private_raw = _read_evidence(args.private_key)
    if len(private_raw) > 65536 or not private_raw.startswith(b"-----BEGIN ENCRYPTED PRIVATE KEY-----"):
        raise ValueError("只接受加密PKCS8私钥，不能使用明文私钥")
    _emit({"authority_id": args.authority_id, "policy_id": args.policy_id, "statement": statement})
    if input("输入'签署'确认本次完整声明：") != "签署":
        raise ValueError("本人未确认签署")
    password = _password("私钥解密口令：")
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

    try:
        key = serialization.load_pem_private_key(private_raw, password=password)
    except (ValueError, TypeError):
        raise ValueError("口令错误或加密私钥格式不支持") from None
    if not isinstance(key, Ed25519PrivateKey):
        raise ValueError("私钥必须是Ed25519")
    public = _public(key)
    proof = {"schema_version": 1, "algorithm": "Ed25519", "authority_id": args.authority_id,
             "policy_id": args.policy_id, "key_id": public["key_id"], "signature_hex": key.sign(message).hex()}
    _write_new(output_path, json.dumps(proof, ensure_ascii=False, indent=2).encode("utf-8"))
    return {"ok": True, "proof_path": str(output_path), "key_id": public["key_id"],
            "identity_binding_verified": False, "trust_policy_installed": False}


def main(argv=None):
    parser = argparse.ArgumentParser(description="本人离线Ed25519签名，私钥加密保存")
    commands = parser.add_subparsers(dest="command", required=True)
    keygen = commands.add_parser("keygen")
    keygen.add_argument("--private-key", required=True)
    keygen.add_argument("--public-key", required=True)
    sign = commands.add_parser("sign")
    for name in ("private-key", "statement", "authority-id", "policy-id", "output"):
        sign.add_argument("--" + name, required=True)
    args = parser.parse_args(argv)
    try:
        if not sys.stdin.isatty() or not sys.stdout.isatty():
            raise ValueError("密钥生成和签署必须由本人在交互终端确认")
        result = _keygen(args) if args.command == "keygen" else _sign(args)
        _emit(result)
        return 0
    except (OSError, ValueError, ImportError, RuntimeError) as exc:
        message = "缺少cryptography依赖或签名模块" if isinstance(exc, ImportError) else str(exc)
        _emit({"ok": False, "error": message})
        return 2
    except (KeyboardInterrupt, EOFError):
        _emit({"ok": False, "error": "本人交互已取消"})
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
