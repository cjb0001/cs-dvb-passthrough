# Current validation

Validated 25 October 2026.

## Running Proxmox deployment

| Application | Assigned hardware | Stable frontend paths |
| --- | --- | --- |
| Plex | Single-tuner USB device | `/dev/dvb/adapter1000/frontend0` |
| TVHeadend | Dual-tuner USB device | `/dev/dvb/adapter1001/frontend0`, `/dev/dvb/adapter1002/frontend0` |

The operator confirms normal operation. Independent checks on Proxmox VE 9.2
established:

- Stable dry runs for both saved assignments returned exit code 0.
- Assigning TVHeadend's dual tuner to Plex was refused with exit code 1.
- The revised runtime verifier passed against both running daemons, including
  effective identities, device access and supplementary groups.
- Both services remained active; container configuration checksums were unchanged.
- Both installed host utility copies were backed up and atomically upgraded:
  `/root/dvb-passthrough.sh` and `/usr/local/libexec/dvb-passthrough`.
- A dry run using the installed utility passed after replacement.

The installed copies have this validated SHA-256:

```text
7b83d5880ecfb1bdcfb92776f5dc2d5b220a867ffc93316de94ee0a293c17bf2
```

The local repository now includes a subsequent variable-naming cleanup that
has passed local validation but has not been deployed to the host.

The container permission/banner helpers were not upgraded by replacing the host
utility. Revised verification functions ran transiently inside the containers.

## Automated checks

- Supporting suite: 182 tests, passing under root and non-root with expected
  privilege-specific skips.
- Core branch tests: offline public-loader/helper emission and rejection of
  non-root Incus DVB requests before creation.
- Application branch tests: ordinary Plex and TVHeadend updates retain their
  upstream restart.
- Eleven patches apply and pass relevant shell syntax checks against the pinned
  baselines in `../community-scripts/upstream-revisions.json`.
- Core prefetch and generated API checks pass.
- New shared modules pass shfmt; reviewed shell sources pass blocking-level
  ShellCheck. Git whitespace checks pass.

## Validation limits

Fresh live Community Scripts builds, real package upgrades and failure/debug
trials have not been performed with this revision. The latest independent live
checks did not restart containers or retune hardware. RF reception, recordings,
EPG and Plex network streaming are outside these passthrough checks.

The operator has no Incus host. Incus hardware behavior remains unverified;
mocked tests and the real non-root rejection check are the available evidence.
GitHub-hosted CI has not run. No upstream PR has been published.
