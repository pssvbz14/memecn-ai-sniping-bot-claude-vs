# pump.fun sniper bot — Βήμα 1: Detection layer

Αυτό είναι το πρώτο κομμάτι του pipeline: **μόνο ανίχνευση + logging**,
καμία συναλλαγή. Στόχος αυτού του βήματος είναι να δούμε:

- πόσα νέα tokens/λεπτό πιάνει το listener
- πόσο latency έχει το fetch της συναλλαγής μετά το detection
- αν η εξαγωγή του mint address δουλεύει σωστά (θα χρειαστεί επιβεβαίωση,
  βλ. σημείωση στο `listener.py`)

## Εγκατάσταση

```bash
pip install -r requirements.txt
cp .env.example .env
```

Άνοιξε το `.env` και βάλε το δικό σου RPC WebSocket URL. Το δημόσιο
`wss://api.mainnet-beta.solana.com` έχει αυστηρά rate limits και θα σε
κόβει σε λίγα λεπτά — είναι μόνο για γρήγορο sanity-check. Για πραγματικό
testing πάρε δωρεάν key από **Helius** ή **QuickNode** (και οι δύο έχουν
δωρεάν tier με websocket logsSubscribe).

## Εκτέλεση

```bash
python main.py
```

Θα δεις logs σαν αυτό για κάθε νέο token:
```
2026-08-23 19:40:12 [INFO] [NEW TOKEN] mint=... creator=... slot=... sig=...
```

Όλα καταγράφονται στο `./data/detections.db` (SQLite) — μπορείς να το
ανοίξεις με οποιοδήποτε SQLite viewer για να δεις τα raw δεδομένα.

## Τι ΔΕΝ κάνει ακόμα αυτό το βήμα

- Δεν φιλτράρει scams (έρχεται risk_filters.py)
- Δεν κάνει καμία αγορά (έρχεται executor.py, μόνο σε MODE=live)
- Το extraction του mint address είναι best-effort — βλ. σχόλιο στο
  `listener.py` για το γιατί χρειάζεται επιβεβαίωση κόντρα στο ενεργό IDL

## Επόμενα βήματα (με τη σειρά που έχει νόημα να χτιστούν)

1. **Τώρα:** `listener.py` — τρέξε το 24-48 ώρες και δες πόσα tokens/λεπτό
   πιάνει και πόσο σταθερό είναι το reconnect logic
2. `risk_filters.py` — holders, volume, bundle check, dev wallet history
   (on-chain δεδομένα, χωρίς εξωτερικό API)
3. `sniffer_api.py` — wrapper για το Solana Sniffer API πάνω στα tokens
   που ήδη πέρασαν το βήμα 2 (γλιτώνεις calls)
4. ~~`social_check.py` — Twitter/X API~~ → **αντικαταστάθηκε από
   `metaplex_social.py`** (βλ. παρακάτω), μόνο για tokens που πέρασαν 2+3
5. `executor.py` — Jito bundle execution, πρώτα σε `MODE=paper`
   (προσομοίωση αγοράς/πώλησης χωρίς πραγματικά χρήματα) πριν `MODE=live`

## metaplex_social.py — social signal χωρίς Twitter API

Αντί για Twitter API (rate limits), διαβάζουμε απευθείας το on-chain
Metaplex metadata account του token — free, χωρίς rate limits, χωρίς
extra API key. **Πριν το εμπιστευτείς:**

```bash
python3 -c "
from metaplex_social import get_social_signal
import json
# βάλε ένα πραγματικό, πρόσφατο pump.fun mint address εδώ
result = get_social_signal('https://api.mainnet-beta.solana.com', 'MINT_ADDRESS_ΕΔΩ')
print(json.dumps(result, indent=2))
"
```
Σύγκρινε το αποτέλεσμα με ό,τι βλέπεις στο πραγματικό pump.fun/dexscreener
για το ίδιο token. Το έχω unit-tested με constructed δεδομένα (σωστό byte
parsing), αλλά ΔΕΝ το έχω τρέξει κόντρα σε πραγματικό on-chain account
(χωρίς δίκτυο στο sandbox) — αυτό το χειροκίνητο βήμα είναι απαραίτητο.

**Θυμήσου:** `has_any_social` δείχνει τι *δήλωσε* ο δημιουργός, όχι
πραγματικό community buzz. Χρησιμοποίησέ το ως ένα ακόμα φίλτρο
("καθόλου social links" = ύποπτο), όχι ως engagement score.

## Deploy σε Railway

Το repo είναι έτοιμο για Railway όπως είναι: `python main.py` ως start
command, τα env vars μπαίνουν στο Railway dashboard αντί για `.env`.
Σημείωση: για το detection-only στάδιο το Railway αρκεί. Όταν φτάσουμε
στο execution layer (βήμα 5) θα ξαναδούμε αν χρειάζεται πιο εξειδικευμένη
υποδομή για latency.
