"""Risk classification and action recommendation. Turns an identify_path()
result into one of KEEP / DELETE / MOVE / CHECK and provides the guardrail
cleanup/migration execute code must call before touching anything.

Straight port of scripts/lib/Risk.psm1 -- logic and priority order are
copied verbatim (see docs/plans/storops-v2-cross-platform-refactor.md
§2.9: the safety model is the one part of this rewrite with no room for
"creative" changes).
"""
from __future__ import annotations

from storops.core.errors import CriticalPathError
from storops.core.models import PathIdentity, RecommendedAction

_RISK_ORDER = {"low": 0, "medium": 1, "high": 2, "critical": 3}


def risk_rank(risk: str) -> int:
    key = risk.lower()
    if key not in _RISK_ORDER:
        raise ValueError(f"StorOps: unknown risk level '{risk}' (expected low/medium/high/critical).")
    return _RISK_ORDER[key]


def within_limit(risk: str, max_risk: str) -> bool:
    """Is `risk` at or below `max_risk` in severity?"""
    return risk_rank(risk) <= risk_rank(max_risk)


def assert_not_critical(identity: PathIdentity) -> None:
    """Last-line-of-defense guardrail: execute-tier code calls this
    immediately before any delete/move/link operation, independent of
    whatever a plan file claims, so a stale or hand-edited plan can never
    be used to touch a critical-risk path.
    """
    if identity.cleanup_risk and risk_rank(identity.cleanup_risk) >= risk_rank("critical"):
        raise CriticalPathError(
            f"StorOps: refusing to operate on '{identity.path}' -- classified CRITICAL risk "
            f"({identity.category}). This tier is never eligible for automatic delete/move, "
            f"regardless of what a plan file says."
        )


def recommended_action(identity: PathIdentity) -> RecommendedAction:
    """Deterministic KEEP / DELETE / MOVE / CHECK recommendation. Never
    performs any action itself -- only labels a plan row.
    """
    if identity.category == "unknown":
        return RecommendedAction(
            action="CHECK",
            reason=(
                "No identification rule matched this path -- StorOps does not guess; "
                "review it yourself before doing anything."
            ),
        )

    if risk_rank(identity.cleanup_risk) >= risk_rank("critical"):
        return RecommendedAction(
            action="KEEP",
            reason=f"Classified CRITICAL risk ({identity.category}) -- never offered for cleanup or migration.",
        )

    if identity.deletable and risk_rank(identity.cleanup_risk) <= risk_rank("medium"):
        return RecommendedAction(
            action="DELETE",
            reason=(
                f"Identified as {identity.application}'s {identity.category}; safely "
                f"re-creatable/re-downloadable ({identity.cleanup_risk} risk)."
            ),
        )

    if identity.migratable:
        return RecommendedAction(
            action="MOVE",
            reason=(
                f"Identified as {identity.application}'s {identity.category}; large and "
                f"portable, but not disposable -- prefer relocating over deleting."
            ),
        )

    if not identity.deletable and not identity.migratable:
        return RecommendedAction(
            action="KEEP",
            reason=(
                f"Identified as {identity.application}'s {identity.category}; neither safe "
                f"to delete nor supported for migration."
            ),
        )

    return RecommendedAction(
        action="CHECK",
        reason="Identified, but does not cleanly fit an automatic recommendation -- review manually.",
    )
