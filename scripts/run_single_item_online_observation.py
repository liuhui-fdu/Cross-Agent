#!/usr/bin/env python3
"""Run one online ActMemEval item and generate a full observation bundle."""

from __future__ import annotations

import json
import os
import sqlite3
import sys
import zipfile
import argparse
import time
from datetime import datetime
from html import escape
from pathlib import Path
from typing import Any, Sequence


REPO_ROOT = Path(__file__).resolve().parents[1]
SRC = REPO_ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from cross_agent.config import load_settings
from cross_agent.evaluation import run_actmem_eval
from cross_agent.llm.openai_compatible import ChatMessage, OpenAICompatibleChatClient
from cross_agent.utils.env import load_env_file
from cross_agent.utils.jsonio import read_json, write_json


def main() -> None:
    parser = argparse.ArgumentParser(description="Run or resume one-item online observation.")
    parser.add_argument("--run-dir", default=None, help="Existing eval/eval-* directory to resume.")
    args = parser.parse_args()
    run_dir = Path(args.run_dir) if args.run_dir else REPO_ROOT / "eval" / ("eval-" + datetime.now().strftime("%Y%m%d-%H%M%S"))
    if not run_dir.is_absolute():
        run_dir = REPO_ROOT / run_dir
    run_dir.mkdir(parents=True, exist_ok=bool(args.run_dir))
    load_env_file(REPO_ROOT / ".env")

    config_path = run_dir / "run_config.json"
    raw_item = read_json(REPO_ROOT / "configs" / "yunai.json")
    source_dataset = read_json(raw_item["evaluation"]["dataset_path"])
    original_item = source_dataset[0]
    replayed_item = _build_or_load_api_replayed_item(run_dir, original_item)
    replayed_dataset_path = run_dir / "actmem_first_item_api_replayed.json"
    write_json(replayed_dataset_path, [replayed_item])
    if not config_path.exists():
        config_path = _write_run_config(run_dir, replayed_dataset_path)
    settings = load_settings(config_path)
    result_path = run_dir / "actmem_eval_results.json"
    if result_path.exists():
        result = read_json(result_path)
    else:
        result = run_actmem_eval(settings, limit=1)
    item = replayed_item

    state_path = run_dir / "memory_state_dump.json"
    events_path = run_dir / "memory_events_dump.json"
    if state_path.exists() and events_path.exists():
        memory_state = read_json(state_path)
        memory_events = read_json(events_path)
    else:
        memory_state = _dump_table(Path(settings.storage.sqlite_path), "memory_state")
        memory_events = _dump_table(Path(settings.storage.sqlite_path), "memory_events")
        write_json(state_path, memory_state)
        write_json(events_path, memory_events)

    trajectory_md = _write_trajectory_markdown(run_dir, original_item, replayed_item)
    parameter_trace = _build_parameter_trace(settings, result, memory_state, memory_events)
    write_json(run_dir / "system_parameter_state_trace.json", parameter_trace)

    report_path = run_dir / "Cross-Agent第一条在线评测完整观测报告.docx"
    _write_docx_report(report_path, result, original_item, replayed_item, memory_state, memory_events, parameter_trace)
    print(str(run_dir))
    print(str(report_path))
    print(str(trajectory_md))


def _write_run_config(run_dir: Path, replayed_dataset_path: Path) -> Path:
    source = REPO_ROOT / "configs" / "yunai.json"
    raw = read_json(source)
    raw["evaluation"]["limit"] = 1
    raw["evaluation"]["output_dir"] = str(run_dir)
    raw["evaluation"]["dataset_path"] = str(replayed_dataset_path)
    raw["storage"]["sqlite_path"] = str(run_dir / "cross_agent_eval.sqlite3")
    raw.setdefault("llm", {})["enabled"] = True
    config_path = run_dir / "run_config.json"
    write_json(config_path, raw)
    masked = json.loads(json.dumps(raw))
    masked["llm"]["api_key_env"] = raw["llm"].get("api_key_env", "YUNAI_API_KEY")
    masked["llm"]["api_key"] = "***not-stored***"
    write_json(run_dir / "run_config.masked.json", masked)
    return config_path


