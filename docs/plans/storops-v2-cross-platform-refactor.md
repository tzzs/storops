# StorOps v2 —— 跨平台重构：审计报告 + 架构方案

> 状态：**规划文档（Phase 1 审计 + Phase 2 架构设计）**，尚未开始代码迁移。
> 范围：`tzzs/storops`（本仓库，`origin` 已指向 `https://github.com/tzzs/storops.git`）。
> 原始需求见对话记录；本文档是该需求在"先审计、再设计、不机械翻译"原则下，
> 基于仓库真实代码得出的结论,补充/修正了原始 Prompt 中与实际代码冲突的假设。
> 优先级（按原始需求约定）：**实际代码 > 现有用户行为 > 跨平台可靠性 > Agent 可用性 > Prompt 示例结构**。

---

## 0. 一句话结论

当前仓库**不是**"能跑但设计混乱"的 PowerShell 脚本堆——它已经有一层相当克制、经过深思熟虑的
**平台抽象**（`scripts/lib/ScanBackend.psm1` 调度器 + `backends/{WizTree,Gdu,Du}.psm1` 三个等契约实现），
以及一套完整独立于扫描后端的**安全模型**（Read/Plan/Write 三层 + `Assert-StorOpsNotCritical` 硬编码兜底 +
强制 `-Confirm` + 迁移后强制 `verify.ps1`）。这两块是这个项目**真正的资产**，重写时必须原样保留其行为契约，
只是把实现语言换成 Python、把调度器从 PowerShell module 换成 Python 抽象基类。

真正需要重构的，是原始 Prompt 猜对的部分：**没有统一 CLI**（9 个互相独立的 `.ps1` 入口，Agent 要知道文件名和
参数名才能调用）、**Windows 强依赖 WizTree 这个第三方 GUI 软件的 CLI**（无 WizTree = Windows 上完全不能扫描，
没有任何原生 fallback，这一点上 Windows 反而比 Linux/macOS 更脆弱）、**迁移执行只支持 Windows**
（`migrate-execute.ps1` 硬编码调用 `robocopy.exe`，在非 Windows 平台上无法运行——这是 SKILL.md 承诺的能力
在 Linux/macOS 上事实缺失）、**没有统一错误模型/退出码**、**没有跨平台 CI**、**规则库对 Linux/macOS 的应用识别
覆盖基本为零**。

**补充说明（本轮已用真实 PowerShell 7.4.6 在 Linux 上做了现场验证，见 §1.6）**：上一版文档里"路径拼接 bug"的
猜测（原 §1.6.4）经实测**证伪**——`Join-Path` 在 PowerShell 里对内嵌反斜杠的处理比想象中宽容，这条不是真问题
（更正见新 §1.6.7）。但验证过程中意外揪出了一个**严重得多、且真实存在**的问题：`scripts/lib/backends/{Du,Gdu,
WizTree}.psm1` 三个后端模块统一使用的 `return @($someList)` 写法，在 PowerShell 7.4.6 上对 `List[object]`
会稳定触发引擎自身 `PSEnumerableBinder` 的绑定异常（`Argument types do not match`）——**已经用 `scan.ps1`
端到端实测复现**：在 Linux 上，通过 README 官方推荐的 `pwsh scripts/scan.ps1 -Path ...` 调用方式，扫描功能
目前直接崩溃退出，不是"能用但慢"，是完全不可用。详见新 §1.6.1（严重级最高，已重新编号）。

---

## 1. Phase 1 —— 现状审计

### 1.1 仓库结构（全量文件清单）

```
storops/  (32 个文件，不含 .git)
├── .claude-plugin/
│   ├── marketplace.json         Claude Code 插件市场清单
│   └── plugin.json              插件元数据（name/version/repo/license）
├── .github/workflows/
│   └── release-please.yml       仅做版本号/CHANGELOG 自动化，不含任何测试 job
├── docs/
│   └── DESIGN.md                产品设计源文档（22KB，中文，权威，随实现同步）
├── rules/                       声明式识别规则（六个 YAML 文件，1039 行）
│   ├── README.md                规则 schema 说明
│   ├── ai-models.yaml           AI 模型/推理工具（LM Studio/Ollama/HF/ComfyUI/PyTorch..）
│   ├── applications.yaml        开发工具 + 通用应用（npm/pip/Docker/WSL/Steam/Chrome..）
│   ├── caches.yaml              通用 OS/浏览器/临时缓存
│   ├── windows.yaml             Windows 关键系统路径（critical 短路规则）
│   ├── linux.yaml               Linux 关键系统路径
│   └── macos.yaml               macOS 关键系统路径
├── scripts/
│   ├── scan.ps1                 [Read]  扫描磁盘/目录，Top N 直属子项
│   ├── inspect.ps1              [Read]  深入某个目录一层
│   ├── search.ps1               [Read]  按名称/大小/时间搜索
│   ├── identify.ps1             [Read]  单路径身份识别
│   ├── cleanup-plan.ps1         [Plan]  生成清理计划 JSON（只读）
│   ├── cleanup-execute.ps1      [Write] 执行清理计划（需 -Confirm）
│   ├── migrate-plan.ps1         [Plan]  生成迁移计划 JSON（只读）
│   ├── migrate-execute.ps1      [Write] 执行迁移（需 -Confirm，可能需 -AppClosed）
│   ├── verify.ps1               [Read]  复核迁移结果
│   └── lib/
│       ├── Common.psm1          平台探测/路径规范化/字节格式化/工作目录/容量查询
│       ├── ScanBackend.psm1     扫描后端调度器（本仓库最重要的抽象层）
│       ├── Identify.psm1        规则加载 + YAML 子集 reader + 路径匹配
│       ├── Risk.psm1            风险分级 + KEEP/DELETE/MOVE/CHECK 决策
│       └── backends/
│           ├── WizTree.psm1     Windows：包装 WizTree64.exe CLI + 解析 CSV
│           ├── Gdu.psm1         Linux/macOS 首选：包装 gdu 二进制 + 解析 JSON
│           └── Du.psm1          Linux/macOS 兜底：包装系统 du（GNU/BSD 双方言）
├── tests/
│   └── smoke.ps1                零依赖冒烟测试（规则加载/识别/风险引擎），仅 Windows 断言
├── SKILL.md                     Agent 行为契约（13 条 non-negotiable rules + 3 个工作流）
├── README.md / README.zh-CN.md
├── LICENSE (MIT)
└── release-please-config.json / .release-please-manifest.json
```

无 `pyproject.toml`、无任何 `.py` 文件、无 `package.json`——**100% PowerShell + YAML**，这一点与 Prompt 假设一致。

### 1.2 现有能力清单（真实功能面，作为迁移基线）

| 能力 | 入口脚本 | 层级 | 依赖的后端能力 |
|---|---|---|---|
| 扫描磁盘/目录 Top N | `scan.ps1` | Read | `Get-StorOpsTopEntries` + `Get-StorOpsFreeSpaceInfo` |
| 深入某目录一层 | `inspect.ps1` | Read | `Get-StorOpsTopEntries` |
| 名称/大小/时间搜索 | `search.ps1` | Read | `Invoke-StorOpsScan` |
| 单路径身份识别 | `identify.ps1` | Read | 仅规则引擎，不碰后端 |
| 生成清理计划 | `cleanup-plan.ps1` | Plan | `Get-StorOpsPathSize` + 规则引擎 |
| 执行清理 | `cleanup-execute.ps1` | Write | `Remove-Item`（PowerShell 原生，天然跨平台） |
| 生成迁移计划 | `migrate-plan.ps1` | Plan | `Get-StorOpsPathSize` + 规则引擎 + `Get-Process`（best-effort） |
| 执行迁移 | `migrate-execute.ps1` | Write | **`robocopy.exe`（Windows-only硬依赖）** + `New-Item -ItemType Junction`（Windows-only） |
| 复核迁移结果 | `verify.ps1` | Read | `Get-ChildItem -Recurse`（PowerShell 原生） + Junction 检测（`LinkType`，Windows-only 语义） |

这 9 个能力、其参数形状、`-Json` 输出字段名、Read/Plan/Write 三层的确认语义，**都是必须原样保留的现有用户行为**
（SKILL.md 的 13 条规则和三个工作流全部是围绕这套形状写的，Agent 的行为契约不应因语言迁移而改变）。

### 1.3 当前实际架构（不是 Prompt 假设的"Skill → PowerShell → OS command"那么扁平）

```
Skill (SKILL.md)
   │  agent 读 SKILL.md 决定何时调用哪个脚本
   ▼
scripts/*.ps1  (9 个独立入口，无统一 CLI 外壳)
   │  各自 Import-Module lib/{Common,ScanBackend,Identify,Risk}.psm1
   ▼
scripts/lib/*.psm1  (业务逻辑：规则匹配、风险分级、计划生成、验证)
   │  唯一依赖 ScanBackend.psm1 的稳定契约（3 个函数签名+返回形状），从不直接 import 具体 backend
   ▼
scripts/lib/ScanBackend.psm1  (调度器：探测平台 + 可用工具 → 选中一个 backend 并原样重新导出)
   │
   ├─ Windows            → backends/WizTree.psm1 → WizTree64.exe CLI（第三方 GUI 软件的 CLI 部分）
   ├─ Linux/macOS + gdu   → backends/Gdu.psm1     → gdu 二进制（Go，静态，并行遍历）
   └─ Linux/macOS 无 gdu  → backends/Du.psm1      → 系统自带 du（GNU/BSD 双方言探测）
```

