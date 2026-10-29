# FTO 自由实施（防侵权撞库）排查工作流与规范

本指南是 `paper2patent` 技能**第八步（FTO 撞库排查）**的操作与判定规范。当 Agent 完成步骤 1–7（生成并质检 `<名称>_专利申请文件.docx`、`<名称>_专利申请文件_撰写说明.docx` 及 `<名称>_专利申请文件.pdf`）后，应立即依据本指南与 `fto/fto_patent_check.py` 脚本，基于**已提炼转化的专利技术方案（`patent.json`）**调用智慧芽（PatSnap）专利数据库执行 FTO 防侵权撞库排查。

> **为什么在“论文转交底书/申请初稿”之后执行 FTO？**
> 学术论文侧重科学原理与实验验证，而 FTO（Freedom to Operate）比对的是**拟实施/商业化落地的具体技术方案**是否落入他人有效专利的**独立权利要求保护范围**。在完成步骤 1–6 将论文转化为结构化 `patent.json`（明确独立权利要求 1 的核心技术特征、模块架构及具体实施方式）后执行 FTO，既能精准拆解技术特征（Feature List）进行“全面覆盖原则”比对，又能直接对刚生成的专利初稿给出规避设计（Design-Around）或权利要求强化建议。

---

## 一、智慧芽（PatSnap）MCP 数据库配置与每日 100 篇额度红线

### 1.1 MCP 服务配置（`fto/mcp.json`）
本技能在 `fto/mcp.json` 中内置了智慧芽专利平台 MCP 服务配置（`patent-platform`，端点 `https://patent.jiuan.com/mcp`）。
- 若宿主 Agent 环境已挂载 `patent-platform` MCP 工具（如 `search_patents`、`semantic_search_patents`、`get_claims`、`batch_prefetch_claims`、`get_legal_status`、`get_bibliography`），可直接调用或统一通过 `fto/fto_patent_check.py` 调用。
- 若宿主环境未自动挂载 MCP，**`fto/fto_patent_check.py` 内置了零依赖的原生 HTTP/SSE MCP 客户端**，会自动读取 `fto/mcp.json` 完成握手、检索、权利要求拉取与本地配额记账。

### 1.2 每日 100 篇额度硬性红线（Quota Discipline）
智慧芽专利接口（P002 搜索、语义搜索、P018 权利要求全文、P013 法律状态）属于高价值计费接口，**每日可用额度严格限制在 100 篇唯一专利（Unique Patent Publications）以内**。

1. **本地持久化记账**：`fto_patent_check.py` 自动在 `~/.paper2patent_fto_quota.json`（可通过环境变量 `PAPER2PATENT_FTO_QUOTA_FILE` 覆盖）按自然日记录已查询的去重公开号列表（`queried_pns`）与权利要求拉取数。
2. **先粗筛、后精读（漏斗式分配）**：
   - **单次任务推荐配额预算**：单篇论文转专利后的 FTO 撞库，建议控制在 **20–45 篇**唯一专利以内（每日可支撑 2–4 个完整项目的深度 FTO 排查，绝不触碰 100 篇红线）。
   - **第一层（检索召回，约 15–35 篇）**：使用 `semantic_search_patents`（限定有效 `1` / 审中 `2` 专利）+ 1~2 组 `search_patents` 布尔精准检索式召回候选池，并按申请号（`apno`）自动去重（同一专利族优先保留已授权公告号 `B/B1/B2`，剔除重复的 `A` 公布号）。
   - **第二层（标题与相关性初筛，收敛至 Top 5–10 篇）**：根据标题、申请人、相关度分数，剔除明显跨领域的噪音专利。
   - **第三层（权利要求与法律状态深拉，5–10 篇）**：仅对高度相关的 Top 5–10 篇专利调用 `batch_prefetch_claims`、`get_claims` 和 `get_legal_status`，拉取其独立权利要求全文与当前法律状态。
3. **严禁盲目翻页或超额拉取**：
   - 严禁设置 `limit > 50` 或对未筛选的几十篇专利全量调用 `get_description`（说明书全文 P019 仅在独立权利要求存在歧义、必须查说明书界定术语时才按需单篇调用）。
   - 遇到 `rate_limited`（分钟桶限流 67200001/67200002）时必须等待 `retry_after_seconds` 秒后再试，严禁立即循环重试消耗额度；遇到 `quota_exhausted`（67200004）或本地今日额度满 100 篇时立即停止外部请求并向用户汇报。

