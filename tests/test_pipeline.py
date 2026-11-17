import json
from datetime import timedelta

from app import llm, pipeline
from app.collectors import RawItem
from app.pipeline import Processor, relevance_score

from .conftest import NOW


def _item(titulo, url, trecho="", dias=0, **extra):
    published = None if dias is None else NOW - timedelta(days=dias)
    return RawItem(titulo=titulo, url=url, trecho=trecho, publicado_em=published, **extra)


def test_classifica_por_palavras_chave_e_categoria_da_fonte(conn, sources):
    proc = Processor(conn, now=NOW)
    cats = proc.classify(sources["Jornal de Propaganda"], "Instagram muda Reels e afeta influenciadores", "")
    slugs = [slug for slug, _, _ in cats]
    assert slugs[0] == "social"  # regra com mais evidência vem primeiro
    assert "branding" in slugs  # categoria padrão da fonte é mantida
    assert dict((s, o) for s, o, _ in cats)["branding"] == "fonte"


def test_palavra_chave_exige_palavra_inteira(conn, sources):
    proc = Processor(conn, now=NOW)
    # "geografia" e "seoul" não podem acionar "geo" nem "seo"
    cats = proc.classify(sources["Jornal de Propaganda"], "Aula de geografia em Seoul reúne estudantes", "")
    assert [slug for slug, _, _ in cats] == ["branding"]


def test_classifica_lojas_de_construcao_varejo_e_marketing_digital(conn, sources):
    proc = Processor(conn, now=NOW)
    slugs = lambda title: [slug for slug, _, _ in proc.classify(sources["Blog de SEO"], title, "")]  # noqa: E731
    assert "construcao" in slugs("Leroy Merlin dá voz à casa em campanha de Black Friday")
    assert "construcao" in slugs("Telhanorte escolhe nova tecnologia para lojas de material de construção")
    assert "construcao" in slugs("Magazine Luiza e Casas Bahia apostam em lojas físicas no Natal")
    assert "digital" in slugs("Varejo acelera estratégia omnichannel com marketing digital")
    # "reforma tributária" não é assunto de lojas de construção
    assert "construcao" not in slugs("Reforma tributária muda regras para o setor de serviços")


def test_reclassifica_o_banco_quando_as_abas_mudam(conn, sources):
    base = [{"slug": "seo", "nome": "SEO", "palavras": ["seo"]}, {"slug": "branding", "nome": "B", "palavras": ["campanha"]}]
    proc = Processor(conn, categories=base, now=NOW)
    article_id = proc.process(sources["Jornal de Propaganda"], _item("Leroy Merlin lança campanha de Natal", "https://jornal.example.com/lm"))
    conn.commit()
    cats = lambda: {r[0] for r in conn.execute("SELECT categoria FROM article_categories WHERE article_id = ?", (article_id,))}  # noqa: E731
    assert cats() == {"branding"}

    assert pipeline.reclassify_if_changed(conn, base) >= 1  # primeira vez grava a impressão digital
    assert pipeline.reclassify_if_changed(conn, base) is None  # nada mudou: não refaz
    novas = base + [{"slug": "construcao", "nome": "Construção", "palavras": ["leroy merlin"]}]
    assert pipeline.reclassify_if_changed(conn, novas) >= 1
    assert cats() == {"branding", "construcao"}
    conn.execute("UPDATE sources SET categoria = 'seo' WHERE nome = 'Jornal de Propaganda'")
    assert pipeline.reclassify_if_changed(conn, novas) >= 1  # tema da fonte mudou
    assert "seo" in cats()


def test_processa_grava_noticia_completa(conn, sources):
    proc = Processor(conn, now=NOW)
    article_id = proc.process(
        sources["Plataforma Oficial"],
        _item(
            "Google Ads: novo recurso de lances para Performance Max",
            "https://plataforma.example.com/pmax?utm_source=x",
            "A plataforma apresenta nova ferramenta para melhorar o ROAS e o CPA das campanhas.",
        ),
    )
    row = conn.execute("SELECT * FROM articles WHERE id = ?", (article_id,)).fetchone()
    analysis = conn.execute("SELECT * FROM article_analysis WHERE article_id = ?", (article_id,)).fetchone()
    assert row["url_canonica"] == "https://plataforma.example.com/pmax"
    assert row["status"] == "verificada" and row["data_confirmada"] == 1 and row["oficial"] == 1
    assert analysis["tipo"] == "oficial"
    assert analysis["sinal"] == "atualizacao_plataforma"
    assert analysis["provedor"] == "regras"
    assert {"ROAS", "CPA", "Performance Max"} <= set(json.loads(analysis["termos"]))
    assert analysis["resumo"].startswith("A plataforma apresenta")
    fts = conn.execute("SELECT rowid FROM articles_fts WHERE articles_fts MATCH 'lances'").fetchall()
    assert [r[0] for r in fts] == [article_id]


def test_url_repetida_e_item_invalido_sao_descartados(conn, sources):
    proc = Processor(conn, now=NOW)
    src = sources["Blog de SEO"]
    assert proc.process(src, _item("Guia de SEO para lojas virtuais em 2026", "https://seo.example.com/guia")) is not None
    assert proc.process(src, _item("Guia de SEO para lojas virtuais em 2026", "https://www.seo.example.com/guia/?utm_medium=rss")) is None
    assert proc.process(src, _item("", "https://seo.example.com/sem-titulo")) is None
    assert proc.process(src, _item("Título válido com link inválido", "javascript:alert(1)")) is None
    assert conn.execute("SELECT COUNT(*) FROM articles").fetchone()[0] == 1


