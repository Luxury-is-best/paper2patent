#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Structural and formal assertions for one or more paper2patent evaluation runs.

Each run directory must contain the run's artefacts (at any depth):
  - the patent content JSON (containing `claims`, `description`, `drawings`, `support_map`, `notes`)
  - the application DOCX (`<name>.docx`)
  - the drafting notes DOCX (`<name>_撰写说明.docx`)
  - the exported PDF (`<name>.pdf`)
  - `final_message.md` (the final response delivered to the user)

Writes `grading.json` into each run directory, prints a detailed per-run and
cross-run summary, and optionally writes an aggregate summary JSON.

These checks verify form, structure, pipeline compliance, checker warnings,
and numerical traceability. Substantive quality of claim scope, enablement,
and fidelity still requires blind evaluation with `rubric.md`.

Usage:
    python <skill>/evals/grade_structure.py --source <skill>/evals/inputs/2308.00352.txt RUN_DIR [RUN_DIR ...]
    python <skill>/evals/grade_structure.py --eval-id multi-agent-llm RUN_DIR [RUN_DIR ...]
"""

from __future__ import annotations

import argparse
import copy
import json
import re
import statistics
import sys
import zipfile
from pathlib import Path
from typing import Any

EVALS_DIR = Path(__file__).resolve().parent
SKILL_DIR = EVALS_DIR.parent
sys.path.insert(0, str(SKILL_DIR / "scripts"))
from check_patent_draft import has_loop_or_decision, run_checks  # noqa: E402
from patent_common import DESCRIPTION_SECTIONS, normalize_drawings  # noqa: E402

SCOPE_WARN_CODES = {"C20", "C21", "C22", "C23", "C24", "C25", "D08", "F10"}
FORMAL_SUPPORT_WARN_CODES = {
    "C04", "C06", "C13", "C14",
    "N02", "N03", "N05",
    "A03", "A05",
    "D03", "D04", "D05", "D06", "D07",
    "F03", "F04", "F07", "F09",
    "S01", "S02", "S03", "S04",
}
REQUIRED_APP_PARTS = ["说明书摘要", "摘要附图", "权利要求书", "说明书", "说明书附图"]
LEAKED_MARKERS = (
    "来源论文",
    "材料缺口说明",
    "撰写说明",
    "待补充材料清单",
    "充分公开核查",
    "support_map",
    "claim_plan",
    "本文提出",
    "本论文",
    "我们提出",
)


def docx_paragraphs(path: Path) -> list[str]:
    """Extract paragraph texts from a DOCX file, marking drawing paragraphs as '[IMG]'."""
    with zipfile.ZipFile(path) as zf:
        xml = zf.read("word/document.xml").decode("utf-8")
    paras: list[str] = []
    for p in re.findall(r"<w:p[ >].*?</w:p>", xml, flags=re.S):
        text = "".join(re.findall(r"<w:t[^>]*>(.*?)</w:t>", p, flags=re.S)).strip()
        if text:
            paras.append(text)
        elif "<w:drawing" in p:
            paras.append("[IMG]")
        else:
            paras.append("")
    return paras


def pick(run: Path, pattern: str, exclude: str | None = None) -> Path | None:
    """Find the shallowest matching file in a run directory, ignoring preview/ and grading files."""
    ignored_names = {
        "grading.json", "run_meta.json", "blind_key.json", "summary.json",
        "fto_candidates.json", "fto_analysis.json", "fto_extra_search.json", "fto_extra_details.json",
    }
    hits = [
        p for p in run.rglob(pattern)
        if "preview" not in p.parts and p.name not in ignored_names
    ]
    if exclude:
        hits = [p for p in hits if exclude not in p.name]
    return sorted(hits, key=lambda p: (len(p.parts), len(p.name)))[0] if hits else None


def load_evals_spec(evals_json_path: Path | None = None) -> dict[str, dict[str, Any]]:
    """Load eval cases keyed by id from evals.json."""
    path = evals_json_path or (EVALS_DIR / "evals.json")
    if not path.exists():
        return {}
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    specs: dict[str, dict[str, Any]] = {}
    for group_key in ("evals", "mode_evals"):
        for item in raw.get(group_key) or []:
            if isinstance(item, dict) and item.get("id"):
                specs[str(item["id"])] = item
    return specs


def infer_eval_id(run: Path, specs: dict[str, dict[str, Any]]) -> str | None:
    """Infer the eval ID from any path component of the run directory."""
    for part in reversed(run.resolve().parts):
        if part in specs:
            return part
    return None


def _normalize_num_str(val: float) -> str:
    formatted = format(val, ".10f").rstrip("0").rstrip(".")
    return formatted if formatted and formatted != "-0" else "0"


def extract_numbers(text: str, expand_percentages: bool = False) -> set[str]:
    """Extract normalized numeric literals from text (handling commas and scientific notation)."""
    # Join comma-separated thousands like 1,024 or 10,000,000
    cleaned = re.sub(r"(?<=\d),(?=\d{3}\b)", "", text)
    found: set[str] = set()
    for raw in re.findall(r"\d+(?:\.\d+)?", cleaned):
        found.add(raw)
        if "." in raw:
            found.add(raw.rstrip("0").rstrip("."))
    # Scientific notation: 1e-4, 1.5E-3, 10^{-4}, 10^-4, 10−4
    sci_patterns = [
        r"(\d+(?:\.\d+)?)[eE]([+-−–]?\d+)",
        r"(?:(\d+(?:\.\d+)?)\s*[×xX*]\s*)?10\s*(?:\^\s*\{?\s*([+-−–]?\d+)\s*\}?|[−–-](\d+))",
    ]
    for pat in sci_patterns:
        for match in re.finditer(pat, cleaned):
            groups = match.groups()
            mant_str = groups[0] if groups[0] else "1"
            exp_str = (groups[1] or (groups[2] if len(groups) > 2 else "")).replace("−", "-").replace("–", "-")
            try:
                val = float(mant_str) * (10.0 ** int(exp_str))
                found.add(_normalize_num_str(val))
            except (ValueError, OverflowError):
                continue
    if expand_percentages:
        extra: set[str] = set()
        for item in list(found):
            try:
                fval = float(item)
            except ValueError:
                continue
            if 0 < fval <= 100:
                # Allow matching 0.831 in paper with 83.1% in draft and vice versa
                extra.add(_normalize_num_str(fval * 100.0))
                extra.add(_normalize_num_str(fval / 100.0))
        found.update(extra)
    return found


def collect_structural_numerals(content: dict[str, Any] | None) -> set[str]:
    """Collect reference numerals, step numbers, figure/table/formula/claim indices to exclude from source number audit."""
    excluded = {str(i) for i in range(0, 21)}
    if not content:
        return excluded
    figures, _ = normalize_drawings(content)
    for fig in figures:
        excluded.add(str(fig.get("figure_no", "")))
        container = fig.get("container")
        if isinstance(container, dict) and container.get("ref"):
            excluded.add(str(container["ref"]))
        for node in fig.get("nodes", []):
            if node.get("ref"):
                excluded.add(str(node["ref"]))
            for step_num in re.findall(r"S(\d{2,4})", str(node.get("label", ""))):
                excluded.add(step_num)
    return excluded


def strip_patent_numbering(text: str) -> str:
    """Remove patent-specific structural numbering (S101, [0001], 图1, 权利要求1, （301）, placeholders) before number audit."""
    # Remove inline placeholders 【待补充：…】
    text = re.sub(r"【待补充[^】]*】", " ", text)
    # Remove step numbers like S101, S1020
    text = re.sub(r"\bS\d{2,4}\b", " ", text)
    # Remove paragraph numbers like [0001]
    text = re.sub(r"\[\d{4}\]", " ", text)
    # Remove claim numbering and references ("1.", "权利要求1至6")
    text = re.sub(r"权利要求\s*[\d\s或、至到\-～~，,]+", " ", text)
    text = re.sub(r"(?:^|\n)\s*\d+\s*[\.．、]", " ", text)
    # Remove figure/table/embodiment/formula labels ("图1", "表2", "（1）", "（301）")
    text = re.sub(r"[图表]\s*\d+", " ", text)
    text = re.sub(r"[（(]\s*\d{1,4}\s*[)）]", " ", text)
    # Remove apparatus/device reference numerals attached to nouns (e.g. 装置300, 模块301, 处理器401, 存储器402)
    text = re.sub(r"(?:装置|系统|模块|单元|处理器|存储器|设备|总线|接口)\s*[1-9]\d{2}\b", " ", text)
    return text


def check_app_sections(paras: list[str], expected_fig_count: int) -> tuple[bool, str]:
    """Verify the 5 application parts, 5 description subsections, and embedded drawing images."""
    if not paras:
        return False, "DOCX为空或未生成"
    non_empty = [p for p in paras if p]
    part_indices: dict[str, int] = {}
    for part in REQUIRED_APP_PARTS:
        idx = next(
            (i for i, p in enumerate(non_empty) if re.fullmatch(rf"(?:[一二三四五六七八九十]、)?{re.escape(part)}", p)),
            -1,
        )
        part_indices[part] = idx
    missing_parts = [k for k, v in part_indices.items() if v < 0]
    if missing_parts:
        return False, f"缺少部分: {missing_parts}"
    ordered = [part_indices[p] for p in REQUIRED_APP_PARTS]
    if ordered != sorted(ordered):
        return False, f"五部分顺序错误: {part_indices}"

    # Check 5 description subsections inside 说明书
    spec_slice = non_empty[part_indices["说明书"]: part_indices["说明书附图"]]
    missing_sub = [
        heading for _, heading in DESCRIPTION_SECTIONS
        if not any(re.fullmatch(rf"(?:[一二三四五六七八九十]、)?{re.escape(heading)}", p) for p in spec_slice)
    ]
    if missing_sub:
        return False, f"说明书缺少小节: {missing_sub}"

    # Check [IMG] in 摘要附图 and 说明书附图
    abs_fig_slice = non_empty[part_indices["摘要附图"] + 1: part_indices["权利要求书"]]
    spec_fig_slice = non_empty[part_indices["说明书附图"] + 1:]
    abs_imgs = sum(1 for p in abs_fig_slice if p == "[IMG]")
    spec_imgs = sum(1 for p in spec_fig_slice if p == "[IMG]")
    if abs_imgs < 1:
        return False, "摘要附图下未嵌入图片([IMG])"
    if expected_fig_count > 0 and spec_imgs != expected_fig_count:
        return False, f"说明书附图图片数({spec_imgs})与JSON附图数({expected_fig_count})不一致"
    if spec_imgs < 1:
        return False, "说明书附图下未嵌入图片([IMG])"
    return True, f"5大部+5小节齐全，摘要附图={abs_imgs}张，说明书附图={spec_imgs}张"


def grade(
    run: Path,
    source_text: str | None = None,
    eval_spec: dict[str, Any] | None = None,
) -> dict[str, Any]:
    results: list[dict[str, Any]] = []

    def add(text: str, passed: bool, evidence: str, category: str = "core") -> None:
        results.append({
            "text": text,
            "passed": bool(passed),
            "evidence": evidence,
            "category": category,
        })

    app = pick(run, "*.docx", exclude="撰写说明")
    if app and "FTO" in app.name:
        # Ensure FTO report DOCX is not mistaken for the main application DOCX
        hits = [
            p for p in run.rglob("*.docx")
            if "preview" not in p.parts and "撰写说明" not in p.name and "FTO" not in p.name
        ]
        app = sorted(hits, key=lambda p: (len(p.parts), len(p.name)))[0] if hits else None
    notes_doc = pick(run, "*撰写说明*.docx")
    pdf = pick(run, "*.pdf")
    final_msg = pick(run, "final_message.md")
    content: dict[str, Any] | None = None
    content_path: Path | None = None
    for candidate in sorted(run.rglob("*.json"), key=lambda p: (len(p.parts), len(p.name))):
        if candidate.name in {
            "grading.json", "run_meta.json", "blind_key.json", "summary.json",
            "fto_candidates.json", "fto_analysis.json", "fto_extra_search.json", "fto_extra_details.json",
        }:
            continue
        try:
            data = json.loads(candidate.read_text(encoding="utf-8-sig"))
        except (ValueError, OSError):
            continue
        if isinstance(data, dict) and ("claims" in data or "description" in data):
            content = data
            content_path = candidate
            break

    # 1. Deliverables present
    add(
        "核心交付产物齐全（申请文件DOCX、撰写说明DOCX、PDF、JSON）",
        bool(app and notes_doc and pdf and content),
        f"app={app and app.name}, notes={notes_doc and notes_doc.name}, pdf={pdf and pdf.name}, json={content_path and content_path.name}",
    )

    # 2. Five parts & five description sections & embedded images
    paras = docx_paragraphs(app) if app else []
    figures, _ = normalize_drawings(content) if content else ([], [])
    sections_ok, sections_ev = check_app_sections(paras, len(figures))
    add("申请文件五部分与说明书五小节齐全且含附图图像", sections_ok, sections_ev)

    # 3. Checker zero errors
    report = run_checks(copy.deepcopy(content)) if content else None
    errors = [i for i in (report.items if report else []) if i["level"] == "ERROR"]
    add(
        "检查脚本 0 个错误（ERROR）",
        report is not None and not errors,
        "; ".join(f"{i['code']} {i['where']}: {i['message'][:40]}" for i in errors[:5]) or "0 errors",
    )

    # 4. Scope & meta commentary warnings
    warn_codes = sorted({i["code"] for i in (report.items if report else []) if i["level"] == "WARN"})
    scope_hits = [c for c in warn_codes if c in SCOPE_WARN_CODES]
    add(
        "无范围与编稿备注类警告（C20–C25、D08、F10）",
        report is not None and not scope_hits,
        f"scope_warnings={scope_hits}" if scope_hits else f"0 scope warnings (all warnings: {warn_codes})",
    )

    # 5. Formal, antecedent basis & support/sufficiency warnings
    formal_hits = [c for c in warn_codes if c in FORMAL_SUPPORT_WARN_CODES]
    add(
        "无引用基础、语域与支撑核查类警告（C14、D03–D04、F03–F04、F07、S01–S04）",
        report is not None and not formal_hits,
        f"formal_support_warnings={formal_hits}" if formal_hits else "0 formal/support warnings",
    )

    # 6. Claim category layout
    claims_list = (content or {}).get("claims") or []
    claims_joined = "\n".join(str(c) for c in claims_list)
    has_method = bool(re.search(r"1\s*[\.．、]\s*一种[^，,。]{1,50}方法", claims_joined))
    has_apparatus = bool(re.search(r"一种[^，,。]{1,50}(?:装置|系统)", claims_joined))
    has_device = "电子设备" in claims_joined or "计算设备" in claims_joined
    has_medium = "存储介质" in claims_joined
    layout_ok = has_method and has_apparatus and has_device and has_medium
    add(
        "保护客体布局完整（方法 + 装置/系统 + 电子设备 + 存储介质）",
        layout_ok,
        f"method={has_method}, apparatus={has_apparatus}, device={has_device}, medium={has_medium}",
    )

    # 7. Clean application file (no leaked notes or paper self-references)
    joined_app = "\n".join(paras)
    leaked = [k for k in LEAKED_MARKERS if k in joined_app]
    add(
        "申请文件正文纯净（不含非申请内容或论文自称）",
        app is not None and not leaked,
        f"found: {leaked}" if leaked else "clean",
    )

    # 8. Figure captions under 说明书附图
    heads = [i for i, p in enumerate(paras) if re.fullmatch(r"(?:[一二三四五六七八九十]、)?说明书附图", p)]
    tail = [p for p in paras[heads[-1] + 1:] if p and p != "[IMG]"] if heads else ["(no 说明书附图)"]
    captions_ok = bool(heads and tail and all(re.fullmatch(r"图\s*\d+", p) for p in tail))
    add("说明书附图下仅有“图N”标准图注", captions_ok, f"{len(tail)} captions: {tail[:6]}")

    # 9. Drafting notes completeness (handover, sufficiency, support_map, disclosure/risks)
    notes_text = "\n".join(docx_paragraphs(notes_doc)) if notes_doc else ""
    notes_obj = (content or {}).get("notes") if isinstance((content or {}).get("notes"), dict) else {}
    support_obj = (content or {}).get("support_map") if isinstance((content or {}).get("support_map"), dict) else {}
    handover_ok = (
        "待补充材料清单" in notes_text
        and "权属" in notes_text
        and "公开情况" in notes_text
        and "现有技术检索" in notes_text
    )
    sufficiency_ok = (
        "充分公开核查" in notes_text
        and isinstance(notes_obj.get("sufficiency"), list)
        and len(notes_obj.get("sufficiency") or []) > 0
    )
    support_ok = (
        "权利要求—论文出处对照" in notes_text
        and bool(support_obj)
        and len(support_obj) >= len(claims_list)
    )
    add(
        "撰写说明含集中交接清单、充分公开核查与权利要求出处对照",
        bool(notes_doc and handover_ok and sufficiency_ok and support_ok),
        f"handover={handover_ok}, sufficiency={sufficiency_ok}, support_map={len(support_obj)}/{len(claims_list)}",
    )

    # 10. Final message check
    if final_msg and final_msg.exists():
        msg_text = final_msg.read_text(encoding="utf-8", errors="ignore").strip()
        has_disclaimer = any(k in msg_text for k in ("不构成法律意见", "初稿", "草稿"))
        has_handover_ref = any(k in msg_text for k in ("待补充", "撰写说明", "交接"))
        has_attorney_ref = any(k in msg_text for k in ("专利代理", "代理师", "现有技术检索"))
        msg_ok = bool(len(msg_text) >= 50 and has_disclaimer and has_handover_ref and has_attorney_ref)
        add(
            "交付消息（final_message.md）要素齐全",
            msg_ok,
            f"len={len(msg_text)}, disclaimer={has_disclaimer}, handover={has_handover_ref}, attorney={has_attorney_ref}",
        )
    else:
        add("交付消息（final_message.md）要素齐全", False, "missing final_message.md")

    # 11. No workaround scripts
    workaround = [
        p.name for p in run.rglob("*.py")
        if re.search(r"draw|font|cjk|postprocess|render|fix", p.name, flags=re.I)
    ]
    add("未编写外部绕过脚本", not workaround, f"{workaround}" if workaround else "none")

    # 12. Source number traceability audit
    if source_text is not None:
        raw_draft_text = claims_joined + "\n" + json.dumps((content or {}).get("description", {}), ensure_ascii=False)
        cleaned_draft_text = strip_patent_numbering(raw_draft_text)
        out_nums = extract_numbers(cleaned_draft_text, expand_percentages=False)
        src_nums = extract_numbers(source_text, expand_percentages=True)
        excluded_nums = collect_structural_numerals(content)
        unmatched = sorted(
            x for x in (out_nums - src_nums - excluded_nums)
            if not re.fullmatch(r"\d", x)
        )
        add(
            "正文中技术数值均可追溯至源论文",
            not unmatched,
            f"unmatched ({len(unmatched)}): {unmatched[:12]}" if unmatched else "all numbers traceable",
        )

    # 13. Eval-specific structural assertions (from evals.json)
    assertions = (eval_spec or {}).get("structural_assertions") if isinstance(eval_spec, dict) else None
    if isinstance(assertions, dict) and content:
        spec_issues: list[str] = []
        if assertions.get("require_loop_in_drawings"):
            if not any(has_loop_or_decision(f) for f in figures):
                spec_issues.append("附图中缺少判断节点(decision)或反馈回环")
        claim1_text = claims_list[0] if claims_list else ""
        for forbidden in assertions.get("claim1_forbidden_terms") or []:
            if forbidden in claim1_text:
                spec_issues.append(f"权利要求1包含应下沉的特定词“{forbidden}”")
        notes_dump = json.dumps(notes_obj, ensure_ascii=False) + "\n" + notes_text
        must_notes = assertions.get("notes_must_contain_any") or []
        if must_notes and not any(k in notes_dump for k in must_notes):
            spec_issues.append(f"撰写说明未包含关键词之一: {must_notes}")
        must_risk = assertions.get("notes_risk_must_contain_any") or []
        if must_risk and not any(k in notes_dump for k in must_risk):
            spec_issues.append(f"撰写说明风险分析未包含关键词之一: {must_risk}")
        must_msg = assertions.get("final_message_must_contain_any") or []
        if must_msg and final_msg and final_msg.exists():
            msg_body = final_msg.read_text(encoding="utf-8", errors="ignore")
            if not any(k in msg_body for k in must_msg):
                spec_issues.append(f"final_message.md未包含关键词之一: {must_msg}")
        add(
            f"论文专项结构断言（{eval_spec.get('id')}）",
            not spec_issues,
            "; ".join(spec_issues) if spec_issues else "passed all paper-specific assertions",
            category="paper_specific",
        )

    passed = sum(1 for r in results if r["passed"])
    grading = {
        "run_dir": str(run),
        "eval_id": (eval_spec or {}).get("id"),
        "expectations": results,
        "summary": {
            "passed": passed,
            "failed": len(results) - passed,
            "total": len(results),
            "pass_rate": round(passed / len(results), 3) if results else 0.0,
            "all_passed": passed == len(results),
        },
    }
    (run / "grading.json").write_text(json.dumps(grading, ensure_ascii=False, indent=2), encoding="utf-8")
    return grading


def summarize_runs(gradings: list[dict[str, Any]]) -> dict[str, Any]:
    """Compute cross-run stability and per-check pass rates across multiple runs."""
    if not gradings:
        return {}
    rates = [g["summary"]["pass_rate"] for g in gradings]
    all_passed_count = sum(1 for g in gradings if g["summary"]["all_passed"])
    by_check: dict[str, list[bool]] = {}
    for g in gradings:
        for exp in g["expectations"]:
            by_check.setdefault(exp["text"], []).append(bool(exp["passed"]))
    check_summary = [
        {
            "text": text,
            "passed_runs": sum(vals),
            "total_runs": len(vals),
            "pass_rate": round(sum(vals) / len(vals), 3),
            "flaky": 0 < sum(vals) < len(vals),
        }
        for text, vals in by_check.items()
    ]
    return {
        "total_runs": len(gradings),
        "fully_passing_runs": all_passed_count,
        "mean_pass_rate": round(statistics.mean(rates), 3),
        "stdev_pass_rate": round(statistics.stdev(rates), 3) if len(rates) > 1 else 0.0,
        "min_pass_rate": min(rates),
        "max_pass_rate": max(rates),
        "checks": check_summary,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("runs", nargs="+", type=Path, help="One or more run directories to grade.")
    parser.add_argument(
        "--source",
        type=Path,
        required=False,
        help="Paper text (pdftotext -layout output) for the numerical traceability audit.",
    )
    parser.add_argument(
        "--eval-id",
        type=str,
        default=None,
        help="Optional eval case ID from evals.json (auto-inferred from run path if omitted).",
    )
    parser.add_argument(
        "--evals-json",
        type=Path,
        default=None,
        help="Path to evals.json (defaults to <skill>/evals/evals.json).",
    )
    parser.add_argument(
        "--summary-json",
        type=Path,
        default=None,
        help="Optional file path to write aggregated multi-run summary JSON.",
    )
    args = parser.parse_args()

    specs = load_evals_spec(args.evals_json)
    explicit_source = args.source.read_text(encoding="utf-8", errors="ignore") if args.source else None

    all_gradings: list[dict[str, Any]] = []
    for run in args.runs:
        eval_id = args.eval_id or infer_eval_id(run, specs)
        eval_spec = specs.get(eval_id) if eval_id else None
        source_text = explicit_source
        if source_text is None and eval_spec and eval_spec.get("source_txt"):
            candidate_src = EVALS_DIR / str(eval_spec["source_txt"])
            if candidate_src.exists():
                source_text = candidate_src.read_text(encoding="utf-8", errors="ignore")

        g = grade(run, source_text=source_text, eval_spec=eval_spec)
        all_gradings.append(g)
        label = f" (eval={eval_id})" if eval_id else ""
        print(f"== {run}{label}: {g['summary']['passed']}/{g['summary']['total']} ({g['summary']['pass_rate']:.1%})")
        for r in g["expectations"]:
            status = "PASS" if r["passed"] else "FAIL"
            print(f"   [{status}] {r['text']} -- {r['evidence'][:110]}")

    if len(all_gradings) > 1:
        agg = summarize_runs(all_gradings)
        print(
            f"\n== Aggregate ({agg['total_runs']} runs): "
            f"full_pass={agg['fully_passing_runs']}/{agg['total_runs']}, "
            f"mean_rate={agg['mean_pass_rate']:.1%} (sd={agg['stdev_pass_rate']:.1%})"
        )
        flaky = [c["text"] for c in agg["checks"] if c["flaky"]]
        if flaky:
            print(f"   Flaky checks across runs: {flaky}")
    else:
        agg = summarize_runs(all_gradings)

    if args.summary_json:
        args.summary_json.parent.mkdir(parents=True, exist_ok=True)
        args.summary_json.write_text(
            json.dumps({"aggregate": agg, "runs": all_gradings}, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
