"""Funções puras de texto, URL e data usadas pela coleta e pelo processamento."""

from __future__ import annotations

import html
import re
import unicodedata
from datetime import datetime, timedelta, timezone
from difflib import SequenceMatcher
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

ISO_FORMAT = "%Y-%m-%dT%H:%M:%SZ"

_TAG_RE = re.compile(r"<[^>]+>")
_SCRIPT_RE = re.compile(r"<(script|style)\b.*?</\1>", re.IGNORECASE | re.DOTALL)
_SPACE_RE = re.compile(r"\s+")
_PUNCT_RE = re.compile(r"[^\w\s]", re.UNICODE)
_SENTENCE_RE = re.compile(r"(?<=[.!?])\s+(?=[A-ZÀ-Ý0-9\"“])")
_TRACKING_PREFIXES = ("utm_", "mc_", "pk_", "oly_")
_TRACKING_KEYS = {
    "fbclid", "gclid", "gclsrc", "dclid", "msclkid", "igshid", "ref", "ref_src",
    "source", "cmpid", "ocid", "yclid", "_hsenc", "_hsmi", "hss_channel", "s_cid",
}


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def to_iso(value: datetime) -> str:
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc).strftime(ISO_FORMAT)


def from_iso(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.strptime(value, ISO_FORMAT).replace(tzinfo=timezone.utc)
    except ValueError:
        return None


def now_iso() -> str:
    return to_iso(utcnow())


def strip_html(text: str | None) -> str:
    """Remove marcação e entidades HTML, devolvendo texto corrido."""
    if not text:
        return ""
    text = _SCRIPT_RE.sub(" ", text)
    text = _TAG_RE.sub(" ", text)
    text = html.unescape(text)
    return _SPACE_RE.sub(" ", text).strip()


def strip_accents(text: str) -> str:
    normalized = unicodedata.normalize("NFKD", text)
    return "".join(ch for ch in normalized if not unicodedata.combining(ch))


def normalize_title(title: str) -> str:
    """Forma comparável do título: minúsculas, sem acentos e sem pontuação."""
    text = strip_accents(title or "").lower()
    text = _PUNCT_RE.sub(" ", text)
    return _SPACE_RE.sub(" ", text).strip()


def title_similarity(a: str, b: str) -> float:
    """Similaridade (0 a 1) entre dois títulos já normalizados."""
    if not a or not b:
        return 0.0
    if a == b:
        return 1.0
    return SequenceMatcher(None, a, b, autojunk=False).ratio()


def secure_image_url(url: str | None) -> str | None:
    """Imagem sempre por https: páginas https (como o GitHub Pages) bloqueiam imagens http."""
    if url and url.strip().lower().startswith("http://"):
        return "https://" + url.strip()[7:]
    return url


def is_http_url(url: str | None) -> bool:
    if not url:
        return False
    parts = urlsplit(url.strip())
    return parts.scheme in ("http", "https") and bool(parts.netloc)


def canonical_url(url: str) -> str:
    """URL sem rastreadores, fragmento ou barra final, para detectar repetição."""
    parts = urlsplit(url.strip())
    host = parts.netloc.lower()
    if host.startswith("www."):
        host = host[4:]
    query = [
        (key, value)
        for key, value in parse_qsl(parts.query, keep_blank_values=True)
        if key.lower() not in _TRACKING_KEYS
        and not key.lower().startswith(_TRACKING_PREFIXES)
    ]
    path = parts.path.rstrip("/") or "/"
    return urlunsplit((parts.scheme.lower(), host, path, urlencode(query), ""))


def host_of(url: str) -> str:
    host = urlsplit(url).netloc.lower()
    return host[4:] if host.startswith("www.") else host


def extractive_summary(text: str, max_chars: int = 320) -> str:
    """Resumo extrativo: as primeiras frases do texto da própria fonte."""
    clean = strip_html(text)
    if not clean:
        return ""
    clean = re.sub(r"\s*(\[…\]|\[\.\.\.\]|Continue reading.*|The post .* appeared first on .*)$", "", clean).strip()
    if len(clean) <= max_chars:
        return clean
    picked: list[str] = []
    size = 0
    for sentence in _SENTENCE_RE.split(clean):
        if size + len(sentence) > max_chars and picked:
            break
        picked.append(sentence)
        size += len(sentence) + 1
        if size >= max_chars:
            break
    summary = " ".join(picked)
    if len(summary) > max_chars:
        summary = summary[:max_chars].rsplit(" ", 1)[0].rstrip(",;:") + "…"
    return summary


def struct_time_to_datetime(value) -> datetime | None:
    """Converte o struct_time (UTC) do feedparser; datas absurdas viram None."""
    if not value:
        return None
    try:
        parsed = datetime(*value[:6], tzinfo=timezone.utc)
    except (TypeError, ValueError):
        return None
    if parsed.year < 2000 or parsed > utcnow() + timedelta(days=1):
        return None
    return parsed


def fts_query(text: str) -> str:
    """Transforma a busca do usuário em uma consulta FTS5 segura (prefixos AND)."""
    words = re.findall(r"[\w][\w\-\.]*", text or "", flags=re.UNICODE)
    terms = ['"' + word.replace('"', "") + '"*' for word in words[:12] if word.strip("-.")]
    return " ".join(terms)
