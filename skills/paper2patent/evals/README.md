# paper2patent 评估方案（Evaluation Suite v2.0）

本目录提供一套完整的**两层基准评测体系**（自动化结构门禁 + 九维成对盲评），用于客观判断对 `paper2patent` 技能的某次修改是否真正提高了中国发明专利申请初稿的实质撰写质量，而不只是让文件“表面看起来更像专利”。

> **核心原则**：只跑一篇论文、每个版本跑一次的对比，只能说明“这次哪份产物碰巧更好”，不能说明版本稳定性，更不能说明专利保护范围与充分公开水平。任何技能提示词、参考规范或脚本的改动，均须通过本方案的回归测试与多轮对比评测。

---

## 1. 实验控制条件（Controlled Setup）

为确保评测差异可唯一归因于技能版本（Skill Version），必须严格控制以下变量：

1. **同模型同预算**：新旧版本（`baseline` vs. `candidate`）必须使用完全相同的底层大模型、温度参数、最大输出 token 预算、相同输入文件与完全一致的用户提示词（见 `evals.json`）。
2. **环境与引用隔离**：旧版只能在独立工作目录中加载旧版自身的 `SKILL.md`、`references/` 和 `scripts/`，严禁混入新版新增的参考文档或校验脚本。
3. **运行元数据记录**：每次运行在产物目录保存 `run_meta.json`，记录：
   - `skill_commit` / `branch`
   - `model_id`
   - `eval_id` 与 `run_index`（`1..3`）
   - `wall_time_sec`、`input_tokens`、`output_tokens`
   - 运行过程中的工具调用日志（确认未手写外部绕过脚本）

---

## 2. 评测基准集（`evals.json`）

### 2.1 五篇核心全流程评测集（`evals`）

覆盖人工智能与计算机领域最具代表性的五类发明场景及高频撰写陷阱：

| Eval ID | 基准论文 | 技术领域 | 核心考察点与常见失败陷阱 |
|---|---|---|---|
| `multi-agent-llm` | arXiv:2308.00352 (MetaGPT) | 多智能体 LLM 协同系统 | **权1概括与闭环附图**：考察是否将软件公司五种具体岗位名称（产品经理/架构师等）下沉至从属权项，权1聚焦 SOP 结构化通信产物与可执行反馈机制；流程图是否画出测试失败返回修订回路（`F10`）；提示词与检查准则是否列入充分公开缺口。 |
| `vision-architecture` | arXiv:2103.14030 (Swin Transformer) | 视觉骨干网络架构 | **模型结构公开与参数分拣**：考察 Patch Merging 层级下采样、连续模块中规则窗口（W-MSA）与移位窗口（SW-MSA）交替连接关系；具体超参数（$M=7, C=96$, 层数比）是否下沉；循环移位与掩码自注意力是否独立成项；多任务实测指标是否零误差。 |
| `audio-generation` | arXiv:2301.12503 (AudioLDM) | 跨模态音频生成与扩散模型 | **训练/推理非对称流程与分支支撑**：考察是否清晰区分“仅用音频嵌入训练 LDM”与“用文本嵌入引导推理”的非对称机制；VAE、CLAP、LDM、声码器连接关系；上位概括各分支在说明书中的实施例支撑及证据边界。 |
| `systems-efficiency` | arXiv:2205.14135 (FlashAttention) | 系统算子与硬件感知优化 | **《专利法》第25条技术属性**：考察是否将分块加载（Tiling）、在线 Softmax 动态重缩放、反向传播片上重计算与计算机多级存储结构（HBM 主存 vs. 片上 SRAM 缓存）及 IO 访存复杂度降低深度绑定，避免写成纯数学矩阵推导。 |
| `forecasting-subject-matter` | arXiv:1912.09363 (TFT) | 多步时间序列预测 | **公开状态核查与第25条客体风险管控**：提示词故意不声明“假设未公开”，考察是否主动预警 2019 年论文已公开构成现有技术（《专利法》第22/24条）；是否识别零售/金融预测受经济规律支配的智力活动驳回风险，并依托论文中的电网负荷、交通占用率等工程物理信号场景构建技术方案。 |

### 2.2 三类专项工作流模式评测（`mode_evals`）

