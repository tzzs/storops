"""Unit tests for the rule engine (core/rules.py). Mirrors the assertions
in tests/smoke.ps1's "Rule loading" / "Path identification" sections, but
runs on any platform (this file itself is platform-agnostic; platform-
specific path assertions live in tests/platform/).
"""
from __future__ import annotations

import textwrap
from pathlib import Path

import pytest

from storops.core import rules


def test_loads_real_rule_files_from_repo():
    loaded = rules.load_rules(force=True)
    assert len(loaded) >= 20

    lmstudio = next(r for r in loaded if r.id == "lmstudio-models")
    assert lmstudio.application == "LM Studio"
    assert lmstudio.category == "ai-model-weights"
    assert lmstudio.cleanup_risk == "high"
    assert len(lmstudio.path_patterns) >= 2
    assert "GGUF" in (lmstudio.purpose or "")


def test_unmatched_path_is_unknown_and_critical():
    identity = rules.identify_path("/definitely/not/a/real/known/app/StorOpsTestOnly")
    assert identity.category == "unknown"
    assert identity.cleanup_risk == "critical"
    assert identity.deletable is False
    assert identity.matched_rule_id is None


def test_linux_system_path_matches_critical(monkeypatch, tmp_path):
    # /etc is covered by rules/linux.yaml regardless of the running
    # platform in CI -- this test only asserts the *pattern* matches on
    # Linux; on non-Linux runners it is skipped by test collection via
    # tests/platform/ instead. Kept here as a smoke check when running on
    # a real Linux CI runner.
    import sys

    if sys.platform != "linux":
        pytest.skip("Linux-specific critical-path rule")
    identity = rules.identify_path("/etc/passwd")
    assert identity.category == "os-system"
    assert identity.cleanup_risk == "critical"


def test_custom_rules_dir_folded_block_scalar_and_list(tmp_path: Path):
    rules_dir = tmp_path / "rules"
    rules_dir.mkdir()
    (rules_dir / "caches.yaml").write_text(
        textwrap.dedent(
            """\
            # comment line, ignored
            - id: fake-cache
              application: FakeApp
              category: application-cache
              path_patterns:
                - "%HOME%/.fakeapp/cache/*"
                - "%HOME%/.cache/fakeapp/*"
              confidence: 0.77
              deletable: true
              migratable: false
              cleanup_risk: low
              purpose: >
                A folded block scalar
                spanning two lines.
            """
        ),
        encoding="utf-8",
    )

    loaded = rules.load_rules(rules_dir=rules_dir, force=True)
    assert len(loaded) == 1
    rule = loaded[0]
    assert rule.id == "fake-cache"
    assert rule.confidence == pytest.approx(0.77)
    assert rule.deletable is True
    assert rule.path_patterns == ("%HOME%/.fakeapp/cache/*", "%HOME%/.cache/fakeapp/*")
    assert rule.purpose == "A folded block scalar spanning two lines."

    # Reset the module-level cache so later tests reload the real repo rules.
    rules.load_rules(force=True)


def test_separator_insensitive_matching(tmp_path: Path, monkeypatch):
    rules_dir = tmp_path / "rules"
    rules_dir.mkdir()
    (rules_dir / "applications.yaml").write_text(
        '- id: comfy\n'
        '  application: ComfyUI\n'
        '  category: ai-model-weights\n'
        '  path_patterns:\n'
        '    - "*\\\\ComfyUI\\\\models\\\\*"\n'
        '  cleanup_risk: high\n',
        encoding="utf-8",
    )
    target = tmp_path / "home" / "user" / "ComfyUI" / "models" / "checkpoint.safetensors"
    target.parent.mkdir(parents=True)
    target.write_text("x")

    identity = rules.identify_path(target, rules_dir=rules_dir)
    assert identity.application == "ComfyUI"
    rules.load_rules(force=True)
