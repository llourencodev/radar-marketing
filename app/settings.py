"""Configuração: caminhos do projeto, leitura do .env e dos arquivos em config/."""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
CONFIG_DIR = BASE_DIR / "config"
WEB_DIR = BASE_DIR / "web"
DATA_DIR = BASE_DIR / "data"

USER_AGENT = "RadarMarketing/1.0 (leitor pessoal de noticias; uso local)"
HTTP_TIMEOUT = 20.0
HOST_MIN_INTERVAL = 2.0  # segundos entre requisições ao mesmo site
DEDUP_THRESHOLD = 0.85
DEDUP_WINDOW_DAYS = 7
MAX_BACKOFF_MIN = 24 * 60


def load_env_file(path: Path) -> None:
    """Carrega pares CHAVE=valor de um .env sem sobrescrever o ambiente real."""
    if not path.is_file():
        return
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        value = value.strip().strip('"').strip("'")
        os.environ.setdefault(key.strip(), value)


def _env_int(name: str, default: int) -> int:
    try:
        return int(os.environ.get(name, "") or default)
    except ValueError:
        return default


@dataclass(frozen=True)
class Settings:
    host: str = "127.0.0.1"
    port: int = 8765
    db_path: Path = field(default_factory=lambda: DATA_DIR / "radar.db")
    collect_interval_min: int = 60
    fetch_page_image: bool = True
    llm_provider: str = "none"
    llm_max_per_run: int = 25
    claude_model: str = "claude-opus-5-5"
    ollama_url: str = "http://127.0.0.1:11434"
    ollama_model: str = "llama3.1"


def get_settings() -> Settings:
    load_env_file(BASE_DIR / ".env")
    return Settings(
        host=os.environ.get("RADAR_HOST", "127.0.0.1"),
        port=_env_int("RADAR_PORT", 8765),
        db_path=Path(os.environ.get("RADAR_DB", str(DATA_DIR / "radar.db"))),
        collect_interval_min=max(5, _env_int("COLLECT_INTERVAL_MIN", 60)),
        fetch_page_image=os.environ.get("FETCH_PAGE_IMAGE", "1") != "0",
        llm_provider=(os.environ.get("LLM_PROVIDER", "none") or "none").strip().lower(),
        llm_max_per_run=max(0, _env_int("LLM_MAX_PER_RUN", 25)),
        claude_model=os.environ.get("CLAUDE_MODEL", "claude-opus-5-5") or "claude-opus-5-5",
        ollama_url=os.environ.get("OLLAMA_URL", "http://127.0.0.1:11434").rstrip("/"),
        ollama_model=os.environ.get("OLLAMA_MODEL", "llama3.1"),
    )


def _load_json(name: str):
    return json.loads((CONFIG_DIR / name).read_text(encoding="utf-8"))


@lru_cache(maxsize=1)
def load_categories() -> list[dict]:
    return _load_json("categories.json")


# Listas usadas só pela classificação; a interface não precisa delas.
_RULE_FIELDS = {"palavras", "marcas", "regiao"}


def public_categories() -> list[dict]:
    """Abas como a interface as recebe (sem as listas de palavras da classificação)."""
    return [{k: v for k, v in c.items() if k not in _RULE_FIELDS} for c in load_categories()]


@lru_cache(maxsize=1)
def load_glossary() -> dict[str, str]:
    return _load_json("glossary.json")


def load_sources_config() -> list[dict]:
    return _load_json("sources.json")
