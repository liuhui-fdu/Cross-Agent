#!/usr/bin/env python3
"""Generate a structured Word report without third-party dependencies."""

from __future__ import annotations

import json
import sys
import zipfile
from datetime import datetime
from html import escape
from pathlib import Path
from typing import Iterable, List, Sequence, Tuple


REPO_ROOT = Path(__file__).resolve().parents[1]
OUT_PATH = REPO_ROOT / "reports" / "Cross-Agent架构设计报告.docx"
SUMMARY_PATH = REPO_ROOT / "eval" / "output" / "actmem_eval_summary.json"


def main() -> None:
    summary = _load_summary()
    sections = _build_sections(summary)
    OUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    _write_docx(OUT_PATH, sections)
    print(str(OUT_PATH))


def _load_summary() -> dict:
    if SUMMARY_PATH.exists():
        return json.loads(SUMMARY_PATH.read_text(encoding="utf-8"))
    return {
        "limit": 5,
        "top_k": 5,
        "recall_at_k": None,
        "mrr": None,
        "answer_token_f1": None,
    }


def _build_sections(summary: dict) -> list[dict]:
    metric_text = (
        f"ActMemEval 前 {summary.get('limit', 5)} 条，Top-{summary.get('top_k', 5)}；"
        f"Recall@K={_fmt(summary.get('recall_at_k'))}，"
        f"MRR={_fmt(summary.get('mrr'))}，"
        f"Answer Token F1={_fmt(summary.get('answer_token_f1'))}。"
    )
    return [
        {
            "type": "title",
            "title": "Cross-Agent 项目架构设计报告",
            "subtitle": "跨 Session 长期记忆会话 Agent 本地 MVP",
        },
        {
            "type": "meta",
            "rows": [
                ("生成日期", datetime.now().strftime("%Y-%m-%d")),
                ("项目路径", str(REPO_ROOT)),
                ("验收数据", "/Users/mac/workspace/ActMem/dataset/ActMemEval.json"),
                ("技术主线", "类型化状态 + 时序事件 + 混合检索 + 安全治理"),
                ("在线模型配置", "yunai.chat OpenAI 兼容接口，deepseek-v4-pro:floor"),
            ],
        },
        {
            "type": "heading",
            "level": 1,
            "text": "1. 总体说明",
        },
        {
            "type": "paragraph",
            "text": (
                "本项目根据《跨 Session 长期记忆会话 Agent》技术报告完成本地代码设计。"
                "系统遵循“原始对话不可替代、记忆状态可演化、每次使用有依据”的原则，"
                "将写入、治理、存储、读取、回答校验拆分为独立模块。当前 MVP 不依赖外部 API，"
                "默认使用 SQLite 和确定性混合检索完成验收。"
            ),
        },
        {
            "type": "heading",
            "level": 1,
            "text": "2. 架构分层",
        },
        {
            "type": "table",
            "headers": ["层级", "模块", "职责", "替换点"],
            "rows": [
                ("配置层", "config.py / configs/default.json", "集中管理路径、阈值、权重、策略开关", "环境变量覆盖、不同环境 JSON"),
                ("领域层", "models.py", "定义 Candidate、Operation、Record、Evidence 等核心对象", "保持稳定，供所有实现共享"),
                ("写入层", "writer", "从 Session 生成候选记忆，不直接写库", "可替换为 LLM 抽取器"),
                ("治理层", "governor / policies", "隐私准入、字面性、置信度、幂等和操作决策", "可替换为模型治理或规则引擎"),
                ("存储层", "store", "事件日志与当前状态投影，默认 SQLite", "PostgreSQL、pgvector、FAISS"),
                ("读取层", "reader", "查询规划、同义扩展、BM25 近似、伪相关反馈、重排", "向量库、Elasticsearch、图检索"),
                ("生成层", "answer / guard", "基于证据生成回答并做证据约束、拒答", "接入在线 LLM 和后验核验"),
                ("模型层", "llm", "OpenAI 兼容 Chat Completions 客户端", "通过 base_url、model、api_key_env 配置切换"),
                ("验收层", "eval", "读取 ActMemEval 前 5 条，输出指标和 trace", "扩展到全量集和消融实验"),
            ],
        },
        {
            "type": "heading",
            "level": 1,
            "text": "3. 数据生命周期",
        },
        {
            "type": "list",
            "items": [
                "Session 输入后先进入 Memory Writer，抽取候选证据，不直接持久化。",
                "Memory Governor 结合 WritePolicy 和 PrivacyPolicy 判断 create、reinforce 或 reject。",
                "Memory Store 在同一事务语义下写入 memory_events，并维护 memory_state 当前投影。",
                "Memory Reader 先按 tenant_id、user_id、status 做硬过滤，再执行混合召回和重排。",
                "EvidenceBundle 保留 memory_id、source_session_id、score、snippet 和完整 trace。",
                "Response Guard 要求个性化回答必须有证据；无证据时返回拒答消息。",
            ],
        },
        {
            "type": "heading",
            "level": 1,
            "text": "4. 解耦与配置策略",
        },
        {
            "type": "paragraph",
            "text": (
                "代码按工具类和业务类分离：utils 仅包含纯文本与 JSON 工具；业务模块通过协议和领域模型交互。"
                "所有可调项都进入 configs/default.json，包括数据库路径、Top-K、召回权重、同义词、敏感词、拒答消息和评测数量。"
                "在线模型使用 configs/yunai.json 开启，API key 只从环境变量 YUNAI_API_KEY 读取，不写入源码或 JSON。"
                "这保证了后续接入 API key、模型参数、向量库或服务框架时，不需要改动核心业务逻辑。"
            ),
        },
        {
            "type": "heading",
            "level": 1,
            "text": "5. 验收结果",
        },
        {"type": "paragraph", "text": metric_text},
        {
            "type": "table",
            "headers": ["指标", "含义", "当前结果"],
            "rows": [
                ("Recall@K", "Top-K 证据中覆盖金标 answer_session_ids 的比例", _fmt(summary.get("recall_at_k"))),
                ("MRR", "第一个金标证据的倒数排名均值", _fmt(summary.get("mrr"))),
                ("Answer Token F1", "本地抽取式回答与标准答案的词重叠，仅作烟测参考", _fmt(summary.get("answer_token_f1"))),
            ],
        },
        {
            "type": "heading",
            "level": 1,
            "text": "6. 后续扩展建议",
        },
        {
            "type": "list",
            "items": [
                "新增 LLMMemoryExtractor，实现报告中的原子化 fact、preference、task、relation 抽取。",
                "将 SQLiteMemoryStore 替换为 PostgreSQLMemoryStore，并为向量字段接入 pgvector。",
                "引入 FastAPI 暴露 /v1/memories/search、/v1/users/{user_id}/memories 和 trace 查询接口。",
                "扩展 eval 为全量 ActMemEval、LongMemEval 和自建反讽/隐私/改口测试集。",
                "把 Response Guard 升级为逐条个性化断言核验，记录实际使用的 memory_id。",
            ],
        },
    ]


