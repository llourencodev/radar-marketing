"""Processamento por regras locais (sempre ativo e gratuito).

Cada notícia nova é classificada nas abas, recebe tipo (oficial ou
jornalístico), sinal (o que ela representa), natureza (fato ou opinião),
resumo extrativo, termos do glossário, grupo de duplicidade e relevância.
"""

from __future__ import annotations

import json
import re
import sqlite3
from datetime import datetime, timedelta

from . import settings
from .collectors import RawItem
from .textutil import (
    canonical_url,
    extractive_summary,
    from_iso,
    host_of,
    is_http_url,
    normalize_title,
    strip_accents,
    title_similarity,
    to_iso,
    utcnow,
)

OFFICIAL_DOMAINS = (
    "gov.br", "leg.br", "jus.br", "conar.org.br", "blog.google", "developers.google.com",
    "ads.google.com", "support.google.com", "about.fb.com", "about.meta.com",
    "business.facebook.com", "blog.youtube", "newsroom.tiktok.com", "business.linkedin.com",
    "business.x.com", "blog.x.com", "business.pinterest.com", "newsroom.pinterest.com",
    "blogs.microsoft.com", "about.ads.microsoft.com", "europa.eu", "ftc.gov",
)

SINAIS: dict[str, dict] = {
    "regulacao": {
        "rotulo": "Regulação e privacidade",
        "padrao": r"lgpd|anpd|conar|gdpr|regulac\w+|regulat\w+|regulamenta\w+|antitrust\w*|ftc|"
        r"projeto de lei|privacy law|multa\w*|fined?|lawsuit|processo judicial|acao judicial|procon|senacon|"
        r"digital markets act|cookies? de terceiros|third.party cookies?|proib\w+|banned|"
        r"autorregulamenta\w+|consulta publica|sancion\w+",
    },
    "atualizacao_plataforma": {
        "rotulo": "Atualização de plataforma",
        "padrao": r"core update|spam update|algorithm (update|change)s?|"
        r"atualiza\w+ d[oe] algoritmo|novo algoritmo|muda\w* (o|no) algoritmo|rolls? out|"
        r"rolling out|new features?|novo recurso|novos recursos|nova funcionalidade|"
        r"nova ferramenta|policy (update|change)s?|nova politica|politica de (anuncios|uso)|"
        r"deprecat\w+|sunset\w*|descontinu\w+|now available|is launching|libera (novo|nova)|"
        r"passa a permitir|introducing|apresenta (novo|nova)|ganha (novo|nova)",
    },
    "mercado": {
        "rotulo": "Movimentação de mercado",
        "padrao": r"aquisic\w+|adquire\w*|acquir\w+|fusao|merger|contrata\w*|nomeia|novo cmo|"
        r"nova cmo|appoints?|hires?|rodada|funding|faturamento|revenue|demiss\w+|layoffs?|"
        r"vence concorrencia|conquista (a )?conta|wins? (the )?account|assume (a )?conta|ipo|"
        r"investimento publicitario|ad spend",
    },
    "pesquisa": {
        "rotulo": "Pesquisa e dados",
        "padrao": r"pesquisa|estudo|levantamento|survey|study|report|relatorio|benchmark|"
        r"dados mostram|aponta (estudo|pesquisa)|\d+ ?%|por cento|percent",
    },
    "campanha": {
        "rotulo": "Campanha e case",
        "padrao": r"campanhas?|campaigns?|comercial|filme|spot|case|cannes|premi\w+|awards?|"
        r"collab|parceria|patrocin\w+|sponsor\w*|ativacao",
    },
}

PRIORITY_TERMS = (
    "core update", "algoritmo", "algorithm", "ai overviews", "ai mode", "google ads", "meta ads",
    "performance max", "lgpd", "anpd", "conar", "cookies", "privacidade", "privacy",
    "inteligencia artificial", "generative ai", "ia generativa", "chatgpt", "tiktok", "instagram",
    "whatsapp", "black friday", "cannes", "retail media", "influenciadores", "creators",
)

