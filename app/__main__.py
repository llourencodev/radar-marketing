"""Ponto de entrada: `python -m app` sobe o servidor; `--collect` roda uma coleta e sai."""

from __future__ import annotations

import argparse
import logging
import threading
import webbrowser

import uvicorn

from . import service, settings
from .main import create_app


def main() -> None:
    parser = argparse.ArgumentParser(description="Radar de Marketing")
    parser.add_argument("--open", action="store_true", help="abre o navegador ao iniciar")
    parser.add_argument("--collect", action="store_true", help="executa uma coleta completa e encerra")
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    logging.getLogger("httpx").setLevel(logging.WARNING)
    cfg = settings.get_settings()

    if args.collect:
        service.prepare_db(cfg)
        print(service.run_collection(cfg, trigger="manual", only_due=False))
        return

    url = f"http://{cfg.host}:{cfg.port}"
    if args.open:
        threading.Timer(1.5, webbrowser.open, args=(url,)).start()
    uvicorn.run(create_app(cfg), host=cfg.host, port=cfg.port, log_level="warning")


if __name__ == "__main__":
    main()
