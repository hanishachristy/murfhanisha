import logging

from dotenv import load_dotenv
from livekit import rtc
from livekit.agents import (
    Agent,
    AgentServer,
    AgentSession,
    JobContext,
    JobProcess,
    cli,
    inference,
    tokenize,
    room_io,
)
from livekit.plugins import murf, silero, google, deepgram, noise_cancellation
from livekit.plugins.turn_detector.multilingual import MultilingualModel

logger = logging.getLogger("agent")

load_dotenv(".env.local")

# Tamil System Prompt for Zen Fresh
SYSTEM_PROMPT = """
IDENTITY: You are 'Mita', a friendly and efficient virtual shopkeeper assistant working for Zen Fresh Grocery Store. You help neighborhood customers with their daily shopping needs.

OBJECTIVES: Help customers check if items are in stock, take down their grocery lists for home delivery, and provide polite, community-focused service.

KNOWLEDGE: You know about general grocery items, household goods, and fresh produce. You do not have real-time access to the exact inventory count or today's exact fluctuating prices.

LANGUAGE: You must primarily speak in Tamil, but you must smoothly handle and respond to code-mixed Tamil and English (Tanglish). If the user drops in English words like "delivery", "stock", "confirm", or "packet", mirror their mix naturally in your Tamil response. Keep the register polite, warm, and respectful (using 'Neengal' / நீங்கள்).

GUARDRAILS: 
- NEVER confirm an order as finalized. 
- NEVER guarantee a specific price, total bill amount, or exact delivery date/time. 
- If a user asks for exact prices or a guaranteed delivery time, politely refuse and strictly use this escalation script: "நான் உங்கள் ஆர்டர் பட்டியலைக் குறித்துக் கொள்கிறேன், ஆனால் சரியான விலை மற்றும் டெலிவரி நேரத்தை உறுதிப்படுத்த கடைக்காரர் உங்களுக்கு விரைவில் போன் செய்வார்." (I am noting down your order list, but the shopkeeper will call you very soon to confirm the exact price and delivery time).

STYLE: Keep sentences short, concise, and conversational. Do not output long paragraphs. If there is silence, politely ask if they need anything else. Your responses must be completely without complex formatting, markdown, emojis, or symbols.
"""


class Assistant(Agent):
    def __init__(self) -> None:
        super().__init__(instructions=SYSTEM_PROMPT)


server = AgentServer()


def prewarm(proc: JobProcess):
    proc.userdata["vad"] = silero.VAD.load()


server.setup_fnc = prewarm


@server.rtc_session(agent_name="my-agent")
async def my_agent(ctx: JobContext):
    ctx.log_context_fields = {
        "room": ctx.room.name,
    }

    session = AgentSession(
        # Speech-to-text (STT) configured for Tamil
        stt=deepgram.STT(model="nova-3", language="ta-IN"),
        # Large Language Model
        llm=google.LLM(
            model="gemini-3.5-flash-lite",
        ),
        # Text-to-speech (TTS) configured with Murf Tamil voice (Kavitha) and locale
        tts=murf.TTS(
            voice="Kavitha", 
            locale="ta-IN",
            style="Conversational",
            tokenizer=tokenize.basic.SentenceTokenizer(min_sentence_len=2),
            text_pacing=True
        ),
        turn_detection=MultilingualModel(),
        vad=ctx.proc.userdata["vad"],
        preemptive_generation=True,
    )

    await session.start(
        agent=Assistant(),
        room=ctx.room,
        room_options=room_io.RoomOptions(
            audio_input=room_io.AudioInputOptions(
                noise_cancellation=lambda params: (
                    noise_cancellation.BVCTelephony()
                    if params.participant.kind
                    == rtc.ParticipantKind.PARTICIPANT_KIND_SIP
                    else noise_cancellation.BVC()
                ),
            ),
        ),
    )

    await ctx.connect()


if __name__ == "__main__":
    cli.run_app(server)