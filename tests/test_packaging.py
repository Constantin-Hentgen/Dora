import subprocess
import sys
from pathlib import Path

from dora_routes.activities import ACTIVITIES

# Profiles that ship with BRouter (profiles2/). The public brouter.de has these and nothing else.
BUILT_IN_PROFILES = {"hiking-mountain", "trekking", "fastbike", "gravel", "mtb", "shortest"}


def test_core_library_imports_no_web_framework() -> None:
    """`pip install dora-routes` users must not need FastAPI: the core never imports it, even indirectly."""
    code = (
        "import sys, dora_routes, dora_routes.config, dora_routes.segments; "
        "print(sorted({m.split('.')[0] for m in sys.modules} & {'fastapi', 'starlette', 'uvicorn'}))"
    )
    out = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, check=True).stdout
    assert out.strip() == "[]"


ROOT = Path(__file__).resolve().parent.parent


def test_every_custom_profile_is_shipped_and_mounted_in_the_brouter_container() -> None:
    """An activity asks BRouter for a profile by name: without its file in `profiles/` and a mount in compose, every
    request would fail with `unknown profile`."""
    compose = (ROOT / "docker-compose.yml").read_text()
    custom = {a.brouter_profile for a in ACTIVITIES.values()} - BUILT_IN_PROFILES
    assert custom, "no custom profile used: update this test"
    for name in custom:
        assert (ROOT / "profiles" / f"{name}.brf").is_file(), f"profiles/{name}.brf is missing"
        assert f"./profiles/{name}.brf:/profiles2/{name}.brf:ro" in compose, f"{name}.brf is not mounted"
    for activity in ACTIVITIES.values():
        if activity.public_brouter_profile:
            assert activity.public_brouter_profile in BUILT_IN_PROFILES


def test_running_profile_has_its_tuning_knobs_documented_in_the_file() -> None:
    text = (ROOT / "profiles" / "running-deserted.brf").read_text()
    for knob in ("road_penalty_primary", "crowded_penalty", "sidewalk_penalty", "crossing_cost"):
        assert f"assign   {knob}" in text
