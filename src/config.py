"""Application configuration loaded from environment variables."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
import os
from pathlib import Path

from dotenv import load_dotenv


PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_ENV_FILE = PROJECT_ROOT / ".env"
DEFAULT_AS_OF_DATE = date(2026, 8, 23)


class ConfigurationError(RuntimeError):
    """Raised when required application configuration is missing or invalid."""


@dataclass(frozen=True, slots=True)
class DatabaseConfig:
    """PostgreSQL connection settings."""

    host: str
    port: int
    dbname: str
    user: str
    password: str

    def as_connection_kwargs(self) -> dict[str, str | int]:
        """Return keyword arguments accepted by ``psycopg.connect``."""

        return {
            "host": self.host,
            "port": self.port,
            "dbname": self.dbname,
            "user": self.user,
            "password": self.password,
        }


def get_database_config(env_file: Path = DEFAULT_ENV_FILE) -> DatabaseConfig:
    """Load and validate PostgreSQL configuration without exposing secrets."""

    load_dotenv(dotenv_path=env_file, override=False)

    variable_names = ("DB_HOST", "DB_PORT", "DB_NAME", "DB_USER", "DB_PASSWORD")
    values = {name: os.getenv(name) for name in variable_names}
    missing = [name for name, value in values.items() if value is None or not value.strip()]
    if missing:
        missing_list = ", ".join(missing)
        raise ConfigurationError(
            f"Missing required database configuration: {missing_list}. "
            f"Set the values in {env_file}."
        )

    def required_value(name: str) -> str:
        value = values[name]
        if value is None or not value.strip():
            raise ConfigurationError(f"Missing required database configuration: {name}.")
        return value

    try:
        port = int(required_value("DB_PORT"))
    except ValueError as exc:
        raise ConfigurationError("DB_PORT must be a whole number.") from exc

    if not 1 <= port <= 65535:
        raise ConfigurationError("DB_PORT must be between 1 and 65535.")

    return DatabaseConfig(
        host=required_value("DB_HOST"),
        port=port,
        dbname=required_value("DB_NAME"),
        user=required_value("DB_USER"),
        password=required_value("DB_PASSWORD"),
    )
