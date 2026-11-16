from datetime import timedelta

from app import db, service
from app.collectors import FetchResult, RawItem
from app.textutil import to_iso, utcnow


def _fetcher(fail_host=None):
    calls = []

    def fetch(client, source, throttle, robots, known, fetch_images):
        calls.append(source["nome"])
        if fail_host and fail_host in source["url"]:
            return FetchResult(status=500, error="HTTP 500", duration_ms=5)
        slug = source["url"].split("//")[1].split(".")[0]
        return FetchResult(
            status=200,
            etag='"e1"',
            duration_ms=5,
            items=[
                RawItem(titulo=f"Notícia exclusiva número um do site {slug} sobre marketing", url=f"{source['url']}/1", publicado_em=utcnow()),
                RawItem(titulo=f"Notícia exclusiva número dois do site {slug} sobre anúncios", url=f"{source['url']}/2", publicado_em=utcnow()),
            ],
        )

    fetch.calls = calls
    return fetch


def test_ciclo_grava_itens_e_nao_repete(cfg):
    fetch = _fetcher()
    first = service.run_collection(cfg, trigger="manual", only_due=False, fetcher=fetch)
    assert first["status"] == "concluida" and first["fontes_ok"] == 3 and first["itens_novos"] == 6
    second = service.run_collection(cfg, trigger="manual", only_due=False, fetcher=fetch)
    assert second["itens_novos"] == 0
    with db.session(cfg.db_path) as conn:
        assert conn.execute("SELECT COUNT(*) FROM articles").fetchone()[0] == 6
        assert conn.execute("SELECT COUNT(*) FROM collection_runs WHERE fim IS NOT NULL").fetchone()[0] == 2
        assert conn.execute("SELECT etag FROM sources LIMIT 1").fetchone()[0] == '"e1"'


def test_falha_de_uma_fonte_nao_interrompe_as_outras(cfg):
    result = service.run_collection(cfg, trigger="manual", only_due=False, fetcher=_fetcher(fail_host="jornal."))
    assert result["fontes_ok"] == 2 and result["fontes_falha"] == 1 and result["itens_novos"] == 4
    with db.session(cfg.db_path) as conn:
        failed = conn.execute("SELECT falhas_seguidas, ultimo_erro FROM sources WHERE url LIKE '%jornal.%'").fetchone()
        assert failed["falhas_seguidas"] == 1 and failed["ultimo_erro"] == "HTTP 500"
    health = service.health(cfg.db_path)
    assert health["nivel"] == "amarelo" and health["fontes_com_falha"] == 1


def test_so_coleta_fontes_vencidas(cfg):
    fetch = _fetcher()
    service.run_collection(cfg, only_due=True, fetcher=fetch)
    assert len(fetch.calls) == 3
    again = service.run_collection(cfg, only_due=True, fetcher=fetch)
    assert again["status"] == "nada_a_fazer" and len(fetch.calls) == 3


def test_intervalo_dobra_apos_tres_falhas_ate_24h():
    assert service.effective_interval({"intervalo_min": 60, "falhas_seguidas": 2}, 60) == 60
    assert service.effective_interval({"intervalo_min": 60, "falhas_seguidas": 3}, 60) == 120
    assert service.effective_interval({"intervalo_min": 60, "falhas_seguidas": 5}, 60) == 480
    assert service.effective_interval({"intervalo_min": None, "falhas_seguidas": 30}, 60) == 1440


def test_is_due():
    now = utcnow()
    assert service.is_due({"ultima_tentativa": None}, 60, now)
    assert not service.is_due({"ultima_tentativa": to_iso(now - timedelta(minutes=10))}, 60, now)
    assert service.is_due({"ultima_tentativa": to_iso(now - timedelta(minutes=61))}, 60, now)


def test_segundo_pedido_durante_a_coleta_e_ignorado(cfg):
    assert service._run_lock.acquire(blocking=False)
    try:
        assert service.run_collection(cfg, fetcher=_fetcher()) == {"status": "em_andamento"}
        assert service.is_running()
    finally:
        service._run_lock.release()


def test_saude_sem_coleta(cfg):
    assert service.health(cfg.db_path)["nivel"] == "amarelo"
