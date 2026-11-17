"""Acesso ao SQLite: esquema, conexão e sincronização das fontes configuradas."""

from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator
from urllib.parse import quote_plus

SCHEMA = """
CREATE TABLE IF NOT EXISTS sources (
    id              INTEGER PRIMARY KEY,
    nome            TEXT NOT NULL,
    tipo            TEXT NOT NULL CHECK (tipo IN ('rss', 'html', 'search')),
    url             TEXT NOT NULL UNIQUE,
    consulta        TEXT,
    categoria       TEXT NOT NULL,
    oficial         INTEGER NOT NULL DEFAULT 0,
    prioridade      INTEGER NOT NULL DEFAULT 1,
    intervalo_min   INTEGER,
    ativo           INTEGER NOT NULL DEFAULT 1,
    idioma          TEXT NOT NULL DEFAULT 'pt',
    seletor         TEXT,
    etag            TEXT,
    last_modified   TEXT,
    ultimo_sucesso  TEXT,
    ultima_tentativa TEXT,
    falhas_seguidas INTEGER NOT NULL DEFAULT 0,
    ultimo_erro     TEXT,
    regional        INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS article_groups (
    id          INTEGER PRIMARY KEY,
    titulo      TEXT NOT NULL,
    primeira_pub TEXT,
    ultima_pub  TEXT,
    n_fontes    INTEGER NOT NULL DEFAULT 1
);

CREATE TABLE IF NOT EXISTS articles (
    id              INTEGER PRIMARY KEY,
    titulo          TEXT NOT NULL,
    titulo_norm     TEXT NOT NULL,
    url             TEXT NOT NULL,
    url_canonica    TEXT NOT NULL UNIQUE,
    source_id       INTEGER NOT NULL REFERENCES sources(id),
    veiculo         TEXT NOT NULL,
    imagem_url      TEXT,
    imagem_origem   TEXT NOT NULL DEFAULT 'nenhuma',
    publicado_em    TEXT,
    data_confirmada INTEGER NOT NULL DEFAULT 0,
    coletado_em     TEXT NOT NULL,
    trecho          TEXT,
    idioma          TEXT NOT NULL DEFAULT 'pt',
    oficial         INTEGER NOT NULL DEFAULT 0,
    relevancia      INTEGER NOT NULL DEFAULT 0,
    status          TEXT NOT NULL DEFAULT 'verificada',
    grupo_id        INTEGER REFERENCES article_groups(id)
);
CREATE INDEX IF NOT EXISTS idx_articles_data ON articles (COALESCE(publicado_em, coletado_em));
CREATE INDEX IF NOT EXISTS idx_articles_grupo ON articles (grupo_id);
CREATE INDEX IF NOT EXISTS idx_articles_source ON articles (source_id);

CREATE TABLE IF NOT EXISTS article_categories (
    article_id  INTEGER NOT NULL REFERENCES articles(id) ON DELETE CASCADE,
    categoria   TEXT NOT NULL,
    origem      TEXT NOT NULL,
    confianca   REAL NOT NULL,
    PRIMARY KEY (article_id, categoria)
);
CREATE INDEX IF NOT EXISTS idx_artcat_categoria ON article_categories (categoria);

CREATE TABLE IF NOT EXISTS article_analysis (
    article_id  INTEGER PRIMARY KEY REFERENCES articles(id) ON DELETE CASCADE,
    resumo      TEXT,
    pontos      TEXT NOT NULL DEFAULT '[]',
    impactos    TEXT NOT NULL DEFAULT '[]',
    termos      TEXT NOT NULL DEFAULT '[]',
    tipo        TEXT NOT NULL,
    sinal       TEXT,
    natureza    TEXT NOT NULL DEFAULT 'fato',
    provedor    TEXT NOT NULL DEFAULT 'regras',
    gerado_em   TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS collection_runs (
    id           INTEGER PRIMARY KEY,
    inicio       TEXT NOT NULL,
    fim          TEXT,
    gatilho      TEXT NOT NULL,
    fontes_ok    INTEGER NOT NULL DEFAULT 0,
    fontes_falha INTEGER NOT NULL DEFAULT 0,
    itens_novos  INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS collection_logs (
    id          INTEGER PRIMARY KEY,
    run_id      INTEGER NOT NULL REFERENCES collection_runs(id) ON DELETE CASCADE,
    source_id   INTEGER NOT NULL REFERENCES sources(id),
    status_http INTEGER,
    erro        TEXT,
    duracao_ms  INTEGER,
    itens_novos INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS app_meta (
    chave TEXT PRIMARY KEY,
    valor TEXT NOT NULL
);

CREATE VIRTUAL TABLE IF NOT EXISTS articles_fts USING fts5(
    titulo, trecho, resumo, veiculo,
    tokenize = "unicode61 remove_diacritics 2"
);
"""


