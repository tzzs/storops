"""Windows platform adapters: WizTree-backed scan (with a native fallback),
disk-capacity lookup, robocopy-based copy, and mklink/J-based Junction
linking. See src/storops/platform/base.py for the Protocol contracts these
modules satisfy, and docs/plans/storops-v2-cross-platform-refactor.md
§2.11a/§2.13/§2.14 for the design rationale.
"""
