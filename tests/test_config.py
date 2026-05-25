"""Unit tests for meshtad.config."""
from __future__ import annotations

import pathlib

import pytest

from meshtad.config import Config, MAX_PAYLOAD_BYTES, DEFAULT_DB_NAME


class TestConfig:
    def test_default_paths(self):
        """Default config uses ~/.local/share/meshtad/meshtad.db."""
        cfg = Config.default()
        assert cfg.db_path.name == DEFAULT_DB_NAME
        assert str(cfg.db_path).endswith(f"meshtad/{DEFAULT_DB_NAME}")

    def test_custom_base_dir(self):
        """default() accepts a custom base directory."""
        cfg = Config.default(base_dir=pathlib.Path("/tmp/foo"))
        assert cfg.db_path == pathlib.Path("/tmp/foo") / DEFAULT_DB_NAME

    def test_max_payload_constant(self):
        """MAX_PAYLOAD_BYTES is the agreed conservative limit."""
        assert MAX_PAYLOAD_BYTES == 228

    def test_config_is_mutable_for_testing(self):
        """Config is not frozen so tests/daemon can tweak fields."""
        cfg = Config.default()
        cfg.max_retries = 2
        assert cfg.max_retries == 2

    def test_retry_backoff_params(self):
        """Retry math: initial * base^(count) clamped at max."""
        cfg = Config.default()
        assert cfg.retry_initial_s == 5.0
        assert cfg.retry_base == 2.0
        assert cfg.retry_max_s == 300.0
        for retry_count in range(cfg.max_retries + 1):
            delay = min(
                cfg.retry_initial_s * (cfg.retry_base ** retry_count),
                cfg.retry_max_s,
            )
            assert delay > 0

    def test_ack_timeout_positive(self):
        """ACK timeout must be positive."""
        cfg = Config.default()
        assert cfg.ack_timeout_s > 0

    def test_redact_bodies_default(self):
        """Privacy: redact_bodies defaults True."""
        cfg = Config.default()
        assert cfg.redact_bodies is True


class TestTuiConfig:
    def test_tui_defaults(self):
        """TUI config fields have sensible defaults."""
        cfg = Config.default()
        assert cfg.tui_poll_interval_s == 2.0
        assert cfg.tui_theme == "dark"

    def test_tui_poll_interval_from_toml(self, tmp_path):
        """[tui] poll_interval_s is parsed from config.toml."""
        cfg_path = tmp_path / "config.toml"
        cfg_path.write_text("\n[tui]\npoll_interval_s = 5.5\n")
        cfg = Config.from_toml(cfg_path)
        assert cfg.tui_poll_interval_s == 5.5

    def test_tui_theme_from_toml(self, tmp_path):
        """[tui] theme is parsed from config.toml."""
        cfg_path = tmp_path / "config.toml"
        cfg_path.write_text("\n[tui]\ntheme = \"light\"\n")
        cfg = Config.from_toml(cfg_path)
        assert cfg.tui_theme == "light"

    def test_tui_theme_no_color(self, tmp_path):
        """[tui] theme \"no-color\" disables colours."""
        cfg_path = tmp_path / "config.toml"
        cfg_path.write_text("\n[tui]\ntheme = \"no-color\"\n")
        cfg = Config.from_toml(cfg_path)
        assert cfg.tui_theme == "no-color"