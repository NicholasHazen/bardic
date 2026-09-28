"""python -m bardic"""
import uvicorn

from . import lan
from .config import environment_value, load_project_env


def main():
    load_project_env()
    port = int(environment_value("PORT", "8765"))
    try:
        host, name = lan.bind_host(), lan.lan_name()
        lan.allowed_hosts()  # Report a bad setting now rather than as an import error inside uvicorn.
        advertiser = lan.Advertiser(name, port, lan.advertised_address(host)) if name else None
    except ValueError as exc:
        raise SystemExit(f"Bardic: {exc}") from None
    if lan.port_in_use(port):
        raise SystemExit(f"Bardic: port {port} is already in use on this computer. "
                         "Stop that server or choose another BARDIC_PORT.")
    if advertiser:
        advertiser.start()
    try:
        uvicorn.run("bardic.app:app", host=host, port=port)
    finally:
        if advertiser:
            advertiser.stop()


if __name__ == "__main__":
    main()
