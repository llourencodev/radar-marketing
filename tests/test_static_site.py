"""Site estático (GitHub Pages): exportação, limpeza do banco e sincronização das fontes."""

import json
from datetime import timedelta

import gerar_site
from app import db, service, static_site
from app.collectors import FetchResult, RawItem
from app.pipeline import Processor
from app.textutil import to_iso, utcnow
from tests.conftest import TEST_SOURCES


def _seed(cfg):
    now = utcnow()
    with db.session(cfg.db_path) as conn:
        sources = {row["nome"]: dict(row) for row in conn.execute("SELECT * FROM sources")}
        proc = Processor(conn, now=now)
        proc.process(sources["Blog de SEO"], RawItem(
            titulo="Google anuncia core update de novembro de 2026", url="https://seo.example.com/core",
            trecho="Atualização do algoritmo começa hoje. " * 40, publicado_em=now - timedelta(hours=3)))
        proc.process(sources["Jornal de Propaganda"], RawItem(
            titulo="Google anuncia o core update de novembro de 2026", url="https://jornal.example.com/core",
            publicado_em=now - timedelta(hours=2)))
        proc.process(sources["Jornal de Propaganda"], RawItem(
            titulo="Agência lança campanha de Natal para rede de varejo", url="https://jornal.example.com/natal",
            publicado_em=now - timedelta(days=static_site.SITE_DAYS + 5)))
        conn.execute("UPDATE sources SET etag = 'segredo-etag' WHERE nome = 'Blog de SEO'")


def test_export_gera_pagina_e_dados(cfg, tmp_path):
    _seed(cfg)
    out = tmp_path / "site"
    (out / "velho").mkdir(parents=True)
    info = static_site.export(cfg, out)

    assert info["articles"] == 2  # a notícia fora da janela do site não é publicada
    assert not (out / "velho").exists()
    for name in ("index.html", "app.js", "styles.css", "static-api.js", ".nojekyll"):
        assert (out / name).exists()
    html = (out / "index.html").read_text(encoding="utf-8")
    assert "Content-Security-Policy" in html
    assert html.index("static-api.js") < html.index('src="app.js"')

    data = json.loads((out / "data" / "radar.json").read_text(encoding="utf-8"))
    assert data["generated_at"] == info["generated_at"]
    assert len(data["meta"]["categorias"]) == 8 and "palavras" not in data["meta"]["categorias"][0]
    assert data["status"]["coletando"] is False
    by_url = {a["url"]: a for a in data["articles"]}
    core = by_url["https://seo.example.com/core"]
    assert len(core["trecho"]) == static_site.EXCERPT_MAX and core["categorias"]
    assert core["grupo_id"] == by_url["https://jornal.example.com/core"]["grupo_id"]  # duplicadas vão juntas
    assert "imagem_origem" not in core

    fontes = json.loads((out / "data" / "fontes.json").read_text(encoding="utf-8"))
    assert len(fontes["sources"]) == 3
    assert "segredo-etag" not in json.dumps(fontes)
    status = json.loads((out / "data" / "status.json").read_text(encoding="utf-8"))
    assert status["generated_at"] == info["generated_at"]


def test_prune_remove_noticias_antigas(cfg):
    _seed(cfg)
    old = to_iso(utcnow() - timedelta(days=static_site.KEEP_DAYS + 1))
    with db.session(cfg.db_path) as conn:
        conn.execute("UPDATE articles SET coletado_em = ? WHERE url LIKE '%natal'", (old,))
        conn.execute("INSERT INTO collection_runs (inicio, gatilho) VALUES (?, 'manual')", (old,))
    assert static_site.prune(cfg) == 1
    with db.session(cfg.db_path) as conn:
        assert conn.execute("SELECT COUNT(*) FROM articles").fetchone()[0] == 2
        assert conn.execute("SELECT COUNT(*) FROM article_analysis").fetchone()[0] == 2
        assert conn.execute("SELECT COUNT(*) FROM articles_fts").fetchone()[0] == 2
        assert conn.execute("SELECT COUNT(*) FROM collection_runs").fetchone()[0] == 0
    assert static_site.prune(cfg) == 0


def test_sincronizar_fontes_segue_o_arquivo_de_configuracao(cfg):
    config = [dict(TEST_SOURCES[0]), dict(TEST_SOURCES[1], ativo=False)]  # a terceira saiu do arquivo
    gerar_site.sincronizar_fontes(cfg, config)
    with db.session(cfg.db_path) as conn:
        ativo = {row["nome"]: row["ativo"] for row in conn.execute("SELECT nome, ativo FROM sources")}
    assert ativo == {"Blog de SEO": 1, "Plataforma Oficial": 0, "Jornal de Propaganda": 0}
    gerar_site.sincronizar_fontes(cfg, TEST_SOURCES)
    with db.session(cfg.db_path) as conn:
        assert conn.execute("SELECT COUNT(*) FROM sources WHERE ativo = 1").fetchone()[0] == 3


def test_tolerancia_antecipa_fontes_quase_vencidas(cfg):
    calls = []

    def fetcher(client, source, throttle, robots, known, fetch_images):
        calls.append(source["nome"])
        return FetchResult(status=200)

    almost_due = to_iso(utcnow() - timedelta(minutes=50))  # intervalo padrão: 60 min
    with db.session(cfg.db_path) as conn:
        conn.execute("UPDATE sources SET ultima_tentativa = ?", (almost_due,))
    assert service.run_collection(cfg, fetcher=fetcher)["status"] == "nada_a_fazer"
    assert service.run_collection(cfg, fetcher=fetcher, tolerance_min=15)["fontes"] == 3
    assert len(calls) == 3
