#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""FTO (Freedom to Operate) Patent Database Screening & Report Generator.

Connects to the PatSnap (智慧芽) MCP server configured in `fto/mcp.json`,
enforces a strict daily quota of <= 100 unique patents (tracked locally in
`~/.paper2patent_fto_quota.json`), and supports:
  1. `extract-features`: Extract structured technical features and recommended
     search queries from `patent.json` (after Paper2Patent drafting).
  2. `quota`: View today's PatSnap MCP patent quota usage (limit: 100/day).
  3. `search`: Run boolean (`search_patents`) or semantic (`semantic_search_patents`)
     patent searches within the daily quota.
  4. `fetch-details`: Batch-fetch claims, bibliography, and legal status for
     candidate patents within the remaining daily quota.
  5. `auto-screen`: End-to-end automated candidate retrieval + claim/legal-status
     collection from `patent.json`, writing `fto_candidates.json` for Agent
     feature-by-feature infringement comparison.
  6. `report`: Generate `<名称>_FTO撞库排查报告.md` and `<名称>_FTO撞库排查报告.docx`
     from structured FTO analysis JSON (`fto_analysis.json`).

Standard library only (Python 3.9+).
"""

from __future__ import annotations

import argparse
import datetime as _dt
import json
import os
import re
import sys
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

SKILL_DIR = Path(__file__).resolve().parent.parent
FTO_DIR = SKILL_DIR / "fto"
DEFAULT_MCP_CONFIG = FTO_DIR / "mcp.json"
DAILY_PATENT_LIMIT = 100
DEFAULT_QUOTA_FILE = Path.home() / ".paper2patent_fto_quota.json"

sys.path.insert(0, str(SKILL_DIR / "scripts"))
from patent_common import as_paragraphs, claims_list, eprint, read_json, write_json  # noqa: E402
from generate_patent_docx import ImageRegistry, para, table, write_package  # noqa: E402


# ---------------------------------------------------------------------------
# Daily Quota Manager (Strict <= 100 patents / day)
# ---------------------------------------------------------------------------

class QuotaExceededError(RuntimeError):
    """Raised when an operation would exceed the daily 100-patent cap."""


class DailyQuotaTracker:
    """Track unique patent numbers and API calls per calendar day."""

    def __init__(self, quota_file: Path = DEFAULT_QUOTA_FILE, daily_limit: int = DAILY_PATENT_LIMIT) -> None:
        self.quota_file = Path(os.environ.get("PAPER2PATENT_FTO_QUOTA_FILE") or quota_file)
        self.daily_limit = daily_limit
        self.today = _dt.date.today().isoformat()
        self.state = self._load()

    def _load(self) -> dict[str, Any]:
        if self.quota_file.exists():
            try:
                raw = json.loads(self.quota_file.read_text(encoding="utf-8"))
                if isinstance(raw, dict) and raw.get("date") == self.today:
                    raw.setdefault("queried_pns", [])
                    raw.setdefault("claim_fetched_pns", [])
                    raw.setdefault("api_calls", 0)
                    return raw
            except (OSError, ValueError):
                pass
        return {
            "date": self.today,
            "daily_limit": self.daily_limit,
            "queried_pns": [],
            "claim_fetched_pns": [],
            "api_calls": 0,
        }

    def save(self) -> None:
        self.quota_file.parent.mkdir(parents=True, exist_ok=True)
        self.quota_file.write_text(
            json.dumps(self.state, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )

    @property
    def used_count(self) -> int:
        return len(set(self.state.get("queried_pns") or []))

    @property
    def remaining(self) -> int:
        return max(0, self.daily_limit - self.used_count)

    def filter_and_reserve(self, pns: list[str]) -> tuple[list[str], list[str]]:
        """Return (allowed_pns, skipped_due_to_quota_pns) while respecting daily_limit."""
        known = set(self.state.get("queried_pns") or [])
        allowed: list[str] = []
        skipped: list[str] = []
        for raw_pn in pns:
            pn = str(raw_pn).strip().upper()
            if not pn:
                continue
            if pn in known:
                if pn not in allowed:
                    allowed.append(pn)
                continue
            if len(known) < self.daily_limit:
                known.add(pn)
                allowed.append(pn)
            else:
                skipped.append(pn)
        self.state["queried_pns"] = sorted(known)
        self.save()
        return allowed, skipped

    def record_claims_fetched(self, pns: list[str]) -> None:
        fetched = set(self.state.get("claim_fetched_pns") or [])
        for pn in pns:
            if pn:
                fetched.add(str(pn).strip().upper())
        self.state["claim_fetched_pns"] = sorted(fetched)
        self.save()

    def increment_api_call(self) -> None:
        self.state["api_calls"] = int(self.state.get("api_calls") or 0) + 1
        self.save()

    def summary(self) -> dict[str, Any]:
        return {
            "date": self.today,
            "daily_limit": self.daily_limit,
            "used_patents": self.used_count,
            "remaining_patents": self.remaining,
            "claim_fetched_count": len(self.state.get("claim_fetched_pns") or []),
            "api_calls": int(self.state.get("api_calls") or 0),
            "quota_file": str(self.quota_file),
        }


# ---------------------------------------------------------------------------
# PatSnap MCP HTTP / JSON-RPC Client
# ---------------------------------------------------------------------------

class PatSnapMCPClient:
    """Minimal standard-library JSON-RPC over HTTP/SSE client for `patent-platform` MCP."""

    def __init__(self, mcp_config_path: Path = DEFAULT_MCP_CONFIG, server_name: str = "patent-platform") -> None:
        if not mcp_config_path.exists():
            raise FileNotFoundError(f"MCP configuration file not found: {mcp_config_path}")
        cfg = json.loads(mcp_config_path.read_text(encoding="utf-8"))
        servers = cfg.get("mcpServers") or {}
        if server_name not in servers:
            raise KeyError(f"Server '{server_name}' not found in {mcp_config_path}")
        srv = servers[server_name]
        self.url: str = str(srv["url"]).strip()
        self.headers: dict[str, str] = {
            "Content-Type": "application/json",
            "Accept": "application/json, text/event-stream",
        }
        for k, v in (srv.get("headers") or {}).items():
            self.headers[str(k)] = str(v)
        env_token = os.environ.get("PATENT_MCP_TOKEN")
        if env_token:
            self.headers["Authorization"] = f"Bearer {env_token}"
        self.session_id: str | None = None
        self._req_id = 0

    def _post(self, payload: dict[str, Any], timeout: int = 60) -> dict[str, Any]:
        req_headers = dict(self.headers)
        if self.session_id:
            req_headers["mcp-session-id"] = self.session_id
        data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        req = urllib.request.Request(self.url, data=data, headers=req_headers, method="POST")
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                sid = resp.headers.get("mcp-session-id")
                if sid:
                    self.session_id = sid
                raw_body = resp.read().decode("utf-8", errors="replace")
        except urllib.error.HTTPError as exc:
            err_body = exc.read().decode("utf-8", errors="replace") if exc.fp else ""
            raise RuntimeError(f"MCP HTTP {exc.code}: {err_body[:400]}") from exc
        except urllib.error.URLError as exc:
            raise RuntimeError(f"MCP connection error: {exc}") from exc

        # Parse either SSE (`data: {...}`) or direct JSON response
        last_json: dict[str, Any] | None = None
        for line in raw_body.splitlines():
            line_str = line.strip()
            if line_str.startswith("data:"):
                candidate = line_str[5:].strip()
                if candidate:
                    try:
                        last_json = json.loads(candidate)
                    except ValueError:
                        continue
        if last_json is None:
            try:
                last_json = json.loads(raw_body)
            except ValueError as exc:
                raise RuntimeError(f"Unparseable MCP response: {raw_body[:400]}") from exc
        return last_json

    def ensure_initialized(self) -> None:
        if self.session_id:
            return
        self._req_id += 1
        res = self._post({
            "jsonrpc": "2.0",
            "id": self._req_id,
            "method": "initialize",
            "params": {
                "protocolVersion": "2024-11-05",
                "capabilities": {},
                "clientInfo": {"name": "paper2patent-fto", "version": "1.0.0"},
            },
        })
        if "error" in res:
            raise RuntimeError(f"MCP initialize failed: {res['error']}")

    def call_tool(self, name: str, arguments: dict[str, Any], timeout: int = 90) -> Any:
        self.ensure_initialized()
        self._req_id += 1
        res = self._post({
            "jsonrpc": "2.0",
            "id": self._req_id,
            "method": "tools/call",
            "params": {"name": name, "arguments": arguments},
        }, timeout=timeout)
        if "error" in res:
            raise RuntimeError(f"MCP tool '{name}' RPC error: {res['error']}")
        result = res.get("result") or {}
        content_list = result.get("content") or []
        text_chunks = [c.get("text", "") for c in content_list if isinstance(c, dict) and c.get("type") == "text"]
        joined = "\n".join(text_chunks).strip()
        if result.get("isError"):
            raise RuntimeError(f"MCP tool '{name}' error: {joined}")
        try:
            return json.loads(joined)
        except ValueError:
            return {"raw_text": joined}


# ---------------------------------------------------------------------------
# Feature Decomposition & Query Builder from `patent.json`
# ---------------------------------------------------------------------------

CLAUSE_SPLIT_RE = re.compile(r"[；;。]|(?<=，)(?=(?:将|对|根据|通过|采用|利用|获取|接收|计算|生成|输出|构建|输入|确定|判断|执行|基于|其中))")


def decompose_claim_features(claim_text: str) -> list[dict[str, str]]:
    """Split an independent claim into sequential technical feature units (F1, F2, ...)."""
    cleaned = re.sub(r"^\s*\d+\s*[\.．、]\s*", "", claim_text.strip())
    if "其特征在于" in cleaned:
        preamble, body = cleaned.split("其特征在于", 1)
        preamble = re.sub(r"^[，,：:\s]+|[，,：:\s]+$", "", preamble)
        body = re.sub(r"^[，,：:\s]*(?:包括[：:\s]*|所述方法包括以下步骤[：:\s]*|所述方法包括[：:\s]*)?", "", body)
    else:
        preamble, body = "", cleaned

    features: list[dict[str, str]] = []
    if preamble:
        features.append({
            "id": "F0",
            "type": "主题名称/前序特征",
            "text": preamble.rstrip("，,；;。"),
        })
    raw_clauses = [c.strip(" ，,；;。") for c in CLAUSE_SPLIT_RE.split(body) if c.strip(" ，,；;。")]
    idx = 1
    for clause in raw_clauses:
        if len(clause) < 4:
            continue
        features.append({
            "id": f"F{idx}",
            "type": "核心技术特征",
            "text": clause,
        })
        idx += 1
    return features


def extract_keywords_from_patent(data: dict[str, Any]) -> list[str]:
    """Extract clean core technical noun phrases from invention_name, claim 1, and invention_points."""
    inv_name = str(data.get("invention_name") or "").strip()
    core_subject = re.sub(r"^(?:一种)?(.*?)(?:方法|装置|系统|电子设备|计算设备|存储介质).*$", r"\1", inv_name).strip()
    keywords: list[str] = []
    if core_subject and 2 <= len(core_subject) <= 20:
        keywords.append(core_subject)

    claims = claims_list(data)
    claim1 = claims[0] if claims else ""
    notes = data.get("notes") if isinstance(data.get("notes"), dict) else {}
    inv_points_text = "；".join(as_paragraphs(notes.get("invention_points")))
    combined_source = f"{claim1}；{inv_points_text}"

    # Split into clean fragments by verbs/prepositions/particles so we only extract pure noun phrases
    fragments = re.split(
        r"[，,；;。：:、（）()\[\]]|"
        r"(?:将所述|对所述|根据所述|通过所述|采用所述|利用所述|获取待|获取|得到与所述|得到|输入至|输入|"
        r"输出|其中|所述|包括|其特征在于|一种|分别进行|进行|并根据|根据|采用|通过|利用|用于|以及|或者|与|及|的|中|上|后|前|对|将|为|是)",
        combined_source,
    )
    suffix_re = re.compile(
        r".*(?:网络|模型|算法|机制|分量|模块|编码器|解码器|注意力|变换|分解|去噪|增强|校正|融合|估计|优化|调度|缓存|量化|蒸馏|损失|图|器|层|矩阵|特征|权重)$"
    )
    blacklist = {"待增强", "增强", "去噪", "融合", "估计", "校正", "分解", "优化", "增强方法", "技术方案", "现有技术", "处理方法", "计算方法", "本实施例", "本发明"}
    for frag in fragments:
        term = frag.strip()
        if not (3 <= len(term) <= 12):
            continue
        if term in blacklist or term in keywords:
            continue
        if suffix_re.match(term):
            keywords.append(term)
    return keywords[:8]


def extract_fto_context(data: dict[str, Any]) -> dict[str, Any]:
    """Build structured FTO context from a drafted `patent.json`."""
    inv_name = str(data.get("invention_name") or "")
    claims = claims_list(data)
    claim1 = claims[0] if claims else ""
    dep_claims = claims[1:] if len(claims) > 1 else []
    features = decompose_claim_features(claim1) if claim1 else []
    keywords = extract_keywords_from_patent(data)
    notes = data.get("notes") if isinstance(data.get("notes"), dict) else {}
    inv_points = as_paragraphs(notes.get("invention_points"))

    # Construct recommended PatSnap boolean queries
    queries: list[dict[str, str]] = []
    if keywords:
        main_kw = keywords[0]
        sub_kws = keywords[1:4]
        if sub_kws:
            or_clause = " OR ".join(f'"{k}"' for k in sub_kws)
            queries.append({
                "purpose": "核心发明点精准检索（标题+摘要+权项）",
                "query": f'TTL:("{main_kw}") AND TACD:({or_clause})',
            })
            queries.append({
                "purpose": "权利要求核心特征组合检索",
                "query": f'CLMS:("{main_kw}") AND CLMS:({or_clause})',
            })
        else:
            queries.append({
                "purpose": "核心主题检索",
                "query": f'TTL:("{main_kw}")',
            })

    semantic_text = "\n".join(filter(None, [
        inv_name,
        str(data.get("abstract") or ""),
        claim1,
        "；".join(inv_points),
    ]))

    return {
        "invention_name": inv_name,
        "source_title": str(data.get("source_title") or ""),
        "independent_claim_1": claim1,
        "dependent_claims_count": len(dep_claims),
        "decomposed_features": features,
        "extracted_keywords": keywords,
        "invention_points": inv_points,
        "recommended_boolean_queries": queries,
        "recommended_semantic_text": semantic_text,
    }


# ---------------------------------------------------------------------------
# Deduplication & Priority Filtering (<= 100 Patents Cap)
# ---------------------------------------------------------------------------

def deduplicate_by_application(records: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Deduplicate search results by application number (`apno`), preferring granted `B` publications over `A`."""
    by_key: dict[str, dict[str, Any]] = {}
    for rec in records:
        pn = str(rec.get("pn") or "").strip().upper()
        if not pn:
            continue
        apno = str(rec.get("apno") or pn).strip().upper()
        existing = by_key.get(apno)
        if not existing:
            by_key[apno] = dict(rec)
            continue
        # Prefer granted B/B1/B2 over A/A1
        old_pn = str(existing.get("pn") or "")
        if (pn.endswith(("B", "B1", "B2")) and not old_pn.endswith(("B", "B1", "B2"))):
            merged = dict(existing)
            merged.update(rec)
            merged["also_published_as"] = old_pn
            by_key[apno] = merged
        else:
            existing.setdefault("also_published_as", pn)
    return list(by_key.values())


