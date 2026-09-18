"""Create local config with fresh secrets. Never overwrites existing .env."""
import os
import secrets
from pathlib import Path

root = Path(__file__).resolve().parent.parent
target = root / ".env"
if target.exists():
    raise SystemExit(".env already exists; left unchanged.")
content = (root / ".env.example").read_text()
for name in ["USER", "REVIEWER", "ADMIN", "DATABASE"]:
    content = content.replace(f"GENERATE_ME_{name}", secrets.token_urlsafe(36))
fd = os.open(target, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
with os.fdopen(fd, "w") as handle:
    handle.write(content)
print("Created .env with separate user, reviewer and admin keys. Keep this file private.")
print("For the UI, copy USER_API_KEY from .env. For reviews, sign in with REVIEWER_API_KEY.")
