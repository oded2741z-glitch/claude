"""End-to-end self test with fake audio devices. No sound card required.

    python tests/selftest.py

Runs a real signalling server and two real nodes over real UDP sockets on
localhost, with the audio devices replaced by fake ones, and checks that:

  1. the two nodes get matched, audio reaches both sides, and the status
     file reports the far side's state;
  2. `intercom = off` in the control file closes the call;
  3. `intercom = on` reopens it;
  4. a bare `off` / `on` in the switch file does the same without touching
     the other settings;
  5. unplugging the audio device ends the call and replugging recovers it;
  6. `command = quit` shuts a node down.

Exit code 0 means every check passed.
"""

import os
import sys
import threading
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import fake_sounddevice                      # noqa: E402
fake_sounddevice.install()                   # must precede the app imports

import build_single_file                     # noqa: E402
import txt_bridge                            # noqa: E402
from node import IntercomNode                # noqa: E402

PORT = 45999
WORKDIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "_selftest_run")

failures = []


def check(label: str, ok: bool) -> None:
    print(f"  [{'PASS' if ok else 'FAIL'}] {label}", flush=True)
    if not ok:
        failures.append(label)


def wait_until(predicate, timeout: float, interval: float = 0.1) -> bool:
    deadline = time.time() + timeout
    while time.time() < deadline:
        if predicate():
            return True
        time.sleep(interval)
    return predicate()


def write_control(path: str, **fields) -> None:
    txt_bridge.write_text_atomic(path, "\n".join(f"{k} = {v}" for k, v in fields.items()) + "\n")
    # ControlFile משווה (mtime, size); על מערכות קבצים גסות שינוי מהיר עלול
    # להיראות זהה, ולכן דוחפים את חותמת הזמן אחורה
    stat = os.stat(path)
    os.utime(path, (stat.st_atime, stat.st_mtime + 1))


def read_status(path: str) -> dict:
    try:
        with open(path, "r", encoding="utf-8") as f:
            return txt_bridge.parse_config_text(f.read()) or {}
    except OSError:
        return {}


def main() -> int:
    print("0. single-file bundles")
    check("intercom_A.py / intercom_B.py match the modules", build_single_file.check())

    os.makedirs(WORKDIR, exist_ok=True)
    os.chdir(WORKDIR)

    ctrl_a, ctrl_b = "control_A.txt", "control_B.txt"
    status_a, status_b = "status_A.txt", "status_B.txt"
    switch_a = "switch_A.txt"
    for stale in (ctrl_a, ctrl_b, status_a, status_b, switch_a):
        if os.path.exists(stale):
            os.remove(stale)

    common = {"server_ip": "127.0.0.1", "port": PORT, "local_mode": "on", "intercom": "on"}
    write_control(ctrl_a, my_id="node_A", signalling="on", **common)
    write_control(ctrl_b, my_id="node_B", signalling="off", **common)

    node_a = IntercomNode("a", ctrl_a, status_a, {}, switch_a)
    node_b = IntercomNode("b", ctrl_b, status_b, {})
    threads = [threading.Thread(target=n.run, daemon=True) for n in (node_a, node_b)]

    print("1. matching + live audio")
    for t in threads:
        t.start()
    ok = wait_until(lambda: read_status(status_a).get("state") == "live"
                    and read_status(status_b).get("state") == "live", 20)
    check("both nodes report state=live", ok)
    check("node A learned the peer id", read_status(status_a).get("peer_id") == "node_B")
    check("node B learned the peer id", read_status(status_b).get("peer_id") == "node_A")
    check("server reports the remote client connected",
          read_status(status_a).get("remote_ready") == "yes")

    heard = len(fake_sounddevice.played)
    check("audio frames are being played", wait_until(
        lambda: len(fake_sounddevice.played) > heard + 10, 10))

    print("2. intercom = off closes the call")
    write_control(ctrl_a, my_id="node_A", signalling="on", server_ip="127.0.0.1",
                  port=PORT, local_mode="on", intercom="off")
    check("node A goes idle", wait_until(
        lambda: read_status(status_a).get("state") == "idle", 15))
    quiet = len(fake_sounddevice.played)
    time.sleep(1.0)
    check("audio really stopped", len(fake_sounddevice.played) - quiet < 5)

    print("3. intercom = on reopens it")
    write_control(ctrl_a, my_id="node_A", signalling="on", server_ip="127.0.0.1",
                  port=PORT, local_mode="on", intercom="on")
    check("both nodes are live again", wait_until(
        lambda: read_status(status_a).get("state") == "live"
        and read_status(status_b).get("state") == "live", 25))

    print("4. the one-word switch file")
    txt_bridge.write_text_atomic(switch_a, "off\n")
    check("a bare 'off' closes the call", wait_until(
        lambda: read_status(status_a).get("state") == "idle", 15))
    check("the other settings survived the switch",
          node_a.cfg.get("server_ip") == "127.0.0.1" and node_a.cfg.get("my_id") == "node_A")
    txt_bridge.write_text_atomic(switch_a, "on\n")
    check("a bare 'on' reopens it", wait_until(
        lambda: read_status(status_a).get("state") == "live"
        and read_status(status_b).get("state") == "live", 25))

    print("5. unplugging the audio device")
    fake_sounddevice.set_available(False)
    check("call drops when the device disappears", wait_until(
        lambda: read_status(status_a).get("state") != "live", 20))
    fake_sounddevice.set_available(True)
    check("call recovers when the device returns", wait_until(
        lambda: read_status(status_a).get("state") == "live"
        and read_status(status_b).get("state") == "live", 30))

    print("6. command = quit")
    write_control(ctrl_b, my_id="node_B", signalling="off", server_ip="127.0.0.1",
                  port=PORT, local_mode="on", intercom="on", command="quit")
    check("node B exits", wait_until(lambda: not threads[1].is_alive(), 20))

    node_a.stop_event.set()
    threads[0].join(timeout=20)
    check("node A exits", not threads[0].is_alive())

    print()
    if failures:
        print(f"FAILED ({len(failures)}): " + "; ".join(failures))
        return 1
    print("All checks passed.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