# ---------------------------------------------------------------------------
# Report Generation (Markdown + DOCX)
# ---------------------------------------------------------------------------

RISK_BADGE = {
    "HIGH": "高风险（High）",
    "MEDIUM": "中风险（Medium）",
    "LOW": "低风险（Low）",
    "NONE": "无明显风险（Low/Clear）",
}


def build_fto_markdown(analysis: dict[str, Any]) -> str:
    """Render a comprehensive FTO infringement screening Markdown report."""
    inv_name = str(analysis.get("invention_name") or "未命名发明方案")
    source_title = str(analysis.get("source_title") or "未注明")
    overall_risk = str(analysis.get("overall_risk") or "LOW").upper()
    overall_label = RISK_BADGE.get(overall_risk, overall_risk)
    jurisdictions = "、".join(analysis.get("target_jurisdictions") or ["CN（中国）"])
    search_date = str(analysis.get("search_date") or _dt.date.today().isoformat())
    quota_info = analysis.get("quota_usage") or {}
    used_patents = quota_info.get("session_unique_patents") or quota_info.get("used_patents") or 0
    daily_total = quota_info.get("daily_total_used") or used_patents
    daily_limit = quota_info.get("daily_limit") or DAILY_PATENT_LIMIT

    lines: list[str] = [
        f"# 《{inv_name}》FTO 自由实施（防侵权撞库）排查报告",
        "",
        "> **声明**：本报告基于论文转化后的专利技术方案及智慧芽（PatSnap）全球专利数据库检索结果自动辅助生成，旨在研发立项与专利申请阶段识别潜在第三方专利障碍并提供规避设计参考，**不构成正式的法律意见书（Legal Opinion）**。在商业化产品上市或重大投融资前，请委托执业专利代理师或知识产权律师开展正式 FTO 尽职调查。",
        "",
        "## 一、排查概况与执行结论",
        "",
        "| 项目 | 内容 |",
        "|---|---|",
        f"| **目标技术方案名称** | {inv_name} |",
        f"| **源论文/交底书标题** | {source_title} |",
        f"| **目标排查法域** | {jurisdictions} |",
        f"| **排查日期** | {search_date} |",
        f"| **智慧芽 MCP 额度消耗** | 本次排查比对 **{used_patents}** 篇专利（今日累计 **{daily_total} / {daily_limit}** 篇上限） |",
        f"| **综合 FTO 风险评级** | **{overall_label}** |",
        "",
    ]

    exec_summary = str(analysis.get("executive_summary") or "").strip()
    if exec_summary:
        lines.extend(["**核心排查结论摘要**：", "", exec_summary, ""])

    # Section 2: Target technical features breakdown
    lines.extend([
        "## 二、目标实施方案核心技术特征拆解",
        "",
        "FTO 排查以转化后的独立权利要求 1 及核心实施例技术方案为基准，将其拆解为如下关键技术特征单元：",
        "",
        "| 特征编号 | 特征类型 | 我方技术方案特征描述 |",
        "|---|---|---|",
    ])
    for feat in analysis.get("target_features") or []:
        fid = str(feat.get("id") or "")
        ftype = str(feat.get("type") or "核心技术特征")
        ftext = str(feat.get("text") or "").replace("|", "｜")
        lines.append(f"| {fid} | {ftype} | {ftext} |")
    lines.append("")

    # Section 3: Search strategy & funnel
    lines.extend([
        "## 三、智慧芽（PatSnap）检索策略与漏斗统计",
        "",
        "| 轮次/类型 | 检索式 / 语义检索描述 | 命中总数 | 纳入筛查数 | 说明 |",
        "|---|---|---|---|---|",
    ])
    for s_item in analysis.get("search_log") or []:
        stype = str(s_item.get("type") or "检索")
        squery = str(s_item.get("query") or "").replace("|", "｜").replace("\n", " ")
        if len(squery) > 90:
            squery = squery[:87] + "..."
        stotal = str(s_item.get("total_hits") if s_item.get("total_hits") is not None else "-")
        sscreened = str(s_item.get("screened_count") or 0)
        snote = str(s_item.get("note") or "")
        lines.append(f"| {stype} | `{squery}` | {stotal} | {sscreened} | {snote} |")
    lines.append("")

    # Section 4: High/Medium/Low Risk Patent Summary Table
    patents = analysis.get("analyzed_patents") or []
    lines.extend([
        "## 四、重点对比专利清单及法律状态核查",
        "",
        "| 序号 | 公开（公告）号 | 专利名称 | 当前权利人 | 申请日 | 法律状态 | 风险等级 | 核心差异/结论简述 |",
        "|---|---|---|---|---|---|---|---|",
    ])
    for idx, pat in enumerate(patents, start=1):
        pn = str(pat.get("pn") or "")
        title = str(pat.get("title") or "").replace("|", "｜")
        assignee = str(pat.get("assignee") or "未知").replace("|", "｜")
        apdt = str(pat.get("apdt") or "-")
        legal = str(pat.get("legal_status") or "待核实")
        risk = RISK_BADGE.get(str(pat.get("risk_level") or "LOW").upper(), str(pat.get("risk_level") or "LOW"))
        short_reason = str(pat.get("summary") or "").replace("|", "｜")
        lines.append(f"| {idx} | {pn} | {title} | {assignee} | {apdt} | {legal} | {risk} | {short_reason} |")
    lines.append("")

    # Section 5: Claim Chart (All-Elements Rule / 全面覆盖与等同原则比对)
    lines.extend([
        "## 五、核心障碍专利权利要求逐项比对（Claim Chart）",
        "",
        "依据《专利法》第 64 条及最高人民法院专利侵权判定司法解释（**全面覆盖原则**与**等同原则**）：仅当被比对技术方案包含他人有效专利独立权利要求记载的**全部技术特征**（相同或等同）时，才落入其保护范围；若缺少至少一项必要技术特征且不构成等同替换，则不构成侵权。",
        "",
    ])
    high_or_detailed = [p for p in patents if p.get("feature_comparison")]
    if not high_or_detailed:
        lines.append("本次排查未发现需要展开高危要素表比对的有效障碍专利，或已通过整体技术路线差异排除。")
        lines.append("")
    else:
        for pat in high_or_detailed:
            pn = str(pat.get("pn") or "")
            title = str(pat.get("title") or "")
            assignee = str(pat.get("assignee") or "未知")
            legal = str(pat.get("legal_status") or "未知")
            risk = RISK_BADGE.get(str(pat.get("risk_level") or "LOW").upper(), str(pat.get("risk_level") or "LOW"))
            lines.extend([
                f"### 5.1 对比专利：{pn}《{title}》",
                "",
                f"- **权利人**：{assignee}　｜　**当前法律状态**：{legal}　｜　**侵权风险评估**：**{risk}**",
                f"- **对比独立权利要求原文**：",
                f"  > {str(pat.get('independent_claim_text') or '（见智慧芽权利要求缓存）').strip()}",
                "",
                "| 对方独权特征项 | 对方专利权利要求特征描述 | 我方方案对应特征 | 比对判定（相同/等同/缺失/不同） | 不侵权抗辩或风险分析依据 |",
                "|---|---|---|---|---|",
            ])
            for fc in pat.get("feature_comparison") or []:
                cid = str(fc.get("patent_feature_id") or "")
                ctext = str(fc.get("patent_feature_text") or "").replace("|", "｜")
                our_text = str(fc.get("our_feature_text") or "").replace("|", "｜")
                match_type = str(fc.get("match_result") or "不同")
                rationale = str(fc.get("rationale") or "").replace("|", "｜")
                lines.append(f"| {cid} | {ctext} | {our_text} | **{match_type}** | {rationale} |")
            conclusion = str(pat.get("conclusion") or pat.get("summary") or "")
            if conclusion:
                lines.extend(["", f"- **全面覆盖判定结论**：{conclusion}", ""])

    # Section 6: Design-around & Patent Layout Recommendations
    lines.extend([
        "## 六、规避设计建议（Design-Around）与我方专利布局策略",
        "",
    ])
    recommendations = analysis.get("recommendations") or []
    if recommendations:
        for i, rec in enumerate(recommendations, start=1):
            if isinstance(rec, dict):
                cat = str(rec.get("category") or f"建议 {i}")
                detail = str(rec.get("detail") or "")
                lines.append(f"{i}. **{cat}**：{detail}")
            else:
                lines.append(f"{i}. {rec}")
    else:
        lines.extend([
            "1. **强化区别技术特征壁垒**：在《权利要求书》独立权利要求 1 及说明书“发明内容”中突出我方方案与上述对比专利的核心区别特征及其带来的专属技术效果。",
            "2. **审中专利持续跟踪**：对于法律状态处于“实质审查/审中”的高相关度在先申请，建议纳入 B 级专利法律状态监控，跟踪其最终授权权利要求范围变化。",
            "3. **现有技术抗辩储备**：若产品商业化落地形态涉及在先专利的上位概念，建议留存论文发表及开源基线文献时间戳，作为现有技术抗辩（《专利法》第 67 条）证据链。",
        ])
    lines.append("")
    return "\n".join(lines)


