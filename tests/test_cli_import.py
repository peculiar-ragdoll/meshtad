"""Regression test: importing cli.py must NOT create ~/.local/share/meshtad/."""
from __future__ import annotations

import pathlib
import sys
import tempfile
from unittest.mock import patch


def test_import_does_not_create_default_dir():
    """Before any argument parsing, the CLI module must not eagerly create
    the default meshtad state directory."""
    with tempfile.TemporaryDirectory() as fake_home:
        fake_local = pathlib.Path(fake_home) / ".local" / "share"
        fake_local.mkdir(parents=True)
        with patch.dict(sys.modules):
            # Remove cached module so fresh import runs
            sys.modules.pop("meshtad.cli", None)
            # Point HOME away so expanduser hits our fake dir
            with patch.object(pathlib.Path, "home", return_value=pathlib.Path(fake_home)):
                import meshtad.cli as _cli_module  # noqa: F401
        assert not (fake_local / "meshtad").exists()
