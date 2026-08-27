"""Graph builder: constructs and compiles the Plan-Execute-Reflect StateGraph."""

from langgraph.graph import StateGraph, START, END

from phone_agent.graph.state import AgentState
from phone_agent.graph.nodes.goal_node import goal_node
from phone_agent.graph.nodes.plan import plan_node
from phone_agent.graph.nodes.execute import execute_node
from phone_agent.graph.nodes.reflect import reflect_node
from phone_agent.graph.nodes.acceptance import acceptance_node
from phone_agent.graph.nodes.confirm import confirm_node
from phone_agent.graph.nodes.takeover import takeover_node
from phone_agent.graph.edges import (
    after_acceptance,
    after_execute,
    after_goal,
    after_interrupt,
    after_plan,
    should_continue,
)


def create_agent_graph(checkpointer=None):
    """
    Create and compile the Plan-Execute-Reflect StateGraph.

    ``checkpointer`` (optional) is passed to ``graph.compile()``; when a
    checkpointer is present, ``interrupt()`` inside confirm/takeover pauses
    the graph instead of escaping as a ``GraphInterrupt`` and the run can be
    resumed with ``Command(resume=...)`` (HITL resume).

    Graph topology:
    ```
    START → goal → plan → execute → [confirm|takeover|acceptance|reflect|replan|end]
                                   ├─ confirm → after_interrupt → [execute|reflect|end]
                                   ├─ takeover → after_interrupt → [reflect|end]
                                   ├─ acceptance → after_acceptance → [takeover|replan→goal|end]
                                   ├─ reflect → should_continue → [takeover|replan→goal|end]
                                   ├─ replan → goal → plan (only internal no-observation capabilities)
                                   └─ end → END
    ```

    `reflect` answers "did this action work?" on every step; `acceptance`
    answers "is the task complete?" and runs only on a finish claim.
    """
    graph = StateGraph(AgentState)

    graph.add_node("goal", goal_node)
    graph.add_node("plan", plan_node)
    graph.add_node("execute", execute_node)
    graph.add_node("reflect", reflect_node)
    graph.add_node("acceptance", acceptance_node)
    graph.add_node("confirm", confirm_node)
    graph.add_node("takeover", takeover_node)

    graph.add_edge(START, "goal")
    graph.add_conditional_edges(
        "goal",
        after_goal,
        {"plan": "plan", "takeover": "takeover", "end": END},
    )
    graph.add_conditional_edges(
        "plan",
        after_plan,
        {"execute": "execute", "replan": "plan", "end": END},
    )
    graph.add_conditional_edges(
        "execute",
        after_execute,
        {
            "reflect": "reflect",
            "acceptance": "acceptance",
            "replan": "plan",
            "confirm": "confirm",
            "takeover": "takeover",
            "end": END,
        },
    )
    graph.add_conditional_edges(
        "reflect",
        should_continue,
        {
            "replan": "goal",
            "takeover": "takeover",
            "acceptance": "acceptance",
            "end": END,
        },
    )
    graph.add_conditional_edges(
        "acceptance",
        after_acceptance,
        {"replan": "goal", "takeover": "takeover", "end": END},
    )
    graph.add_conditional_edges(
        "confirm",
        after_interrupt,
        {"reflect": "reflect", "execute": "execute", "end": END},
    )
    graph.add_conditional_edges(
        "takeover",
        after_interrupt,
        {"reflect": "reflect", "end": END},
    )

    return graph.compile(checkpointer=checkpointer)
