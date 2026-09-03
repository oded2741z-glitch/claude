"""בדיקת חומרת אודיו לצד הלקוח.

מריצים על המחשב שבו רץ clint.py, ואז מחברים ושולפים את האוזניות ורואים
בזמן אמת מה sounddevice/PortAudio באמת רואה. זו הדרך המהירה להבין למה
חיבור אוזניות לא מייצר חיווי: ההתקן לא מגיע בכלל, רשימת ההתקנים לא
מתרעננת, או שהאוזניות אינן התקן ברירת המחדל.

    python audio_check.py                 # התקני ברירת המחדל
    python audio_check.py "USB Audio"     # התקן מסוים לפי חלק מהשם
    python audio_check.py 3 4             # לפי אינדקס (קלט, פלט)
"""
import sys
import time

import sounddevice as sd

SAMPLE_RATE = 16000
CHANNELS = 1
DTYPE = 'int16'
POLL = 2.0


def as_device(text):
    """ריק = ברירת המחדל. מספר = אינדקס. אחרת: חלק משם ההתקן."""
    if text is None:
        return None
    text = str(text).strip()
    if not text:
        return None
    try:
        return int(text)
    except ValueError:
        return text


def dump_devices():
    try:
        devices = sd.query_devices()
    except Exception as e:
        print(f"  query_devices failed: {type(e).__name__}: {e}")
        return
    try:
        default_in, default_out = sd.default.device
    except Exception:
        default_in = default_out = None
    for index, dev in enumerate(devices):
        marks = []
        if index == default_in:
            marks.append("DEFAULT-IN")
        if index == default_out:
            marks.append("DEFAULT-OUT")
        print(f"  [{index:2}] in={dev['max_input_channels']:2} out={dev['max_output_channels']:2}"
              f"  {dev['name']}  {' '.join(marks)}")


def device_slot(wanted, kind, devices):
    """ההתקן שישמש בפועל: (אינדקס, שם). זהה ללוגיקה שב-clint.py.

    אותו התקן מופיע בכמה host APIs, ולכן חיפוש לפי שם דרך sounddevice זורק
    ValueError ("Multiple devices found") במקום לבחור אחד.
    """
    key = "max_input_channels" if kind == "input" else "max_output_channels"
    slot = 0 if kind == "input" else 1

    def default_index():
        try:
            value = sd.default.device[slot]
        except Exception:
            return -1
        return value if isinstance(value, int) else -1

    if wanted is None:
        index = default_index()
        if 0 <= index < len(devices) and devices[index].get(key, 0) >= CHANNELS:
            return index, devices[index]["name"]
        return None, ""
    if isinstance(wanted, int):
        if 0 <= wanted < len(devices) and devices[wanted].get(key, 0) >= CHANNELS:
            return wanted, devices[wanted]["name"]
        return None, ""
    text = str(wanted).lower()
    matches = [i for i, d in enumerate(devices)
               if d.get(key, 0) >= CHANNELS and text in d["name"].lower()]
    if not matches:
        return None, ""
    index = default_index()
    preferred = devices[index]["hostapi"] if 0 <= index < len(devices) else None
    for i in matches:
        if preferred is not None and devices[i].get("hostapi") == preferred:
            return i, devices[i]["name"]
    return matches[0], devices[matches[0]]["name"]


def check(mic, speaker):
    """מחזיר (יש התקן?, הסבר) - בדיוק הבדיקה שהלקוח עצמו מריץ."""
    try:
        devices = sd.query_devices()
    except Exception as e:
        return False, f"query_devices failed: {type(e).__name__}: {e}"

    speaker_id, speaker_name = device_slot(speaker, "output", devices)
    mic_id, mic_name = device_slot(mic, "input", devices)
    if speaker_id is None:
        return False, f"no output device matching {speaker or 'system default'}"
    if mic_id is None:
        return False, f"no input device matching {mic or 'system default'}"
    try:
        sd.check_output_settings(device=speaker_id, samplerate=SAMPLE_RATE,
                                 channels=CHANNELS, dtype=DTYPE)
    except Exception as e:
        return False, f"output [{speaker_id}] {speaker_name}: {type(e).__name__}: {e}"
    try:
        sd.check_input_settings(device=mic_id, samplerate=SAMPLE_RATE,
                                channels=CHANNELS, dtype=DTYPE)
    except Exception as e:
        return False, f"input [{mic_id}] {mic_name}: {type(e).__name__}: {e}"
    return True, f"in [{mic_id}] {mic_name}  |  out [{speaker_id}] {speaker_name}"


def refresh():
    """אותו רענון שהלקוח עושה. בלעדיו התקן שחובר אחרי העלייה לא נראה."""
    try:
        sd._terminate()
        sd._initialize()
        return True, ""
    except Exception as e:
        return False, f"{type(e).__name__}: {e}"


def main():
    mic = as_device(sys.argv[1] if len(sys.argv) > 1 else None)
    speaker = as_device(sys.argv[2] if len(sys.argv) > 2 else (sys.argv[1] if len(sys.argv) > 1 else None))

    print(f"sounddevice {sd.__version__}   |   {sd.get_portaudio_version()[1]}")
    print(f"looking for  mic={mic or 'system default'}   speaker={speaker or 'system default'}")
    print(f"format       {SAMPLE_RATE} Hz, {CHANNELS} ch, {DTYPE}\n")

    print("Devices at startup:")
    dump_devices()

    ok, why = check(mic, speaker)
    print(f"\nCheck now: {'HEADPHONES PRESENT' if ok else 'NO DEVICE'} -> {why}")
    print("\nPlug the headphones in and out. Every change is printed below.")
    print("If nothing is printed when you plug them in, PortAudio never sees the")
    print("device - that is the bug, not the intercom.  (Ctrl+C to stop)\n")

    print("\nThe line below is what the client uses. If it names a Realtek/NVIDIA")
    print("device while the USB adapter is plugged in, put the adapter's name in")
    print('settings.txt ("mic" / "speaker") so the client stops following the')
    print("system default.\n")

    last = None
    while True:
        refreshed, error = refresh()
        if error:
            print(f"[{time.strftime('%H:%M:%S')}] device list refresh FAILED: {error}")
        try:
            count = len(sd.query_devices())
        except Exception:
            count = -1
        ok, why = check(mic, speaker)
        state = (ok, count)
        if state != last:
            last = state
            print(f"[{time.strftime('%H:%M:%S')}] {'PRESENT' if ok else 'MISSING'}  "
                  f"({count} devices)  {why}")
            if not ok:
                dump_devices()
        time.sleep(POLL)


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\nstopped.")