_OPINION_URL_RE = re.compile(r"/(opiniao|opinion|opinioes|coluna|colunas|colunistas|artigos?|ponto-de-vista|blog-d[oae])(/|$)")
_OPINION_TITLE_RE = re.compile(r"^(opiniao|opinion|artigo|coluna|analise)\s*[:|\-–]")

STATUS_VERIFICADA = "verificada"
STATUS_AGREGADOR = "link via agregador"
STATUS_SEM_DATA = "data não confirmada"
STATUS_ANTIGA = "antiga"


def _norm(text: str) -> str:
    return strip_accents(text or "").lower()


def _word_regex(terms: list[str] | tuple[str, ...]) -> re.Pattern:
    alternatives = sorted({re.escape(_norm(term)) for term in terms if term.strip()}, key=len, reverse=True)
    return re.compile(r"(?<![\w])(" + "|".join(alternatives) + r")(?![\w])")


def is_official(source: dict, url: str) -> bool:
    if source.get("oficial"):
        return True
    host = host_of(url)
    return any(host == domain or host.endswith("." + domain) for domain in OFFICIAL_DOMAINS)


def detect_signal(text_norm: str) -> str | None:
    """Sinal com mais ocorrências no texto; empate segue a ordem de SINAIS."""
    best, best_hits = None, 0
    for key, spec in SINAIS.items():
        hits = len(set(m.group(0) for m in _SIGNAL_RE[key].finditer(text_norm)))
        if hits > best_hits:
            best, best_hits = key, hits
    return best


_SIGNAL_RE = {key: re.compile(r"(?<![\w])(" + spec["padrao"] + r")(?![\w])") for key, spec in SINAIS.items()}
_PRIORITY_RE = _word_regex(PRIORITY_TERMS)


def detect_nature(title: str, url: str) -> str:
    if _OPINION_URL_RE.search(url.lower()) or _OPINION_TITLE_RE.search(_norm(title)):
        return "opiniao"
    return "fato"


def relevance_score(
    *,
    oficial: bool,
    prioridade: int,
    text_norm: str,
    sinal: str | None,
    publicado_em: datetime | None,
    n_fontes: int,
    via_agregador: bool,
    now: datetime | None = None,
) -> int:
    """Pontuação 0-100: fonte oficial, termos prioritários, recência e repetição."""
    now = now or utcnow()
    score = 10
    if oficial:
        score += 25
    score += max(0, min(prioridade, 3) - 1) * 8
    score += min(18, 6 * len(set(m.group(0) for m in _PRIORITY_RE.finditer(text_norm))))
    if sinal in ("atualizacao_plataforma", "regulacao"):
        score += 10 + (5 if oficial else 0)
    if publicado_em is not None:
        age = now - publicado_em
        if age <= timedelta(hours=24):
            score += 25
        elif age <= timedelta(hours=72):
            score += 15
        elif age <= timedelta(days=7):
            score += 8
    score += min(15, 5 * max(0, n_fontes - 1))
    if via_agregador:
        score -= 5
    return max(0, min(100, score))