def build_fto_docx(analysis: dict[str, Any], output_path: Path) -> None:
    """Build `<名称>_FTO撞库排查报告.docx` using the same standard-library DOCX engine."""
    inv_name = str(analysis.get("invention_name") or "未命名发明方案")
    source_title = str(analysis.get("source_title") or "未注明")
    overall_risk = str(analysis.get("overall_risk") or "LOW").upper()
    overall_label = RISK_BADGE.get(overall_risk, overall_risk)
    jurisdictions = "、".join(analysis.get("target_jurisdictions") or ["CN（中国）"])
    search_date = str(analysis.get("search_date") or _dt.date.today().isoformat())
    quota_info = analysis.get("quota_usage") or {}
    used_patents = quota_info.get("session_unique_patents") or quota_info.get("used_patents") or 0
    daily_total = quota_info.get("daily_total_used") or used_patents
    daily_limit = quota_info.get("daily_limit") or DAILY_PATENT_LIMIT

    parts: list[str] = [
        para("FTO 自由实施（防侵权撞库）排查报告", style="PartTitle"),
        para(inv_name, style="InventionName"),
        para(
            "声明：本报告基于论文转化后的专利技术方案及智慧芽（PatSnap）全球专利数据库检索结果辅助生成，"
            "旨在识别潜在第三方专利障碍并提供规避设计参考，不构成正式法律意见书。",
            style="Small",
        ),
        para("一、排查概况与执行结论", style="SectionTitle", keep_next=True),
        table(
            [
                ["项目", "内容"],
                ["目标技术方案名称", inv_name],
                ["源论文/交底书标题", source_title],
                ["目标排查法域", jurisdictions],
                ["排查日期", search_date],
                ["智慧芽 MCP 额度消耗", f"本次排查 {used_patents} 篇（今日累计 {daily_total}/{daily_limit} 篇上限）"],
                ["综合 FTO 风险评级", overall_label],
            ],
            align="left",
        ),
    ]

    exec_summary = str(analysis.get("executive_summary") or "").strip()
    if exec_summary:
        parts.append(para(f"核心排查结论：{exec_summary}"))

    parts.append(para("二、目标实施方案核心技术特征拆解", style="SectionTitle", keep_next=True))
    feat_rows = [["特征编号", "特征类型", "我方技术方案特征描述"]]
    for feat in analysis.get("target_features") or []:
        feat_rows.append([
            str(feat.get("id") or ""),
            str(feat.get("type") or "核心技术特征"),
            str(feat.get("text") or ""),
        ])
    if len(feat_rows) > 1:
        parts.append(table(feat_rows, align="left"))

    parts.append(para("三、智慧芽（PatSnap）检索策略与漏斗统计", style="SectionTitle", keep_next=True))
    search_rows = [["轮次/类型", "检索式 / 语义检索描述", "命中总数", "纳入筛查数", "说明"]]
    for s_item in analysis.get("search_log") or []:
        squery = str(s_item.get("query") or "")
        if len(squery) > 100:
            squery = squery[:97] + "..."
        search_rows.append([
            str(s_item.get("type") or "检索"),
            squery,
            str(s_item.get("total_hits") if s_item.get("total_hits") is not None else "-"),
            str(s_item.get("screened_count") or 0),
            str(s_item.get("note") or ""),
        ])
    if len(search_rows) > 1:
        parts.append(table(search_rows, align="left"))

    parts.append(para("四、重点对比专利清单及法律状态核查", style="SectionTitle", keep_next=True))
    patents = analysis.get("analyzed_patents") or []
    pat_rows = [["序号", "公开/公告号", "专利名称", "当前权利人", "申请日", "法律状态", "风险等级", "核心差异简述"]]
    for idx, pat in enumerate(patents, start=1):
        risk = RISK_BADGE.get(str(pat.get("risk_level") or "LOW").upper(), str(pat.get("risk_level") or "LOW"))
        pat_rows.append([
            str(idx),
            str(pat.get("pn") or ""),
            str(pat.get("title") or ""),
            str(pat.get("assignee") or "未知"),
            str(pat.get("apdt") or "-"),
            str(pat.get("legal_status") or "待核实"),
            risk,
            str(pat.get("summary") or ""),
        ])
    if len(pat_rows) > 1:
        parts.append(table(pat_rows, align="left"))

    parts.append(para("五、核心障碍专利权利要求逐项比对（Claim Chart）", style="SectionTitle", keep_next=True))
    parts.append(para(
        "依据全面覆盖原则与等同原则：仅当被比对技术方案包含他人有效专利独立权利要求记载的全部技术特征时，才落入其保护范围；"
        "若缺少至少一项必要技术特征且不构成等同替换，则不构成侵权。"
    ))
    for pat in patents:
        f_comp = pat.get("feature_comparison") or []
        if not f_comp:
            continue
        pn = str(pat.get("pn") or "")
        title = str(pat.get("title") or "")
        assignee = str(pat.get("assignee") or "未知")
        legal = str(pat.get("legal_status") or "未知")
        risk = RISK_BADGE.get(str(pat.get("risk_level") or "LOW").upper(), str(pat.get("risk_level") or "LOW"))
        parts.append(para(f"对比专利：{pn}《{title}》（权利人：{assignee}｜法律状态：{legal}｜风险：{risk}）", bold=True, keep_next=True))
        ind_claim = str(pat.get("independent_claim_text") or "").strip()
        if ind_claim:
            parts.append(para(f"对方独立权利要求原文：{ind_claim}", style="Small"))
        cc_rows = [["对方特征项", "对方专利权利要求特征描述", "我方方案对应特征", "比对判定", "不侵权抗辩或风险分析依据"]]
        for fc in f_comp:
            cc_rows.append([
                str(fc.get("patent_feature_id") or ""),
                str(fc.get("patent_feature_text") or ""),
                str(fc.get("our_feature_text") or ""),
                str(fc.get("match_result") or "不同"),
                str(fc.get("rationale") or ""),
            ])
        parts.append(table(cc_rows, align="left"))
        conclusion = str(pat.get("conclusion") or pat.get("summary") or "")
        if conclusion:
            parts.append(para(f"全面覆盖判定结论：{conclusion}"))

    parts.append(para("六、规避设计建议与我方专利布局策略", style="SectionTitle", keep_next=True))
    recommendations = analysis.get("recommendations") or []
    if recommendations:
        for i, rec in enumerate(recommendations, start=1):
            if isinstance(rec, dict):
                parts.append(para(f"{i}. {rec.get('category', '建议')}：{rec.get('detail', '')}"))
            else:
                parts.append(para(f"{i}. {rec}"))
    else:
        parts.append(para("1. 强化区别技术特征壁垒：在独立权利要求1及发明内容中突出我方方案与对比专利的核心区别特征及其技术效果。"))
        parts.append(para("2. 审中专利持续跟踪：对处于实质审查阶段的相关在先申请定期跟踪其授权权利要求范围。"))

    registry = ImageRegistry(embed_svg=False)
    write_package(output_path, "".join(parts), registry, f"{inv_name}（FTO排查报告）")