这已经就是 Prompt 第三节要求的目标架构（`Skill → CLI → Core → Platform Abstraction → OS`）的雏形，只是：
- "CLI" 这一层没有真正存在（9 个脚本 = 9 个入口，不是一个 `storops <verb>` 分发器）；
- "Platform Abstraction" 只覆盖了"扫描"这一个能力域，没有覆盖迁移执行（robocopy/Junction）、
  权限判断（`Test-StorOpsIsAdmin`是有的，但没有更细的"这个平台如何创建符号链接/如何检测进程占用"抽象）；
- 语言是 PowerShell 而非 Python，这带来两个独立问题（见 §1.5）。

### 1.4 值得原样保留的设计（重写时的"契约",不是"实现细节"）

1. **三层安全模型**（Read 自由执行 / Plan 只读产出计划文件 / Write 需要显式确认）—— `SKILL.md` 第 1、2、6 条,
   `README.md` "Safety model" 表。
2. **`Assert-StorOpsNotCritical`独立兜底**：每个 `*-execute.ps1` 在动手前都会对**新鲜读取**的身份重新判定一次
   critical 风险，不信任计划文件里写的内容——防止过期或被手改的 plan 绕过安全检查。这是纵深防御设计，必须在
   Python 版本里保留为独立于"生成计划"路径的第二次校验，而不是只在生成计划时判断一次。
3. **未识别路径默认拒绝**（`category: unknown` → `cleanup_risk: critical` → `Action: CHECK`），而不是从文件名猜测
   ——`docs/DESIGN.md` §3.3、`Identify.psm1` 尾部的兜底分支。
4. **规则与代码分离**：`rules/*.yaml` 是纯数据，`Identify.psm1` 是无状态的匹配引擎。这个数据/代码分离本身应该
   保留，只是 reader 换成 Python。
5. **扫描后端的三函数契约**（`Invoke-StorOpsScan` / `Get-StorOpsTopEntries` / `Get-StorOpsPathSize`，统一返回形状
   `FullName/IsFolder/SizeBytes/AllocatedBytes/Modified/FileCount/FolderCount`）—— 这正是 Prompt 要求的
   `DiskProvider`/`PlatformProvider` 抽象，只是目前用 PowerShell module 导出函数实现，而不是类。Python 版本
   直接把这个契约翻译成一个 `ScanBackend` Protocol/ABC 即可，不需要重新设计。
6. **`-Json` 模式下 stdout 只有 JSON**：审计确认所有入口脚本在 `$Json` 分支里只有一条 `ConvertTo-Json | return`，
   没有混入 `Write-Host`。`Write-Verbose`/`Write-Warning` 走独立流。这个纪律已经基本符合 Prompt 第三十一节
   "stdout=结果，stderr=日志"的要求，Python 重写只需保持，不需要"发明"。
7. **`Backend`/`BackendAdvice` 字段**：把"当前用的是哪个后端""是否应该提醒用户装更快的工具"做成结构化 JSON
   字段而不是只打一条人类可读的 warning——这是一个很好的"给 Agent 用而不是给人用"的设计范式，应在 Python 版本
   的所有子命令输出里延续（不仅限于扫描类命令）。
8. **迁移安全序列**：copy → 校验（文件数+字节数）→ 只有校验通过才删除原始数据 → （Junction 方式）重新链接 →
   再验证一次链接本身。verify.ps1 独立于 execute 之外可重复运行。这个状态机必须整体保留。

### 1.5 PowerShell 依赖的两类问题（严格区分,不能混为一谈）

**A. "PowerShell 语言本身"跨平台**——现状：**已经是跨平台的**。所有脚本 `#requires -Version 5.1` 或 `7.0`，
`Common.psm1` 用 `Get-Variable -Name IsLinux/IsMacOS` 而不是直接引用（兼容 5.1 严格模式），路径处理用
`[System.IO.Path]` 而不是手拼分隔符。语言层面没有 Windows-only 语法。

**B. "PowerShell 里调的系统 API/命令"跨平台**——现状：**这才是真正的不对称**：

| 能力 | Windows 实现 | Linux/macOS 实现 | 对称性 |
|---|---|---|---|
| 目录大小扫描 | `WizTree64.exe`（第三方 GUI 软件的 CLI，需**用户自行安装**，无内置 fallback） | `gdu`（推荐，需安装）→ 系统自带 `du`（**总能用**） | **不对称**：Linux/macOS 有兜底，Windows 没有 |
| 容量查询 | `Get-CimInstance Win32_LogicalDisk`（Windows API，无外部依赖） | `df -Pk`（系统自带，总能用） | 对称，都不依赖第三方 |
| 提权判断 | `WindowsIdentity`/`WindowsPrincipal`（.NET API） | `id -u`（系统自带命令） | 对称 |
| 批量复制+校验迁移 | `robocopy.exe`（Windows 自带，但**硬编码依赖**，非 Windows 平台 100% throw） | **无实现** | **完全不对称**：这是一个真实的功能缺口，不是"细节实现不同" |
| 迁移后旧路径重定向 | NTFS Junction（`New-Item -ItemType Junction`） | **无实现**（symlink 从未被 `migrate-plan.ps1`/`migrate-execute.ps1` 考虑） | **完全不对称** |
| 关键路径识别规则 | `windows.yaml`（完整） | `linux.yaml`/`macos.yaml`（完整，覆盖度对等） | 对称（这一块做得对） |
| 应用识别规则（LM Studio/Docker/npm 等） | `ai-models.yaml`/`applications.yaml`/`caches.yaml`（Windows token-only） | **规则文件里几乎没有 Linux/macOS 的 `path_patterns`**（README/DESIGN 里已自述这是已知缺口） | 不对称，且是 `docs/DESIGN.md` §4c 里明确写的待办 |

原始 Prompt 假定"Windows 是问题最大的一端"（建议 Windows 用 API 替代 PowerShell/WMIC）。**审计结论相反**：
Windows 这一侧的抽象和实现质量其实是最好的（`Get-CimInstance` 已经是结构化 API 调用，不是解析 `wmic`/`diskpart`
文本；没有出现 `Get-Volume`/`Get-Disk`/`Get-PSDrive` 这类 Prompt 特别点名要排查的高风险 cmdlet）。**真正拖后腿
的是 Windows 端缺少不依赖第三方软件的 fallback 扫描器，以及迁移执行完全没做跨平台**。这一条直接推翻了 Prompt
里"Windows 不应该继续成为 PowerShell-first"的默认假设——Windows 端问题不是"用了 PowerShell",而是"扫描能力
100% 外包给一个用户可能没装的 GUI 软件"和"迁移执行只写了 Windows 分支"。按本文档开头约定的优先级（实际代码 >
Prompt 假设），架构方案会以这个真实结论为准。

### 1.6 具体跨平台问题清单

#### 1.6.1【✅ 已现场验证，最高严重级】三个扫描后端共用的 `return @($list)` 写法在 PowerShell 7.4.6 上崩溃

**验证方法**：本仓库沙箱最初没有 `pwsh`，已从 PowerShell 官方 GitHub Release 下载 `powershell-7.4.6-linux-x64`
（与 `Gdu.psm1`/`Du.psm1` 声明的 `#requires -Version 7.0` 一致），装了 `libicu` 让运行环境干净（排除掉最初为
绕过缺库而加的 `DOTNET_SYSTEM_GLOBALIZATION_INVARIANT=1` 这一环境干扰变量,确认结论与该变量无关），在真实
`en-US`/正常全球化模式下直接跑仓库里的脚本，不是猜测。

**复现步骤（对着仓库真实文件跑通）**：
```
$ pwsh scripts/scan.ps1 -Path /tmp/storops-scan-test -Json
WARNING: StorOps: 'gdu' not found on PATH -- falling back to 'du', ...
Invoke-StorOpsScan: Argument types do not match
```
`cleanup-plan.ps1 -Json` 同样命中（虽然因为 §1.6.4 的规则缺口，它的失败症状目前会先被"空计划"掩盖，见 §1.6.5）。

**最小复现（隔离出真正的触发条件，与 StorOps 代码本身无关，是 PowerShell/.NET 的行为）**：
```powershell
function Get-Foo {
    $l = New-Object System.Collections.Generic.List[object]   # 空 List 也一样炸，不需要真的塞数据
    return @($l)                                               # <- 就是这一行
}
Get-Foo   # ArgumentException: Argument types do not match
```
完整异常堆栈定位到 PowerShell 引擎内部：
```
System.ArgumentException: Argument types do not match
   at System.Linq.Expressions.Expression.Condition(...)
   at System.Management.Automation.Language.PSEnumerableBinder.MaybeDebase(...)
   at System.Management.Automation.Language.PSToObjectArrayBinder.Bind(...)
```
逐项排除后精确定位触发条件：`@()` 数组子表达式运算符作用于一个 **`List<object>`**（元素类型是 `object`/接口，
而非具体类型）时触发；`List<string>`、非泛型 `ArrayList`、`,$list`（逗号一元运算符）、直接返回 `$list`（不包
`@()`）、或者先 `.ToArray()` 再返回，**全部不受影响**——问题精确地卡在"`@()` + 元素类型是 `object` 的泛型
List"这一个组合上，跟内容是否为空、是否用 `Set-StrictMode`、是否设置 `$ErrorActionPreference` 都无关。