除默认的 `direct` 全流程模式外，`evals.json` 中的 `mode_evals` 专项考察其他关键交互模式：

- `mode-human-in-loop`（`human-in-loop` 模式）：考察在起草正文前是否先输出公开状态核查、发明点链条及权利要求1特征分拣表（A/B/C 三类），并等待用户确认，不提前生成 DOCX/PDF。
- `mode-claims-only`（`claims-only` 模式）：考察快速起草权利要求树、`claim_plan` 与 `support_map`，且输出的权利要求通过 `check_patent_draft.py` 检查（0 错误、0 范围警告）。
- `mode-review-audit`（`review` 模式）：考察根据源论文对已有草稿进行机械校验与逐项忠实度/范围审计，精准定位篡改数值、过度上位或权1过窄问题。

### 2.3 输入准备

将论文 PDF 下载至 `<skill>/evals/inputs/`，并使用 `pdftotext -layout` 生成同名 `.txt` 供数值溯源审计使用：

```bash
mkdir -p <skill>/evals/inputs
# 下载 PDF 后生成同名文本文件
for pdf in <skill>/evals/inputs/*.pdf; do
  pdftotext -layout "$pdf" "${pdf%.pdf}.txt"
done
```

> `inputs/` 和 `runs/` 已配置在 `.gitignore` 中。本地测试的未公开交底书或论文只放在本地 `inputs/`，切勿提交入库。

---

## 3. 运行次数与目录规范

- **每篇论文、每个版本至少独立运行 3 次**（`run-1`, `run-2`, `run-3`），以评估生成稳定性（方差）。
- **标准目录结构**：

```text
<skill>/evals/runs/
└── <eval-id>/                  # 例如 multi-agent-llm
    ├── old/                    # 基线版本 (baseline)
    │   ├── run-1/
    │   │   ├── patent.json
    │   │   ├── <名称>_专利申请文件.docx
    │   │   ├── <名称>_专利申请文件_撰写说明.docx
    │   │   ├── <名称>_专利申请文件.pdf
    │   │   ├── final_message.md
    │   │   ├── run_meta.json
    │   │   └── grading.json    # 由 grade_structure.py 自动生成
    │   ├── run-2/
    │   └── run-3/
    └── new/                    # 待测新版 (candidate)
        ├── run-1/
        ├── run-2/
        └── run-3/
```

---

## 4. 两层评估执行流程

### 第一层：自动化结构与溯源门禁（`grade_structure.py`）

运行 `grade_structure.py` 对单次或批量运行目录执行 13 项机械与结构断言：

```bash
# 方式1：显式指定论文文本路径
python <skill>/evals/grade_structure.py \
  --source <skill>/evals/inputs/2308.00352.txt \
  --eval-id multi-agent-llm \
  --summary-json <skill>/evals/runs/multi-agent-llm/new/summary.json \
  <skill>/evals/runs/multi-agent-llm/new/run-*

# 方式2：若目录路径含 eval-id 且 inputs/<arxiv>.txt 已存在，脚本可自动匹配源文本与专项断言
python <skill>/evals/grade_structure.py <skill>/evals/runs/multi-agent-llm/new/run-*
```