---

## 二、标准执行流程（四步法）

在步骤 6 生成完 `<名称>_专利申请文件.docx` 和 `<名称>_专利申请文件.pdf` 后，按以下四步完成 FTO 撞库排查：

### 步骤 8.1：检查今日额度并执行自动化检索与候选拉取
运行 `fto/fto_patent_check.py` 的 `quota` 与 `auto-screen` 子命令（`<skill>` 为本技能根目录）：

```bash
# 1. 查看今日智慧芽 MCP 剩余可用额度（上限 100 篇/日）
python <skill>/fto/fto_patent_check.py quota

# 2. 基于已生成的 patent.json 执行核心特征拆解、语义+布尔双路检索及 Top 候选专利权项拉取
python <skill>/fto/fto_patent_check.py auto-screen patent.json \
  -o out/fto_candidates.json \
  --semantic-limit 15 \
  --boolean-limit 10 \
  --detail-limit 8 \
  --country CNA,CNB
```

> **补充精准检索（可选）**：若 `auto-screen` 返回的候选专利较少或需针对特定细分特征补充检索，可使用 `search` 与 `fetch-details` 子命令：
> ```bash
> # 自定义智慧芽 P002 布尔检索（合法字段前缀：TTL / ABST / CLMS / DESC / TACD / AN / IPC / CPC / APD / PBD）
> python <skill>/fto/fto_patent_check.py search --mode boolean \
>   -q 'TTL:("低照度") AND CLMS:("多尺度" AND "注意力")' --limit 10 -o out/fto_extra_search.json
>
> # 对筛选出的高危公开号补充拉取独立权利要求与法律状态
> python <skill>/fto/fto_patent_check.py fetch-details CN113052814A CN111462008B -o out/fto_extra_details.json
> ```

### 步骤 8.2：法律状态过滤与高危专利甄别
读取 `out/fto_candidates.json` 中的 `deep_fetched_candidates`，首先核查每件专利的法律状态与法域：
1. **有效专利（已授权且维持有效，如 `CN...B` 且法律状态为“有效/授权”）**：属于**直接 FTO 侵权风险排查对象**。
2. **审中专利（实质审查中/公开未授权，如 `CN...A` 且状态为“审中”）**：属于**潜在/预警风险对象**（其权利要求在审查中可能缩限或驳回，需评估若按现有公开权项授权是否会覆盖我方方案，并建议纳入监控）。
3. **失效专利（撤回、驳回、未缴年费终止、届满）**：进入公有领域，**无 FTO 侵权风险**（可作为我方自由实施的现有技术抗辩依据，在报告中标为 `NONE` 低/无风险）。

### 步骤 8.3：独立权利要求逐项特征比对（Claim Chart 侵权判定）
针对 Top 重点专利的**每一条独立权利要求**，将其拆解为技术特征分句（`A1, A2, A3, ...`），与我方 `patent.json` 的技术方案（含独立权利要求 1 及具体实施方式）执行严格的法律比对：

1. **全面覆盖原则（All-Elements Rule，《最高人民法院关于审理专利纠纷案件适用法律问题的若干规定》第 17 条）**：
   - **字面相同（Literal）**：我方技术方案包含了对方独立权利要求记载的该项技术特征，且技术手段、功能、效果完全一致，或者我方下位概念落入对方上位概念。
   - **等同特征（Equivalent）**：我方特征与对方权利要求特征相比，以基本相同的手段，实现基本相同的功能，达到基本相同的效果，且本领域普通技术人员无需经过创造性劳动即可联想到。
   - **缺失/不同（Missing / Different）**：对方独立权利要求中明确限定的某项必要技术特征（如特定的模块结构、特定的计算顺序、特定的参数约束或硬件依赖），在我方方案中**完全不存在**，或我方采用了截然相反/不可替代的技术路线。
2. **风险等级判定标准**：
   - **`HIGH`（高风险）**：目标专利处于**有效（已授权）**状态，且我方技术方案包含了其某一条独立权利要求的**全部技术特征**（字面相同或等同覆盖）。
   - **`MEDIUM`（中风险/预警）**：
     - 情形 A：目标专利处于**审中（在审）**状态，其当前公开的独立权利要求覆盖我方方案全部特征；
     - 情形 B：目标专利处于**有效**状态，绝大多数核心特征相同，仅个别特征存在是否构成“等同替换”的解释空间，或依赖我方具体工程实现中尚未明确的外围特征。
   - **`LOW`（低风险）**：目标专利虽属同一技术领域且解决类似问题，但其独立权利要求中存在**至少 1 项核心区别技术特征**在我方方案中明确缺失或技术原理明显不同（不满足全面覆盖原则）。
   - **`NONE`（无风险）**：目标专利已失效（驳回/撤回/终止），或技术路线完全不相关。

