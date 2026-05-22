import hashlib
import os
import shutil
from pathlib import Path

STORAGE_DIR = os.environ.get("ARTIFACT_STORAGE_DIR", "/data/artifacts")

def _blob_path(sha256: str) -> Path:
    prefix = sha256[:2]
    return Path(STORAGE_DIR) / "blobs" / prefix / sha256

def store_blob(data: bytes) -> str:
    sha256 = hashlib.sha256(data).hexdigest()
    path = _blob_path(sha256)
    path.parent.mkdir(parents=True, exist_ok=True)
    if not path.exists():
        path.write_bytes(data)
    return sha256

def get_blob(sha256: str) -> bytes | None:
    path = _blob_path(sha256)
    if not path.exists():
        return None
    return path.read_bytes()

def get_blob_path(sha256: str) -> Path | None:
    path = _blob_path(sha256)
    return path if path.exists() else None

def verify_checksum(data: bytes, declared: str) -> bool:
    actual = hashlib.sha256(data).hexdigest()
    declared_clean = declared.replace("sha256:", "").strip()
    return actual == declared_clean
