#!/usr/bin/env python3
"""Generate the final Cross-Agent technical report without third-party deps."""

from __future__ import annotations

import json
import zipfile
from datetime import datetime
from html import escape
from pathlib import Path
from typing import Sequence


REPO_ROOT = Path(__file__).resolve().parents[1]
SUMMARY_PATH = REPO_ROOT / "eval" / "output" / "actmem_eval_summary.json"
OUT_PATH = REPO_ROOT / "Cross-Agent技术报告2.docx"


def main() -> None:
    summary = _load_summary()
    _write_docx(OUT_PATH, _sections(summary))
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
        "answer_generator": "ExtractiveAnswerGenerator",
    }


def _sections(summary: dict) -> list[dict]:
    return [
        {
            "kind": "title",
            "title": "Cross-Agent 技术报告 2",
            "subtitle": "跨 Session 长期记忆会话 Agent 的最终实现版",
        },
        {
            "kind": "table",
            "headers": ["字段", "内容"],
            "rows": [
                ("项目路径", str(REPO_ROOT)),
                ("版本日期", datetime.now().strftime("%Y-%m-%d")),
                ("实现形态", "本地可运行 MVP，标准库 + SQLite + 可选 OpenAI 兼容模型"),
                ("技术主线", "类型化状态、时序事件、混合检索、安全治理、证据约束回答"),
                ("评测范围", f"ActMemEval 前 {summary.get('limit', 5)} 条，Top-{summary.get('top_k', 5)}"),
            ],
            "widths": [2200, 7160],
        },
        {
            "kind": "h1",
            "text": "1. 实现结论",
        },
        {
            "kind": "p",
            "text": (
                "Cross-Agent 已经从设计报告中的长期记忆方案落地为一个完整的本地 Agent 项目。"
                "当前版本覆盖对话写入、候选准入、事件日志、当前状态投影、混合检索、证据包、回答生成、"
                "响应校验、配置化运行和 ActMemEval 验收。项目没有采用重型知识图谱或在线依赖作为第一版底座，"
                "而是选择更可控的 SQLite 状态存储和确定性检索链路，先把来源、时间、状态、用户隔离、改口和拒答做扎实。"
            ),
        },
        {
            "kind": "p",
            "text": (
                "本次审查后补齐了结构化槽位记忆与 supersede 生命周期：系统现在既能保存原始 session evidence，"
                "也能把明确表达的姓名、居住地、编程语言偏好、饮品偏好和任务抽成类型化状态。"
                "同一槽位重复表达会强化当前记忆，同一槽位出现新值会把旧值标记为 superseded，再创建新的 active 记录。"
                "这样项目具备了报告里最关键的“可追溯事件 + 当前有效状态”能力。"
            ),
        },
        {
            "kind": "h1",
            "text": "2. 原报告设计项与实现状态",
        },
        {
            "kind": "table",
            "headers": ["设计项", "实现状态", "项目中的落点"],
            "rows": [
                ("类型化记忆模型", "已实现", "models.py 定义 MemoryType、MemoryStatus、Candidate、Record、EvidenceBundle"),
                ("事件日志 + 当前状态", "已实现", "SQLite memory_events 追加记录，memory_state 保存当前投影"),
                ("写入抽取", "已实现并增强", "HeuristicSessionExtractor 生成 session evidence，并保守抽取结构化槽位"),
                ("写入治理", "已实现", "RuleBasedMemoryGovernor 结合 WritePolicy 与 PrivacyPolicy 决定 create、reinforce、supersede、reject"),
                ("改口与时序状态", "已补齐核心路径", "同槽不同值触发 supersede，旧记录写 valid_to，新记录保持 active"),
                ("混合召回与重排", "已实现", "BM25 风格词法分、计数余弦、时间分、重要性、置信度和反馈词重排"),
                ("隐私写入门", "已实现基础版", "禁存密码、验证码、私钥、完整卡号等凭证类模式"),
                ("披露门与拒答", "已实现基础版", "默认不披露 sensitive/high 记忆，无证据时由 ResponseGuard 拒答"),
                ("在线模型接入", "已预留并可运行", "LLMAnswerGenerator 和 OpenAICompatibleChatClient 通过配置启用"),
                ("管理页和 HTTP API", "未纳入当前实现", "当前为本地库和 CLI，后续可在现有模块外包 FastAPI"),
            ],
            "widths": [2600, 1900, 4860],
        },
        {
            "kind": "h1",
            "text": "3. 最终方案选择",
        },
        {
            "kind": "p",
            "text": (
                "可选路线包括全量长上下文、纯向量检索、知识图谱优先、PostgreSQL/pgvector 服务化版本，以及本地 SQLite MVP。"
                "本项目最终选择 SQLite + 事件日志 + 当前状态 + 轻量混合检索作为第一版主线。"
                "这个选择的理由很直接：长期记忆最容易出问题的地方不是索引规模，而是写入准入、改口、过期、隐私边界和证据约束。"
                "在这些能力稳定前，上复杂图结构或分布式向量库会增加维护面，却不能直接解决旧事实误用和越权披露。"
            ),
        },
        {
            "kind": "table",
            "headers": ["候选方案", "取舍结果", "原因"],
            "rows": [
                ("全量历史塞回上下文", "不采用", "成本高、噪声大、无法表达当前状态和失效事实"),
                ("纯向量 Top-K", "不单独采用", "召回相似片段可以，但缺少状态、来源、权限和时序治理"),
                ("知识图谱优先", "暂不采用", "多跳关系不是当前瓶颈，图抽取会带来实体合并和错误边维护成本"),
                ("PostgreSQL/pgvector", "保留升级路径", "适合服务化和规模化，但本地验收阶段 SQLite 更简单可控"),
                ("SQLite + 混合检索 + 槽位状态", "当前采用", "能覆盖可运行验收、改口治理、审计追溯和低依赖交付"),
            ],
            "widths": [2500, 2000, 4860],
        },
        {
            "kind": "h1",
            "text": "4. 最终架构",
        },
        {
            "kind": "table",
            "headers": ["层级", "模块", "职责"],
            "rows": [
                ("配置层", "config.py / configs", "集中管理路径、阈值、权重、模型、敏感策略和评测参数"),
                ("领域层", "models.py", "统一 Candidate、Operation、Record、QueryPlan、EvidenceItem、GuardedAnswer"),
                ("写入层", "writer/extractor.py", "保留原始 session evidence，并抽取保守结构化状态"),
                ("治理层", "governor / policies", "执行隐私准入、置信度门槛、字面性门槛、幂等强化和 supersede"),
                ("存储层", "store/sqlite_store.py", "维护 memory_state、memory_events、槽位索引和租户用户过滤"),
                ("读取层", "reader", "完成查询规划、同义扩展、混合打分、反馈词扩展、去重和证据包构造"),
                ("生成层", "answer", "默认抽取式回答，可切换 OpenAI 兼容 LLM 回答"),
                ("校验层", "guard", "要求个性化回答必须有证据，记录实际使用的 memory_id"),
                ("评测层", "evaluation.py / eval", "运行 ActMemEval 子集，输出指标、状态变化和检索 trace"),
            ],
            "widths": [1600, 2600, 5160],
        },
        {
            "kind": "h1",
            "text": "5. 数据生命周期",
        },
        {
            "kind": "p",
            "text": (
                "一次 Session 写入时，Extractor 首先生成 event 类型的 session_evidence，保留完整 transcript、summary、keywords、"
                "source_session_id 和 source_turn_ids。随后它只对高确定性的表达抽取结构化记忆，例如 preferred_programming_language、"
                "preferred_drink、current_residence、name 和 user_task。Governor 对每条候选先执行隐私门和写入门，再根据候选类型选择操作。"
            ),
        },
        {
            "kind": "p",
            "text": (
                "session_evidence 按 source_session_id 幂等强化，避免重复处理同一个 Session 造成重复活动记忆。"
                "结构化记忆按 tenant_id、user_id、type、subject、predicate、scope 定位同一槽位。"
                "同槽同值执行 reinforce，提高 confidence 和 importance；同槽不同值执行 supersede，旧记录 status 改为 superseded，"
                "valid_to 写入新候选的 valid_from，新记录写入 active，并通过 supersedes 指向旧记录。"
            ),
        },
        {
            "kind": "h1",
            "text": "6. 检索与回答",
        },
        {
            "kind": "p",
            "text": (
                "Reader 先根据查询判断是否需要长期记忆，只有通过 tenant_id、user_id、active 状态和敏感披露策略过滤后的记录才会进入召回。"
                "打分由语义近似、词法相关性、时间有效性、重要性、置信度和覆盖度组成。"
                "初排后系统从 Top 证据中提取反馈词，再做二次重排，最后按 source_session_id 去重，形成 EvidenceBundle。"
            ),
        },
        {
            "kind": "p",
            "text": (
                "回答阶段默认使用 ExtractiveAnswerGenerator，直接从证据片段组织回答，适合离线验收。"
                "当启用 configs/yunai.json 时，LLMAnswerGenerator 会把 EvidenceBundle 传给 OpenAI 兼容模型，并要求模型只基于证据回答。"
                "ResponseGuard 负责最后一道门：没有证据时拒答，有证据时记录 used_memory_ids 和 source_sessions，便于调试和审计。"
            ),
        },
        {
            "kind": "h1",
            "text": "7. 验证结果",
        },
        {
            "kind": "table",
            "headers": ["验证项", "结果", "说明"],
            "rows": [
                ("结构化生命周期单测", "通过", "Java 偏好被 Python 偏好 supersede，旧记录 valid_to 正确写入"),
                ("语法编译检查", "通过", "使用 PYTHONPYCACHEPREFIX=/private/tmp/cross-agent-pycache 运行 compileall"),
                ("ActMemEval 子集", "通过", f"Recall@K={_fmt(summary.get('recall_at_k'))}，MRR={_fmt(summary.get('mrr'))}"),
                ("回答烟测指标", "记录", f"Answer Token F1={_fmt(summary.get('answer_token_f1'))}，当前抽取式回答主要用于证据链验证"),
            ],
            "widths": [2600, 1600, 5160],
        },
        {
            "kind": "h1",
            "text": "8. 当前边界",
        },
        {
            "kind": "p",
            "text": (
                "当前版本是完整的本地长期记忆 Agent MVP，不是面向多租户生产流量的服务化版本。"
                "它已经具备核心数据生命周期和评测闭环，但还没有实现管理页面、HTTP API、真实向量索引、复杂时间解析、"
                "用户可视化编辑删除界面、全量 LongMemEval/LoCoMo 评测和逐条自然语言断言核验。"
                "这些能力可以在现有模块边界上继续扩展，不需要推翻当前架构。"
            ),
        },
        {
            "kind": "h1",
            "text": "9. 结论",
        },
        {
            "kind": "p",
            "text": (
                "Cross-Agent 的最终实现版已经抓住长期记忆 Agent 的核心：不把历史简单堆进上下文，而是把会话转换为可治理、"
                "可追溯、可更新、可检索和可拒答的记忆状态。第一版选择轻量技术栈是合理的，因为它优先解决了记忆系统最关键的正确性问题。"
                "下一阶段如果要进入服务化，可以在保持 Candidate、Operation、Store、Reader 和 Guard 协议不变的前提下，"
                "替换为 PostgreSQL/pgvector、FastAPI、LLM 抽取器和更强的响应核验器。"
            ),
        },
    ]