# ---------------------------------------------------------------------------
# Subcommand Handlers
# ---------------------------------------------------------------------------

def cmd_quota(args: argparse.Namespace) -> int:
    tracker = DailyQuotaTracker(daily_limit=args.daily_limit)
    if args.reset:
        if tracker.quota_file.exists():
            tracker.quota_file.unlink()
        tracker = DailyQuotaTracker(daily_limit=args.daily_limit)
    print(json.dumps(tracker.summary(), ensure_ascii=False, indent=2))
    return 0


def cmd_extract_features(args: argparse.Namespace) -> int:
    data = read_json(args.patent_json)
    ctx = extract_fto_context(data)
    if args.output:
        write_json(args.output, ctx)
        print(f"Wrote extracted FTO features and queries to {args.output}")
    else:
        print(json.dumps(ctx, ensure_ascii=False, indent=2))
    return 0


def cmd_search(args: argparse.Namespace) -> int:
    tracker = DailyQuotaTracker(daily_limit=args.daily_limit)
    if tracker.remaining <= 0:
        eprint(f"ERROR: Daily FTO patent quota ({tracker.daily_limit}/day) is exhausted for {tracker.today}.")
        return 2
    effective_limit = min(args.limit, tracker.remaining, 50)
    client = PatSnapMCPClient(args.mcp_config)
    tracker.increment_api_call()

    if args.mode == "semantic":
        countries = [c.strip() for c in args.country.split(",") if c.strip()] if args.country else ["CNA", "CNB"]
        res = client.call_tool("semantic_search_patents", {
            "text": args.query,
            "limit": effective_limit,
            "country": countries,
            "simple_legal_status": args.legal_status,
            "confirm_paid": True,
        })
    else:
        res = client.call_tool("search_patents", {
            "query": args.query,
            "limit": effective_limit,
            "offset": args.offset,
            "confirm_paid": True,
        })

    data_block = (res.get("data") if isinstance(res, dict) else None) or {}
    raw_results = data_block.get("results") or []
    deduped = deduplicate_by_application(raw_results)
    pns = [str(r.get("pn") or "") for r in deduped if r.get("pn")]
    allowed_pns, skipped_pns = tracker.filter_and_reserve(pns)
    allowed_set = set(allowed_pns)
    filtered_results = [r for r in deduped if str(r.get("pn") or "").upper() in allowed_set]

    output_payload = {
        "mode": args.mode,
        "query": args.query,
        "total_search_result_count": data_block.get("total_search_result_count", len(filtered_results)),
        "returned_count": len(filtered_results),
        "skipped_due_to_daily_quota": skipped_pns,
        "quota_status": tracker.summary(),
        "results": filtered_results,
    }
    if args.output:
        write_json(args.output, output_payload)
        print(f"Wrote {len(filtered_results)} search results to {args.output} (remaining daily quota: {tracker.remaining})")
    else:
        print(json.dumps(output_payload, ensure_ascii=False, indent=2))
    return 0


