"""Run upstream prefetch and API checks in a prepared Core checkout."""
from pathlib import Path
import os
import subprocess
import sys

checkout = Path(sys.argv[1]).resolve()
workflow = (checkout / ".github/workflows/verify-split-files.yml").read_text()
for name in ("Prefetch lists cover every engine file", "Each loader still defines its whole API"):
    section = workflow.split("      - name: " + name + "\n", 1)[1]
    payload = section.split("        run: |\n", 1)[1].split("\n      - name:", 1)[0]
    script = "\n".join(line[10:] if line.startswith("          ") else line for line in payload.splitlines())
    subprocess.run(["bash", "-c", script], cwd=checkout,
                   env=os.environ | {"ENGINE_DIRS": "core lxc host api lib ui vm pve incus"},
                   check=True)
    print(name + ": passed")
