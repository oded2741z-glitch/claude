# versions

Frozen snapshots. Nothing here is imported or executed — the code that runs
is `intercom_manager.py` and `intercom_client.py` in the project root.

Each folder is a byte-for-byte copy of the root files at the moment it was
saved, and is never edited afterwards.

| folder | commit | branch | contents |
|---|---|---|---|
| `v1` | `055a883` | `version/v1` | Green theme, TCP control plane, jitter-buffered audio, client tray checkbox. 156 checks passing. |

To restore a snapshot over the live files:

    cp versions/v1/*.py .

To compare the live code against a snapshot:

    diff versions/v1/intercom_manager.py intercom_manager.py