def _build_or_load_api_replayed_item(run_dir: Path, original_item: dict[str, Any]) -> dict[str, Any]:
    replayed_path = run_dir / "api_replayed_item.json"
    if replayed_path.exists():
        existing = read_json(replayed_path)
        if len(existing.get("haystack_sessions", [])) == len(original_item.get("haystack_sessions", [])):
            return existing
    load_env_file(REPO_ROOT / ".env")
    base_config = load_settings(REPO_ROOT / "configs" / "yunai.json")
    api_key = os.environ.get(base_config.llm.api_key_env)
    if not api_key:
        raise RuntimeError(f"Missing {base_config.llm.api_key_env}")
    client = OpenAICompatibleChatClient(
        base_url=base_config.llm.base_url,
        api_key=api_key,
        model=base_config.llm.model,
        timeout_seconds=base_config.llm.timeout_seconds,
    )
    if replayed_path.exists():
        replayed = read_json(replayed_path)
        replayed_sessions = list(replayed.get("haystack_sessions", []))
    else:
        replayed = json.loads(json.dumps(original_item))
        replayed["haystack_sessions"] = []
        replayed_sessions = []
    if len(replayed_sessions) > len(original_item["haystack_sessions"]):
        replayed_sessions = replayed_sessions[: len(original_item["haystack_sessions"])]
    replay_log_path = run_dir / "api_replay_log.json"
    replay_log = read_json(replay_log_path) if replay_log_path.exists() else []
    start_index = len(replayed_sessions)
    for session_index, (session_id, turns) in enumerate(
        zip(original_item["haystack_session_ids"], original_item["haystack_sessions"]),
        start=1,
    ):
        if session_index <= start_index:
            continue
        replayed_turns, session_log = _replay_session_with_api(
            client=client,
            settings=base_config,
            session_id=session_id,
            turns=turns,
        )
        replayed_sessions.append(replayed_turns)
        replay_log.append(
            {
                "session_index": session_index,
                "session_id": session_id,
                "turn_count": len(turns),
                "assistant_turns_replayed": len(session_log),
                "assistant_replay_log": session_log,
            }
        )
        write_json(run_dir / "api_replay_progress.json", replay_log)
        replayed["haystack_sessions"] = replayed_sessions
        write_json(replayed_path, replayed)
        print(f"api replayed session {session_index}/{len(original_item['haystack_sessions'])} {session_id}", flush=True)
    replayed["haystack_sessions"] = replayed_sessions
    replayed["metadata"] = {
        "assistant_responses": "original assistant turns masked and regenerated via online API",
        "source_question_id": original_item.get("question_id"),
    }
    write_json(replayed_path, replayed)
    write_json(run_dir / "api_replay_log.json", replay_log)
    return replayed


def _replay_session_with_api(
    client: OpenAICompatibleChatClient,
    settings,
    session_id: str,
    turns: list[dict[str, str]],
) -> tuple[list[dict[str, str]], list[dict[str, Any]]]:
    messages = [
        ChatMessage(
            role="system",
            content=(
                "You are replaying one historical chat session for a memory-agent evaluation. "
                "Answer naturally but very briefly to the user's latest message, usually in "
                "one short sentence. Do not mention that this is an evaluation."
            ),
        )
    ]
    replayed_turns: list[dict[str, str]] = []
    replay_log: list[dict[str, Any]] = []
    for turn_index, turn in enumerate(turns, start=1):
        role = turn.get("role", "")
        content = turn.get("content", "")
        if role == "user":
            replayed_turns.append({"role": "user", "content": content})
            messages.append(ChatMessage(role="user", content=content))
            continue
        if role == "assistant":
            generated = _complete_with_retry(
                client=client,
                messages=messages,
                temperature=settings.llm.temperature,
                max_tokens=min(settings.llm.max_tokens, 512),
                label=f"{session_id}/turn_{turn_index:02d}",
            )
            replayed_turns.append({"role": "assistant", "content": generated})
            messages.append(ChatMessage(role="assistant", content=generated))
            replay_log.append(
                {
                    "turn_index": turn_index,
                    "original_assistant_masked": True,
                    "original_char_count": len(content),
                    "generated_char_count": len(generated),
                }
            )
            continue
        replayed_turns.append({"role": role, "content": content})
    return replayed_turns, replay_log


