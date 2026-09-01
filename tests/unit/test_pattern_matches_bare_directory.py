"""Regression test for a fundamental rule-matching bug found via end-to-end
testing of `storops migrate plan`: a "%TOKEN%/sub/*"-shaped pattern never
matched the bare directory it describes (only things strictly inside it),
which broke migrating exactly the top-level folders README.md/SKILL.md's
own examples show (e.g. the bare ".lmstudio/models" directory itself).
See core/rules.py's _pattern_matches() docstring for the full explanation.
"""
from __future__ import annotations

import textwrap
from pathlib import Path

from storops.core import rules


def _rules_dir_with(tmp_path: Path, pattern: str) -> Path:
    rules_dir = tmp_path / "rules"
    rules_dir.mkdir()
    (rules_dir / "ai-models.yaml").write_text(
        textwrap.dedent(
            f"""\
            - id: fake-app
              application: FakeApp
              category: ai-model-weights
              path_patterns:
                - "{pattern}"
              migratable: true
              migration_method: app-config
              cleanup_risk: high
            """
        ),
        encoding="utf-8",
    )
    return rules_dir


def test_bare_directory_named_by_pattern_prefix_matches(tmp_path, monkeypatch):
    home = tmp_path / "home"
    models_dir = home / ".fakeapp" / "models"
    models_dir.mkdir(parents=True)
    rules_dir = _rules_dir_with(tmp_path, "%HOME%/.fakeapp/models/*")

    monkeypatch.setattr(Path, "home", classmethod(lambda cls: home))

    # The bare "models" directory itself -- previously always "unknown".
    identity = rules.identify_path(models_dir, rules_dir=rules_dir)
    assert identity.application == "FakeApp"
    assert identity.migratable is True

    # A file/subfolder strictly inside it must still match, as before.
    child = models_dir / "weights.bin"
    identity_child = rules.identify_path(child, rules_dir=rules_dir)
    assert identity_child.application == "FakeApp"

    # A sibling directory that merely shares the prefix textually must NOT
    # match -- this fix must not turn the matcher into a naive prefix check.
    sibling = home / ".fakeapp" / "models-backup"
    identity_sibling = rules.identify_path(sibling, rules_dir=rules_dir)
    assert identity_sibling.category == "unknown"

    rules.load_rules(force=True)
