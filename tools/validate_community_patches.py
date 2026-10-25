"""Apply all integration patches to disposable copies of downloaded baselines.

Usage: python3 tools/validate_community_patches.py /path/to/upstream-downloads
See community-scripts/upstream-revisions.json for the required revisions.
"""

from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import tarfile
import io
import json


ROOT = Path(__file__).resolve().parents[1]
baseline = Path(sys.argv[1]).resolve()
count = 0
with tempfile.TemporaryDirectory(prefix="dvb-patch-check-") as temporary:
    for repository in ("core", "ProxmoxVE"):
        checkout = Path(temporary) / repository
        source = baseline / repository
        if (source / ".git").exists():
            revision = json.loads((ROOT / "community-scripts/upstream-revisions.json").read_text())[repository]
            archive = subprocess.check_output(["git", "archive", revision], cwd=source)
            checkout.mkdir()
            with tarfile.open(fileobj=io.BytesIO(archive)) as contents:
                contents.extractall(checkout, filter="data")
        else:
            shutil.copytree(source, checkout)
        for patch in sorted((ROOT / "community-scripts" / repository).glob("*/*_dvb.patch")):
            subprocess.run(["git", "apply", "--check", str(patch)], cwd=checkout, check=True)
            subprocess.run(["git", "apply", str(patch)], cwd=checkout, check=True)
            targets = [line.removeprefix("+++ b/") for line in patch.read_text().splitlines() if line.startswith("+++ b/")]
            for target in targets:
                if target.endswith((".func", ".sh")):
                    subprocess.run(["bash", "-n", str(checkout / target)], check=True)
                generated = baseline / "patched" / repository / target
                if generated.exists() and generated.read_bytes() != (checkout / target).read_bytes():
                    raise SystemExit(f"Applied patch differs from generated source: {target}")
            count += 1
print(f"Applied and syntax-checked {count} patches against disposable baseline copies.")
