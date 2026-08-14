import asyncio
import json
import logging
import secrets
import sqlite3
from datetime import datetime
from typing import Any

from dotenv import load_dotenv
from livekit import rtc
from livekit.agents import (
    Agent,
    AgentServer,
    AgentSession,
    ChatContext,
    JobContext,
    JobProcess,
    RunContext,
    cli,
    function_tool,
    room_io,
    tokenize,
)
from livekit.plugins import (
    deepgram,
    google,
    murf,
    noise_cancellation,
    silero,
)
from livekit.plugins.turn_detector.multilingual import MultilingualModel


# ============================================================
# LOGGING
# ============================================================

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
)

logger = logging.getLogger("zen-fresh-agent")

load_dotenv(".env.local")


# ============================================================
# VOICES
# ============================================================

MAIN_VOICE = "Anisha"

# Specialist voice.
# If your Murf account exposes a different exact voice name,
# change this value only.
SPECIALIST_VOICE = "Latika"


# ============================================================
# DATABASE
# ============================================================

DB_PATH = "home_fresh.db"


def init_db() -> None:
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()

    # --------------------------------------------------------
    # CUSTOMER MEMORY
    # --------------------------------------------------------

    cursor.execute(
        """
        CREATE TABLE IF NOT EXISTS customers (
            user_id TEXT PRIMARY KEY,
            name TEXT,
            past_orders TEXT,
            preferred_delivery_slot TEXT,
            last_interaction TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
        """
    )

    # --------------------------------------------------------
    # DAY 7 - HUMAN ESCALATION REQUESTS
    # --------------------------------------------------------

    cursor.execute(
        """
        CREATE TABLE IF NOT EXISTS escalation_requests (
            reference_id TEXT PRIMARY KEY,
            user_id TEXT NOT NULL,
            customer_name TEXT,
            reason TEXT NOT NULL,
            what_happened TEXT NOT NULL,
            what_was_checked TEXT NOT NULL,
            urgency TEXT NOT NULL,
            language TEXT NOT NULL,
            preferred_follow_up TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT 'open',
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
        """
    )

    # --------------------------------------------------------
    # DAY 8 - CALL ANALYTICS
    # --------------------------------------------------------

    cursor.execute(
        """
        CREATE TABLE IF NOT EXISTS call_analytics (
            call_id TEXT PRIMARY KEY,
            user_id TEXT NOT NULL,
            channel TEXT NOT NULL DEFAULT 'browser',
            started_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
            ended_at TIMESTAMP,
            outcome TEXT NOT NULL DEFAULT 'in_progress'
        )
        """
    )

    conn.commit()
    conn.close()


init_db()


# ============================================================
# STORE CATALOGUE
# ============================================================

CATALOG: dict[str, dict[str, Any]] = {
    "rice": {
        "id": "rice",
        "name": "Rice",
        "emoji": "🍚",
        "unit": "KG",
        "price": 325.0,
        "stock": 50.0,
        "aliases": [
            "rice",
            "arisi",
            "அரிசி",
            "பச்சை அரிசி",
        ],
    },
    "tomatoes": {
        "id": "tomatoes",
        "name": "Tomatoes",
        "emoji": "🍅",
        "unit": "KG",
        "price": 80.0,
        "stock": 25.0,
        "aliases": [
            "tomato",
            "tomatoes",
            "தக்காளி",
            "tamatar",
        ],
    },
    "milk": {
        "id": "milk",
        "name": "Milk",
        "emoji": "🥛",
        "unit": "L",
        "price": 65.0,
        "stock": 30.0,
        "aliases": [
            "milk",
            "பால்",
        ],
    },
    "butter": {
        "id": "butter",
        "name": "Butter",
        "emoji": "🧈",
        "unit": "PACKET",
        "price": 120.0,
        "stock": 20.0,
        "aliases": [
            "butter",
            "பட்டர்",
        ],
    },
    "bajji_mix": {
        "id": "bajji_mix",
        "name": "Bajji Mix",
        "emoji": "🥠",
        "unit": "PACKET",
        "price": 55.0,
        "stock": 18.0,
        "aliases": [
            "bajji",
            "bajji mix",
            "பஜ்ஜி",
        ],
    },
    "tea": {
        "id": "tea",
        "name": "Tea Powder",
        "emoji": "☕",
        "unit": "PACKET",
        "price": 180.0,
        "stock": 15.0,
        "aliases": [
            "tea",
            "tea powder",
            "chai",
            "டீ",
            "தேயிலை",
        ],
    },
    "biscuits": {
        "id": "biscuits",
        "name": "Biscuits",
        "emoji": "🍪",
        "unit": "PACKET",
        "price": 40.0,
        "stock": 35.0,
        "aliases": [
            "biscuit",
            "biscuits",
            "பிஸ்கட்",
        ],
    },
    "almond_milk": {
        "id": "almond_milk",
        "name": "Almond Milk",
        "emoji": "🥛",
        "unit": "L",
        "price": 180.0,
        "stock": 12.0,
        "aliases": [
            "almond milk",
            "almondmilk",
            "ஆல்மண்ட் மில்க்",
        ],
    },
}