def _complete_with_retry(
    client: OpenAICompatibleChatClient,
    messages: list[ChatMessage],
    temperature: float,
    max_tokens: int,
    label: str,
) -> str:
    last_error: Exception | None = None
    for attempt in range(1, 5):
        try:
            request_max_tokens = min(max_tokens * attempt, 1200)
            return client.complete(messages=messages, temperature=temperature, max_tokens=request_max_tokens)
        except Exception as exc:
            last_error = exc
            wait = min(6 * attempt, 18)
            print(f"api replay retry {attempt}/4 for {label}: {exc}")
            time.sleep(wait)
    raise RuntimeError(f"api replay failed for {label}: {last_error}") from last_error


def _dump_table(sqlite_path: Path, table: str) -> list[dict[str, Any]]:
    conn = sqlite3.connect(str(sqlite_path))
    conn.row_factory = sqlite3.Row
    rows = conn.execute(f"SELECT * FROM {table}").fetchall()
    conn.close()
    result = []
    for row in rows:
        item = dict(row)
        for key, value in list(item.items()):
            if key.endswith("_json") and isinstance(value, str):
                try:
                    item[key] = json.loads(value)
                except json.JSONDecodeError:
                    pass
        result.append(item)
    return result


def _format_turns(turns: list[dict[str, str]]) -> str:
    lines = []
    for idx, turn in enumerate(turns, start=1):
        role = turn.get("role", "unknown")
        content = " ".join(turn.get("content", "").split())
        lines.append(f"turn_{idx:02d} [{role}]: {content}")
    return "\n".join(lines)


def _write_trajectory_markdown(run_dir: Path, original_item: dict[str, Any], replayed_item: dict[str, Any]) -> Path:
    path = run_dir / "conversation_trajectory_full_replayed.md"
    lines = [
        "# 第一条数据完整对话轨迹",
        "",
        "> 评测逻辑：用户输入来自 ActMemEval；原始 assistant 回复已掩码，不进入长期记忆；assistant 回复由在线 API 重放生成。",
        "",
        f"- question_id: `{original_item['question_id']}`",
        f"- question: {original_item['question']}",
        f"- answer_session_ids: {', '.join(original_item.get('answer_session_ids', []))}",
        "",
    ]
    gold = set(original_item.get("answer_session_ids", []))
    for idx, (session_id, date, original_turns, replayed_turns) in enumerate(
        zip(
            original_item["haystack_session_ids"],
            original_item["haystack_dates"],
            original_item["haystack_sessions"],
            replayed_item["haystack_sessions"],
        ),
        start=1,
    ):
        marker = "（金标证据）" if session_id in gold else ""
        lines.extend(
            [
                f"## {idx:02d}. {session_id} {marker}",
                "",
                f"- date: {date}",
                f"- turn_count: {len(replayed_turns)}",
                "",
                "### 评测使用轨迹",
                "",
                "```text",
                _format_turns(replayed_turns),
                "```",
                "",
                "### 原始 assistant 掩码检查",
                "",
                "```text",
                _format_masked_original_turns(original_turns),
                "```",
                "",
            ]
        )
    path.write_text("\n".join(lines), encoding="utf-8")
    return path


def _format_masked_original_turns(turns: list[dict[str, str]]) -> str:
    lines = []
    for idx, turn in enumerate(turns, start=1):
        role = turn.get("role", "unknown")
        if role == "assistant":
            content = "[MASKED: original assistant reply not used]"
        else:
            content = " ".join(turn.get("content", "").split())
        lines.append(f"turn_{idx:02d} [{role}]: {content}")
    return "\n".join(lines)