**代码里三处踩中这个组合**（`ConvertFrom-WizTreeCsv`/`ConvertFrom-GduNode`/`ConvertFrom-CSV`+循环等所有内部
collector 都是 `New-Object System.Collections.Generic.List[object]`，也就是元素类型清一色是 `object`）：
```powershell
scripts/lib/backends/Du.psm1:125:        return @($entries)
scripts/lib/backends/Gdu.psm1:190:        return @($entries)
scripts/lib/backends/WizTree.psm1:240:    return @(ConvertFrom-WizTreeCsv -CsvPath $csv)
```
`Du.psm1` 那一处已经通过 `scan.ps1` 端到端实测确认必炸；`Gdu.psm1`/`WizTree.psm1` 是**同一个函数级别的写法**
（`List[object]` + `return @(...)`），本沙箱没有 `gdu`/WizTree 二进制无法端到端跑通那两条链路，但触发条件已经
被最小复现精确锁定为与外部工具输出内容完全无关的语言层面问题，可以高置信度推断同样会炸——**也就是说 Windows
的 WizTree 后端大概率同样受影响**（前提是按 README 文档要求用 `pwsh`（PowerShell 7 Core）调用，而不是老式
Windows PowerShell 5.1/Desktop；README 的 Quick Start 示例本身就是 `pwsh scripts/scan.ps1 ...`）。

**结论与建议**：这是当前仓库里目前发现的**唯一一个已经端到端实测复现、且推断影响三平台**的功能性 crash——
比原 Prompt 假设的任何问题都更紧急，因为它挡在整个功能面最前面（`scan`/`inspect`/`search`/`cleanup-plan`/
`migrate-plan` 全部先调用扫描后端）。**低成本 hotfix**（不依赖本次 v2 重写，可以独立、立即修）：把三处
`return @($x)` 改成 `return $x.ToArray()` 或直接 `return $x`（去掉 `@()` 包装——调用方本来就已经用
`@(Invoke-StorOpsScan ...)`/`Sort-Object | Select-Object` 这类会重新枚举的方式接收返回值，未必需要发送端强制
转数组）。建议作为独立 PR，不必等 Python 重写落地。

#### 1.6.2 迁移执行（migrate-execute.ps1）在非 Windows 上完全不可用

`scripts/migrate-execute.ps1:102-105`：
```powershell
$robocopy = Get-Command -Name 'robocopy.exe' -ErrorAction SilentlyContinue
if (-not $robocopy) {
    throw 'StorOps: robocopy.exe was not found on PATH -- it ships with Windows and is required for the verified-copy step.'
}
```
无条件要求 `robocopy.exe`。理论上 `migrate-plan.ps1` 会在 Linux/macOS 上生成一个计划，`migrate-execute.ps1`
拿到后应该在这一行直接 throw——但**实测发现实际症状更靠前**：用一个 Linux 上真实存在的目录跑
`migrate-plan.ps1`，在到达 robocopy 检查之前就先被 `Assert-StorOpsNotCritical` 拦截了：
```
$ pwsh scripts/migrate-plan.ps1 -Path /tmp/x -Destination /tmp/y -Json
Exception: StorOps: refusing to operate on '/tmp/x' -- classified CRITICAL risk (unknown).
This tier is never eligible for automatic delete/move, regardless of what a plan file says.
```
原因是 §1.6.4 描述的规则缺口——Linux 上**没有任何一条规则会把路径标记为 `migratable: true`**（`linux.yaml`/
`macos.yaml` 只有 critical 系统路径，`ai-models.yaml`/`applications.yaml` 的 `migratable: true` 规则清一色
Windows token-only），所以任何 Linux 路径要么命中 critical 系统规则，要么（更常见）命中不到任何规则、被兜底
分类为 `unknown`/`critical`——安全模型按设计正确拒绝了操作,但结果是**整条 migrate 工作流在 Linux 上目前对任意
路径都无法通过 plan 阶段**，比"到 execute 阶段才因为 robocopy 缺失而失败"更早、更彻底地断掉。§2.15 补齐
Linux/macOS 的 `migratable: true` 规则后，才能真正复现到 robocopy 这一步的失败，届时结论不变：仍然需要
§2.14 设计的 `shutil`/`os.symlink` 版本 `CopyEngine`/`LinkEngine`。**结论不变，只是根因链条更长：SKILL.md
承诺的"migrate X to another drive"工作流，在 Linux/macOS 上目前从规则匹配这一步开始就是断的。**

#### 1.6.3 Windows 扫描无第三方软件时零 fallback
`backends/WizTree.psm1` 的 `Get-StorOpsWizTreePath` 找不到 WizTree 时直接 `throw`，整条链路
（`scan.ps1`/`inspect.ps1`/`search.ps1`/`cleanup-plan.ps1`/`migrate-plan.ps1`）在 Windows 上就完全瘫痪。
`docs/DESIGN.md` §14/§22 明确写了"不要让 WizTree 成为整个架构的强依赖"，但代码现状与这个设计原则矛盾——
这不是 Prompt 强加的新要求，是仓库自己文档里承诺了但没兑现的东西。

#### 1.6.4 规则库对 Linux/macOS 应用识别覆盖基本为零
`rules/ai-models.yaml`/`applications.yaml`/`caches.yaml` 的 `path_patterns` 清一色 `%USERPROFILE%`/
`%LOCALAPPDATA%` 等 Windows token。`rules/README.md`、`docs/DESIGN.md` §4c 都已自述这是已知缺口。这意味着
即使扫描后端在 Linux/macOS 上能跑（gdu/du 已实现，修完 §1.6.1 之后），"identify"这个 StorOps 的核心差异化能力
（"这是什么、能不能删、能不能迁移"）在非 Windows 平台上**几乎总是返回 `unknown`/`critical`**——
**已现场验证**：对一个真实存在的 `~/.cache/huggingface/models--test` 路径跑 `identify.ps1 -Json`，返回
`"Category": "unknown", "CleanupRisk": "critical", "Recommended": "CHECK"`——尽管 `huggingface-cache` 规则
本身是存在的，只是它的 `path_patterns` 只写了 `%USERPROFILE%\.cache\huggingface\*`（Windows token），在
Linux 上永远不会展开匹配。能扫描但不能理解，价值大打折扣。§2.15 是这个缺口的具体补齐方案。

#### 1.6.5 清理候选路径探测的分隔符硬编码【✅ 已现场验证】
`cleanup-plan.ps1` 的 `Get-StorOpsProbePath`：
```powershell
if (-not $Pattern.EndsWith('\*')) { return $null }
$stripped = $Pattern.Substring(0, $Pattern.Length - 2)
```
只认 `\*` 结尾的 pattern。**已现场验证**：在 Linux 上跑 `cleanup-plan.ps1 -Json`，返回
`"Items": [], "TotalReclaimableBytes": 0, "Backend": "Du"`——因为 §1.6.4 描述的现状（deletable 规则的
pattern 也都是 Windows token-only），这条路径在 Linux/macOS 上现在是"死代码"（永远返回 null，等于清理计划
在非 Windows 上恒为空），暂时不会报错，但一旦按 §2.15 补齐 Linux/macOS 的 `deletable: true` 规则，这里就会
成为一个新的隐藏 bug——扩规则库的人不会想到还要来改这一行的 `\*` 判断。这是"平台判断散落在业务逻辑里"的
典型反面案例，Prompt 第七节点名的问题在这里是真实存在的，Python 重写时必须把 pattern 匹配和"如何从 pattern
反推一个可探测目录"这两件事做成与分隔符无关的纯函数。

#### 1.6.6 `Test-Path -PathType Container` / `LinkType` 等 Windows 语义在处理 symlink 时未经审视
`verify.ps1`/`migrate-execute.ps1` 用 `.LinkType` 判断"是不是 Junction"——这是 Windows `FileSystemInfo` 的
属性，在 PowerShell 7 for Linux/macOS 上 `LinkType` 对 symlink 返回的是 `'SymbolicLink'` 而不是 `'Junction'`
（Junction 是 NTFS 专有概念，Linux/macOS 根本没有这个链接类型）。也就是说,即便 §1.6.2 的 robocopy 依赖被解决,
"Junction 迁移方式"在设计上就是 Windows-only 的概念，Linux/macOS 需要的是完全不同的一条判断分支
（symlink，而不是"Junction 的等价物"）——这一点 Prompt 第十六节其实已经预见到了
（"Windows symlink / Windows junction / mount point 是不同概念，必须明确策略"），仓库目前对此没有任何区分。