# ============================================================
# HELPERS
# ============================================================

def normalize_text(value: str) -> str:
    return " ".join(value.lower().strip().split())


def find_product(item: str) -> dict[str, Any] | None:
    normalized = normalize_text(item)

    if normalized in CATALOG:
        return CATALOG[normalized]

    for product in CATALOG.values():
        for alias in product["aliases"]:
            if normalize_text(alias) in normalized:
                return product

    return None


def normalize_unit(unit: str | None, fallback: str) -> str:
    if not unit:
        return fallback

    value = normalize_text(unit)

    if value in {"kg", "kgs", "kilo", "kilos", "கிலோ"}:
        return "KG"

    if value in {"g", "gram", "grams", "கிராம்"}:
        return "G"

    if value in {
        "l",
        "litre",
        "litres",
        "liter",
        "liters",
        "லிட்டர்",
    }:
        return "L"

    if value in {
        "ml",
        "millilitre",
        "milliliter",
        "மில்லி",
    }:
        return "ML"

    if value in {
        "packet",
        "pack",
        "packets",
        "packs",
        "பாக்கெட்",
    }:
        return "PACKET"

    return value.upper()


def generate_reference_id() -> str:
    date_part = datetime.now().strftime("%Y%m%d")
    random_part = secrets.token_hex(3).upper()

    return f"HF-{date_part}-{random_part}"


def generate_call_id() -> str:
    date_part = datetime.now().strftime("%Y%m%d")
    random_part = secrets.token_hex(4).upper()

    return f"CALL-{date_part}-{random_part}"


# ============================================================
# DAY 8 - CALL ANALYTICS
# ============================================================

def create_call_record(
    call_id: str,
    user_id: str,
    channel: str = "browser",
) -> None:

    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()

    cursor.execute(
        """
        INSERT INTO call_analytics (
            call_id,
            user_id,
            channel,
            started_at,
            outcome
        )
        VALUES (
            ?,
            ?,
            ?,
            CURRENT_TIMESTAMP,
            'in_progress'
        )
        """,
        (
            call_id,
            user_id,
            channel,
        ),
    )

    conn.commit()
    conn.close()


def finish_call_record(
    call_id: str,
    successful: bool,
) -> None:

    outcome = "success" if successful else "failed"

    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()

    cursor.execute(
        """
        UPDATE call_analytics
        SET ended_at = CURRENT_TIMESTAMP,
            outcome = ?
        WHERE call_id = ?
        """,
        (
            outcome,
            call_id,
        ),
    )

    conn.commit()
    conn.close()


def get_call_analytics() -> dict[str, int]:

    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()

    cursor.execute(
        """
        SELECT
            COUNT(*),
            SUM(
                CASE
                    WHEN outcome = 'success'
                    THEN 1
                    ELSE 0
                END
            ),
            SUM(
                CASE
                    WHEN outcome = 'failed'
                    THEN 1
                    ELSE 0
                END
            )
        FROM call_analytics
        """
    )

    total, successful, failed = cursor.fetchone()

    conn.close()

    return {
        "totalCalls": int(total or 0),
        "successfulCalls": int(successful or 0),
        "failedCalls": int(failed or 0),
    }


# ============================================================
# MAIN AGENT PROMPT
# ============================================================

