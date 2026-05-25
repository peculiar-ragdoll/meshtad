"""Regression tests for Config edge cases."""
from __future__ import annotations

import pathlib
import tempfile

import pytest

from meshtad.config import Config, ConfigWatcher


class TestBoolStringCoercion:
    def test_string_false_becomes_false(self):
        """A string 'false' in TOML must parse as boolean False."""
        with tempfile.TemporaryDirectory() as td:
            p = pathlib.Path(td) / "config.toml"
            p.write_text('[meshtad]\nredact_bodies = "false"\n')
            cfg = Config.from_toml(p)
            assert cfg.redact_bodies is False

    def test_string_no_becomes_false(self):
        with tempfile.TemporaryDirectory() as td:
            p = pathlib.Path(td) / "config.toml"
            p.write_text('[meshtad]\nsize_warning_enabled = "no"\n')
            cfg = Config.from_toml(p)
            assert cfg.size_warning_enabled is False

    def test_string_0_becomes_false(self):
        with tempfile.TemporaryDirectory() as td:
            p = pathlib.Path(td) / "config.toml"
            p.write_text('[meshtad]\nredact_bodies = "0"\n')
            cfg = Config.from_toml(p)
            assert cfg.redact_bodies is False

    def test_bare_false_still_false(self):
        with tempfile.TemporaryDirectory() as td:
            p = pathlib.Path(td) / "config.toml"
            p.write_text('[meshtad]\nredact_bodies = false\n')
            cfg = Config.from_toml(p)
            assert cfg.redact_bodies is False

    def test_bare_true_still_true(self):
        with tempfile.TemporaryDirectory() as td:
            p = pathlib.Path(td) / "config.toml"
            p.write_text('[meshtad]\nredact_bodies = true\n')
            cfg = Config.from_toml(p)
            assert cfg.redact_bodies is True

    def test_string_true_becomes_true(self):
        with tempfile.TemporaryDirectory() as td:
            p = pathlib.Path(td) / "config.toml"
            p.write_text('[meshtad]\nredact_bodies = "true"\n')
            cfg = Config.from_toml(p)
            assert cfg.redact_bodies is True


class TestMalformedToml:
    def test_malformed_toml_returns_defaults_and_warns(self, caplog):
        with tempfile.TemporaryDirectory() as td:
            p = pathlib.Path(td) / "config.toml"
            p.write_text("[meshtad\nunclosed = 1\n")
            with caplog.at_level("WARNING"):
                cfg = Config.from_toml(p)
            assert cfg.redact_bodies is True  # default
            assert "Failed to parse" in caplog.text
