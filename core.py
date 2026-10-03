"""Infrastructure shared by the desktop apps in this repository.

Both apps keep their data the same way: a JSON file in a per-user directory,
written atomically, and encrypted with a key derived from the cover-screen
password when one is set. Everything here is deliberately free of Tkinter so it
can be tested, and reused, without a display.
"""

import base64
import json
import os
import sys
import tempfile

from cryptography.fernet import Fernet
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC

KDF_ITERATIONS = 480000


def user_data_dir(app_name):
    """Per-user data directory, so the data does not depend on the working directory."""
    try:
        if sys.platform == "win32":
            base = os.environ.get("APPDATA") or os.path.expanduser("~")
        elif sys.platform == "darwin":
            base = os.path.join(os.path.expanduser("~"), "Library", "Application Support")
        else:
            base = os.environ.get("XDG_CONFIG_HOME") or os.path.join(os.path.expanduser("~"), ".config")
        path = os.path.join(base, app_name)
        os.makedirs(path, exist_ok=True)
        return path
    except Exception:
        return os.path.abspath(".")


def write_json_atomic(path, data):
    """Write through a temp file so a failed write cannot truncate the existing one."""
    directory = os.path.dirname(path) or "."
    fd, tmp_path = tempfile.mkstemp(dir=directory, prefix=".tmp_", suffix=".json")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=4)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp_path, path)
    except Exception:
        try:
            os.unlink(tmp_path)
        except OSError:
            pass
        raise


def read_json(path):
    """Read a config-style file as utf-8, falling back to the locale encoding."""
    if not os.path.exists(path):
        return {}
    for encoding in ("utf-8", None):
        try:
            with open(path, 'r', encoding=encoding) as f:
                data = json.load(f)
            return data if isinstance(data, dict) else {}
        except UnicodeError:
            continue
        except Exception:
            return {}
    return {}


def derive_key(password, salt):
    """Fernet key from a password. Deliberately slow to brute-force."""
    kdf = PBKDF2HMAC(algorithm=hashes.SHA256(), length=32, salt=salt, iterations=KDF_ITERATIONS)
    return base64.urlsafe_b64encode(kdf.derive(password.encode("utf-8")))


def new_password_credentials(password):
    """(salt, check token, Fernet) for a newly chosen password. The password is not kept."""
    salt = os.urandom(16)
    key = derive_key(password, salt)
    return (base64.urlsafe_b64encode(salt).decode("ascii"),
            Fernet(key).encrypt(b"unlocked").decode("ascii"),
            Fernet(key))


def unlock(password, salt_b64, check_token):
    """The Fernet for this password, or None when it does not match."""
    try:
        key = derive_key(password, base64.urlsafe_b64decode(salt_b64))
        fernet = Fernet(key)
        fernet.decrypt(check_token.encode("utf-8"))
        return fernet
    except Exception:
        return None


def read_store(path, fernet):
    """Read a data file. Returns (data, status): ok / missing / locked / failed.

    "locked" means the file is encrypted and no key was supplied — that is a
    normal state before unlocking, not a failure.
    """
    if not os.path.exists(path):
        return {}, "missing"

    try:
        with open(path, 'r', encoding='utf-8') as f:
            raw = json.load(f)
    except Exception:
        return {}, "failed"

    if not isinstance(raw, dict):
        return {}, "failed"

    if not raw.get("encrypted"):
        return raw, "ok"

    if fernet is None:
        return {}, "locked"

    try:
        data = json.loads(fernet.decrypt(raw["payload"].encode("utf-8")).decode("utf-8"))
    except Exception:
        return {}, "failed"
    return (data, "ok") if isinstance(data, dict) else ({}, "failed")


def write_store(path, data, fernet):
    """Write a data file, encrypted when a key is supplied."""
    if fernet is None:
        write_json_atomic(path, data)
        return
    payload = fernet.encrypt(json.dumps(data, ensure_ascii=False).encode("utf-8"))
    write_json_atomic(path, {"encrypted": True, "payload": payload.decode("ascii")})
