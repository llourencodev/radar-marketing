from datetime import datetime, timezone

import httpx

from app import collectors, db

RSS = """<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0" xmlns:media="http://search.yahoo.com/mrss/">
<channel><title>Blog</title>
<item>
  <title>Google confirma &lt;b&gt;core update&lt;/b&gt; de novembro</title>
  <link>https://seo.example.com/core-update?utm_source=rss</link>
  <description><![CDATA[<p>O Google confirmou a atualização.</p><img src="https://seo.example.com/img.jpg">]]></description>
  <pubDate>Sat, 14 Nov 2026 10:00:00 GMT</pubDate>
</item>
<item>
  <title>Sem data e sem imagem</title>
  <link>https://seo.example.com/sem-data</link>
  <description>Texto simples.</description>
</item>
<item><title>Link inválido</title><link>javascript:alert(1)</link></item>
<item><title></title><link>https://seo.example.com/sem-titulo</link></item>
</channel></rss>"""

GOOGLE_NEWS = """<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0"><channel><title>Google Notícias</title>
<item>
  <title>Marca lança campanha de Natal - Meio &amp; Mensagem</title>
  <link>https://news.google.com/rss/articles/CBMiABC?oc=5</link>
  <pubDate>Fri, 13 Nov 2026 12:00:00 GMT</pubDate>
  <description>&lt;a href="x"&gt;lista de links&lt;/a&gt;</description>
  <source url="https://www.meioemensagem.com.br">Meio &amp; Mensagem</source>
</item></channel></rss>"""

BING_NEWS = """<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0" xmlns:News="https://www.bing.com/news/search"><channel><title>Bing</title>
<item>
  <title>Varejo aposta em retail media</title>
  <link>http://www.bing.com/news/apiclick.aspx?ref=FexRss&amp;aid=&amp;url=https%3a%2f%2fportal.example.com%2fretail-media&amp;c=1</link>
  <pubDate>Fri, 13 Nov 2026 12:00:00 GMT</pubDate>
  <description>Redes investem em mídia.</description>
  <News:Source>Portal Exemplo</News:Source>
</item></channel></rss>"""

HTML_LIST = """<html><body>
<a href="/anpd/pt-br/assuntos/noticias">Notícias</a>
<a href="/anpd/pt-br/assuntos/noticias/anpd-publica-guia-sobre-cookies-e-publicidade">ANPD publica guia sobre cookies e publicidade direcionada</a>
<a href="https://www.gov.br/anpd/pt-br/assuntos/noticias/anpd-abre-consulta-publica-sobre-dados">ANPD abre consulta pública sobre tratamento de dados</a>
<a href="/anpd/pt-br/assuntos/noticias/anpd-aplica-sancao-a-empresa-de-marketing">ANPD aplica sanção a empresa de marketing digital por spam</a>
<a href="/anpd/pt-br/assuntos/noticias/anpd-publica-guia-sobre-cookies-e-publicidade">ANPD publica guia sobre cookies e publicidade direcionada</a>
<a href="/anpd/pt-br/acesso-a-informacao">Acesso à informação e transparência institucional</a>
<a href="https://outro-site.example.com/uma-noticia-com-titulo-bem-comprido">Notícia de outro site com título bem comprido</a>
<a href="/curto">Curto</a>
</body></html>"""


def _source(url, tipo="rss", **extra):
    return {"id": 1, "nome": "Fonte", "tipo": tipo, "url": url, "categoria": "seo", **extra}


def test_parse_feed_extrai_campos_so_da_fonte():
    items = collectors.parse_feed(RSS, _source("https://seo.example.com/feed"))
    assert [i.titulo for i in items] == ["Google confirma core update de novembro", "Sem data e sem imagem"]
    first, second = items
    assert first.publicado_em == datetime(2026, 11, 14, 10, 0, tzinfo=timezone.utc)
    assert first.imagem_url == "https://seo.example.com/img.jpg" and first.imagem_origem == "feed"
    assert first.trecho == "O Google confirmou a atualização."
    assert not first.via_agregador
    # ausência é mantida como ausência: nada de data ou imagem inventada
    assert second.publicado_em is None and second.imagem_url is None and second.imagem_origem == "nenhuma"


