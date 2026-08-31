"""Regression test for a real bug found via end-to-end manual testing of
`storops cleanup plan`: cleanup.plan()'s probe logic strips a rule
pattern's trailing "/*" to get a concrete directory to test/size (e.g.
"%HOME%/.npm/*" -> "$HOME/.npm"), then used to re-identify that bare
directory via a full rules.identify_path() search. That re-search can
never match the very pattern it came from, because fnmatch requires the
literal "/" immediately before the "*" to be present in the candidate,
and a bare directory path has no trailing separator -- so cleanup plans
were always empty for every deletable rule shaped this way (inherited
faithfully, without noticing, from the same -like semantics in the
original PowerShell version's Get-StorOpsProbePath/Get-StorOpsPathIdentity
pairing -- this was never a Python-specific regression).

Fixed by falling back to rules.identity_from_rule(rule, ...) instead of
trusting a from-scratch identify_path() re-search when it returns
"unknown" for a probe path (see core/cleanup.py and core/rules.py).
"""
from __future__ import annotations

import json
import textwrap
from pathlib import Path

from storops.core import cleanup, rules


class _FakeSizeBackend:
    name = "Fake"

    def __init__(self, size_bytes: int):
        self._size_bytes = size_bytes

    def path_size(self, path, *, admin=False):
        from storops.core.models import Entry

        return Entry(full_name=path, is_folder=True, size_bytes=self._size_bytes, allocated_bytes=self._size_bytes)

    def advice(self):
        return None


def test_cleanup_plan_finds_a_deletable_directory_probe(monkeypatch, tmp_path: Path):
    rules_dir = tmp_path / "rules"
    rules_dir.mkdir()
    home = tmp_path / "home"
    (home / ".npm").mkdir(parents=True)
    (home / ".npm" / "blob.bin").write_bytes(b"x" * 1024)

    (rules_dir / "caches.yaml").write_text(
        textwrap.dedent(
            """\
            - id: fake-npm-cache
              application: npm
              category: package-manager-cache
              path_patterns:
                - "%HOME%/.npm/*"
              deletable: true
              migratable: false
              cleanup_risk: low
            """
        ),
        encoding="utf-8",
    )

    monkeypatch.setattr(Path, "home", classmethod(lambda cls: home))
    monkeypatch.setattr(rules, "_default_rules_dir", lambda: rules_dir)
    monkeypatch.setattr("storops.platform.get_scan_backend", lambda: _FakeSizeBackend(1024))

    out_file = tmp_path / "plan.json"
    plan = cleanup.plan("low", out_file=str(out_file))

    assert len(plan.items) == 1
    item = plan.items[0]
    assert item.application == "npm"
    assert item.approved is True
    assert item.size_bytes == 1024

    on_disk = json.loads(out_file.read_text())
    assert len(on_disk["Items"]) == 1

    rules.load_rules(force=True)  # reset the module cache for later tests
