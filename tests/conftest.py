"""Fixtures compartilhadas: banco temporário com fontes de teste (sem rede)."""

from __future__ import annotations

from datetime import datetime, timezone

import pytest

from app import db
from app.settings import Settings

NOW = datetime(2026, 11, 15, 12, 0, 0, tzinfo=timezone.utc)

TEST_SOURCES = [
    {"nome": "Blog de SEO", "tipo": "rss", "url": "https://seo.example.com/feed", "categoria": "seo", "prioridade": 2},
    {"nome": "Plataforma Oficial", "tipo": "rss", "url": "https://plataforma.example.com/feed", "categoria": "performance", "oficial": True, "prioridade": 3},
    {"nome": "Jornal de Propaganda", "tipo": "rss", "url": "https://jornal.example.com/feed", "categoria": "branding", "prioridade": 1},
]


@pytest.fixture
def db_path(tmp_path):
    path = tmp_path / "radar.db"
    db.init_db(path, TEST_SOURCES)
    return path


@pytest.fixture
def cfg(db_path):
    return Settings(db_path=db_path, fetch_page_image=False)


@pytest.fixture
def conn(db_path):
    connection = db.connect(db_path)
    yield connection
    connection.close()


@pytest.fixture
def sources(conn):
    return {row["nome"]: dict(row) for row in conn.execute("SELECT * FROM sources")}
