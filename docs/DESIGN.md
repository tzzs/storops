# StorOps — Storage Operations Skill for AI Agents

> Design source of truth. This document is the original product/design brief for
> StorOps and should be kept in sync as the design evolves. Implementation lives
> under `rules/`, `scripts/`, and `SKILL.md` at the repo root.

## 1. 项目目标

我们要开发一个面向 AI Coding Agent 的 **Storage Operations Skill**，项目名称暂定：

> **StorOps**

Skill 名称：

> `storops`

一句话定位：

> **Storage Operations for AI Agents.**

目标不是重新实现一个 WizTree，也不是简单做一个"磁盘空间分析器"或"磁盘清理器"。

核心理念：

> **WizTree 负责"看见"，StorOps 负责"理解、规划和行动"。**

StorOps 应该让 Claude Code、Codex、OpenCode 等 Agent 能够安全地理解和管理本地存储空间。

---

## 2. 为什么要做 StorOps

目前已经存在一些相关项目，例如：

- `disk-space-analyzer-skill`
- `wiztree-mcp`
- `disk-cleaner`

这些项目已经解决了很多基础问题，因此**不要重复造轮子**。

尤其是：

- 不重新实现磁盘扫描器
- 不重新实现 WizTree
- 不重新实现 NTFS MFT 扫描
- 不把项目做成一个新的 GUI 磁盘分析软件

我们应该站在现有工具之上。

现有工具主要解决：

> "哪些文件/目录占用了空间？"

StorOps 要进一步解决：

> "这些空间是什么？为什么在那里？是否可以删除？是否应该迁移？迁移到哪里？怎么安全迁移？迁移后是否正常？"

因此 StorOps 的定位应该是：

```text
Discover
   ↓
Understand
   ↓
Diagnose
   ↓
Recommend
   ↓
Plan
   ↓
Execute
   ↓
Verify
```

---

## 3. 核心设计原则

### 3.1 Agent-first

StorOps 不是传统 GUI 工具。所有能力都应该围绕 Agent 使用设计。

Agent 应该能够自然地处理：

> "为什么我的 C 盘快满了？"
> "找出 C 盘最大的 20 个目录。"
> "哪些东西可以清理？"
> "我的 LM Studio 模型为什么占了这么多 C 盘空间？"
> "把可以迁移的 AI 模型迁移到 E 盘。"
> "帮我清理掉可以安全删除的缓存。"

### 3.2 Read-first

默认情况下，所有操作都应该是只读的。例如：`scan / inspect / search / identify / analyze / diagnose / recommend` 都可以自动执行。

任何修改用户文件系统的操作：`delete / move / rename / junction / symlink / configuration change` 必须经过明确的用户确认。

### 3.3 不要让 LLM 自己猜路径

Agent 不应该看到 `C:\Users\xxx\.cache` 就凭经验猜"这应该是 Hugging Face"。应该尽可能通过确定性规则、软件配置、环境变量、注册表或已知应用目录进行识别。

识别结果应该包含：`application / purpose / category / owner / size / location / confidence / actionability`，例如：

```json
{
  "path": "C:\\Users\\xxx\\.cache\\huggingface",
  "application": "Hugging Face",
  "category": "ai-model-cache",
  "size": 54.2,
  "unit": "GB",
  "confidence": 0.98,
  "deletable": true,
  "migratable": true
}
```

---

## 4. 技术定位

第一阶段主要针对 **Windows**，因为 WizTree 在 Windows / NTFS 环境下具有非常优秀的扫描性能。但架构不与 WizTree 强绑定。StorOps 将 WizTree 视为 **Windows storage discovery backend**，而不是整个 StorOps。

### 4a. 跨平台 scan backend 抽象

StorOps 通过 `scripts/lib/ScanBackend.psm1` 这一层调度器，把"扫一个目录、拿到它的直属子项大小"这件事和具体用什么工具做完全解耦：

```text
scripts/lib/
  ScanBackend.psm1          调度器：探测平台 + 可用工具，选中一个 backend
                            并原样重新导出它的三个标准函数
  backends/
    WizTree.psm1            Windows，基于 WizTree CLI（§5）
    Gdu.psm1                Linux/macOS 首选，基于 gdu（见下）
    Du.psm1                 Linux/macOS 兜底，基于系统自带 du
```

