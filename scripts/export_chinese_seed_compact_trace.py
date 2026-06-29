#!/usr/bin/env python3
"""Export a compact per-round JSON and DOCX from the real Chinese seed run."""

from __future__ import annotations

import argparse
import json
import zipfile
from copy import deepcopy
from datetime import datetime
from html import escape
from pathlib import Path
from typing import Any, Sequence


REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_INPUT = REPO_ROOT / "eval" / "output" / "chinese_seed_api_conversation.json"
DEFAULT_JSON = REPO_ROOT / "eval" / "output" / "chinese_seed_compact_trace.json"
DEFAULT_DOCX = REPO_ROOT / "eval" / "output" / "chinese_seed_compact_trace.docx"


def main() -> None:
    parser = argparse.ArgumentParser(description="Export compact Chinese seed trace.")
    parser.add_argument("--input", default=str(DEFAULT_INPUT))
    parser.add_argument("--json", default=str(DEFAULT_JSON))
    parser.add_argument("--docx", default=str(DEFAULT_DOCX))
    parser.add_argument("--no-docx", action="store_true")
    args = parser.parse_args()

    source = json.loads(Path(args.input).read_text(encoding="utf-8"))
    compact = _build_compact(source, Path(args.input))
    json_path = Path(args.json)
    docx_path = Path(args.docx)
    json_path.parent.mkdir(parents=True, exist_ok=True)
    json_path.write_text(json.dumps(compact, ensure_ascii=False, indent=2), encoding="utf-8")
    if not args.no_docx:
        _write_docx(docx_path, compact)
    print(str(json_path))
    if not args.no_docx:
        print(str(docx_path))


def _build_compact(source: dict[str, Any], input_path: Path) -> dict[str, Any]:
    rounds: list[dict[str, Any]] = []
    projection: dict[str, dict[str, Any]] = {}
    for item in source["conversation"]:
        memory = item["memory"]
        for row in memory["state_delta"].get("created", []):
            projection[row["memory_id"]] = deepcopy(row)
        for pair in memory["state_delta"].get("updated", []):
            projection[pair["after"]["memory_id"]] = deepcopy(pair["after"])
        memory_after_round = sorted(
            (
                {
                    "memory_id": row["memory_id"],
                    "type": row["type"],
                    "predicate": row["predicate"],
                    "scope": row["scope"],
                    "value_json": row["value_json"],
                    "status": row["status"],
                    "sensitivity": row["sensitivity"],
                    "source_session_id": row["source_session_id"],
                    "valid_from": row["valid_from"],
                    "valid_to": row["valid_to"],
                    "supersedes": row["supersedes"],
                }
                for row in projection.values()
            ),
            key=lambda row: (row["predicate"], row["scope"], row["memory_id"]),
        )
        rounds.append(
            {
                "round": item["round"],
                "date": item["date"],
                "session_id": item["session_id"],
                "model_input": item["api_call"]["messages_sent_to_api"],
                "retrieved_long_term_memory": memory["evidence"],
                "answer": item["assistant"],
                "long_term_memory_after_round": memory_after_round,
            }
        )

    return {
        "metadata": {
            "source_file": source["metadata"]["source_seed"],
            "conversation_result": str(input_path.resolve()),
            "record_type": "compact_round_trace",
            "round_count": len(rounds),
            "model": source["metadata"]["model"],
            "generated_at": datetime.now().strftime("%Y-%m-%d %H:%M"),
            "flow": source["metadata"]["flow"],
        },
        "rounds": rounds,
    }


