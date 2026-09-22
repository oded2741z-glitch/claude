# Backups

One directory per frozen version of the application, each a straight copy
of the working files at the moment it was taken, with a `VERSION.md`
naming the source commit, the app version and the checksums.

| Version | App | Taken | Source commit |
| --- | --- | --- | --- |
| [`V1`](V1/VERSION.md) | 2.4.0 | 2026-09-22 | `bce5eb7` |

Each version also carries a matching git tag, so `git show V1:ouster_gui.py`
reads it without touching the working tree.
