"""
Risk filters πάνω σε on-chain δεδομένα - καθόλου εξωτερικό API (Solana Sniffer
έρχεται σε επόμενο βήμα, πάνω σε ό,τι ήδη περάσει από εδώ, ώστε να γλιτώνουμε
calls σε αυτό).

Τέσσερα σήματα, όπως συζητήσαμε:
  1. sol_raised       -> πόσο SOL έχει μπει στο bonding curve (proxy για "volume")
  2. holder_concentration -> τι % του supply κρατάει ο μεγαλύτερος holder
  3. bundle_signal    -> πόσοι διαφορετικοί αγοραστές στο ΙΔΙΟ slot με το create
  4. creator_history  -> πόσες προηγούμενες συναλλαγές έχει το dev wallet
     (πολύ λίγες = πιθανό φρεσκο-χρηματοδοτημένο wallet, κλασικό rug pattern)

ΑΞΙΟΠΙΣΤΙΑ ΤΟΥ BONDING CURVE PARSING:
Η δομή του account (discriminator + 5 πεδία u64) είναι διασταυρωμένη σε 3
ανεξάρτητες πηγές (επίσημο pump-fun/pump-public-docs, Rust SDK, TS SDK) και
ο discriminator υπολογίζεται εδώ live με sha256 (standard Anchor convention),
όχι hardcoded - αν ποτέ αλλάξει το layout, το πρόγραμμα θα το καταλάβει αμέσως
(βλ. except στο parse_bonding_curve_account) αντί να επιστρέψει σιωπηλά λάθος
νούμερα. Διαβάζουμε ΜΟΝΟ τα πρώτα 48 bytes (μέχρι το token_total_supply) -
πεδία μετά από αυτό (creator, quote_mint) έχουν αλλάξει πρόσφατα σε πρωτόκολλο
του pump.fun και δεν τα χρειαζόμαστε, οπότε τα αγνοούμε εντελώς.
Παρόλα αυτά, ΔΕΝ έχει τεσταριστεί κόντρα σε πραγματικό on-chain account
(χωρίς δίκτυο στο sandbox) - επιβεβαίωσέ το με ένα πραγματικό mint πριν
βασιστείς πλήρως πάνω του.
"""
import hashlib
import logging
import time

import requests
from solders.pubkey import Pubkey

log = logging.getLogger("risk_filters")

PUMPFUN_PROGRAM_ID = Pubkey.from_string("6EF8rrecthR5Dkzon8Nwu78hRvfCKubJ14M5uBEwF6P")
TOKEN_PROGRAM_ID = "TokenkegQfeZyiNwAJbNbGKPFXCWuBvf9Ss623VQ5DA"

# Υπολογισμένος (όχι hardcoded) - standard Anchor convention: sha256("account:{Name}")[:8]
BONDING_CURVE_DISCRIMINATOR = hashlib.sha256(b"account:BondingCurve").digest()[:8]

LAMPORTS_PER_SOL = 1_000_000_000


def derive_bonding_curve_pda(mint_address: str) -> str:
    mint_pubkey = Pubkey.from_string(mint_address)
    pda, _bump = Pubkey.find_program_address(
        [b"bonding-curve", bytes(mint_pubkey)], PUMPFUN_PROGRAM_ID
    )
    return str(pda)


def _rpc_call(http_url: str, method: str, params: list, retries: int = 2) -> dict | None:
    """Γενικό helper για JSON-RPC calls με μικρό retry (ίδιο σκεπτικό με το listener.py)."""
    import base64  # local import, χρησιμοποιείται μόνο εδώ μέσα

    for attempt in range(retries + 1):
        try:
            resp = requests.post(
                http_url,
                json={"jsonrpc": "2.0", "id": 1, "method": method, "params": params},
                timeout=5,
            )
            resp.raise_for_status()
            body = resp.json()
            if "error" in body:
                log.debug(f"RPC error σε {method}: {body['error']}")
                time.sleep(0.3 * (attempt + 1))
                continue
            return body.get("result")
        except Exception:
            log.debug(f"Απέτυχε {method} (attempt {attempt+1})", exc_info=True)
            time.sleep(0.3 * (attempt + 1))
    return None


def parse_bonding_curve_account(raw_bytes: bytes) -> dict | None:
    if len(raw_bytes) < 48:
        return None
    if raw_bytes[:8] != BONDING_CURVE_DISCRIMINATOR:
        log.warning(
            "Discriminator mismatch στο bonding curve account - το layout "
            "μπορεί να έχει αλλάξει. Επιστρέφω None αντί για λάθος δεδομένα."
        )
        return None

    def u64(offset: int) -> int:
        return int.from_bytes(raw_bytes[offset : offset + 8], "little")

    return {
        "virtual_token_reserves": u64(8),
        "virtual_sol_reserves": u64(16),
        "real_token_reserves": u64(24),
        "real_sol_reserves": u64(32),
        "token_total_supply": u64(40),
    }


def fetch_bonding_curve_state(http_url: str, mint_address: str) -> dict | None:
    import base64

    pda = derive_bonding_curve_pda(mint_address)
    result = _rpc_call(http_url, "getAccountInfo", [pda, {"encoding": "base64"}])
    if not result or not result.get("value"):
        return None
    raw_bytes = base64.b64decode(result["value"]["data"][0])
    parsed = parse_bonding_curve_account(raw_bytes)
    if not parsed:
        return None
    parsed["sol_raised"] = parsed["real_sol_reserves"] / LAMPORTS_PER_SOL
    return parsed


