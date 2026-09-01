# Changelog

## [1.0.1](https://github.com/tzzs/storops/compare/storops-v1.0.0...storops-v1.0.1) (2026-09-01)


### Bug Fixes

* **ci:** gate SkillHub publish on the test workflow, drop dead trigger ([8547df2](https://github.com/tzzs/storops/commit/8547df2af7668493aae063439c8bcb88cb7a5c6f))
* **ci:** trigger SkillHub publish via workflow_run, not release ([60b8f5c](https://github.com/tzzs/storops/commit/60b8f5c7aa1d2c63b58e71b13a5229b608aaf8c1))
* **release:** let release-please own SKILL.md's version ([009c5d1](https://github.com/tzzs/storops/commit/009c5d12d11d1cd531b6e8d1b04df309eec6160f))

## [1.0.0](https://github.com/tzzs/storops/compare/storops-v0.2.0...storops-v1.0.0) (2026-09-01)


### ⚠ BREAKING CHANGES

* `scripts/*.ps1` entry points (scan.ps1, inspect.ps1, search.ps1, identify.ps1, cleanup-plan.ps1, cleanup-execute.ps1, migrate-plan.ps1, migrate-execute.ps1, verify.ps1) and scripts/lib/ no longer exist. Automation invoking them must switch to the `storops` CLI (`python -m storops <verb> ...`, or `storops <verb> ...` if pip-installed). Parameter names map 1:1 (`-Path` -> positional/`--path`, `-MaxRisk` -> `--max-risk`, `-Confirm` -> `--confirm`, `-AppClosed` -> `--app-closed`, etc.) and JSON output field names are unchanged (still PascalCase). Pin to a pre-removal tag if you need more time to migrate.

### Features

* **ci:** publish to SkillHub, unify cross-platform description ([1c48c48](https://github.com/tzzs/storops/commit/1c48c48822033f8672cf03e0283964e4339dd830))
* **compat:** wrap scripts/*.ps1 around the Python CLI, add CI, sync docs ([7e4f4b8](https://github.com/tzzs/storops/commit/7e4f4b8de7ba06bd3e3f64beba3734d56afbdc63))
* **platform:** add Linux/macOS posix/du/gdu backends + rule coverage ([5a6a9fe](https://github.com/tzzs/storops/commit/5a6a9fe2493f6be99ffb6b28d5d4b8730a04d9d4))
* **platform:** add Windows scan/copy/link adapters (WizTree + native fallback, robocopy, mklink /J) ([e0515ed](https://github.com/tzzs/storops/commit/e0515edcd7d545053edcfe02a3436d5d10f7145e))
* storops v2 -- cross-platform Python rewrite (Linux/macOS/Windows) ([075c9d6](https://github.com/tzzs/storops/commit/075c9d6a9a30ecd80ab3f6e0db489981fef2398e))
* **v2:** Python core layer -- models, rule engine, risk model, orchestration ([29590c9](https://github.com/tzzs/storops/commit/29590c919ba6b2b5a8c0550e2b64e249ef617226))
* **v2:** unified storops CLI (Phase 5) ([375449c](https://github.com/tzzs/storops/commit/375449c6dbdd38c5adbf9a91f58eb6be17e724d2))


### Bug Fixes

* BSD du -d alone drops file-level entries, breaking depth-limited scans ([87beb2e](https://github.com/tzzs/storops/commit/87beb2e1fa0c6d05f8889454af1f0f0c10dbf6ba))
* cleanup plan always returned empty (probe-path re-identification bug) ([3c3fc04](https://github.com/tzzs/storops/commit/3c3fc04a76262bb01b25869af80021ccf489d4aa))
* gdu backend parsed the wrong JSON shape entirely (verified against real gdu) ([9b3a8ab](https://github.com/tzzs/storops/commit/9b3a8ab0cfaac593a7f16ee4bce01186f7bd3278))
* macOS du usage-error crash and Windows %HOME% token/test failures in CI ([8fdb079](https://github.com/tzzs/storops/commit/8fdb0797b9ab8ada1fc238f942e11e4a1aed05cb))
* PythonBridge.psm1 was leaking stderr onto stdout via Write-Warning ([2cb0716](https://github.com/tzzs/storops/commit/2cb0716bfb048d5e3728a6fb807015460605f98d))
* rule patterns never matched the bare directory they describe ([e19e87e](https://github.com/tzzs/storops/commit/e19e87eaedd4f3bacb4061401a05ed07171868fd))
* test_path_size_finds_the_file assumed apparent size on BSD du too ([0ddbc90](https://github.com/tzzs/storops/commit/0ddbc909c53bbdf2f2d35a3aaf162a02e77b056d))
* three confirmed scan/rule-matching bugs ([65955de](https://github.com/tzzs/storops/commit/65955de00af218b404a2ec27f2fd1b75be056e58))
* work around PowerShell @() binder crash in scan backends ([3ebe7a7](https://github.com/tzzs/storops/commit/3ebe7a7701ce94f992cf4e5908b34c3769f97902))


### Code Refactoring

* remove PowerShell compat wrappers, storops CLI is now the only entry point ([d01271d](https://github.com/tzzs/storops/commit/d01271d23ba326eb5bdbccbd1ff7ada80d9e8589))

## [0.2.0](https://github.com/tzzs/storops/compare/storops-v0.1.0...storops-v0.2.0) (2026-08-31)


### Features

* add cross-platform scan-backend abstraction (Linux/macOS via gdu/du) ([f25d632](https://github.com/tzzs/storops/commit/f25d63261bc7a33d8ab833660c261c67318ca445))
* add SKILL.md agent behavior contract ([e4d2699](https://github.com/tzzs/storops/commit/e4d2699852fc01bc740ff60912980d2dfa1b4821))
* cross-platform scan-backend abstraction (Linux/macOS via gdu/du) ([f5d0c15](https://github.com/tzzs/storops/commit/f5d0c1509a5b079973b8da6f3756e16966d33e50))
* **engine:** add WizTree integration, rule engine, and risk logic ([f2615a0](https://github.com/tzzs/storops/commit/f2615a05024566f563a37965321e1a1ee20841e4))
* **rules:** add deterministic identification rule base ([69184dd](https://github.com/tzzs/storops/commit/69184ddf9131ec33acda6fe9646172390888edc7))
* **scripts:** add cleanup plan/execute (write-tier) capability ([c555756](https://github.com/tzzs/storops/commit/c555756a9a9b93c77c98a2a67eedc2a86748b73f))
* **scripts:** add migration plan/execute and verification capability ([fd03720](https://github.com/tzzs/storops/commit/fd03720edc5380d44796f00853fb81fab851e956))
* **scripts:** add read-tier capabilities (scan, inspect, search, identify) ([123ffe8](https://github.com/tzzs/storops/commit/123ffe879e52ef14250afd525a0d957a16bf846e))
* surface scan-backend fallback advice as structured JSON, not just a warning ([cff4e69](https://github.com/tzzs/storops/commit/cff4e69ed48227cf6566c3de8a5ad90791fb2054))


### Bug Fixes

* **skill:** quote SKILL.md description to fix YAML frontmatter parse error ([9339f99](https://github.com/tzzs/storops/commit/9339f996302e2eac396da9f2652e3ebe285354c7))
