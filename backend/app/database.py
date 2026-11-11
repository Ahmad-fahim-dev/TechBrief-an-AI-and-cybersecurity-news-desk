from collections.abc import Generator
from fastapi import Request
from sqlalchemy import create_engine, inspect
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker


class Base(DeclarativeBase):
    pass


def build_engine(database_url: str):
    connect_args = {"check_same_thread": False} if database_url.startswith("sqlite") else {}
    return create_engine(database_url, connect_args=connect_args, pool_pre_ping=True)


def build_session_factory(engine):
    return sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)


def add_article_image_columns(engine) -> None:
    if "articles" not in inspect(engine).get_table_names():
        return
    existing = {column["name"] for column in inspect(engine).get_columns("articles")}
    columns = {
        "image_url": "TEXT",
        "image_alt": "TEXT",
        "image_credit": "VARCHAR(300)",
        "image_source_name": "VARCHAR(180)",
        "image_source_url": "TEXT",
        "image_license": "VARCHAR(300)",
        "image_type": "VARCHAR(32) NOT NULL DEFAULT 'category_fallback'",
        "is_illustrative": "BOOLEAN NOT NULL DEFAULT TRUE",
        "image_verified": "BOOLEAN NOT NULL DEFAULT FALSE",
        "image_checked_at": (
            "TIMESTAMP WITH TIME ZONE" if engine.dialect.name == "postgresql" else "DATETIME"
        ),
    }
    missing = [(name, definition) for name, definition in columns.items() if name not in existing]
    if missing:
        with engine.begin() as connection:
            for name, definition in missing:
                connection.exec_driver_sql(f"ALTER TABLE articles ADD COLUMN {name} {definition}")


def get_db(request: Request) -> Generator[Session, None, None]:
    session = request.app.state.session_factory()
    try:
        yield session
    finally:
        session.close()