每个 backend 模块必须实现同一份契约（三个函数，签名和返回形状完全一致）：

- `Invoke-StorOpsScan -Path -MaxDepth -ExportFolders -ExportFiles -Filter -FilterExclude -Admin`
  返回一组标准化对象：`FullName, IsFolder, SizeBytes, AllocatedBytes, Modified, FileCount, FolderCount`
  （`AllocatedBytes` 在 Linux/macOS backend 上目前等于 `SizeBytes` —— ext4/APFS 没有 NTFS MFT
  那种统一暴露"逻辑大小 vs 实际占用块数"的简单途径，这是一个已知的、可接受的精度取舍）。
- `Get-StorOpsTopEntries -Path -Top -MaxDepth -Admin -IncludeFiles`（scan.ps1/inspect.ps1 的直接依赖）
- `Get-StorOpsPathSize -Path -Admin`（cleanup-plan.ps1/migrate-plan.ps1 给单个已知路径称重）

调度逻辑（`Get-StorOpsScanBackendName`）：

```text
Windows            -> WizTree
Linux/macOS + gdu   -> Gdu
Linux/macOS 无 gdu  -> Du（打印一次性能提示，但仍然可用）
```

所有入口脚本（`scan.ps1`/`inspect.ps1`/`search.ps1`/`cleanup-plan.ps1`/`migrate-plan.ps1`）只导入
`ScanBackend.psm1`，从不直接导入某个具体 backend —— 这样新增/更换一个平台的 backend 不需要碰任何
入口脚本。`identify.ps1`/`Identify.psm1`/`Risk.psm1` 完全不感知 backend，只消费上面这组标准化字段。

### 4b. 为什么是 gdu，而不是直接用 du

`du` 是逐文件 `stat()` 遍历，单线程，瓶颈是 I/O **延迟**而不是吞吐 —— 在 SSD/NVMe 上尤其浪费，因为
一次只发一个 syscall，队列深度打不满。WizTree 快是因为它绕过文件系统驱动直接读 NTFS MFT，这个技巧
在 ext4/APFS 上没有公开、稳定的等价物（ext4 可以用 `debugfs` 读裸块设备，但需要 root 且脆弱，
StorOps 不会这么做）。

