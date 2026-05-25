"""Configuration and defaults."""
from __future__ import annotations

import logging
import pathlib
from dataclasses import dataclass, field
from typing import Optional

logger = logging.getLogger("meshtad.config")

DEFAULT_DB_NAME = "meshtad.db"
MAX_PAYLOAD_BYTES = 228  # conservative; Meshtastic Constants.DATA_PAYLOAD_LEN = 233


def _load_toml(path: pathlib.Path) -> dict:
    import tomllib
    with open(path, "rb") as f:
        return tomllib.load(f)


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
        """Load config from a TOML file.  Missing keys keep their defaults."""
        db = path.parent / DEFAULT_DB_NAME if path.suffix == ".toml" else path
        cfg = cls(db_path=db)

        if not path.exists():
            return cfg

        try:
            data = _load_toml(path)
        except Exception as exc:
            logger.warning("Failed to parse config %s: %s; using defaults", path, exc)
            return cfg

        meshtad = data.get("meshtad", {})
        auto_delete = data.get("auto_delete", {})

        for key in ("log_level", "serial_port"):
            if key in meshtad:
                setattr(cfg, key, meshtad[key])

        for key in ("max_retries", "size_warning_mb"):
            if key in meshtad:
                setattr(cfg, key, int(meshtad[key]))

        for key in ("retry_initial_s", "retry_max_s", "retry_base", "ack_timeout_s"):
            if key in meshtad:
                setattr(cfg, key, float(meshtad[key]))

        for key in ("redact_bodies", "size_warning_enabled"):
            if key in meshtad:
                val = meshtad[key]
                if isinstance(val, str):
                    setattr(cfg, key, val.lower() not in ("false", "no", "0", "off"))
                else:
                    setattr(cfg, key, bool(val))

        if "global_s" in auto_delete:
            cfg.auto_delete_global_s = int(auto_delete["global_s"])

        return cfg

    @classmethod
    def default(cls, base_dir: Optional[pathlib.Path] = None) -> "Config":
        base = base_dir or pathlib.Path("~/.local/share/meshtad").expanduser()
        return cls(db_path=base / DEFAULT_DB_NAME)


class ConfigWatcher:
    """Watches a TOML config file and reloads when its mtime changes."""

    def __init__(self, path: pathlib.Path):
        self.path = path
        self.current = Config.from_toml(path)
        self._mtime: Optional[float] = self._get_mtime()

    def _get_mtime(self) -> Optional[float]:
        try:
            return self.path.stat().st_mtime
        except (OSError, FileNotFoundError):
            return None

    def reload_if_changed(self) -> Optional[Config]:
        """Return a new Config if the file changed; otherwise None."""
        current_mtime = self._get_mtime()
        if current_mtime is None:
            # File missing — keep current config, don't crash
            return None
        if self._mtime is not None and current_mtime <= self._mtime:
            return None
        self._mtime = current_mtime
        self.current = Config.from_toml(self.path)
        return self.current
