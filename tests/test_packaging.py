import subprocess
import sys


def test_core_library_imports_no_web_framework() -> None:
    """`pip install dora-routes` users must not need FastAPI: the core never imports it, even indirectly."""
    code = (
        "import sys, dora_routes, dora_routes.config, dora_routes.segments; "
        "print(sorted({m.split('.')[0] for m in sys.modules} & {'fastapi', 'starlette', 'uvicorn'}))"
    )
    out = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, check=True).stdout
    assert out.strip() == "[]"
