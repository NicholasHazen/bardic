"""Load explicit project configuration without searching unrelated directories."""
import os
from pathlib import Path

from dotenv import load_dotenv


PROJECT_ROOT = Path(__file__).resolve().parent.parent
RENAMED_SETTINGS = ("DATA_DIR", "PORT")


def load_project_env() -> None:
    # Shell/deployment variables win. Treat credential values literally, including
    # dollar signs; no variable expansion or search through parent directories.
    # An old-prefix shell value must also beat a new-prefix value in .env.
    # Within the same source, the Bardic spelling takes precedence.
    shell_aliases = {
        f"BARDIC_{name}": os.environ[f"SPINTAILS_{name}"]
        for name in RENAMED_SETTINGS
        if f"SPINTAILS_{name}" in os.environ and f"BARDIC_{name}" not in os.environ
    }
    load_dotenv(PROJECT_ROOT / ".env", override=False, interpolate=False)
    os.environ.update(shell_aliases)


def environment_value(name: str, default: str | None = None) -> str | None:
    """Read a Bardic setting, accepting its original Spin Tails spelling."""
    return os.environ.get(f"BARDIC_{name}", os.environ.get(f"SPINTAILS_{name}", default))


def data_directory() -> Path:
    """Choose a library without moving, rewriting, or splitting existing data."""
    configured = environment_value("DATA_DIR")
    if configured is not None:
        return Path(configured)
    current, legacy = Path(".bardic"), Path(".spintails")
    if not current.exists() and legacy.is_dir():
        return legacy
    return current
