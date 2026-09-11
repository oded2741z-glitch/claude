import os

CONFIG_FILE = "config.txt"
TARGETS_FILE = "targets.txt"
SNAPSHOTS_DIR = "snapshots"
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


SERVER_URL = load_config().get("server_url", DEFAULT_SERVER_URL).strip() or DEFAULT_SERVER_URL