**自动化检查的 13 项核心指标：**
1. **核心交付产物齐全**：申请文件 DOCX、撰写说明 DOCX、申请文件 PDF、结构化 JSON 均已生成。
2. **申请文件五部分与五小节结构完整**：按序包含 `说明书摘要` $\rightarrow$ `摘要附图`（含内嵌图像）$\rightarrow$ `权利要求书` $\rightarrow$ `说明书`（含 `技术领域`、`背景技术`、`发明内容`、`附图说明`、`具体实施方式` 五小节）$\rightarrow$ `说明书附图`（内嵌图像数与 JSON 附图数严格一致）。
3. **检查脚本 0 错误**：`check_patent_draft.py` 返回 0 个 `ERROR`。
4. **无保护范围与编稿备注类警告**：无 `C20–C25`（代码变量名、不当否定限定、权1并列堆砌/超长、从属权项合并多机制、绝对化措辞）、`D08`（说明书混入编稿说明）、`F10`（含校验/迭代机制但附图无判断节点或回环）。
5. **无引用基础、语域与支撑核查类警告**：无 `C14`（缺少“所述X”先行词基础）、`D03–D04`（论文腔/商业宣传语）、`F03–F04/F07`（附图未结合说明、步骤号未出现）、`S01–S04`（缺失 `support_map` 或 `notes.sufficiency`）。
6. **保护客体布局完整**：权利要求书涵盖方法 + 装置/系统 + 电子设备 + 计算机可读存储介质。
7. **申请文件正文纯净**：不含 `来源论文`、`撰写说明`、`待补充材料清单`、`充分公开核查`、`support_map` 等非申请内容或 `本文提出`/`本论文` 等论文自称。
8. **说明书附图图注规范**：`说明书附图` 下除图片外仅有“图1”、“图2”等标准图号。
9. **撰写说明交接与溯源齐备**：《撰写说明》包含置顶的 `待补充材料清单`（含权属、公开情况、现有技术检索）、`充分公开核查` 表、`权利要求—论文出处对照` 表，且 `support_map` 覆盖全部权利要求。
10. **交付消息（`final_message.md`）合规**：包含草稿/非法律意见声明、待补充清单提示、公开风险说明及专利代理师审阅与现有技术检索建议。
11. **未编写外部绕过脚本**：未在运行目录私自编写绕过内置绘图/字体校验的脚本。
12. **技术数值可追溯至源论文**：剥离专利结构编号（如 `S101`、`[0001]`、附图标记 `301`、权利要求序号及 `【待补充】` 占位）后，正文中所有技术数值均能在论文 `.txt` 中找到依据（支持千分位、科学计数法及小数/百分比换算匹配）。
13. **论文专项结构断言（`structural_assertions`）**：执行 `evals.json` 中针对特定论文配置的断言（如 `multi-agent-llm` 附图必须含闭环回路、权1不得硬编码具体软件岗位名称；`forecasting-subject-matter` 必须在风险说明与交付消息中预警“已公开/现有技术/新颖性”与“第25条/智力活动/经济规律”客体风险）。

> **注意**：自动化结构检查只是**准入门槛（Gate）**，100% 通过仅代表格式合规、无明显机械缺陷，不代表权利要求概括得当或说明书公开充分。

---

### 第二层：九维实质质量盲评（`rubric.md`）

