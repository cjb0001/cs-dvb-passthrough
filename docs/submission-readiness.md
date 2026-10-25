# Submission readiness

Updated 25 October 2026, including the completed lowercase-variable cleanup.

## Implemented

- Separate Core and Plex/TVHeadend feature branches, with focused upstream diffs.
- Public `lib/tools.func` loading of DVB permission and banner helpers; generated
  `lib/API.txt` includes all 14 newly exposed functions.
- Installer use of prepared helpers, with no separate DVB library download.
  Persisted update helpers are emitted from the loaded function definitions.
- Host and container prefetch registration and resolver-based host selection loading.
- Explicit privileged DVB boundary on Incus: non-root requests fail before
  creation or device mutations. The global ownership lock stays shared. Ordinary
  non-DVB Incus builds do not acquire this restriction.
- New DVB state, selection and override variables use lowercase. `ENABLE_DVB`
  and `_TOOLS_DVB_LOADED` follow existing upstream capability/loader conventions.
- Formatted new modules and synchronized standalone embedded helper bodies.
- Portable tests and a dedicated workflow in each prepared upstream branch.
- Correct website guidance: host capability values are not installer `app_vars`.

## Verification

- Supporting regression suite: 182 tests in both root and non-root runs.
  Expected skips: one under root and two under non-root; all executed tests pass.
- Non-root boundary test and offline public-loader test pass separately.
- Application update regression passes for both Plex and TVHeadend.
- Eleven patches apply and parse against pinned baselines.
- Upstream prefetch and loader API checks pass.
- New module shfmt checks and blocking-level ShellCheck pass.

## Outstanding runtime and publication checks

The deployed revision passed live Proxmox stable previews, ownership-conflict
rejection and actual daemon permission checks. The operator confirms normal
operation. Both installed host utility copies were backed up and upgraded.
The subsequent lowercase-variable cleanup passes local validation but has not
been deployed or independently tested on the live host.

Fresh Community Scripts creation and real package upgrade/failure trials remain
unperformed. The operator has no Incus host, so real Incus hardware validation
is unavailable. GitHub-hosted workflows have not run. Notes for community-scripts.org have not
been submitted. These gaps prevent a claim of complete upstream readiness.

See [compliance assessment](compliance-audit.md) and
[validation evidence](validation.md). The application guide is
[Plex and TVHeadend setup](plex-tvheadend-setup.md).
