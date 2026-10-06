import os

from dotenv import load_dotenv

from livekit import agents
from livekit.agents import AgentServer, AgentSession, Agent, inference, room_io, TurnHandlingOptions
from livekit.plugins import ai_coustics, elevenlabs, groq

load_dotenv(".env")

voice_id="FGY2WhTYpPnrIDTdsKH5"
model_id="eleven_v3"

class Assistant(Agent):
    def __init__(self) -> None:
        super().__init__(
            instructions="""You are a helpful voice AI assistant.
            You are having a live spoken conversation. The app transcribes the user's
            microphone input into text for you and speaks your replies aloud using text to speech.
            When the user asks whether you can hear them, acknowledge that their message
            reached you naturally, for example: Yes, I'm getting your voice. How can I help?
            Do not respond to microphone checks by saying you are only a text-based AI
            or cannot hear. You receive their speech through the app's transcription.
            You cannot assess actual audio quality, volume, tone, or background sounds
            from a transcript alone, so do not claim to hear clearly or diagnose the microphone.
            Explain the transcription setup if the user specifically asks how you hear them.
            You eagerly assist users with their questions by providing information from your extensive knowledge.
            Your responses are concise, to the point, and without any complex formatting or punctuation including emojis, asterisks, or other symbols.
            You are curious, friendly, and have a sense of humor.""",
        )

server = AgentServer()

@server.rtc_session(agent_name="my-agent")
async def my_agent(ctx: agents.JobContext):
    session = AgentSession(
        # Uses GROQ_API_KEY from .env for Whisper transcription.
        stt=inference.STT(model="cartesia/ink-whisper"),
        # Detect speech activity for turn handling and interruptions.
        vad=inference.VAD(model="silero"),
        # Uses GROQ_API_KEY from .env.
        llm=inference.LLM(model="google/gemini-3.5-flash"),
        # Uses your ElevenLabs API key and configured voice/model.
        tts=inference.TTS(
            model="deepgram/aura-2:odysseus"
        ),
        turn_handling=TurnHandlingOptions(
            turn_detection="vad",
            interruption={"mode": "vad"},
        ),
    )


    await session.start(
        room=ctx.room,
        agent=Assistant(),
        room_options=room_io.RoomOptions(
            audio_input=room_io.AudioInputOptions(
                noise_cancellation=ai_coustics.audio_enhancement(model=ai_coustics.EnhancerModel.QUAIL_VF_S),
            ),
        ),
    )

    # Speak directly: some LLMs reject requests before the first user message.
    await session.say("Hi! How can I help you today?")


if __name__ == "__main__":
    agents.cli.run_app(server)
