from __future__ import annotations

import pytest

from storops.core import risk
from storops.core.errors import CriticalPathError
from storops.core.models import PathIdentity


def _identity(**overrides) -> PathIdentity:
    base = dict(
        path="/x",
        application="Thing",
        category="ai-model-cache",
        confidence=0.9,
        owner="user",
        purpose=None,
        deletable=False,
        migratable=False,
        migration_method=None,
        migration_hint=None,
        requires_app_closed=False,
        cleanup_risk="low",
        consequence=None,
        notes=None,
        matched_rule_id="rule-1",
        matched_pattern="*",
    )
    base.update(overrides)
    return PathIdentity(**base)


def test_risk_rank_orders_low_below_critical():
    assert risk.risk_rank("low") < risk.risk_rank("critical")


def test_within_limit():
    assert risk.within_limit("low", "medium") is True
    assert risk.within_limit("high", "medium") is False


def test_risk_rank_rejects_unknown_level():
    with pytest.raises(ValueError):
        risk.risk_rank("nonsense")


def test_assert_not_critical_raises_for_critical():
    identity = _identity(category="unknown", cleanup_risk="critical")
    with pytest.raises(CriticalPathError):
        risk.assert_not_critical(identity)


def test_assert_not_critical_passes_for_low():
    risk.assert_not_critical(_identity(cleanup_risk="low"))  # must not raise


def test_recommended_action_unknown_is_check():
    action = risk.recommended_action(_identity(category="unknown", cleanup_risk="critical"))
    assert action.action == "CHECK"


def test_recommended_action_critical_is_keep():
    action = risk.recommended_action(_identity(category="os-system", cleanup_risk="critical"))
    assert action.action == "KEEP"


def test_recommended_action_deletable_low_risk_is_delete():
    action = risk.recommended_action(_identity(deletable=True, cleanup_risk="low"))
    assert action.action == "DELETE"


def test_recommended_action_migratable_not_deletable_is_move():
    action = risk.recommended_action(_identity(deletable=False, migratable=True, cleanup_risk="high"))
    assert action.action == "MOVE"


def test_recommended_action_neither_is_keep():
    action = risk.recommended_action(_identity(deletable=False, migratable=False, cleanup_risk="high"))
    assert action.action == "KEEP"
