# Review: saving messages to SQL

## Verdict

The design is a reasonable SQLite demo: listen for committed LiveKit conversation messages and insert their text into a messages table linked to a conversation. However, `saving_message_to_database.py` is currently a Markdown tutorial, not an executable Python module. Its two main Python code blocks pass a syntax check independently; that does not prove the complete application runs.

## What is correct

- `conversation_item_added` is the appropriate LiveKit event for capturing messages added to conversation history, including user and assistant messages. The listener is registered before session startup and the greeting.
- `item.text_content` extracts text. It does not store attached screen images, audio, or a video recording.
- The Conversation-to-Message relationship models one conversation with many messages.
- SQLAlchemy ORM inserts bind values rather than constructing SQL from transcript strings.
- Each database operation creates its own AsyncSession, which avoids sharing a mutable session between concurrent tasks.
- An async database call cannot simply be awaited inside the synchronous event callback; scheduling asynchronous work is a valid starting point.
- SQL transcript storage does not require LangGraph. The same listener can be added to the AgentSession in `voice_assis.py`.

## Findings and required changes

### 1. The file cannot run as Python

The file starts with prose and includes headings, installation commands, Markdown fences, and citation placeholders. Running it with Python fails at line 1. The imported `database.py` does not currently exist.

Keep the tutorial in a Markdown file and extract the database definitions into `database.py` and the agent code into a separate executable Python file. The short snippets near the end are explanatory fragments, not additional code to paste into the agent.

### 2. Writes can be lost at shutdown

Both `save_message(...)` and `end_conversation(...)` are launched through `asyncio.create_task(...)`, without keeping task references or awaiting completion. The close callback schedules work but does not ensure it finishes before the job exits. Database failures also have no application-level recovery or success tracking.

Use a per-conversation queue and a tracked writer task, or retain and await all pending write tasks. Register an async job shutdown callback with `ctx.add_shutdown_callback(...)`. During shutdown, finish session activity while the listener is still active, drain accepted writes, persist the conversation end time, and close database resources owned by that job. Log write failures explicitly and decide how retries are handled. A graceful drain cannot guarantee delivery after a process crash; durable delivery needs additional design.

### 3. Message ordering and duplicate protection are missing

Independent tasks may commit in a different order from the events. Database insertion IDs and `datetime.utcnow()` at insertion time do not establish the original conversation order.

Assign a sequence number in the synchronous event handler and persist LiveKit's `item.id`, `item.created_at`, and `item.interrupted` alongside the text. Add a unique constraint such as `(conversation_id, livekit_message_id)` for retry protection. Read messages with an explicit ORDER BY sequence. Store interruption metadata so a partial response is not mistaken for an uninterrupted answer; the event is a chat-history record, not proof that the user heard every word.

### 4. Unique room names can reject later sessions

`room_id` stores `ctx.room.name`, not a room SID. It is unique in the schema, but `create_conversation()` always inserts a new row. Another session using the same room name can therefore fail with a uniqueness error.

Choose the intended policy: one row per session with a separate session identifier and a non-unique room name, or an explicit get-or-create/resume policy for a room. Rename the field to `room_name` if that is what it stores.

### 5. Dependencies are not captured in requirements.txt

The current requirements declare LiveKit with Anam and python-dotenv. They do not declare this demo's SQLAlchemy, aiosqlite, LangGraph, LangChain OpenAI integration, or LiveKit LangChain extra. The installation commands in the tutorial are not a reproducible project dependency file.

Before running the extracted demo, declare compatible dependencies and provide the credentials required by its model setup. The direct LangChain OpenAI model needs its provider credentials in addition to the LiveKit configuration. They were not inspected or tested during this review.

### 6. SQLite needs explicit database configuration

- `sqlite+aiosqlite:///./voice_agent.db` creates the file relative to the process working directory. Use a deliberate absolute path if launch directories can vary.
- SQLite foreign-key enforcement needs `PRAGMA foreign_keys=ON` on each connection. Declaring ForeignKey alone is insufficient; the ORM cascade is not a substitute for database enforcement.
- Concurrent session jobs can compete for SQLite writes. A per-session queue helps within a session but does not serialize every worker process.
- `create_all()` is suitable for initial demo setup; it does not migrate an existing schema. Prefer initialization outside per-call startup and migrations for later schema changes.
- `datetime.utcnow()` returns naive UTC and is deprecated on newer Python versions. Define a consistent UTC storage policy; changing to aware datetimes also requires matching column/driver handling, especially with SQLite.

### 7. Scope should match the description

The handler checks for ChatMessage and nonempty text, but does not explicitly restrict roles. If the intended policy is only user and assistant text, add `if item.role not in {"user", "assistant"}: return`.

SQL inserts alone do not restore conversation history into LiveKit or provide LangGraph checkpoint memory. Reading stored messages and restoring context is a separate feature. This demo also does not include the existing screen-sharing or avatar behavior from `voice_assis.py`.

The demo uses `@server.rtc_session()` without a dispatch name. Do not assume it registers as `my-agent`; align the decorator with your chosen Playground dispatch configuration when integrating it.

## Recommended flow

1. Create or identify the conversation before starting the session.
2. Register the message listener before the greeting.
3. On each relevant event, capture message ID, role, text, event time, interruption flag, and sequence.
4. Enqueue that record without waiting for SQL inside the synchronous callback.
5. A tracked writer persists records and handles errors/retries without duplicating rows.
6. At graceful shutdown, drain pending records, record the end time, and release resources.

Keep transcript logging intentional: the current logger writes the entire message text as well as storing it in SQL. Configure access and retention for both locations according to the application's requirements.

## Validation performed

- Read the demo, `langgraph_livekit.py`, and requirements.
- Confirmed that the complete demo file is invalid Python and `database.py` is absent.
- Parsed the two main Python blocks successfully.
- Confirmed the installed LiveKit SDK exposes ConversationItemAddedEvent, the shutdown callback API, and message ID/time/interruption metadata.
- Did not run a live call, install dependencies, create a database, or verify successful SQL inserts. No existing agent behavior was changed.

When implementing, test user and assistant inserts, non-text filtering, conversation isolation, ordering, retries, reuse of a room name, and graceful shutdown with pending writes.

## References

- [LiveKit events](https://docs.livekit.io/reference/agents/events/)
- [LiveKit Python SDK](https://docs.livekit.io/reference/python/livekit/agents/)
- [SQLAlchemy async sessions](https://docs.sqlalchemy.org/en/20/orm/extensions/asyncio.html)
- [SQLAlchemy SQLite configuration](https://docs.sqlalchemy.org/en/20/dialects/sqlite.html)
