import socket
import contextlib
import json
import threading
import time
import sounddevice as sd
import numpy as np
import os
import sys
from typing import Optional, Tuple, Dict, Any

# --- System & Audio Settings ---
SAMPLE_RATE: int = 16000
CHANNELS: int = 1
CHUNK_SIZE: int = 512
DTYPE: str = 'int16'
BUFFER_SIZE: int = 65535          # UDP חותך בשקט חבילות גדולות מהבאפר
TIMEOUT_SECS: float = 3.0
SETTINGS_FILE: str = "settings.txt"

# --- P2P wire protocol (חייב להיות זהה ל-server.py) ---
AUDIO_TAG: bytes = b'\x01'
PUNCH_TAG: bytes = b'\x02'
PUNCH_PACKET: bytes = PUNCH_TAG + b'PUNCH'
P2P_TAGS: Tuple[bytes, bytes] = (AUDIO_TAG, PUNCH_TAG)

# --- Signalling status values (חייב להיות זהה ל-server.py) ---
# ללא status = רישום ("יש אוזניות, חפש לי עמית")
STATUS_ALIVE: str = "alive"                 # פעימה במהלך שיחה: האוזניות עדיין מחוברות
STATUS_LEFT: str = "left"                   # יציאה מהשיחה - מצב האוזניות לא השתנה
STATUS_DISCONNECTED: str = "disconnected"   # האוזניות באמת נשלפו

PUNCH_DURATION: float = 2.0
PUNCH_INTERVAL: float = 0.1
HW_REFRESH_GAP: float = 5.0       # מרווח מזערי בין אתחולי PortAudio
STREAM_CLOSE_WAIT: float = 5.0    # סגירת stream על התקן שנשלף עלולה להיתקע

# --- Server state reporting ---
# חבילת UDP בודדת נעלמת בשקט. כל דיווח מצב נשלח כמה פעמים, ובנוסף המצב
# משודר שוב ושוב כל עוד הוא נכון - כך אובדן חבילה בודדת לא "מקפיא" את
# התצוגה בצד השרת.
REPORT_RETRIES: int = 4
REPORT_RETRY_DELAY: float = 0.04
STATE_REPORT_INTERVAL: float = 2.0   # שידור חוזר של "אין אוזניות" בזמן המתנה להתקן
HEARTBEAT_INTERVAL: float = 1.0      # פעימה לשרת במהלך שיחה
CAPTURE_STALL_SECS: float = 5.0      # המיקרופון הפסיק לספק דגימות -> ההתקן נעלם
DEVICE_CHECK_INTERVAL: float = 2.0   # בדיקת נוכחות התקן בזמן המתנה לעמית
DEVICE_DIAG_INTERVAL: float = 10.0   # כל כמה זמן להסביר ביומן למה אין התקן

# --- Acoustic echo suppression (voice switch) ---
# ההד נולד בצד שמשמיע ברמקול: המיקרופון שלו קולט את קול הצד השני ומחזיר
# אותו. הפתרון כאן הוא voice switch פשוט - מנמיכים את המיקרופון המקומי כל
# עוד הצד השני מדבר. אין תלות בספריות DSP, ועובד גם מול רמקולים.
ECHO_SUPPRESSION: bool = True
FAR_END_ACTIVE_RMS: float = 300.0   # מעל זה נחשב "הצד השני מדבר" (int16 RMS)
DUCK_HANGOVER: float = 0.25         # כמה זמן להנמיך אחרי המילה האחרונה שנקלטה
DUCK_GAIN: float = 0.05             # לא אפס: החבילות חייבות להמשיך לזרום,
                                    # אחרת הצד השני יכריז ניתוק אחרי 3 שניות
BREAK_IN_RATIO: float = 2.5         # דיבור חזק פי כך מהנכנס גובר על ההנמכה


def audio_rms(block) -> float:
    """עוצמת בלוק אודיו. float32 כדי שלא תהיה גלישה בריבוע של int16."""
    if block is None or block.size == 0:
        return 0.0
    return float(np.sqrt(np.mean(np.square(block.astype(np.float32)))))


