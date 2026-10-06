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