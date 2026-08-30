"""
Κεντρικές ρυθμίσεις του bot. Φορτώνονται από .env (βλ. .env.example).
"""
import os
from dotenv import load_dotenv

load_dotenv()


class Config:
    # Solana RPC
    SOLANA_WSS_URL: str = os.getenv("SOLANA_WSS_URL", "wss://api.mainnet-beta.solana.com")
    SOLANA_HTTP_URL: str = os.getenv("SOLANA_HTTP_URL", "https://api.mainnet-beta.solana.com")

    # pump.fun program (public on-chain address)
    PUMPFUN_PROGRAM_ID: str = os.getenv(
        "PUMPFUN_PROGRAM_ID", "6EF8rrecthR5Dkzon8Nwu78hRvfCKubJ14M5uBEwF6P"
    )

    # paper = μόνο ανίχνευση/log, live = πραγματικές συναλλαγές (έρχεται σε επόμενο βήμα)
    MODE: str = os.getenv("MODE", "paper")

    # Storage
    DB_PATH: str = os.getenv("DB_PATH", "./data/detections.db")

    # Reconnect / backoff
    RECONNECT_DELAY_SECONDS: float = 3.0
    MAX_RECONNECT_DELAY_SECONDS: float = 30.0
