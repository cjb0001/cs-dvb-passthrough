"""Prepare two local upstream feature branches from the pinned baselines.

Usage: python3 tools/prepare_upstream_prs.py upstream-downloads
The checkouts must be clean. No commit, push, or PR is created.
"""
import json
from pathlib import Path
import subprocess
import sys

root = Path(__file__).resolve().parents[1]
base = Path(sys.argv[1]).resolve()
revisions = json.loads((root / "community-scripts/upstream-revisions.json").read_text())
branches = {"core": "feat/dvb-device-assignment", "ProxmoxVE": "feat/plex-tvheadend-dvb"}
for repo, branch in branches.items():
    checkout = base / repo
    def git(*args):
        return subprocess.check_output(["git", "-C", str(checkout), *args], text=True).strip()
    if git("status", "--porcelain"):
        raise SystemExit(f"Refusing dirty checkout: {checkout}")
    if git("rev-parse", "HEAD") != revisions[repo]:
        raise SystemExit(f"Unexpected baseline: {checkout}")
    if git("branch", "--show-current") != branch:
        git("switch", "-c", branch)
    for patch in sorted((root / "community-scripts" / repo).glob("*/*_dvb.patch")):
        git("apply", "--check", str(patch))
        git("apply", str(patch))
    if repo == "core":
        for source in (root / "community-scripts/core/lib").glob("*.func"):
            (checkout / "lib" / source.name).write_bytes(source.read_bytes())
    for directory in ("tests", ".github"):
        source_root = root / "community-scripts" / repo / directory
        for source in source_root.rglob("*"):
            if source.is_file():
                destination = checkout / directory / source.relative_to(source_root)
                destination.parent.mkdir(parents=True, exist_ok=True)
                destination.write_bytes(source.read_bytes())
    git("add", "--all")
    print(f"{repo}: {branch}")
    print(git("diff", "--cached", "--stat"))
