import os

CONFIG_FILE = "config.txt"
TARGETS_FILE = "targets.txt"
SNAPSHOTS_DIR = "snapshots"
DISPLAYS_FILE = "displays_map.txt"
DEFAULT_SERVER_URL = "http://127.0.0.1:5000"


def load_config():
    config = {"show_header": "True", "show_animation": "True", "server_url": DEFAULT_SERVER_URL}
    if os.path.exists(CONFIG_FILE):
        try:
            with open(CONFIG_FILE, "r", encoding="utf-8") as f:
                for line in f:
                    if "=" in line:
                        k, v = line.strip().split("=", 1)
                        config[k] = v
        except Exception:
            pass
    return config


def update_config(updates, remove=()):
    config = load_config()
    config.update(updates)
    for k in remove:
        config.pop(k, None)
    try:
        with open(CONFIG_FILE, "w", encoding="utf-8") as f:
            for k, v in config.items():
                f.write(f"{k}={v}\n")
    except Exception:
        pass


def load_targets():
    targets = []
    if os.path.exists(TARGETS_FILE):
        try:
            with open(TARGETS_FILE, "r", encoding="utf-8") as f:
                for line in f:
                    if "|" in line:
                        name, url = line.strip().split("|", 1)
                        targets.append({"name": name, "url": url})
        except Exception:
            pass
    return targets


def load_display_nodes():
    nodes = {}
    if not os.path.exists(DISPLAYS_FILE):
        return nodes
    try:
        with open(DISPLAYS_FILE, "r", encoding="utf-8") as f:
            for line in f:
                l = line.strip()
                if not l or l.startswith("CONFIG"):
                    continue
                p = [x.strip() for x in l.split(",")]
                if len(p) < 10:
                    continue
                nodes[p[0]] = {
                    "label": p[0], "type": p[1], "info1": p[2], "info2": p[3],
                    "res": p[4], "offset": p[5], "primary": p[6]
                }
    except Exception:
        pass
    return nodes


def parse_offset(offset_text):
    try:
        parts = offset_text.split()
        x = int(parts[0].split(":")[1])
        y = int(parts[1].split(":")[1])
        return x, y
    except Exception:
        return None


SERVER_URL = load_config().get("server_url", DEFAULT_SERVER_URL).strip() or DEFAULT_SERVER_URL
