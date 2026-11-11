<div align="center">

# Paper2Patent

**面向科研成果转化的论文转专利工具：Prompt 模板 + AI Skill，辅助撰写中国发明专利申请文件与 FTO 防侵权撞库排查**

*Make AI Writing Better for Everyone*

[![Version: 0.0.4](https://img.shields.io/badge/Version-0.0.4-blue.svg)](CHANGELOG.md) [![License: MIT](https://img.shields.io/badge/License-MIT-green.svg)](LICENSE)

[快速开始](#快速开始) · [本仓库改进](#本仓库改进说明fork-演进) · [paper2patent Skill](#paper2patent-skill) · [FTO 撞库排查](#第八步fto-撞库排查智慧芽-mcp) · [Prompt 模板](#prompt-模板) · [使用须知](#使用须知) · [更新记录](CHANGELOG.md)

</div>

> **原始仓库与版权声明**
>
> - **上游原仓库**：[7toCR/paper2patent](https://github.com/7toCR/paper2patent)（© 2026-present `7toCR` · All Rights Reserved）
> - **当前维护仓库**：[Luxury-is-best/paper2patent](https://github.com/Luxury-is-best/paper2patent)（基于 `7toCR/paper2patent` 派生并扩展维护）
> - 本项目遵循原仓库声明与 [MIT License](LICENSE) 开源条款，保留原作者版权声明与原始来源链接。欢迎免费公开使用，引用或二次分发请同步注明原始来源（[7toCR/paper2patent](https://github.com/7toCR/paper2patent)）与本仓库改进来源。

## 为什么做这个项目

论文证明学术贡献，专利保护技术成果。但权利要求怎么布局、说明书写到什么程度才算充分公开、附图有哪些规范、**转化后的技术方案是否落入他人有效专利的保护范围（FTO 防侵权风险）**，对科研人员来说往往要从零摸索。本项目在原作者 `7toCR` 整理的论文转专利模板与工具基础上，进一步打通了**“论文转专利初稿 → 自动质检与排版 → 智慧芽（PatSnap）专利数据库 FTO 撞库排查”**全链路闭环，帮助你把论文转化为一份可直接交给专利代理师审核的申请草稿与防侵权排查报告。

## 本仓库改进说明（Fork 演进）

本仓库继承自 [7toCR/paper2patent](https://github.com/7toCR/paper2patent)（含原项目完整的五部分撰写规范、黑白附图生成、DOCX/PDF 导出、形式自检脚本及三大对话 Prompt 模板），并在此基础上完成了以下核心升级：

1. **新增第八步：FTO 自由实施（防侵权撞库）排查（`skills/paper2patent/fto/`）**
   - 在输出完专利申请文件 DOCX/PDF 后，**基于已提炼转化的专利技术方案（`patent.json` 的独立权利要求 1 与核心实施方式）**，接入智慧芽（PatSnap）专利平台 MCP 服务（`patent-platform`）执行防侵权撞库排查。
   - **每日 ≤100 篇额度硬性红线与漏斗式配额管控**：`fto/fto_patent_check.py` 内置零依赖原生 HTTP/SSE MCP 客户端与本地持久化配额账本（`~/.paper2patent_fto_quota.json`），采用“语义+布尔双路检索召回（按申请号 `apno` 去重）→ 相关性初筛 → Top 5–10 篇高危专利深拉权利要求全文与法律状态”的漏斗策略，单次任务严格控制在 **20–45 篇**唯一专利以内，超额自动拦截。
   - **全面覆盖原则 / 等同原则逐项比对（Claim Chart）**：严格区分**有效专利**（直接侵权风险）、**审中专利**（潜在授权预警）与**失效专利**（公有领域/现有技术抗辩），输出 `<名称>_FTO撞库排查报告.md` 与 `<名称>_FTO撞库排查报告.docx`，并针对中高风险障碍专利给出工程**规避设计建议（Design-Around）**与我方权利要求加固建议。
2. **全面汉化 `SKILL.md` 并升级为八步闭环工作流**
   - 将 `SKILL.md` 的技能描述、优秀标准、八步工作流（需求与输入接入 → 原始事实清单 → 发明挖掘与权利要求规划 → 撰写五部分与充分公开审计 → 附图绘制 → 组装质检与生成 → 交付 → FTO 撞库排查）、核心准则及常见场景全面转化为规范中文表述。
3. **拆分 Evals 评测体系为独立配套仓库 [`paper2patent-evals`](https://github.com/Luxury-is-best/paper2patent-evals)（`v0.0.1`）并与主仓库配合使用**
   - **双仓库解耦与联动**：将原 `skills/paper2patent/evals/` 拆分为独立 Git 仓库与独立 Skill `paper2patent-evals`，使 `paper2patent` 主包保持轻量纯净、避免常规撰写任务误触发评测流程。评测时，`paper2patent-evals` 通过 `--skill-dir`（或同级目录自动发现）加载指定版本 `paper2patent` 的 `check_patent_draft.py` 统一打分，并在 `summary.json` 中记录检查器路径与版本号。
   - **评测集扩展（`evals.json`）**：覆盖 5 篇代表性核心论文（补充必含机制 `must_have`、常见陷阱 `must_not_have`、专项结构断言 `structural_assertions`），并新增 `mode_evals` 覆盖 `human-in-loop`、`claims-only` 与 `review` 三类专项工作流模式。
   - **九维评分细则与评审模型配置（`rubric.md` / `judge_pairwise.py`）**：明确三条一票否决红线（捏造事实、脑补掩盖缺口、漏报重大新颖性/第25条客体风险），补齐 9 大评估维度的 1–5 分五档量表、单稿件 A/B/C/D 质量分级、LLM-as-a-Judge 双向位置交换盲评模板及 `openai` / `anthropic` / `self` 评审模型配置引导。
   - **13 项自动化结构与溯源门禁（`grade_structure.py`）**：升级五部分+五小节顺序与内嵌附图校验、数值溯源审计（自动剥离步骤号/段落号/附图标记，支持千分位、科学计数法与百分比换算匹配）及多轮运行稳定性统计（均值、标准差与波动项识别）。
4. **仓库结构精简与单包跨 Agent 分发**
   - 精简仓库根目录，移除特定 IDE 的冗余配置镜像，将运行脚本、参考规范与 `fto/` 撞库模块统一收敛于 `skills/paper2patent/`（配套评测套件独立为 `paper2patent-evals` 仓库），规范中间产物统一存放于 `reference/`、最终交付文档输出至 `out/`，并配置 `.gitignore` 忽略本地运行时产物（如 `skills/paper2patent/files/`、`reference/`、`out/`、`mcp.local.json`）。

## 能做什么

| 能力 | 说明 |
|---|---|
| 📝 五部分申请文本 | 说明书摘要、摘要附图、权利要求书、说明书、说明书附图 |
| ⚖️ 权利要求布局 | 独立权利要求只写必要技术特征，从属权利要求一项一机制；方法、装置、电子设备、存储介质成套 |
| 🤝 忠实原文 | 技术内容只来自论文；论文没给的信息标为【待补充】，不编造 |
| 📐 专利附图 | 黑白线条流程图、框图，支持分支、判断、回环和附图标记 |
| ✅ 自动检查 | 禁用词、单句号、引用关系、引用基础、摘要字数、附图编号与附图标记一致性等 |
| ⚠️ 风险与缺口 | 论文公开与新颖性、AI/算法发明的客体问题、充分公开核查与待补充材料清单 |
| 🛡️ FTO 撞库排查 | 接入智慧芽（PatSnap）MCP 数据库（每日 ≤100 篇额度管控），对独立权利要求执行全面覆盖/等同原则逐项特征比对（Claim Chart），生成 MD/DOCX 报告与规避设计建议 |
| 📊 双层基准评测 | 配套独立评测仓库 [`paper2patent-evals`](https://github.com/Luxury-is-best/paper2patent-evals)（13 项自动化结构/数值溯源门禁 + 9 维成对盲评量表），与主 Skill 配合保障迭代质量 |

撰写与排查依据：《专利法》（2020年修正）、《专利法实施细则》（2023年修订）、《专利审查指南》（2026年1月1日施行版）、《最高人民法院关于审理专利纠纷案件适用法律问题的若干规定》（全面覆盖原则与等同原则）。

## 快速开始

| 方式 | 适合 | 产出 |
|---|---|---|
| **paper2patent Skill**（推荐） | Codex、Kiro、Claude Code、WorkBuddy 等支持 Agent Skills 的工具，以及可上传文件夹的 Agent | Word/PDF 申请文件、黑白附图、撰写说明、**FTO 撞库排查报告（MD/DOCX）** |
| [**论文转专利 Flash**](chatbotprompt.md#论文转专利-flash) | 任意网页端大模型对话，快速出一版初稿 | 五部分纯文本 |
| [**论文转专利 Pro**](chatbotprompt.md#论文转专利-pro) | 任意网页端大模型对话，需要更细的撰写规范和自检 | 五部分纯文本 |
| [**专利附图生成（Gemini）**](chatbotprompt.md#专利附图生成gemini) | 已有专利文本，在网页端生成参考附图 | 附图图片 |

## paper2patent Skill

在支持 Agent Skills 的 AI 工具中，把“**读论文 → 判断公开状态 → 规划权利要求 → 撰写 → 画图 → 检查 → 生成 Word/PDF → 智慧芽 FTO 撞库排查**”整套八步流程交给 AI 完成。整个可分发单元都在 `skills/paper2patent/` 中，不依赖特定 Agent 的仓库配置目录。

### 安装

```bash
git clone https://github.com/Luxury-is-best/paper2patent.git
cd paper2patent
```

| 工具 | 安装方式 |
|---|---|
| Codex | macOS/Linux：`cp -R skills/paper2patent ~/.codex/skills/`<br>Windows：`Copy-Item -Recurse -Force .\skills\paper2patent "$env:USERPROFILE\.codex\skills\"` |
| Kiro / Claude Code / 其他支持 Agent Skills 的工具 | 将 `skills/paper2patent/` 导入该工具的 Skills 目录；具体目标路径按工具版本的说明设置 |
| 豆包、ChatGPT 等支持文件上传的 Agent | 上传 `paper2patent.zip`，并明确要求先读取根目录中的 `SKILL.md`；是否自动触发取决于该 Agent 是否支持 Skills 规范 |

只分发主 skill 时，在仓库根目录执行：

```bash
cd skills
zip -r paper2patent.zip paper2patent \
  -x 'paper2patent/files/*' 'paper2patent/fto/mcp.local.json' '*/__pycache__/*' '*.pyc'
```

若需同时打包配套的独立评测 skill（位于同级仓库 `../paper2patent-evals`）：

```bash
cd ..
zip -r paper2patent-evals.zip paper2patent-evals \
  -x 'paper2patent-evals/.git/*' 'paper2patent-evals/inputs/*' 'paper2patent-evals/runs/*' 'paper2patent-evals/judge.config*.json' '*/__pycache__/*' '*.pyc'
```

**版本与升级**（当前版本：`0.0.4`，唯一记录于 `skills/paper2patent/VERSION`；配套评测仓库 `paper2patent-evals` 当前版本：`0.0.1`）：升级时可先对比本地已安装目录中的 `VERSION` 版本号（或运行 `python skills/paper2patent/fto/fto_patent_check.py --version`），拉取最新代码或指定版本 Tag 后，**先删除旧目录再复制**，避免已删除的旧文件残留：

```bash
# 查看当前已安装 Skill 版本
cat ~/.codex/skills/paper2patent/VERSION

# 拉取更新（或切换到指定版本标签：git checkout v0.0.4）并重新安装
git pull
rm -rf ~/.codex/skills/paper2patent && cp -R skills/paper2patent ~/.codex/skills/
```

### 使用

直接用自然语言描述任务即可（Codex 中也可以输入 `$paper2patent` 调用）：

- “使用 paper2patent skill，将这篇论文生成中国发明专利申请书 Word、PDF，并完成智慧芽 FTO 撞库排查。”
- “先帮我梳理发明点和权利要求布局，确认后再写全文并做 FTO 排查。”
- “对已生成的 `patent.json` 技术方案执行智慧芽 FTO 防侵权撞库排查，生成 FTO 排查报告。”
- “请基于论文原文检查这份专利草稿是否有编造内容。”
- “根据已生成的权利要求书和说明书生成黑白专利附图。”
- “我的论文已经上传 arXiv 了，还能申请专利吗？”

### 输出

Skill 的最终回复采用**极简输出规范（路由通道 + 交付产物清单 + 一句话核心摘要与提醒）**：

- **路由通道**：明确标示本次执行的路由通道（`direct` 默认全流程通道，或 `human-in-loop` / `text-only` / `claims-only` / `review` / `fto-only`）。
- **交付产物**：
  - `<名称>_专利申请文件.docx / .pdf`：只含五部分申请文本，每部分另起一页，附图下只标“图N”。
  - `<名称>_专利申请文件_撰写说明.docx`：供申请人和代理师审核，包括待补充材料清单、充分公开核查、公开与新颖性风险、权利要求布局与论文出处对照、附图来源、自动检查结果。
  - `<名称>_FTO撞库排查报告.md / .docx`：基于智慧芽（PatSnap）专利数据库检索与独立权利要求逐项比对（Claim Chart）生成的防侵权排查报告（**注：若未配置智慧芽 MCP 密钥，Skill 会明确向用户报告“当前没有专利数据库”，仅输出离线特征拆解表 `fto_features.json` 并引导配置密钥**）。
  - 各附图的 SVG 和 PNG 文件。

运行环境：Python 3.9+；画附图需要 Pillow 和一种中文字体（Windows/macOS 自带字体或 Noto CJK 均可，也可以用环境变量 `PATENT_CJK_FONT` 指定）；导出 PDF 需要 LibreOffice；执行第八步 FTO 撞库排查需网络可访问 `fto/mcp.json` 中配置的智慧芽 MCP 服务端点。

### 第八步：FTO 撞库排查（智慧芽 MCP）

使用前需配置智慧芽 MCP 密钥（任选其一，均不会被 Git 提交；若未配置，Skill 也会在执行时主动引导你提供）：
- **对话内引导配置（推荐）**：直接在对话中把智慧芽 MCP Token（如 `pp_...`）发给 Agent，或运行以下命令一键写入本地隔离文件 `skills/paper2patent/fto/mcp.local.json`（已被 `.gitignore` 忽略）：
  ```bash
  python skills/paper2patent/fto/fto_patent_check.py set-token <你的智慧芽MCP密钥>
  ```
- **环境变量配置**：设置 `export PATENT_MCP_TOKEN="你的密钥"`（Windows PowerShell：`$env:PATENT_MCP_TOKEN="你的密钥"`）。
- **手动本地文件配置**：将 `skills/paper2patent/fto/mcp.json` 复制为 `skills/paper2patent/fto/mcp.local.json` 并填入密钥。

在完成步骤 1–6 输出专利申请文件 DOCX/PDF 后，Skill 会基于已提炼的 `patent.json`（独立权利要求 1 及核心实施方式）继续执行第八步 FTO 撞库排查（详见 `skills/paper2patent/fto/fto-workflow.md`）：

```bash
# 1. 检查智慧芽 MCP 密钥配置状态与今日剩余额度（硬性上限 100 篇/日）
python skills/paper2patent/fto/fto_patent_check.py quota

# 2. 从 patent.json 拆解核心技术特征，执行智慧芽语义+布尔双路检索并拉取 Top 候选专利权利要求与法律状态
python skills/paper2patent/fto/fto_patent_check.py auto-screen patent.json -o out/fto_candidates.json

# 3. 完成全面覆盖/等同原则逐项特征比对（写入 out/fto_analysis.json）后，一键生成 MD 与 DOCX 报告
python skills/paper2patent/fto/fto_patent_check.py report out/fto_analysis.json \
  --md-output out/<名称>_FTO撞库排查报告.md \
  --docx-output out/<名称>_FTO撞库排查报告.docx
```

<details>
<summary><b>目录结构</b></summary>

```text
skills/paper2patent/                 可独立打包和分发的完整 Skill（v0.0.4）
├── VERSION                          Skill 版本号文件（当前：0.0.4）
├── SKILL.md                         八步完整工作流与核心规则（中文规范版）
├── agents/
│   └── openai.yaml                  Agent 默认提示词与元数据配置
├── references/
│   ├── patentability-and-disclosure.md     论文公开与新颖性、AI/算法发明的客体与充分公开
│   ├── patent-drafting-standard.md         五部分撰写规范、论文→专利对照、充分公开核查
│   ├── claims-drafting.md                  权利要求特征分拣、从属权利要求层次、用语规则
│   ├── claims-and-specification-rules.md   权利要求与说明书补充规则参考
│   ├── drawing-generation.md               附图规范与节点/连线写法
│   ├── document-generation.md              结构化内容格式与生成流程
│   ├── text-conversion-workflow.md         纯文本转换工作流参考
│   ├── input-requirements.md               输入要求与缺口处理
│   └── quality-checklist.md                交付前检查清单
├── scripts/
│   ├── check_patent_draft.py        自动形式与范围检查
│   ├── generate_patent_drawings.py  黑白专利附图（SVG + PNG）
│   ├── generate_patent_docx.py      申请文件与撰写说明（DOCX）
│   ├── export_patent_pdf.py         PDF 导出与逐页预览
│   └── patent_common.py             公共工具与字体/排版辅助函数
├── fto/                             第八步：智慧芽（PatSnap）FTO 撞库排查模块
│   ├── fto-workflow.md              FTO 撞库排查四步法、额度红线与全面覆盖/等同判定规范
│   ├── fto_patent_check.py          零依赖 MCP 客户端、每日 100 篇额度管控、检索与 MD/DOCX 报告生成脚本
│   └── mcp.json                     智慧芽 patent-platform MCP 服务配置
└── assets/
    └── example_patent_content.json  完整的结构化内容示例

# 配套评测套件位于独立仓库 paper2patent-evals（建议与本仓库并列克隆于同一父目录）：
# ../paper2patent-evals/
# ├── SKILL.md                       评测 Skill 入口与评审模型引导规范
# ├── VERSION                        评测套件版本号（当前：0.0.1）
# ├── evals.json                     5 篇核心基准论文 + 3 类专项模式评测配置
# ├── rubric.md                      九维 1–5 分评分细则、一票否决红线与双向盲评模板
# ├── grade_structure.py             13 项自动化结构与数值溯源门禁脚本（通过 --skill-dir 调用主 Skill 检查器）
# └── judge_pairwise.py              LLM-as-a-Judge / Self 评审模型配置与成对盲评骨架
```

</details>

## Prompt 模板

面向不使用本地 Agent / CLI 环境、希望直接在**网页端大模型聊天框**（如 DeepSeek、ChatGPT、Claude、Kimi、豆包、Gemini 网页版）中复制粘贴使用的用户，仓库提供了三套完整的现成对话 Prompt 模板，已统一抽离并维护于独立文件 [**`chatbotprompt.md`**](chatbotprompt.md) 中：

| 模板名称 | 路径入口 | 简介 |
|---|---|---|
| **论文转专利 Flash** | [`chatbotprompt.md#论文转专利-flash`](chatbotprompt.md#论文转专利-flash) | 精简快速版：提炼“区别点 → 技术问题 → 技术效果”三要素闭环与核心标点/禁词规则，适合在对话框中快速生成五部分纯文本初稿。 |
| **论文转专利 Pro** | [`chatbotprompt.md#论文转专利-pro`](chatbotprompt.md#论文转专利-pro) | 完整进阶版：包含五部分详细撰写框架、三层保护范围布局方法论、四阶段内部执行流与输出前强制自检清单。 |
| **专利附图生成（Gemini）** | [`chatbotprompt.md#专利附图生成gemini`](chatbotprompt.md#专利附图生成gemini) | 网页端生图版：配合 Gemini 图像生成能力，根据论文与专利文本绘制黑白线条流程图与系统框图。 |

> 💡 **说明**：[`skills/paper2patent/SKILL.md`](skills/paper2patent/SKILL.md) 及其配套规范与脚本已完整覆盖并升级了上述全部 Prompt 的能力（修正了旧模板格式偏差，并新增确定性代码自检、矢量附图生成、Word/PDF 排版与智慧芽 FTO 撞库排查）。需要完整交付物时请优先使用上方的 [paper2patent Skill](#paper2patent-skill)。

## 使用须知

- 生成内容（含专利申请文件与 FTO 撞库排查报告）是供专利代理师审核的草稿与参考，不构成正式法律意见；提交申请或产品商业化落地前，请务必由专业专利代理师审核并开展正式法律尽职调查。
- **智慧芽 MCP 额度纪律**：第八步 FTO 撞库排查默认通过 `fto/fto_patent_check.py` 将智慧芽接口调用严格限制在**每日 100 篇唯一专利**以内（单次排查建议 20–45 篇），请勿手动绕过本地配额记账或盲目全量拉取说明书全文。
- **申请要赶在论文公开之前**：arXiv、会议或期刊出版、项目主页、开源代码、公开报告都可能构成公开；国际学术会议一般不能当然适用 6 个月宽限期。
- 不要把未公开的论文、生成的申请文件或个人信息提交到公开仓库。

## 更新记录与评估

完整版本历史请查阅 [**CHANGELOG.md**](CHANGELOG.md)：

- **v0.0.4 (2026-11-11)**：将评测套件反向拆分为独立配套仓库 [`paper2patent-evals`](https://github.com/Luxury-is-best/paper2patent-evals)（`v0.0.1`），实现双仓库联动与评测标尺解耦；统一规范中间产物（`reference/`）与最终交付产物（`out/`）目录严格隔离；版本号唯一收敛至 `skills/paper2patent/VERSION`。
- **v0.0.3 (2026-11-08)**：规范极简最终输出（路由通道 + 交付产物清单 + 一句话核心摘要与提醒）；未配置智慧芽 MCP 密钥时强制明确报告“当前没有接入专利数据库”，严禁虚构对比专利号。
- **v0.0.2 (2026-11-08)**：主 Skill 增加智慧芽 MCP 密钥状态核查与交互引导，`fto_patent_check.py` 新增 `mcp_auth` 脱敏诊断与 `set-token` 子命令（安全写入 `.gitignore` 忽略的 `fto/mcp.local.json`）。
- **v0.0.1 (2026-11-08)**：Fork 维护版首次正式发布——新增第八步智慧芽（PatSnap）MCP FTO 防侵权撞库排查（每日 ≤100 篇额度管控、全面覆盖/等同原则 Claim Chart 比对、MD/DOCX 报告生成）、全面汉化 `SKILL.md` 八步闭环工作流、升级 Evals v2.0 评测体系。
- **上游基石（2026-10）**：继承自原作者重构的五部分专利撰写标准、新颖性与 AI 算法客体合规审查、黑白矢量附图布局脚本、DOCX/PDF 生成与 20+ 项形式自检脚本。

**相关规范与评测入口**：
- **FTO 撞库排查规范**：[`skills/paper2patent/fto/fto-workflow.md`](skills/paper2patent/fto/fto-workflow.md)
- **独立基准评测套件**：[`Luxury-is-best/paper2patent-evals`](https://github.com/Luxury-is-best/paper2patent-evals)（含 5 篇代表性核心论文 + 3 类专项模式评测 `evals.json`、13 项自动化结构与数值溯源门禁 `grade_structure.py`、9 维成对盲评量表 `rubric.md` 与 `judge_pairwise.py`；建议克隆于同级目录 `../paper2patent-evals/` 配合使用）

## 致谢

- 特别致谢 **CR7**（[@7toCR](https://github.com/7toCR)）开源的原始项目 [7toCR/paper2patent](https://github.com/7toCR/paper2patent)，为本项目奠定了扎实的论文转专利撰写规范、黑白矢量附图生成、文档导出与自动形式检查基础。

## Star History

[![Star History Chart](https://api.star-history.com/svg?repos=Luxury-is-best/paper2patent&type=Date)](https://star-history.com/#Luxury-is-best/paper2patent&Date)
