"""API local (FastAPI), agendador da coleta automática e entrega da interface."""

from __future__ import annotations

import asyncio
import json
import logging
import sqlite3
import threading
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
from typing import Literal

from fastapi import FastAPI, HTTPException, Query
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from . import __version__, collectors, db, service, settings
from .pipeline import SINAIS
from .textutil import fts_query, is_http_url, to_iso, utcnow

log = logging.getLogger("radar.api")

ARTICLE_SELECT = """
SELECT a.id, a.titulo, a.url, a.veiculo, a.imagem_url, a.imagem_origem, a.publicado_em,
       a.data_confirmada, a.coletado_em, a.idioma, a.oficial, a.relevancia, a.status,
       a.grupo_id, a.source_id, s.nome AS fonte,
       an.resumo, an.pontos, an.impactos, an.termos, an.tipo, an.sinal, an.natureza, an.provedor,
       COALESCE(g.n_fontes, 1) AS n_fontes
FROM articles a
JOIN sources s ON s.id = a.source_id
JOIN article_analysis an ON an.article_id = a.id
LEFT JOIN article_groups g ON g.id = a.grupo_id
"""
DATE_EXPR = "COALESCE(a.publicado_em, a.coletado_em)"
PERIOD_WINDOWS = {
    "1h": timedelta(hours=1),
    "24h": timedelta(hours=24),
    "7d": timedelta(days=7),
    "30d": timedelta(days=30),
    "90d": timedelta(days=90),
    "1a": timedelta(days=365),
}
REPRESENTATIVE = (
    "(a.grupo_id IS NULL OR a.id = (SELECT x.id FROM articles x WHERE x.grupo_id = a.grupo_id "
    "ORDER BY x.oficial DESC, x.relevancia DESC, x.id LIMIT 1))"
)


class SourceIn(BaseModel):
    nome: str = Field(min_length=2, max_length=120)
    tipo: Literal["rss", "html", "search"]
    url: str | None = None
    consulta: str | None = Field(default=None, max_length=200)
    motor: Literal["google", "bing"] = "google"
    categoria: str
    oficial: bool = False
    idioma: Literal["pt", "en"] = "pt"
    seletor: str | None = Field(default=None, max_length=200)


class SourcePatch(BaseModel):
    ativo: bool | None = None
    intervalo_min: int | None = Field(default=None, ge=5, le=1440)
    prioridade: int | None = Field(default=None, ge=1, le=3)


class WebFiles(StaticFiles):
    """Interface estática; o navegador sempre revalida, então atualizações aparecem na hora."""

    async def get_response(self, path, scope):
        response = await super().get_response(path, scope)
        response.headers["Cache-Control"] = "no-cache"
        return response


def _serialize(row: sqlite3.Row, categories: dict[int, list[str]], glossary: dict[str, str]) -> dict:
    item = dict(row)
    for key in ("pontos", "impactos", "termos"):
        item[key] = json.loads(item[key] or "[]")
    item["termos"] = [{"termo": t, "explicacao": glossary.get(t, "")} for t in item["termos"]]
    item["oficial"] = bool(item["oficial"])
    item["data_confirmada"] = bool(item["data_confirmada"])
    item["categorias"] = categories.get(item["id"], [])
    item["sinal_rotulo"] = SINAIS[item["sinal"]]["rotulo"] if item["sinal"] in SINAIS else None
    # Uma mudança de plataforma só é tratada como confirmada quando vem da própria plataforma.
    item["sinal_confirmado"] = item["oficial"] if item["sinal"] == "atualizacao_plataforma" else True
    item["resumo_por_ia"] = item["provedor"] != "regras"
    return item


def _load(conn: sqlite3.Connection, sql: str, params: list | tuple = ()) -> list[dict]:
    rows = conn.execute(sql, params).fetchall()
    ids = [row["id"] for row in rows]
    categories: dict[int, list[str]] = {}
    if ids:
        marks = ",".join("?" * len(ids))
        for cat in conn.execute(
            f"SELECT article_id, categoria FROM article_categories WHERE article_id IN ({marks}) ORDER BY confianca DESC",
            ids,
        ):
            categories.setdefault(cat["article_id"], []).append(cat["categoria"])
    glossary = settings.load_glossary()
    return [_serialize(row, categories, glossary) for row in rows]


def _local_day_start_utc() -> str:
    local_midnight = datetime.now().astimezone().replace(hour=0, minute=0, second=0, microsecond=0)
    return to_iso(local_midnight.astimezone(timezone.utc))