class Processor:
    """Aplica as regras a cada item novo e grava a notícia no banco."""

    def __init__(
        self,
        conn: sqlite3.Connection,
        categories: list[dict] | None = None,
        glossary: dict[str, str] | None = None,
        now: datetime | None = None,
    ) -> None:
        self.conn = conn
        self.now = now or utcnow()
        self.categories = categories if categories is not None else settings.load_categories()
        self.glossary = glossary if glossary is not None else settings.load_glossary()
        self._cat_re = {c["slug"]: _word_regex(c["palavras"]) for c in self.categories}
        self._window = {c["slug"]: int(c.get("janela_dias", 30)) for c in self.categories}
        self._glossary_re = [
            (term, re.compile(r"(?<![\w])" + re.escape(term if term.isupper() else _norm(term)) + r"(?![\w])"), term.isupper())
            for term in self.glossary
        ]
        since = to_iso(self.now - timedelta(days=settings.DEDUP_WINDOW_DAYS))
        self._recent: list[dict] = [
            {"id": row["id"], "norm": row["titulo_norm"], "tokens": set(row["titulo_norm"].split()), "grupo_id": row["grupo_id"]}
            for row in conn.execute(
                "SELECT id, titulo_norm, grupo_id FROM articles WHERE coletado_em >= ?", (since,)
            )
        ]

    # ------------------------------------------------------------------ regras
    def classify(self, source: dict, title: str, trecho: str) -> list[tuple[str, str, float]]:
        """Abas da notícia: categoria da fonte + palavras-chave (título vale o dobro)."""
        title_norm, body_norm = _norm(title), _norm(trecho)
        found: dict[str, tuple[str, float]] = {}
        for slug, regex in self._cat_re.items():
            score = 2 * len(set(m.group(0) for m in regex.finditer(title_norm)))
            score += len(set(m.group(0) for m in regex.finditer(body_norm)))
            if score >= 2:
                found[slug] = ("regra", round(min(1.0, 0.4 + 0.15 * score), 2))
        default = source["categoria"]
        if default in self._cat_re and default not in found:
            found[default] = ("fonte", 0.6)
        ordered = sorted(found.items(), key=lambda kv: kv[1][1], reverse=True)
        return [(slug, origem, conf) for slug, (origem, conf) in ordered]

    def find_terms(self, text: str) -> list[str]:
        text_norm = _norm(text)
        return [
            term
            for term, regex, case_sensitive in self._glossary_re
            if regex.search(text if case_sensitive else text_norm)
        ][:8]

    def _find_duplicate(self, norm: str, tokens: set[str]) -> dict | None:
        if len(tokens) < 3:
            return None
        best, best_ratio = None, settings.DEDUP_THRESHOLD
        for candidate in self._recent:
            other = candidate["tokens"]
            common = len(tokens & other)
            if common * 2 < max(len(tokens), len(other)):
                continue
            ratio = title_similarity(norm, candidate["norm"])
            if ratio >= best_ratio:
                best, best_ratio = candidate, ratio
        return best

    def _join_group(self, twin: dict, title: str) -> int:
        if twin["grupo_id"] is None:
            row = self.conn.execute(
                "SELECT titulo, COALESCE(publicado_em, coletado_em) AS data FROM articles WHERE id = ?",
                (twin["id"],),
            ).fetchone()
            cursor = self.conn.execute(
                "INSERT INTO article_groups (titulo, primeira_pub, ultima_pub, n_fontes) VALUES (?, ?, ?, 1)",
                (row["titulo"], row["data"], row["data"]),
            )
            twin["grupo_id"] = cursor.lastrowid
            self.conn.execute("UPDATE articles SET grupo_id = ? WHERE id = ?", (twin["grupo_id"], twin["id"]))
        return twin["grupo_id"]

    def _refresh_group(self, group_id: int) -> int:
        row = self.conn.execute(
            """
            SELECT COUNT(DISTINCT veiculo) AS fontes,
                   MIN(COALESCE(publicado_em, coletado_em)) AS primeira,
                   MAX(COALESCE(publicado_em, coletado_em)) AS ultima
            FROM articles WHERE grupo_id = ?
            """,
            (group_id,),
        ).fetchone()
        self.conn.execute(
            "UPDATE article_groups SET n_fontes = ?, primeira_pub = ?, ultima_pub = ? WHERE id = ?",
            (row["fontes"], row["primeira"], row["ultima"], group_id),
        )
        return row["fontes"]

    # --------------------------------------------------------------- principal
    def process(self, source: dict, raw: RawItem) -> int | None:
        """Grava a notícia se for nova e válida; devolve o id ou None."""
        title = (raw.titulo or "").strip()
        if not title or not is_http_url(raw.url):
            return None  # sem fonte identificável: rejeitada, nunca exibida
        canonical = canonical_url(raw.url)
        if self.conn.execute("SELECT 1 FROM articles WHERE url_canonica = ?", (canonical,)).fetchone():
            return None

        veiculo = raw.veiculo or source["nome"]
        categories = self.classify(source, title, raw.trecho)
        if not categories:
            return None
        text_norm = _norm(f"{title} {raw.trecho}")
        oficial = is_official(source, raw.url)
        sinal = detect_signal(text_norm)
        natureza = detect_nature(title, raw.url)

        published = raw.publicado_em
        window = self._window.get(categories[0][0], 30)
        if raw.via_agregador:
            status = STATUS_AGREGADOR
        elif published is None:
            status = STATUS_SEM_DATA
        elif self.now - published > timedelta(days=window):
            status = STATUS_ANTIGA
        else:
            status = STATUS_VERIFICADA

        norm = normalize_title(title)
        tokens = set(norm.split())
        twin = self._find_duplicate(norm, tokens)
        group_id = self._join_group(twin, title) if twin else None

        collected = to_iso(self.now)
        cursor = self.conn.execute(
            """
            INSERT INTO articles (titulo, titulo_norm, url, url_canonica, source_id, veiculo,
                                  imagem_url, imagem_origem, publicado_em, data_confirmada,
                                  coletado_em, trecho, idioma, oficial, relevancia, status, grupo_id)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 0, ?, ?)
            """,
            (
                title, norm, raw.url, canonical, source["id"], veiculo,
                raw.imagem_url if is_http_url(raw.imagem_url) else None,
                raw.imagem_origem if is_http_url(raw.imagem_url) else "nenhuma",
                to_iso(published) if published else None,
                1 if published else 0,
                collected, raw.trecho, source.get("idioma", "pt"), 1 if oficial else 0,
                status, group_id,
            ),
        )
        article_id = cursor.lastrowid
        n_fontes = self._refresh_group(group_id) if group_id else 1

        relevancia = relevance_score(
            oficial=oficial, prioridade=int(source.get("prioridade", 1)), text_norm=text_norm,
            sinal=sinal, publicado_em=published, n_fontes=n_fontes,
            via_agregador=raw.via_agregador, now=self.now,
        )
        self.conn.execute("UPDATE articles SET relevancia = ? WHERE id = ?", (relevancia, article_id))
        self.conn.executemany(
            "INSERT INTO article_categories (article_id, categoria, origem, confianca) VALUES (?, ?, ?, ?)",
            [(article_id, slug, origem, conf) for slug, origem, conf in categories],
        )
        resumo = extractive_summary(raw.trecho)
        self.conn.execute(
            """
            INSERT INTO article_analysis (article_id, resumo, termos, tipo, sinal, natureza, provedor, gerado_em)
            VALUES (?, ?, ?, ?, ?, ?, 'regras', ?)
            """,
            (
                article_id, resumo,
                json.dumps(self.find_terms(f"{title} {raw.trecho}"), ensure_ascii=False),
                "oficial" if oficial else "jornalistico", sinal, natureza, collected,
            ),
        )
        self.conn.execute(
            "INSERT INTO articles_fts (rowid, titulo, trecho, resumo, veiculo) VALUES (?, ?, ?, ?, ?)",
            (article_id, title, raw.trecho, resumo, veiculo),
        )
        self._recent.append({"id": article_id, "norm": norm, "tokens": tokens, "grupo_id": group_id})
        return article_id


