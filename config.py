import os
import secrets

HOST = os.getenv("HOST", "0.0.0.0")
PORT = int(os.getenv("PORT", "9090"))

DB_PATH = os.path.join(os.path.dirname(__file__), "data", "speedtest.db")

DOWNLOAD_SIZE_MB = int(os.getenv("DOWNLOAD_SIZE_MB", "50"))
UPLOAD_SIZE_MB = int(os.getenv("UPLOAD_SIZE_MB", "25"))

# --- Security: load .env if present ---
_env_path = os.path.join(os.path.dirname(__file__), ".env")
if os.path.isfile(_env_path):
    with open(_env_path) as _f:
        for _line in _f:
            _line = _line.strip()
            if _line and not _line.startswith("#") and "=" in _line:
                _k, _v = _line.split("=", 1)
                _k, _v = _k.strip(), _v.strip().strip("'\"")
                if _k and _k not in os.environ:
                    os.environ[_k] = _v

# --- Secret key: auto-generate if not set ---
SECRET_KEY = os.getenv("SECRET_KEY", "")
if not SECRET_KEY:
    SECRET_KEY = secrets.token_hex(32)
    with open(_env_path, "a") as _f:
        _f.write(f"\nSECRET_KEY={SECRET_KEY}\n")
    print("[SECURITY] Generated new SECRET_KEY, saved to .env")

ALGORITHM = "HS256"
ACCESS_TOKEN_EXPIRE_MINUTES = int(os.getenv("ACCESS_TOKEN_EXPIRE_MINUTES", "480"))

DEFAULT_OPERATOR_LOGIN = os.getenv("ADMIN_LOGIN", "admin")
DEFAULT_OPERATOR_PASSWORD = os.getenv("ADMIN_PASSWORD", "admin")

RATE_LIMIT_SECONDS = int(os.getenv("RATE_LIMIT_SECONDS", "60"))