def _build_parameter_trace(settings, result: dict[str, Any], memory_state: list[dict[str, Any]], memory_events: list[dict[str, Any]]) -> dict[str, Any]:
    item = result["items"][0]
    return {
        "run_time": datetime.now().isoformat(timespec="seconds"),
        "config": {
            "app": settings.app.__dict__,
            "storage": settings.storage.__dict__,
            "writer": settings.writer.__dict__,
            "reader": settings.reader.__dict__,
            "guard": settings.guard.__dict__,
            "answer": settings.answer.__dict__,
            "llm": {
                "enabled": settings.llm.enabled,
                "base_url": settings.llm.base_url,
                "model": settings.llm.model,
                "api_key_env": settings.llm.api_key_env,
                "timeout_seconds": settings.llm.timeout_seconds,
                "temperature": settings.llm.temperature,
                "max_tokens": settings.llm.max_tokens,
            },
            "evaluation": settings.evaluation.__dict__,
        },
        "item_state_transition": item.get("state_transition", {}),
        "ingest_stats": item.get("ingest_stats", {}),
        "ingest_trace": item.get("ingest_trace", {}),
        "reader_trace": item.get("trace", {}),
        "evidence_items": item.get("evidence_items", []),
        "answer_trace": item.get("answer_trace", {}),
        "metrics": item.get("metrics", {}),
        "memory_state_count": len(memory_state),
        "memory_event_count": len(memory_events),
    }


def _write_docx_report(path: Path, result: dict[str, Any], original_item: dict[str, Any], replayed_item: dict[str, Any], memory_state: list[dict[str, Any]], memory_events: list[dict[str, Any]], parameter_trace: dict[str, Any]) -> None:
    document_xml = _document_xml(result, original_item, replayed_item, memory_state, memory_events, parameter_trace)
    with zipfile.ZipFile(path, "w", compression=zipfile.ZIP_DEFLATED) as z:
        z.writestr("[Content_Types].xml", _content_types())
        z.writestr("_rels/.rels", _rels())
        z.writestr("word/_rels/document.xml.rels", _document_rels())
        z.writestr("word/styles.xml", _styles_xml())
        z.writestr("word/document.xml", document_xml)


