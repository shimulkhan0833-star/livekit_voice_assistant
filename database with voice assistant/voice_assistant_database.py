import asyncio
import logging

from typing import Annotated, TypedDict

from dotenv import load_dotenv

from langchain.chat_models import init_chat_model
from langchain_core.messages import (
    BaseMessage,
    SystemMessage,
)

from langgraph.graph import START, StateGraph
from langgraph.graph.message import add_messages

from livekit import agents
from livekit.agents import (
    Agent,
    AgentServer,
    AgentSession,
    ConversationItemAddedEvent,
    JobContext,
    inference,
)

from livekit.agents.llm import ChatMessage
from livekit.plugins import langchain

from database import (
    create_conversation,
    end_conversation,
    init_db,
    save_message,
)


load_dotenv()

logging.basicConfig(level=logging.INFO)

logger = logging.getLogger("voice-agent")


# =========================================================
# LangGraph
# =========================================================

class State(TypedDict):
    messages: Annotated[
        list[BaseMessage],
        add_messages,
    ]


def create_graph():

    model = init_chat_model(
        "openai:gpt-4.1-mini"
    )

    system_message = SystemMessage(
        content="""
You are a helpful voice assistant.

Keep responses concise and natural.

If the user speaks Bengali,
reply in Bengali.

If the user speaks English,
reply in English.
"""
    )

    async def chatbot(state: State):

        response = await model.ainvoke(
            [
                system_message,
                *state["messages"],
            ]
        )

        return {
            "messages": [response]
        }

    builder = StateGraph(State)

    builder.add_node(
        "chatbot",
        chatbot,
    )

    builder.add_edge(
        START,
        "chatbot",
    )

    return builder.compile()


# =========================================================
# LiveKit
# =========================================================

server = AgentServer()


@server.rtc_session()
async def entrypoint(ctx: JobContext):

    await init_db()

    room_id = ctx.room.name

    logger.info(
        "Starting conversation for room: %s",
        room_id,
    )

    # Later this could come from caller phone number
    customer_id = None

    conversation_id = await create_conversation(
        room_id=room_id,
        customer_id=customer_id,
    )

    logger.info(
        "Database conversation ID: %s",
        conversation_id,
    )

    graph = create_graph()

    agent = Agent(
        instructions="",
        llm=langchain.LLMAdapter(
            graph=graph
        ),
    )

    session = AgentSession(
        stt=inference.STT(
            model="deepgram/nova-3-general",
        ),
        tts=inference.TTS(
            model="cartesia/sonic-3",
            voice="9626c31c-bec5-4cca-baa8-f8ba9e84c8bc",
        ),
    )


    # =====================================================
    # Save every user + assistant message
    # =====================================================

    @session.on("conversation_item_added")
    def on_conversation_item_added(
        event: ConversationItemAddedEvent,
    ):

        item = event.item

        if not isinstance(item, ChatMessage):
            return

        role = str(item.role)

        text = item.text_content

        if not text:
            return

        logger.info(
            "%s: %s",
            role,
            text,
        )

        # Event handlers are synchronous,
        # so schedule async DB write
        asyncio.create_task(
            save_message(
                conversation_id=conversation_id,
                role=role,
                content=text,
            )
        )


    # =====================================================
    # When session closes
    # =====================================================

    @session.on("close")
    def on_close(event):

        logger.info(
            "Conversation ended: %s",
            conversation_id,
        )

        asyncio.create_task(
            end_conversation(
                conversation_id
            )
        )


    # =====================================================
    # Start voice agent
    # =====================================================

    await session.start(
        room=ctx.room,
        agent=agent,
    )

    await session.generate_reply(
        instructions=(
            "Greet the caller briefly "
            "and ask how you can help."
        )
    )


if __name__ == "__main__":
    agents.cli.run_app(server)