def recompute_relevance(conn: sqlite3.Connection, now: datetime | None = None, days: int = 8) -> None:
    """Reavalia a relevância das notícias recentes (recência e repetição mudam)."""
    now = now or utcnow()
    rows = conn.execute(
        """
        SELECT a.id, a.titulo, a.trecho, a.oficial, a.publicado_em, a.status, a.relevancia,
               s.prioridade, an.sinal, COALESCE(g.n_fontes, 1) AS n_fontes
        FROM articles a
        JOIN sources s ON s.id = a.source_id
        JOIN article_analysis an ON an.article_id = a.id
        LEFT JOIN article_groups g ON g.id = a.grupo_id
        WHERE a.coletado_em >= ?
        """,
        (to_iso(now - timedelta(days=days)),),
    ).fetchall()
    updates = []
    for row in rows:
        score = relevance_score(
            oficial=bool(row["oficial"]), prioridade=row["prioridade"],
            text_norm=_norm(f"{row['titulo']} {row['trecho'] or ''}"), sinal=row["sinal"],
            publicado_em=from_iso(row["publicado_em"]), n_fontes=row["n_fontes"],
            via_agregador=row["status"] == STATUS_AGREGADOR, now=now,
        )
        if score != row["relevancia"]:
            updates.append((score, row["id"]))
    conn.executemany("UPDATE articles SET relevancia = ? WHERE id = ?", updates)
