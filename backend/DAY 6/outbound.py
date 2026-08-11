import argparse
import asyncio
import json
import os
import random
import re
import sys
import uuid

from dotenv import load_dotenv
from livekit import api

load_dotenv(".env.local")

AGENT_NAME = "my-agent"

async def dial(phone_number: str, room_name: str) -> None:
    """Create the room, dispatch the agent, and trigger the SIP call to Linphone."""
    lk = api.LiveKitAPI()
    try:
        # 1. Create the room
        await lk.room.create_room(api.CreateRoomRequest(name=room_name))

        # 2. Dispatch Mita into the room
        print(f"Dispatching '{AGENT_NAME}' to room '{room_name}'...")
        await lk.agent_dispatch.create_dispatch(
            api.CreateAgentDispatchRequest(
                agent_name=AGENT_NAME,
                room=room_name,
                metadata=json.dumps({"phone_number": phone_number}),
            )
        )

        # 3. Actually ring the Linphone app via SIP Trunk
        # Make sure your .env.local has LIVEKIT_SIP_OUTBOUND_TRUNK_ID set!
        SIP_TRUNK_ID = os.getenv("LIVEKIT_SIP_OUTBOUND_TRUNK_ID")
        if not SIP_TRUNK_ID:
            print("Error: LIVEKIT_SIP_OUTBOUND_TRUNK_ID is missing from .env.local!")
            return

        print(f"Dialing {phone_number} via LiveKit SIP into {room_name}...")
        await lk.sip.create_sip_participant(
            api.CreateSIPParticipantRequest(
                sip_trunk_id=SIP_TRUNK_ID,
                sip_call_to=phone_number,
                room_name=room_name,
                participant_identity="customer_hanisha"
            )
        )
        print("Call initiated successfully! Your Linphone app should ring now.")

    finally:
        await lk.aclose()

def main() -> None:
    parser = argparse.ArgumentParser(description="Place an outbound call.")
    parser.add_argument(
        "--to",
        required=True,
        help="Target SIP user or number (e.g., zenfreshai07)",
    )
    parser.add_argument(
        "--room",
        default=None,
        help="Room name to use. Defaults to a generated one.",
    )
    args = parser.parse_args()

    room_name = args.room or f"outbound-{uuid.uuid4().hex[:8]}"

    asyncio.run(dial(args.to, room_name))

if __name__ == "__main__":
    main()