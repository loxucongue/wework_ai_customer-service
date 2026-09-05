from __future__ import annotations

from logging.config import fileConfig
from urllib.parse import quote_plus

from alembic import context
from sqlalchemy import create_engine, pool, text

from app.config import Settings
from app.services.storage.mysql_schema import metadata


config = context.config
if config.config_file_name is not None:
    fileConfig(config.config_file_name)

target_metadata = metadata


def _settings() -> Settings:
    settings = Settings()
    if settings.aics_mysql_database != "wecom_cs":
        raise RuntimeError("Alembic refuses to manage a database other than wecom_cs")
    if settings.aics_table_prefix != "aics_":
        raise RuntimeError("Alembic requires AICS_TABLE_PREFIX=aics_")
    return settings


def _url(settings: Settings) -> str:
    return (
        f"mysql+pymysql://{quote_plus(settings.aics_mysql_user)}:"
        f"{quote_plus(settings.aics_mysql_password)}@"
        f"{settings.aics_mysql_host}:{settings.aics_mysql_port}/"
        f"{settings.aics_mysql_database}?charset=utf8mb4"
    )


def _connect_args(settings: Settings) -> dict:
    args: dict = {
        "connect_timeout": settings.aics_mysql_connect_timeout_seconds,
        "read_timeout": settings.aics_mysql_read_timeout_seconds,
        "write_timeout": settings.aics_mysql_write_timeout_seconds,
    }
    if settings.aics_mysql_ssl_ca:
        args["ssl"] = {"ca": settings.aics_mysql_ssl_ca}
    elif settings.aics_mysql_ssl_required:
        args["ssl"] = {"check_hostname": False}
    return args


def _validate_connection(connection, settings: Settings) -> None:
    database = connection.execute(text("SELECT DATABASE()")).scalar()
    if database != settings.aics_mysql_database:
        raise RuntimeError("Alembic connected to an unexpected database")
    ssl_cipher = connection.execute(text("SHOW STATUS LIKE 'Ssl_cipher'")).mappings().first()
    if settings.aics_mysql_ssl_required and not str((ssl_cipher or {}).get("Value") or ""):
        raise RuntimeError("Alembic refuses an unencrypted MySQL connection")


def run_migrations_offline() -> None:
    settings = _settings()
    context.configure(
        url=_url(settings),
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        version_table="aics_schema_version",
        include_object=lambda obj, name, type_, reflected, compare_to: (
            type_ != "table" or name.startswith("aics_")
        ),
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    settings = _settings()
    engine = create_engine(
        _url(settings),
        poolclass=pool.NullPool,
        connect_args=_connect_args(settings),
    )
    with engine.connect() as connection:
        _validate_connection(connection, settings)
        context.configure(
            connection=connection,
            target_metadata=target_metadata,
            version_table="aics_schema_version",
            compare_type=True,
            include_object=lambda obj, name, type_, reflected, compare_to: (
                type_ != "table" or name.startswith("aics_")
            ),
        )
        with context.begin_transaction():
            context.run_migrations()
        # MySQL DDL is non-transactional, while the Alembic version-table
        # update is ordinary DML.  SQLAlchemy 2.x autobegins that DML after
        # the DDL's implicit commit; closing the connection without an
        # explicit commit rolls the revision marker back even though the
        # columns and indexes already exist.
        connection.commit()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
