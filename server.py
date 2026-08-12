import socket
import json
import sys
import time

SERVER_PORT = 9999

# כמה שניות רישום של לקוח נשאר תקף לפני שהוא נמחק
CLIENT_TTL = 30.0

# כמה פעמים לשלוח את פרטי העמית (UDP לא אמין - שולחים כמה עותקים)
MATCH_RETRIES = 5
MATCH_RETRY_DELAY = 0.05

sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
try:
    sock.bind(('0.0.0.0', SERVER_PORT))
except OSError as e:
    # חשוב: יוצאים עם קוד שגיאה כדי שה-GUI ידע שהשרת לא עלה
    print(f"FATAL: could not bind to port {SERVER_PORT}: {e}", file=sys.stderr)
    sys.exit(1)

clients = {}  # client_id -> (addr, registered_at)

print(f"Signalling Server running on port {SERVER_PORT}...", flush=True)


def purge_stale(now):
    """מוחק רישומים ישנים כדי שלא נצמיד לקוח לכתובת מתה"""
    for cid in [c for c, (_, ts) in clients.items() if now - ts > CLIENT_TTL]:
        del clients[cid]
        print(f"Client '{cid}' expired.", flush=True)


def parse_registration(data):
    """מחזיר client_id רק עבור הודעת רישום תקינה, אחרת None"""
    try:
        msg = json.loads(data.decode('utf-8'))
    except (UnicodeDecodeError, json.JSONDecodeError):
        return None  # חבילת אודיו / PUNCH / זבל - מתעלמים בשקט

    if not isinstance(msg, dict) or msg.get("type") != "register":
        return None

    client_id = msg.get("id")
    if not isinstance(client_id, str) or not client_id.strip():
        return None

    return client_id.strip()


def send_repeatedly(payload, addr):
    for _ in range(MATCH_RETRIES):
        sock.sendto(payload, addr)
        time.sleep(MATCH_RETRY_DELAY)


while True:
    try:
        data, addr = sock.recvfrom(65535)
    except ConnectionResetError:
        # Windows WinError 10054 - נשלחה חבילה ליעד סגור, לא רלוונטי ב-UDP
        continue
    except OSError as e:
        print(f"Socket error: {e}", file=sys.stderr, flush=True)
        continue

    try:
        now = time.time()
        purge_stale(now)

        client_id = parse_registration(data)
        if client_id is None:
            continue

        # שמירת הכתובת הציבורית והפורט שה-NAT הקצה ללקוח
        known = clients.get(client_id)
        clients[client_id] = (addr, now)
        if known is None or known[0] != addr:
            print(f"Client '{client_id}' registered from {addr}", flush=True)

        # אם יש לנו בדיוק 2 נקודות מחוברות - מצמדים ביניהן
        if len(clients) == 2:
            (peer1_id, (addr1, _)), (peer2_id, (addr2, _)) = clients.items()

            # שליחת הכתובת של 2 ל-1
            send_repeatedly(
                json.dumps({"type": "peer", "peer_ip": addr2[0], "peer_port": addr2[1]}).encode('utf-8'),
                addr1,
            )

            # שליחת הכתובת של 1 ל-2
            send_repeatedly(
                json.dumps({"type": "peer", "peer_ip": addr1[0], "peer_port": addr1[1]}).encode('utf-8'),
                addr2,
            )

            print(f"Matched {peer1_id} <---> {peer2_id}. Addresses exchanged!", flush=True)
            clients.clear()  # איפוס לשיחה הבאה

    except Exception as e:
        print(f"Error handling packet: {e}", file=sys.stderr, flush=True)
