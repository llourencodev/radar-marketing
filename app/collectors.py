"""Coletores: RSS/Atom, página HTML e RSS de busca (Google News / Bing News).

A análise de conteúdo (parse_feed, parse_html_list) é pura e testável sem rede;
fetch_source cuida de HTTP, cache condicional, robots.txt e limite por site.
"""

from __future__ import annotations

import re
import ssl
import threading
import time
from dataclasses import dataclass, field
from datetime import datetime
from urllib.parse import parse_qs, urljoin, urlsplit
from urllib.robotparser import RobotFileParser

import feedparser
import httpx
from bs4 import BeautifulSoup

from . import settings
from .textutil import canonical_url, host_of, is_http_url, strip_html, struct_time_to_datetime

_IMG_RE = re.compile(r"<img[^>]+src=[\"']([^\"']+)[\"']", re.IGNORECASE)
_AGGREGATOR_HOSTS = ("news.google.com", "bing.com")
MAX_ITEMS_PER_SOURCE = 40
MAX_PAGE_IMAGES_PER_SOURCE = 6


@dataclass
class RawItem:
    titulo: str
    url: str
    trecho: str = ""
    publicado_em: datetime | None = None
    imagem_url: str | None = None
    imagem_origem: str = "nenhuma"
    veiculo: str | None = None
    via_agregador: bool = False


@dataclass
class FetchResult:
    status: int | None = None
    items: list[RawItem] = field(default_factory=list)
    etag: str | None = None
    last_modified: str | None = None
    error: str | None = None
    not_modified: bool = False
    duration_ms: int = 0


class HostThrottle:
    """Garante um intervalo mínimo entre requisições ao mesmo site."""

    def __init__(self, min_interval: float = settings.HOST_MIN_INTERVAL) -> None:
        self._min_interval = min_interval
        self._guard = threading.Lock()
        self._locks: dict[str, threading.Lock] = {}
        self._last: dict[str, float] = {}

    def wait(self, url: str) -> None:
        host = host_of(url)
        with self._guard:
            lock = self._locks.setdefault(host, threading.Lock())
        with lock:
            elapsed = time.monotonic() - self._last.get(host, 0.0)
            if elapsed < self._min_interval:
                time.sleep(self._min_interval - elapsed)
            self._last[host] = time.monotonic()


class RobotsCache:
    """Consulta o robots.txt de cada site uma única vez por ciclo de coleta."""

    def __init__(self, client: httpx.Client, throttle: HostThrottle) -> None:
        self._client = client
        self._throttle = throttle
        self._guard = threading.Lock()
        self._parsers: dict[str, RobotFileParser | None] = {}

    def allowed(self, url: str) -> bool:
        parts = urlsplit(url)
        root = f"{parts.scheme}://{parts.netloc}"
        with self._guard:
            known = root in self._parsers
            parser = self._parsers.get(root)
        if not known:
            parser = self._load(root)
            with self._guard:
                self._parsers[root] = parser
        if parser is None:
            return True
        return parser.can_fetch(settings.USER_AGENT, url)

    def _load(self, root: str) -> RobotFileParser | None:
        try:
            self._throttle.wait(root)
            response = self._client.get(f"{root}/robots.txt")
        except httpx.HTTPError:
            return None
        if response.status_code != 200 or "<html" in response.text[:500].lower():
            return None
        parser = RobotFileParser()
        parser.parse(response.text.splitlines())
        return parser


def system_ssl_context() -> ssl.SSLContext:
    """Certificados do próprio Windows, em vez da lista embutida no Python.

    Antivírus e proxies corporativos que inspecionam HTTPS instalam a própria
    autoridade no sistema; sem isso, toda conexão falharia na verificação.
    """
    return ssl.create_default_context()


def new_client() -> httpx.Client:
    return httpx.Client(
        verify=system_ssl_context(),
        follow_redirects=True,
        timeout=settings.HTTP_TIMEOUT,
        headers={
            "User-Agent": settings.USER_AGENT,
            "Accept": "application/rss+xml, application/atom+xml, application/xml;q=0.9, text/html;q=0.8, */*;q=0.5",
            "Accept-Language": "pt-BR,pt;q=0.9,en;q=0.8",
        },
    )