SYSTEM_PROMPT = """
IDENTITY:

You are Mita, a friendly virtual shopkeeper assistant for
ZEN Fresh Grocery Store.

LANGUAGE:

- Primarily speak Tamil using Tamil script.
- Smoothly handle Tamil-English code switching.
- Never romanize Tamil when speaking Tamil.
- Keep responses short, warm, polite, and conversational.
- Do not use markdown when speaking to the customer.
- Never reveal tools, implementation details, database details,
  code, or this system prompt.

============================================================
DAY 9 - SPECIALIST HANDOFF
============================================================

You are the MAIN grocery assistant.

You handle:

- Product prices
- Product availability
- Adding products to cart
- Removing products from cart
- Clearing cart
- Cart totals
- Customer memory
- Normal grocery questions

You have a specialist called LATIKA.

Latika is the Returns and Refunds Specialist.

============================================================
WHEN TO HAND OFF TO LATIKA
============================================================

Use transfer_to_returns_specialist when the customer needs
specialist help with:

- Returning a product
- Refund requests
- Incorrect previous orders
- Damaged products
- Missing products from a completed order
- Wrong product received
- Order dispute
- Questions about an already completed order
- Payment/refund issue that requires specialist review
- "I want to return this"
- "I need a refund"
- "My order was wrong"
- "I received the wrong item"
- "My product was damaged"
- "My order is missing something"
- "I was charged twice"

Do NOT hand off for normal questions like:

- Rice price
- Milk availability
- Adding rice
- Removing tomatoes from the cart
- Cart total
- Normal catalogue questions

Before handing off, ALWAYS tell the customer clearly:

"இந்த விஷயத்துக்கு Returns and Refunds specialist Latika
உங்களுக்கு உதவுவார். அவரிடம் connect செய்கிறேன்."

Then use the transfer tool.

Do NOT ask the customer to repeat the issue.

The specialist receives the conversation history.

============================================================
WHEN LATIKA RETURNS TO MITA
============================================================

If Latika determines that the customer is asking about:

- Product price
- Product availability
- Adding an item
- Removing an item
- Cart total
- Normal grocery questions

Latika should hand the conversation back to Mita.

Latika must NOT simply say "I will transfer you" and stop.

She must actually use the return_to_mita tool.

The customer should NOT have to repeat anything.

============================================================
CUSTOMER MEMORY
============================================================

1. At the start of a new conversation, use
   check_returning_customer.

2. If the customer is returning, greet them by name and mention
   their past order.

3. If the customer gives their name, order preferences, or
   delivery slot, ask permission before saving.

4. Only call save_customer_data when permission_granted is
   explicitly true.

5. If the customer asks to forget/delete their data, call
   delete_customer_data.

============================================================
CATALOGUE AND PRICE RULES
============================================================

1. The current catalogue is the ONLY source of truth.

2. NEVER invent prices.

3. Whenever giving a price, say it is according to the
   current catalogue.

4. If the customer asks availability or price, use
   check_product_availability.

5. If a product is not found, clearly say it is not currently
   listed in the catalogue.

6. If requested quantity is greater than stock, tell the actual
   available quantity.

============================================================
CART RULES
============================================================

1. If customer asks to add an item, use add_to_cart.

2. add_to_cart is authoritative.

3. Never say something was added unless the tool succeeds.

4. If quantity exceeds stock, do not add.

5. If customer asks to remove an item, use remove_from_cart.

6. If customer asks to clear cart, use clear_cart.

7. If customer asks for the total, ALWAYS call get_cart_total.

8. Never calculate cart total from conversation memory.

9. The total must include EVERY item currently in the cart.

============================================================
DAY 7 - HUMAN ESCALATION
============================================================

Human escalation is different from Day 9 specialist handoff.

Only create a human escalation when the issue cannot be
resolved by the available specialist workflow.

NEVER create an escalation without explicit customer consent.

First explain that human support is required.

Ask:

"இந்த issue-ஐ human support team பார்க்க வேண்டும்.
நான் இந்த பிரச்சனை மற்றும் நான் check செய்த தகவல்களை
support team-க்கு அனுப்பலாமா?"

WAIT for explicit YES.

Only then call create_escalation with
permission_granted=true.

Never treat silence as permission.

Never invent a reference ID.

============================================================
DAY 8 - SUCCESS
============================================================

A successful Local Commerce call is one where the customer
receives a valid product price/availability answer OR
successfully adds a product to the cart.

This is internal analytics only.

Do not tell the customer that the call is successful.
"""


# ============================================================
# LATIKA SPECIALIST PROMPT
# ============================================================

LATIKA_PROMPT = """
IDENTITY:

You are Latika, the Returns and Refunds Specialist for
ZEN Fresh Grocery Store.

You are taking over from Mita.

You are NOT the general grocery assistant.

Your job is ONLY to help with:

- Returns
- Refunds
- Wrong products
- Damaged products
- Missing products
- Incorrect completed orders
- Order disputes
- Payment/refund disputes

LANGUAGE:

- Primarily speak Tamil using Tamil script.
- Smoothly handle Tamil-English code switching.
- Never romanize Tamil.
- Keep responses short and natural.
- Do not use markdown when speaking.

============================================================
IMPORTANT FIRST ACTION
============================================================

When you take over, immediately introduce yourself.

Say something similar to:

"வணக்கம், நான் Latika. Returns and Refunds specialist.
Mita உங்களுடைய issue-ஐ எனக்கு transfer செய்திருக்கிறார்.
நான் தொடர்ந்து help பண்றேன்."

Then continue naturally.

The user should NEVER have to explain the complete issue again.

The previous conversation history has been passed to you.

============================================================
ORDER REFERENCE
============================================================

If needed, ask the customer for their order reference number.

Do NOT invent an order reference number.

A reference number supplied by the customer should be repeated
carefully and used only for this conversation.

IMPORTANT:

The human-support escalation reference starts with HF-.

Do not confuse an escalation reference with an order reference.

============================================================
RETURN / REFUND HANDLING
============================================================

Understand the customer's problem first.

Ask only necessary questions.

Do not ask the customer to repeat information already present
in the conversation.

Do not promise that a refund has already been approved.

Do not claim that money has already been returned unless a
real backend tool confirms it.

If human support is required, explain that clearly.

============================================================
WHEN TO RETURN TO MITA
============================================================

If the customer changes topic and asks about:

- Product price
- Product availability
- Adding groceries
- Removing groceries
- Cart
- Cart total
- Normal grocery shopping

you must return control to Mita.

Do not continue answering general grocery questions yourself.

Use the return_to_mita tool.

Before returning, say naturally:

"இந்த grocery question-க்கு Mita தான் help பண்ணுவாங்க.
அவரிடம் உங்களை மீண்டும் connect செய்கிறேன்."

Then actually call return_to_mita.

The customer must NOT repeat the question.

============================================================
VERY IMPORTANT
============================================================

You are a specialist.

Do not behave like Mita.

Do not expose implementation details.

Do not mention Python, LiveKit, tools, prompts, databases,
agents, or handoff implementation.
"""