def cmd_fetch_details(args: argparse.Namespace) -> int:
    tracker = DailyQuotaTracker(daily_limit=args.daily_limit)
    raw_pns = [p.strip().upper() for p in args.pns if p.strip()]
    allowed_pns, skipped_pns = tracker.filter_and_reserve(raw_pns)
    if not allowed_pns:
        eprint(f"ERROR: Cannot fetch patents; daily limit ({tracker.daily_limit}) reached. Skipped: {skipped_pns}")
        return 2

    client = PatSnapMCPClient(args.mcp_config)
    # 1. Batch prefetch claims into cache
    tracker.increment_api_call()
    prefetch_info = client.call_tool("batch_prefetch_claims", {
        "patent_numbers": allowed_pns,
        "confirm_paid": True,
    })

    detailed_patents: list[dict[str, Any]] = []
    fetched_claim_pns: list[str] = []
    for pn in allowed_pns:
        entry: dict[str, Any] = {"pn": pn}
        # Legal status
        try:
            tracker.increment_api_call()
            l_res = client.call_tool("get_legal_status", {"patent_number": pn})
            l_data = (l_res.get("data") or [{}])[0] if isinstance(l_res, dict) else {}
            p_legal = l_data.get("patent_legal") or {}
            simple_status = "、".join(p_legal.get("simple_legal_status") or [])
            detail_status = "、".join(p_legal.get("legal_status") or [])
            entry["legal_status"] = f"{simple_status}（{detail_status}）" if detail_status else (simple_status or "未知")
        except Exception as exc:  # noqa: BLE001
            entry["legal_status"] = f"状态查询异常: {exc}"

        # Bibliography (optional if user wants abstract/IPC)
        if args.with_biblio:
            try:
                tracker.increment_api_call()
                b_res = client.call_tool("get_bibliography", {"patent_number": pn})
                b_data = (b_res.get("data") or [{}])[0] if isinstance(b_res, dict) else {}
                entry["bibliography"] = b_data.get("bibliographic_data") or {}
            except Exception as exc:  # noqa: BLE001
                entry["bibliography_error"] = str(exc)

        # Claims
        try:
            tracker.increment_api_call()
            c_res = client.call_tool("get_claims", {"patent_number": pn, "confirm_paid": True})
            if isinstance(c_res, dict):
                structured = c_res.get("claims_structured") or []
                ind_claims = [c for c in structured if c.get("type") == "independent"]
                entry["claim_count"] = c_res.get("claim_count", len(structured))
                entry["independent_claims"] = ind_claims or structured[:1]
                entry["claim_text"] = c_res.get("claim_text", "")
                fetched_claim_pns.append(pn)
        except Exception as exc:  # noqa: BLE001
            entry["claims_error"] = str(exc)

        detailed_patents.append(entry)

    tracker.record_claims_fetched(fetched_claim_pns)
    payload = {
        "requested_pns": raw_pns,
        "fetched_pns": allowed_pns,
        "skipped_due_to_daily_quota": skipped_pns,
        "prefetch_summary": prefetch_info,
        "quota_status": tracker.summary(),
        "patents": detailed_patents,
    }
    if args.output:
        write_json(args.output, payload)
        print(f"Wrote detailed claims & legal status for {len(detailed_patents)} patents to {args.output}")
    else:
        print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 0


