"""Trace-safe task goal contract for plan/finish validation."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
import hashlib
import re
from typing import Any

from phone_agent.graph.context import redact_context_text, sanitize_context_payload


MAX_ENTITY_COUNT = 4
ORDINAL_PATTERNS: tuple[tuple[str, int], ...] = (
    ("第一个", 1),
    ("第1个", 1),
    ("第一", 1),
    ("第二个", 2),
    ("第2个", 2),
    ("第二", 2),
    ("第三个", 3),
    ("第3个", 3),
    ("第三", 3),
    ("第四个", 4),
    ("第4个", 4),
    ("第四", 4),
    ("第五个", 5),
    ("第5个", 5),
    ("第五", 5),
)
APP_HINTS = {
    "bilibili": ("b站", "哔哩", "bilibili"),
    "wechat": ("微信", "wechat"),
    "douyin": ("抖音", "douyin", "tiktok"),
    "xiaohongshu": ("小红书", "rednote"),
    "settings": ("设置", "settings"),
}


@dataclass(frozen=True)
class TaskEntity:
    slot: str
    alias: str
    length: int
    sha256: str

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class TaskGoalContract:
    """Durable trace-safe task goal summary independent from message history."""

    task_hash: str
    task_length: int
    redacted_task_summary: str
    target_app_hint: str | None = None
    goal_type: str = "generic_task"
    ordinal: int | None = None
    terminal_evidence: list[str] = field(default_factory=list)
    entities: list[TaskEntity] = field(default_factory=list)

    def to_trace_payload(self) -> dict[str, Any]:
        return {
            "task_hash": self.task_hash,
            "task_length": self.task_length,
            "redacted_task_summary": _safe_task_summary(self.redacted_task_summary),
            "target_app_hint": self.target_app_hint,
            "goal_type": self.goal_type,
            "ordinal": self.ordinal,
            "terminal_evidence": list(self.terminal_evidence),
            "entities": [entity.to_dict() for entity in self.entities],
        }

    def to_prompt_block(self, *, lang: str = "cn") -> str:
        evidence = ", ".join(self.terminal_evidence) if self.terminal_evidence else "generic_task_done"
        entity_aliases = ", ".join(entity.alias for entity in self.entities) or "none"
        if lang == "en":
            lines = [
                "** Task Goal Contract (belief only; not execution authorization) **",
                f"goal_type={self.goal_type} app={self.target_app_hint or 'unknown'} ordinal={self.ordinal or 'none'}",
                f"terminal_evidence={evidence}",
                f"task_hash={self.task_hash} task_length={self.task_length} entities={entity_aliases}",
                f"redacted_task_summary={_safe_task_summary(self.redacted_task_summary)}",
                "Finish is only valid when final screen evidence satisfies this contract; otherwise continue/replan.",
            ]
        else:
            lines = [
                "** 任务目标契约（仅为目标信念，不是执行授权） **",
                f"goal_type={self.goal_type} app={self.target_app_hint or 'unknown'} ordinal={self.ordinal or 'none'}",
                f"terminal_evidence={evidence}",
                f"task_hash={self.task_hash} task_length={self.task_length} entities={entity_aliases}",
                f"redacted_task_summary={_safe_task_summary(self.redacted_task_summary)}",
                "只有最终屏幕证据满足该契约时才允许 finish；否则必须继续或重新规划。",
            ]
        return "\n".join(lines)


def build_task_goal_contract(task: str) -> TaskGoalContract:
    """Derive a compact, privacy-aware goal contract from the original task."""

    text = str(task or "").strip()
    task_hash = hashlib.sha256(text.encode("utf-8")).hexdigest()[:16]
    app_hint = _detect_app_hint(text)
    ordinal = _detect_ordinal(text)
    goal_type = _detect_goal_type(text, ordinal=ordinal)
    terminal_evidence = _terminal_evidence(goal_type, ordinal=ordinal)
    entities = _extract_entities(text)
    redacted_summary = redact_context_text(text)[:160] if text else ""
    return TaskGoalContract(
        task_hash=task_hash,
        task_length=len(text),
        redacted_task_summary=redacted_summary,
        target_app_hint=app_hint,
        goal_type=goal_type,
        ordinal=ordinal,
        terminal_evidence=terminal_evidence,
        entities=entities,
    )


def ensure_task_goal_contract(state: dict[str, Any]) -> TaskGoalContract:
    value = state.get("task_goal_contract")
    if isinstance(value, TaskGoalContract):
        return value
    if isinstance(value, dict):
        try:
            entities = [
                TaskEntity(**item)
                for item in value.get("entities", [])
                if isinstance(item, dict)
            ]
            return TaskGoalContract(
                task_hash=str(value.get("task_hash") or ""),
                task_length=int(value.get("task_length") or 0),
                redacted_task_summary=str(value.get("redacted_task_summary") or ""),
                target_app_hint=value.get("target_app_hint") if isinstance(value.get("target_app_hint"), str) else None,
                goal_type=str(value.get("goal_type") or "generic_task"),
                ordinal=value.get("ordinal") if isinstance(value.get("ordinal"), int) else None,
                terminal_evidence=[str(item) for item in value.get("terminal_evidence", []) if isinstance(item, str)],
                entities=entities,
            )
        except (TypeError, ValueError):
            pass
    return build_task_goal_contract(str(state.get("task") or ""))


def task_goal_prompt_block(state: dict[str, Any], *, lang: str = "cn") -> str:
    return ensure_task_goal_contract(state).to_prompt_block(lang=lang)


def task_goal_trace_payload(state: dict[str, Any]) -> dict[str, Any]:
    return ensure_task_goal_contract(state).to_trace_payload()


def finish_claim_summary(value: str | None) -> dict[str, Any] | None:
    return _safe_finish_claim(value)


def validate_finish_claim(
    *,
    contract: TaskGoalContract,
    verifier_status: str | None,
    verifier_evidence: dict[str, Any] | None,
    after_observation: dict[str, Any] | None,
    finish_claim: str | None = None,
) -> dict[str, Any]:
    """Fail closed unless final evidence satisfies the task goal contract."""

    text_blob = _collect_text(after_observation).lower()
    matched: list[str] = []
    missing: list[str] = []
    detail_terms = ("播放", "播放器", "全屏", "暂停", "弹幕", "详情", "评论", "player", "pause", "fullscreen", "detail", "comment")
    search_terms = ("搜索结果", "综合", "筛选", "search results")
    current_app = _current_app(after_observation).lower()
    if contract.goal_type in {"open_or_watch_ranked_content", "open_or_watch_content"}:
        if any(term in text_blob for term in detail_terms):
            matched.append("detail_or_player_visible")
        else:
            missing.append("detail_or_player_visible")
        if contract.ordinal:
            selected_signals = {}
            if isinstance(verifier_evidence, dict):
                maybe = verifier_evidence.get("selected_object_signals")
                selected_signals = maybe if isinstance(maybe, dict) else {}
            if (
                selected_signals.get("selected_object_match")
                and selected_signals.get("selected_object_expected_rank") == contract.ordinal
            ):
                matched.append(f"selected_rank={contract.ordinal}")
            else:
                missing.append(f"selected_rank={contract.ordinal}")
        if any(term in text_blob for term in search_terms) and not any(term in text_blob for term in detail_terms):
            missing.append("still_on_search_or_feed_surface")
    elif contract.goal_type == "search_result_visible":
        if any(term in text_blob for term in search_terms) or ("搜索" in text_blob and "结果" in text_blob):
            matched.append("search_results_visible")
        else:
            missing.append("search_results_visible")
    elif contract.goal_type == "page_or_app_opened":
        app_hint = (contract.target_app_hint or "").lower()
        if app_hint and (app_hint in current_app or app_hint in text_blob):
            matched.append("target_app_visible")
        elif verifier_status == "success":
            matched.append("verifier_success")
        else:
            missing.append("target_page_or_app_visible")
    elif verifier_status == "success":
        matched.append("verifier_success")
    else:
        missing.append("generic_final_evidence_unverified")

    status = "success" if matched and not missing else "failure"
    if not matched and missing:
        status = "unknown"
    return {
        "status": status,
        "matched_terminal_evidence": matched,
        "missing_terminal_evidence": missing,
        "goal_type": contract.goal_type,
        "ordinal": contract.ordinal,
        "task_hash": contract.task_hash,
        "finish_claim_summary": _safe_finish_claim(finish_claim),
    }


def _safe_finish_claim(value: str | None) -> dict[str, Any] | None:
    if not isinstance(value, str) or not value:
        return None
    return {"length": len(value), "sha256": hashlib.sha256(value.encode("utf-8")).hexdigest()[:12]}


def _safe_task_summary(value: str) -> dict[str, Any]:
    return {
        "redacted": True,
        "length": len(value),
        "sha256": hashlib.sha256(value.encode("utf-8")).hexdigest()[:12],
    }


def _detect_app_hint(text: str) -> str | None:
    lowered = text.lower()
    for app, terms in APP_HINTS.items():
        if any(term.lower() in lowered for term in terms):
            return app
    return None


def _detect_ordinal(text: str) -> int | None:
    for pattern, value in ORDINAL_PATTERNS:
        if pattern in text:
            return value
    match = re.search(r"第\s*([1-9]\d*)\s*(?:个|条|项|部|集)?", text)
    if match:
        return int(match.group(1))
    return None


def _detect_goal_type(text: str, *, ordinal: int | None) -> str:
    lowered = text.lower()
    if any(term in lowered for term in ("视频", "播放", "看", "watch", "video", "play")):
        return "open_or_watch_ranked_content" if ordinal else "open_or_watch_content"
    if any(term in lowered for term in ("搜索", "查找", "search")):
        return "search_result_visible"
    if any(term in lowered for term in ("打开", "进入", "open")):
        return "page_or_app_opened"
    return "generic_task"


def _terminal_evidence(goal_type: str, *, ordinal: int | None) -> list[str]:
    if goal_type == "open_or_watch_ranked_content":
        evidence = ["detail_or_player_visible", "content_playing_or_detail_visible"]
        if ordinal:
            evidence.insert(1, f"selected_rank={ordinal}")
        return evidence
    if goal_type == "open_or_watch_content":
        return ["detail_or_player_visible", "content_playing_or_detail_visible"]
    if goal_type == "search_result_visible":
        return ["search_results_visible"]
    if goal_type == "page_or_app_opened":
        return ["target_page_or_app_visible"]
    return ["generic_task_done"]


def _extract_entities(text: str) -> list[TaskEntity]:
    normalized_text = _remove_known_goal_terms(text)
    candidates = []
    for chunk in re.split(r"[\s,，。.!！?？/\\]+", normalized_text):
        cleaned = chunk.strip("'\"“”‘’《》<>（）()[]【】")
        if len(cleaned) < 2:
            continue
        if re.fullmatch(r"第?\d+个?", cleaned):
            continue
        candidates.append(cleaned)
    entities: list[TaskEntity] = []
    for index, item in enumerate(candidates[:MAX_ENTITY_COUNT], start=1):
        entities.append(
            TaskEntity(
                slot=f"entity_{index}",
                alias=f"<matches_task_entity:{index}>",
                length=len(item),
                sha256=hashlib.sha256(item.encode("utf-8")).hexdigest()[:12],
            )
        )
    return entities


def _remove_known_goal_terms(text: str) -> str:
    cleaned = str(text or "")
    for terms in APP_HINTS.values():
        for term in terms:
            cleaned = re.sub(re.escape(term), " ", cleaned, flags=re.IGNORECASE)
    for pattern, _value in ORDINAL_PATTERNS:
        cleaned = cleaned.replace(pattern, " ")
    for term in (
        "去",
        "看",
        "打开",
        "搜索",
        "查找",
        "播放",
        "进入",
        "视频",
        "第一个",
        "第二个",
        "第三个",
        "第四个",
        "第五个",
        "watch",
        "video",
        "play",
        "open",
        "search",
    ):
        cleaned = re.sub(re.escape(term), " ", cleaned, flags=re.IGNORECASE)
    return cleaned


def _collect_text(value: Any) -> str:
    chunks: list[str] = []

    def visit(item: Any, key: str | None = None) -> None:
        normalized = (key or "").lower()
        if isinstance(item, str):
            if normalized in {"text", "text_summary", "label", "content_desc", "visible_text", "observed_text", "value", "role"}:
                chunks.append(item)
        elif isinstance(item, dict):
            for child_key, child in item.items():
                visit(child, str(child_key))
        elif isinstance(item, list):
            for child in item:
                visit(child, key)

    visit(value)
    return "\n".join(chunks)


def _current_app(value: Any) -> str:
    if not isinstance(value, dict):
        return ""
    snapshot = value.get("snapshot")
    if isinstance(snapshot, dict) and isinstance(snapshot.get("current_app"), str):
        return snapshot["current_app"]
    if isinstance(value.get("current_app"), str):
        return value["current_app"]
    return ""
