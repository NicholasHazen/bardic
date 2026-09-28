"""python -m spintails"""
import os
import uvicorn

from .config import load_project_env


def main():
    load_project_env()
    uvicorn.run("spintails.app:app", host="127.0.0.1", port=int(os.environ.get("SPINTAILS_PORT", "8765")))


if __name__ == "__main__":
    main()
