"""TaskDoc: the unified goal + plan + facts task board (thin-loop v2 增量一).

The TaskDoc is a *single* document with three sections — 目标 (goal), 路线
(plan), 关键事实 (facts). It lives on the :class:`~phone_agent.v2.session.PhoneSession`
and is mutated *only* through the ``update_task_doc`` tool (the model is the sole
writer). The harness seeds ``goal_base`` at run start; the model never rewrites
it. A ``before_model`` middleware renders the doc as a pinned block so it is
immune to context compaction.

See ``docs/refactor-thin-loop-v2-taskdoc.md`` §1-§2.1 for the binding contract.
"""

from __future__ import annotations

from dataclasses import dataclass, field

# Section limits / vocabulary (spec §1).
MAX_ITEMS = 15
MAX_FACTS = 10
MAX_FACT_LEN = 120
OPEN_STATUSES = ("pending", "in_progress")
VALID_STATUSES = ("pending", "in_progress", "completed", "blocked")


def _is_cn(lang: str) -> bool:
    """Return True for Chinese language codes (mirrors ``prompts.get_system_prompt``)."""

    return (lang or "").strip().lower() in {"cn", "zh", "zh-cn", "zh_cn", "chinese"}


@dataclass
class TaskItem:
    """One 路线 milestone.

    ``status`` is one of :data:`VALID_STATUSES`; ``reason`` is required only when
    ``status == "blocked"`` (why the item is stuck).
    """

    id: str
    content: str
    status: str = "pending"
    reason: str | None = None


@dataclass
class TaskDoc:
    """The unified goal + plan + facts board. Mutated only via ``update_task_doc``.

    - ``goal_base``: the user's original task text; harness-seeded, never rewritten.
    - ``amendments``: append-only refinements (model understanding / user additions).
    - ``items``: the 路线 milestones (full-replaced on each tool write).
    - ``facts``: short model notes (prices / chosen values / gotchas).
    """

    goal_base: str = ""
    amendments: list[str] = field(default_factory=list)
    items: list[TaskItem] = field(default_factory=list)
    facts: list[str] = field(default_factory=list)

    # -- validation -------------------------------------------------------

    def validate(self) -> str | None:
        """Return ``None`` when valid, else a human-readable error string.

        Enforces the spec §1 constraints: at most one ``in_progress`` item;
        at most :data:`MAX_ITEMS` items; every ``blocked`` item carries a
        ``reason``; only known statuses; at most :data:`MAX_FACTS` facts, each
        at most :data:`MAX_FACT_LEN` characters.
        """

        if len(self.items) > MAX_ITEMS:
            return f"路线项过多：{len(self.items)} 项，至多 {MAX_ITEMS} 项。"

        in_progress = 0
        for item in self.items:
            if item.status not in VALID_STATUSES:
                return (
                    f"未知状态 {item.status!r}（item {item.id!r}）；"
                    f"合法状态：{', '.join(VALID_STATUSES)}。"
                )
            if item.status == "in_progress":
                in_progress += 1
            if item.status == "blocked" and not (item.reason or "").strip():
                return f"blocked 项 {item.id!r} 必须带 reason（说明为何卡住）。"
        if in_progress > 1:
            return f"至多一个 in_progress 项，当前有 {in_progress} 个。"

        if len(self.facts) > MAX_FACTS:
            return f"关键事实过多：{len(self.facts)} 条，至多 {MAX_FACTS} 条。"
        for fact in self.facts:
            if len(fact) > MAX_FACT_LEN:
                return f"关键事实过长（>{MAX_FACT_LEN} 字符）：{fact[:20]!r}…"
        return None

    # -- open-item queries (finish gate uses these) -----------------------

    def has_open_items(self) -> bool:
        """True if any item is ``pending`` or ``in_progress``."""

        return any(item.status in OPEN_STATUSES for item in self.items)

    def open_items_summary(self) -> str:
        """One-line summary of open items, for the finish rejection message."""

        return "；".join(
            f"{item.id}:{item.content}[{item.status}]"
            for item in self.items
            if item.status in OPEN_STATUSES
        )

    # -- rendering (pinned block) -----------------------------------------

    def render(self, lang: str = "cn") -> str:
        """Render as a pinned block. Empty doc -> ``""``; empty sections omitted.

        A doc is *empty* only when it has no ``goal_base``, no amendments, no
        items, and no facts. Any populated section is rendered under its header;
        blank sections (including 路线/关键事实) are skipped.
        """

        if not (self.goal_base.strip() or self.amendments or self.items or self.facts):
            return ""

        cn = _is_cn(lang)
        lines: list[str] = []

        if self.goal_base.strip() or self.amendments:
            lines.append("## 目标" if cn else "## Goal")
            if self.goal_base.strip():
                lines.append(f"base: {self.goal_base}")
            if self.amendments:
                lines.append("补充：" if cn else "Amendments:")
                lines.extend(f"- {a}" for a in self.amendments)

        if self.items:
            lines.append("## 路线" if cn else "## Plan")
            for item in self.items:
                suffix = ""
                if item.status == "blocked" and item.reason:
                    suffix = (
                        f"（原因：{item.reason}）" if cn else f" (reason: {item.reason})"
                    )
                lines.append(f"- [{item.status}] {item.id}: {item.content}{suffix}")

        if self.facts:
            lines.append("## 关键事实" if cn else "## Key Facts")
            lines.extend(f"- {fact}" for fact in self.facts)

        return "\n".join(lines)
