from datetime import datetime, timedelta, timezone

from app import textutil


def test_canonical_url_remove_rastreadores_e_normaliza():
    url = "https://WWW.Exemplo.com.br/noticia/abc/?utm_source=news&fbclid=123&id=7#topo"
    assert textutil.canonical_url(url) == "https://exemplo.com.br/noticia/abc?id=7"


def test_canonical_url_iguala_variacoes_da_mesma_pagina():
    a = textutil.canonical_url("https://site.com/post/")
    b = textutil.canonical_url("https://www.site.com/post?utm_campaign=x")
    assert a == b


def test_is_http_url_rejeita_esquemas_perigosos():
    assert textutil.is_http_url("https://site.com/a")
    assert not textutil.is_http_url("javascript:alert(1)")
    assert not textutil.is_http_url("")
    assert not textutil.is_http_url(None)


def test_strip_html_remove_tags_scripts_e_entidades():
    html = "<p>Ol&aacute; <b>mundo</b></p><script>alert('x')</script>"
    assert textutil.strip_html(html) == "Olá mundo"


def test_normalize_title_ignora_acentos_e_pontuacao():
    assert textutil.normalize_title("Google lança “Core Update”: o que muda?") == "google lanca core update o que muda"


def test_title_similarity_detecta_titulos_quase_iguais():
    a = textutil.normalize_title("Google anuncia core update de novembro de 2026")
    b = textutil.normalize_title("Google anuncia o core update de novembro de 2026")
    c = textutil.normalize_title("Meta lança nova ferramenta de anúncios para o WhatsApp")
    assert textutil.title_similarity(a, b) >= 0.85
    assert textutil.title_similarity(a, c) < 0.5


def test_extractive_summary_respeita_limite_e_usa_frases_da_fonte():
    text = "Primeira frase do texto. " + "Segunda frase bem mais longa do que a primeira. " * 12
    summary = textutil.extractive_summary(text, max_chars=120)
    assert summary.startswith("Primeira frase do texto.")
    assert len(summary) <= 121
    assert textutil.extractive_summary("") == ""


def test_iso_ida_e_volta():
    moment = datetime(2026, 11, 15, 9, 30, tzinfo=timezone.utc)
    assert textutil.from_iso(textutil.to_iso(moment)) == moment
    assert textutil.from_iso("data inválida") is None
    assert textutil.from_iso(None) is None


def test_struct_time_descarta_datas_absurdas():
    future = (datetime.now(timezone.utc) + timedelta(days=30)).timetuple()
    assert textutil.struct_time_to_datetime(future) is None
    assert textutil.struct_time_to_datetime((1970, 1, 1, 0, 0, 0)) is None
    assert textutil.struct_time_to_datetime(None) is None
    ok = textutil.struct_time_to_datetime((2026, 11, 1, 8, 0, 0, 0, 0, 0))
    assert ok == datetime(2026, 11, 1, 8, 0, tzinfo=timezone.utc)


def test_fts_query_neutraliza_sintaxe_especial():
    assert textutil.fts_query('core "update" OR (x)') == '"core"* "update"* "OR"* "x"*'
    assert textutil.fts_query("   ") == ""
