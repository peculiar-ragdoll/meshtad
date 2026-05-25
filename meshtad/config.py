"""Configuration and defaults."""
from __future__ import annotations

import pathlib
from dataclasses import dataclass
from typing import Optional

DEFAULT_DB_NAME = "meshtad.db"
MAX_PAYLOAD_BYTES = 228  # conservative; Meshtastic Constants.DATA_PAYLOAD_LEN = 233


@dataclass(frozen=False)
class Config:
    db_path: pathlib.Path
    log_level: str = "INFO"
    redact_bodies: bool = True
    max_retries: int = 5
    retry_initial_s: float = 5.0
    retry_max_s: float = 300.0
    retry_base: float = 2.0
    ack_timeout_s: float = 30.0
    auto_delete_global_s: Optional[int] = None
    size_warning_mb: int = 100
    size_warning_enabled: bool = True
    serial_port: Optional[str] = None  # None = auto-detect

    @classmethod
    def from_toml(cls, path: pathlib.Path) -> "Config":
        # TODO: load TOML; for now, all defaults
        db = path.parent / DEFAULT_DB_NAME if path.suffix == ".toml" else path
        return cls(db_path=db)

    @classmethod
    def default(cls, base_dir: Optional[pathlib.Path] = None) -> "Config":
        base = base_dir or pathlib.Path("~/.local/share/meshtad").expanduser()
        return cls(db_path=base / DEFAULT_DB_NAME)