#### 1.6.7【✅ 已现场验证，证伪——原文档的猜测是错的，记录以免重复排查】`Identify.psm1` 规则目录路径拼接
上一版文档在这里猜测 `scripts/lib/Identify.psm1:135` 的 `Join-Path $PSScriptRoot '..\..\rules'` 会在非
Windows 上因为内嵌反斜杠不被当作分隔符而拼出错误路径。**已用真实 pwsh 7.4.6 在 Linux 上验证，结论是：这条
路径没有问题**：
```
$ pwsh -Command '
Join-Path "/mnt/e/.../scripts/lib" "..\..\rules"
'
# 结果: /mnt/e/.../scripts/lib/../../rules  (反斜杠被 Join-Path 正确处理/规范化了)
```
进一步端到端验证 `Get-StorOpsRules -Force` 在 Linux 上正常加载了 53 条规则，`identify.ps1` 对真实 Linux 路径
返回正确的分类结果（见 §1.6.4 的验证输出）。**根因**：PowerShell 的 `Join-Path` cmdlet（不同于 .NET 原始的
`Path.Combine`）在 FileSystem provider 下会把子路径参数里的 `\` 和 `/` 都当作合法分隔符对待，不区分当前平台——
这是 PowerShell 为了让 Windows 上写的脚本在跨平台场景下更宽容而做的有意设计，不是巧合。**结论**：这一条从
审计发现清单中移除，不再是需要处理的问题；保留在这里只是为了记录"怀疑过、查过、排除了"，避免以后有人重新
花时间去查同一个假设。真正命中的高严重级问题是 §1.6.1。

### 1.7 测试与 CI 现状

- **CI**：唯一的 workflow 是 `release-please.yml`，只做版本号/CHANGELOG 自动化，**不含任何测试 job，不含任何
  平台 matrix**。Prompt 要求的 `ubuntu-latest / macos-latest / windows-latest` matrix 目前完全不存在。
- **测试**：唯一的测试文件是 `tests/smoke.ps1`，零依赖（不用 Pester），只测 `Identify.psm1`/`Risk.psm1`
  （规则加载、路径识别、风险分级、`Format-StorOpsSize`），**测试用例本身显式声明是 Windows-path-specific**
  （用 `$env:USERPROFILE`/`$env:SystemRoot`），文件头注释原话："the equivalent coverage for linux.yaml/
  macos.yaml is not yet written"。
- 没有任何针对 `scan.ps1`/`inspect.ps1`/`cleanup-*`/`migrate-*` 的集成测试，也没有 Unicode 文件名、带空格路径、
  权限拒绝、symlink、大目录等场景的测试。

### 1.8 未提交的本地改动（WIP，与本次审计无关，不要改动）

`git status` 显示三个文件有未提交的修改（另一个 in-progress bug fix session 的工作）：
- `rules/applications.yaml`：修正 WSL VHDX 的 `path_patterns`（旧的 `*.WSL*\LocalState\*.vhdx` 从未真正匹配过
  Store 安装的发行版目录名，改成固定文件名 `ext4.vhdx`）。
- `scripts/lib/ScanBackend.psm1`：`Import-Module Common.psm1` 加上 `-Global`（让 `Common.psm1` 导出的函数在
  backend 模块里也可见）。
- `scripts/lib/backends/WizTree.psm1`：CSV header 检测从"匹配英文文本 `File Name`"改成"结构化检测（第二字段
  不是纯数字的第一行）"，修复中文 Windows 下 WizTree 导出 CSV 表头被本地化导致解析失败的问题——这正是 Prompt
  第十七节强调的"不要依赖 locale/本地化文本解析"的一个真实、已经被踩过的坑，修复方向完全正确，应保留。

这三处改动与本次 v2 架构规划正交，**本文档及后续 Phase 不会触碰、回滚或基于它们做设计**；它们应该按自己的节奏
被审查、测试、提交。

### 1.9 迁移风险清单（进入 Phase 3 前必须心里有数的坑）

1. `rules/*.yaml` 里的**规则数据本身**（1039 行，业务知识含量最高的部分）要原样保留，只换 reader；§1.6.7 的
   排查过程本身就是一个提醒：不能只凭"看起来像会有问题"就下结论，也不能只凭"在作者本机跑通"就假设"在目标平台
   也跑通"——重写 reader 时用 `pathlib`/三平台单元测试双重保险，两头都不要靠猜。
2. `migrate-execute.ps1` 的 Windows 分支（robocopy + Junction）目前是**唯一被验证过大致能用**的迁移执行路径
   （即便如此也从未在真实 Windows 机器上跑过，参见 `WizTree.psm1` 注释"authored without access to a Windows
   machine"）——Python 重写时 Windows 分支功能对等即可，不需要主观"改进"太多，优先把 Linux/macOS 分支**从无到有**
   补上，这是净新增能力而不是迁移。
3. WizTree CLI 参数（`/exportmaxdepth` 相对扫描目标还是相对盘符根）从未在真机验证过，`gdu` JSON 导出格式
   （`[schemaVersion, flags, rootNode]`）也从未验证过——这两处协议假设进 Python 重写时要原样带着"未验证"标签,
   建议 Phase 4 落地时找一台真实 Windows/Linux 机器跑一次最小验证用例,而不是继续凭文档假设往前叠代码。
   **另外，如果有人在 Python 重写之前想临时用一下现有 PowerShell 版本**：`Gdu.psm1`/`WizTree.psm1` 内部与
   `Du.psm1` 用的是完全相同的 `return @($listOfObject)` 写法（§1.6.1），即使这两个函数本身没有被现场跑通,
   同一个 hotfix（把 `return @($x)` 改成 `return $x.ToArray()`）也应该一并应用到这两个文件，不要只修
   `Du.psm1` 那一处——三处是同一个 bug 的三次重复,不是三个独立问题。Python 重写本身不会继承这个问题
   （Python 没有这种"数组子表达式对泛型 List 绑定失败"的语言级怪癖），但这是当前 PowerShell 代码库在被淘汰
   之前，唯一值得单独出 hotfix 的功能性 crash。
4. SKILL.md 的 13 条规则、三个工作流、以及 README 里的"Quick start"示例命令，都是**外部契约**（其他人可能已经
   把 SKILL.md 的行为预期编码进自己的 Agent prompt / 自动化里）。CLI 从"9 个 .ps1"变成"1 个 storops 三层子命令"
   之后，SKILL.md 和 README 需要同步更新，且要在 CHANGELOG 里明确写"这是一次不兼容的调用方式变更"，不能静默改。

---

## 2. Phase 2 —— 目标架构

### 2.1 分层设计（三层,不过度抽象，呼应 Prompt §33 的自我约束）

```
                         Skill (SKILL.md，行为契约不变)
                                    │
                                    ▼
                    storops CLI（Python, argparse 子命令树）
                storops scan / inspect / search / identify /
                storops cleanup plan|execute / migrate plan|execute /
                storops verify
                                    │
                    ┌───────────────┼───────────────┐
                    ▼                                ▼
              Core（业务逻辑，100% 跨平台）      Output（human / json 两种渲染器）
       - 规则引擎（rules/*.yaml 数据不变）
       - 风险分级 + 推荐动作（KEEP/DELETE/MOVE/CHECK）
       - 计划生成/校验状态机（cleanup-plan, migrate-plan）
       - Assert not-critical 兜底
                    │
                    ▼
         Platform Abstraction（ABC/Protocol，每个能力域一个接口）
       - ScanBackend       ：list_top_entries / path_size / scan
       - CapacityProvider  ：drive/filesystem 容量
       - CopyEngine        ：带校验的批量复制
       - LinkEngine        ：迁移后旧路径重定向（Junction/symlink,平台各自实现）
       - PrivilegeProbe    ：是否具备提权/root
                    │
        ┌───────────┼───────────┐
        ▼           ▼           ▼
     LinuxProvider MacOSProvider WindowsProvider
     (gdu→du,      (gdu→du,      (WizTree→
      shutil,       shutil,       原生 Python 扫描 fallback，
      os.symlink)   os.symlink)   shutil, ctypes Win32 API,
                                  NTFS Junction 创建)
```

与 Prompt 原始建议的差别，全部是"实际代码优先"的结果：
- 不单独设一个 `filesystem.py` command 模块——现有 9 个能力已经是清晰的 verb 集合（`scan/inspect/search/
  identify/cleanup-plan/cleanup-execute/migrate-plan/migrate-execute/verify`），直接映射为 `storops` 的
  9 个（分组后是 7 个顶层命令，`cleanup`/`migrate` 各带 `plan`/`execute` 子命令）子命令,不额外发明新词。
- Platform Abstraction 不只是"DiskProvider"一个接口——因为迁移执行明确需要"复制引擎"和"链接引擎"两个独立能力域
  （§1.6.2/§1.6.6 的教训：Windows 是 robocopy+Junction，Linux/macOS 应该是 `shutil.copytree`+`os.symlink`，
  两者的失败模式、校验方式都不同，硬塞进一个接口只会重演"Junction 概念被错误套用到 symlink"的错误）。

### 2.2 目录结构（基于现有仓库布局微调，不推倒重来）

```
storops/  (repo root, 保持不变的部分：docs/, rules/, SKILL.md, README*, LICENSE)
├── pyproject.toml
├── SKILL.md                      更新：调用方式改为 `storops <verb> ... --json`
├── README.md / README.zh-CN.md   更新：Requirements 改为 Python 3.11+，标注旧版兼容策略
├── docs/
│   ├── DESIGN.md                 继续作为产品设计源文档，§4a-4c 需要重写为 Python 版描述
│   └── plans/
│       └── storops-v2-cross-platform-refactor.md   本文档
├── rules/                        原样保留（数据不变，Phase 3 起补 Linux/macOS 应用规则）
│   ├── README.md
│   ├── ai-models.yaml / applications.yaml / caches.yaml
│   └── windows.yaml / linux.yaml / macos.yaml
├── src/storops/
│   ├── __init__.py
│   ├── __main__.py                `python -m storops`
│   ├── cli.py                     argparse 子命令树 + dispatch，唯一的 stdout/stderr 纪律执行点
│   │
│   ├── core/
│   │   ├── models.py              dataclass：Entry, PathIdentity, RecommendedAction,
│   │   │                          CleanupPlan/Item, MigratePlan, MigrateResult, VerifyReport, Capacity
│   │   ├── rules.py                YAML 规则加载 + 迁移自 Identify.psm1 的匹配/token 展开逻辑
│   │   ├── risk.py                 迁移自 Risk.psm1：风险分级 + 推荐动作 + Assert-not-critical
│   │   ├── scan.py                 scan/inspect/search 的编排逻辑（调用 ScanBackend 协议）
│   │   ├── cleanup.py              cleanup-plan/cleanup-execute 的编排逻辑
│   │   ├── migrate.py              migrate-plan/migrate-execute/verify 的编排逻辑
│   │   └── errors.py               StoropsError 层级 + 退出码映射
│   │
│   ├── platform/
│   │   ├── __init__.py             get_scan_backend() / get_copy_engine() / get_link_engine() 工厂
│   │   ├── base.py                 ScanBackend / CopyEngine / LinkEngine 的 Protocol 定义
│   │   ├── linux.py                LinuxScanBackend(gdu→du), LinuxCopyEngine(shutil), LinuxLinkEngine(symlink)
│   │   ├── macos.py                同 linux.py，token 展开表不同（~/Library/Caches 等）
│   │   ├── windows/
│   │   │   ├── __init__.py
│   │   │   ├── scan.py             WindowsScanBackend：优先 WizTree CLI，
│   │   │   │                       无 WizTree 时 fallback 到原生 os.scandir 遍历（新增能力，弥补 §1.6.3）
│   │   │   ├── copy.py             robocopy 包装（保留），失败时可选 shutil fallback
│   │   │   ├── link.py             NTFS Junction 创建（ctypes 或 subprocess mklink /J）
│   │   │   └── powershell.py       兼容层：仅当某能力暂时没有原生 Python 实现时,
│   │   │                           临时调用旧 .ps1（隔离在这一个文件里，core 层永远不 import 它）
│   │   └── backends/
│   │       ├── gdu.py              解析 gdu JSON 导出（对齐 Gdu.psm1 的协议假设,同样标注"待真机验证"）
│   │       ├── du.py                GNU/BSD 双方言 du 包装（对齐 Du.psm1）
│   │       └── wiztree.py           WizTree CLI 包装 + CSV 解析（结构化 header 检测，延续本次未提交修复的思路）
│   │
│   └── output/
│       ├── json.py                 dataclass → JSON（stdout-only 纪律：这个模块是唯一允许 print() 的地方之一）
│       └── human.py                表格/彩色文本渲染（stderr 承载 warning，不占用 stdout 的 JSON 通道）
│
├── tests/
│   ├── unit/                       规则匹配、风险分级、路径规范化、JSON schema 校验（纯函数，任意平台跑）
│   ├── integration/                真实调用 CLI 子进程，tmp_path 生成临时目录树，断言 stdout 的 JSON
│   └── platform/                   仅在对应 OS 的 CI runner 上跑：Windows Junction、Linux bind mount 等
│
└── compat/                         PowerShell 兼容入口（旧调用方式的 wrapper，§2.10）
    └── storops.ps1                 pwsh scripts/scan.ps1 -Path X  →  storops scan X（参数名映射）
```

`scripts/` 目录整体保留一段时间作为兼容层来源（不是马上删除，见 §2.10），但**新功能一律只在 `src/storops/` 里写**，
不再往 `scripts/lib/*.psm1` 里加新逻辑。

### 2.3 Platform Abstraction 的具体协议

```python
# src/storops/platform/base.py
from typing import Protocol, Iterable
from storops.core.models import Entry, Capacity

class ScanBackend(Protocol):
    name: str  # "WizTree" | "Gdu" | "Du" | "WindowsNative" —— 对齐现有 Backend 字段语义
    def scan(self, path: Path, *, export_folders: bool, export_files: bool,
              max_depth: int, name_filter: str | None, admin: bool) -> list[Entry]: ...
    def top_entries(self, path: Path, *, top: int, max_depth: int,
                      admin: bool, include_files: bool) -> list[Entry]: ...
    def path_size(self, path: Path, *, admin: bool) -> Entry | None: ...
    def advice(self) -> str | None:  # 对齐 Get-StorOpsScanBackendAdvice
        ...

class CapacityProvider(Protocol):
    def free_space(self, path: Path) -> Capacity: ...

class CopyEngine(Protocol):
    def copy_verified(self, source: Path, dest: Path) -> "CopyStats": ...

class LinkEngine(Protocol):
    kind: str  # "junction" | "symlink"
    def relink(self, old_path: Path, new_target: Path) -> "LinkResult": ...
    def verify(self, old_path: Path, expected_target: Path) -> bool: ...
```

`get_scan_backend()` 等工厂函数集中在 `platform/__init__.py`，逻辑与现有 `Get-StorOpsScanBackendName` 完全对应
（`sys.platform` 判断只在这一处出现一次，core 层和 CLI 层永远只拿到已经选好的 backend 实例，不自己判断平台）——
这是把 Prompt §7 的"平台判断必须封装"落到实处，而不是发明新规则:现有 `ScanBackend.psm1` 的调度器模式已经是
正确答案，直接照搬成 Python 工厂函数即可。

### 2.4 数据模型（对齐现有字段名，不重新命名造成两套心智负担）

```python
@dataclass(frozen=True)
class Entry:
    full_name: str          # 对齐 FullName
    is_folder: bool          # 对齐 IsFolder
    size_bytes: int          # 对齐 SizeBytes
    allocated_bytes: int      # 对齐 AllocatedBytes
    modified: datetime | None
    file_count: int | None
    folder_count: int | None

@dataclass(frozen=True)
class PathIdentity:
    path: str
    application: str | None
    category: str
    confidence: float
    owner: str | None
    purpose: str | None
    deletable: bool
    migratable: bool
    migration_method: str | None
    migration_hint: str | None
    requires_app_closed: bool
    cleanup_risk: str          # low | medium | high | critical
    consequence: str | None
    notes: str | None
    matched_rule_id: str | None
    matched_pattern: str | None

@dataclass(frozen=True)
class RecommendedAction:
    action: str   # KEEP | DELETE | MOVE | CHECK
    reason: str
```

字段名刻意 1:1 对应 PowerShell 版（只是 PascalCase → snake_case），JSON 序列化时**保持 PascalCase 输出**
（`by_alias`风格），这样现有依赖 `-Json` 输出字段名的下游（用户的 Agent prompt、脚本）不需要改一行——
这是"外部契约不能静默变化"（§1.9 第 4 条）在数据模型层面的具体落实。

### 2.5 CLI 设计

```text
storops scan [PATH] [--top N] [--include-files] [--admin] [--json]
storops inspect PATH [--top N] [--folders-only] [--admin] [--json]
storops search [PATH] [--name-pattern P] [--min-size-gb N] [--older-than-days N] [--folders] [--json]
storops identify PATH [--size-bytes N] [--json]
storops cleanup plan [--max-risk low|medium|high] [--out-file F] [--admin] [--json]
storops cleanup execute --plan-file F [--confirm] [--json]
storops migrate plan SOURCE DESTINATION [--admin] [--json]
storops migrate execute --plan-file F [--confirm] [--app-closed] [--json]
storops verify --result-file F [--json]
storops --version / storops --help / storops <verb> --help
```

与现有 9 个 `.ps1` 的参数是逐一对应关系（`-Path`→位置参数或 `--path`，`-MaxRisk`→`--max-risk`，`-Confirm`→
`--confirm`），保留同样的默认值（`Top=15/20/50`、`MaxRisk=low` 等）。`python -m storops` 和 console script
`storops` 两种启动方式都提供（`pyproject.toml` 的 `[project.scripts] storops = "storops.cli:main"`）。

### 2.6 JSON Schema（示例，三平台字段结构相同,只有值不同）

```json
{
  "scanned_path": "/home/user",
  "drive": {
    "drive": "/dev/sda1",
    "total_bytes": 1000204886016,
    "used_bytes": 500102443008,
    "free_bytes": 500102443008,
    "volume_name": "/dev/sda1",
    "file_system": null
  },
  "entries": [
    {
      "path": "/home/user/.cache/huggingface",
      "is_folder": true,
      "size_bytes": 58204885000,
      "application": "Hugging Face",
      "category": "ai-model-cache",
      "confidence": 0.95,
      "cleanup_risk": "medium"
    }
  ],
  "backend": "Gdu",
  "backend_advice": null,
  "warnings": [
    {"path": "/home/user/.cache/protected", "code": "permission_denied", "message": "..."}
  ]
}
```
`warnings` 数组是**新增字段**（现有 PowerShell 版没有，权限错误目前是 `Write-Warning` 单条文本,不结构化）——
对应 Prompt §14 的要求，也是 Phase 3 里"单个文件读不到不能让整个扫描失败"这条原则在 JSON 里的落地方式，
补上不算破坏性变更（新增字段，旧消费者忽略即可）。

### 2.7 错误模型与退出码

```python
class StoropsError(Exception): ...
class InvalidPathError(StoropsError): ...
class PermissionDeniedError(StoropsError): ...
class BackendNotFoundError(StoropsError): ...      # 对齐 "WizTree/gdu not found" 场景
class UnsupportedOperationError(StoropsError): ...  # 对齐 "migrate on unsupported platform" 场景（§1.6.2 修复后仍可能有平台特例）
class CriticalPathError(StoropsError): ...          # 对齐 Assert-StorOpsNotCritical
class StalePlanError(StoropsError): ...             # 对齐 "plan 内容与当前状态不一致，拒绝执行"
class VerificationFailedError(StoropsError): ...

EXIT_CODES = {
    StoropsError: 1,               # 未分类的一般错误
    "argparse_error": 2,           # 参数错误（argparse 自身即返回 2，天然对齐）
    PermissionDeniedError: 3,
    UnsupportedOperationError: 4,
    CriticalPathError: 5,
    VerificationFailedError: 6,
}
```
现有 PowerShell 版本没有任何退出码约定（未处理异常一律是 PowerShell 默认的非零码，`smoke.ps1` 是唯一显式
`exit 0/1` 的地方）。这是一处**必须新增而非"迁移"**的能力，Prompt §13 的退出码表可以基本照搬，因为它不与
任何现有行为冲突。

### 2.8 规则引擎迁移策略

**决定：不引入 PyYAML，继续用等价的手写 YAML 子集 reader（Python 版）。**

理由（对齐 Prompt §21"标准库优先，非必要不引入依赖"的要求，同时尊重 `rules/README.md` 里已经写明的设计
初衷——"Identify.psm1 是 NOT a general-purpose YAML parser，只解析这些规则文件实际用到的子集"）：
1. `rules/*.yaml` 的 schema 本来就是刻意收窄过的子集（flat mapping + 一个嵌套 list + folded block scalar，
   见 `rules/README.md` "YAML subset supported"一节），PyYAML 提供的能力大部分用不上。
2. 保持零第三方依赖能让 `pip install storops` 在任何 Python 3.11+ 环境秒装,不用等 PyYAML 的 wheel（尤其
   在一些受限的 CI/沙箱镜像里，纯标准库脚本更容易免审即用）——这与 SKILL.md 现在"除了 WizTree/gdu 之外零依赖"
   的定位一致。
3. 唯一要小心的：Python 版 reader 要为 §1.6.1/§1.6.7 这一类"代码读起来没问题、只有真的跑起来才会炸（或者反过来，
   看起来像会有问题、实测却没事）"的情况建立**单元测试**（用 `tmp_path` 构造假规则目录，三个平台的 CI matrix
   各自跑一次 `rules.load_rules()`），不能重蹈"从未在非开发平台真正执行过就下结论"的覆辙——无论下的是"有 bug"
   还是"没 bug"的结论，都需要实测支撑。
4. Token 展开表（`%USERPROFILE%` 等）原样迁移，key 保持原始拼写（大写、`%...%`包裹），行为不变。

### 2.9 安全模型的 1:1 保留（这是重写中唯一"不允许有创造性"的部分）

- `core/risk.py` 移植 `Risk.psm1` 全部四个函数：`risk_rank`、`within_limit`、`assert_not_critical`、
  `recommended_action`，判定逻辑（含"什么时候 DELETE / MOVE / KEEP / CHECK"的优先级顺序）逐行对照迁移，
  不做"顺便优化"。
- `core/cleanup.py`/`core/migrate.py` 的 execute 路径，在真正执行删除/复制前，必须**重新调用一次**
  `assert_not_critical`（对新鲜读取的身份,不是对 plan 文件里缓存的字段）——这是 §1.4 第 2 条的硬要求，
  Phase 5 代码审查时要专门检查这一行没有被"重构"掉。
- CLI 层：`--confirm`/`--app-closed` 两个 flag 的语义（没传就只 dry-run 打印将要做的事）必须保留，
  且这两个 flag 只能通过显式命令行参数传入，**不允许**从 plan 文件或环境变量里读取默认值——防止 Agent
  不小心把"已确认"状态硬编码进自动化脚本里绕过用户交互。

### 2.10 PowerShell 兼容策略【已确认：本次 v2 发布即 100% 向后兼容，不分期、不留窗口期】

**决定（用户已明确）**：不采用"先发布新 CLI、旧脚本以后再考虑要不要兼容"的分期策略。`scripts/*.ps1` 的
wrapper 化是 v2 这一次发布的**强制交付项**，与 Phase 5（CLI）在**同一个版本**里一起出，不是"Phase 6 以后
再评估"。也就是说：v2 发布当天，`pwsh scripts/scan.ps1 -Path C:\` 这种旧调用方式必须继续可用，行为
（参数名、默认值、`-Json` 输出的字段结构、Read/Plan/Write 三层确认语义）与重写前**逐一比对一致**，不允许有
任何静默的行为差异——这也顺带解决了 §1.9 第 4 条提到的"外部契约不能静默变化"的顾虑：因为压根不允许变化。

**落地方式（两部分，Phase 6 与 Phase 5 并行，而不是先后串行）**：

1. **`scripts/*.ps1` 本身**：改造成薄 wrapper，只做参数名转换 + 调用 `storops` CLI + 把 JSON
   结果转回 PowerShell 对象（如果调用方期待 `ConvertFrom-Json` 之后的 PSCustomObject）。示例：
   ```powershell
   # scripts/scan.ps1（v2 之后）
   param([string]$Path = '.', [int]$Top = 15, [switch]$IncludeFiles, [switch]$Admin, [switch]$Json)
   $args = @($Path, '--top', $Top)
   if ($IncludeFiles) { $args += '--include-files' }
   if ($Admin) { $args += '--admin' }
   if ($Json) { $args += '--json' }
   $result = & storops scan @args
   if ($Json) { $result } else { Write-Host $result }
   ```
   PowerShell 最终变成"compatibility entrypoint"，不再是"core runtime"，与 Prompt §9/§28 的要求一致，
   也是本仓库自己 `docs/DESIGN.md` §14 早就写过的"不要让任何单一工具成为强依赖"精神的自然延伸。
2. **`scripts/lib/*.psm1`**：v2 发布时全部降级为"仅供 wrapper 内部调用的薄封装或直接删除"——不再承载任何
   独立于 Python `core/` 的业务逻辑分支，避免出现"两套判定逻辑各自维护、迟早分叉"的局面。§1.6.1 的 hotfix
   （见该节）若在 Python 重写完成前先单独落地，属于修旧代码的独立 PR，与这里的 wrapper 化互不冲突。
3. **验收标准（强制，纳入 Phase 7 测试矩阵）**：为现有 9 个能力各写一条 `tests/integration/` 用例，
   同时跑"旧 wrapper 调用"和"新 CLI 直接调用"两条路径，断言两者的 `-Json`/`--json` 输出在字段名、字段值、
   数组顺序上完全一致（允许的例外：新增的 `warnings` 字段，见 §2.6，这是纯新增，旧消费者忽略即可，不算
   行为变化）。这条测试没通过，Phase 6 不能算完成。
4. **兼容期长度**：因为是"这一版就 100% 兼容"而不是"给一个过渡期"，所以不存在"什么时候可以删掉 wrapper"
   这个问题需要现在回答——`scripts/*.ps1` 作为 wrapper 会一直存在，直到用户明确决定不再需要为止，不设定
   自动到期或自动移除的时间点。

### 2.11 依赖策略

| 依赖 | 是否引入 | 理由 |
|---|---|---|
| Python 标准库（`pathlib`/`subprocess`/`json`/`dataclasses`/`argparse`/`shutil`） | 核心依赖 | 全部满足，见 §2.8 |
| PyYAML | **不引入** | 见 §2.8，规则文件 schema 刻意收窄，标准库手写 reader 足够 |
| Windows 专属能力（磁盘容量/原生扫描 fallback/Junction 创建） | **纯标准库 + 按需 subprocess 到 Windows 自带命令，不引入 `pywin32`** | 见下方 §2.11a 三方案对比，已确定结论 |
| `gdu` / WizTree / `robocopy` / `mklink` | 外部二进制或 Windows 自带命令，非 Python 包依赖 | 现状已是"探测优先，缺失则清晰报错"的模式（§1.4），保留 |
| 测试用 `pytest` | dev-only 依赖 | 标准做法，不进入运行时依赖 |

#### 2.11a Windows 依赖策略的三个选项对比（已确认结论，不再是开放决策）

Windows 端要实现的具体能力只有四项：磁盘容量查询、原生扫描 fallback（§2.13）、Junction 创建（§2.14）、
提权判断。逐项看三个候选方案的覆盖能力：

| | A. 纯标准库 + 按需 shell 原生命令 | B. ctypes 直接绑定 Win32 API | C. `pywin32`（可选 extra） |
|---|---|---|---|
| 磁盘容量查询 | `shutil.disk_usage()`（stdlib 已封装 `GetDiskFreeSpaceExW`） | 同左，多此一举 | 同左，多此一举 |
| 原生扫描 fallback | `os.scandir()` / `os.stat()` | 同左 | 同左 |
| 提权判断 | `ctypes.windll.shell32.IsUserAnAdmin()`（一行 ctypes 调用，标准库自带 `ctypes`） | 同左 | `win32api`/`win32security`，等价但更重 |
| **创建 Junction**（迁移执行的关键能力） | `subprocess` 调用 `cmd /c mklink /J`（Windows 自带命令，零安装） | 手写 `DeviceIoControl` + `FSCTL_SET_REPARSE_POINT` 的 `REPARSE_DATA_BUFFER` 结构体绑定 | **同样解决不了**——`pywin32` 的 `win32file` 没有一等公民的 Junction API，实际上还是要退回 subprocess 或 ctypes |
| ACL / 锁文件检测（当前 9 个能力均不需要，纯前瞻） | 用不了 | 可以（`GetNamedSecurityInfo`），要自己写绑定 | `win32security` 现成封装，能力最强 |
| WMI/CIM 查询（等价 `Get-CimInstance Win32_LogicalDisk`，当前不需要） | 用不了 | 可以但复杂（COM 互操作） | `wmi` 包（基于 pywin32）现成封装 |
| 安装体积/依赖重量 | 零 | 零 | 大（编译产物，版本兼容问题，`pywin32_postinstall` 注册脚本） |
| 与现有代码风格的一致性 | **高**——现有 `migrate-execute.ps1` 本来就 shell 到 `robocopy.exe`，`mklink /J` 是同一路数 | 中——项目里从未出现过手写 ctypes 绑定 | 低——引入一整套新的编程模型 |
| 出错风险 | 低（调用已知工具，行为可预测） | 高（struct 打包/对齐一错就是难调的崩溃） | 中（依赖第三方包版本兼容性） |

**结论：选 A。** Junction 创建是 Windows 分支里唯一"标准库确实无替代"的能力，而 Prompt 自己的原则就是
"只有在没有合理跨平台 API 的情况下才用系统命令"——`mklink /J` 和现有代码已经在用的 `robocopy.exe` 是同一类
"Windows 自带、无需安装"的原生命令，选 A 是对现有代码风格的延续，不是妥协。B（ctypes 手写 reparse point）
复杂度和出错概率都偏高，只有在"启动一个新进程的开销/闪一下控制台窗口"真的成为体验问题时才值得考虑，留作
后续可选优化项，不进 MVP。C（`pywin32`）唯一能带来的独有能力是 ACL 检查和 WMI 查询——但当前 9 个能力里
没有一个真正依赖这两者，现在引入纯属"为了以后可能用得上"的重量级依赖，违反 Prompt §21"非必要不引入依赖"。
如果未来真的要做 Prompt §14 提到的 ACL 级权限诊断，再把 `pywin32` 作为 `storops[windows-acl]` 这样命名
清楚的可选 extra 引入，不影响这次的默认安装体积。

### 2.12 测试与 CI 策略

```yaml
strategy:
  matrix:
    os: [ubuntu-latest, macos-latest, windows-latest]
    python-version: ["3.11", "3.12"]
```
- `tests/unit/`：规则匹配、风险分级、路径规范化、JSON 序列化——纯函数，任意平台跑，不需要真实磁盘扫描后端。
- `tests/integration/`：起子进程调用 `storops` CLI，用 `tmp_path` 生成含中文/emoji/空格/特殊字符文件名的目录树
  （对齐 Prompt §24 的 Unicode 测试要求），断言 `--json` 输出的 stdout **只包含合法 JSON**（这是回归防线，
  防止未来有人不小心在 `$Json` 分支里加了一条 `print()`调试语句破坏 stdout 纪律）。
- `tests/platform/`：只在对应 runner 上跑，覆盖 §1.6.6 指出的 Junction vs symlink 分叉、Windows UNC 路径、
  macOS `/Volumes` 外置卷、Linux bind mount/overlayfs 场景（有条件时用 `pytest.mark.skipif` 按平台跳过）。
- `tests/smoke.ps1` 保留但降级为"兼容层烟雾测试"，验证 Phase 6 之后的 `.ps1` wrapper 参数转换没有拼错。

### 2.13 Windows 原生扫描 fallback（弥补 §1.6.3 的净新增能力）

WizTree 仍是 Windows 上的**首选**后端（NTFS MFT 直读，速度无可替代），但新增一层：WizTree 未安装时，不再直接
报错阻断，而是 fallback 到 `os.scandir` 遍历 + `os.stat().st_size`（外加 `ctypes` 调用
`GetDiskFreeSpaceExW` 拿卷容量，标准库 `shutil.disk_usage()` 已经封装了这个调用，直接用即可,不需要手写
`ctypes`）。这条 fallback 路径速度显然不如 WizTree（普通 `stat()` 遍历，与 Linux 上的 `du` 是同一数量级），
但**保证"零第三方软件也能用"**这一点在三个平台上首次做到对称——目前只有 Linux/macOS 有这个保证。
`Get-StorOpsScanBackendAdvice` 的等价字段在这种情况下应提示"检测到未安装 WizTree，已使用较慢的原生扫描，
安装 WizTree 可显著提速"，与现有 Du 场景的提示风格一致。

### 2.14 迁移执行跨平台方案（弥补 §1.6.2，SKILL.md 承诺但当前缺失的能力）

- `CopyEngine`：Windows 保留 `robocopy` 包装（校验方式不变：copy → 对比 pre/post 文件数+字节数 → 通过才删除
  原始）；Linux/macOS 新增 `shutil.copytree(..., dirs_exist_ok=False)` + 前后各遍历一次统计文件数/字节数校验，
  校验逻辑与 Windows 分支共享同一段 Python 代码（不是各自平台重复实现"数一遍文件"的逻辑）。
- `LinkEngine`：Windows 保留 Junction（`mklink /J` 或 `ctypes` 调用 `CreateSymbolicLink`/junction 相关 API）；
  Linux/macOS 新增 `os.symlink(target, old_path, target_is_directory=True)`。**两者在 `migrate-plan.ps1` 生成的
  `Method` 字段里应该分开命名**（`"junction"` vs `"symlink"`），而不是像现在这样用同一个 `useJunction` 布尔值
  概念——这是吸取 §1.6.6 教训后的具体设计调整,是本方案与"机械翻译"路线唯一一处对现有字段语义做的必要修正,
  修正原因和范围在此明确记录，不做静默变更。
- `verify.py` 对应地按 `Method` 字段分支：`junction` 走 Windows 的 `LinkType`/`Target` 检查，`symlink` 走
  `os.path.islink()` + `os.readlink()` 比对,两条分支独立测试。

### 2.15 Linux/macOS 应用识别规则补齐方案【已确认：并入本次方案，不再单独排期】

**决定（用户已明确）**：§1.6.4 描述的"Linux/macOS 应用识别规则基本为零"这个缺口（§1.6.2 里 migrate 工作流
在 Linux 上从规则匹配这一步就断掉，是这个缺口的下游症状之一），不作为独立排期的
后续任务，而是**并入本次方案的 Phase 4 范围**一起做。逐条核对了 `ai-models.yaml`/`applications.yaml`/
`caches.yaml` 现有的每一条规则，分成三类：

**A 类：通配符前缀、无 token 的规则——不需要改数据，改一个匹配器行为即可自动在 Linux/macOS 生效**

前提是把规则匹配器改成"分隔符无关"（`\` 和 `/` 统一规范化后再比较——现有规则数据一律用 `\` 书写是历史遗留，
Python 重写时读入 `rules/*.yaml` 后先把 pattern 和目标路径都规范化成同一种分隔符再做 `fnmatch`，不需要为
每条规则手写两份分隔符变体）。命中这一类的规则：`comfyui-models`、`stable-diffusion-webui-models`、
`git-repo-objects`、`steam-library`——它们的 pattern 本来就是 `*\ComfyUI\models\*` 这种前缀通配、不含任何
`%TOKEN%`，这几个目录名（`ComfyUI/models`、`steamapps/common`、`.git/objects`）在三平台下本来就一样。

**B 类：需要新增 Linux/macOS token 变体的规则——已逐条查到真实默认路径，可直接在 Phase 4 落地**

| 规则 id | Windows 现有 pattern | 新增 Linux pattern | 新增 macOS pattern |
|---|---|---|---|
| `lmstudio-models` | `%USERPROFILE%\.lmstudio\models\*` | `%HOME%/.lmstudio/models/*` | `%HOME%/.lmstudio/models/*` |
| `ollama-models` | `%USERPROFILE%\.ollama\models\*` | `%HOME%/.ollama/models/*` | `%HOME%/.ollama/models/*` |
| `huggingface-cache` | `%USERPROFILE%\.cache\huggingface\*` | `%HOME%/.cache/huggingface/*`（HF 库默认不遵循 XDG，固定用 `~/.cache/huggingface`） | 同 Linux |
| `pytorch-hub-cache` | `%USERPROFILE%\.cache\torch\*` | `%HOME%/.cache/torch/*` | 同 Linux |
| `whisper-cpp-ggml-models` | `%USERPROFILE%\.cache\whisper\*` | `%HOME%/.cache/whisper/*` | 同 Linux |
| `cuda-compute-cache` | `%LOCALAPPDATA%\NVIDIA\ComputeCache\*` | `%HOME%/.nv/ComputeCache/*` | *不适用*（现代 macOS 无独立 NVIDIA GPU，`notes` 里注明原因，不强凑路径） |
| `npm-cache` | `%LOCALAPPDATA%\npm-cache\*` | `%HOME%/.npm/*`（Linux/macOS 默认缓存目录是 `~/.npm`，不是 `~/.cache/npm`） | 同 Linux |
| `pnpm-store` | `%LOCALAPPDATA%\pnpm\store\*` | `%XDG_DATA_HOME%/pnpm/store/*`（fallback `~/.local/share/pnpm/store`） | `%HOME%/Library/pnpm/store/*` |
| `yarn-cache` | `%LOCALAPPDATA%\Yarn\Cache\*` | `%XDG_CACHE_HOME%/yarn/*` | `%CACHES%/Yarn/*` |
| `pip-cache` | `%LOCALAPPDATA%\pip\Cache\*` | `%XDG_CACHE_HOME%/pip/*` | `%CACHES%/pip/*` |
| `uv-cache` | `%LOCALAPPDATA%\uv\cache\*` | `%XDG_CACHE_HOME%/uv/*` | `%CACHES%/uv/*` |
| `conda-pkgs-cache` | `%USERPROFILE%\.conda\pkgs\*` 等 | `%HOME%/.conda/pkgs/*`、`%HOME%/miniconda3/pkgs/*`、`%HOME%/anaconda3/pkgs/*` | 同 Linux |
| `vscode-caches-workspacestorage` | `%APPDATA%\Code\...` | `%XDG_CONFIG_HOME%/Code/{CachedData,Cache,CachedExtensionVSIXs,User/workspaceStorage}/*`（VS Code 默认用 `~/.config`，未必遵循自定义 XDG_CONFIG_HOME，需要在实现时同时兜底 `~/.config/Code`） | `%APP_SUPPORT%/Code/...`（`~/Library/Application Support/Code/...`） |
| `jetbrains-caches` | `%LOCALAPPDATA%\JetBrains\*\caches\*` | `%HOME%/.cache/JetBrains/*` | `%CACHES%/JetBrains/*` |
| `browser-cache-generic` | Chrome/Edge `...User Data\*\Cache\*` | `%XDG_CONFIG_HOME%/google-chrome/*/Cache/*`、`%XDG_CONFIG_HOME%/microsoft-edge/*/Cache/*`（默认 `~/.config/...`） | `%CACHES%/Google/Chrome/*/Cache/*`、`%CACHES%/Microsoft Edge/*/Cache/*` |
| `discord-cache` | `%APPDATA%\discord\Cache\*` 等 | `%XDG_CONFIG_HOME%/discord/{Cache,Code Cache,GPUCache}/*` | `%APP_SUPPORT%/discord/{Cache,Code Cache,GPUCache}/*` |
| `downloads-folder` | `%USERPROFILE%\Downloads\*` | `%HOME%/Downloads/*` | `%HOME%/Downloads/*` |

**C 类：平台本质不存在的规则——明确标注"不适用"，不强凑一个假路径**

`visual-studio-componentcache`（Visual Studio 不支持 Linux/macOS）、`adobe-media-cache`（Adobe 桌面套件
不支持 Linux；macOS 版本存在，补一条 `%APP_SUPPORT%/Adobe/Common/Media Cache Files/*` 即可，Linux 侧留空）、
`docker-desktop-wsl-data`/`wsl-distro-vhdx`（WSL 是 Windows 专属概念，Linux 上 Docker 是原生运行、无 VM 磁盘；
macOS 上 Docker Desktop 有自己的 VM 磁盘概念，路径是 `~/Library/Containers/com.docker.docker/Data/vms/0/*`，
值得单独新增一条 `docker-desktop-macos-vm-disk` 规则而不是套用 Windows 那条的字段）、`caches.yaml` 里除
`downloads-folder` 外的其余七条（`windows-temp`/`windows-update-cache`/`windows-error-reporting`/
`windows-old`/`thumbnail-cache`/`delivery-optimization-cache`/`prefetch`/`crash-dumps`）本身就是 Windows
系统概念，不存在，也不适合发明一条"看起来像"的 Linux/macOS 版本硬凑进去——这几项刻意留空，是设计决定，
不是遗漏。

**明确不做的事（避免过度扩权）**：不新增"用户缓存目录全量通配"这种粗粒度兜底规则（例如 `%XDG_CACHE_HOME%/*`
或 `%CACHES%/*`）——这类规则粒度太粗，会把任何未识别的子目录都误判为"已识别的缓存"，与 §1.4 第 3 条
"未识别路径默认拒绝"的设计原则冲突。规则库只以"具体到某个应用"的粒度扩展，不识别的东西继续老实返回
`unknown`/`critical`。同理，root 拥有的系统级缓存（Linux 的 `/var/cache/apt/archives`、Docker Engine 的
`/var/lib/docker`）暂不纳入 MVP 规则库——它们需要提权才能读/删，属于 §14 权限模型要单独设计的一类场景，
不在这次"补齐用户级应用识别"的范围内，留在 Phase 4 的验收清单里明确写"不含需要 root 的系统级路径"。

**该动作对 Phase 划分的影响**：Phase 4（平台适配层）的交付范围新增"更新 `rules/*.yaml`，为上表 B 类规则
补齐 Linux/macOS `path_patterns`，C 类里的 macOS Adobe/Docker 两条新增独立规则"，与 §1.6.1 的三处
`return @($list)` hotfix、§2.13 的原生扫描 fallback、§2.14 的 CopyEngine/LinkEngine 一起验收，不再是
"以后再排期"的悬而未决事项。

---

## 3. Phase 划分与里程碑（在本文档基础上执行，不在本次改动范围内）

| Phase | 内容 | 产出 | 前置条件 |
|---|---|---|---|
| 0（可选，独立于 v2，随时可做） | §1.6.1 hotfix：三处 `return @($x)` 改成 `.ToArray()`/去掉 `@()` 包装 | 对现有 `.psm1` 的一个小 PR | 无，不依赖本方案其余部分 |
| 1 | 审计（本文档 §1） | 本文档 | 无 |
| 2 | 架构设计（本文档 §2，含 §2.11a 依赖策略结论、§2.15 规则补齐方案，均已确认） | 本文档 | Phase 1 |
| 3 | Core 迁移：`models.py`/`rules.py`/`risk.py` + 单元测试 | `src/storops/core/` | Phase 2 确认（即本次） |
| 4 | Platform Adapters：Linux/macOS（gdu/du/shutil/symlink）→ Windows（WizTree/robocopy/Junction + §2.13 原生 fallback），**同时按 §2.15 补齐 `rules/*.yaml` 的 Linux/macOS `path_patterns`**（不再单独排期） | `src/storops/platform/`、更新后的 `rules/*.yaml` | Phase 3 |
| 5 | CLI：9 个子命令 + `--json`/`--human`，退出码，stdout/stderr 纪律 | `src/storops/cli.py` | Phase 4 |
| 6 | 兼容层：`scripts/*.ps1` 降级为 wrapper，SKILL.md/README 同步更新调用方式。**与 Phase 5 同版本发布，100% 行为兼容是硬性验收标准（§2.10 第 3 点），不是可选项** | 更新后的 `scripts/`、`SKILL.md`、`README*.md` | Phase 5，二者同版本一起验收 |
| 7 | 测试：unit + integration（含 §2.10 第 3 点的"新旧路径 JSON 一致性"用例）+ platform，接入三平台 CI matrix | `.github/workflows/test.yml`、`tests/` | Phase 4-6 |
| 8 | 文档：README/SKILL.md/DESIGN.md/CHANGELOG 全量同步 | 文档 | Phase 7 通过 |

**当前状态**：Phase 1/2 已完成，且 §2.10（兼容策略）、§2.11a（Windows 依赖策略）、§2.15（Linux/macOS 规则
补齐方案）三处原本待用户决策的开放问题均已收敛为明确结论，不再需要额外确认。按用户要求（"先等以上所有内容都
更新完成，生成一个完整的方案，然后再进行执行"），**Phase 3 起的实际代码迁移在本文档定稿之后才开始**，作为
后续任务逐步推进并各自接受审查，不在一次改动里"机械重写所有代码"；Phase 0 的 hotfix 因为完全独立、成本极低，
可以随时单独发起,不需要等 v2 其余部分。

---

## 4. 决策收敛记录（原"悬而未决问题"，四项已全部确认）

| # | 原问题 | 结论 | 依据 |
|---|---|---|---|
| 1 | 兼容窗口长度 | **不设窗口期——v2 发布即 100% 向后兼容**，`scripts/*.ps1` wrapper 长期存在，不设自动移除时间点 | §2.10 |
| 2 | Windows optional 依赖用不用 `pywin32` | **不引入，默认纯标准库 + 按需 shell 原生命令**（方案 A） | §2.11a 三方案对比 |
| 3 | Linux/macOS 应用规则补齐排期 | **并入 Phase 4，不单独排期**，已给出 B/C 两类规则的具体 token 路径表 | §2.15 |
| 4 | 疑似路径拼接 bug 是否成立 | **经真机验证，不成立，已证伪**；但验证过程中发现了一个严重得多、已确认的真问题（§1.6.1：三个后端 `return @($list)` 在 PowerShell 7.4.6 上必炸），当前唯一真正待你决策的是下面这一条 | §1.6.1、§1.6.7 |

**唯一剩余的、真正需要你决定的事**：§1.6.1 发现的 `@(List[object])` PowerShell 引擎 bug，是否现在就单独开一个
Phase 0 hotfix（把三处 `return @($x)` 改成 `.ToArray()`）？这个改动完全独立于 v2 重写、成本很低（三行代码），
但它意味着现在就要动一次现有 `.psm1` 代码，与"Phase 3 之前不碰代码"的既定节奏不完全一致，所以没有替你默认
决定，留给你选：现在就修，还是留到 Phase 4 用 Python 重写掉这三个模块时一并解决（反正 Python 重写不会继承
这个 bug，晚修不会有额外风险，只是现有 PowerShell 版本在这之前对 Linux/macOS 用户来说 `scan` 相关功能一直
是坏的）。

除这一条以外，本文档不再有需要你决策的开放问题——按你的要求，下一步是等你确认这份完整方案后再进入 Phase 3
执行。

---

*本文档基于 2026-09-01 对 `tzzs/storops`（当前 worktree 分支 `tzzs/nerite`）的代码审计撰写，所有文件路径、
行号引用均对照仓库当前 HEAD（含 §1.8 列出的三处未提交改动，这三处改动未被本文档采纳或依赖）。§1.6.1/§1.6.2/
§1.6.4/§1.6.5/§1.6.7 标记"已现场验证"的结论，均已在同一天用真实 PowerShell 7.4.6（Linux x64，从
PowerShell 官方 GitHub Release 下载，非发行版仓库）对着仓库当前代码实际跑通/跑炸，不是基于代码阅读的推测；
验证过程安装了系统级 `libicu76`/`libicu-dev`（pwsh 运行所需的全球化库，属于常规、可逆的开发环境依赖，未对
仓库本身做任何改动）。*
