from datetime import timedelta

import pytest
from fastapi.testclient import TestClient

from app import collectors, db, service
from app.collectors import FetchResult, RawItem
from app.main import create_app
from app.pipeline import Processor
from app.textutil import utcnow


@pytest.fixture
def client(cfg):
    now = utcnow()
    with db.session(cfg.db_path) as conn:
        sources = {row["nome"]: dict(row) for row in conn.execute("SELECT * FROM sources")}
        proc = Processor(conn, now=now)
        proc.process(sources["Plataforma Oficial"], RawItem(
            titulo="Meta Ads ganha novo recurso de segmentação por inteligência artificial",
            url="https://plataforma.example.com/ia", trecho="Anunciantes poderão otimizar o ROAS das campanhas.",
            publicado_em=now - timedelta(hours=1), imagem_url="https://plataforma.example.com/i.png", imagem_origem="feed"))
        proc.process(sources["Blog de SEO"], RawItem(
            titulo="Google anuncia core update de novembro de 2026",
            url="https://seo.example.com/core", trecho="Atualização do algoritmo começa hoje.",
            publicado_em=now - timedelta(hours=3)))
        proc.process(sources["Jornal de Propaganda"], RawItem(
            titulo="Google anuncia o core update de novembro de 2026",
            url="https://jornal.example.com/core", publicado_em=now - timedelta(hours=2)))
        proc.process(sources["Jornal de Propaganda"], RawItem(
            titulo="Agência lança campanha de Natal para rede de varejo",
            url="https://jornal.example.com/natal", trecho="Filme estreia na TV aberta.",
            publicado_em=now - timedelta(days=10)))
    with TestClient(create_app(cfg, scheduler=False)) as test_client:
        yield test_client


def test_meta_e_interface(client):
    meta = client.get("/api/meta").json()
    assert len(meta["categorias"]) == 8 and "palavras" not in meta["categorias"][0]
    assert {"digital", "construcao"} <= {c["slug"] for c in meta["categorias"]}
    assert meta["ia"] == "none"
    page = client.get("/")
    assert page.status_code == 200 and "Radar de" in page.text
    assert client.get("/app.js").status_code == 200


def test_lista_agrupa_duplicadas_e_ordena_por_data(client):
    data = client.get("/api/articles").json()
    assert data["total"] == 3  # as duas versões do core update viram uma
    titles = [a["titulo"] for a in data["items"]]
    assert titles[0].startswith("Meta Ads")
    assert client.get("/api/articles", params={"collapse": "false"}).json()["total"] == 4


def test_campos_do_cartao(client):
    article = client.get("/api/articles", params={"tipo": "oficial"}).json()["items"][0]
    assert article["oficial"] is True and article["tipo"] == "oficial"
    assert article["sinal"] == "atualizacao_plataforma" and article["sinal_confirmado"] is True
    assert article["resumo_por_ia"] is False
    assert article["imagem_url"] == "https://plataforma.example.com/i.png"
    assert {"termo": "ROAS"}.items() <= article["termos"][0].items() or any(t["termo"] == "ROAS" for t in article["termos"])
    assert all(t["explicacao"] for t in article["termos"])


def test_atualizacao_de_plataforma_jornalistica_fica_como_relatada(client):
    items = client.get("/api/articles", params={"q": "core update", "tipo": "jornalistico"}).json()["items"]
    assert items and all(a["sinal_confirmado"] is False for a in items if a["sinal"] == "atualizacao_plataforma")


def test_filtros(client):
    get = lambda **params: client.get("/api/articles", params=params).json()
    assert get(category="seo")["total"] >= 1
    assert get(q="segmentacao")["total"] == 1  # busca sem diferenciar acentos
    assert get(q="natal", period="7d")["total"] == 0
    assert get(q="natal", period="30d")["total"] == 1
    assert get(q="natal", period="1h")["total"] == 0
    assert get(period="1h")["total"] <= get(period="24h")["total"] <= get(period="90d")["total"] <= get(period="1a")["total"]
    strong = get(min_relevance=60)
    assert 0 < strong["total"] < 3 and all(a["relevancia"] >= 60 for a in strong["items"])
    assert get(sort="relevant")["items"][0]["relevancia"] >= get(sort="relevant")["items"][-1]["relevancia"]
    assert get(q="((( ")["total"] == 0
    assert client.get("/api/articles", params={"period": "ontem"}).status_code == 422
    assert client.get("/api/articles", params={"from": "15/11/2026"}).status_code == 422


def test_detalhe_traz_linha_do_tempo(client):
    core = client.get("/api/articles", params={"q": "core update", "collapse": "false"}).json()["items"][0]
    detail = client.get(f"/api/articles/{core['id']}").json()
    assert len(detail["relacionadas"]) == 1 and detail["n_fontes"] == 2
    assert "trecho" in detail
    assert client.get("/api/articles/99999").status_code == 404


def test_dashboard_e_status(client):
    data = client.get("/api/dashboard").json()
    assert data["total"] == 4
    assert data["faixas"]["mais_relevantes"] and data["faixas"]["oficiais"]
    assert data["saude"]["nivel"] in ("verde", "amarelo", "vermelho")
    assert client.get("/api/status").json()["coletando"] is False
    assert client.get("/api/runs").json() == []


def test_refresh_dispara_coleta_manual(client, monkeypatch):
    called = []
    monkeypatch.setattr(service, "run_collection", lambda cfg, trigger, only_due: called.append((trigger, only_due)))
    assert client.post("/api/refresh").json() == {"status": "iniciada"}
    for _ in range(50):
        if called:
            break
        import time
        time.sleep(0.02)
    assert called == [("manual", False)]


def test_fontes_listar_adicionar_e_desativar(client, monkeypatch):
    sources = client.get("/api/sources").json()
    assert len(sources) >= 3 and "intervalo_efetivo_min" in sources[0]

    monkeypatch.setattr(collectors, "probe_source", lambda entry: FetchResult(status=404, error="HTTP 404"))
    body = {"nome": "Novo blog", "tipo": "rss", "url": "https://novo.example.com/feed", "categoria": "martech"}
    refused = client.post("/api/sources", json=body)
    assert refused.status_code == 422 and "HTTP 404" in refused.json()["detail"]

    monkeypatch.setattr(collectors, "probe_source", lambda entry: FetchResult(status=200, items=[RawItem("t", "https://x.com")]))
    created = client.post("/api/sources", json=body)
    assert created.status_code == 201 and created.json()["itens_no_teste"] == 1
    assert client.post("/api/sources", json=body).status_code == 409
    assert client.post("/api/sources", json={**body, "url": "ftp://x"}).status_code == 422
    assert client.post("/api/sources", json={**body, "categoria": "inexistente"}).status_code == 422
    search = client.post("/api/sources", json={"nome": "Busca nova", "tipo": "search", "consulta": "retail media", "categoria": "mercado"})
    assert search.status_code == 201

    source_id = created.json()["id"]
    assert client.patch(f"/api/sources/{source_id}", json={"ativo": False}).json()["ativo"] == 0
    assert client.patch(f"/api/sources/{source_id}", json={}).status_code == 422
    assert client.patch("/api/sources/99999", json={"ativo": True}).status_code == 404
