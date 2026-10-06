Yes. A clean way is to listen to LiveKit’s `conversation_item_added` event and save every committed user/assistant message to SQL. LiveKit emits that event for both user and agent messages. :chatgpt-content-reference{index="0"}

Below is a simple **SQLite demo first** because it runs immediately. Later you can switch the connection string to PostgreSQL or MySQL without changing much.

## 1. Install packages

```bash
pip install sqlalchemy aiosqlite
pip install "livekit-agents[langchain]~=1.8"
pip install langgraph "langchain[openai]" python-dotenv
```

---

## 2. `database.py`

```python
from datetime import datetime

from sqlalchemy import (
    DateTime,
    ForeignKey,
    Integer,
    String,
    Text,
)
from sqlalchemy.ext.asyncio import (
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.orm import (
    DeclarativeBase,
    Mapped,
    mapped_column,
    relationship,
)


DATABASE_URL = "sqlite+aiosqlite:///./voice_agent.db"


engine = create_async_engine(
    DATABASE_URL,
    echo=False,
)


SessionLocal = async_sessionmaker(
    bind=engine,
    class_=AsyncSession,
    expire_on_commit=False,
)


class Base(DeclarativeBase):
    pass


class Conversation(Base):
    __tablename__ = "conversations"

    id: Mapped[int] = mapped_column(
        Integer,
        primary_key=True,
        autoincrement=True,
    )

    room_id: Mapped[str] = mapped_column(
        String(255),
        unique=True,
        index=True,
    )

    customer_id: Mapped[str | None] = mapped_column(
        String(255),
        nullable=True,
    )

    started_at: Mapped[datetime] = mapped_column(
        DateTime,
        default=datetime.utcnow,
    )

    ended_at: Mapped[datetime | None] = mapped_column(
        DateTime,
        nullable=True,
    )

    messages: Mapped[list["Message"]] = relationship(
        back_populates="conversation",
        cascade="all, delete-orphan",
    )


class Message(Base):
    __tablename__ = "messages"

    id: Mapped[int] = mapped_column(
        Integer,
        primary_key=True,
        autoincrement=True,
    )

    conversation_id: Mapped[int] = mapped_column(
        ForeignKey("conversations.id"),
        index=True,
    )

    role: Mapped[str] = mapped_column(
        String(50),
    )

    content: Mapped[str] = mapped_column(
        Text,
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime,
        default=datetime.utcnow,
    )

    conversation: Mapped["Conversation"] = relationship(
        back_populates="messages",
    )


async def init_db():
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)


async def create_conversation(
    room_id: str,
    customer_id: str | None = None,
):
    async with SessionLocal() as db:

        conversation = Conversation(
            room_id=room_id,
            customer_id=customer_id,
        )

        db.add(conversation)

        await db.commit()
        await db.refresh(conversation)

        return conversation.id


async def save_message(
    conversation_id: int,
    role: str,
    content: str,
):
    async with SessionLocal() as db:

        message = Message(
            conversation_id=conversation_id,
            role=role,
            content=content,
        )

        db.add(message)

        await db.commit()


async def end_conversation(
    conversation_id: int,
):
    async with SessionLocal() as db:

        conversation = await db.get(
            Conversation,
            conversation_id,
        )

        if conversation:
            conversation.ended_at = datetime.utcnow()
            await db.commit()
```

---

## 3. LiveKit + LangGraph agent

```python
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
```

The important part is this:

```python
@session.on("conversation_item_added")
def on_conversation_item_added(event):

    item = event.item

    asyncio.create_task(
        save_message(
            conversation_id,
            str(item.role),
            item.text_content,
        )
    )
```

LiveKit documents `conversation_item_added` specifically as the event fired when a user or agent message is committed to chat history. :chatgpt-content-reference{index="1"}

## What gets saved

Suppose the call is:

```text
User:
Hello

AI:
Hello! How can I help you?

User:
আমার order কোথায়?

AI:
আপনার order number বলুন।
```

Your database becomes roughly:

### `conversations`

| id | room_id | customer_id |
|---:|---|---|
| 1 | call_abc123 | null |

### `messages`

| id | conversation_id | role | content |
|---:|---:|---|---|
| 1 | 1 | user | Hello |
| 2 | 1 | assistant | Hello! How can I help you? |
| 3 | 1 | user | আমার order কোথায়? |
| 4 | 1 | assistant | আপনার order number বলুন। |

So the relationship is:

```text
conversation 1
       │
       ├── message 1
       ├── message 2
       ├── message 3
       └── message 4
```

## Why use `conversation_item_added` instead of only STT?

You could listen to:

```python
@session.on("user_input_transcribed")
```

and save user speech. LiveKit provides final/interim transcript information through that event. :chatgpt-content-reference{index="2"}

But then you're only directly capturing:

```text
user → STT
```

You would separately need to capture the assistant response.

`conversation_item_added` is cleaner because it gives you both:

```text
user message
assistant message
```

from the same event.

## If you want PostgreSQL

Change:

```python
DATABASE_URL = "sqlite+aiosqlite:///./voice_agent.db"
```

to:

```python
DATABASE_URL = (
    "postgresql+asyncpg://"
    "postgres:password@localhost:5432/voice_agent"
)
```

and install:

```bash
pip install asyncpg
```

Then your architecture becomes:

```text
                LiveKit Room
                     │
                     ↓
               AgentSession
                     │
           conversation_item_added
                     │
                     ↓
              SQLAlchemy
                     │
                     ↓
                PostgreSQL

       ┌─────────────┴─────────────┐
       ↓                           ↓

conversations                   messages
─────────────                   ────────
id                              id
room_id                         conversation_id
customer_id                     role
started_at                      content
ended_at                        created_at
```

For your eventual phone system, I would add these fields too:

```text
conversations
├── id
├── room_id
├── customer_id
├── caller_phone
├── called_phone
├── direction
├── started_at
├── ended_at
├── duration
├── status
├── summary
└── recording_url
```

That gives you a proper call-history system rather than just message storage.