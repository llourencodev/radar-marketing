"""Camada opcional de IA: resumo, pontos principais e impactos para marketing.

Só é usada quando LLM_PROVIDER está configurado no .env. O modelo recebe apenas
o título e o trecho vindos da fonte, com a instrução de não acrescentar fatos.
Cada resultado fica gravado no banco, então nenhuma notícia é paga duas vezes.
"""

from __future__ import annotations

import json
import logging
import sqlite3
import ssl
from datetime import timedelta

import httpx

from .settings import Settings
from .textutil import now_iso, to_iso, utcnow

log = logging.getLogger("radar.llm")

SYSTEM_PROMPT = (
    "Você analisa notícias de marketing para profissionais brasileiros. "
    "Use somente o título e o trecho fornecidos: não acrescente fatos, números, nomes ou datas "
    "que não estejam no texto. Escreva em português do Brasil, mesmo que a fonte esteja em inglês. "
    "Responda apenas com um objeto JSON, sem texto antes ou depois, neste formato: "
    '{"resumo": "até 60 palavras, objetivo", '
    '"pontos": ["até 3 pontos principais, uma frase cada"], '
    '"impactos": ["até 3 implicações práticas para equipes de marketing, mídia ou conteúdo"]}. '
    "Se o texto não permitir apontar impactos com segurança, devolva a lista de impactos vazia."
)

# Modelos que aceitam o fallback automático do servidor em caso de recusa.
_FALLBACK_PREFIXES = ("claude-opus-5", "claude-fable-5", "claude-sonnet-5-5")


class LLMUnavailable(Exception):
    """Erro que impede continuar neste ciclo (chave inválida, limite, provedor fora do ar)."""


def parse_analysis(text: str) -> dict | None:
    """Lê o JSON devolvido pelo modelo, tolerando texto ao redor."""
    start, end = text.find("{"), text.rfind("}")
    if start < 0 or end <= start:
        return None
    try:
        data = json.loads(text[start : end + 1])
    except json.JSONDecodeError:
        return None
    resumo = str(data.get("resumo") or "").strip()
    if not resumo:
        return None

    def _list(key: str) -> list[str]:
        value = data.get(key) or []
        if not isinstance(value, list):
            return []
        return [str(item).strip() for item in value if str(item).strip()][:3]

    return {"resumo": resumo[:700], "pontos": _list("pontos"), "impactos": _list("impactos")}


def _user_message(titulo: str, trecho: str, veiculo: str) -> str:
    return f"Veículo: {veiculo}\nTítulo: {titulo}\nTrecho da fonte: {trecho or '(sem trecho; use só o título)'}"


class ClaudeProvider:
    name = "claude"

    def __init__(self, cfg: Settings) -> None:
        try:
            import anthropic
        except ImportError as exc:  # pacote opcional
            raise LLMUnavailable("pacote 'anthropic' não instalado (pip install anthropic)") from exc
        self._anthropic = anthropic
        # Credenciais vêm do ambiente (ANTHROPIC_API_KEY); certificados, do Windows,
        # para funcionar atrás de antivírus/proxy que inspeciona HTTPS.
        self._client = anthropic.Anthropic(
            http_client=anthropic.DefaultHttpxClient(verify=ssl.create_default_context())
        )
        self._model = cfg.claude_model

    def analyze(self, titulo: str, trecho: str, veiculo: str) -> dict | None:
        anthropic = self._anthropic
        params = dict(
            model=self._model,
            max_tokens=8000,
            system=SYSTEM_PROMPT,
            output_config={"effort": "low"},
            messages=[{"role": "user", "content": _user_message(titulo, trecho, veiculo)}],
        )
        try:
            if self._model.startswith(_FALLBACK_PREFIXES):
                response = self._client.beta.messages.create(
                    betas=["server-side-fallback-2026-07-01"], fallbacks="default", **params
                )
            else:
                response = self._client.messages.create(**params)
        except (anthropic.AuthenticationError, anthropic.PermissionDeniedError) as exc:
            raise LLMUnavailable("chave da Anthropic inválida ou sem permissão") from exc
        except anthropic.NotFoundError as exc:
            raise LLMUnavailable(f"modelo não encontrado: {self._model}") from exc
        except anthropic.BadRequestError as exc:
            raise LLMUnavailable(f"requisição recusada pela API: {exc.message}") from exc
        except anthropic.RateLimitError as exc:
            raise LLMUnavailable("limite de uso da API atingido; continua no próximo ciclo") from exc
        except anthropic.APIStatusError as exc:
            log.warning("Claude devolveu erro %s; notícia fica para depois", exc.status_code)
            return None
        except anthropic.APIConnectionError as exc:
            raise LLMUnavailable("sem conexão com a API da Anthropic") from exc
        if response.stop_reason == "refusal":
            return None
        text = "".join(block.text for block in response.content if block.type == "text")
        return parse_analysis(text)


class OllamaProvider:
    name = "ollama"

    def __init__(self, cfg: Settings) -> None:
        self._url = f"{cfg.ollama_url}/api/chat"
        self._model = cfg.ollama_model

    def analyze(self, titulo: str, trecho: str, veiculo: str) -> dict | None:
        payload = {
            "model": self._model,
            "stream": False,
            "format": "json",
            "messages": [
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": _user_message(titulo, trecho, veiculo)},
            ],
        }
        try:
            response = httpx.post(self._url, json=payload, timeout=180)
        except httpx.HTTPError as exc:
            raise LLMUnavailable(f"Ollama não respondeu em {self._url}") from exc
        if response.status_code != 200:
            raise LLMUnavailable(f"Ollama devolveu HTTP {response.status_code}")
        return parse_analysis(response.json().get("message", {}).get("content", ""))


def get_provider(cfg: Settings):
    """Provedor configurado, ou None quando a IA está desligada."""
    if cfg.llm_provider == "claude":
        return ClaudeProvider(cfg)
    if cfg.llm_provider == "ollama":
        return OllamaProvider(cfg)
    return None


def enrich_pending(conn: sqlite3.Connection, provider, limit: int) -> int:
    """Gera resumo/pontos/impactos para as notícias recentes mais relevantes ainda sem IA."""
    if provider is None or limit <= 0:
        return 0
    rows = conn.execute(
        """
        SELECT a.id, a.titulo, a.trecho, a.veiculo
        FROM articles a JOIN article_analysis an ON an.article_id = a.id
        WHERE an.provedor = 'regras' AND a.coletado_em >= ?
        ORDER BY a.relevancia DESC, a.id DESC LIMIT ?
        """,
        (to_iso(utcnow() - timedelta(days=3)), limit),
    ).fetchall()
    done = 0
    for row in rows:
        try:
            analysis = provider.analyze(row["titulo"], row["trecho"] or "", row["veiculo"])
        except LLMUnavailable as exc:
            log.warning("IA indisponível: %s", exc)
            break
        if not analysis:
            continue
        conn.execute(
            """
            UPDATE article_analysis
            SET resumo = ?, pontos = ?, impactos = ?, provedor = ?, gerado_em = ?
            WHERE article_id = ?
            """,
            (
                analysis["resumo"],
                json.dumps(analysis["pontos"], ensure_ascii=False),
                json.dumps(analysis["impactos"], ensure_ascii=False),
                provider.name, now_iso(), row["id"],
            ),
        )
        conn.execute("UPDATE articles_fts SET resumo = ? WHERE rowid = ?", (analysis["resumo"], row["id"]))
        conn.commit()
        done += 1
    return done
