# StorOps identification rules

These YAML files are the deterministic knowledge base `src/storops/core/rules.py`
matches paths against. They exist so the agent never has to *guess* what a
path is from its name alone (see [`docs/DESIGN.md`](../docs/DESIGN.md) §3.3).

## Files

| File | Covers |
|---|---|
| `ai-models.yaml` | AI/ML model weights and inference-tool caches (LM Studio, Ollama, Hugging Face, ComfyUI/Stable Diffusion, PyTorch/CUDA) -- `path_patterns` are currently Windows-token-only |
| `applications.yaml` | Dev tooling (npm, pnpm, pip, uv, conda, Git, VS Code, JetBrains, Visual Studio, Docker, WSL) and general consumer apps (Steam, Chrome, Edge, Discord, Adobe) -- `path_patterns` are currently Windows-token-only |
| `caches.yaml` | Generic OS/browser/temp caches not owned by one specific application above -- `path_patterns` are currently Windows-token-only |
| `windows.yaml` | Windows system paths StorOps must never classify as safe to touch |
| `linux.yaml` | Linux system paths StorOps must never classify as safe to touch |
| `macos.yaml` | macOS system paths StorOps must never classify as safe to touch |

Extending `ai-models.yaml`/`applications.yaml`/`caches.yaml` with Linux/macOS
`path_patterns` for the same applications (e.g. LM Studio under
`%XDG_CACHE_HOME%` or `%CACHES%` instead of only `%LOCALAPPDATA%`) is
tracked follow-up work, not yet done -- see docs/DESIGN.md §4c.

## Rule schema

Each file is a YAML list of **flat** rule entries — deliberately shallow (one
list field, everything else a scalar) so `src/storops/core/rules.py`'s small,
purpose-built YAML-subset reader can parse it without pulling in an external
YAML module:

```yaml
- id: lmstudio-models                  # stable, unique, kebab-case
  application: LM Studio               # human-readable owner name
  category: ai-model-cache             # see "Categories" below
  path_patterns:                       # shell-style wildcard patterns, matched
    - "%USERPROFILE%\\.lmstudio\\models\\*"   # against the full normalized
    - "%USERPROFILE%\\.cache\\lm-studio\\*"   # path (case-insensitive)
  confidence: 0.95                     # 0.0-1.0, how sure this ID is
  owner: user                          # user | system | shared
  purpose: >
    One or two sentences: what this is and why it exists on disk.
  deletable: false                     # can StorOps ever offer to delete it?
  migratable: true                     # can it be relocated?
  migration_method: app-config         # app-config | junction | manual | none
  migration_config_hint: >
    Where/how to repoint the app (setting name, env var, config file).
  migration_requires_app_closed: true
  cleanup_risk: high                   # low | medium | high | critical
  cleanup_consequence: >
    What the user loses/must redo if this is deleted.
  notes: >
    Optional free-text guidance for the agent (edge cases, caveats).
```

Only `id`, `application`, `category`, `path_patterns`, and `cleanup_risk` are
required; everything else has safe defaults (`confidence: 0.5`,
`deletable: false`, `migratable: false`, `owner: user`).

### YAML subset supported

`src/storops/core/rules.py`'s reader is **not** a general-purpose YAML parser
— it only understands what these rule files actually use:

- a top-level block sequence (`- id: ...`) of flat mappings
- scalar values: quoted strings, bare strings, `true`/`false`, numbers
- one nested block sequence per item: `path_patterns:` followed by `  - "..."` lines
- folded block scalars (`field: >` followed by more-indented lines, joined
  with spaces) for long text like `purpose`/`notes`/`*_consequence`/`*_config_hint`
- `#` line comments

It does **not** support flow style (`{ }` / `[ ]`), multi-document files,
anchors/aliases, or literal block scalars (`|`). Keep new rules within this
subset.

### Path pattern variables

Patterns may use these tokens, expanded from the current environment at match
time by `expand_pattern_tokens()` in `src/storops/core/rules.py`
(case-sensitive as written; see docs/DESIGN.md §4c). Only the set matching
the *current*
platform actually expands -- a token from another platform's set is left
literal and simply never matches a real path, which is why `windows.yaml`/
`linux.yaml`/`macos.yaml` are all loaded unconditionally regardless of which
platform StorOps is running on.

| Platform | Tokens |
|---|---|
| Windows | `%USERPROFILE%`, `%LOCALAPPDATA%`, `%APPDATA%`, `%PROGRAMDATA%`, `%PROGRAMFILES%`, `%PROGRAMFILES(X86)%`, `%TEMP%`, `%SYSTEMROOT%` |
| Linux | `%HOME%`, `%XDG_CACHE_HOME%`, `%XDG_CONFIG_HOME%`, `%XDG_DATA_HOME%`, `%TMPDIR%` |
| macOS | `%HOME%`, `%CACHES%` (`~/Library/Caches`), `%APP_SUPPORT%` (`~/Library/Application Support`), `%XDG_CACHE_HOME%`, `%TMPDIR%` |

Patterns use shell-style wildcards (`*`, `?`, via `fnmatch`), matched
case-insensitively -- `_pattern_matches()` in `src/storops/core/rules.py`
also special-cases a trailing `%TOKEN%/sub/*` pattern to match the bare
`.../sub` directory itself, not just things strictly inside it, since a
plain `fnmatch` alone would otherwise silently miss the common "the thing
being scanned is exactly the directory the pattern describes" case (this is
the same false-negative the original PowerShell `-like` operator had).

### Categories (used for grouping/summaries)

`ai-model-cache`, `ai-model-weights`, `package-manager-cache`, `container-runtime`,
`vm-disk`, `dev-tool-data`, `ide-cache`, `browser-cache`, `application-data`,
`application-cache`, `os-temp`, `os-system`, `unknown`.

### Risk levels

`low` (temp files, disposable logs, safe app caches), `medium` (re-downloadable
caches with a real inconvenience cost — e.g. Hugging Face/npm/pip caches,
Docker unused layers), `high` (application data, dev environments, large
model files, WSL VHDX — real, hard-to-replace data or config), `critical`
(Windows, `Program Files`, unknown system paths, user documents — StorOps
never offers these for automatic deletion, full stop).

### Matching precedence

`identify_path()` in `src/storops/core/rules.py` evaluates `windows.yaml`,
`linux.yaml`, `macos.yaml` first (a
`critical` system match wins outright and short-circuits further matching),
then `ai-models.yaml`, then `applications.yaml`, then `caches.yaml`. Within a
file, first matching rule wins; more specific patterns should be listed
before broader ones. A path matching nothing is reported as `category:
unknown`, `confidence: 0`, `deletable: false` — StorOps never invents a
classification for the unknown case.