def _document_xml(result: dict[str, Any], original_item: dict[str, Any], replayed_item: dict[str, Any], memory_state: list[dict[str, Any]], memory_events: list[dict[str, Any]], parameter_trace: dict[str, Any]) -> str:
    eval_item = result["items"][0]
    summary = result["summary"]
    body: list[str] = []
    body.append(_paragraph("Cross-Agent 第一条在线评测完整观测报告", "Title"))
    body.append(_paragraph("API 重放轨迹、长期记忆写入变化与系统参数状态", "Subtitle"))
    body.append(_table(["字段", "值"], [
        ("question_id", original_item["question_id"]),
        ("问题", original_item["question"]),
        ("标准答案", original_item["answer"]),
        ("回答生成器", summary.get("answer_generator", "")),
        ("Recall@K / MRR / Token F1", f"{_fmt(summary.get('recall_at_k'))} / {_fmt(summary.get('mrr'))} / {_fmt(summary.get('answer_token_f1'))}"),
        ("候选 Session / Turn", f"{len(replayed_item['haystack_sessions'])} / {sum(len(s) for s in replayed_item['haystack_sessions'])}"),
        ("Assistant 来源", "原始 assistant 回复已掩码；评测轨迹中的 assistant 回复由在线 API 重放生成"),
        ("长期记忆状态 / 事件", f"{len(memory_state)} / {len(memory_events)}"),
    ], [2400, 6960]))

    body.append(_paragraph("1. 系统参数状态", "Heading1"))
    cfg = parameter_trace["config"]
    body.append(_table(["模块", "关键参数"], [
        ("Writer", json.dumps(cfg["writer"], ensure_ascii=False)),
        ("Reader", json.dumps({k: v for k, v in cfg["reader"].items() if k != "domain_synonyms"}, ensure_ascii=False)),
        ("Guard", json.dumps(cfg["guard"], ensure_ascii=False)),
        ("LLM", json.dumps(cfg["llm"], ensure_ascii=False)),
        ("Evaluation", json.dumps(cfg["evaluation"], ensure_ascii=False)),
    ], [2000, 7360]))

    body.append(_paragraph("2. 状态变化", "Heading1"))
    st = eval_item["state_transition"]
    body.append(_table(["阶段", "active_memories", "all_memories", "events"], [
        _count_row("写入前", st["before_ingest"]),
        _count_row("写入后", st["after_ingest"]),
        _count_row("检索后", st["after_search"]),
    ], [2200, 2380, 2380, 2400]))
    body.append(_table(["写入变量", "值"], [
        ("抽取候选数", str(eval_item["ingest_stats"]["extracted"])),
        ("新增记忆数", str(eval_item["ingest_stats"]["created"])),
        ("强化记忆数", str(eval_item["ingest_stats"]["reinforced"])),
        ("拒绝记忆数", str(eval_item["ingest_stats"]["rejected"])),
        ("金标 Session", ", ".join(original_item["answer_session_ids"])),
    ], [2400, 6960]))

    body.append(_paragraph("3. 检索与回答", "Heading1"))
    body.append(_table(["变量", "值"], [
        ("初始 Top Sessions", ", ".join(eval_item["trace"].get("initial_top_sessions", []))),
        ("反馈词", ", ".join(eval_item["trace"].get("feedback_terms", []))),
        ("最终 Top Sessions", ", ".join(eval_item["trace"].get("final_top_sessions", []))),
        ("命中 Session", ", ".join(eval_item.get("retrieved_session_ids", []))),
        ("预测回答", eval_item.get("prediction", "")),
    ], [2400, 6960]))
    ev_rows = [
        (
            str(ev["rank"]),
            ev["source_session_id"],
            _fmt(ev["score"]),
            _clip(ev["snippet"], 260),
        )
        for ev in eval_item.get("evidence_items", [])
    ]
    body.append(_paragraph("Top 证据包", "Heading2"))
    body.append(_table(["Rank", "Session", "Score", "Snippet"], ev_rows, [800, 2300, 1200, 5060]))

    body.append(_paragraph("4. 写入长期记忆的内容", "Heading1"))
    for idx, record in enumerate(memory_state, start=1):
        value = record.get("value_json", {})
        session_id = record.get("source_session_id", "")
        body.append(_paragraph(f"4.{idx} {session_id} / {record.get('memory_id', '')}", "Heading2"))
        body.append(_table(["字段", "值"], [
            ("type / status / sensitivity", f"{record.get('type')} / {record.get('status')} / {record.get('sensitivity')}"),
            ("confidence / importance", f"{record.get('confidence')} / {record.get('importance')}"),
            ("valid_from", str(record.get("valid_from"))),
            ("summary", _clip(value.get("summary", ""), 900)),
        ], [2400, 6960]))

    body.append(_paragraph("5. 完整对话轨迹", "Heading1"))
    body.append(_paragraph("说明：以下为评测实际使用的轨迹。用户输入来自评测集；原始 assistant 回复没有使用，已由在线 API 重放生成。", "Normal"))
    gold = set(original_item.get("answer_session_ids", []))
    for idx, (session_id, date, replayed_turns) in enumerate(
        zip(replayed_item["haystack_session_ids"], replayed_item["haystack_dates"], replayed_item["haystack_sessions"]),
        start=1,
    ):
        marker = "（金标证据）" if session_id in gold else ""
        body.append(_paragraph(f"{idx:02d}. {session_id} {marker}", "Heading2"))
        body.append(_paragraph(f"时间：{date}；轮次数：{len(replayed_turns)}", "Normal"))
        for paragraph in _split_for_docx(_format_turns(replayed_turns)):
            body.append(_paragraph(paragraph, "Normal"))
    body.append(_sect_pr())
    return (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">'
        '<w:background w:color="FFFFFF"/>'
        f"<w:body>{''.join(body)}</w:body></w:document>"
    )


