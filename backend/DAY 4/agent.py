import logging
import sqlite3
import json

from dotenv import load_dotenv
from livekit import rtc
from livekit.agents import (
    Agent,
    AgentServer,
    AgentSession,
    JobContext,
    JobProcess,
    cli,
    tokenize,
    room_io,
    function_tool,
    RunContext
)
from livekit.plugins import murf, silero, google, deepgram, noise_cancellation
from livekit.plugins.turn_detector.multilingual import MultilingualModel

logger = logging.getLogger("agent")

load_dotenv(".env.local")


# --- SQLITE DATABASE SETUP ---

def init_db():
    conn = sqlite3.connect('home_fresh.db')
    c = conn.cursor()

    c.execute('''
        CREATE TABLE IF NOT EXISTS customers (
            user_id TEXT PRIMARY KEY,
            name TEXT,
            past_orders TEXT,
            preferred_delivery_slot TEXT,
            last_interaction TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    ''')

    conn.commit()
    conn.close()


init_db()


# ============================================================
# SYSTEM PROMPT
# ============================================================

SYSTEM_PROMPT = """
IDENTITY: You are 'Mita', a friendly virtual shopkeeper assistant for ZEN Fresh Grocery Store.

OBJECTIVES:

1. Always start by using the `check_returning_customer` tool to see if you know the caller's preferences.

2. If they are returning, greet them warmly by their name and mention their past order.

Example:
"வணக்கம் [Name], கடந்த முறை நீங்கள் [Items] ஆர்டர் செய்திருந்தீர்கள். இந்த முறையும் அவை வேண்டுமா?"

3. If they are new, introduce yourself and ask what they need.

4. If a user tells you their name, order preferences, or delivery slot,
YOU MUST ASK FOR PERMISSION TO SAVE IT.

Example:
"அடுத்த முறை உங்களுக்கு எளிதாக உதவ உங்கள் பெயர் மற்றும் ஆர்டர் விவரங்களை சேமித்து வைக்கலாமா?"

5. ONLY if they explicitly say yes, use the `save_customer_data` tool to remember them.

6. If a user asks you to forget them, delete their data, or erase their memory,
use the `delete_customer_data` tool immediately.


KNOWLEDGE & GUARDRAILS:

- NEVER confirm an order as finalized.
- NEVER guarantee a specific price.

- If asked for exact prices, politely refuse and say exactly:

"நான் உங்கள் ஆர்டர் பட்டியலை குறித்து வைத்துக்கொள்கிறேன், ஆனால் சரியான விலை மற்றும் டெலிவரி நேரத்தை உறுதிப்படுத்த கடைக்காரர் உங்களுக்கு விரைவில் போன் செய்து கான்ஃபர்ம் செய்வார்."


LANGUAGE & SCRIPT (STRICT):

- You must primarily speak in Tamil.
- You must smoothly handle code-mixed Tamil and English.

- Always write Tamil using Tamil script.

Correct:
"வணக்கம், எப்படி உதவலாம்?"

Incorrect:
"Vanakkam, eppadi udhavalaam?"

- Do NOT romanize Tamil.

- If the user drops in English words such as:
"delivery", "order", "stock", "packet", "confirm"

you may naturally mirror those English words in your Tamil response.

Example:
"சரி, 2 packet milk உங்கள் order list-ல சேர்த்துக்கொள்கிறேன்."

- Keep the register polite, warm, and respectful.

Use:
"நீங்கள்"
"உங்களுக்கு"
"உங்கள்"


STYLE:

Keep sentences short, concise, and conversational.

Do not output long paragraphs.

Do not use markdown.

Do not reveal internal tools, database information, or implementation details.
"""


# ============================================================
# ASSISTANT
# ============================================================