能做到的、性价比最高的加速手段是**并行遍历**：[gdu](https://github.com/dundee/gdu)（Go，goroutine
并发扫描，内置 JSON 导出，单个跨平台静态二进制）是目前最接近"WizTree 替身"的选择 —— 检测优先级为：

1. `$env:STOROPS_GDU_PATH`（显式指定，呼应 `$env:STOROPS_WIZTREE_PATH` 的现有约定）
2. PATH 上的 `gdu`
3. 都没有 -> 回退到系统自带的 `du`（始终可用，但大目录树上明显更慢，打印一次警告提示安装 gdu）

`du` 分支需要同时兼容 GNU coreutils（`--max-depth`/`-b`）和 BSD/macOS（`-d`/`-k`）两套完全不同的
参数，`backends/Du.psm1` 在调用前探测 `du --version` 来决定用哪一套；两种情况都把深度限制原生传给
`du` 本身（而不是先全量扫描再在 PowerShell 里截断），避免"只要顶层几个目录的大小"却触发一次全盘遍历。

### 4c. 规则文件按平台拆分

`rules/windows.yaml`（已有）、`rules/linux.yaml`、`rules/macos.yaml` 各自维护该平台"绝不允许自动
清理/迁移"的关键系统路径短路规则，`Identify.psm1` 始终把三个文件都加载 —— 不匹配当前平台的 token
（如 Linux 上出现 `%SYSTEMROOT%`）不会展开，规则自然不命中，不需要按平台条件加载。`ai-models.yaml`
/`applications.yaml`/`caches.yaml` 目前的 `path_patterns` 仍以 Windows token 为主；补齐 Linux/macOS
下同一批应用（LM Studio、Ollama、Docker、npm/pip 等）的路径是后续需要单独投入的工作量，不在这次
抽象层改动范围内。

---

## 5. WizTree 集成方式

不要操作 WizTree GUI，不要使用 GUI automation / mouse click / screenshot / OCR。直接使用 WizTree CLI。

基本流程：

```text
StorOps → invoke WizTree CLI → export structured data → parse result → normalize → analyze
```

优先使用 WizTree 原生的 CLI / export 能力，充分利用其支持的 CSV export、file type information、percentage information、drive capacity、maximum depth、treemap/export capabilities。

同时注意控制导出数据量，不要在每次扫描时无脑导出整个磁盘的全部文件。优先：

```text
drive summary → top directories → targeted drill-down → targeted search
```

减少：扫描时间 / CSV 大小 / 内存占用 / Agent context/token 消耗。

---

## 6. 核心能力

### 6.1 Scan

扫描指定磁盘或目录（如 `scan C:`、`scan C:\Users`、`scan E:\AI`）。输出：total capacity / used / free / top directories / largest files / file type distribution。

### 6.2 Inspect

深入分析指定路径，支持逐层展开（如 `inspect C:\Users\xxx\AppData\Local`）。

### 6.3 Search

支持：`find files > 10GB`、`find *.gguf`、`find model files`、`find files older than 1 year`、`find directories named cache`。

### 6.4 Identify

这是 StorOps 与现有磁盘分析工具的重要区别。尝试识别：

- AI / Development: LM Studio, Ollama, Hugging Face, ComfyUI, Docker, WSL, npm, pnpm, yarn, pip, uv, conda, Python, Visual Studio, JetBrains, VS Code, Git
- General Applications: Steam, Chrome, Edge, Discord, Adobe, etc.

识别结果应该尽可能告诉 Agent：What is it? Who owns it? Why does it exist? Can it be deleted? Can it be moved? How should it be moved? What happens if it is deleted?

---

## 7. AI 模型和缓存

现代 AI 开发环境非常容易产生大量存储占用，例如：LM Studio models / Hugging Face cache / Ollama models / ComfyUI models / Stable Diffusion models / PyTorch cache / CUDA cache / npm cache / pip cache / uv cache / Docker images / WSL VHDX。

不能简单把它们全部分类成 `cache = safe delete`，而应该区分：`Delete / Move / Keep / Re-download required / Currently in use / Configuration required`。

例如：

```text
Hugging Face cache — 54 GB
Delete: Yes
Consequence: Models may need to be downloaded again.
Migration: Recommended.
Target: E:\AI\HuggingFace
```

---

## 8. Migration（核心差异化能力）

用户可能会说"C 盘的 LM Studio 模型太大了，帮我迁到 E 盘"。StorOps 应该能够：

```text
1. Identify application
2. Identify storage directory
3. Determine whether application is running
4. Recommend migration method
5. Ask user for confirmation
6. Stop application if necessary
7. Move data
8. Update application configuration
9. Verify data
10. Remove old data only after verification
```

---

## 9. Junction / Symlink

对不支持修改存储路径的软件，可以考虑用 Junction（Windows 优先 Junction 而非 symbolic link）把旧路径指向新位置。必须：确认目标路径、确认源路径、确认数据已经完整迁移、确认应用没有运行、创建后进行验证。

---

## 10. Cleanup 风险分级

- **LOW**：temporary files、known disposable logs、safe application cache
- **MEDIUM**：Hugging Face cache、npm/pip cache、browser cache、Docker unused layers（需要明确告诉用户删除后的后果）
- **HIGH**：application data、development environments、large model files、WSL virtual disks
- **CRITICAL**：Windows、System32、Program Files、unknown system files、user documents（默认禁止 Agent 自动删除）

---

## 11. Cleanup 必须采用 Action Plan

不要直接执行删除，而应该先生成 Cleanup Plan，列出每一项的 size / risk / consequence / action，汇总 total reclaimable，只有得到用户明确确认之后才能执行。

---

## 12. Verification

任何写操作都必须支持验证，例如迁移后核对 file count / total size / expected files / target accessible / source no longer contains original data / junction works。验证失败时不要自动删除原始数据。

---

## 13. 历史和趋势（第二阶段，架构预留）

允许保存 snapshot，Agent 可以回答"为什么我这个月 C 盘少了 200GB"之类的问题，通过对比 snapshot 得出各类别的增量。

---

## 14. 推荐的工具接口

- 第一阶段：`scan_drive`, `inspect_path`, `find_large_files`, `search_files`, `extension_summary`, `identify_path`, `analyze_storage`
- 第二阶段：`recommend_cleanup`, `recommend_migration`, `generate_action_plan`
- 第三阶段：`move_path`, `create_junction`, `delete_path`, `update_configuration`, `verify_operation`
- 第四阶段：`create_snapshot`, `compare_snapshots`, `storage_growth`

---

## 15. 权限模型

- **Read**（自动执行）：scan, inspect, search, identify, analyze
- **Plan**（自动执行，但不能修改文件）：recommend_cleanup, recommend_migration, generate_action_plan
- **Write**（必须用户确认）：move, delete, rename, junction, configuration

---

## 16. Skill 本身的职责

`SKILL.md` 不应该只是说明"调用 WizTree"，而应该定义 Agent 的行为规范，例如：

```text
When user asks why disk space is low:
    1. Scan the relevant drive.
    2. Find top-level consumers.
    3. Drill down into unusually large directories.
    4. Identify known applications/caches/models.
    5. Classify each result.
    6. Recommend actions.
    7. Never delete automatically.

When user asks to clean:
    1. Analyze first.
    2. Generate cleanup plan.
    3. Explain consequences.
    4. Ask for confirmation.
    5. Execute only approved actions.
    6. Verify.
```

Skill 应该尽量让 Agent **主动使用 StorOps**，而不是让用户必须知道具体工具名称。

---

## 17. 项目结构（MVP 简化版）

```text
storops/ (repo root)
├── SKILL.md
├── README.md
├── LICENSE
├── docs/
│   └── DESIGN.md
├── scripts/
│   ├── lib/
│   │   ├── WizTree.psm1
│   │   ├── Identify.psm1
│   │   └── Risk.psm1
│   ├── scan.ps1
│   ├── inspect.ps1
│   ├── search.ps1
│   ├── identify.ps1
│   ├── cleanup-plan.ps1
│   ├── cleanup-execute.ps1
│   ├── migrate-plan.ps1
│   ├── migrate-execute.ps1
│   └── verify.ps1
├── rules/
│   ├── applications.yaml
│   ├── caches.yaml
│   ├── ai-models.yaml
│   └── windows.yaml
└── tests/
```

不要为了架构完整而过早复杂化；MCP server / 完整跨平台后端属于后续阶段。

---

## 18. 与现有项目的关系

StorOps **不是** `disk-space-analyzer-skill` 的 clone，也不是 `wiztree-mcp` 的 fork，也不是 `disk-cleaner` 的 clone。应该复用它们已经验证过的思路（WizTree CLI、CSV export、targeted scanning、structured analysis、risk classification），然后把价值集中在：Application Identification / AI Model Awareness / Migration / Action Planning / Verification / Agent-native Workflow。

---

## 19. MVP 范围

- **A. WizTree integration**：find WizTree、invoke CLI、export structured data、parse data
- **B. Disk analysis**：scan drive、top directories、largest files、file extensions、drill-down
- **C. Application identification**：至少支持 LM Studio, Ollama, Hugging Face, ComfyUI, Docker, WSL, npm, pnpm, pip, uv
- **D. Recommendations**：KEEP / DELETE / MOVE / CHECK，并说明 risk / reason / consequence / recommended destination
- **E. Safety**：所有修改操作 confirmation required
- **F. Verification**：迁移和清理完成后必须验证

## 20. MVP 不应该实现的东西

GUI、自己实现磁盘扫描器/MFT scanner、自动后台监控、自动定时清理、跨平台完整支持、自动删除未知文件、复杂数据库、云端服务。

---

## 21. 典型用户体验

### 场景一：C 盘快满

```text
StorOps scanning C:...
C: 930 GB  Used: 891 GB  Free: 39 GB

Largest consumers:
LM Studio models       87 GB
Hugging Face cache     54 GB
WSL VHDX               48 GB
Docker                 31 GB
Windows                28 GB
Downloads              21 GB
```

```text
LM Studio models — 87 GB
Identified: LM Studio
Recommended: MOVE → E:\AI\LMStudio\Models
Risk: LOW
Reason: Model files are large and portable.
```

### 场景二：迁移 LM Studio

```text
Migration Plan
Source: C:\Users\xxx\.lmstudio\models   Size: 87.2 GB
Target: E:\AI\LMStudio\models
Method: Application-supported path change
Steps:
1. Close LM Studio
2. Move models
3. Update model directory
4. Verify models
5. Remove old files
Proceed?
```

### 场景三：清理缓存

```text
StorOps found 37.8 GB of low-risk cleanup candidates.

LOW RISK
Temp files              8.4 GB
npm cache               4.1 GB
pip cache               2.8 GB

MEDIUM RISK
Hugging Face cache     22.5 GB
Consequence: Models may need to be downloaded again.

I will only clean LOW RISK items unless you approve the Hugging Face cache separately.
Proceed with 15.3 GB cleanup?
```

---

## 22. Agent 行为原则

1. 分析优先，执行其次。
2. 默认只读。
3. 不要猜测文件用途。
4. 不要因为名字叫 cache 就认为可以删除。
5. 不要删除未知文件。
6. 不要自动修改系统目录。
7. 任何破坏性操作必须获得明确确认。
8. 迁移完成后必须验证。
9. 如果应用正在运行，不要直接移动其数据。
10. 对于可能重新下载的大型 AI 模型，必须明确告知用户后果。
11. 优先迁移，而不是删除用户有价值的数据。
12. 尽可能使用应用官方支持的路径配置，而不是强制使用 Junction。
13. 只有在应用不支持路径配置时，才考虑 Junction。
14. 不要让 WizTree 成为整个架构的强依赖。

---

## 23. 成功标准

不是"我们能扫描 C 盘"，而是：用户问"为什么 C 盘满了"，Agent 可以从扫描结果一路追踪到具体的软件/缓存/模型，并给出可靠、可执行、安全的解决方案，整个过程都由 Agent 驱动：

```text
C:\... → 87 GB → LM Studio → AI model storage → migratable → E:\AI\LMStudio
       → migration plan → user confirmation → move → verify
```

---

## 24. 最终产品定位

- 项目名称：**StorOps**
- Skill：`storops`
- Tagline：**Storage Operations for AI Agents.**
- 核心理念：**See where your storage goes. Understand why. Move what matters. Clean what doesn't.**

不要把它定位成 Disk Cleaner 或 Disk Analyzer，而应该定位成 **Storage Operations layer for AI Agents**：

```text
                    StorOps
                       │
          ┌────────────┼────────────┐
          │            │            │
       Discover     Understand    Diagnose
          │            │            │
       WizTree      Identify      Analyze
          │            │            │
          └────────────┼────────────┘
                       │
                    Plan
                       │
             ┌─────────┴─────────┐
             │                   │
          Migrate              Clean
             │                   │
             └─────────┬─────────┘
                       │
                    Verify
```

第一阶段重点不是"做更多功能"，而是把这条 Agent workflow 做正确：优先复用 WizTree，把开发精力放在**识别、智能判断、迁移规划、安全执行和验证**上。

---

## 附：WizTree CLI 参考（用于实现 `scripts/lib/WizTree.psm1`）

可执行文件：`WizTree64.exe`（或 `WizTree.exe`，32 位）。命令行导出用法：

```text
WizTree64.exe "<drive-or-folder>" /export="<output.csv>" [options]
```

关键参数：

| 参数 | 说明 |
|---|---|
| `/admin=0|1` | 以管理员权限运行以启用 MFT 直读扫描（更快、更完整） |
| `/exportfolders=0|1` | 是否导出目录条目 |
| `/exportfiles=0|1` | 是否导出文件条目 |
| `/exportmaxdepth=n` | 限制导出的目录深度，`0` 为不限制 |
| `/sortby=n` | `0`=name, `1`=size desc, `2`=allocated desc, `3`=date desc |
| `/filter="spec"` | 只包含匹配的文件（如 `*.gguf`） |
| `/filterexclude="spec"` | 排除匹配的文件 |
| `/filterfullpath=0|1` | 过滤时是否匹配完整路径 |
| `/exportallsizes=1` | 导出目录“自身”大小（不含子目录） |
| `/exportpercentofparent=1` | 导出相对父目录的百分比 |
| `/exportdrivecapacity=1` | 导出盘符总容量 |

CSV 导出列：`File Name, Size, Allocated, Modified, Attributes, Files, Folders`。目录名以 `\` 结尾；`Size`/`Allocated` 对目录是递归总和；`Attributes` 为位掩码（1=只读, 2=隐藏, 4=系统, 32=归档, 2048=压缩）。

StorOps 通过组合 `/exportfolders=1 /exportfiles=0 /exportmaxdepth=N /sortby=1` 来实现"drive summary → top directories → targeted drill-down"的分层、受控扫描，避免一次性导出整盘全部文件。
