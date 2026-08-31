"""Unit tests for storops.platform.windows.copy (RobocopyEngine).

Mocks subprocess.run -- this is the highest-value test achievable without
a real Windows machine + robocopy.exe: it locks down the exact argument
list and the exit-code interpretation (0-7 success, >=8 failure).
"""
from __future__ import annotations

import subprocess

import pytest

from storops.core.errors import BackendNotFoundError, StoropsError
from storops.platform.windows import copy as copy_mod


class TestRobocopyEngineArgs:
    def test_copy_invokes_expected_args(self, monkeypatch):
        captured: dict = {}

        def fake_run(args, capture_output=True):
            captured["args"] = args
            captured["capture_output"] = capture_output
            return subprocess.CompletedProcess(args, 1, stdout=b"", stderr=b"")

        monkeypatch.setattr(
            copy_mod.shutil, "which", lambda name: "C:\\Windows\\System32\\robocopy.exe" if name == "robocopy.exe" else None
        )
        monkeypatch.setattr(copy_mod.subprocess, "run", fake_run)

        engine = copy_mod.RobocopyEngine()
        engine.copy("C:\\src", "D:\\dst")

        assert captured["args"] == [
            "C:\\Windows\\System32\\robocopy.exe",
            "C:\\src",
            "D:\\dst",
            "/E",
            "/COPY:DAT",
            "/R:2",
            "/W:2",
            "/MT:8",
            "/NFL",
            "/NDL",
            "/NP",
            "/NJH",
            "/NJS",
        ]
        assert captured["capture_output"] is True

    def test_kind_is_robocopy(self):
        assert copy_mod.RobocopyEngine.kind == "robocopy"

    def test_robocopy_not_found_raises_backend_not_found(self, monkeypatch):
        monkeypatch.setattr(copy_mod.shutil, "which", lambda name: None)
        engine = copy_mod.RobocopyEngine()
        with pytest.raises(BackendNotFoundError):
            engine.copy("C:\\src", "D:\\dst")


class TestRobocopyExitCodeInterpretation:
    @pytest.mark.parametrize(
        ("code", "should_raise"),
        [
            (0, False),  # no files copied, no errors
            (1, False),  # files copied successfully
            (7, False),  # highest success bit-flag combination
            (8, True),   # at least one failure
            (16, True),  # fatal error
        ],
    )
    def test_exit_code_maps_to_success_or_failure(self, monkeypatch, code, should_raise):
        monkeypatch.setattr(copy_mod.shutil, "which", lambda name: "robocopy.exe")
        monkeypatch.setattr(
            copy_mod.subprocess,
            "run",
            lambda args, capture_output=True: subprocess.CompletedProcess(args, code, stdout=b"", stderr=b""),
        )
        engine = copy_mod.RobocopyEngine()

        if should_raise:
            with pytest.raises(StoropsError):
                engine.copy("C:\\src", "D:\\dst")
        else:
            engine.copy("C:\\src", "D:\\dst")  # must not raise

    def test_failure_message_includes_detail_from_output(self, monkeypatch):
        monkeypatch.setattr(copy_mod.shutil, "which", lambda name: "robocopy.exe")
        monkeypatch.setattr(
            copy_mod.subprocess,
            "run",
            lambda args, capture_output=True: subprocess.CompletedProcess(
                args, 16, stdout=b"", stderr=b"ERROR 5 (0x00000005) Access is denied."
            ),
        )
        engine = copy_mod.RobocopyEngine()
        with pytest.raises(StoropsError, match="Access is denied"):
            engine.copy("C:\\src", "D:\\dst")
