"""Ciclo de coleta: escolhe as fontes vencidas, baixa em paralelo e grava em série."""

from __future__ import annotations

import logging
import sqlite3
import threading
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta
from pathlib import Path

from . import collectors, db, llm, settings
from .pipeline import Processor, recompute_relevance
from .textutil import from_iso, now_iso, to_iso, utcnow

log = logging.getLogger("radar.coleta")

_run_lock = threading.Lock()
MAX_WORKERS = 6


def is_running() -> bool:
    return _run_lock.locked()


def effective_interval(source: dict, default_min: int) -> int:
    """Intervalo da fonte; após 3 falhas seguidas dobra a cada falha, até 24 h."""
    interval = int(source.get("intervalo_min") or default_min)
    failures = int(source.get("falhas_seguidas") or 0)
    if failures >= 3:
        interval = min(settings.MAX_BACKOFF_MIN, interval * 2 ** (failures - 2))
    return interval


def is_due(source: dict, default_min: int, now: datetime) -> bool:
    last = from_iso(source.get("ultima_tentativa"))
    if last is None:
        return True
    return now - last >= timedelta(minutes=effective_interval(source, default_min))


def _record_result(conn: sqlite3.Connection, run_id: int, source: dict, result, new_items: int) -> None:
    now = now_iso()
    if result.error:
        conn.execute(
            "UPDATE sources SET ultima_tentativa = ?, falhas_seguidas = falhas_seguidas + 1, ultimo_erro = ? WHERE id = ?",
            (now, result.error, source["id"]),
        )
    else:
        conn.execute(
            """
            UPDATE sources SET ultima_tentativa = ?, ultimo_sucesso = ?, falhas_seguidas = 0,
                   ultimo_erro = NULL, etag = COALESCE(?, etag), last_modified = COALESCE(?, last_modified)
            WHERE id = ?
            """,
            (now, now, result.etag, result.last_modified, source["id"]),
        )
    conn.execute(
        "INSERT INTO collection_logs (run_id, source_id, status_http, erro, duracao_ms, itens_novos) VALUES (?, ?, ?, ?, ?, ?)",
        (run_id, source["id"], result.status, result.error, result.duration_ms, new_items),
    )


def run_collection(
    cfg: settings.Settings,
    trigger: str = "automatico",
    only_due: bool = True,
    fetcher=collectors.fetch_source,
    tolerance_min: int = 0,
) -> dict:
    """Executa um ciclo. Um segundo pedido durante a coleta é ignorado.

    `tolerance_min` antecipa fontes que vencem nos próximos minutos (útil quando o
    ciclo é disparado por um agendador externo de hora em hora, como o GitHub Actions).
    """
    if not _run_lock.acquire(blocking=False):
        return {"status": "em_andamento"}
    try:
        return _run(cfg, trigger, only_due, fetcher, tolerance_min)
    finally:
        _run_lock.release()


def _run(cfg: settings.Settings, trigger: str, only_due: bool, fetcher, tolerance_min: int = 0) -> dict:
    now = utcnow()
    conn = db.connect(cfg.db_path)
    try:
        sources = [dict(row) for row in conn.execute("SELECT * FROM sources WHERE ativo = 1")]
        if only_due:
            due_at = now + timedelta(minutes=tolerance_min)
            sources = [s for s in sources if is_due(s, cfg.collect_interval_min, due_at)]
        if not sources:
            return {"status": "nada_a_fazer", "fontes": 0, "itens_novos": 0}

        run_id = conn.execute(
            "INSERT INTO collection_runs (inicio, gatilho) VALUES (?, ?)", (to_iso(now), trigger)
        ).lastrowid
        conn.commit()
        known = {row[0] for row in conn.execute("SELECT url_canonica FROM articles")}

        with collectors.new_client() as client:
            throttle = collectors.HostThrottle()
            robots = collectors.RobotsCache(client, throttle)
            with ThreadPoolExecutor(max_workers=MAX_WORKERS, thread_name_prefix="coleta") as pool:
                results = list(
                    pool.map(
                        lambda s: fetcher(client, s, throttle, robots, known, cfg.fetch_page_image),
                        sources,
                    )
                )

        processor = Processor(conn, now=now)
        ok = failed = total_new = 0
        for source, result in zip(sources, results):
            new_items = 0
            try:
                for item in result.items:
                    if processor.process(source, item) is not None:
                        new_items += 1
            except sqlite3.Error as exc:
                conn.rollback()
                result.error = f"erro ao gravar: {exc}"[:300]
                new_items = 0
            _record_result(conn, run_id, source, result, new_items)
            conn.commit()
            total_new += new_items
            if result.error:
                failed += 1
                log.warning("Fonte com falha: %s (%s)", source["nome"], result.error)
            else:
                ok += 1

        recompute_relevance(conn, now=now)
        conn.execute(
            "UPDATE collection_runs SET fim = ?, fontes_ok = ?, fontes_falha = ?, itens_novos = ? WHERE id = ?",
            (now_iso(), ok, failed, total_new, run_id),
        )
        conn.commit()

        enriched = 0
        try:
            enriched = llm.enrich_pending(conn, llm.get_provider(cfg), cfg.llm_max_per_run)
        except llm.LLMUnavailable as exc:
            log.warning("IA desligada neste ciclo: %s", exc)
        log.info("Coleta %s: %d fontes ok, %d com falha, %d itens novos", trigger, ok, failed, total_new)
        return {
            "status": "concluida", "run_id": run_id, "fontes": len(sources), "fontes_ok": ok,
            "fontes_falha": failed, "itens_novos": total_new, "resumos_ia": enriched,
        }
    finally:
        conn.close()


def health(db_path: Path | str) -> dict:
    """Indicador de saúde: verde, amarelo (algumas fontes falharam) ou vermelho (parada)."""
    with db.session(db_path) as conn:
        last = conn.execute("SELECT * FROM collection_runs WHERE fim IS NOT NULL ORDER BY id DESC LIMIT 1").fetchone()
        counts = conn.execute(
            """
            SELECT COUNT(*) AS ativas,
                   SUM(CASE WHEN falhas_seguidas > 0 THEN 1 ELSE 0 END) AS com_falha,
                   MAX(ultimo_sucesso) AS ultimo_sucesso
            FROM sources WHERE ativo = 1
            """
        ).fetchone()
    ativas = counts["ativas"] or 0
    com_falha = counts["com_falha"] or 0
    last_success = from_iso(counts["ultimo_sucesso"])
    if last is None:
        level, message = "amarelo", "Nenhuma coleta concluída ainda"
    elif ativas and (com_falha == ativas or last_success is None or utcnow() - last_success > timedelta(hours=6)):
        level, message = "vermelho", "Coleta parada: nenhuma fonte respondeu recentemente"
    elif com_falha:
        level, message = "amarelo", f"{com_falha} de {ativas} fontes com falha"
    else:
        level, message = "verde", "Coletando normalmente"
    return {
        "nivel": level,
        "mensagem": message,
        "coletando": is_running(),
        "fontes_ativas": ativas,
        "fontes_com_falha": com_falha,
        "ultima_atualizacao": last["fim"] if last else None,
        "ultimo_ciclo": dict(last) if last else None,
    }
