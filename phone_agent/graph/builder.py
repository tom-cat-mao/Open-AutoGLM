"""Graph builder: constructs and compiles the Plan-Execute-Reflect StateGraph."""

from langgraph.graph import StateGraph, START, END

from phone_agent.graph.state import AgentState
from phone_agent.graph.nodes.plan import plan_node
from phone_agent.graph.nodes.execute import execute_node
from phone_agent.graph.nodes.reflect import reflect_node
from phone_agent.graph.nodes.confirm import confirm_node
from phone_agent.graph.nodes.takeover import takeover_node
from phone_agent.graph.edges import should_continue, after_execute, after_interrupt


def create_agent_graph():
    """
    Create and compile the Plan-Execute-Reflect StateGraph.

    Graph topology:
    ```
    START → plan → execute → reflect → should_continue?
                                    ├─ "end" → END
                                    └─ "replan" → plan
    ```

    execute node also has a conditional edge:
    - "reflect" → reflect
    - "replan" → plan (skip reflect for Wait/Note/Call_API/Interact)
    - "confirm" → confirm → reflect/end
    - "takeover" → takeover → reflect/end
    - "end" → END (finish or error)
    """
    graph = StateGraph(AgentState)

    graph.add_node("plan", plan_node)
    graph.add_node("execute", execute_node)
    graph.add_node("reflect", reflect_node)
    graph.add_node("confirm", confirm_node)
    graph.add_node("takeover", takeover_node)

    graph.add_edge(START, "plan")
    graph.add_edge("plan", "execute")
    graph.add_conditional_edges(
        "execute",
        after_execute,
        {"reflect": "reflect", "replan": "plan", "confirm": "confirm", "takeover": "takeover", "end": END},
    )
    graph.add_conditional_edges(
        "reflect",
        should_continue,
        {"replan": "plan", "end": END},
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
