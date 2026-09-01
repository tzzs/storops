"""Regression test for a real bug: cleanup.plan()'s probe dict was keyed on
the raw, un-normalized output of rules.expand_pattern_tokens(). Two
different deletable rules whose patterns are anchored on different
%TOKEN%s (e.g. "%TEMP%/*" vs "%USERPROFILE%/AppData/Local/Temp/*") can
expand to the exact same real directory but as differently-shaped raw
strings (extra "./" segments, mixed separators, etc.) -- which the dict
then treated as two distinct probes, double-counting that one directory's
size in the plan totals and emitting a duplicate CleanupItem for it.

Fixed by normalizing each probe through core.paths.resolve_path() before
using it as the dict key (see core/cleanup.py's `plan()`).
"""
from __future__ import annotations

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


def test_cleanup_plan_dedupes_probes_that_resolve_to_the_same_directory(monkeypatch, tmp_path):
    rules_dir = tmp_path / "rules"
    rules_dir.mkdir()
    home = tmp_path / "home"
    target = home / "Cache"
    target.mkdir(parents=True)
    (target / "blob.bin").write_bytes(b"x" * 1024)

    (rules_dir / "caches.yaml").write_text(
        textwrap.dedent(
            """\
            - id: fake-cache-a
              application: appA
              category: cache
              path_patterns:
                - "%HOME%/Cache/*"
              deletable: true
              migratable: false
              cleanup_risk: low
            - id: fake-cache-b
              application: appB
              category: cache
              path_patterns:
                - "%ALT_HOME%/Cache/*"
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

    real_tokens = rules._platform_tokens

    def fake_tokens():
        tokens = dict(real_tokens())
        # Points at the exact same directory as %HOME% above, just via a
        # differently-shaped raw string (redundant "/." segment) -- mirrors
        # a real "%TEMP%" vs "%USERPROFILE%\\AppData\\Local\\Temp" collision,
        # which only os.path.abspath()-style normalization can see through.
        tokens["%ALT_HOME%"] = str(home) + "/."
        return tokens

    monkeypatch.setattr(rules, "_platform_tokens", fake_tokens)

    out_file = tmp_path / "plan.json"
    plan = cleanup.plan("low", out_file=str(out_file))

    # Without the resolve_path() normalization, this would be 2 items and
    # total_reclaimable_bytes would double-count the one real directory.
    assert len(plan.items) == 1
    assert plan.items[0].application == "appA"
    assert plan.total_candidate_bytes == 1024
    assert plan.total_reclaimable_bytes == 1024

    rules.load_rules(force=True)  # reset the module cache for later tests
