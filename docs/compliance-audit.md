# Submission compliance

Reviewed 25 October 2026 against the Community Scripts contributor, template,
loading, metadata, platform, and update documentation.

## Current implementation

- Core owns discovery, selection, assignment, permissions and the DVB banner.
  Plex and TVHeadend scripts supply application defaults and lifecycle hooks.
- Installers use the prepared runtime. `lib/tools.func` exposes the DVB helpers;
  its generated API inventory includes 14 DVB functions. Both prefetch manifests
  register the three modules. Host selection loading uses the Core resolver.
- Persisted update helpers are emitted from loaded functions without separate
  downloads. Application bootstraps, update guards and standard restarts remain.
- DVB hardware management requires root on either backend. Non-root Incus DVB
  requests fail before creation or device mutation; ordinary non-DVB builds
  retain non-root support. Ownership changes use a shared host lock.
- New modules pass formatting checks. DVB state and override variables use
  lowercase; `ENABLE_DVB` and `_TOOLS_DVB_LOADED` follow upstream conventions.
- Each prepared upstream branch includes regression tests and a workflow.
- DVB settings are host capabilities, not installer `app_vars`; see
  [community-scripts.org submission notes](community-scripts-site-notes.md).

## Validation and submission limits

The supporting suite has 182 tests, passing in root and non-root runs with
expected privilege-specific skips. Eleven patches apply and parse. Prefetch,
public API, formatting and blocking-level ShellCheck checks pass. Live Proxmox
previews, conflict rejection and daemon permission checks pass; normal operation
is confirmed by the operator. See [validation](validation.md).

Fresh Community Scripts creation and real package-upgrade/failure trials remain
unverified for this revision. The operator has no Incus host, so Incus hardware
validation is unavailable. GitHub-hosted CI and website publication are pending.
Upstream acceptance, including the explicit root requirement for DVB on Incus,
remains subject to maintainer review.

## References

- [Contribution rules](https://community-scripts.org/docs/contribution/contributing)
- [Review checklist](https://community-scripts.org/docs/contribution/code-audit)
- [Install libraries](https://community-scripts.org/docs/misc/libraries)
- [Source loading](https://community-scripts.org/docs/misc/loading)
- [Platform contracts](https://community-scripts.org/docs/misc/platforms)
- [Website metadata](https://community-scripts.org/docs/contribution/templates_json/appname)
- [Core contribution guide](https://github.com/community-scripts/core/blob/main/docs/contributing.md)