def create_app(cfg: settings.Settings | None = None, scheduler: bool = True) -> FastAPI:
    cfg = cfg or settings.get_settings()
    db.init_db(cfg.db_path, settings.load_sources_config())
    category_slugs = {c["slug"] for c in settings.load_categories()}

    async def _scheduler_loop() -> None:
        while True:
            try:
                await asyncio.to_thread(service.run_collection, cfg, "automatico", True)
            except Exception:  # noqa: BLE001 - o agendador nunca para por causa de um ciclo
                log.exception("Falha no ciclo automático de coleta")
            await asyncio.sleep(60)

    @asynccontextmanager
    async def lifespan(_: FastAPI):
        task = asyncio.create_task(_scheduler_loop()) if scheduler else None
        yield
        if task:
            task.cancel()

    app = FastAPI(title="Radar de Marketing", version=__version__, lifespan=lifespan)

    @app.get("/api/meta")
    def meta() -> dict:
        return {
            "versao": __version__,
            "categorias": [{k: v for k, v in c.items() if k != "palavras"} for c in settings.load_categories()],
            "sinais": {key: spec["rotulo"] for key, spec in SINAIS.items()},
            "ia": cfg.llm_provider,
            "intervalo_min": cfg.collect_interval_min,
        }

    @app.get("/api/dashboard")
    def dashboard() -> dict:
        week = to_iso(utcnow() - timedelta(days=7))
        with db.session(cfg.db_path) as conn:
            total = conn.execute("SELECT COUNT(*) FROM articles").fetchone()[0]
            today = conn.execute(
                "SELECT COUNT(*) FROM articles WHERE publicado_em >= ?", (_local_day_start_utc(),)
            ).fetchone()[0]
            per_category = {
                row["categoria"]: row["n"]
                for row in conn.execute(
                    f"""
                    SELECT ac.categoria, COUNT(*) AS n FROM article_categories ac
                    JOIN articles a ON a.id = ac.article_id
                    WHERE {DATE_EXPR} >= ? GROUP BY ac.categoria
                    """,
                    (week,),
                )
            }
            recent = f"WHERE {DATE_EXPR} >= ? AND {REPRESENTATIVE}"
            faixas = {
                "mais_relevantes": _load(
                    conn, f"{ARTICLE_SELECT} {recent} ORDER BY a.relevancia DESC, {DATE_EXPR} DESC LIMIT 8", (week,)
                ),
                "oficiais": _load(
                    conn, f"{ARTICLE_SELECT} {recent} AND a.oficial = 1 ORDER BY {DATE_EXPR} DESC LIMIT 4", (week,)
                ),
                "plataformas": _load(
                    conn,
                    f"{ARTICLE_SELECT} {recent} AND an.sinal = 'atualizacao_plataforma' ORDER BY {DATE_EXPR} DESC LIMIT 4",
                    (week,),
                ),
                "regulacao": _load(
                    conn,
                    f"{ARTICLE_SELECT} {recent} AND an.sinal = 'regulacao' ORDER BY {DATE_EXPR} DESC LIMIT 4",
                    (week,),
                ),
            }
        return {
            "total": total,
            "publicadas_hoje": today,
            "por_categoria_7d": per_category,
            "saude": service.health(cfg.db_path),
            "faixas": faixas,
        }

    @app.get("/api/articles")
    def list_articles(
        q: str = "",
        category: str = "",
        source: int | None = None,
        period: Literal["", "1h", "24h", "hoje", "7d", "30d", "90d", "1a"] = "",
        date_from: str = Query("", alias="from", pattern=r"^(\d{4}-\d{2}-\d{2})?$"),
        date_to: str = Query("", alias="to", pattern=r"^(\d{4}-\d{2}-\d{2})?$"),
        min_relevance: int = Query(0, ge=0, le=100),
        tipo: Literal["", "oficial", "jornalistico"] = "",
        sinal: str = "",
        sort: Literal["recent", "relevant"] = "recent",
        collapse: bool = True,
        limit: int = Query(30, ge=1, le=100),
        offset: int = Query(0, ge=0),
    ) -> dict:
        where, params = ["1 = 1"], []
        if q.strip():
            match = fts_query(q)
            if not match:
                return {"total": 0, "items": []}
            where.append("a.id IN (SELECT rowid FROM articles_fts WHERE articles_fts MATCH ?)")
            params.append(match)
        if category:
            where.append("EXISTS (SELECT 1 FROM article_categories ac WHERE ac.article_id = a.id AND ac.categoria = ?)")
            params.append(category)
        if source is not None:
            where.append("a.source_id = ?")
            params.append(source)
        if period == "hoje":
            where.append(f"{DATE_EXPR} >= ?")
            params.append(_local_day_start_utc())
        elif period:
            where.append(f"{DATE_EXPR} >= ?")
            params.append(to_iso(utcnow() - PERIOD_WINDOWS[period]))
        if date_from:
            where.append(f"{DATE_EXPR} >= ?")
            params.append(f"{date_from}T00:00:00Z")
        if date_to:
            where.append(f"{DATE_EXPR} <= ?")
            params.append(f"{date_to}T23:59:59Z")
        if min_relevance:
            where.append("a.relevancia >= ?")
            params.append(min_relevance)
        if tipo:
            where.append("an.tipo = ?")
            params.append(tipo)
        if sinal:
            where.append("an.sinal = ?")
            params.append(sinal)
        if collapse and not q.strip():
            where.append(REPRESENTATIVE)
        clause = " AND ".join(where)
        order = f"a.relevancia DESC, {DATE_EXPR} DESC" if sort == "relevant" else f"{DATE_EXPR} DESC, a.id DESC"
        with db.session(cfg.db_path) as conn:
            total = conn.execute(
                f"SELECT COUNT(*) FROM articles a JOIN article_analysis an ON an.article_id = a.id WHERE {clause}",
                params,
            ).fetchone()[0]
            items = _load(conn, f"{ARTICLE_SELECT} WHERE {clause} ORDER BY {order} LIMIT ? OFFSET ?", [*params, limit, offset])
        return {"total": total, "items": items}

    @app.get("/api/articles/{article_id}")
    def get_article(article_id: int) -> dict:
        with db.session(cfg.db_path) as conn:
            found = _load(conn, f"{ARTICLE_SELECT} WHERE a.id = ?", (article_id,))
            if not found:
                raise HTTPException(404, "Notícia não encontrada")
            article = found[0]
            article["trecho"] = conn.execute("SELECT trecho FROM articles WHERE id = ?", (article_id,)).fetchone()[0]
            article["relacionadas"] = []
            if article["grupo_id"]:
                article["relacionadas"] = _load(
                    conn,
                    f"{ARTICLE_SELECT} WHERE a.grupo_id = ? AND a.id != ? ORDER BY {DATE_EXPR}",
                    (article["grupo_id"], article_id),
                )
        return article

    @app.post("/api/refresh")
    def refresh() -> dict:
        if service.is_running():
            return {"status": "em_andamento"}
        threading.Thread(
            target=service.run_collection, args=(cfg, "manual", False), name="coleta-manual", daemon=True
        ).start()
        return {"status": "iniciada"}

    @app.get("/api/status")
    def status() -> dict:
        return service.health(cfg.db_path)

    @app.get("/api/runs")
    def runs(limit: int = Query(50, ge=1, le=200)) -> list[dict]:
        with db.session(cfg.db_path) as conn:
            return [dict(r) for r in conn.execute("SELECT * FROM collection_runs ORDER BY id DESC LIMIT ?", (limit,))]

    @app.get("/api/sources")
    def list_sources() -> list[dict]:
        with db.session(cfg.db_path) as conn:
            rows = conn.execute(
                """
                SELECT s.*, (SELECT COUNT(*) FROM articles a WHERE a.source_id = s.id) AS total_artigos
                FROM sources s ORDER BY s.ativo DESC, s.falhas_seguidas DESC, s.nome
                """
            ).fetchall()
        result = []
        for row in rows:
            item = dict(row)
            item["oficial"], item["ativo"] = bool(item["oficial"]), bool(item["ativo"])
            item["intervalo_efetivo_min"] = service.effective_interval(item, cfg.collect_interval_min)
            result.append(item)
        return result

    @app.post("/api/sources", status_code=201)
    def add_source(body: SourceIn) -> dict:
        if body.categoria not in category_slugs:
            raise HTTPException(422, "Categoria desconhecida")
        entry = body.model_dump()
        if body.tipo == "search":
            if not (body.consulta or "").strip():
                raise HTTPException(422, "Informe a consulta de busca")
            entry["url"] = db.search_url(body.motor, body.consulta.strip(), body.idioma)
        elif not is_http_url(body.url):
            raise HTTPException(422, "Informe um endereço http(s) válido")
        entry["url"] = entry["url"].strip()
        probe = collectors.probe_source(entry)
        if probe.error:
            raise HTTPException(422, f"A fonte não passou no teste: {probe.error}")
        try:
            with db.session(cfg.db_path) as conn:
                source_id = conn.execute(
                    """
                    INSERT INTO sources (nome, tipo, url, consulta, categoria, oficial, idioma, seletor)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (body.nome.strip(), body.tipo, entry["url"], body.consulta, body.categoria,
                     1 if body.oficial else 0, body.idioma, body.seletor),
                ).lastrowid
        except sqlite3.IntegrityError:
            raise HTTPException(409, "Essa fonte já está cadastrada") from None
        return {"id": source_id, "itens_no_teste": len(probe.items)}

    @app.patch("/api/sources/{source_id}")
    def patch_source(source_id: int, body: SourcePatch) -> dict:
        changes = body.model_dump(exclude_none=True)
        if not changes:
            raise HTTPException(422, "Nada para alterar")
        if "ativo" in changes:
            changes["ativo"] = 1 if changes["ativo"] else 0
            if changes["ativo"]:
                changes["falhas_seguidas"] = 0  # reativar zera a espera por falhas
        assignments = ", ".join(f"{column} = ?" for column in changes)
        with db.session(cfg.db_path) as conn:
            cursor = conn.execute(f"UPDATE sources SET {assignments} WHERE id = ?", [*changes.values(), source_id])
            if cursor.rowcount == 0:
                raise HTTPException(404, "Fonte não encontrada")
        return {"id": source_id, **changes}

    app.mount("/", WebFiles(directory=settings.WEB_DIR, html=True), name="web")
    return app
