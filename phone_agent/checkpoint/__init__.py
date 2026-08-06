"""Checkpoint egress helpers for phone_agent."""

from phone_agent.checkpoint.serde import RedactingSerializer
from phone_agent.checkpoint.goal_resume import TrustedGoalResumeBinder

__all__ = ["RedactingSerializer", "TrustedGoalResumeBinder", "build_hitl_checkpointer"]


def build_hitl_checkpointer():
    """Build the process-local checkpointer used by the HITL resume path.

    Returns a plain ``InMemorySaver`` — deliberately NOT wrapped in
    ``RedactingSerializer``. Rationale (verified against langgraph 1.2.2):

    * ``InMemorySaver`` supports a ``serde`` parameter, but the checkpoint
      envelope includes structural metadata (``channel_versions`` /
      ``versions_seen`` string values) which ``RedactingSerializer`` stubs at
      egress; on resume ``_load_blobs`` rebuilds blob keys from those stubs and
      raises ``TypeError: unhashable type: 'dict'``.
    * Even with the envelope fixed, the stub policy replaces every non-safe
      string state channel (``task``, ``messages``, ``action_raw``, ...) with a
      ``{redacted, length}`` stub, so the resumed run would lose the task and
      conversation history — defeating "从原地续跑". P0#10's "宁死不写明文" is
      preserved because the live checkpointer is process-local (never written
      to disk): the state it holds in RAM is the same state the graph already
      holds in memory.

    A future durable (sqlite) checkpointer should re-introduce a checkpoint
    egress policy here (see ``RedactingSerializer``) with a resume-safe policy.
    """

    from langgraph.checkpoint.memory import InMemorySaver

    return InMemorySaver()
