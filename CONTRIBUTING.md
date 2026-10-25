# Contributing

## Development setup

Use Linux or WSL with Bash, Python 3, and the standard utilities documented in
the README. Run the regression suite from the repository root:

```bash
python3 -m unittest discover -s tests -v
```

When available, also run ShellCheck against the standalone utility and shared
shell helpers:

```bash
shellcheck -S warning dvb-passthrough.sh community-scripts/core/lib/*.func
```

## Community Scripts patches

The patch baselines are pinned in
`community-scripts/upstream-revisions.json`. See `tools/README.md` for the
generation and validation workflow. If a shared helper changes, synchronize
its embedded copy in the standalone script before running the tests.

Keep unrelated upstream formatting out of generated patches. Document any
baseline revision change and regenerate every affected example and patch.

## Pull requests

- Keep device discovery and assignment safe by default.
- Add regression coverage for behavior changes and failure paths.
- Do not include credentials, private addresses, hardware serial numbers, or
  host backups in fixtures, documentation, logs, or screenshots.
- State which checks ran and identify any hardware validation that remains.
