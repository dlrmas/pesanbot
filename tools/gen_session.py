"""Buat session string akun asisten (jalankan sekali, interaktif).

    python tools/gen_session.py
"""
import asyncio
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from dotenv import load_dotenv
from pyrogram import Client


async def main():
    load_dotenv()
    api_id = int(os.environ.get("API_ID") or input("API_ID: "))
    api_hash = os.environ.get("API_HASH") or input("API_HASH: ")
    async with Client(":memory:", api_id=api_id, api_hash=api_hash, in_memory=True) as app:
        me = await app.get_me()
        print(f"\nLogin sebagai: {me.first_name} ({me.id})")
        print("\nASSISTANT_SESSION=\n" + await app.export_session_string())
        print("\nSalin nilai di atas ke file .env — jangan dibagikan ke siapa pun!")


if __name__ == "__main__":
    asyncio.run(main())