### 步骤 8.4：编写 `fto_analysis.json` 并生成 FTO 撞库排查报告（MD + DOCX）
将逐项比对结果整理为结构化的 `out/fto_analysis.json`（JSON 格式规范见下文第三节），随后运行 `report` 子命令一键生成 Markdown 与 Word（DOCX）双格式报告：

```bash
python <skill>/fto/fto_patent_check.py report out/fto_analysis.json \
  --md-output out/<名称>_FTO撞库排查报告.md \
  --docx-output out/<名称>_FTO撞库排查报告.docx
```

---

## 三、`fto_analysis.json` 结构规范

Agent 在完成权利要求比对后，按以下结构写入 `out/fto_analysis.json` 供 `report` 子命令渲染：

```json
{
  "invention_name": "低照度图像增强方法、装置、电子设备及存储介质",
  "source_title": "Multi-Scale Attention Retinex Network for Low-Light Image Enhancement",
  "search_date": "2026-10-29",
  "target_jurisdictions": ["CN（中国）"],
  "overall_risk": "LOW",
  "executive_summary": "本次 FTO 撞库排查在智慧芽全球专利数据库中检索并筛查了 22 篇相关专利（消耗今日额度 22/100 篇），重点对 6 篇高相关度有效及审中专利进行了独立权利要求逐项特征比对（Claim Chart）。排查结果表明：现有高相关度专利在去噪算子（如采用固定 BM3D 滤波或在光照分量上引入注意力）与本方案“在反射分量上执行多尺度卷积+通道注意力加权融合，且在伽马校正相乘前完成去噪”存在实质性特征差异，均未全面覆盖本方案，综合 FTO 自由实施风险为【低风险（Low）】。",
  "quota_usage": {
    "session_unique_patents": 22,
    "daily_total_used": 22,
    "daily_limit": 100
  },
  "target_features": [
    {"id": "F0", "type": "主题名称", "text": "一种低照度图像增强方法"},
    {"id": "F1", "type": "核心技术特征", "text": "将低照度图像输入光照估计网络，得到同尺寸光照图，并逐像素相除得到反射分量"},
    {"id": "F2", "type": "核心技术特征", "text": "将反射分量输入多尺度注意力去噪网络，对至少两种尺度的特征按通道注意力权重加权融合得到去噪反射分量"},
    {"id": "F3", "type": "核心技术特征", "text": "对光照图进行伽马校正后与去噪反射分量逐像素相乘，得到增强图像"}
  ],
  "search_log": [
    {
      "type": "语义检索 (semantic_search_patents)",
      "query": "低照度图像增强 + Retinex分解 + 多尺度通道注意力去噪反射分量",
      "total_hits": 2000,
      "screened_count": 15,
      "note": "限定 CNA/CNB，有效及审中状态，取相关度 Top 15"
    },
    {
      "type": "布尔检索 (核心发明点精准检索)",
      "query": "TTL:(\"低照度图像增强\" OR \"暗光图像增强\") AND TACD:(\"Retinex\" AND \"注意力\")",
      "total_hits": 86,
      "screened_count": 7,
      "note": "去重后新增 7 篇候选专利"
    }
  ],
  "analyzed_patents": [
    {
      "pn": "CN113052814A",
      "title": "基于Retinex和注意力机制的暗光图像增强方法",
      "assignee": "浙江工业大学",
      "apdt": "2021-03-23",
      "legal_status": "有效/审中",
      "risk_level": "LOW",
      "summary": "对方独权1限定采用双支路U-Net分解网络、对反射分量采用BM3D去噪，并将注意力机制用于光照调节网络，而我方采用多尺度注意力网络对反射分量去噪，缺失对方BM3D及光照调节率α特征，不构成全面覆盖。",
      "independent_claim_text": "1.一种基于注意力机制和Retinex的暗光图像增强方法，其特征在于，所述方法包括以下步骤：步骤1：设计一个网络主体结构为U-Net的多尺度融合的分解网络，它由两个支路组成；将低光照图像Slow和正常光图像Snormal分别输入到分解网络的不同支路中，得到两者的反射分量R和光照分量L；步骤2：对步骤1得到的低光照图像的反射分量Rlow采用BM3D方法进行去噪操作……步骤3：改进基于注意力机制的光照调节网络……将暗光图像的光照分量Llow和光照调节率α作为输入……",
      "feature_comparison": [
        {
          "patent_feature_id": "1-A",
          "patent_feature_text": "设计网络主体为U-Net的双支路多尺度融合分解网络，输入低光照和正常光图像得到反射分量R和光照分量L",
          "our_feature_text": "F1：单路三层卷积+Sigmoid光照估计网络估计光照图，再通过逐像素相除 R(x)=I(x)/max(L(x),ε) 计算反射分量",
          "match_result": "不同",
          "rationale": "我方无需双支路U-Net同时分解反射分量与光照分量，而是通过显式除法分解，分解机理不同。"
        },
        {
          "patent_feature_id": "1-B",
          "patent_feature_text": "对低光照图像的反射分量采用BM3D方法搜索相似块并在变换域滤波去噪",
          "our_feature_text": "F2：采用多尺度卷积（3×3/5×5/7×7）与通道注意力权重加权融合网络对反射分量去噪",
          "match_result": "缺失/不同",
          "rationale": "对方独权明确限定传统非学习算子BM3D去噪，我方采用端到端多尺度通道注意力网络去噪，技术手段与效果截然不同。"
        },
        {
          "patent_feature_id": "1-C",
          "patent_feature_text": "将光照分量Llow和扩展为特征图的光照调节率α输入带通道与空间注意力的encoder-decoder光照调节网络",
          "our_feature_text": "F3：对光照图执行幂函数伽马校正 L'(x)=L(x)^γ，无光照调节神经网络及输入参数特征图α",
          "match_result": "缺失",
          "rationale": "我方未设置神经网络光照调节器及扩展特征图α，完全缺少该必要技术特征。"
        },
        {
          "patent_feature_id": "1-D",
          "patent_feature_text": "将处理后的光照分量与反射分量点乘重构得到增强结果",
          "our_feature_text": "F3：将去噪反射分量与校正光照图逐像素相乘得到增强图像",
          "match_result": "相同",
          "rationale": "均基于Retinex重构原理将两分量逐像素相乘。"
        }
      ],
      "conclusion": "因我方技术方案缺少对比专利CN113052814A独立权利要求1中的特征1-A（双支路U-Net分解）、1-B（BM3D变换域滤波）及1-C（带光照调节率α特征图的注意力光照增强网络），不满足全面覆盖原则，亦不构成等同替换，无字面或等同侵权风险。"
    }
  ],
  "recommendations": [
    {
      "category": "规避设计加固（Design-Around）",
      "detail": "在产品工程落地及交底书定稿中，保持“显式逐像素相除获取反射分量 + 多尺度通道注意力网络去噪反射分量 + 幂函数伽马校正光照图”的技术路径，避免在反射分量上混用传统BM3D算子或引入双支路U-Net联合分解。"
    },
    {
      "category": "我方权利要求答辩准备",
      "detail": "对比专利CN113052814A等虽公开了Retinex结合注意力机制，但其注意力作用于光照分量而非反射分量多尺度去噪。在后续实质审查答复创造性（专利法第22条第3款）时，可重点强调“在逐像素相除放大暗区噪声后、与校正光照相乘前，利用多尺度卷积与通道注意力自适应抑制反射分量局部噪声”的非显而易见性。"
    }
  ]
}
```

---

## 四、特殊情况与降级处理

1. **MCP 网络不可达或凭证失效**：若运行 `fto_patent_check.py` 时返回网络异常或 401/403 权限错误，先调用 `extract-features` 提取结构化技术特征拆解表与推荐检索式写入报告，并在交付信息中如实说明原因，切勿伪造智慧芽检索命中数据或虚构专利公开号。
2. **发现高风险（`HIGH`）有效专利**：若比对发现某件已授权有效专利的独立权利要求全面覆盖我方方案，必须在交付回复和报告显著位置给出高亮预警，并针对对方独立权利要求中限制最窄的一项特征，提出至少 1 条具体的**工程规避设计建议（Design-Around，即删除或替换该特征）**，询问用户是否需要同步修订刚生成的 `patent.json` 申请初稿。