def cmd_auto_screen(args: argparse.Namespace) -> int:
    """Automated multi-round search + top candidate claim/legal status fetch within <= 100 daily budget."""
    data = read_json(args.patent_json)
    ctx = extract_fto_context(data)
    tracker = DailyQuotaTracker(daily_limit=args.daily_limit)
    initial_used = tracker.used_count
    if tracker.remaining <= 0:
        eprint(f"ERROR: Daily FTO patent quota ({tracker.daily_limit}/day) is exhausted for {tracker.today}.")
        return 2

    client = PatSnapMCPClient(args.mcp_config)
    search_log: list[dict[str, Any]] = []
    candidate_pool: list[dict[str, Any]] = []

    # Round 1: Semantic search on active/pending CN patents (most effective for FTO)
    sem_limit = min(args.semantic_limit, tracker.remaining, 25)
    if sem_limit > 0 and ctx["recommended_semantic_text"]:
        tracker.increment_api_call()
        countries = [c.strip() for c in args.country.split(",") if c.strip()] if args.country else ["CNA", "CNB"]
        sem_res = client.call_tool("semantic_search_patents", {
            "text": ctx["recommended_semantic_text"],
            "limit": sem_limit,
            "country": countries,
            "simple_legal_status": "1,2",
            "confirm_paid": True,
        })
        s_data = (sem_res.get("data") if isinstance(sem_res, dict) else None) or {}
        s_results = deduplicate_by_application(s_data.get("results") or [])
        pns = [str(r.get("pn") or "") for r in s_results if r.get("pn")]
        allowed_pns, _ = tracker.filter_and_reserve(pns)
        allowed_set = set(allowed_pns)
        kept = [r for r in s_results if str(r.get("pn") or "").upper() in allowed_set]
        for r in kept:
            r["_source_round"] = "语义检索（有效/审中）"
        candidate_pool.extend(kept)
        search_log.append({
            "type": "语义检索 (semantic_search_patents)",
            "query": ctx["invention_name"] + " + 独权1核心方案语义向量",
            "total_hits": s_data.get("total_search_result_count", len(kept)),
            "screened_count": len(kept),
            "note": f"限定法域 {','.join(countries)}，法律状态：有效/审中",
        })

    # Round 2: Boolean keyword searches
    for q_spec in ctx.get("recommended_boolean_queries") or []:
        if tracker.remaining <= 0:
            break
        b_limit = min(args.boolean_limit, tracker.remaining, 20)
        q_str = q_spec["query"]
        try:
            tracker.increment_api_call()
            b_res = client.call_tool("search_patents", {
                "query": q_str,
                "limit": b_limit,
                "confirm_paid": True,
            })
            b_data = (b_res.get("data") if isinstance(b_res, dict) else None) or {}
            b_results = deduplicate_by_application(b_data.get("results") or [])
            pns = [str(r.get("pn") or "") for r in b_results if r.get("pn")]
            allowed_pns, _ = tracker.filter_and_reserve(pns)
            allowed_set = set(allowed_pns)
            kept = [r for r in b_results if str(r.get("pn") or "").upper() in allowed_set]
            for r in kept:
                r["_source_round"] = q_spec["purpose"]
            candidate_pool.extend(kept)
            search_log.append({
                "type": f"布尔检索 ({q_spec['purpose']})",
                "query": q_str,
                "total_hits": b_data.get("total_search_result_count", len(kept)),
                "screened_count": len(kept),
                "note": "P002 关键词组合检索",
            })
        except Exception as exc:  # noqa: BLE001
            search_log.append({
                "type": f"布尔检索 ({q_spec['purpose']})",
                "query": q_str,
                "total_hits": 0,
                "screened_count": 0,
                "note": f"检索异常: {exc}",
            })

    # Deduplicate merged pool and pick top N for deep claims + legal status inspection
    merged_candidates = deduplicate_by_application(candidate_pool)
    top_candidates = merged_candidates[: args.detail_limit]
    top_pns = [str(c.get("pn") or "").upper() for c in top_candidates if c.get("pn")]

    if top_pns:
        try:
            tracker.increment_api_call()
            client.call_tool("batch_prefetch_claims", {
                "patent_numbers": top_pns,
                "confirm_paid": True,
            })
        except Exception as exc:  # noqa: BLE001
            eprint(f"WARN: batch_prefetch_claims partial error: {exc}")

    fetched_claim_pns: list[str] = []
    for cand in top_candidates:
        pn = str(cand.get("pn") or "").upper()
        if not pn:
            continue
        # Legal status
        try:
            tracker.increment_api_call()
            l_res = client.call_tool("get_legal_status", {"patent_number": pn})
            l_data = (l_res.get("data") or [{}])[0] if isinstance(l_res, dict) else {}
            p_legal = l_data.get("patent_legal") or {}
            simple_status = "、".join(p_legal.get("simple_legal_status") or [])
            detail_status = "、".join(p_legal.get("legal_status") or [])
            cand["legal_status"] = f"{simple_status}（{detail_status}）" if detail_status else (simple_status or "未知")
        except Exception as exc:  # noqa: BLE001
            cand["legal_status"] = f"待核实 ({exc})"

        # Claims
        try:
            tracker.increment_api_call()
            c_res = client.call_tool("get_claims", {"patent_number": pn, "confirm_paid": True})
            if isinstance(c_res, dict):
                structured = c_res.get("claims_structured") or []
                ind_claims = [c for c in structured if c.get("type") == "independent"]
                cand["claim_count"] = c_res.get("claim_count", len(structured))
                cand["independent_claims"] = ind_claims or structured[:1]
                cand["independent_claim_1_text"] = (
                    (ind_claims[0].get("text") if ind_claims else None)
                    or (structured[0].get("text") if structured else None)
                    or str(c_res.get("claim_text") or "")[:1500]
                )
                fetched_claim_pns.append(pn)
        except Exception as exc:  # noqa: BLE001
            cand["claims_error"] = str(exc)

    tracker.record_claims_fetched(fetched_claim_pns)
    session_used = max(len(merged_candidates), tracker.used_count - initial_used)

    output_data = {
        "invention_name": ctx["invention_name"],
        "source_title": ctx["source_title"],
        "search_date": _dt.date.today().isoformat(),
        "target_jurisdictions": [c.strip() for c in args.country.split(",") if c.strip()] if args.country else ["CN（中国）"],
        "quota_usage": {
            "session_unique_patents": session_used,
            "daily_total_used": tracker.used_count,
            "daily_limit": tracker.daily_limit,
            "remaining_today": tracker.remaining,
        },
        "target_features": ctx["decomposed_features"],
        "extracted_keywords": ctx["extracted_keywords"],
        "search_log": search_log,
        "all_screened_candidates_count": len(merged_candidates),
        "deep_fetched_candidates": top_candidates,
        "other_candidates": merged_candidates[args.detail_limit :],
    }
    write_json(args.output, output_data)
    print(
        f"Wrote FTO screening package to {args.output} "
        f"(candidates={len(merged_candidates)}, deep_claims={len(top_candidates)}, "
        f"daily_quota={tracker.used_count}/{tracker.daily_limit})"
    )
    return 0