def test_parse_feed_google_news_usa_veiculo_original_e_marca_agregador():
    source = _source(db.search_url("google", "campanha"), tipo="search")
    (item,) = collectors.parse_feed(GOOGLE_NEWS, source)
    assert item.titulo == "Marca lança campanha de Natal"
    assert item.veiculo == "Meio & Mensagem"
    assert item.via_agregador and item.trecho == ""


def test_parse_feed_bing_resolve_link_original():
    source = _source(db.search_url("bing", "retail media"), tipo="search")
    (item,) = collectors.parse_feed(BING_NEWS, source)
    assert item.url == "https://portal.example.com/retail-media"
    assert item.veiculo == "Portal Exemplo"
    assert not item.via_agregador


def test_parse_html_list_pega_noticias_da_listagem_sem_repetir():
    source = _source("https://www.gov.br/anpd/pt-br/assuntos/noticias", tipo="html")
    items = collectors.parse_html_list(HTML_LIST, source)
    assert len(items) == 3
    assert all(i.url.startswith("https://www.gov.br/anpd/pt-br/assuntos/noticias/") for i in items)
    assert all(i.publicado_em is None for i in items)


def test_extract_page_image():
    html = '<html><head><meta property="og:image" content="/img/capa.png"></head></html>'
    assert collectors.extract_page_image(html, "https://site.com/post") == "https://site.com/img/capa.png"
    assert collectors.extract_page_image("<html></html>", "https://site.com/post") is None


def test_search_url_monta_enderecos_de_busca():
    assert db.search_url("google", "meta ads").startswith("https://news.google.com/rss/search?q=meta+ads")
    assert "format=rss" in db.search_url("bing", "meta ads")


def _client(handler):
    return httpx.Client(transport=httpx.MockTransport(handler), follow_redirects=True)


def test_fetch_source_envia_cache_condicional_e_trata_304():
    seen = {}

    def handler(request):
        seen["etag"] = request.headers.get("if-none-match")
        return httpx.Response(304)

    source = _source("https://seo.example.com/feed", etag='"abc"')
    with _client(handler) as client:
        throttle = collectors.HostThrottle(0)
        result = collectors.fetch_source(client, source, throttle, collectors.RobotsCache(client, throttle))
    assert seen["etag"] == '"abc"'
    assert result.not_modified and result.error is None and result.items == []


def test_fetch_source_sucesso_guarda_etag():
    def handler(request):
        return httpx.Response(200, content=RSS.encode(), headers={"etag": '"v2"', "content-type": "application/rss+xml"})

    with _client(handler) as client:
        throttle = collectors.HostThrottle(0)
        result = collectors.fetch_source(
            client, _source("https://seo.example.com/feed"), throttle,
            collectors.RobotsCache(client, throttle), fetch_images=False,
        )
    assert result.error is None and len(result.items) == 2 and result.etag == '"v2"'


def test_fetch_source_nunca_levanta_excecao():
    def boom(request):
        raise httpx.ConnectError("sem rede")

    def not_found(request):
        return httpx.Response(404)

    def not_a_feed(request):
        return httpx.Response(200, text="<html><body>página comum</body></html>")

    for handler, expected in ((boom, "falha de rede"), (not_found, "HTTP 404"), (not_a_feed, "feed")):
        with _client(handler) as client:
            throttle = collectors.HostThrottle(0)
            result = collectors.fetch_source(
                client, _source("https://seo.example.com/feed"), throttle, collectors.RobotsCache(client, throttle)
            )
        assert expected in result.error and result.items == []


def test_fetch_source_html_respeita_robots_txt():
    def handler(request):
        if request.url.path == "/robots.txt":
            return httpx.Response(200, text="User-agent: *\nDisallow: /noticias")
        return httpx.Response(200, text=HTML_LIST)

    with _client(handler) as client:
        throttle = collectors.HostThrottle(0)
        result = collectors.fetch_source(
            client, _source("https://site.example.com/noticias", tipo="html"), throttle,
            collectors.RobotsCache(client, throttle),
        )
    assert "robots.txt" in result.error
