"""Protect stored credentials with the current Windows user's DPAPI key."""
import base64
import ctypes
from ctypes import wintypes
import os

SECRET_KEYS = {'qbittorrent_password', 'tmdb_credential', 'password', 'key'}


def crypt(value, decrypt=False):
    if os.name != 'nt':
        return value
    class Blob(ctypes.Structure):
        _fields_ = [('size', wintypes.DWORD), ('data', ctypes.POINTER(ctypes.c_ubyte))]
    raw = base64.b64decode(value) if decrypt else value.encode('utf-8')
    buffer = ctypes.create_string_buffer(raw)
    incoming = Blob(len(raw), ctypes.cast(buffer, ctypes.POINTER(ctypes.c_ubyte)))
    outgoing = Blob()
    library = ctypes.WinDLL('crypt32', use_last_error=True)
    function = library.CryptUnprotectData if decrypt else library.CryptProtectData
    function.argtypes = [ctypes.POINTER(Blob), ctypes.c_void_p, ctypes.c_void_p,
                         ctypes.c_void_p, ctypes.c_void_p, wintypes.DWORD, ctypes.POINTER(Blob)]
    function.restype = wintypes.BOOL
    if not function(ctypes.byref(incoming), None, None, None, None, 1, ctypes.byref(outgoing)):
        raise OSError('Stored credential could not be read for this Windows account.')
    try:
        output = ctypes.string_at(outgoing.data, outgoing.size)
        return output.decode('utf-8') if decrypt else base64.b64encode(output).decode('ascii')
    finally:
        free = ctypes.WinDLL('kernel32').LocalFree
        free.argtypes = [ctypes.c_void_p]
        free.restype = ctypes.c_void_p
        free(outgoing.data)


def transform(value, decrypt=False):
    if isinstance(value, dict):
        if set(value) == {'dpapi'}:
            return crypt(value['dpapi'], True)
        return {key: transform(item, decrypt) if decrypt or key not in SECRET_KEYS or not isinstance(item, str) or not item
                else {'dpapi': crypt(item)} if os.name == 'nt' else item
                for key, item in value.items()}
    if isinstance(value, list):
        return [transform(item, decrypt) for item in value]
    return value