def _write_docx(path: Path, compact: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(path, "w", compression=zipfile.ZIP_DEFLATED) as z:
        z.writestr("[Content_Types].xml", _content_types())
        z.writestr("_rels/.rels", _rels())
        z.writestr("word/_rels/document.xml.rels", _document_rels())
        z.writestr("word/styles.xml", _styles_xml())
        z.writestr("word/document.xml", _document_xml(compact))


def _document_xml(compact: dict[str, Any]) -> str:
    body: list[str] = []
    md = compact["metadata"]
    body.append(_paragraph("中文长期记忆简洁逐轮记录", "Title"))
    body.append(_paragraph("每轮仅保留送入模型内容、检索到的长期记忆、回答、轮末长期记忆全量状态", "Subtitle"))
    body.append(
        _table(
            ["字段", "内容"],
            [
                ("生成时间", md.get("generated_at", "")),
                ("轮数", str(md.get("round_count", ""))),
                ("模型", md.get("model", "")),
                ("执行顺序", md.get("flow", "")),
            ],
            widths=[2200, 6960],
        )
    )
    for item in compact["rounds"]:
        body.append(_paragraph(f"第 {item['round']} 轮｜{item['date']}｜{item['session_id']}", "Heading1"))
        body.append(_paragraph("送入模型的内容", "Heading2"))
        body.append(_paragraph(_clip(json.dumps(item["model_input"], ensure_ascii=False), 2200), "Normal"))
        body.append(_paragraph("从长期记忆检索到的内容", "Heading2"))
        retrieved_rows = [
            (
                row.get("memory_id", ""),
                row.get("predicate", ""),
                row.get("scope", ""),
                row.get("status", ""),
                _clip(row.get("snippet", ""), 360),
            )
            for row in item["retrieved_long_term_memory"]
        ]
        body.append(_table(["memory_id", "predicate", "scope", "status", "snippet"], retrieved_rows or [("", "", "", "", "")], widths=[2200, 1700, 1700, 1100, 2660]))
        body.append(_paragraph("回答", "Heading2"))
        body.append(_paragraph(item["answer"], "Normal"))
        body.append(_paragraph("本轮结束后的长期记忆", "Heading2"))
        memory_rows = [
            (
                row.get("memory_id", ""),
                row.get("predicate", ""),
                row.get("scope", ""),
                row.get("status", ""),
                _clip(row.get("value_json", ""), 420),
            )
            for row in item["long_term_memory_after_round"]
        ]
        body.append(_table(["memory_id", "predicate", "scope", "status", "value_json"], memory_rows or [("", "", "", "", "")], widths=[2200, 1700, 1700, 1100, 2660]))
    body.append(_sect_pr())
    return (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">'
        '<w:background w:color="FFFFFF"/>'
        f"<w:body>{''.join(body)}</w:body></w:document>"
    )


def _paragraph(text: str, style: str = "Normal") -> str:
    return f'<w:p><w:pPr><w:pStyle w:val="{style}"/></w:pPr>{_run(text)}</w:p>'


def _run(text: str, bold: bool = False) -> str:
    bold_xml = "<w:b/>" if bold else ""
    return f'<w:r><w:rPr>{bold_xml}<w:color w:val="000000"/></w:rPr><w:t xml:space="preserve">{escape(str(text))}</w:t></w:r>'


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
        '<w:rPr><w:rFonts w:ascii="Arial" w:hAnsi="Arial"/><w:sz w:val="20"/><w:color w:val="000000"/></w:rPr>'
        '<w:pPr><w:spacing w:after="120" w:line="280" w:lineRule="auto"/></w:pPr></w:style>'
        '<w:style w:type="paragraph" w:styleId="Title"><w:name w:val="Title"/>'
        '<w:basedOn w:val="Normal"/><w:rPr><w:b/><w:color w:val="000000"/><w:sz w:val="36"/></w:rPr>'
        '<w:pPr><w:spacing w:after="120"/></w:pPr></w:style>'
        '<w:style w:type="paragraph" w:styleId="Subtitle"><w:name w:val="Subtitle"/>'
        '<w:basedOn w:val="Normal"/><w:rPr><w:color w:val="555555"/><w:sz w:val="22"/></w:rPr>'
        '<w:pPr><w:spacing w:after="240"/></w:pPr></w:style>'
        '<w:style w:type="paragraph" w:styleId="Heading1"><w:name w:val="heading 1"/>'
        '<w:basedOn w:val="Normal"/><w:rPr><w:b/><w:color w:val="000000"/><w:sz w:val="28"/></w:rPr>'
        '<w:pPr><w:spacing w:before="260" w:after="120"/></w:pPr></w:style>'
        '<w:style w:type="paragraph" w:styleId="Heading2"><w:name w:val="heading 2"/>'
        '<w:basedOn w:val="Normal"/><w:rPr><w:b/><w:color w:val="222222"/><w:sz w:val="22"/></w:rPr>'
        '<w:pPr><w:spacing w:before="180" w:after="80"/></w:pPr></w:style>'
        "</w:styles>"
    )


if __name__ == "__main__":
    main()
