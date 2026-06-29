#!/usr/bin/env python3
"""Generate a Word report for online ActMemEval observation traces."""

from __future__ import annotations

import json
import zipfile
from datetime import datetime
from html import escape
from pathlib import Path
from typing import Sequence


REPO_ROOT = Path(__file__).resolve().parents[1]
RESULT_PATH = REPO_ROOT / "eval" / "output" / "actmem_eval_results.json"
OUT_PATH = REPO_ROOT / "reports" / "Cross-Agent在线评测观测报告.docx"


def main() -> None:
    if not RESULT_PATH.exists():
        raise SystemExit(f"Missing evaluation result: {RESULT_PATH}")
    result = json.loads(RESULT_PATH.read_text(encoding="utf-8"))
    OUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    _write_docx(OUT_PATH, result)
    print(str(OUT_PATH))


def _write_docx(path: Path, result: dict) -> None:
    with zipfile.ZipFile(path, "w", compression=zipfile.ZIP_DEFLATED) as z:
        z.writestr("[Content_Types].xml", _content_types())
        z.writestr("_rels/.rels", _rels())
        z.writestr("word/_rels/document.xml.rels", _document_rels())
        z.writestr("word/styles.xml", _styles_xml())
        z.writestr("word/document.xml", _document_xml(result))


def _document_xml(result: dict) -> str:
    summary = result.get("summary", {})
    body: list[str] = []
    body.append(_paragraph("Cross-Agent 在线评测观测报告", "Title"))
    body.append(_paragraph("ActMemEval 前 5 条关键中间变量与状态变化", "Subtitle"))
    body.append(
        _table(
            ["字段", "内容"],
            [
                ("生成时间", datetime.now().strftime("%Y-%m-%d %H:%M")),
                ("评测数据", summary.get("dataset_path", "")),
                ("样本数量", str(summary.get("limit", ""))),
                ("Top-K", str(summary.get("top_k", ""))),
                ("回答生成器", summary.get("answer_generator", "")),
                ("Recall@K", _fmt(summary.get("recall_at_k"))),
                ("MRR", _fmt(summary.get("mrr"))),
                ("Answer Token F1", _fmt(summary.get("answer_token_f1"))),
            ],
            widths=[2600, 6760],
        )
    )
    body.append(_paragraph("1. 架构观测点", "Heading1"))
    for item in [
        "写入链路：每条样本先读取 haystack_sessions，再将每个 Session 转为 event 类型记忆候选。",
        "治理链路：Governor 对候选执行 create、reinforce 或 reject，SQLite 同步维护 memory_state 和 memory_events。",
        "读取链路：Reader 记录查询扩展词、初始 Top 会话、反馈词、最终 Top 会话和每条证据得分。",
        "生成链路：在线配置下 LLMAnswerGenerator 使用 EvidenceBundle 生成回答，ResponseGuard 记录实际使用的 memory_id。",
    ]:
        body.append(_paragraph(item, "ListParagraph", bullet=True))

    for index, item in enumerate(result.get("items", []), start=1):
        body.append(_paragraph(f"2.{index} 样本 {item.get('question_id', '')}", "Heading1"))
        body.append(_paragraph("问题", "Heading2"))
        body.append(_paragraph(_clip(item.get("question", ""), 900), "Normal"))
        body.append(_paragraph("状态变化", "Heading2"))
        st = item.get("state_transition", {})
        body.append(
            _table(
                ["阶段", "active_memories", "all_memories", "events"],
                [
                    _count_row("写入前", st.get("before_ingest", {})),
                    _count_row("写入后", st.get("after_ingest", {})),
                    _count_row("检索后", st.get("after_search", {})),
                ],
                widths=[2600, 2300, 2300, 2160],
            )
        )
        body.append(_paragraph("写入摘要", "Heading2"))
        ingest = item.get("ingest_stats", {})
        trace = item.get("ingest_trace", {})
        body.append(
            _table(
                ["变量", "值"],
                [
                    ("候选 Session 数", str(trace.get("haystack_session_count", ""))),
                    ("抽取候选数", str(ingest.get("extracted", ""))),
                    ("新增记忆数", str(ingest.get("created", ""))),
                    ("强化记忆数", str(ingest.get("reinforced", ""))),
                    ("拒绝记忆数", str(ingest.get("rejected", ""))),
                    ("金标 Session", ", ".join(trace.get("gold_session_ids", []))),
                ],
                widths=[2600, 6760],
            )
        )
        body.append(_paragraph("金标会话写入状态", "Heading2"))
        gold_rows = []
        for state in trace.get("gold_session_write_states", []):
            gold_rows.append(
                (
                    state.get("session_id", ""),
                    state.get("occurred_at", ""),
                    str(state.get("created", "")),
                    str(state.get("rejected", "")),
                )
            )
        body.append(_table(["session_id", "时间", "created", "rejected"], gold_rows or [("无", "", "", "")], widths=[3000, 3000, 1600, 1760]))
        body.append(_paragraph("检索计划与 Trace", "Heading2"))
        search_trace = item.get("trace", {})
        body.append(
            _table(
                ["变量", "值"],
                [
                    ("候选记忆数", str(search_trace.get("candidate_count", ""))),
                    ("初始 Top Sessions", ", ".join(search_trace.get("initial_top_sessions", []))),
                    ("反馈词", ", ".join(search_trace.get("feedback_terms", []))),
                    ("最终 Top Sessions", ", ".join(search_trace.get("final_top_sessions", []))),
                ],
                widths=[2600, 6760],
            )
        )
        body.append(_paragraph("Top 证据包", "Heading2"))
        evidence_rows = []
        for ev in item.get("evidence_items", [])[:5]:
            evidence_rows.append(
                (
                    str(ev.get("rank", "")),
                    ev.get("source_session_id", ""),
                    _fmt(ev.get("score")),
                    _clip(ev.get("snippet", ""), 220),
                )
            )
        body.append(_table(["Rank", "Session", "Score", "Snippet"], evidence_rows or [("", "", "", "")], widths=[900, 2500, 1200, 4760]))
        body.append(_paragraph("回答与指标", "Heading2"))
        metrics = item.get("metrics", {})
        body.append(
            _table(
                ["变量", "值"],
                [
                    ("预测回答", _clip(item.get("prediction", ""), 900)),
                    ("标准答案", _clip(item.get("gold_answer", ""), 900)),
                    ("命中 Session", ", ".join(item.get("retrieved_session_ids", []))),
                    ("Recall@K", _fmt(metrics.get("recall_at_k"))),
                    ("MRR", _fmt(metrics.get("mrr"))),
                    ("Answer Token F1", _fmt(metrics.get("answer_token_f1"))),
                ],
                widths=[2600, 6760],
            )
        )
    body.append(_sect_pr())
    return (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">'
        '<w:background w:color="FFFFFF"/>'
        f"<w:body>{''.join(body)}</w:body></w:document>"
    )