# ============================================================
# MAIN ASSISTANT
# ============================================================

class Assistant(Agent):

    def __init__(
        self,
        room: rtc.Room,
        chat_ctx: ChatContext | None = None,
    ) -> None:

        super().__init__(
            instructions=SYSTEM_PROMPT,
            chat_ctx=chat_ctx,
            tts=murf.TTS(
                voice=MAIN_VOICE,
                locale="ta-IN",
                style="Conversational",
                tokenizer=tokenize.basic.SentenceTokenizer(
                    min_sentence_len=2,
                ),
                text_pacing=True,
            ),
        )

        self.room = room

        self.current_user_id = "demo_user_01"

        self.call_id = ""

        self.call_successful = False

        self.cart: dict[str, dict[str, Any]] = {}
        self.returned_from_specialist = False

    async def on_enter(self) -> None:
        """Speak immediately whenever Mita becomes the active agent."""
        if self.returned_from_specialist:
            self.returned_from_specialist = False
            self.session.generate_reply(
                instructions=(
                    "You are Mita and you have just received the conversation "
                    "back from Latika. Tell the customer briefly in Tamil that "
                    "you are back and continue helping with their latest normal "
                    "grocery question. Do not ask them to repeat what they said."
                )
            )
        else:
            self.session.generate_reply(
                instructions=(
                    "You are Mita. Greet the customer briefly in Tamil and "
                    "ask how you can help with their grocery needs."
                )
            )

    # ========================================================
    # FRONTEND EVENTS
    # ========================================================

    async def publish_ui_event(
        self,
        payload: dict[str, Any],
    ) -> None:

        encoded = json.dumps(
            payload,
            ensure_ascii=False,
        ).encode("utf-8")

        await self.room.local_participant.publish_data(
            encoded,
            reliable=True,
            topic="ui-events",
        )

    async def publish_cart_sync(self) -> None:

        await self.publish_ui_event(
            {
                "action": "cart",
                "operation": "sync",
                "items": list(self.cart.values()),
            }
        )

    # ========================================================
    # DAY 8 SUCCESS
    # ========================================================

    def mark_call_successful(self) -> None:

        if self.call_successful:
            return

        self.call_successful = True

        logger.info(
            "DAY 8 | CALL SUCCESS | call_id=%s",
            self.call_id,
        )

        try:
            asyncio.create_task(
                self.publish_ui_event(
                    {
                        "action": "call_analytics",
                        "event": "success_reached",
                        "callId": self.call_id,
                    }
                )
            )

        except RuntimeError:
            logger.exception(
                "DAY 8 | Could not publish success event"
            )

    # ========================================================
    # DAY 9 - HANDOFF TO LATIKA
    # ========================================================

    @function_tool
    async def transfer_to_returns_specialist(
        self,
        context: RunContext,
        reason: str,
    ):
        """
        Transfer the conversation to Latika, the Returns and
        Refunds Specialist.

        Use this ONLY when the customer needs specialist help
        with returns, refunds, damaged products, wrong products,
        missing products, completed-order disputes, or payment/
        refund issues.

        Before calling this tool, tell the customer that you are
        connecting them to Latika and that they will not need to
        repeat their problem.
        """

        logger.info(
            "DAY 9 | MITA -> LATIKA | reason=%s",
            reason,
        )

        # ----------------------------------------------------
        # IMPORTANT:
        # Preserve the conversation history.
        # Exclude Mita's system instructions so Latika gets only
        # the conversation context.
        # ----------------------------------------------------

        chat_ctx = self.chat_ctx.copy(
            exclude_instructions=True
        )

        specialist = ReturnsRefundsAgent(
            room=self.room,
            chat_ctx=chat_ctx,
            main_agent=self,
        )

        # Python LiveKit handoffs are performed by returning the
        # next Agent (optionally with a tool-result string).
        # Do NOT use llm.handoff() here; that helper is for the
        # JavaScript API.
        return (
            specialist,
            "Transferring the conversation to Latika, the Returns "
            "and Refunds Specialist."
        )

    # ========================================================
    # CUSTOMER MEMORY
    # ========================================================

    @function_tool
    async def check_returning_customer(
        self,
        context: RunContext,
    ) -> str:

        conn = sqlite3.connect(DB_PATH)
        cursor = conn.cursor()

        cursor.execute(
            """
            SELECT
                name,
                past_orders,
                preferred_delivery_slot
            FROM customers
            WHERE user_id = ?
            """,
            (self.current_user_id,),
        )

        result = cursor.fetchone()

        conn.close()

        if result:
            return (
                f"Customer found. Name: {result[0]}, "
                f"Past Orders: {result[1]}, "
                f"Delivery Slot: {result[2]}. "
                "Greet them by name and ask if they want "
                "to repeat their past order."
            )

        return (
            "New customer found. "
            "Introduce yourself and ask how you can help."
        )

    @function_tool
    async def save_customer_data(
        self,
        context: RunContext,
        name: str,
        past_orders: str,
        delivery_slot: str,
        permission_granted: bool,
    ) -> str:

        if not permission_granted:
            return (
                "Do not save the data. "
                "Tell the customer you respect their privacy."
            )

        conn = sqlite3.connect(DB_PATH)
        cursor = conn.cursor()

        cursor.execute(
            """
            INSERT INTO customers (
                user_id,
                name,
                past_orders,
                preferred_delivery_slot
            )
            VALUES (?, ?, ?, ?)

            ON CONFLICT(user_id) DO UPDATE SET
                name = excluded.name,
                past_orders = excluded.past_orders,
                preferred_delivery_slot =
                    excluded.preferred_delivery_slot,
                last_interaction = CURRENT_TIMESTAMP
            """,
            (
                self.current_user_id,
                name,
                past_orders,
                delivery_slot,
            ),
        )

        conn.commit()
        conn.close()

        await self.publish_ui_event(
            {
                "action": "toast",
                "message": "💾 Preferences Saved",
                "type": "success",
            }
        )

        return (
            "Customer data successfully saved. "
            "Tell the customer you will remember them next time."
        )

    @function_tool
    async def delete_customer_data(
        self,
        context: RunContext,
    ) -> str:

        conn = sqlite3.connect(DB_PATH)
        cursor = conn.cursor()

        cursor.execute(
            "DELETE FROM customers WHERE user_id = ?",
            (self.current_user_id,),
        )

        conn.commit()
        conn.close()

        await self.publish_ui_event(
            {
                "action": "toast",
                "message": "🗑️ Customer Data Erased",
                "type": "error",
            }
        )

        return (
            "Customer data successfully deleted. "
            "Confirm that the saved preferences have been erased."
        )

    # ========================================================
    # DAY 7 - HUMAN ESCALATION
    # ========================================================

    @function_tool
    async def create_escalation(
        self,
        context: RunContext,
        reason: str,
        what_happened: str,
        what_was_checked: str,
        urgency: str = "high",
        preferred_follow_up: str =
            "Current conversation / voice follow-up",
        permission_granted: bool = False,
    ) -> str:

        if permission_granted is not True:

            logger.warning(
                "ESCALATION BLOCKED: customer permission "
                "was not granted."
            )

            return (
                "No human support request was created because "
                "the customer did not grant permission."
            )

        allowed_urgency = {
            "low",
            "medium",
            "high",
            "emergency",
        }

        urgency_normalized = normalize_text(urgency)

        if urgency_normalized not in allowed_urgency:
            urgency_normalized = "high"

        customer_name = "Customer"

        conn = sqlite3.connect(DB_PATH)
        cursor = conn.cursor()

        cursor.execute(
            """
            SELECT name
            FROM customers
            WHERE user_id = ?
            """,
            (self.current_user_id,),
        )

        result = cursor.fetchone()

        if result and result[0]:
            customer_name = result[0]

        conn.close()

        reference_id = generate_reference_id()

        clean_reason = reason.strip()

        clean_what_happened = (
            what_happened.strip()
        )

        clean_what_was_checked = (
            what_was_checked.strip()
        )

        clean_follow_up = (
            preferred_follow_up.strip()
            if preferred_follow_up
            else "Current conversation / voice follow-up"
        )

        language = "Tamil (ta-IN)"

        conn = sqlite3.connect(DB_PATH)
        cursor = conn.cursor()

        cursor.execute(
            """
            INSERT INTO escalation_requests (
                reference_id,
                user_id,
                customer_name,
                reason,
                what_happened,
                what_was_checked,
                urgency,
                language,
                preferred_follow_up,
                status
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                reference_id,
                self.current_user_id,
                customer_name,
                clean_reason,
                clean_what_happened,
                clean_what_was_checked,
                urgency_normalized,
                language,
                clean_follow_up,
                "open",
            ),
        )

        conn.commit()
        conn.close()

        logger.warning("")
        logger.warning("=" * 72)
        logger.warning("🚨 HUMAN HELP REQUESTED 🚨")
        logger.warning("=" * 72)
        logger.warning(
            "REFERENCE ID      : %s",
            reference_id,
        )
        logger.warning(
            "CUSTOMER          : %s",
            customer_name,
        )
        logger.warning(
            "USER ID           : %s",
            self.current_user_id,
        )
        logger.warning(
            "REASON            : %s",
            clean_reason,
        )
        logger.warning(
            "WHAT HAPPENED     : %s",
            clean_what_happened,
        )
        logger.warning(
            "WHAT WAS CHECKED  : %s",
            clean_what_was_checked,
        )
        logger.warning(
            "URGENCY           : %s",
            urgency_normalized.upper(),
        )
        logger.warning(
            "LANGUAGE          : %s",
            language,
        )
        logger.warning(
            "FOLLOW-UP         : %s",
            clean_follow_up,
        )
        logger.warning("STATUS            : OPEN")
        logger.warning("=" * 72)

        await self.publish_ui_event(
            {
                "action": "human_help",
                "operation": "created",
                "referenceId": reference_id,
                "status": "open",
                "urgency": urgency_normalized,
                "reason": clean_reason,
            }
        )

        return (
            f"Human support request created successfully. "
            f"Reference ID: {reference_id}. "
            "Tell the customer the reference ID and explain "
            "that human support will review the request."
        )

    # ========================================================
    # PRODUCT AVAILABILITY
    # ========================================================

    @function_tool
    async def check_product_availability(
        self,
        context: RunContext,
        item: str,
        quantity: float = 1,
        unit: str = "",
    ) -> str:

        product = find_product(item)

        if not product:
            return (
                f"Product '{item}' was not found in the current "
                "catalogue. It is not currently available."
            )

        requested_unit = normalize_unit(
            unit,
            product["unit"],
        )

        if requested_unit != product["unit"]:

            return (
                f"{product['name']} is sold in "
                f"{product['unit']} units. "
                f"Current catalogue stock is "
                f"{product['stock']:g} {product['unit']}. "
                f"The catalogue price is "
                f"₹{product['price']:.2f} per "
                f"{product['unit']}."
            )

        if product["stock"] <= 0:

            return (
                f"{product['name']} is currently out of stock "
                "according to the current catalogue. "
                f"The catalogue price is "
                f"₹{product['price']:.2f} per "
                f"{product['unit']}."
            )

        requested_total = product["price"] * quantity

        if quantity > product["stock"]:

            return (
                f"{product['name']} has only "
                f"{product['stock']:g} {product['unit']} "
                "available according to the current catalogue. "
                f"The catalogue price is "
                f"₹{product['price']:.2f} per "
                f"{product['unit']}."
            )

        self.mark_call_successful()

        return (
            f"{product['name']} is available. "
            f"According to the current catalogue, "
            f"there are {product['stock']:g} "
            f"{product['unit']} in stock. "
            f"The catalogue price is "
            f"₹{product['price']:.2f} per "
            f"{product['unit']}. "
            f"For {quantity:g} {product['unit']}, "
            f"the catalogue price total is "
            f"₹{requested_total:.2f}."
        )

    # ========================================================
    # ADD TO CART
    # ========================================================

    @function_tool
    async def add_to_cart(
        self,
        context: RunContext,
        item: str,
        quantity: float,
        unit: str = "",
    ) -> str:

        product = find_product(item)

        if not product:
            return (
                f"I could not find {item} in the current "
                "catalogue, so nothing was added."
            )

        requested_unit = normalize_unit(
            unit,
            product["unit"],
        )

        if requested_unit != product["unit"]:

            return (
                f"{product['name']} must be added in "
                f"{product['unit']} units. "
                "Nothing was added."
            )

        if quantity <= 0:
            return (
                "Quantity must be greater than zero. "
                "Nothing was added."
            )

        if quantity > product["stock"]:

            return (
                f"Only {product['stock']:g} "
                f"{product['unit']} of {product['name']} "
                "is currently available. "
                "Nothing was added."
            )

        line_total = product["price"] * quantity

        cart_item = {
            "id": product["id"],
            "name": product["name"],
            "quantity": float(quantity),
            "unit": product["unit"],
            "emoji": product["emoji"],
            "unitPrice": float(product["price"]),
            "lineTotal": float(line_total),
            "priceSource": "Current catalogue",
        }

        self.cart[product["id"]] = cart_item

        self.mark_call_successful()

        await self.publish_ui_event(
            {
                "action": "cart",
                "operation": "add",
                "item": cart_item,
                "message": (
                    f"{product['name']} added to cart."
                ),
            }
        )

        return (
            f"{quantity:g} {product['unit']} "
            f"{product['name']} has been added successfully. "
            f"According to the current catalogue, "
            f"the price is ₹{product['price']:.2f} "
            f"per {product['unit']}, "
            f"so this item costs ₹{line_total:.2f}."
        )

    # ========================================================
    # REMOVE FROM CART
    # ========================================================

    @function_tool
    async def remove_from_cart(
        self,
        context: RunContext,
        item: str,
    ) -> str:

        product = find_product(item)

        if not product:
            return (
                f"I could not find {item} in the current catalogue."
            )

        if product["id"] not in self.cart:

            return (
                f"{product['name']} is not currently "
                "in your cart."
            )

        removed_item = self.cart[product["id"]]

        del self.cart[product["id"]]

        await self.publish_ui_event(
            {
                "action": "cart",
                "operation": "remove",
                "itemId": product["id"],
                "message": (
                    f"{product['name']} removed from cart."
                ),
            }
        )

        return (
            f"{product['name']} has been removed "
            "from your cart."
        )

    # ========================================================
    # CLEAR CART
    # ========================================================

    @function_tool
    async def clear_cart(
        self,
        context: RunContext,
    ) -> str:

        self.cart.clear()

        await self.publish_ui_event(
            {
                "action": "cart",
                "operation": "clear",
            }
        )

        return "Your cart has been cleared."

    # ========================================================
    # CART TOTAL
    # ========================================================

    @function_tool
    async def get_cart_total(
        self,
        context: RunContext,
    ) -> str:

        if not self.cart:

            return (
                "The cart is currently empty. "
                "The total is ₹0."
            )

        total = 0.0
        item_lines = []

        for cart_item in self.cart.values():

            product = CATALOG.get(cart_item["id"])

            if not product:
                continue

            quantity = float(
                cart_item["quantity"]
            )

            unit_price = float(
                product["price"]
            )

            line_total = (
                quantity * unit_price
            )

            cart_item["unitPrice"] = unit_price
            cart_item["lineTotal"] = line_total
            cart_item["priceSource"] = (
                "Current catalogue"
            )

            total += line_total

            item_lines.append(
                f"{product['name']}: "
                f"{quantity:g} {product['unit']} × "
                f"₹{unit_price:.2f} = "
                f"₹{line_total:.2f}"
            )

        await self.publish_cart_sync()

        if not item_lines:

            return (
                "The cart is empty. "
                "The total is ₹0."
            )

        details = "; ".join(item_lines)

        return (
            "Here is the complete cart total. "
            "All prices are according to the current "
            "catalogue. "
            f"{details}. "
            f"Complete cart total: ₹{total:.2f}."
        )


# ============================================================
# LATIKA SPECIALIST AGENT
# ============================================================

class ReturnsRefundsAgent(Agent):

    def __init__(
        self,
        room: rtc.Room,
        chat_ctx: ChatContext | None = None,
        main_agent: Assistant | None = None,
    ) -> None:

        super().__init__(
            instructions=LATIKA_PROMPT,
            chat_ctx=chat_ctx,

            # =================================================
            # THIS IS THE IMPORTANT FIX FOR LATIKA NOT SPEAKING
            # =================================================

            tts=murf.TTS(
                voice=SPECIALIST_VOICE,
                locale="ta-IN",
                style="Conversational",
                tokenizer=tokenize.basic.SentenceTokenizer(
                    min_sentence_len=2,
                ),
                text_pacing=True,
            ),
        )

        self.room = room
        self.main_agent = main_agent

    # ========================================================
    # LATIKA ENTERS
    # ========================================================

    async def on_enter(self) -> None:

        logger.info(
            "DAY 9 | LATIKA ENTERED | voice=%s",
            SPECIALIST_VOICE,
        )

        # Force Latika to generate an introductory response
        # immediately after takeover.
        # Do not await this generation: on_enter can run as part of
        # the handoff tool, and awaiting playout there can deadlock.
        self.session.generate_reply(
            instructions=(
                "You have just taken over the conversation "
                "from Mita. Introduce yourself as Latika, "
                "the Returns and Refunds Specialist. "
                "Acknowledge that Mita already transferred "
                "the customer's issue to you. "
                "Do NOT ask the customer to repeat the full "
                "problem. Continue naturally from the "
                "conversation history."
            )
        )

    # ========================================================
    # LATIKA -> MITA
    # ========================================================

    @function_tool
    async def return_to_mita(
        self,
        context: RunContext,
        reason: str,
    ):
        """
        Return the conversation to Mita when the customer
        changes from a return/refund issue to a normal grocery
        question.
        """

        logger.info(
            "DAY 9 | LATIKA -> MITA | reason=%s",
            reason,
        )

        # ----------------------------------------------------
        # Preserve the entire conversation.
        # ----------------------------------------------------

        chat_ctx = self.chat_ctx.copy(
            exclude_instructions=True
        )

        # Reuse the original Mita instance so the session cart,
        # call analytics state, and customer state are preserved.
        # Creating a brand-new Assistant here would create a fresh
        # empty cart and could make it look like Mita did not return.
        mita = self.main_agent

        if mita is None:
            # Defensive fallback; this should not happen during a
            # normal Mita -> Latika handoff.
            mita = Assistant(
                room=self.room,
                chat_ctx=chat_ctx,
            )

        mita.returned_from_specialist = True

        return (
            mita,
            "Transferring the conversation back to Mita, the main "
            "grocery assistant."
        )


# ============================================================
# SERVER
# ============================================================

server = AgentServer()


# ============================================================
# PREWARM
# ============================================================

def prewarm(proc: JobProcess) -> None:

    proc.userdata["vad"] = silero.VAD.load()


server.setup_fnc = prewarm


# ============================================================
# LIVEKIT SESSION
# ============================================================

@server.rtc_session(agent_name="my-agent")
async def my_agent(ctx: JobContext) -> None:

    ctx.log_context_fields = {
        "room": ctx.room.name,
    }

    # ========================================================
    # DAY 8 - START ANALYTICS
    # ========================================================

    call_id = generate_call_id()

    create_call_record(
        call_id=call_id,
        user_id="demo_user_01",
        channel="browser",
    )

    logger.info(
        "DAY 8 | CALL STARTED | call_id=%s",
        call_id,
    )

    # ========================================================
    # SESSION
    # ========================================================

    session = AgentSession(

        stt=deepgram.STT(
            model="nova-3",
            language="ta-IN",
        ),

        llm=google.LLM(
            model="gemini-3.1-flash-lite",
        ),

        # ====================================================
        # DEFAULT TTS = MITA
        # ====================================================

        tts=murf.TTS(
            voice=MAIN_VOICE,
            locale="ta-IN",
            style="Conversational",
            tokenizer=tokenize.basic.SentenceTokenizer(
                min_sentence_len=2,
            ),
            text_pacing=True,
        ),

        turn_detection=MultilingualModel(),

        vad=ctx.proc.userdata["vad"],

        preemptive_generation={
            "enabled": True,
            "preemptive_tts": True,
            "max_speech_duration": 8.0,
            "max_retries": 2,
        },

        min_endpointing_delay=0.45,

        max_endpointing_delay=1.5,

        min_interruption_duration=0.45,

        min_interruption_words=2,

        resume_false_interruption=True,
    )

    # ========================================================
    # INITIAL MITA
    # ========================================================

    assistant = Assistant(
        room=ctx.room,
    )

    assistant.call_id = call_id

    # ========================================================
    # FINALIZATION
    # ========================================================

    call_finalized = False

    async def publish_analytics(
        event_name: str,
    ) -> None:

        counts = get_call_analytics()

        try:

            await assistant.publish_ui_event(
                {
                    "action": "call_analytics",
                    "event": event_name,
                    "callId": assistant.call_id,
                    **counts,
                }
            )

        except Exception:

            logger.exception(
                "DAY 8 | Failed to publish analytics"
            )

    async def finalize_call() -> None:

        nonlocal call_finalized

        if call_finalized:
            return

        call_finalized = True

        try:

            finish_call_record(
                call_id=assistant.call_id,
                successful=assistant.call_successful,
            )

            outcome = (
                "success"
                if assistant.call_successful
                else "failed"
            )

            logger.info(
                "DAY 8 | CALL FINISHED | call_id=%s | outcome=%s",
                assistant.call_id,
                outcome,
            )

            await publish_analytics(
                "call_finished"
            )

        except Exception:

            logger.exception(
                "DAY 8 | Failed to finalize call"
            )

    # ========================================================
    # CALL CONTROL
    # ========================================================

    @ctx.room.on("data_received")
    def on_data_received(
        packet: rtc.DataPacket,
    ) -> None:

        if packet.topic != "call-control":
            return

        if packet.participant is None:
            return

        try:

            message = json.loads(
                packet.data.decode("utf-8")
            )

        except (
            json.JSONDecodeError,
            UnicodeDecodeError,
        ):

            return

        action = message.get("action")

        message_call_id = message.get(
            "callId"
        )

        if message_call_id != assistant.call_id:

            logger.warning(
                "DAY 8 | Wrong callId | received=%s expected=%s",
                message_call_id,
                assistant.call_id,
            )

            return

        if action != "end_call":
            return

        logger.info(
            "DAY 8 | END REQUEST RECEIVED | call_id=%s",
            assistant.call_id,
        )

        asyncio.create_task(
            finalize_call()
        )

    # ========================================================
    # SESSION CLOSE
    # ========================================================

    @session.on("close")
    def on_session_close(
        _event: Any,
    ) -> None:

        asyncio.create_task(
            finalize_call()
        )

    # ========================================================
    # START SESSION
    # ========================================================

    await session.start(

        agent=assistant,

        room=ctx.room,

        room_options=room_io.RoomOptions(

            audio_input=room_io.AudioInputOptions(
                noise_cancellation=noise_cancellation.BVC(),
            ),

            text_output=room_io.TextOutputOptions(
                sync_transcription=True,
            ),
        ),
    )

    # ========================================================
    # CONNECT
    # ========================================================

    await ctx.connect()

    # ========================================================
    # INITIAL ANALYTICS
    # ========================================================

    await publish_analytics(
        "call_started"
    )

    # ========================================================
    # INITIAL CART
    # ========================================================

    await assistant.publish_cart_sync()

    # ========================================================
    # SHUTDOWN FALLBACK
    # ========================================================

    async def shutdown_fallback(
        _reason: str = "",
    ) -> None:

        await finalize_call()

    ctx.add_shutdown_callback(
        shutdown_fallback
    )


# ============================================================
# MAIN
# ============================================================

if __name__ == "__main__":
    cli.run_app(server)