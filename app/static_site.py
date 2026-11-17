"""Versão estática do Radar (GitHub Pages): copia a interface e exporta os dados em JSON.

A busca, os filtros e o painel passam a ser calculados no navegador (web/static-api.js),
com o mesmo comportamento da API do servidor local.
"""

from __future__ import annotations

import json
import os
import shutil
from datetime import timedelta
from pathlib import Path

from . import __version__, db, service, settings
from .main import ARTICLE_SELECT, DATE_EXPR, _load
from .pipeline import SINAIS
from .textutil import now_iso, to_iso, utcnow

SITE_DAYS = int(os.environ.get("RADAR_SITE_DAYS", "90"))        # janela de notícias publicadas no site
SITE_MAX_ITEMS = int(os.environ.get("RADAR_SITE_MAX", "4000"))  # limite de notícias no JSON (~2 KB cada)
KEEP_DAYS = int(os.environ.get("RADAR_KEEP_DAYS", "200"))       # notícias mais antigas saem do banco
EXCERPT_MAX = 500

# O site só carrega os próprios arquivos; imagens das notícias podem vir de qualquer site https.
CSP = (
    "default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; "
    "img-src 'self' https: data:; connect-src 'self'; object-src 'none'; base-uri 'none'; form-action 'self'"
)
WEB_FILES = ("app.js", "styles.css", "static-api.js")
SCRIPT_TAG = '<script src="app.js"></script>'
CHARSET_TAG = '<meta charset="utf-8">'
PRIVATE_SOURCE_FIELDS = ("etag", "last_modified")


def _articles(cfg: settings.Settings) -> list[dict]:
    since = to_iso(utcnow() - timedelta(days=SITE_DAYS))
    with db.session(cfg.db_path) as conn:
        items = _load(
            conn,
            f"{ARTICLE_SELECT} WHERE {DATE_EXPR} >= ? ORDER BY {DATE_EXPR} DESC, a.id DESC LIMIT ?",
            (since, SITE_MAX_ITEMS),
        )
        excerpts = dict(conn.execute("SELECT id, trecho FROM articles").fetchall()) if items else {}
    for item in items:
        item["trecho"] = (excerpts.get(item["id"]) or "")[:EXCERPT_MAX]
        item.pop("imagem_origem", None)
    return items


def _sources(cfg: settings.Settings) -> list[dict]:
    with db.session(cfg.db_path) as conn:
        rows = conn.execute(
            """
            SELECT s.*, (SELECT COUNT(*) FROM articles a WHERE a.source_id = s.id) AS total_artigos
            FROM sources s ORDER BY s.ativo DESC, s.falhas_seguidas DESC, s.nome
            """
        ).fetchall()
    result = []
    for row in rows:
        item = {key: value for key, value in dict(row).items() if key not in PRIVATE_SOURCE_FIELDS}
        item["oficial"], item["ativo"] = bool(item["oficial"]), bool(item["ativo"])
        item["intervalo_efetivo_min"] = service.effective_interval(item, cfg.collect_interval_min)
        result.append(item)
    return result


def _page(web_dir: Path) -> str:
    """index.html adaptado: política de conteúdo restrita e a API estática antes da interface."""
    html = (web_dir / "index.html").read_text(encoding="utf-8")
    if SCRIPT_TAG not in html or CHARSET_TAG not in html:
        raise RuntimeError("web/index.html mudou: não foi possível adaptar a página para a versão estática")
    html = html.replace(SCRIPT_TAG, f'<script src="static-api.js"></script>\n{SCRIPT_TAG}', 1)
    return html.replace(
        CHARSET_TAG,
        f'{CHARSET_TAG}\n  <meta http-equiv="Content-Security-Policy" content="{CSP}">\n'
        '  <meta name="referrer" content="strict-origin-when-cross-origin">',
        1,
    )


def _write(path: Path, data) -> None:
    path.write_text(json.dumps(data, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")


def export(cfg: settings.Settings, out_dir: Path | str) -> dict:
    """Gera o site em `out_dir` (apaga o conteúdo anterior) e devolve um resumo."""
    out_dir = Path(out_dir)
    if out_dir.exists():
        shutil.rmtree(out_dir)
    (out_dir / "data").mkdir(parents=True)

    for name in WEB_FILES:
        shutil.copy2(settings.WEB_DIR / name, out_dir / name)
    (out_dir / "index.html").write_text(_page(settings.WEB_DIR), encoding="utf-8")
    (out_dir / ".nojekyll").write_text("", encoding="utf-8")

    generated = now_iso()
    health = service.health(cfg.db_path)
    health["coletando"] = False
    meta = {
        "versao": __version__,
        "categorias": settings.public_categories(),
        "sinais": {key: spec["rotulo"] for key, spec in SINAIS.items()},
        "ia": cfg.llm_provider,
        "intervalo_min": cfg.collect_interval_min,
        "repo": os.environ.get("GITHUB_REPOSITORY", ""),
    }
    articles = _articles(cfg)
    _write(out_dir / "data" / "radar.json",
           {"generated_at": generated, "meta": meta, "status": health, "articles": articles})
    with db.session(cfg.db_path) as conn:
        runs = [dict(r) for r in conn.execute("SELECT * FROM collection_runs ORDER BY id DESC LIMIT 50")]
    _write(out_dir / "data" / "fontes.json", {"generated_at": generated, "sources": _sources(cfg), "runs": runs})
    _write(out_dir / "data" / "status.json", {"generated_at": generated})
    return {"articles": len(articles), "generated_at": generated}


def prune(cfg: settings.Settings) -> int:
    """Mantém o banco enxuto entre execuções: notícias e históricos antigos saem.

    Itens velhos não voltam como novidade: os feeds só trazem publicações recentes.
    """
    old = to_iso(utcnow() - timedelta(days=KEEP_DAYS))
    old_runs = to_iso(utcnow() - timedelta(days=30))
    with db.session(cfg.db_path) as conn:
        ids = [row[0] for row in conn.execute("SELECT id FROM articles WHERE coletado_em < ?", (old,))]
        for start in range(0, len(ids), 500):
            chunk = ids[start:start + 500]
            marks = ",".join("?" * len(chunk))
            conn.execute(f"DELETE FROM articles_fts WHERE rowid IN ({marks})", chunk)
            conn.execute(f"DELETE FROM articles WHERE id IN ({marks})", chunk)  # análise e categorias em cascata
        conn.execute(
            "DELETE FROM article_groups WHERE id NOT IN "
            "(SELECT DISTINCT grupo_id FROM articles WHERE grupo_id IS NOT NULL)"
        )
        conn.execute("DELETE FROM collection_runs WHERE inicio < ?", (old_runs,))  # logs em cascata
    return len(ids)
