"""Local Windows CurrentUser DPAPI credential storage; no plaintext persistence."""
from __future__ import annotations

import ctypes
from ctypes import wintypes
import os
from pathlib import Path
import uuid

from runtime import APP_DIR

KEY_PATH = APP_DIR / '.api-key.dpapi'
_PREFIX = b'LIVERY-DPAPI1\x00'
_ENTROPY = b'livery-lab.api-key.v1'
_UI_FORBIDDEN = 0x1


class CredentialError(RuntimeError):
    """Sanitized local credential error, never containing secret data."""


class _Blob(ctypes.Structure):
    _fields_ = [('cbData', wintypes.DWORD), ('pbData', ctypes.POINTER(ctypes.c_ubyte))]


def _buffer_blob(data):
    buffer = ctypes.create_string_buffer(data)
    blob = _Blob(len(data), ctypes.cast(buffer, ctypes.POINTER(ctypes.c_ubyte)))
    return buffer, blob


def _crypt(data, *, decrypt):
    if os.name != 'nt':
        raise CredentialError('本机密钥加密仅支持 Windows；其他系统请使用环境变量。')
    try:
        crypt = ctypes.WinDLL('crypt32', use_last_error=True)
        kernel = ctypes.WinDLL('kernel32', use_last_error=True)
        crypt.CryptProtectData.argtypes = [ctypes.POINTER(_Blob), wintypes.LPCWSTR, ctypes.POINTER(_Blob), ctypes.c_void_p, ctypes.c_void_p, wintypes.DWORD, ctypes.POINTER(_Blob)]
        crypt.CryptProtectData.restype = wintypes.BOOL
        crypt.CryptUnprotectData.argtypes = [ctypes.POINTER(_Blob), ctypes.c_void_p, ctypes.POINTER(_Blob), ctypes.c_void_p, ctypes.c_void_p, wintypes.DWORD, ctypes.POINTER(_Blob)]
        crypt.CryptUnprotectData.restype = wintypes.BOOL
        kernel.LocalFree.argtypes = [ctypes.c_void_p]
        kernel.LocalFree.restype = ctypes.c_void_p
        source_buffer, source = _buffer_blob(data)
        entropy_buffer, entropy = _buffer_blob(_ENTROPY)
        output = _Blob()
        try:
            # No CRYPTPROTECT_LOCAL_MACHINE: protection is bound to CurrentUser.
            if decrypt:
                success = crypt.CryptUnprotectData(ctypes.byref(source), None, ctypes.byref(entropy), None, None, _UI_FORBIDDEN, ctypes.byref(output))
            else:
                success = crypt.CryptProtectData(ctypes.byref(source), 'Livery Lab API credential', ctypes.byref(entropy), None, None, _UI_FORBIDDEN, ctypes.byref(output))
            if not success:
                raise CredentialError('Windows 无法解密本机密钥；请在当前用户下重新保存。' if decrypt else 'Windows 无法加密本机密钥，未保存明文。')
            return ctypes.string_at(output.pbData, output.cbData)
        finally:
            ctypes.memset(source_buffer, 0, ctypes.sizeof(source_buffer))
            if output.pbData:
                ctypes.memset(output.pbData, 0, output.cbData)
                kernel.LocalFree(ctypes.cast(output.pbData, ctypes.c_void_p))
    except CredentialError:
        raise
    except Exception:
        raise CredentialError('Windows 本机密钥操作失败，未记录密钥内容。') from None


def _validated_key(secret):
    if not isinstance(secret, str) or not secret.strip() or not secret.isascii() or len(secret) > 32768 or any(ord(c) < 32 or ord(c) == 127 for c in secret):
        raise CredentialError('API 密钥格式无效。')
    return secret.strip()


def save_api_key(secret: str, path: Path | None = None) -> Path:
    """Encrypt for this Windows user and atomically store ciphertext only."""
    secret = _validated_key(secret)
    encrypted = _PREFIX + _crypt(secret.encode('utf-8'), decrypt=False)
    target = Path(path) if path is not None else KEY_PATH
    temporary = target.with_name(target.name + '.' + uuid.uuid4().hex + '.tmp')
    try:
        target.parent.mkdir(parents=True, exist_ok=True)
        with temporary.open('xb') as stream:
            stream.write(encrypted)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, target)
        return target
    except OSError:
        raise CredentialError('无法保存加密密钥文件；请检查应用目录写入权限。') from None
    finally:
        try:
            temporary.unlink(missing_ok=True)
        except OSError:
            pass


def load_api_key(path: Path | None = None) -> str | None:
    """Return a transient plaintext value, or None when no local key is saved."""
    target = Path(path) if path is not None else KEY_PATH
    try:
        with target.open('rb') as stream:
            encrypted = stream.read(128 * 1024 + 1)
    except FileNotFoundError:
        return None
    except OSError:
        raise CredentialError('无法读取本机加密密钥文件。') from None
    if len(encrypted) > 128 * 1024 or not encrypted.startswith(_PREFIX) or len(encrypted) == len(_PREFIX):
        raise CredentialError('本机加密密钥文件无效；请重新保存密钥。')
    try:
        plaintext = _crypt(encrypted[len(_PREFIX):], decrypt=True).decode('utf-8')
        return _validated_key(plaintext)
    except (UnicodeError, ValueError):
        raise CredentialError('本机加密密钥内容无效；请重新保存密钥。') from None
