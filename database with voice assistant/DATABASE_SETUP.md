# Voice assistant with SQL transcripts

Files:
- `database.py`: SQLite schema and async storage helpers using built-in sqlite3.
- `voice_assistant_database.py`: LiveKit session, ordered message queue, and shutdown drain.
- `langgraph_livekit.py`: existing `create_graph()` reused for the LLM. Its model is `openai:gpt-4.1-mini`.
- `saving_message_to_database.py`: original tutorial retained as reference; do not run it as Python.

Install into your activated environment:

```bat
python -m pip install -r requirements-database.txt
```

In `.env`, provide your LiveKit URL, API key and secret, and `OPENAI_API_KEY` for the LangGraph model. STT_MODEL and TTS_MODEL are optional overrides; LLM_MODEL does not override the model in the existing LangGraph module.

Run:

```bat
python voice_assistant_database.py dev
```

Connect Playground to the same LiveKit project and dispatch `sql-agent`. Optionally set `DATABASE_AGENT_NAME` to change this name. A separate default name prevents accidental dispatch to the other agent when both processes run.

The SQLite database `voice_agent.db` is created beside database.py on the first session. No SQL server or SQLAlchemy installation is required. This is a new SQLite implementation, not a migration of the SQLAlchemy tutorial's schema. If you already created a database from that tutorial, back it up and configure a different DATABASE_PATH before running this version.

Each session creates a conversation row, including when a room name is reused. Text messages are saved with a sequence, LiveKit message ID, timestamp, and interruption flag. Query messages with `ORDER BY sequence` within a conversation. Images/audio are not stored. The existing avatar/screen assistant remains separate.

The writer retries failures three times and logs unsuccessful writes. Graceful shutdown waits for queued writes; a crash or forced termination can still lose queued data. SQL storage does not automatically restore conversation memory.

Validation: temporary-database checks cover inserts, duplicate protection, room reuse, ordering, and conversation end time. Python syntax checks pass. A live LangGraph/LiveKit call has not been tested.
