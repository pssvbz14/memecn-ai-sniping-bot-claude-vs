"""
Entry point. Προς το παρόν τρέχει μόνο το detection layer (Βήμα 1).
Στα επόμενα βήματα εδώ θα μπουν: risk_filters -> social_check -> executor.
"""
import asyncio
import logging

from config import Config
from storage import Storage
from listener import PumpFunListener

log = logging.getLogger("main")


async def stats_loop(storage: Storage, interval_seconds: int = 300):
    while True:
        await asyncio.sleep(interval_seconds)
        count = storage.count_last_hour()
        log.info(f"[STATS] Detections την τελευταία ώρα: {count}")


async def run():
    config = Config()
    storage = Storage(config.DB_PATH)
    listener = PumpFunListener(config, storage)

    await asyncio.gather(
        listener.run_forever(),
        stats_loop(storage),
    )


if __name__ == "__main__":
    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s"
    )
    asyncio.run(run())