def _count_row(label: str, counts: dict) -> tuple[str, str, str, str]:
    return (
        label,
        str(counts.get("active_memories", "")),
        str(counts.get("all_memories", "")),
        str(counts.get("events", "")),
    )


def _paragraph(text: str, style: str = "Normal", bullet: bool = False) -> str:
    ppr = f'<w:pStyle w:val="{style}"/>'
    if bullet:
        ppr += '<w:ind w:left="420" w:hanging="220"/>'
    run = _run("• " + text if bullet else text)
    return f"<w:p><w:pPr>{ppr}</w:pPr>{run}</w:p>"


def _run(text: str, bold: bool = False) -> str:
    bold_xml = "<w:b/>" if bold else ""
    return f'<w:r><w:rPr>{bold_xml}<w:color w:val="000000"/></w:rPr><w:t>{escape(str(text))}</w:t></w:r>'


def _table(headers: Sequence[str], rows: Sequence[Sequence[str]], widths: Sequence[int]) -> str:
    grid = "".join(f'<w:gridCol w:w="{width}"/>' for width in widths)
    total = sum(widths)
    out = [
        f'<w:tbl><w:tblPr><w:tblW w:w="{total}" w:type="dxa"/>'
        '<w:tblInd w:w="120" w:type="dxa"/>'
        '<w:tblBorders><w:top w:val="single" w:sz="4" w:color="DADCE0"/>'
        '<w:left w:val="single" w:sz="4" w:color="DADCE0"/>'
        '<w:bottom w:val="single" w:sz="4" w:color="DADCE0"/>'
        '<w:right w:val="single" w:sz="4" w:color="DADCE0"/>'
        '<w:insideH w:val="single" w:sz="4" w:color="DADCE0"/>'
        '<w:insideV w:val="single" w:sz="4" w:color="DADCE0"/>'
        '</w:tblBorders></w:tblPr>'
        f"<w:tblGrid>{grid}</w:tblGrid>"
    ]
    out.append(_row(headers, widths, header=True))
    for row in rows:
        out.append(_row(row, widths, header=False))
    out.append("</w:tbl>")
    return "".join(out)