def connect(db_path: Path | str) -> sqlite3.Connection:
    path = Path(db_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path, timeout=30)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA journal_mode = WAL")
    return conn


@contextmanager
def session(db_path: Path | str) -> Iterator[sqlite3.Connection]:
    """Conexão com commit ao final ou rollback em caso de erro."""
    conn = connect(db_path)
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def search_url(motor: str, consulta: str, idioma: str = "pt") -> str:
    """Monta a URL de RSS de busca (Google News ou Bing News, sem chave)."""
    query = quote_plus(consulta)
    if motor == "bing":
        market = "&setlang=pt-br&cc=BR" if idioma == "pt" else "&setlang=en-us&cc=US"
        return f"https://www.bing.com/news/search?q={query}&format=rss{market}"
    locale = "hl=pt-BR&gl=BR&ceid=BR:pt-419" if idioma == "pt" else "hl=en-US&gl=US&ceid=US:en"
    return f"https://news.google.com/rss/search?q={query}+when:7d&{locale}"


def source_url(entry: dict) -> str:
    if entry.get("tipo") == "search" and not entry.get("url"):
        return search_url(entry.get("motor", "google"), entry["consulta"], entry.get("idioma", "pt"))
    return entry["url"]


def init_db(db_path: Path | str, sources: list[dict] | None = None) -> None:
    """Cria as tabelas e insere/atualiza as fontes de config/sources.json.

    `"ativo": false` no arquivo desativa a fonte; sem o campo, vale o que estiver no banco
    (no servidor local, a tela "Fontes e coleta" pode ativar e desativar).
    """
    with session(db_path) as conn:
        conn.executescript(SCHEMA)
        _migrate(conn)
        for entry in sources or []:
            conn.execute(
                """
                INSERT INTO sources (nome, tipo, url, consulta, categoria, oficial,
                                     prioridade, intervalo_min, idioma, seletor, ativo, regional)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(url) DO UPDATE SET
                    nome = excluded.nome, categoria = excluded.categoria,
                    oficial = excluded.oficial, prioridade = excluded.prioridade,
                    intervalo_min = excluded.intervalo_min, seletor = excluded.seletor,
                    regional = excluded.regional,
                    ativo = CASE WHEN excluded.ativo = 0 THEN 0 ELSE sources.ativo END
                """,
                (
                    entry["nome"],
                    entry["tipo"],
                    source_url(entry),
                    entry.get("consulta"),
                    entry["categoria"],
                    1 if entry.get("oficial") else 0,
                    int(entry.get("prioridade", 1)),
                    entry.get("intervalo_min"),
                    entry.get("idioma", "pt"),
                    entry.get("seletor"),
                    0 if entry.get("ativo") is False else 1,
                    1 if entry.get("regional") else 0,
                ),
            )


# Colunas criadas depois da primeira versão: bancos antigos (inclusive o guardado no
# cache do GitHub Actions) ganham a coluna sem perder dados.
_ADDED_COLUMNS = {"sources": {"regional": "INTEGER NOT NULL DEFAULT 0"}}


def _migrate(conn: sqlite3.Connection) -> None:
    for table, columns in _ADDED_COLUMNS.items():
        existing = {row[1] for row in conn.execute(f"PRAGMA table_info({table})")}
        for name, ddl in columns.items():
            if name not in existing:
                conn.execute(f"ALTER TABLE {table} ADD COLUMN {name} {ddl}")
    # Imagens antigas gravadas com http: o site em https não consegue exibi-las.
    conn.execute(
        "UPDATE articles SET imagem_url = 'https://' || substr(imagem_url, 8) WHERE imagem_url LIKE 'http://%'"
    )
