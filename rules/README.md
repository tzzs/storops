# StorOps identification rules

These YAML files are the deterministic knowledge base `scripts/lib/Identify.psm1`
matches paths against. They exist so the agent never has to *guess* what a
path is from its name alone (see [`docs/DESIGN.md`](../docs/DESIGN.md) §3.3).

## Files

| File | Covers |
|---|---|
| `ai-models.yaml` | AI/ML model weights and inference-tool caches (LM Studio, Ollama, Hugging Face, ComfyUI/Stable Diffusion, PyTorch/CUDA) |
| `applications.yaml` | Dev tooling (npm, pnpm, pip, uv, conda, Git, VS Code, JetBrains, Visual Studio, Docker, WSL) and general consumer apps (Steam, Chrome, Edge, Discord, Adobe) |
| `caches.yaml` | Generic OS/browser/temp caches not owned by one specific application above |
| `windows.yaml` | System paths StorOps must never classify as safe to touch |

## Rule schema

Each file is a YAML list of rule entries:

```yaml
- id: lmstudio-models                  # stable, unique, kebab-case
  application: LM Studio               # human-readable owner name
  category: ai-model-cache             # see "Categories" below
  match:
    path_patterns:                     # PowerShell -like patterns, matched
      - "%USERPROFILE%\\.lmstudio\\models\\*"   # against the full normalized
      - "%USERPROFILE%\\.cache\\lm-studio\\*"   # path (case-insensitive)
  confidence: 0.95                     # 0.0-1.0, how sure this ID is
  owner: user                          # user | system | shared
  purpose: >
    One or two sentences: what this is and why it exists on disk.
  deletable: false                     # can StorOps ever offer to delete it?
  migratable: true                     # can it be relocated?
  migration:
    method: app-config                 # app-config | junction | manual | none
    config_hint: >
      Where/how to repoint the app (setting name, env var, config file).
    requires_app_closed: true
  cleanup:
    risk: high                         # low | medium | high | critical
    consequence: >
      What the user loses/must redo if this is deleted.
  notes: >
    Optional free-text guidance for the agent (edge cases, caveats).
```

Only `id`, `application`, `category`, `match`, and `cleanup.risk` are required;
everything else has safe defaults (`confidence: 0.5`, `deletable: false`,
`migratable: false`, `owner: user`).

### Path pattern variables

Patterns may use these tokens, expanded from the current environment at match
time (case-insensitive): `%USERPROFILE%`, `%LOCALAPPDATA%`, `%APPDATA%`,
`%PROGRAMDATA%`, `%PROGRAMFILES%`, `%PROGRAMFILES(X86)%`, `%TEMP%`,
`%SYSTEMROOT%`. Patterns use PowerShell `-like` wildcards (`*`, `?`).

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

`Identify.psm1` evaluates `windows.yaml` first (a `critical` system match wins
outright and short-circuits further matching), then `ai-models.yaml`, then
`applications.yaml`, then `caches.yaml`. Within a file, first matching rule
wins; more specific patterns should be listed before broader ones. A path
matching nothing is reported as `category: unknown`, `confidence: 0`,
`deletable: false` — StorOps never invents a classification for the unknown
case.