对通过结构检查的产物，严格按照 [`rubric.md`](file:///Users/lxy/Documents/ChatGPT/paper2patent/skills/paper2patent/evals/rubric.md) 的 **9 个维度（1–5 分量表）** 与 **红线规则** 进行成对盲评：

1. **原文忠实度与可追溯性**（底线维度，捏造数据/脑补缺口直接 1 分一票否决）
2. **独立权利要求 1 的保护范围与特征分拣**（核心维度：A/B/C 三类特征分拣与上位支撑）
3. **从属权利要求的防线层次与颗粒度**（核心维度：一项一机制、按价值排序与递进引用）
4. **保护客体布局与《专利法》第25条技术属性**
5. **说明书充分公开与支撑关系**（底线维度：四要素公开、训练/推理细节、核心机制完整实例）
6. **技术效果与证据边界**（因果逻辑链、实测数值一致、保留消融与场景边界）
7. **专利附图质量与机制表达**（反馈闭环、多源输入接入点、标记一致性）
8. **申请文件纯净度与正文/附件分离**（保留技术澄清与证据边界，剥离编稿元说明）
9. **材料缺口审计与风险交接清单**（底线维度：精确到权项特征的缺口审计与风险预警）

**成对盲评配对与防偏要求：**
- 每篇论文取新旧版本各 3 次同序号运行配对（`old/run-1` vs `new/run-1` 等），每篇论文至少评 **3 对**（5 篇论文共 15 对）。
- 使用大模型评审时，每对稿件必须按 `rubric.md` 第四节模板执行**双向位置交换盲评**（Pass 1: `A=old, B=new`；Pass 2: `A=new, B=old`），两次结论不一致的维度强制记为**“持平（Tie）”**。

---

## 5. 版本胜出判定规则与汇报要求

### 5.1 判定“新版优于旧版”必须同时满足以下 4 条准则：

1. **零红线违例**：新版在全部运行中未触发任何一票否决项（无捏造事实、无脑补掩盖缺口、无重大新颖性/第25条客体风险漏报）。
2. **底线维度不退化**：在 **维度 1（忠实度）**、**维度 5（充分公开）**、**维度 9（缺口交接）** 三个底线维度上，新版平均分不低于旧版，且在全部配对中评为“新版更好”或“持平”的次数不少于“新版更差”的次数（即 `Win + Tie >= Loss`，且 `Loss` 仅允许为非红线的微小瑕疵）。
3. **核心与整体维度净胜出**：在 **维度 2（权1概括）** 与 **维度 3（从属权项层次）** 上净胜场次 $\ge 0$（`Win >= Loss`），且在全部 9 个维度中，新版在多数论文上净胜出的维度数多于净落败的维度数。
4. **结构门禁通过率与稳定性不下降**：新版在 `grade_structure.py` 的平均通过率与完全通过运行占比（`fully_passing_runs`）不低于旧版。

### 5.2 评测报告标准格式

每次评测完成后，按以下表格输出汇总报告，并单独说明仅在单篇论文上出现的退化或波动（不得将单篇特例过度外推为全局结论）：

```markdown
| 维度 | 新版胜 (Win) | 持平 (Tie) | 新版负 (Loss) | 旧版均分 (±SD) | 新版均分 (±SD) | 关键证据与典型差异摘要 |
|---|---|---|---|---|---|---|
| 1. 原文忠实度【底线】 | | | | | | |
| 2. 权1保护范围【核心】 | | | | | | |
| 3. 从属权项层次【核心】 | | | | | | |
| 4. 客体布局与技术属性 | | | | | | |
| 5. 说明书充分公开【底线】 | | | | | | |
| 6. 技术效果与证据边界 | | | | | | |
| 7. 附图与机制表达 | | | | | | |
| 8. 正文与附件分离 | | | | | | |
| 9. 缺口与风险交接【底线】 | | | | | | |
| **结构门禁通过率** | — | — | — | xx.x% | xx.x% | |
```

---

## 6. 冒烟回归测试（Smoke Test）

每次修改技能文件或脚本后，在跑完整论文评测前，必须先使用自带示例 `assets/example_patent_content.json` 完成全链路冒烟测试与 `grade_structure.py` 自检（将 `<skill>` 替换为 `skills/paper2patent` 路径）：

```bash
mkdir -p /tmp/p2p/run-smoke
cp <skill>/assets/example_patent_content.json /tmp/p2p/run-smoke/example.json
python3 <skill>/scripts/check_patent_draft.py /tmp/p2p/run-smoke/example.json
python3 <skill>/scripts/generate_patent_drawings.py /tmp/p2p/run-smoke/example.json -o /tmp/p2p/run-smoke/out --update-json
python3 <skill>/scripts/generate_patent_docx.py /tmp/p2p/run-smoke/example.json -o /tmp/p2p/run-smoke/out/example_专利申请文件.docx --require-drawings
python3 <skill>/scripts/export_patent_pdf.py /tmp/p2p/run-smoke/out/example_专利申请文件.docx -o /tmp/p2p/run-smoke/out/example_专利申请文件.pdf --preview-dir /tmp/p2p/run-smoke/out/preview --content-json /tmp/p2p/run-smoke/example.json

# 生成示例交付消息并运行结构评测脚本验证
cat << 'EOF' > /tmp/p2p/run-smoke/final_message.md
已为您生成《低照度图像增强方法、装置、电子设备及存储介质》中国发明专利申请初稿（DOCX与PDF）及配套《撰写说明》。本输出仅为技术初稿，不构成法律意见。
权利要求布局为方法权利要求1–6、装置权利要求7、电子设备权利要求8及存储介质权利要求9。
请注意核对《撰写说明》开篇的“待补充材料清单”（含光照估计网络通道数、权属确认与首次公开日期核查），并在正式提交前由专业专利代理师审阅并开展现有技术检索。
EOF

python3 <skill>/evals/grade_structure.py /tmp/p2p/run-smoke
git diff --check
```

评估目录随 skill 一起分发，但普通论文转专利撰写任务不需要读取或运行它。