def _row(values: Sequence[str], widths: Sequence[int], header: bool) -> str:
    cells = []
    for idx, value in enumerate(values):
        width = widths[min(idx, len(widths) - 1)]
        fill = '<w:shd w:fill="F2F4F7"/>' if header else '<w:shd w:fill="FFFFFF"/>'
        cells.append(
            f'<w:tc><w:tcPr><w:tcW w:w="{width}" w:type="dxa"/>{fill}'
            '<w:tcMar><w:top w:w="80" w:type="dxa"/>'
            '<w:bottom w:w="80" w:type="dxa"/><w:start w:w="120" w:type="dxa"/>'
            '<w:end w:w="120" w:type="dxa"/></w:tcMar></w:tcPr>'
            f'<w:p>{_run(value, bold=header)}</w:p></w:tc>'
        )
    return f"<w:tr>{''.join(cells)}</w:tr>"


def _fmt(value) -> str:
    if value is None or value == "":
        return ""
    try:
        return f"{float(value):.4f}"
    except (TypeError, ValueError):
        return str(value)


def _clip(text: str, limit: int) -> str:
    text = " ".join(str(text).split())
    if len(text) <= limit:
        return text
    return text[: limit - 1].rstrip() + "..."


def _sect_pr() -> str:
    return (
        '<w:sectPr><w:pgSz w:w="12240" w:h="15840"/>'
        '<w:pgMar w:top="1440" w:right="1440" w:bottom="1440" w:left="1440" '
        'w:header="708" w:footer="708" w:gutter="0"/></w:sectPr>'
    )


def _content_types() -> str:
    return (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
        '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
        '<Default Extension="xml" ContentType="application/xml"/>'
        '<Override PartName="/word/document.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"/>'
        '<Override PartName="/word/styles.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.styles+xml"/>'
        "</Types>"
    )


def _rels() -> str:
    return (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
        '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="word/document.xml"/>'
        "</Relationships>"
    )


def _document_rels() -> str:
    return (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships"/>'
    )


def _styles_xml() -> str:
    return (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<w:styles xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">'
        '<w:style w:type="paragraph" w:default="1" w:styleId="Normal"><w:name w:val="Normal"/>'
        '<w:rPr><w:rFonts w:ascii="Calibri" w:hAnsi="Calibri"/><w:sz w:val="22"/><w:color w:val="000000"/></w:rPr>'
        '<w:pPr><w:spacing w:after="120" w:line="264" w:lineRule="auto"/></w:pPr></w:style>'
        '<w:style w:type="paragraph" w:styleId="Title"><w:name w:val="Title"/>'
        '<w:basedOn w:val="Normal"/><w:rPr><w:b/><w:color w:val="0B2545"/><w:sz w:val="40"/></w:rPr>'
        '<w:pPr><w:spacing w:after="160"/></w:pPr></w:style>'
        '<w:style w:type="paragraph" w:styleId="Subtitle"><w:name w:val="Subtitle"/>'
        '<w:basedOn w:val="Normal"/><w:rPr><w:color w:val="555555"/><w:sz w:val="24"/></w:rPr>'
        '<w:pPr><w:spacing w:after="240"/></w:pPr></w:style>'
        '<w:style w:type="paragraph" w:styleId="Heading1"><w:name w:val="heading 1"/>'
        '<w:basedOn w:val="Normal"/><w:rPr><w:b/><w:color w:val="2E74B5"/><w:sz w:val="32"/></w:rPr>'
        '<w:pPr><w:spacing w:before="320" w:after="160"/></w:pPr></w:style>'
        '<w:style w:type="paragraph" w:styleId="Heading2"><w:name w:val="heading 2"/>'
        '<w:basedOn w:val="Normal"/><w:rPr><w:b/><w:color w:val="2E74B5"/><w:sz w:val="26"/></w:rPr>'
        '<w:pPr><w:spacing w:before="240" w:after="120"/></w:pPr></w:style>'
        '<w:style w:type="paragraph" w:styleId="ListParagraph"><w:name w:val="List Paragraph"/>'
        '<w:basedOn w:val="Normal"/><w:pPr><w:spacing w:after="80"/></w:pPr></w:style>'
        "</w:styles>"
    )


if __name__ == "__main__":
    main()
