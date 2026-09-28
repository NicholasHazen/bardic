"""Load explicit project configuration without searching unrelated directories."""
from pathlib import Path

from dotenv import load_dotenv


PROJECT_ROOT = Path(__file__).resolve().parent.parent


def load_project_env() -> None:
    # Shell/deployment variables win. Treat credential values literally, including
    # dollar signs; no variable expansion or search through parent directories.
    load_dotenv(PROJECT_ROOT / ".env", override=False, interpolate=False)