def fetch_holder_concentration(
    http_url: str, mint_address: str, token_total_supply: int
) -> dict:
    """
    top1_pct / top3_pct = μερίδιο του μεγαλύτερου / 3 μεγαλύτερων holders.
    Χρησιμοποιεί raw amounts (ίδια μονάδα με το token_total_supply από το
    bonding curve) ώστε να μην χρειάζεται να ξέρουμε τα decimals.
    """
    result = _rpc_call(http_url, "getTokenLargestAccounts", [mint_address])
    holders = (result or {}).get("value", [])
    if not holders or not token_total_supply:
        return {"top1_pct": None, "top3_pct": None, "holder_sample_size": len(holders)}

    amounts = sorted((int(h["amount"]) for h in holders), reverse=True)
    top1_pct = amounts[0] / token_total_supply
    top3_pct = sum(amounts[:3]) / token_total_supply
    return {
        "top1_pct": top1_pct,
        "top3_pct": top3_pct,
        "holder_sample_size": len(holders),
    }


def fetch_bundle_signal(http_url: str, bonding_curve_pda: str, limit: int = 40) -> dict:
    """
    Κοιτάει τις πρώτες συναλλαγές πάνω στο bonding curve. Αν πολλές έχουν
    ΑΚΡΙΒΩΣ το ίδιο slot με τη δημιουργία, πιθανό bundle (πολλά sniper
    wallets προγραμματισμένα να αγοράσουν στο ίδιο block).
    """
    result = _rpc_call(
        http_url, "getSignaturesForAddress", [bonding_curve_pda, {"limit": limit}]
    )
    if not result:
        return {"same_slot_tx_count": None, "earliest_slot": None}

    slots = [entry["slot"] for entry in result if entry.get("slot") is not None]
    if not slots:
        return {"same_slot_tx_count": None, "earliest_slot": None}

    earliest_slot = min(slots)
    same_slot_count = slots.count(earliest_slot)
    return {"same_slot_tx_count": same_slot_count, "earliest_slot": earliest_slot}


def fetch_creator_history(http_url: str, creator_address: str, limit: int = 10) -> dict:
    """
    Πόσες προηγούμενες συναλλαγές έχει κάνει το creator wallet. Πολύ λίγες
    (π.χ. 0-2, δηλαδή μόνο η ίδια η create tx) -> πιθανό φρεσκο-χρηματοδοτημένο
    wallet φτιαγμένο ειδικά για αυτό το launch, κλασικό red flag.
    """
    result = _rpc_call(
        http_url, "getSignaturesForAddress", [creator_address, {"limit": limit}]
    )
    if result is None:
        return {"tx_count": None}
    return {"tx_count": len(result)}  # capped στο `limit` - αρκεί για το φίλτρο μας


def evaluate_token_risk(config, mint_address: str, creator_address: str | None) -> dict:
    """
    Κεντρικό entry point: τρέχει όλα τα φίλτρα και επιστρέφει ενιαίο verdict.
    'passed=False' με λίστα λόγων αν αποτύχει έστω ένα φίλτρο. Αν κάποιο
    σήμα δεν μπόρεσε να διαβαστεί (None), το θεωρούμε ΑΠΟΤΥΧΙΑ κατά προσέγγιση
    ασφαλείας (fail-closed) - καλύτερα να χάσουμε ένα καλό token παρά να
    περάσουμε στα τυφλά ένα ύποπτο επειδή δεν είχαμε δεδομένα.
    """
    reasons = []
    signals = {}

    curve_state = fetch_bonding_curve_state(config.SOLANA_HTTP_URL, mint_address)
    if not curve_state:
        return {"passed": False, "reasons": ["δεν διαβάστηκε bonding curve account"], "signals": {}}
    signals["sol_raised"] = curve_state["sol_raised"]
    if curve_state["sol_raised"] < config.MIN_SOL_RAISED:
        reasons.append(
            f"sol_raised={curve_state['sol_raised']:.3f} < {config.MIN_SOL_RAISED}"
        )

    holder_info = fetch_holder_concentration(
        config.SOLANA_HTTP_URL, mint_address, curve_state["token_total_supply"]
    )
    signals["top1_holder_pct"] = holder_info["top1_pct"]
    if holder_info["top1_pct"] is None or holder_info["top1_pct"] > config.MAX_TOP_HOLDER_PCT:
        reasons.append(f"top1_holder_pct={holder_info['top1_pct']} > {config.MAX_TOP_HOLDER_PCT}")

    bonding_curve_pda = derive_bonding_curve_pda(mint_address)
    bundle_info = fetch_bundle_signal(config.SOLANA_HTTP_URL, bonding_curve_pda)
    signals["same_slot_buyers"] = bundle_info["same_slot_tx_count"]
    if (
        bundle_info["same_slot_tx_count"] is None
        or bundle_info["same_slot_tx_count"] > config.MAX_BUNDLE_SAME_SLOT_BUYERS
    ):
        reasons.append(
            f"same_slot_buyers={bundle_info['same_slot_tx_count']} > {config.MAX_BUNDLE_SAME_SLOT_BUYERS}"
        )

    if creator_address:
        creator_info = fetch_creator_history(config.SOLANA_HTTP_URL, creator_address)
        signals["creator_tx_count"] = creator_info["tx_count"]
        if (
            creator_info["tx_count"] is None
            or creator_info["tx_count"] < config.MIN_CREATOR_TX_COUNT
        ):
            reasons.append(
                f"creator_tx_count={creator_info['tx_count']} < {config.MIN_CREATOR_TX_COUNT}"
            )
    else:
        reasons.append("δεν υπάρχει creator address για έλεγχο ιστορικού")

    return {"passed": len(reasons) == 0, "reasons": reasons, "signals": signals}
