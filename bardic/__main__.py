"""python -m bardic"""
import uvicorn

from .config import environment_value, load_project_env


def main():
    load_project_env()
    uvicorn.run("bardic.app:app", host="127.0.0.1", port=int(environment_value("PORT", "8765")))


if __name__ == "__main__":
    main()