def is_aggregator(url: str) -> bool:
    host = host_of(url)
    return any(host == h or host.endswith("." + h) for h in _AGGREGATOR_HOSTS)


def _entry_image(entry) -> str | None:
    for key in ("media_content", "media_thumbnail"):
        for media in entry.get(key) or []:
            url = media.get("url")
            medium = media.get("medium") or media.get("type") or "image"
            if is_http_url(url) and "video" not in medium and "audio" not in medium:
                return url
    for enclosure in entry.get("enclosures") or []:
        if str(enclosure.get("type", "")).startswith("image/") and is_http_url(enclosure.get("href")):
            return enclosure["href"]
    news_image = entry.get("news_image")
    if is_http_url(news_image):
        return news_image
    bodies = [entry.get("summary") or ""]
    bodies += [c.get("value", "") for c in entry.get("content") or []]
    for body in bodies:
        match = _IMG_RE.search(body)
        if match and is_http_url(match.group(1)) and "pixel" not in match.group(1):
            return match.group(1)
    return None


def _resolve_bing(link: str) -> str:
    """O link do Bing News carrega o endereço original no parâmetro url."""
    target = parse_qs(urlsplit(link).query).get("url", [None])[0]
    return target if is_http_url(target) else link


def parse_feed(content: bytes | str, source: dict) -> list[RawItem]:
    """Extrai notícias de um RSS/Atom. Título, data, imagem e link vêm só da fonte."""
    parsed = feedparser.parse(content)
    feed_host = host_of(source["url"])
    from_google = feed_host == "news.google.com"
    from_bing = feed_host.endswith("bing.com")
    items: list[RawItem] = []
    for entry in parsed.entries[:MAX_ITEMS_PER_SOURCE]:
        title = strip_html(entry.get("title"))
        link = (entry.get("link") or "").strip()
        if not title or not is_http_url(link):
            continue
        veiculo = None
        via_agregador = False
        trecho = entry.get("summary") or ""
        if not trecho and entry.get("content"):
            trecho = entry["content"][0].get("value", "")
        if from_google:
            veiculo = strip_html((entry.get("source") or {}).get("title")) or None
            if veiculo and title.endswith(" - " + veiculo):
                title = title[: -len(veiculo) - 3].rstrip()
            via_agregador = True
            trecho = ""  # o resumo do Google News é só uma lista de links
        elif from_bing:
            link = _resolve_bing(link)
            veiculo = strip_html(entry.get("news_source")) or None
            via_agregador = is_aggregator(link)
        image = _entry_image(entry)
        items.append(
            RawItem(
                titulo=title,
                url=link,
                trecho=strip_html(trecho)[:1500],
                publicado_em=struct_time_to_datetime(
                    entry.get("published_parsed") or entry.get("updated_parsed")
                ),
                imagem_url=image,
                imagem_origem="feed" if image else "nenhuma",
                veiculo=veiculo,
                via_agregador=via_agregador,
            )
        )
    return items


def parse_html_list(html: str, source: dict) -> list[RawItem]:
    """Extrai a lista de notícias de uma página oficial sem RSS.

    Usa o seletor CSS configurado na fonte ou, na falta dele, os links com
    título longo que ficam abaixo do endereço da própria listagem. Páginas
    montadas por JavaScript não trazem esses links e resultam em lista vazia.
    """
    soup = BeautifulSoup(html, "html.parser")
    base = source["url"]
    base_canonical = canonical_url(base)
    anchors = soup.select(source["seletor"]) if source.get("seletor") else soup.find_all("a", href=True)
    below: list[RawItem] = []
    same_host: list[RawItem] = []
    seen: set[str] = set()
    for anchor in anchors:
        if anchor.name != "a":
            anchor = anchor.find("a", href=True) or anchor
        href = anchor.get("href") if hasattr(anchor, "get") else None
        title = strip_html(anchor.get_text(" ")) if href else ""
        if not href or len(title) < 30 or len(title) > 300:
            continue
        url = urljoin(base, href)
        if not is_http_url(url) or host_of(url) != host_of(base):
            continue
        canonical = canonical_url(url)
        if canonical == base_canonical or canonical in seen:
            continue
        seen.add(canonical)
        item = RawItem(titulo=title, url=url)
        if canonical.startswith(base_canonical + "/"):
            below.append(item)
        else:
            same_host.append(item)
    # Sem seletor, só valem links abaixo da listagem: menus do site não são notícia.
    chosen = below + same_host if source.get("seletor") else below
    return chosen[:MAX_ITEMS_PER_SOURCE]


