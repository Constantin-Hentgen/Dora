# BRouter profiles

Custom BRouter routing profiles (`.brf`) used by Dora. They make BRouter *prefer* the right ways while routing (quiet
paths for running, quiet roads for cycling…) instead of only re-ranking finished loops.

Each profile is mounted into the BRouter container next to the built-in ones (see `docker-compose.yml`), so it is
requested by its file name without the extension (`profile=running-deserted`). A new profile needs a line in
`docker-compose.yml`; a test checks that every profile an activity uses is there.

| Profile | Activity | Since |
|---|---|---|
| `running-deserted.brf` | running | v0.4.0 |

The public brouter.de cannot load custom profiles: Dora then falls back to BRouter's `hiking-mountain` for running.
How the profiles are built and measured: wiki page "Profiles". Compare two profiles on the same start points with
`uv run python scripts/compare_profiles.py --before hiking-mountain --after running-deserted`.

The profiles are derived from BRouter's built-in ones (MIT, <https://github.com/abrensch/brouter>).
