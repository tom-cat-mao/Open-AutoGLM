"""Graph builder: constructs and compiles the Plan-Execute-Reflect StateGraph."""

from langgraph.graph import StateGraph, START, END

from phone_agent.graph.state import AgentState
from phone_agent.graph.nodes.goal_node import goal_node
from phone_agent.graph.nodes.plan import plan_node
from phone_agent.graph.nodes.execute import execute_node
from phone_agent.graph.nodes.reflect import reflect_node
from phone_agent.graph.nodes.confirm import confirm_node
from phone_agent.graph.nodes.takeover import takeover_node
from phone_agent.graph.edges import (
    after_execute,
    after_goal,
    after_interrupt,
    should_continue,
)


def create_agent_graph():
    """
    Create and compile the Plan-Execute-Reflect StateGraph.

    Graph topology:
    ```
    START → goal → plan → execute → [confirm|takeover|reflect|replan|end]
                                   ├─ confirm → after_interrupt → [execute|reflect|end]
                                   ├─ takeover → after_interrupt → [reflect|end]
                                   ├─ reflect → should_continue → [takeover|replan→goal|end]
                                   ├─ replan → goal → plan (only internal no-observation capabilities)
                                   └─ end → END
    ```
    """
    graph = StateGraph(AgentState)

    graph.add_node("goal", goal_node)
    graph.add_node("plan", plan_node)
    graph.add_node("execute", execute_node)
    graph.add_node("reflect", reflect_node)
    graph.add_node("confirm", confirm_node)
    graph.add_node("takeover", takeover_node)

    graph.add_edge(START, "goal")
    graph.add_conditional_edges(
        "goal",
        after_goal,
        {"plan": "plan", "takeover": "takeover", "end": END},
    )
    graph.add_edge("plan", "execute")
    graph.add_conditional_edges(
        "execute",
        after_execute,
        {
            "reflect": "reflect",
            "replan": "plan",
            "confirm": "confirm",
            "takeover": "takeover",
            "end": END,
        },
    )
    graph.add_conditional_edges(
        "reflect",
        should_continue,
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

    return graph.compile()
