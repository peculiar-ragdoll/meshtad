"""RED-phase tests for macOS serial-port auto-detect fallback.

These tests exercise Radio.find_macos_ports() and the CLI surface
that exposes it.  They will fail until the implementation lands.
"""
from __future__ import annotations

import io
import pathlib
import sys
from contextlib import redirect_stdout
from unittest.mock import patch

import pytest

from meshtad.cli import main
from meshtad.radio import Radio


class TestFindMacosPorts:
    @patch("pathlib.Path.glob")
    def test_finds_usbserial(self, mock_glob):
        """find_macos_ports() returns /dev/cu.usbserial-* devices."""
        mock_glob.side_effect = lambda pattern: (
            [pathlib.Path("/dev/cu.usbserial-ABCD")]
            if "usbserial*" in pattern
            else []
        )
        ports = Radio.find_macos_ports()
        assert "/dev/cu.usbserial-ABCD" in ports

    @patch("pathlib.Path.glob")
    def test_finds_usbmodem(self, mock_glob):
        """find_macos_ports() returns /dev/cu.usbmodem-* devices."""
        mock_glob.side_effect = lambda pattern: (
            [pathlib.Path("/dev/cu.usbmodem-EFGH")]
            if "usbmodem*" in pattern
            else []
        )
        ports = Radio.find_macos_ports()
        assert "/dev/cu.usbmodem-EFGH" in ports

    @patch("pathlib.Path.glob")
    def test_returns_empty_when_no_devices(self, mock_glob):
        """No matching devices yields an empty list."""
        mock_glob.return_value = []
        ports = Radio.find_macos_ports()
        assert ports == []

    def test_skips_nonexistent_root(self):
        """Glob on /dev is safe even when the directory somehow vanishes."""
        # Force glob to return nothing by mocking the root
        with patch.object(pathlib.Path, "glob", return_value=[]):
            ports = Radio.find_macos_ports()
            assert ports == []


class TestCliDongleDetectMacosFallback:
    @patch("meshtad.radio.Radio.detect_ports")
    @patch("meshtad.radio.Radio.find_macos_ports")
    def test_falls_back_when_meshtastic_empty(self, mock_macos, mock_primary):
        """dongle-detect lists macOS ports when meshtastic.util.findPorts() returns nothing."""
        mock_primary.return_value = []
        mock_macos.return_value = ["/dev/cu.usbserial-ABCD"]

        buf = io.StringIO()
        with patch.object(sys, "argv", ["meshcli", "dongle-detect"]):
            with redirect_stdout(buf):
                code = main()
        assert code == 0
        assert "/dev/cu.usbserial-ABCD" in buf.getvalue()