def _fmt(value) -> str:
    if value is None:
        return "未运行"
    return f"{float(value):.4f}"


def _write_docx(path: Path, sections: list[dict]) -> None:
    document_xml = _document_xml(sections)
    with zipfile.ZipFile(path, "w", compression=zipfile.ZIP_DEFLATED) as z:
        z.writestr("[Content_Types].xml", _content_types())
        z.writestr("_rels/.rels", _rels())
        z.writestr("word/_rels/document.xml.rels", _document_rels())
        z.writestr("word/styles.xml", _styles_xml())
        z.writestr("word/document.xml", document_xml)


def _document_xml(sections: list[dict]) -> str:
    body: list[str] = []
    for section in sections:
        kind = section["type"]
        if kind == "title":
            body.append(_paragraph(section["title"], style="Title"))
            body.append(_paragraph(section["subtitle"], style="Subtitle"))
        elif kind == "meta":
            body.append(_table(["字段", "内容"], section["rows"]))
        elif kind == "heading":
            style = "Heading1" if section["level"] == 1 else "Heading2"
            body.append(_paragraph(section["text"], style=style))
        elif kind == "paragraph":
            body.append(_paragraph(section["text"], style="Normal"))
        elif kind == "list":
            for item in section["items"]:
                body.append(_paragraph(item, style="ListParagraph", bullet=True))
        elif kind == "table":
            body.append(_table(section["headers"], section["rows"]))
    body.append(_sect_pr())
    return (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">'
        '<w:background w:color="FFFFFF"/>'
        f"<w:body>{''.join(body)}</w:body></w:document>"
    )


def _paragraph(text: str, style: str = "Normal", bullet: bool = False) -> str:
    ppr = f'<w:pStyle w:val="{style}"/>'
    if bullet:
        ppr += '<w:ind w:left="420" w:hanging="220"/>'
    run = f"<w:r><w:t>{escape(text)}</w:t></w:r>"
    if bullet:
        run = '<w:r><w:t>• </w:t></w:r>' + run
    return f"<w:p><w:pPr>{ppr}</w:pPr>{run}</w:p>"


def _table(headers: Sequence[str], rows: Sequence[Sequence[str]]) -> str:
    col_count = len(headers)
    grid = "".join('<w:gridCol w:w="2340"/>' for _ in range(col_count))
    out = [
        '<w:tbl><w:tblPr><w:tblW w:w="9360" w:type="dxa"/>'
        '<w:tblBorders><w:top w:val="single" w:sz="4" w:color="DADCE0"/>'
        '<w:left w:val="single" w:sz="4" w:color="DADCE0"/>'
        '<w:bottom w:val="single" w:sz="4" w:color="DADCE0"/>'
        '<w:right w:val="single" w:sz="4" w:color="DADCE0"/>'
        '<w:insideH w:val="single" w:sz="4" w:color="DADCE0"/>'
        '<w:insideV w:val="single" w:sz="4" w:color="DADCE0"/>'
        '</w:tblBorders></w:tblPr>'
        f"<w:tblGrid>{grid}</w:tblGrid>"
    ]
    out.append(_row(headers, header=True))
    for row in rows:
        out.append(_row(row, header=False))
    out.append("</w:tbl>")
    return "".join(out)


def _row(values: Sequence[str], header: bool) -> str:
    cells = []
    for value in values:
        fill = '<w:shd w:fill="F2F4F7"/>' if header else '<w:shd w:fill="FFFFFF"/>'
        bold_start = "<w:b/>" if header else ""
        cells.append(
            '<w:tc><w:tcPr><w:tcW w:w="2340" w:type="dxa"/>'
            f'{fill}<w:tcMar><w:top w:w="80" w:type="dxa"/>'
            '<w:bottom w:w="80" w:type="dxa"/><w:start w:w="120" w:type="dxa"/>'
            '<w:end w:w="120" w:type="dxa"/></w:tcMar></w:tcPr>'
            f'<w:p><w:r><w:rPr>{bold_start}</w:rPr><w:t>{escape(str(value))}</w:t></w:r></w:p></w:tc>'
        )
    return f"<w:tr>{''.join(cells)}</w:tr>"


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
        '<w:rPr><w:rFonts w:ascii="Calibri" w:hAnsi="Calibri"/><w:sz w:val="22"/></w:rPr>'
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
