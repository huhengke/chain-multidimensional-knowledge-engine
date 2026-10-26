from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from platform_server.installation import configure
configure()
from engine.api.app import create_app
app = create_app()
