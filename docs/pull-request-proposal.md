# Community Scripts DVB passthrough proposal

Two coordinated PRs:

| Repository | Branch | Title |
| --- | --- | --- |
| `core` | `feat/dvb-device-assignment` | `feat(dvb): add physical tuner selection and assignment for Proxmox VE and Incus` |
| `ProxmoxVE` | `feat/plex-tvheadend-dvb` | `feat(plex,tvheadend): support selected DVB tuners during installation and updates` |

Core must be merged and published before the application PR: the application
installers require the new shared helpers, while both backends require the
new assignment implementation. Review can proceed together using local source
overrides. The standalone utility, firmware catalog, development tools, and
project documentation belong in this supporting repository.


This change connects selected initialized USB/PCIe DVB tuners to Proxmox or Incus containers, with equivalent creation hooks for Plex and TVHeadend. Direct Plex operation remains supported; TVHeadend can own the tuners and separately supply network streams to Plex through its HDHomeRun interface.

The implementation is local draft work. It has not been submitted or published upstream.

## Behavior

- Advanced Setup offers an opt-in physical-device checklist. `var_dvb` defaults to `no`; `var_dvb_tuners` accepts explicit identities or `all`. Every adapter of a selected physical device is included.
- Host backends recheck selection and ownership under a shared lock. They reject devices assigned to other containers, including native mappings, broad mounts, inherited Incus devices, numeric character mappings, and overlapping live nodes.
- Proxmox uses native per-device `devN` entries. Incus uses selected `unix-char` devices with `required=false`. Nodes use UID 0, the configured in-container GID, and mode 0660. Existing numeric group IDs and unrelated devices are preserved.
- Compatible device/selection manifests allow later standalone reconciliation and recovery. Proxmox journals pending native entries; Incus records ownership before additions.
- USB autosuspend rules cover the union of all managed assignments. Exact serial identities retain reconnect rules when absent; path identities use verified port/vendor/product rules. Changes are backed up.
- Both installers use the same permission and banner helpers. They add the media-service account to existing node-owning groups when needed, without granting root-group membership or changing host node modes. An explicitly enabled installation reports missing devices as an error.
- TVHeadend resolves its service account (normally `hts`), including custom daemon/service defaults. Its update hook repairs membership before the existing upstream restart, preserves host selections and banner rosters, and verifies the daemon's effective UID and groups afterward. Plex retains its existing update behavior, preserving its existing package-update restart.
- A separate DVB login profile reports selected physical devices and complete/partial/missing availability. It opens no tuner and stays silent in noninteractive shells.

The standalone utility adds `--app plex|tvheadend`, with `--user` as an override and a saved application/account profile for reruns. Older targets still default to Plex. Stopped containers require an explicit device GID. Standalone operations never restart services or containers; incomplete runtime verification returns exit code 2.

Reassignment requires releasing the old mapping and stopping/starting a source Proxmox container to revoke its live nodes. The utility does not steal a tuner or restart a container automatically.

## Files and baselines

Patch baselines are pinned in `community-scripts/upstream-revisions.json`:

- `community-scripts/core`: `7a1183e15312fe275f1fbe84f4dbb5fd03525920`.
- `community-scripts/ProxmoxVE`: `12769eb7c3361014b5a418e63f4ffefe39bec785`.

Core changes include the Advanced UI, both backends, both prefetch manifests, the public library loader and API inventory, three DVB libraries, and tests/workflow.

Application changes comprise the Plex and TVHeadend launcher/installer patches. Local launchers and the TVHeadend installer are included as runnable examples; their patches apply to the upstream files, not to the already-modified examples.

The patched core and scripts sources must be available through the existing loader/source overrides. A host-local directory is accessible to an installer only if that directory is also available inside its container. Unpublished helpers cannot be fetched from the unmodified default upstream URL.

## Scope

This proposal automates device connection and verification. It does not configure TVHeadend networks, channel scans, EPG, recordings, HDHomeRun server settings, streaming access rules, or Plex DVR settings. It does not install a TVHeadend-to-Plex proxy.

Host firmware diagnosis/installation remains an independent explicitly selected standalone workflow. This proposal does not load/unload drivers, change module blacklists, install host packages, rewrite group IDs, apply global 0666 permissions, or mount the entire host DVB directory.

Persistent serial aliases remain an optional standalone Proxmox setup step after creation. This mode requires unique USB serials and complete device groups; PCIe, unusable serials, related media nodes, and Incus use ordinary paths. The creation hooks do not claim to install the stable host infrastructure.

## Validation

The Linux/WSL regression suite covers selection, stable identity, ownership/recovery, firmware, banners, Plex updates, TVHeadend profiles, full standalone application/removal, split assignments, stale live nodes, shared power policy, TVHeadend updates, and the TVHeadend installer. Hardware, package, service, and platform commands are mocked for local tests.

All eleven patches apply to their recorded baselines. Generated patched core files pass Bash syntax checks. The standalone utility, shared helpers, launchers, and TVHeadend installer pass blocking-level ShellCheck.

The current Proxmox deployment assigns the single tuner to Plex and the dual
tuner to TVHeadend. Stable previews, ownership-conflict rejection and runtime
permission checks pass. Both host utility copies are upgraded and match the
repository checksum. The operator confirms normal operation.

The supporting suite has 182 tests, passing with expected privilege-specific
skips. Fresh creation and package-upgrade/failure trials remain unverified for
this revision. Incus hardware testing is unavailable because the operator has
no Incus host. Hosted CI has not run. See [current validation](validation.md).

See `plex-tvheadend-setup.md`, `validation.md`, and `../community-scripts/README.md` for usage, deployment evidence, and the remaining live validation work.

DVB host management requires root on either backend. Non-root Incus DVB requests
are rejected before container creation; ordinary non-DVB builds retain non-root
support. The prepared Core branch exposes DVB helpers through `lib/tools.func`
and includes its regenerated API inventory. Installer hooks use the prepared
runtime, and emitted update helpers require no separate network download.
