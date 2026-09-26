# Backups

One directory per frozen version of the application, each a straight copy
of the working files at the moment it was taken, with a `VERSION.md`
naming the source commit, the app version and the checksums.

| Version | App | Taken | Source commit | Highlights |
| --- | --- | --- | --- | --- |
| [`V1`](V1/VERSION.md) | 2.4.0 | 2026-09-22 | `bce5eb7` | four sensor types: Ouster, camera (ONVIF, multi-screen), Arbe radar, IMU / INS |
| [`V2`](V2/VERSION.md) | 2.6.0 | 2026-09-22 | `bdcb2a4` | compare a sensor with the project; baseline (legacy) settings |
| [`V3`](V3/VERSION.md) | 2.13.0 | 2026-09-23 | `453e8ee` | 3D mount layout, STL models, real (calibration) pose, 2D drag mode |
| [`V4-record`](V4-record/VERSION.md) | 2.15.0 | 2026-09-26 | `1aa2425` | **record** - editable equipment types, simpler forms, 3D sensor models |

Each version also gets a matching git tag. `V1`, `V2`, `V3` and `V4-record`
exist locally but
could not be pushed - this environment's credentials are refused
(HTTP 403) when pushing tags - so read a snapshot by path or by its commit
until the tags are pushed from a checkout that is allowed to:

```bash
git show c4a67d9:backup/V1/ouster_gui.py | less
git show HEAD:backup/V2/ouster_gui.py | less
git show HEAD:backup/V3/ouster_gui.py | less
git show HEAD:backup/V4-record/ouster_gui.py | less
```
