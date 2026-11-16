# BRouter profiles

Custom BRouter routing profiles (`.brf`) used by Dora. They make BRouter *prefer* the right ways while routing (quiet
paths for running, quiet roads for cycling…) instead of only re-ranking finished loops.

`brouter.Dockerfile` copies every `*.brf` here into the BRouter image next to the built-in profiles, so a profile is
requested by its file name without the extension (`profile=running-deserted`). Nothing else to register: add the file,
rebuild (`docker compose up -d --build`). A test checks that every profile an activity uses exists.

| Profile | Activity | Since |
|---|---|---|
| `running-deserted.brf` | running | v0.4.0 |

The public brouter.de cannot load custom profiles: Dora then falls back to BRouter's `hiking-mountain` for running.
How the profiles are built and measured: wiki page "Profiles". Compare two profiles on the same start points with
`uv run python scripts/compare_profiles.py --before hiking-mountain --after running-deserted`.

The profiles are derived from BRouter's built-in ones (MIT, <https://github.com/abrensch/brouter>).
