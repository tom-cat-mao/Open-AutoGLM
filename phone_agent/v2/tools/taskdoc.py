"""``update_task_doc`` tool: the model's sole writer into the TaskDoc board.

Per ``AGENTS.md`` §2.2. The tool full-replaces the
路线 items and 关键事实, appends amendments, validates the resulting document,
and only commits when valid (fail-closed: a validation failure returns an error
string and writes nothing). ``goal_base`` is never accepted here — only the
harness seeds it.
"""

from __future__ import annotations

from langchain_core.tools import StructuredTool

from phone_agent.v2.taskdoc import TaskDoc, TaskItem


def _coerce_item(raw: object) -> TaskItem:
    """Coerce one ``items`` entry (a dict) into a :class:`TaskItem`.

    Raises :class:`ValueError` (caught by the tool body, surfaced as error text)
    when the entry is not a mapping so a malformed payload never writes.
    """

    if not isinstance(raw, dict):
        raise ValueError(f"路线项必须是对象（含 id/content/status），收到 {type(raw).__name__}")
    reason = raw.get("reason")
    evidence_note = raw.get("evidence_note")
    return TaskItem(
        id=str(raw.get("id", "")).strip(),
        content=str(raw.get("content", "")).strip(),
        status=str(raw.get("status", "pending")).strip() or "pending",
        reason=(str(reason).strip() if reason not in (None, "") else None),
        evidence_note=(
            str(evidence_note).strip() if evidence_note not in (None, "") else None
        ),
    )


def make_update_task_doc_tool(session, lang: str) -> StructuredTool:
    """Build the ``update_task_doc`` tool bound to ``session`` (renders in ``lang``)."""

    def update_task_doc(
        items: list[dict] | None = None,
        add_amendments: list[str] | None = None,
        facts: list[str] | None = None,
        intent: str = "",
        note: str | None = None,
    ) -> str:
        """维护任务板——目标/路线/关键事实（多步任务的记忆锚点，压缩免疫）。

        多步骤任务建议在第一次 read_screen 之后创建路线；里程碑 3-7 项起步、
        边走边细化。约束：至多一个 in_progress；完成一项就即时标记 completed；
        被卡住标 blocked 并写明 reason；item 只转状态不删除。关键事实随手记
        （价格/已选值/坑），最多 10 条、每条 ≤120 字。

        参数：
        - items：全量替换路线。每项为 {id, content, status, reason?, evidence_note?}，
          status ∈ pending|in_progress|completed|blocked。不传则保留现有路线。
          完成项（completed）必须写 evidence_note（屏幕上的完成证据）。
        - add_amendments：向"目标.补充"追加条目（只增不改，用于细化理解/记录用户补充）。
        - facts：全量替换关键事实列表。不传则保留现有事实。
        - intent：本步意图（务必填写，会汇入流程线）。
        - note：本步发现（可选）。
        目标 base 段不可由本工具修改（仅任务启动时播种）。
        校验不通过（多个 in_progress / 路线超 15 项 / blocked 缺 reason /
        completed 缺 evidence_note / 事实超限 / 把 pending 直接标 completed（须先
        in_progress）/ 一次批量补标多项 completed）时不写入并返回错误说明。
        """

        current = getattr(session, "task_doc", None)
        if current is None:
            current = TaskDoc()

        candidate = TaskDoc(
            goal_base=current.goal_base,
            amendments=list(current.amendments),
            items=list(current.items),
            facts=list(current.facts),
        )

        try:
            if items is not None:
                candidate.items = [_coerce_item(entry) for entry in items]
            if add_amendments:
                candidate.amendments.extend(
                    str(a).strip() for a in add_amendments if str(a).strip()
                )
            if facts is not None:
                candidate.facts = [str(f).strip() for f in facts if str(f).strip()]
        except ValueError as exc:
            return f"未写入（输入无效）：{exc}"

        # Validate against the pre-write board so the A4 transition discipline
        # (no pending→completed jump, no batch back-fill) can compare item states.
        error = candidate.validate(previous=current)
        if error is not None:
            return f"未写入（校验失败）：{error}"

        session.task_doc = candidate

        parts = ["已更新任务板。"]
        rendered = candidate.render(lang)
        if rendered:
            parts.append(rendered)
        if getattr(session, "screen_seq", 0) == 0:
            parts.append("提示：尚无屏幕观测，建议先 read_screen 再规划路线。")
        return "\n".join(parts)

    return StructuredTool.from_function(update_task_doc, parse_docstring=True)
