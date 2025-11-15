
from __future__ import annotations
import os

def get_allowed_origins():
    raw = os.getenv("CORS_ALLOW_ORIGINS", "*")
    if raw.strip() == "*":
        return ["*"]
    return [x.strip() for x in raw.split(",") if x.strip()]
