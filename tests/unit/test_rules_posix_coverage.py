"""Confirms the Linux/macOS path_patterns added to rules/ai-models.yaml,
rules/applications.yaml, and rules/caches.yaml per docs/plans/
storops-v2-cross-platform-refactor.md §2.15 actually match synthetic
Linux-shaped and macOS-shaped paths -- before this change, identify_path()
returned category "unknown" for all of these since the rule base was
Windows-token-only.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from storops.core import rules


@pytest.fixture(autouse=True)
def _reset_rule_cache():
    # core/rules.py caches load_rules() at module scope; force a reload so
    # monkeypatched HOME/XDG env vars are picked up by _platform_tokens(),
    # and reset back to the real environment afterwards for later tests.
    yield
    rules.load_rules(force=True)


def _force_linux(monkeypatch):
    monkeypatch.setattr(rules._platform, "system", lambda: "Linux")


def _force_darwin(monkeypatch):
    monkeypatch.setattr(rules._platform, "system", lambda: "Darwin")


class TestLinuxPatterns:
    def test_ollama_models(self, tmp_path, monkeypatch):
        _force_linux(monkeypatch)
        home = tmp_path / "home" / "user"
        monkeypatch.setattr(Path, "home", lambda: home)
        target = home / ".ollama" / "models" / "foo"
        target.parent.mkdir(parents=True)
        target.touch()

        identity = rules.identify_path(target)
        assert identity.application == "Ollama"
        assert identity.category == "ai-model-weights"
        assert identity.matched_rule_id == "ollama-models"

    def test_lmstudio_models(self, tmp_path, monkeypatch):
        _force_linux(monkeypatch)
        home = tmp_path / "home" / "user"
        monkeypatch.setattr(Path, "home", lambda: home)
        target = home / ".lmstudio" / "models" / "model.gguf"
        target.parent.mkdir(parents=True)
        target.touch()

        identity = rules.identify_path(target)
        assert identity.application == "LM Studio"
        assert identity.matched_rule_id == "lmstudio-models"

    def test_huggingface_cache(self, tmp_path, monkeypatch):
        _force_linux(monkeypatch)
        home = tmp_path / "home" / "user"
        monkeypatch.setattr(Path, "home", lambda: home)
        target = home / ".cache" / "huggingface" / "hub" / "model"
        target.parent.mkdir(parents=True)
        target.touch()

        identity = rules.identify_path(target)
        assert identity.application == "Hugging Face"
        assert identity.matched_rule_id == "huggingface-cache"

    def test_npm_cache(self, tmp_path, monkeypatch):
        _force_linux(monkeypatch)
        home = tmp_path / "home" / "user"
        monkeypatch.setattr(Path, "home", lambda: home)
        target = home / ".npm" / "some-package"
        target.parent.mkdir(parents=True)
        target.touch()

        identity = rules.identify_path(target)
        assert identity.application == "npm"
        assert identity.matched_rule_id == "npm-cache"

    def test_downloads_folder(self, tmp_path, monkeypatch):
        _force_linux(monkeypatch)
        home = tmp_path / "home" / "user"
        monkeypatch.setattr(Path, "home", lambda: home)
        target = home / "Downloads" / "installer.AppImage"
        target.parent.mkdir(parents=True)
        target.touch()

        identity = rules.identify_path(target)
        assert identity.matched_rule_id == "downloads-folder"

    def test_before_this_change_shaped_path_was_unknown_sanity_check(self, tmp_path, monkeypatch):
        # Sanity check that an unrelated Linux-shaped path is still
        # correctly reported unknown/critical -- i.e. the new patterns are
        # specific, not a blanket catch-all.
        _force_linux(monkeypatch)
        home = tmp_path / "home" / "user"
        monkeypatch.setattr(Path, "home", lambda: home)
        target = home / "some-random-project" / "notes.txt"
        target.parent.mkdir(parents=True)
        target.touch()

        identity = rules.identify_path(target)
        assert identity.category == "unknown"
        assert identity.cleanup_risk == "critical"


class TestMacosPatterns:
    def test_docker_desktop_macos_vm_disk(self, tmp_path, monkeypatch):
        _force_darwin(monkeypatch)
        home = tmp_path / "Users" / "user"
        monkeypatch.setattr(Path, "home", lambda: home)
        target = (
            home
            / "Library"
            / "Containers"
            / "com.docker.docker"
            / "Data"
            / "vms"
            / "0"
            / "data.raw"
        )
        target.parent.mkdir(parents=True)
        target.touch()

        identity = rules.identify_path(target)
        assert identity.application == "Docker Desktop"
        assert identity.matched_rule_id == "docker-desktop-macos-vm-disk"

    def test_adobe_media_cache(self, tmp_path, monkeypatch):
        _force_darwin(monkeypatch)
        home = tmp_path / "Users" / "user"
        monkeypatch.setattr(Path, "home", lambda: home)
        target = (
            home
            / "Library"
            / "Application Support"
            / "Adobe"
            / "Common"
            / "Media Cache Files"
            / "clip.cfa"
        )
        target.parent.mkdir(parents=True)
        target.touch()

        identity = rules.identify_path(target)
        assert identity.application == "Adobe (Creative Cloud apps)"
        assert identity.matched_rule_id == "adobe-media-cache"

    def test_yarn_cache(self, tmp_path, monkeypatch):
        _force_darwin(monkeypatch)
        home = tmp_path / "Users" / "user"
        monkeypatch.setattr(Path, "home", lambda: home)
        target = home / "Library" / "Caches" / "Yarn" / "v6" / "pkg"
        target.parent.mkdir(parents=True)
        target.touch()

        identity = rules.identify_path(target)
        assert identity.application == "Yarn"
        assert identity.matched_rule_id == "yarn-cache"
