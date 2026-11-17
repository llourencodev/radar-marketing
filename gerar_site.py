"""Coleta as fontes vencidas e gera o site estático em site/ (usado pelo GitHub Actions).

Uso:
  python gerar_site.py               coleta e gera o site
  python gerar_site.py --sem-coleta  só gera o site com o que já está no banco
  python gerar_site.py --saida pasta
"""

from __future__ import annotations

import argparse
import logging
import os
import sqlite3
import sys

from app import db, service, settings, static_site

# Fontes que vencem até 15 minutos depois desta execução já entram nela (o agendamento é de hora em hora).
TOLERANCIA_MIN = 15
# Não publica um site com menos notícias que isso (evita trocar o site bom por um vazio se tudo falhar).
MINIMO_NOTICIAS = int(os.environ.get("RADAR_SITE_MIN", "1"))

log = logging.getLogger("radar.site")


def sincronizar_fontes(cfg: settings.Settings, fontes: list[dict]) -> None:
    """No GitHub, config/sources.json é a única forma de controlar as fontes: o campo "ativo"
    vale sempre, e fontes apagadas do arquivo deixam de ser coletadas (o histórico continua no banco)."""
    ativos = {db.source_url(f): 1 if f.get("ativo", True) else 0 for f in fontes}
    with db.session(cfg.db_path) as conn:
        for row in conn.execute("SELECT id, url, ativo FROM sources").fetchall():
            desejado = ativos.get(row["url"], 0)
            if row["ativo"] != desejado:
                conn.execute("UPDATE sources SET ativo = ? WHERE id = ?", (desejado, row["id"]))


def main() -> None:
    parser = argparse.ArgumentParser(description="Radar de Marketing: coleta e site estático")
    parser.add_argument("--sem-coleta", action="store_true", help="só gera o site com o que já está no banco")
    parser.add_argument("--saida", default="site", help="pasta de saída (padrão: site)")
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    logging.getLogger("httpx").setLevel(logging.WARNING)

    cfg = settings.get_settings()
    fontes = settings.load_sources_config()
    service.prepare_db(cfg)
    try:
        if not args.sem_coleta:
            sincronizar_fontes(cfg, fontes)
            log.info("Coleta: %s", service.run_collection(
                cfg, trigger="agendado (GitHub)", only_due=True, tolerance_min=TOLERANCIA_MIN))
            removidas = static_site.prune(cfg)
            if removidas:
                log.info("%s notícia(s) antiga(s) removida(s) do banco", removidas)
        info = static_site.export(cfg, settings.BASE_DIR / args.saida)
        log.info("Site gerado em %s: %s notícias", args.saida, info["articles"])
    finally:
        conn = sqlite3.connect(cfg.db_path)  # grava tudo no arquivo principal antes de guardar o banco
        conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
        conn.close()

    if info["articles"] < MINIMO_NOTICIAS:
        log.error("Só %s notícia(s) no site gerado: a publicação foi cancelada e o site atual continua no ar.",
                  info["articles"])
        sys.exit(1)


if __name__ == "__main__":
    main()
