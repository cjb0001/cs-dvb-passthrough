# Development tools

Run these commands from the repository root. These are development utilities; the user-facing host utility is `../dvb-passthrough.sh`.

| File | Purpose |
| --- | --- |
| `sync_standalone_helpers.py` | Copy shared core helper bodies into the standalone script |
| `generate_community_patches.py` | Generate integration patches and patched examples from downloaded upstream baselines |
| `prepare_upstream_prs.py` | Prepare focused local branches, including tests and workflows |
| `check_core_contracts.py` | Run upstream prefetch and public API checks |
| `validate_community_patches.py` | Apply patches to disposable baseline copies, check Bash syntax and compare generated sources |

```bash
python3 tools/sync_standalone_helpers.py
python3 tools/generate_community_patches.py /path/to/upstream-downloads
python3 tools/validate_community_patches.py /path/to/upstream-downloads
```

Use the upstream revisions in `community-scripts/upstream-revisions.json`. The baseline directory must contain complete `core/` and `ProxmoxVE/` checkouts at the pinned revisions. Generation writes repository patches/examples and a `patched/` directory below the baseline directory. Validation uses disposable copies.

Run the regression suite after synchronization or patch generation.

Prepare clean pinned upstream checkouts for review (creates local branches,
without committing or publishing):

```bash
python3 tools/prepare_upstream_prs.py upstream-downloads
python3 tools/check_core_contracts.py upstream-downloads/core
```

The checkouts must start clean at the pinned commits. Patch validation archives
the pinned commits, so it also works after the review branches are prepared.

DVB host management requires root on either backend. Non-root Incus DVB requests
are rejected before container creation; ordinary non-DVB builds retain non-root
support. The prepared Core branch exposes DVB helpers through `lib/tools.func`
and includes its regenerated API inventory. Installer hooks use the prepared
runtime, and emitted update helpers require no separate network download.