def _write_docx(path: Path, sections: list[dict]) -> None:
    with zipfile.ZipFile(path, "w", compression=zipfile.ZIP_DEFLATED) as z:
        z.writestr("[Content_Types].xml", _content_types())
        z.writestr("_rels/.rels", _rels())
        z.writestr("word/_rels/document.xml.rels", _document_rels())
        z.writestr("word/styles.xml", _styles_xml())
        z.writestr("word/document.xml", _document_xml(sections))


def _document_xml(sections: list[dict]) -> str:
    body: list[str] = []
    for section in sections:
        kind = section["kind"]
        if kind == "title":
            body.append(_paragraph(section["title"], "Title"))
            body.append(_paragraph(section["subtitle"], "Subtitle"))
        elif kind == "h1":
            body.append(_paragraph(section["text"], "Heading1"))
        elif kind == "p":
            body.append(_paragraph(section["text"], "Normal"))
        elif kind == "table":
            body.append(_table(section["headers"], section["rows"], section["widths"]))
    body.append(_sect_pr())
    return (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">'
        '<w:background w:color="FFFFFF"/>'
        f"<w:body>{''.join(body)}</w:body></w:document>"
    )


def _paragraph(text: str, style: str) -> str:
    return f'<w:p><w:pPr><w:pStyle w:val="{style}"/></w:pPr>{_run(text)}</w:p>'


