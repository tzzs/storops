"""Unit tests for storops.platform.windows.link (JunctionLinkEngine).

create() is fully mocked (subprocess.run) -- locks down the exact `cmd /c
mklink /J <old> <target>` argument list and the failure->StoropsError
mapping. verify()'s directory-listing heuristic is exercised for real
against tmp_path directories since it uses only os.path/os.listdir, which
behave the same on Linux as on Windows for this purpose.
"""
from __future__ import annotations

import subprocess

import pytest

from storops.core.errors import StoropsError
from storops.platform.windows import link as link_mod


class TestJunctionLinkEngineCreate:
    def test_create_invokes_expected_args(self, monkeypatch):
        captured: dict = {}

        def fake_run(args, capture_output=True, text=True):
            captured["args"] = args
            captured["kwargs"] = {"capture_output": capture_output, "text": text}
            return subprocess.CompletedProcess(args, 0, stdout="", stderr="")

        monkeypatch.setattr(link_mod.subprocess, "run", fake_run)
        engine = link_mod.JunctionLinkEngine()
        engine.create("C:\\old", "D:\\new")

        assert captured["args"] == ["cmd", "/c", "mklink", "/J", "C:\\old", "D:\\new"]
        assert captured["kwargs"] == {"capture_output": True, "text": True}

    def test_kind_is_junction(self):
        assert link_mod.JunctionLinkEngine.kind == "junction"

    def test_create_raises_storops_error_on_nonzero_exit(self, monkeypatch):
        monkeypatch.setattr(
            link_mod.subprocess,
            "run",
            lambda args, capture_output=True, text=True: subprocess.CompletedProcess(
                args, 1, stdout="", stderr="Access is denied."
            ),
        )
        engine = link_mod.JunctionLinkEngine()
        with pytest.raises(StoropsError, match="Access is denied"):
            engine.create("C:\\old", "D:\\new")

    def test_create_succeeds_silently_on_zero_exit(self, monkeypatch):
        monkeypatch.setattr(
            link_mod.subprocess,
            "run",
            lambda args, capture_output=True, text=True: subprocess.CompletedProcess(args, 0, stdout="", stderr=""),
        )
        engine = link_mod.JunctionLinkEngine()
        engine.create("C:\\old", "D:\\new")  # must not raise


class TestJunctionLinkEngineVerify:
    def test_verify_true_when_listings_match(self, tmp_path):
        old = tmp_path / "old"
        target = tmp_path / "target"
        old.mkdir()
        target.mkdir()
        (old / "a.txt").write_text("x")
        (target / "a.txt").write_text("x")

        engine = link_mod.JunctionLinkEngine()
        assert engine.verify(str(old), str(target)) is True

    def test_verify_false_when_listings_differ(self, tmp_path):
        old = tmp_path / "old"
        target = tmp_path / "target"
        old.mkdir()
        target.mkdir()
        (old / "a.txt").write_text("x")
        (target / "b.txt").write_text("x")

        engine = link_mod.JunctionLinkEngine()
        assert engine.verify(str(old), str(target)) is False

    def test_verify_false_when_old_path_missing(self, tmp_path):
        target = tmp_path / "target"
        target.mkdir()
        engine = link_mod.JunctionLinkEngine()
        assert engine.verify(str(tmp_path / "nope"), str(target)) is False

    def test_verify_false_when_target_missing(self, tmp_path):
        old = tmp_path / "old"
        old.mkdir()
        engine = link_mod.JunctionLinkEngine()
        assert engine.verify(str(old), str(tmp_path / "nope")) is False
