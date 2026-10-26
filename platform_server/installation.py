"""Project-owned installation secrets. Never discovers credentials from other projects."""
import json
import os
import secrets
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

def configure():
    root = Path(os.environ.get("PLATFORM_INSTALLATION_DIR", ROOT / ".runtime/standalone"))
    root.mkdir(parents=True, exist_ok=True)
    target = root / "installation.json"
    if not target.exists():
        try:
            with target.open("x", encoding="utf-8") as out:
                json.dump({"jwt_secret": secrets.token_urlsafe(48), "service_token": secrets.token_urlsafe(48)}, out)
        except FileExistsError:
            pass
    config = json.loads(target.read_text(encoding="utf-8"))
    for key, value in {"JWT_SECRET": config["jwt_secret"], "ENGINE_SERVICE_TOKEN": config["service_token"]}.items():
        os.environ[key] = value
    return root