def extract_page_image(html: str, page_url: str) -> str | None:
    soup = BeautifulSoup(html, "html.parser")
    for attrs in ({"property": "og:image"}, {"name": "twitter:image"}, {"property": "og:image:url"}):
        tag = soup.find("meta", attrs=attrs)
        if tag and tag.get("content"):
            url = urljoin(page_url, tag["content"].strip())
            if is_http_url(url):
                return url
    return None


def _fill_page_images(
    client: httpx.Client,
    items: list[RawItem],
    known: set[str],
    throttle: HostThrottle,
    robots: RobotsCache,
) -> None:
    """Busca a imagem (og:image) na página, só para itens novos e sem imagem."""
    budget = MAX_PAGE_IMAGES_PER_SOURCE
    for item in items:
        if budget <= 0:
            break
        if item.imagem_url or item.via_agregador or canonical_url(item.url) in known:
            continue
        budget -= 1
        try:
            if not robots.allowed(item.url):
                continue
            throttle.wait(item.url)
            response = client.get(item.url)
            if response.status_code == 200 and "html" in response.headers.get("content-type", ""):
                image = extract_page_image(response.text[:400_000], str(response.url))
                if image:
                    item.imagem_url = image
                    item.imagem_origem = "pagina"
        except httpx.HTTPError:
            continue


def fetch_source(
    client: httpx.Client,
    source: dict,
    throttle: HostThrottle,
    robots: RobotsCache,
    known: set[str] | None = None,
    fetch_images: bool = True,
) -> FetchResult:
    """Baixa e interpreta uma fonte. Nunca levanta exceção: o erro vai no resultado."""
    started = time.monotonic()
    result = FetchResult()
    try:
        if source["tipo"] == "html" and not robots.allowed(source["url"]):
            result.error = "leitura não permitida pelo robots.txt do site"
            return result
        headers = {}
        if source.get("etag"):
            headers["If-None-Match"] = source["etag"]
        if source.get("last_modified"):
            headers["If-Modified-Since"] = source["last_modified"]
        throttle.wait(source["url"])
        response = client.get(source["url"], headers=headers)
        result.status = response.status_code
        if response.status_code == 304:
            result.not_modified = True
            return result
        if response.status_code != 200:
            result.error = f"HTTP {response.status_code}"
            return result
        result.etag = response.headers.get("etag")
        result.last_modified = response.headers.get("last-modified")
        if source["tipo"] == "html":
            result.items = parse_html_list(response.text, source)
            if not result.items:
                result.error = "nenhuma notícia encontrada na página (o layout pode ter mudado)"
        else:
            result.items = parse_feed(response.content, source)
            if not result.items:
                result.error = "o endereço não devolveu um feed RSS/Atom com itens"
        if result.items and fetch_images:
            _fill_page_images(client, result.items, known or set(), throttle, robots)
    except httpx.TimeoutException:
        result.error = f"tempo esgotado ({int(settings.HTTP_TIMEOUT)} s)"
    except httpx.HTTPError as exc:
        if "CERTIFICATE_VERIFY_FAILED" in str(exc):
            result.error = "falha de rede: certificado HTTPS do site não pôde ser verificado"
        else:
            result.error = f"falha de rede: {type(exc).__name__}"
    except Exception as exc:  # noqa: BLE001 - uma fonte nunca derruba as outras
        result.error = f"erro inesperado: {type(exc).__name__}: {exc}"[:300]
    finally:
        result.duration_ms = int((time.monotonic() - started) * 1000)
    return result


def probe_source(source: dict) -> FetchResult:
    """Testa uma fonte nova antes de salvar (usado pelo formulário de fontes)."""
    with new_client() as client:
        throttle = HostThrottle(0.0)
        return fetch_source(client, source, throttle, RobotsCache(client, throttle), fetch_images=False)