def _count_row(label: str, counts: dict[str, Any]) -> tuple[str, str, str, str]:
    return (
        label,
        str(counts.get("active_memories", "")),
        str(counts.get("all_memories", "")),
        str(counts.get("events", "")),
    )


def _split_for_docx(text: str, max_len: int = 1500) -> list[str]:
    chunks = []
    for raw in text.splitlines():
        line = raw.strip()
        if not line:
            continue
        while len(line) > max_len:
            chunks.append(line[:max_len])
            line = line[max_len:]
        chunks.append(line)
    return chunks or [""]


def _paragraph(text: str, style: str = "Normal") -> str:
    return f'<w:p><w:pPr><w:pStyle w:val="{style}"/></w:pPr>{_run(text)}</w:p>'


def _run(text: str, bold: bool = False) -> str:
    bold_xml = "<w:b/>" if bold else ""
    return f'<w:r><w:rPr>{bold_xml}<w:color w:val="000000"/></w:rPr><w:t>{escape(str(text))}</w:t></w:r>'


def _table(headers: Sequence[str], rows: Sequence[Sequence[str]], widths: Sequence[int]) -> str:
    total = sum(widths)
    grid = "".join(f'<w:gridCol w:w="{w}"/>' for w in widths)
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
    for idx, value in enumerate(values):
        width = widths[min(idx, len(widths) - 1)]
        fill = '<w:shd w:fill="F2F4F7"/>' if header else '<w:shd w:fill="FFFFFF"/>'
        cells.append(
            f'<w:tc><w:tcPr><w:tcW w:w="{width}" w:type="dxa"/>{fill}'
            '<w:tcMar><w:top w:w="80" w:type="dxa"/><w:bottom w:w="80" w:type="dxa"/>'
            '<w:start w:w="120" w:type="dxa"/><w:end w:w="120" w:type="dxa"/></w:tcMar></w:tcPr>'
            f'<w:p>{_run(value, bold=header)}</w:p></w:tc>'
        )
    return f"<w:tr>{''.join(cells)}</w:tr>"


def _clip(text: str, limit: int) -> str:
    text = " ".join(str(text).split())
    return text if len(text) <= limit else text[: limit - 1].rstrip() + "..."


def _fmt(value: Any) -> str:
    try:
        return f"{float(value):.4f}"
    except (TypeError, ValueError):
        return str(value)


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
        '<w:rPr><w:rFonts w:ascii="Calibri" w:hAnsi="Calibri"/><w:sz w:val="20"/><w:color w:val="000000"/></w:rPr>'
        '<w:pPr><w:spacing w:after="100" w:line="264" w:lineRule="auto"/></w:pPr></w:style>'
        '<w:style w:type="paragraph" w:styleId="Title"><w:name w:val="Title"/><w:basedOn w:val="Normal"/>'
        '<w:rPr><w:b/><w:color w:val="0B2545"/><w:sz w:val="38"/></w:rPr><w:pPr><w:spacing w:after="160"/></w:pPr></w:style>'
        '<w:style w:type="paragraph" w:styleId="Subtitle"><w:name w:val="Subtitle"/><w:basedOn w:val="Normal"/>'
        '<w:rPr><w:color w:val="555555"/><w:sz w:val="24"/></w:rPr><w:pPr><w:spacing w:after="240"/></w:pPr></w:style>'
        '<w:style w:type="paragraph" w:styleId="Heading1"><w:name w:val="heading 1"/><w:basedOn w:val="Normal"/>'
        '<w:rPr><w:b/><w:color w:val="2E74B5"/><w:sz w:val="30"/></w:rPr><w:pPr><w:spacing w:before="300" w:after="140"/></w:pPr></w:style>'
        '<w:style w:type="paragraph" w:styleId="Heading2"><w:name w:val="heading 2"/><w:basedOn w:val="Normal"/>'
        '<w:rPr><w:b/><w:color w:val="1F4D78"/><w:sz w:val="24"/></w:rPr><w:pPr><w:spacing w:before="200" w:after="100"/></w:pPr></w:style>'
        "</w:styles>"
    )


if __name__ == "__main__":
    main()
