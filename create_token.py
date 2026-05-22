#!/usr/bin/env python3
"""Run this on the host to create auth tokens."""
import sys
import os
sys.path.insert(0, "/app")
os.environ["TOKEN_DB_PATH"] = "/data/db/tokens.db"

from registry.auth import create_token

name = sys.argv[1] if len(sys.argv) > 1 else "admin"
token = create_token(name)
print(f"Token for '{name}': {token}")
print("Store this securely — it will not be shown again.")
