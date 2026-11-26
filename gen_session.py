"""Run ONCE on your own PC to create SESSION_STRING. Never commit or share the output."""
from telethon.sync import TelegramClient
from telethon.sessions import StringSession

api_id = int(input("API ID: "))
api_hash = input("API HASH: ").strip()
with TelegramClient(StringSession(), api_id, api_hash) as c:
    print("\nSESSION_STRING (keep secret):\n")
    print(c.session.save())
