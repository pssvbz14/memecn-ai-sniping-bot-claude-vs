"""
On-chain listener για το pump.fun.

Γιατί έτσι και όχι μέσω DexScreener:
DexScreener τραβάει δεδομένα από indexers με καθυστέρηση δευτερολέπτων.
Εδώ ακούμε απευθείας τα logs του pump.fun program μέσω Solana WebSocket
(logsSubscribe) -> τα βλέπουμε τη στιγμή που μπαίνουν στο mempool/ledger,
όχι αφού τα δει κάποιος indexer.

ΣΗΜΑΝΤΙΚΟ για ακρίβεια:
Το να βρεις ΠΟΙΟΣ account μέσα στη συναλλαγή είναι το "mint" απαιτεί να
ξέρεις την ακριβή σειρά accounts της "create" instruction του τρέχοντος
pump.fun IDL. Αυτή η σειρά ΜΠΟΡΕΙ να αλλάξει αν το πρόγραμμα αναβαθμιστεί.
Εδώ κάνουμε best-effort extraction και αποθηκεύουμε ΚΑΙ τα raw logs / account
keys, ώστε να μην χάνεται τίποτα ακόμα κι αν το indexing χρειαστεί fine-tuning.
Πριν πας σε production, επιβεβαίωσε τη σειρά accounts κόντρα στο ενεργό IDL
(π.χ. μέσω του vendored IDL στο pump-fun/pump-public-docs στο GitHub).
"""
import asyncio
import json
import logging
import time

import requests
import websockets

from config import Config
from storage import Storage

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)
log = logging.getLogger("listener")

CREATE_MARKERS = ("Instruction: Create", "Instruction: InitializeMint")


class PumpFunListener:
    def __init__(self, config: Config, storage: Storage):
        self.config = config
        self.storage = storage
        self._reconnect_delay = config.RECONNECT_DELAY_SECONDS

    async def run_forever(self):
        while True:
            try:
                await self._run_once()
            except (websockets.ConnectionClosed, OSError) as e:
                log.warning(f"Σύνδεση έκλεισε ({e}). Επανασύνδεση σε {self._reconnect_delay:.0f}s...")
                await asyncio.sleep(self._reconnect_delay)
                self._reconnect_delay = min(
                    self._reconnect_delay * 2, self.config.MAX_RECONNECT_DELAY_SECONDS
                )
            except Exception:
                log.exception("Απροσδόκητο σφάλμα στο listener, retry σε 5s")
                await asyncio.sleep(5)

    async def _run_once(self):
        log.info(f"Σύνδεση σε {self.config.SOLANA_WSS_URL} ...")
        async with websockets.connect(self.config.SOLANA_WSS_URL, ping_interval=20) as ws:
            self._reconnect_delay = self.config.RECONNECT_DELAY_SECONDS  # reset μετά από επιτυχή σύνδεση

            subscribe_msg = {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "logsSubscribe",
                "params": [
                    {"mentions": [self.config.PUMPFUN_PROGRAM_ID]},
                    {"commitment": "processed"},
                ],
            }
            await ws.send(json.dumps(subscribe_msg))
            ack = await ws.recv()
            log.info(f"Subscribed: {ack}")

            async for raw_message in ws:
                await self._handle_message(raw_message)

    async def _handle_message(self, raw_message: str):
        try:
            data = json.loads(raw_message)
        except json.JSONDecodeError:
            return

        result = data.get("params", {}).get("result")
        if not result:
            return

        value = result.get("value", {})
        logs = value.get("logs", [])
        signature = value.get("signature")
        slot = result.get("context", {}).get("slot")

        if not logs or not signature:
            return

        # Φιλτράρουμε μόνο τα "create" events - τα περισσότερα mentions του
        # προγράμματος είναι buys/sells πάνω σε ήδη υπάρχοντα tokens.
        if not any(marker in line for line in logs for marker in CREATE_MARKERS):
            return

        t0 = time.monotonic()
        mint_address, creator_address = self._extract_accounts(signature)
        fetch_latency_ms = (time.monotonic() - t0) * 1000

        is_new = self.storage.log_detection(
            mint_address=mint_address,
            creator_address=creator_address,
            signature=signature,
            slot=slot,
            raw_logs=json.dumps(logs),
        )

        if is_new:
            log.info(
                f"[NEW TOKEN] mint={mint_address or '??'} "
                f"creator={creator_address or '??'} "
                f"slot={slot} sig={signature[:12]}... "
                f"(tx fetch: {fetch_latency_ms:.0f}ms)"
            )

    def _extract_accounts(self, signature: str) -> tuple[str | None, str | None]:
        """
        Best-effort: τραβάει την πλήρη συναλλαγή και επιστρέφει (mint, creator).
        Αν η δομή δεν αναγνωριστεί, επιστρέφει (None, None) - το detection
        πάντως ΚΑΤΑΓΡΑΦΕΤΑΙ στη βάση με τα raw logs, δεν χάνεται.
        """
        try:
            resp = requests.post(
                self.config.SOLANA_HTTP_URL,
                json={
                    "jsonrpc": "2.0",
                    "id": 1,
                    "method": "getTransaction",
                    "params": [
                        signature,
                        {"encoding": "jsonParsed", "maxSupportedTransactionVersion": 0},
                    ],
                },
                timeout=5,
            )
            resp.raise_for_status()
            tx = resp.json().get("result")
            if not tx:
                return None, None

            account_keys = tx["transaction"]["message"]["accountKeys"]
            # Ο πρώτος signer είναι σχεδόν πάντα ο creator/fee payer.
            creator = next(
                (a["pubkey"] for a in account_keys if a.get("signer")), None
            )
            # TODO: επιβεβαίωσε το index του mint account κόντρα στο ενεργό
            # pump.fun IDL (create instruction). Placeholder: 2ο account μη-signer.
            non_signers = [a["pubkey"] for a in account_keys if not a.get("signer")]
            mint = non_signers[0] if non_signers else None

            return mint, creator
        except Exception:
            log.debug(f"Δεν μπόρεσα να διαβάσω tx {signature}", exc_info=True)
            return None, None


async def main():
    config = Config()
    storage = Storage(config.DB_PATH)
    listener = PumpFunListener(config, storage)
    log.info(f"Ξεκινάει το bot σε MODE={config.MODE}")
    await listener.run_forever()


if __name__ == "__main__":
    asyncio.run(main())