def cmd_report(args: argparse.Namespace) -> int:
    analysis = read_json(args.analysis_json)
    md_text = build_fto_markdown(analysis)
    if args.md_output:
        args.md_output.parent.mkdir(parents=True, exist_ok=True)
        args.md_output.write_text(md_text, encoding="utf-8")
        print(f"Wrote FTO Markdown report to {args.md_output}")
    if args.docx_output:
        build_fto_docx(analysis, args.docx_output)
        print(f"Wrote FTO DOCX report to {args.docx_output} ({args.docx_output.stat().st_size} bytes)")
    if not args.md_output and not args.docx_output:
        print(md_text)
    return 0


# ---------------------------------------------------------------------------
# CLI Entrypoint
# ---------------------------------------------------------------------------

def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Paper2Patent Step 8: FTO Patent Database Screening & Report Generator (<= 100 patents/day)."
    )
    parser.add_argument(
        "--mcp-config",
        type=Path,
        default=DEFAULT_MCP_CONFIG,
        help=f"Path to MCP config JSON (default: {DEFAULT_MCP_CONFIG}).",
    )
    parser.add_argument(
        "--daily-limit",
        type=int,
        default=DAILY_PATENT_LIMIT,
        help=f"Maximum unique patents allowed per day (default: {DAILY_PATENT_LIMIT}).",
    )
    sub = parser.add_subparsers(dest="subcommand", required=True)

    # 1. quota
    p_quota = sub.add_parser("quota", help="Show or reset today's PatSnap MCP patent quota usage.")
    p_quota.add_argument("--reset", action="store_true", help="Reset today's local quota counter.")
    p_quota.set_defaults(func=cmd_quota)

    # 2. extract-features
    p_ext = sub.add_parser("extract-features", help="Decompose claim 1 features and build search queries from patent.json.")
    p_ext.add_argument("patent_json", type=Path, help="Path to drafted patent.json.")
    p_ext.add_argument("-o", "--output", type=Path, help="Optional output JSON path.")
    p_ext.set_defaults(func=cmd_extract_features)

    # 3. search
    p_search = sub.add_parser("search", help="Run boolean or semantic patent search within the 100/day limit.")
    p_search.add_argument("--mode", choices=["boolean", "semantic"], default="boolean", help="Search mode.")
    p_search.add_argument("-q", "--query", required=True, help="PatSnap boolean query or semantic text.")
    p_search.add_argument("--limit", type=int, default=15, help="Max results to fetch (default: 15).")
    p_search.add_argument("--offset", type=int, default=0, help="Result offset.")
    p_search.add_argument("--country", default="CNA,CNB", help="Country codes for semantic search (default: CNA,CNB).")
    p_search.add_argument("--legal-status", default="1,2", help="Simple legal status for semantic search (1=valid, 2=pending).")
    p_search.add_argument("-o", "--output", type=Path, help="Output JSON file path.")
    p_search.set_defaults(func=cmd_search)

    # 4. fetch-details
    p_fetch = sub.add_parser("fetch-details", help="Batch-fetch claims and legal status for specific patent numbers.")
    p_fetch.add_argument("pns", nargs="+", help="Patent publication numbers (e.g. CN113052814A CN111462008B).")
    p_fetch.add_argument("--with-biblio", action="store_true", help="Also fetch full bibliography (P012).")
    p_fetch.add_argument("-o", "--output", type=Path, help="Output JSON file path.")
    p_fetch.set_defaults(func=cmd_fetch_details)

    # 5. auto-screen
    p_auto = sub.add_parser("auto-screen", help="Run end-to-end retrieval + top claims/legal-status fetch from patent.json.")
    p_auto.add_argument("patent_json", type=Path, help="Path to drafted patent.json.")
    p_auto.add_argument("-o", "--output", type=Path, required=True, help="Output candidate package JSON (e.g. out/fto_candidates.json).")
    p_auto.add_argument("--semantic-limit", type=int, default=15, help="Max patents for semantic search round (default: 15).")
    p_auto.add_argument("--boolean-limit", type=int, default=15, help="Max patents per boolean query round (default: 15).")
    p_auto.add_argument("--detail-limit", type=int, default=8, help="Max top patents to fetch full claims + legal status (default: 8).")
    p_auto.add_argument("--country", default="CNA,CNB", help="Target jurisdictions for semantic search (default: CNA,CNB).")
    p_auto.set_defaults(func=cmd_auto_screen)

    # 6. report
    p_rep = sub.add_parser("report", help="Render FTO Markdown and DOCX reports from fto_analysis.json.")
    p_rep.add_argument("analysis_json", type=Path, help="Path to completed fto_analysis.json.")
    p_rep.add_argument("--md-output", type=Path, help="Output Markdown report path (<名称>_FTO撞库排查报告.md).")
    p_rep.add_argument("--docx-output", type=Path, help="Output Word DOCX report path (<名称>_FTO撞库排查报告.docx).")
    p_rep.set_defaults(func=cmd_report)

    return parser


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()
    try:
        return args.func(args)
    except Exception as exc:  # noqa: BLE001
        eprint(f"ERROR: {exc}")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
