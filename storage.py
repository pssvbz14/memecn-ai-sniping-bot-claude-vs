"""
Απλό SQLite logging layer. Κάθε νέο token που εντοπίζεται καταγράφεται εδώ
με timestamp -> αυτό είναι η βάση για να μετρήσουμε αργότερα:
  - πόσα tokens/λεπτό πιάνει το listener
  - πόσο latency έχει από τη στιγμή του on-chain event μέχρι το detection
  - (σε επόμενο βήμα) πόσα περνάνε τα risk filters, πόσα είναι πραγματικά scams κ.λπ.
"""
import sqlite3
import os
from datetime import datetime, timezone
from contextlib import contextmanager


class Storage:
    def __init__(self, db_path: str):
        os.makedirs(os.path.dirname(db_path) or ".", exist_ok=True)
        self.db_path = db_path
        self._init_schema()

    @contextmanager
    def _connect(self):
        conn = sqlite3.connect(self.db_path)
        try:
            yield conn
            conn.commit()
        finally:
            conn.close()

    def _init_schema(self):
        with self._connect() as conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS detections (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    mint_address TEXT,
                    creator_address TEXT,
                    signature TEXT UNIQUE,
                    detected_at_utc TEXT,
                    slot INTEGER,
                    raw_logs TEXT,
                    risk_status TEXT DEFAULT 'pending',
                    notes TEXT
                )
                """
            )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_detected_at ON detections(detected_at_utc)"
            )

    def log_detection(
        self,
        mint_address: str | None,
        creator_address: str | None,
        signature: str,
        slot: int | None,
        raw_logs: str,
    ) -> bool:
        """Καταγράφει ένα νέο detection. Επιστρέφει False αν είναι duplicate (ίδιο signature)."""
        try:
            with self._connect() as conn:
                conn.execute(
                    """
                    INSERT INTO detections
                        (mint_address, creator_address, signature, detected_at_utc, slot, raw_logs)
                    VALUES (?, ?, ?, ?, ?, ?)
                    """,
                    (
                        mint_address,
                        creator_address,
                        signature,
                        datetime.now(timezone.utc).isoformat(),
                        slot,
                        raw_logs,
                    ),
                )
            return True
        except sqlite3.IntegrityError:
            # ίδιο signature -> το έχουμε ήδη δει (μπορεί να έρθει 2 φορές λόγω reconnect)
            return False

    def count_last_hour(self) -> int:
        with self._connect() as conn:
            cur = conn.execute(
                "SELECT COUNT(*) FROM detections WHERE detected_at_utc >= datetime('now', '-1 hour')"
            )
            return cur.fetchone()[0]