class IntercomCLI:
    def __init__(self) -> None:
        self.sock: Optional[socket.socket] = None
        self.is_running: bool = False
        self.audio_lock: threading.Lock = threading.Lock()
        self._open_streams: int = 0       # מוגן ע"י audio_lock
        self._last_refresh: float = 0.0
        self._refresh_error: str = ""          # למה אתחול PortAudio נכשל
        self._refresh_blocked_since: float = 0.0   # מתי נחסם הרענון ע"י stream פתוח

        self.rx_thread: Optional[threading.Thread] = None
        self.tx_thread: Optional[threading.Thread] = None
        self.hb_thread: Optional[threading.Thread] = None

        # מסמן שהניתוק נבע מההתקן ולא מהרשת - קובע מה מדווח לשרת
        self._device_failed: bool = False
        self._last_capture: float = 0.0

        # Echo suppression state (נכתב ע"י תרד הקליטה, נקרא ע"י תרד השידור)
        self._far_end_active: float = 0.0
        self._far_end_level: float = 0.0

        # Peer address handling
        self.peer_lock: threading.Lock = threading.Lock()
        self.assigned_peer: Optional[Tuple[str, int]] = None
        self.locked_peer: Optional[Tuple[str, int]] = None
        self.tx_peer: Optional[Tuple[str, int]] = None

        self.server_ip: str = "192.168.1.11"
        self.server_port: int = 9999
        self.my_id: str = "node_B"
        self.peer_id: str = ""
        # התקנים מפורשים מקובץ ההגדרות. None = התקן ברירת המחדל של המערכת.
        # נחוץ כשהאוזניות אינן ההתקן הראשי - אז בדיקת ברירת המחדל לא רואה אותן.
        self.mic: Optional[Any] = None
        self.speaker: Optional[Any] = None

        self.load_settings()

    def log(self, msg: str) -> None:
        """Thread-safe standard output logging."""
        print(f"[{time.strftime('%H:%M:%S')}] {msg}", flush=True)

    def load_settings(self) -> None:
        """Parses the JSON settings file safely and populates instance variables."""
        if os.path.exists(SETTINGS_FILE):
            try:
                with open(SETTINGS_FILE, "r", encoding="utf-8") as f:
                    data: Dict[str, Any] = json.load(f)
                    self.server_ip = str(data.get("ip", self.server_ip)).strip()
                    self.server_port = int(data.get("port", self.server_port))
                    self.my_id = str(data.get("my_id", self.my_id)).strip()
                    self.mic = self._device_setting(data.get("mic"))
                    self.speaker = self._device_setting(data.get("speaker"))
                picked = ""
                if self.mic is not None or self.speaker is not None:
                    picked = f", Mic={self.mic!r}, Speaker={self.speaker!r}"
                self.log(f"Loaded settings: IP={self.server_ip}, Port={self.server_port}, "
                         f"ID={self.my_id}{picked}")
            except Exception as e:
                self.log(f"Error loading {SETTINGS_FILE}: {e}. Using defaults.")
        else:
            self.log(f"{SETTINGS_FILE} not found. Creating default settings file.")
            self.save_settings()

    def save_settings(self) -> None:
        """Serializes current configuration into a localized JSON config."""
        data: Dict[str, str] = {
            "ip": self.server_ip,
            "port": str(self.server_port),
            "my_id": self.my_id,
            # ריק = התקן ברירת המחדל. אפשר לשים אינדקס (0,1,2...) או חלק משם
            # ההתקן, למשל "USB Audio" - שימושי כשהאוזניות אינן ההתקן הראשי.
            "mic": "" if self.mic is None else str(self.mic),
            "speaker": "" if self.speaker is None else str(self.speaker),
        }
        try:
            with open(SETTINGS_FILE, "w", encoding="utf-8") as f:
                json.dump(data, f, indent=4)
        except OSError as e:
            self.log(f"Error saving settings: {e}")

    @staticmethod
    def _device_setting(value: Any) -> Optional[Any]:
        """ריק/חסר = ברירת המחדל. מספר = אינדקס התקן. אחרת: חלק משם ההתקן."""
        if value is None:
            return None
        text = str(value).strip()
        if not text:
            return None
        try:
            return int(text)
        except ValueError:
            return text

    # ------------------------------------------------------------------
    # Server state reporting
    # ------------------------------------------------------------------
    def _send_signalling(self, payload: Dict[str, Any], repeats: int = REPORT_RETRIES) -> None:
        """שולח הודעת בקרה לשרת, עם חזרות ובלי תלות בסוקט הראשי.

        זו הנקודה שבה נשבר החיווי קודם: הדיווח נשלח פעם אחת בלבד, ורק אם
        סוקט ה-P2P היה פתוח. חבילה אחת שאבדה (או שליפת אוזניות לפני שהיה
        חיבור) הותירה את השרת עם מצב ישן.
        """
        data = json.dumps(payload).encode('utf-8')
        target = (self.server_ip, self.server_port)

        sock = self.sock
        temp: Optional[socket.socket] = None
        if sock is None:
            try:
                temp = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            except OSError:
                return
            sock = temp

        try:
            for i in range(max(1, repeats)):
                try:
                    sock.sendto(data, target)
                except (AttributeError, OSError):
                    return   # הסוקט נסגר תחת הרגליים - לא נורא, יש שידור חוזר
                if i + 1 < repeats:
                    time.sleep(REPORT_RETRY_DELAY)
        finally:
            if temp is not None:
                with contextlib.suppress(OSError):
                    temp.close()

    def _heartbeat(self) -> None:
        """פעימה לשרת כל עוד השיחה חיה.

        אחרי ההצמדה הלקוח מפסיק להירשם, ולכן בלי הפעימות האלה לשרת אין
        שום דרך לדעת שהלקוח נעלם (שליפת אוזניות שהקריסה את התהליך, נפילת
        רשת) - הוא היה נשאר עם "connected" לנצח.
        """
        while self.is_running:
            self._send_signalling({"id": self.my_id, "status": STATUS_ALIVE}, repeats=1)
            deadline = time.time() + HEARTBEAT_INTERVAL
            while self.is_running and time.time() < deadline:
                time.sleep(0.1)

    # ------------------------------------------------------------------
    # Audio hardware
    # ------------------------------------------------------------------
    def _safe_refresh_hardware(self) -> bool:
        """Restarts the PortAudio bindings. Never runs while a stream is open.

        מחזיר True רק כשרשימת ההתקנים באמת התרעננה. בלי הרענון הזה PortAudio
        ממשיך לעבוד מול רשימת ההתקנים שנבנתה באתחול, ולכן אוזניות שחוברו
        אחר כך פשוט לא קיימות מבחינתו.
        """
        with self.audio_lock:
            # אתחול PortAudio בזמן ש-stream פתוח מקריס את התהליך ברמת ה-C,
            # בלי traceback. המונה מוחזק על פני כל חיי ה-stream (ראה _counted_stream).
            if self._open_streams > 0:
                if not self._refresh_blocked_since:
                    self._refresh_blocked_since = time.time()
                return False
            self._refresh_blocked_since = 0.0
            # אתחול גלובלי חוזר בזמן שההתקן מתחבר/מתנתק הוא בעצמו גורם קריסה,
            # לכן לא מרעננים בתדירות גבוהה
            now = time.time()
            if now - self._last_refresh < HW_REFRESH_GAP:
                return False
            self._last_refresh = now
            try:
                sd._terminate()
                sd._initialize()
                self._refresh_error = ""
                return True
            except Exception as e:
                # נשמר כדי שיודפס ביומן: כישלון שקט כאן פירושו שהתקן חדש
                # לעולם לא ייקלט
                self._refresh_error = f"{type(e).__name__}: {e}"
                return False

    @contextlib.contextmanager
    def _counted_stream(self, factory):
        """Opens a stream while holding the open-stream counter for its whole lifetime.

        The counter must cover the close as well: closing a stream on a device
        that was just unplugged can block, and a refresh during that window
        would terminate PortAudio underneath a live stream.
        """
        with self.audio_lock:
            self._open_streams += 1
        try:
            with factory() as stream:
                yield stream
        finally:
            with self.audio_lock:
                self._open_streams -= 1

    def _device_check(self) -> Tuple[bool, str]:
        """שאילתה על ההתקנים במקום לפתוח ולסגור streams אמיתיים -
        כל מחזור פתיחה/סגירה על התקן מתנתק הוא סיכון מיותר.

        מחזיר גם את סיבת הכישלון, כדי שיהיה אפשר לראות ביומן למה האוזניות
        לא זוהו במקום לנחש.
        """
        try:
            sd.check_output_settings(device=self.speaker, samplerate=SAMPLE_RATE,
                                     channels=CHANNELS, dtype=DTYPE)
        except Exception as e:
            return False, f"output ({self.speaker or 'default'}): {type(e).__name__}: {e}"
        try:
            sd.check_input_settings(device=self.mic, samplerate=SAMPLE_RATE,
                                    channels=CHANNELS, dtype=DTYPE)
        except Exception as e:
            return False, f"input ({self.mic or 'default'}): {type(e).__name__}: {e}"
        return True, ""

    def _devices_present(self) -> bool:
        return self._device_check()[0]

    def _device_summary(self) -> str:
        """מה PortAudio רואה כרגע - הדרך המהירה להבין אם ההתקן בכלל הגיע."""
        try:
            devices = sd.query_devices()
        except Exception as e:
            return f"query_devices failed: {type(e).__name__}: {e}"
        ins = [d["name"] for d in devices if d.get("max_input_channels", 0) > 0]
        outs = [d["name"] for d in devices if d.get("max_output_channels", 0) > 0]
        return (f"{len(ins)} input(s) {ins[:4]} | {len(outs)} output(s) {outs[:4]}")

    def _wait_for_audio_device(self) -> None:
        """Blocks until BOTH audio devices are connected, reporting the state meanwhile."""
        self.log("Waiting for headphones (Mic & Speaker) to be connected...")
        last_report: float = 0.0
        last_diag: float = 0.0
        while True:
            ok, reason = self._device_check()
            if ok:
                self.log("Audio devices (Mic & Speaker) detected! Initiating connection to Server...")
                return

            # שידור חוזר של "אין אוזניות": גם אם דיווח קודם אבד ברשת,
            # השרת יקבל את הבא בתור תוך שתי שניות
            now = time.time()
            if now - last_report >= STATE_REPORT_INTERVAL:
                last_report = now
                self._send_signalling({"id": self.my_id, "status": STATUS_DISCONNECTED})

            # בלי ההסבר הזה הלקוח פשוט שותק, ואי אפשר לדעת אם הוא לא רואה
            # את ההתקן, לא מצליח לרענן את הרשימה, או תקוע לגמרי
            if now - last_diag >= DEVICE_DIAG_INTERVAL:
                last_diag = now
                self.log(f"No audio device yet -> {reason}")
                self.log(f"PortAudio currently sees: {self._device_summary()}")
                if self._refresh_error:
                    self.log(f"Device list refresh is failing: {self._refresh_error}")
                if self._refresh_blocked_since:
                    stuck = int(now - self._refresh_blocked_since)
                    self.log(f"WARNING: an audio thread has been holding the sound card for "
                             f"{stuck}s, so the device list cannot be refreshed. Re-plugging "
                             f"the headphones will NOT be detected until this client is "
                             f"restarted.")

            # Refresh hardware in case the device was just plugged in and unrecognized
            if self._safe_refresh_hardware():
                continue   # הרשימה התרעננה - בודקים מיד, בלי להמתין עוד שנייה
            time.sleep(1)

    # ------------------------------------------------------------------
    # Main loop
    # ------------------------------------------------------------------
    def start(self) -> None:
        """Main execution loop for the headless client."""
        self.log(f"Starting Headless Client [{self.my_id}] -> Target: {self.server_ip}:{self.server_port}")
        self.log("Press Ctrl+C to exit.")

        while True:
            try:
                if not self.is_running:
                    self.load_settings()
                    self._wait_for_audio_device()
                    self._connect_and_stream()
                time.sleep(1)
            except KeyboardInterrupt:
                self.log("Exiting...")
                self.stop()
                sys.exit(0)
            except Exception as e:
                self.log(f"Hardware or Network Error: {e}")
                self.stop()
                time.sleep(3)

    def _await_peer(self) -> Optional[Tuple[str, int]]:
        """ממתין לפרטי העמית. מתעלם מכל חבילה שאינה תשובת סיגנלינג תקינה."""
        msg: bytes = json.dumps({"id": self.my_id}).encode('utf-8')
        last_device_check: float = time.time()
        while self.is_running and self.sock:
            # שליפת אוזניות בזמן ההמתנה לעמית: בלי הבדיקה הזו הלקוח ממשיך
            # להירשם ומספר לשרת "יש לי אוזניות" עוד דקות אחרי שנשלפו
            if time.time() - last_device_check >= DEVICE_CHECK_INTERVAL:
                last_device_check = time.time()
                self._safe_refresh_hardware()
                if not self._devices_present():
                    self.log("Headphones removed while waiting for a peer.")
                    self._device_failed = True
                    self.is_running = False
                    return None

            try:
                self.sock.sendto(msg, (self.server_ip, self.server_port))
            except (AttributeError, OSError):
                return None

            try:
                data, _ = self.sock.recvfrom(BUFFER_SIZE)
            except socket.timeout:
                continue
            except ConnectionResetError:
                time.sleep(1)
                continue
            except (AttributeError, OSError):
                return None

            # חבילת אודיו מעמית שהקדים אותנו כבר לא מפילה את הרישום
            try:
                info = json.loads(data.decode('utf-8'))
            except (UnicodeDecodeError, json.JSONDecodeError):
                continue
            if not isinstance(info, dict) or "peer_ip" not in info or "peer_port" not in info:
                continue
            try:
                peer = (str(info["peer_ip"]), int(info["peer_port"]))
            except (TypeError, ValueError):
                continue
            peer_id = info.get("peer_id")
            self.peer_id = peer_id.strip() if isinstance(peer_id, str) else ""
            return peer
        return None

    def _connect_and_stream(self) -> None:
        """Handles server registration, NAT hole punching, and threading activation."""
        self.is_running = True
        self._device_failed = False
        self._far_end_active = 0.0
        self._far_end_level = 0.0
        self._safe_refresh_hardware()
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.sock.settimeout(1.0)
        with self.peer_lock:
            self.assigned_peer = None
            self.locked_peer = None
            self.tx_peer = None

        try:
            self.log("Waiting for matching peer from server...")

            peer_addr = self._await_peer()
            if not self.is_running or not peer_addr:
                return

            with self.peer_lock:
                self.assigned_peer = peer_addr
                self.tx_peer = peer_addr

            who = f" ({self.peer_id})" if self.peer_id else ""
            self.log(f"Peer Target Assigned{who} -> IP: {peer_addr[0]}, Port: {peer_addr[1]}")
            self.log("Sending UDP Hole Punch packets...")

            punch_until = time.time() + PUNCH_DURATION
            while self.is_running and time.time() < punch_until:
                try:
                    self.sock.sendto(PUNCH_PACKET, peer_addr)
                except (AttributeError, OSError):
                    return
                time.sleep(PUNCH_INTERVAL)

            if not self.is_running:
                return

            self.log("NAT hole punched! Live audio streaming active.")

            self._last_capture = time.time()
            self.rx_thread = threading.Thread(target=self._audio_receiver, daemon=True)
            self.tx_thread = threading.Thread(target=self._audio_sender, daemon=True)
            self.hb_thread = threading.Thread(target=self._heartbeat, daemon=True)

            self.rx_thread.start()
            self.tx_thread.start()
            self.hb_thread.start()

            # Wait until one of the threads dies (e.g., due to headphones disconnecting)
            while self.is_running and self.rx_thread.is_alive() and self.tx_thread.is_alive():
                # התקן שנשלף לא תמיד מייצר חריגה - לפעמים ה-read פשוט מפסיק
                # לספק דגימות. בלי השומר הזה התהליך נשאר "מחובר" לנצח.
                if time.time() - self._last_capture > CAPTURE_STALL_SECS:
                    self.log("Microphone stopped delivering audio (device removed?).")
                    self._device_failed = True
                    break
                time.sleep(0.5)

            if self.is_running:
                self.log("Audio stream interrupted by hardware state change. Reconnecting...")

        except Exception as e:
            self.log(f"Connection or Hardware error: {e}")
            time.sleep(2)
        finally:
            # Guarantee state reset and socket cleanup
            self.stop()

    # ------------------------------------------------------------------
    # Audio streaming
    # ------------------------------------------------------------------
    def _peer_audio(self, addr: Tuple[str, int], data: bytes) -> Optional[bytes]:
        """מאמת שהחבילה הגיעה מהעמית ומחזיר payload רק לחבילות אודיו."""
        tag, payload = data[:1], data[1:]
        if tag not in P2P_TAGS:
            return None  # JSON מהשרת או זבל - לעולם לא מתנגן

        with self.peer_lock:
            if self.locked_peer is None:
                # ננעלים על הכתובת של החבילה המתויגת הראשונה, גם אם ה-NAT
                # הקצה פורט שונה מזה שהשרת דיווח
                self.locked_peer = addr
                self.tx_peer = addr
                if addr != self.assigned_peer:
                    self.log(f"Peer reached us from {addr[0]}:{addr[1]} "
                             f"(assigned {self.assigned_peer}). Locking on.")
            elif addr != self.locked_peer:
                return None  # מונע הזרקת אודיו מכל מקור אחר ברשת

        if tag != AUDIO_TAG or not payload:
            return None
        if len(payload) % (2 * CHANNELS) != 0:
            return None  # חבילה קטומה
        return payload


    def _note_far_end(self, block) -> None:
        """מסמן שהצד השני מדבר, כדי שנוכל להנמיך את המיקרופון ולמנוע הד."""
        if not ECHO_SUPPRESSION:
            return
        level = audio_rms(block)
        if level > FAR_END_ACTIVE_RMS:
            self._far_end_level = level
            self._far_end_active = time.time()

    def _tx_block(self, block):
        """מנמיך את המיקרופון בזמן שהצד השני מדבר.

        זה מה שמונע מהצד השני לשמוע את עצמו: מה שהרמקול שלנו משמיע חוזר
        למיקרופון שלנו, ובלי ההנמכה היינו משדרים אותו בחזרה כהד.
        """
        if not ECHO_SUPPRESSION:
            return block
        if time.time() - self._far_end_active > DUCK_HANGOVER:
            return block
        if audio_rms(block) > self._far_end_level * BREAK_IN_RATIO:
            return block   # דיבור חזק מספיק גובר, כדי שאפשר יהיה להפסיק את הצד השני
        return (block * DUCK_GAIN).astype(np.int16)

    def _audio_receiver(self) -> None:
        """Dedicated thread for receiving and playing live P2P audio data."""
        last_data_time: float = time.time()
        try:
            with self._counted_stream(
                    lambda: sd.OutputStream(device=self.speaker, samplerate=SAMPLE_RATE,
                                            channels=CHANNELS, dtype=DTYPE)) as stream:
                while self.is_running:
                    sock = self.sock
                    if sock is None:
                        break
                    try:
                        data, addr = sock.recvfrom(BUFFER_SIZE)
                    except socket.timeout:
                        if time.time() - last_data_time > TIMEOUT_SECS:
                            self.log("Peer disconnected (3 seconds timeout).")
                            self.is_running = False
                            break
                        continue
                    except ConnectionResetError:
                        self.log("Peer disconnected unexpectedly.")
                        self.is_running = False
                        break
                    except (AttributeError, OSError):
                        break  # הסוקט נסגר - יציאה נקייה

                    last_data_time = time.time()
                    payload = self._peer_audio(addr, data)
                    if payload is not None:
                        block = np.frombuffer(payload, dtype=np.int16)
                        self._note_far_end(block)
                        stream.write(block.reshape(-1, CHANNELS))
        except Exception:
            if self.is_running:
                self.log("Output device error (Headphones disconnected).")
            self._device_failed = True   # קובע שידווח לשרת "אוזניות נשלפו"

    def _audio_sender(self) -> None:
        """Dedicated thread for capturing and transmitting local microphone audio."""
        try:
            with self._counted_stream(
                    lambda: sd.InputStream(device=self.mic, samplerate=SAMPLE_RATE,
                                           channels=CHANNELS, dtype=DTYPE,
                                           blocksize=CHUNK_SIZE)) as stream:
                while self.is_running:
                    data, _ = stream.read(CHUNK_SIZE)
                    self._last_capture = time.time()
                    sock, peer = self.sock, self.tx_peer
                    if sock is None or peer is None:
                        break
                    try:
                        sock.sendto(AUDIO_TAG + self._tx_block(data).tobytes(), peer)
                    except ConnectionResetError:
                        continue
                    except (AttributeError, OSError):
                        break  # הסוקט נסגר - יציאה נקייה
        except Exception:
            if self.is_running:
                self.log("Input device disconnected.")
            self._device_failed = True   # קובע שידווח לשרת "אוזניות נשלפו"

    def stop(self) -> None:
        """Safely shuts down sockets and resets state flags."""
        was_connected = self.is_running
        self.is_running = False

        # דיווח לשרת לפני שסוגרים, כדי שישחרר את הסלוט מיד.
        # ההבחנה חשובה: ניתוק עמית / timeout אינו שליפת אוזניות, ודיווח
        # "disconnected" עליו יצר בשרת חיווי שקרי (ומיד אחריו "connected"
        # חוזר) - בדיוק ה"ריצוד" שהסתיר את האירועים האמיתיים.
        if was_connected or self.sock is not None:
            status = STATUS_DISCONNECTED if self._device_failed else STATUS_LEFT
            self._send_signalling({"id": self.my_id, "status": status})

        # ממתינים לתרדי האודיו לפני סגירת הסוקט. סגירה מתחת לרגליהם משאירה
        # את כרטיס הקול תפוס ומייצרת חריגות מטעות.
        # סגירת stream על התקן שנשלף עלולה להיתקע, ולכן ההמתנה ארוכה יחסית:
        # התרד עדיין מחזיק את מונה ה-streams, אז רענון חומרה ייחסם עד שיסיים.
        current = threading.current_thread()
        if self.hb_thread is not None and self.hb_thread.is_alive() and self.hb_thread is not current:
            self.hb_thread.join(timeout=1.0)
        self.hb_thread = None

        for t in (self.rx_thread, self.tx_thread):
            if t is not None and t.is_alive() and t is not current:
                t.join(timeout=STREAM_CLOSE_WAIT)
                if t.is_alive():
                    self.log("Audio thread still closing the device; will not touch PortAudio.")
        self.rx_thread = None
        self.tx_thread = None

        if self.sock:
            try:
                self.sock.close()
            except OSError:
                pass
            finally:
                self.sock = None

        with self.peer_lock:
            self.assigned_peer = None
            self.locked_peer = None
            self.tx_peer = None
        self.peer_id = ""

        if was_connected:
            self.log("Intercom connection closed. Waiting for reconnect...")


if __name__ == "__main__":
    cli = IntercomCLI()
    cli.start()