class Assistant(Agent):

    def __init__(self, room: rtc.Room) -> None:

        super().__init__(
            instructions=SYSTEM_PROMPT
        )

        self.room = room

        # Current demo user ID
        self.current_user_id = "demo_user_01"


    # ========================================================
    # CHECK RETURNING CUSTOMER
    # ========================================================

    @function_tool
    async def check_returning_customer(
        self,
        context: RunContext
    ):
        """
        Use this tool immediately at the start of the conversation
        to see if the caller has a saved profile.
        """

        logger.info(
            f"Checking DB for user: {self.current_user_id}"
        )

        conn = sqlite3.connect('home_fresh.db')

        c = conn.cursor()

        c.execute(
            """
            SELECT
                name,
                past_orders,
                preferred_delivery_slot
            FROM customers
            WHERE user_id=?
            """,
            (self.current_user_id,)
        )

        result = c.fetchone()

        conn.close()


        if result:

            return (
                f"Customer found. "
                f"Name: {result[0]}, "
                f"Past Orders: {result[1]}, "
                f"Delivery Slot: {result[2]}. "
                f"Greet them by name and ask if they want "
                f"to repeat their past order."
            )


        return (
            "New customer found. "
            "Introduce yourself and ask how you can help."
        )


    # ========================================================
    # SAVE CUSTOMER DATA
    # ========================================================

    @function_tool
    async def save_customer_data(
        self,
        context: RunContext,
        name: str,
        past_orders: str,
        delivery_slot: str,
        permission_granted: bool
    ):
        """
        Save customer details.

        ONLY call this tool if permission_granted is True.
        """

        if not permission_granted:

            logger.info(
                "Permission denied by user. Not saving to DB."
            )

            return (
                "Do not save. "
                "Acknowledge that you respect their privacy "
                "and won't save the data."
            )


        logger.info(
            f"Saving data for {name} to DB."
        )


        conn = sqlite3.connect('home_fresh.db')

        c = conn.cursor()


        c.execute(
            '''
            INSERT INTO customers (
                user_id,
                name,
                past_orders,
                preferred_delivery_slot
            )
            VALUES (?, ?, ?, ?)

            ON CONFLICT(user_id) DO UPDATE SET
                name=excluded.name,
                past_orders=excluded.past_orders,
                preferred_delivery_slot=excluded.preferred_delivery_slot,
                last_interaction=CURRENT_TIMESTAMP
            ''',
            (
                self.current_user_id,
                name,
                past_orders,
                delivery_slot
            )
        )


        conn.commit()

        conn.close()


        # ====================================================
        # SIGNAL TO REACT FRONTEND
        # ====================================================

        payload = json.dumps({
            "action": "toast",
            "message": "💾 Preferences Saved",
            "type": "success"
        }).encode('utf-8')


        await self.room.local_participant.publish_data(
            payload,
            reliable=True,
            topic="ui-events"
        )


        return (
            "Customer data successfully saved. "
            "Tell the user you will remember them for next time."
        )


    # ========================================================
    # DELETE CUSTOMER DATA
    # ========================================================

    @function_tool
    async def delete_customer_data(
        self,
        context: RunContext
    ):
        """
        Use this tool if the customer asks you to forget them,
        delete their data, or erase their memory.
        """

        logger.info(
            f"Deleting data for user: {self.current_user_id}"
        )


        conn = sqlite3.connect('home_fresh.db')

        c = conn.cursor()


        c.execute(
            "DELETE FROM customers WHERE user_id=?",
            (self.current_user_id,)
        )


        conn.commit()

        conn.close()


        # ====================================================
        # SIGNAL TO REACT FRONTEND
        # ====================================================

        payload = json.dumps({
            "action": "toast",
            "message": "🗑️ Customer Data Erased",
            "type": "error"
        }).encode('utf-8')


        await self.room.local_participant.publish_data(
            payload,
            reliable=True,
            topic="ui-events"
        )


        return (
            "Data successfully deleted. "
            "Confirm to the user that all their saved preferences "
            "have been erased from the system."
        )


# ============================================================
# LIVEKIT SERVER
# ============================================================

server = AgentServer()


# ============================================================
# PREWARM
# ============================================================

def prewarm(proc: JobProcess):

    proc.userdata["vad"] = silero.VAD.load()


server.setup_fnc = prewarm


# ============================================================
# LIVEKIT SESSION
# ============================================================

@server.rtc_session(agent_name="my-agent")
async def my_agent(ctx: JobContext):

    ctx.log_context_fields = {
        "room": ctx.room.name
    }


    session = AgentSession(

        # ====================================================
        # DEEPGRAM STT - TAMIL
        # ====================================================

        stt=deepgram.STT(
            model="nova-3",
            language="ta-IN"
        ),


        # ====================================================
        # GOOGLE GEMINI
        # ====================================================

        llm=google.LLM(
            model="gemini-3.5-flash-lite"
        ),


        # ====================================================
        # MURF TTS - ANISHA - TAMIL INDIA
        # ====================================================

        tts=murf.TTS(
            voice="Anisha",
            locale="ta-IN",
            style="Conversational",
            tokenizer=tokenize.basic.SentenceTokenizer(
                min_sentence_len=2
            ),
            text_pacing=True
        ),


        # ====================================================
        # TURN DETECTION
        # ====================================================

        turn_detection=MultilingualModel(),


        # ====================================================
        # VAD
        # ====================================================

        vad=ctx.proc.userdata["vad"],


        # ====================================================
        # PREEMPTIVE GENERATION
        # ====================================================

        preemptive_generation=True,
    )


    # ========================================================
    # START SESSION
    # ========================================================

    await session.start(

        agent=Assistant(
            room=ctx.room
        ),

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


# ============================================================
# MAIN
# ============================================================

if __name__ == "__main__":

    cli.run_app(server)