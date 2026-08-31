# StorOps

[![skills.sh](https://skills.sh/b/tzzs/storops)](https://skills.sh/tzzs/storops)

[English](README.md) | **简体中文**

**面向 AI Agent 的存储运维能力。**

> 看清空间去了哪里,理解为什么,把重要的东西挪走,把没用的东西清掉。

StorOps 是一个 agent skill(`storops`),让 Claude Code、Codex、OpenCode 等
AI coding agent 能够安全地理解并管理本地存储空间(目前 Windows 支持最成熟;
Linux/macOS 支持是新加的,详见下方[当前状态](#当前状态))。

它**不是**又一个磁盘分析器,也**不是**又一个磁盘清理工具。WizTree 已经能回答
"什么占用了空间"这个问题;StorOps 回答的是后续的问题:*这是什么、为什么在这里、
能不能删、要不要迁移、迁移到哪里、怎么安全地做、以及做完之后如何验证。*

```text
Discover → Understand → Diagnose → Recommend → Plan → Execute → Verify
 WizTree     Identify     Analyze
```

完整的产品设计参见 [`docs/DESIGN.md`](docs/DESIGN.md),agent 行为契约参见
[`SKILL.md`](SKILL.md)。

## 当前状态

MVP。Windows 是目前最成熟的目标平台,以 [WizTree](https://diskanalyzer.com/)
作为存储发现后端——StorOps 从不重新实现磁盘/MFT 扫描,也从不驱动 WizTree 的
GUI(不做自动化、截图或 OCR):它只在命令行调用 `WizTree64.exe` 并解析其 CSV
导出结果。Linux/macOS 支持是新加的,基于 [gdu](https://github.com/dundee/gdu)
(找不到 gdu 时回退到系统自带的 `du`),走同一套 scan-backend 接口——见
[`docs/DESIGN.md`](docs/DESIGN.md) §4a。AI 模型/应用/缓存的识别规则
(`rules/ai-models.yaml`、`applications.yaml`、`caches.yaml`)目前仍只有
Windows token;只有关键系统路径规则(`rules/windows.yaml`/`linux.yaml`/
`macos.yaml`)已经做了按平台区分。

## 环境要求

- Windows 上用 PowerShell 5.1+(Windows PowerShell),Linux/macOS 上用
  PowerShell 7+。
- **Windows**:NTFS 卷,加上已安装的 [WizTree](https://diskanalyzer.com/)
  (`WizTree64.exe` 需在 `PATH` 中、位于标准安装目录,或通过
  `$env:STOROPS_WIZTREE_PATH` 指定路径)。管理员权限可选但推荐:WizTree 的
  `/admin=1` 模式直接读取 NTFS MFT,比标准扫描快得多、也更完整。
- **Linux/macOS**:推荐安装 [gdu](https://github.com/dundee/gdu)(`brew
  install gdu` / `apt install gdu`,或参见其安装文档)以获得并行、快得多的
  扫描;找不到 `gdu` 时 StorOps 会自动回退到系统自带的 `du`(并打印一次性
  警告——`du` 在大目录树上明显更慢)。如果 `gdu` 不在 `PATH` 上,可以通过
  `$env:STOROPS_GDU_PATH` 指定具体路径。任何情况下都不需要、也不会自动提权。

## 安装

StorOps 是一个标准的 agent skill:一个根目录带有 `SKILL.md` 的目录,agent 依据
其 name/description 自动发现并调用,而非以 slash command 的形式手动触发。无需
构建步骤,也无需安装依赖——agent 会读取 `SKILL.md` 来判断何时使用该 skill,
然后直接调用 `scripts/` 下的 PowerShell 脚本。运行时依赖(Windows 上是
WizTree,Linux/macOS 上是 gdu/du)详见上方[环境要求](#环境要求)。

### 直接让 Agent 帮你安装(推荐)

把下面这段话复制粘贴进任意 AI coding agent 的对话框(Claude Code、Codex、
Cursor 等),让它自己判断当前环境并选择合适的安装方式:

```text
帮我安装 agent skill "storops",仓库地址是 https://github.com/tzzs/storops,
用适合当前 agent 的方式装好,然后确认已加载。
```

### 任意支持 skill 的 agent —— `npx skills add`

[`skills`](https://www.npmjs.com/package/skills) 是一个社区维护的 CLI 工具,
可以把任意公开 GitHub 仓库中的 `SKILL.md` 安装到 agent 的 skills 目录下
(`.claude/skills/`、`.agents/skills/` 等):

```bash
npx skills add tzzs/storops
# 若要在这台机器上的所有项目中都可用:
npx skills add tzzs/storops -g
```

### Claude Code —— 插件市场

仓库自带 `.claude-plugin/marketplace.json`,可以直接在 Claude Code 内部添加为
市场并安装:

```text
/plugin marketplace add tzzs/storops
/plugin install storops@storops
```

### Codex —— skill-installer

Codex 自带官方的 `skill-installer` skill,可以从任意 GitHub URL 安装 skill。
在 Codex 内部执行:

```text
$skill-installer install https://github.com/tzzs/storops
```

### 手动安装

直接克隆到 agent 会扫描的 skills 目录下:

```bash
# 项目级(仅当前仓库可用)
git clone https://github.com/tzzs/storops.git .claude/skills/storops

# 个人级(所有项目可用)
git clone https://github.com/tzzs/storops.git ~/.claude/skills/storops
```

## 目录结构

```text
SKILL.md            agent 行为契约(何时/如何使用该 skill)
docs/DESIGN.md       完整设计文档(意图与范围的唯一事实来源)
rules/               声明式的识别与风险规则(YAML),按平台拆分的关键路径
                     文件 + 共享的应用/缓存规则
scripts/             PowerShell 入口脚本,每个能力对应一个
scripts/lib/         共享 PowerShell 模块(规则匹配、风险分级、
                     plan/verification 辅助逻辑)
scripts/lib/ScanBackend.psm1   选择当前平台的 scan backend(docs/DESIGN.md §4a)
scripts/lib/backends/         WizTree.psm1(Windows)、Gdu.psm1 + Du.psm1(Linux/macOS)
tests/               规则引擎与 CSV 解析的冒烟测试
```

## 安全模型

每个能力都严格属于以下三层之一:

| 层级 | 能力 | 是否需要确认 |
|---|---|---|
| **只读(Read)** | scan、inspect、search、identify、analyze | 不需要——可自由运行 |
| **计划(Plan)** | cleanup-plan、migrate-plan | 不需要——只生成计划,不动任何东西 |
| **写入(Write)** | migrate-execute、cleanup-execute、创建 junction | **必须**确认,没有例外 |

任何删除、移动、重命名或重新配置的操作,在向用户展示明确的、逐项列出的计划
之前绝不会执行;`CRITICAL` 风险等级的路径(Windows、`Program Files`、未知
系统路径、用户文档等)永远不会被作为自动删除的对象提供。

## 快速开始(agent 驱动)

一个遵循 `SKILL.md` 的 agent 通常会这样操作(下面示例用的是 Windows 路径;
在 Linux/macOS 上换成 POSIX 路径即可,例如 `-Path /`、`-Path ~/.cache`——
`scan.ps1`/`search.ps1` 在那两个平台上会自动默认 `/`):

```powershell
# 1. 只读:先看全局概况
pwsh scripts/scan.ps1 -Path C:\

# 2. 只读:深入某个体积大、暂未识别的目录
pwsh scripts/inspect.ps1 -Path C:\Users\me\AppData\Local

# 3. 只读:为找到的内容赋予具体含义
pwsh scripts/identify.ps1 -Path C:\Users\me\.lmstudio\models

# 4. 仅生成计划:构建逐项列出、按风险分级的清理计划
pwsh scripts/cleanup-plan.ps1 -MaxRisk low

# 5. 写入操作,仅在用户确认第 4 步的计划之后执行
pwsh scripts/cleanup-execute.ps1 -PlanFile .\storops-cleanup-plan.json -Confirm

# 6. 仅生成计划:为一个体积大、可迁移的目录构建迁移计划
pwsh scripts/migrate-plan.ps1 -Path C:\Users\me\.lmstudio\models -Destination E:\AI\LMStudio\models

# 7. 写入操作,仅在用户确认第 6 步的计划之后执行——且总会进行验证
pwsh scripts/migrate-execute.ps1 -PlanFile .\storops-migrate-plan.json -Confirm

# 8. 只读:随时回头复查迁移结果
pwsh scripts/verify.ps1 -ResultFile .\storops-migrate-result.json
```

改动历史见 [`CHANGELOG.md`](CHANGELOG.md)。

## 许可证

MIT——详见 [`LICENSE`](LICENSE)。