def test_status_de_validacao(conn, sources):
    proc = Processor(conn, now=NOW)
    src = sources["Blog de SEO"]
    ids = {
        "sem_data": proc.process(src, _item("Como fazer auditoria de backlinks do zero", "https://seo.example.com/a", dias=None)),
        "antiga": proc.process(src, _item("Histórico das atualizações do algoritmo do Google", "https://seo.example.com/b", dias=45)),
        "agregador": proc.process(src, _item("Estudo mostra queda no tráfego orgânico de blogs", "https://news.google.com/x", via_agregador=True)),
    }
    status = {k: conn.execute("SELECT status FROM articles WHERE id = ?", (v,)).fetchone()[0] for k, v in ids.items()}
    assert status == {"sem_data": "data não confirmada", "antiga": "antiga", "agregador": "link via agregador"}


def test_duplicadas_entram_no_mesmo_grupo(conn, sources):
    proc = Processor(conn, now=NOW)
    a = proc.process(sources["Blog de SEO"], _item("Google anuncia core update de novembro de 2026", "https://seo.example.com/core"))
    b = proc.process(sources["Jornal de Propaganda"], _item("Google anuncia o core update de novembro de 2026", "https://jornal.example.com/core"))
    c = proc.process(sources["Jornal de Propaganda"], _item("Meta lança ferramenta de anúncios no WhatsApp Business", "https://jornal.example.com/meta"))
    groups = {r["id"]: r["grupo_id"] for r in conn.execute("SELECT id, grupo_id FROM articles")}
    assert groups[a] is not None and groups[a] == groups[b]
    assert groups[c] is None
    assert conn.execute("SELECT n_fontes FROM article_groups WHERE id = ?", (groups[a],)).fetchone()[0] == 2


def test_fonte_oficial_e_dominio_oficial():
    assert pipeline.is_official({"oficial": 1}, "https://qualquer.com/x")
    assert pipeline.is_official({}, "https://www.gov.br/anpd/pt-br/noticia")
    assert pipeline.is_official({}, "https://developers.google.com/search/blog/x")
    assert not pipeline.is_official({}, "https://blog-de-marketing.com/gov.br")


def test_sinal_e_natureza():
    assert pipeline.detect_signal("anpd abre consulta publica sobre lgpd e cookies de terceiros") == "regulacao"
    assert pipeline.detect_signal("agencia conquista conta da marca e contrata novo cmo") == "mercado"
    assert pipeline.detect_signal("receita de bolo de cenoura") is None
    assert pipeline.detect_nature("Opinião: o fim do funil", "https://site.com/x") == "opiniao"
    assert pipeline.detect_nature("O fim do funil", "https://site.com/colunistas/fulano/x") == "opiniao"
    assert pipeline.detect_nature("Marca lança campanha", "https://site.com/noticias/x") == "fato"


def test_relevancia_premia_oficial_recente_e_repetida():
    base = dict(prioridade=1, text_norm="texto neutro", sinal=None, via_agregador=False, now=NOW)
    old_plain = relevance_score(oficial=False, publicado_em=NOW - timedelta(days=20), n_fontes=1, **base)
    fresh_official = relevance_score(oficial=True, publicado_em=NOW - timedelta(hours=2), n_fontes=3, **base)
    assert fresh_official > old_plain
    assert 0 <= old_plain <= 100 and 0 <= fresh_official <= 100
    top = relevance_score(
        oficial=True, prioridade=3, text_norm="core update algoritmo lgpd google ads", sinal="regulacao",
        publicado_em=NOW, n_fontes=9, via_agregador=False, now=NOW,
    )
    assert top == 100


def test_recompute_relevance_reduz_com_o_tempo(conn, sources):
    proc = Processor(conn, now=NOW)
    article_id = proc.process(sources["Blog de SEO"], _item("Checklist de SEO técnico para sites grandes", "https://seo.example.com/check"))
    before = conn.execute("SELECT relevancia FROM articles WHERE id = ?", (article_id,)).fetchone()[0]
    pipeline.recompute_relevance(conn, now=NOW + timedelta(days=5))
    after = conn.execute("SELECT relevancia FROM articles WHERE id = ?", (article_id,)).fetchone()[0]
    assert after < before


def test_llm_parse_analysis_tolera_texto_ao_redor():
    text = 'Claro!\n{"resumo": "Resumo curto.", "pontos": ["a", "b", "c", "d"], "impactos": "inválido"}\nFim.'
    assert llm.parse_analysis(text) == {"resumo": "Resumo curto.", "pontos": ["a", "b", "c"], "impactos": []}
    assert llm.parse_analysis("sem json") is None
    assert llm.parse_analysis('{"resumo": ""}') is None


def test_llm_enrich_pending_grava_e_marca_provedor(conn, sources):
    class FakeProvider:
        name = "fake"

        def analyze(self, titulo, trecho, veiculo):
            return {"resumo": f"IA: {titulo}", "pontos": ["p1"], "impactos": ["i1"]}

    conn.execute("DELETE FROM articles")
    proc = Processor(conn)  # usa o relógio real: enrich_pending olha os últimos 3 dias
    article_id = proc.process(sources["Blog de SEO"], RawItem(titulo="Novo guia de link building para e-commerce", url="https://seo.example.com/lb"))
    assert llm.enrich_pending(conn, FakeProvider(), 10) == 1
    row = conn.execute("SELECT resumo, pontos, provedor FROM article_analysis WHERE article_id = ?", (article_id,)).fetchone()
    assert row["resumo"].startswith("IA: ") and json.loads(row["pontos"]) == ["p1"] and row["provedor"] == "fake"
    assert llm.enrich_pending(conn, FakeProvider(), 10) == 0  # nunca paga duas vezes
    assert llm.enrich_pending(conn, None, 10) == 0
