import logging
from typing import Annotated, TypedDict

from dotenv import load_dotenv

from langchain.chat_models import init_chat_model
from langchain_core.messages import BaseMessage, SystemMessage

from langgraph.graph import START, StateGraph
from langgraph.graph.message import add_messages

from livekit import agents
from livekit.agents import (
    Agent,
    AgentServer,
    AgentSession,
    JobContext,
    inference,
)

from livekit.plugins import langchain


load_dotenv()

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("voice-agent")


# ============================================================
# 1. LangGraph state
# ============================================================

class State(TypedDict):
    messages: Annotated[list[BaseMessage], add_messages]


# ============================================================
# 2. Create LangGraph
# ============================================================

def create_graph():

    model = init_chat_model(
        "openai:gpt-4.1-mini"
    )

    system_message = SystemMessage(
        content="""
You are a friendly AI voice assistant.

Rules:
- Keep responses concise because you are speaking aloud.
- Speak naturally.
- Avoid markdown.
- Ask only one question at a time.
- If the user speaks Bengali, reply in Bengali.
- If the user speaks English, reply in English.
"""
    )

    def chatbot(state: State):

        messages = [
            system_message,
            *state["messages"],
        ]

        response = model.invoke(messages)

        return {
            "messages": [response]
        }

    graph_builder = StateGraph(State)

    graph_builder.add_node(
        "chatbot",
        chatbot
    )

    graph_builder.add_edge(
        START,
        "chatbot"
    )

    return graph_builder.compile()


# ============================================================
# 3. LiveKit server
# ============================================================

server = AgentServer()


# ============================================================
# 4. LiveKit voice session
# ============================================================

@server.rtc_session()
async def entrypoint(ctx: JobContext):

    logger.info("Starting voice agent")

    graph = create_graph()

    # LangGraph becomes LiveKit's LLM
    agent = Agent(
        instructions="",
        llm=langchain.LLMAdapter(
            graph=graph
        ),
    )

    session = AgentSession(

        # Speech → Text
        stt=inference.STT(
            model="deepgram/flux-general",
            language="en",
        ),

        # Text → Speech
        tts=inference.TTS(
            model="fishaudio/s2.1-pro",
            voice="fa4c9eb3dccc4806b382b40d61c6b10a",
        ),
    )

    # Connect agent to LiveKit room
    await session.start(
        room=ctx.room,
        agent=agent,
    )

    # Initial greeting
    await session.generate_reply(
        instructions=(
            "Greet the user briefly and ask how you can help."
        )
    )


# ============================================================
# 5. Start server
# ============================================================

if __name__ == "__main__":
    agents.cli.run_app(server)