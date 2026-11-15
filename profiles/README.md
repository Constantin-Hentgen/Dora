# BRouter profiles

Custom BRouter routing profiles (`.brf`) used by loopwise. They make BRouter *prefer* the right ways while routing
(quiet paths for running, quiet roads for cycling…) instead of only re-ranking finished loops.

Each profile is mounted into the BRouter container next to the built-in ones (see `docker-compose.yml`), so it can be
requested by its file name without the extension (`profile=running-deserted`).

Profiles arrive with milestone 4 (`running-deserted.brf`), then cycling and hiking.
