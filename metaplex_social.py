"""
Αντικαθιστά το Twitter API signal με κάτι πιο αξιόπιστο σε rate limits:
διαβάζουμε απευθείας το on-chain Metaplex metadata account του token
(το ίδιο account που διαβάζει και το j7tracker) και τραβάμε το URI ->
το off-chain JSON με description/social links.

ΠΡΟΣΟΧΗ - τι ΔΕΝ είναι αυτό:
Αυτό ΔΕΝ μετράει πραγματικό community buzz/engagement. Δείχνει μόνο τι
*δήλωσε* ο δημιουργός του token όταν το έφτιαξε (self-reported claim,
όχι επαληθευμένο). Ένας scammer μπορεί να βάλει fake Twitter link το ίδιο
εύκολα με έναν νόμιμο δημιουργό. Χρησιμοποίησέ το ως ένα ακόμα σήμα
(π.χ. "καθόλου social links" = κόκκινη σημαία), όχι ως αξιόπιστο score.

ΔΕΝ έχω δοκιμάσει το parsing κόντρα σε πραγματικό mainnet account (το
sandbox δεν έχει πρόσβαση σε Solana RPC). Το byte-parsing logic είναι
unit-tested με synthetic δεδομένα - επιβεβαίωσέ το κόντρα σε πραγματικό
mint address πριν το εμπιστευτείς σε production.
"""
import base64
import json
import logging

import requests
from solders.pubkey import Pubkey

log = logging.getLogger("metaplex")

TOKEN_METADATA_PROGRAM_ID = Pubkey.from_string(
    "metaqbxxUerdq28cj1RbAWkYQm3ybzjb6a8bt518x1s"
)


def derive_metadata_pda(mint_address: str) -> str:
    """Υπολογίζει τη διεύθυνση του metadata account για ένα δοσμένο mint."""
    mint_pubkey = Pubkey.from_string(mint_address)
    pda, _bump = Pubkey.find_program_address(
        [b"metadata", bytes(TOKEN_METADATA_PROGRAM_ID), bytes(mint_pubkey)],
        TOKEN_METADATA_PROGRAM_ID,
    )
    return str(pda)


def _read_borsh_string(buf: bytes, offset: int) -> tuple[str, int]:
    """
    Borsh string = 4-byte LE length prefix + utf8 bytes.
    Επιστρέφει (κείμενο χωρίς trailing null padding, νέο offset).
    """
    length = int.from_bytes(buf[offset : offset + 4], "little")
    start = offset + 4
    end = start + length
    raw = buf[start:end]
    text = raw.decode("utf-8", errors="replace").rstrip("\x00")
    return text, end


def parse_metadata_account(raw_bytes: bytes) -> dict:
    """
    Layout Metaplex Token Metadata (MetadataV1):
      [0]      key (u8, discriminator)
      [1:33]   update_authority (32 bytes)
      [33:65]  mint (32 bytes)
      [65:]    data.name (borsh string)
               data.symbol (borsh string)
               data.uri (borsh string)
               ... (δεν μας ενδιαφέρει το υπόλοιπο εδώ)
    """
    offset = 65
    name, offset = _read_borsh_string(raw_bytes, offset)
    symbol, offset = _read_borsh_string(raw_bytes, offset)
    uri, offset = _read_borsh_string(raw_bytes, offset)
    return {"name": name, "symbol": symbol, "uri": uri}


def fetch_onchain_metadata(http_rpc_url: str, mint_address: str) -> dict | None:
    """Διαβάζει το metadata account από τον blockchain μέσω getAccountInfo."""
    try:
        pda = derive_metadata_pda(mint_address)
        resp = requests.post(
            http_rpc_url,
            json={
                "jsonrpc": "2.0",
                "id": 1,
                "method": "getAccountInfo",
                "params": [pda, {"encoding": "base64"}],
            },
            timeout=5,
        )
        resp.raise_for_status()
        value = resp.json().get("result", {}).get("value")
        if not value:
            return None  # δεν υπάρχει metadata account -> ύποπτο από μόνο του

        raw_bytes = base64.b64decode(value["data"][0])
        return parse_metadata_account(raw_bytes)
    except Exception:
        log.debug(f"Δεν μπόρεσα να διαβάσω metadata για {mint_address}", exc_info=True)
        return None


def fetch_social_links(uri: str) -> dict:
    """
    Κατεβάζει το off-chain JSON (Arweave/IPFS συνήθως) και τραβάει τα social
    links. Το schema δεν είναι 100% σταθερό μεταξύ projects - ψάχνουμε και
    στο top level και μέσα σε "extensions", με fallback σε κενά strings.
    """
    result = {"twitter": "", "telegram": "", "website": "", "description": ""}
    try:
        resp = requests.get(uri, timeout=5)
        resp.raise_for_status()
        data = resp.json()

        extensions = data.get("extensions", {}) if isinstance(data, dict) else {}
        result["description"] = data.get("description", "") if isinstance(data, dict) else ""
        result["twitter"] = extensions.get("twitter") or data.get("twitter", "")
        result["telegram"] = extensions.get("telegram") or data.get("telegram", "")
        result["website"] = extensions.get("website") or data.get("website", "")
    except Exception:
        log.debug(f"Δεν μπόρεσα να διαβάσω off-chain JSON από {uri}", exc_info=True)
    return result


def get_social_signal(http_rpc_url: str, mint_address: str) -> dict:
    """
    Το κύριο entry point για τον pipeline: επιστρέφει ό,τι ξέρουμε για τα
    δηλωμένα social links ενός token. has_any_social=False είναι από μόνο
    του χρήσιμο φίλτρο (τα πιο πολλά νόμιμα projects βάζουν έστω Twitter).
    """
    metadata = fetch_onchain_metadata(http_rpc_url, mint_address)
    if not metadata or not metadata.get("uri"):
        return {
            "has_metadata": False,
            "has_any_social": False,
            "twitter": "",
            "telegram": "",
            "website": "",
            "description": "",
        }

    social = fetch_social_links(metadata["uri"])
    has_any_social = any([social["twitter"], social["telegram"], social["website"]])

    return {
        "has_metadata": True,
        "has_any_social": has_any_social,
        **social,
    }