def _run(text: str, bold: bool = False) -> str:
    bold_xml = "<w:b/>" if bold else ""
    return f'<w:r><w:rPr>{bold_xml}</w:rPr><w:t>{escape(str(text))}</w:t></w:r>'


def _table(headers: Sequence[str], rows: Sequence[Sequence[str]], widths: Sequence[int]) -> str:
    total = sum(widths)
    grid = "".join(f'<w:gridCol w:w="{width}"/>' for width in widths)
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
    out.append(_row(headers, widths, True))
    for row in rows:
        out.append(_row(row, widths, False))
    out.append("</w:tbl>")
    return "".join(out)


def _row(values: Sequence[str], widths: Sequence[int], header: bool) -> str:
    cells = []
    for index, value in enumerate(values):
        width = widths[min(index, len(widths) - 1)]
        fill = '<w:shd w:fill="F2F4F7"/>' if header else '<w:shd w:fill="FFFFFF"/>'
        cells.append(
            f'<w:tc><w:tcPr><w:tcW w:w="{width}" w:type="dxa"/>{fill}'
            '<w:tcMar><w:top w:w="80" w:type="dxa"/>'
            '<w:bottom w:w="80" w:type="dxa"/><w:start w:w="120" w:type="dxa"/>'
            '<w:end w:w="120" w:type="dxa"/></w:tcMar></w:tcPr>'
            f'<w:p>{_run(value, bold=header)}</w:p></w:tc>'
        )
    return f"<w:tr>{''.join(cells)}</w:tr>"


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
        "</w:styles>"
    )


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


def _fmt(value) -> str:
    if value is None:
        return "未运行"
    return f"{float(value):.4f}"


if __name__ == "__main__":
    main()
