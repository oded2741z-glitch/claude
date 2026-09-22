# Backups

One directory per frozen version of the application, each a straight copy
of the working files at the moment it was taken, with a `VERSION.md`
naming the source commit, the app version and the checksums.

| Version | App | Taken | Source commit |
| --- | --- | --- | --- |
| [`V1`](V1/VERSION.md) | 2.4.0 | 2026-09-22 | `bce5eb7` |

Each version also gets a matching git tag. `V1` exists locally but could
not be pushed - this environment's credentials are refused (HTTP 403) when
pushing tags - so read the snapshot by path or by its commit until the tag
is pushed from a checkout that is allowed to:

```bash
git show c4a67d9:backup/V1/ouster_gui.py | less
```
