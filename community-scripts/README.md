# Community Scripts integration draft

These files integrate opt-in physical DVB selection with Community Scripts core, Plex, and TVHeadend. They are local drafts, not published upstream. Shared helpers, both update routes, the full standalone flow, and the TVHeadend installer are tested with fake hardware and mocked platform/package commands. Live Proxmox split-assignment previews, ownership rejection and daemon permission checks passed; see [validation report](../docs/validation.md). A fresh live upstream container build and real package upgrade were not performed.

Patch baselines are recorded in `upstream-revisions.json`: core `7a1183e15312fe275f1fbe84f4dbb5fd03525920` and ProxmoxVE `12769eb7c3361014b5a418e63f4ffefe39bec785`. Every patch was checked with `git apply --check` against those revisions. `tools/generate_community_patches.py` regenerates them from baseline downloads; `tools/sync_standalone_helpers.py` synchronizes the standalone embedded helpers.

The advanced DVB step offers a checklist of physical devices, with product names, serials, and current adapter numbers. A dualHD is one checklist item containing both adapters. The chosen IDs are carried to the backend as `dvb_tuners` / `var_dvb_tuners`; the backend does not prompt again when a choice is supplied.

With a saved choice, the checklist marks available saved devices `[Saved]` and leaves them checked. Other available devices are marked `[New]` and start unchecked. These labels compare against the saved selection for this target; first-time setup has no labels. A missing saved device does not relabel other saved devices as new.

For unattended builds, set `var_dvb=yes` and `var_dvb_tuners` to a comma-separated list of displayed USB identities or unique serials. Use `all` only when all attached DVB devices are intended. Example identities:

```bash
var_dvb=yes
var_dvb_tuners=usb:2040:0264:EXAMPLE_SOLO_SERIAL                       # soloHD
var_dvb_tuners=usb:2040:0264:EXAMPLE_SOLO_SERIAL,usb:2040:0265:EXAMPLE_DUAL_SERIAL # both
```

The PVE draft requires native `devN` support and creates only the selected character-device entries. The Incus draft creates only selected `unix-char` entries with `required=false`. Both use UID 0, container GID `var_dvb_gid` (default 44), and mode 0660. They do not mount the entire `/dev/dvb` directory, allow wildcard media access, load drivers, change host permissions, or rewrite group IDs. Related media nodes follow the selected physical device. Existing broad PVE DVB mappings cause a refusal for manual review.

Include `core/lib/dvb-selection.func` when applying the core patches. The standalone script embeds the same helper body so it remains a single downloadable file; a regression test checks that the copies match. After editing the shared library, synchronize that embedded section before testing.

Apply all Core patches, including the loader, API inventory and both prefetch manifests, and copy the three `lib/dvb*.func` libraries into the Core checkout. Include each repository's supplied tests and workflow. `tools/prepare_upstream_prs.py` performs this preparation against clean pinned checkouts. Apply both applications' launcher and installer patches to the ProxmoxVE checkout. The included `ct/plex.sh`, `ct/tvheadend.sh`, and `install/tvheadend-install.sh` are local examples with changes already present; do not apply their patches a second time to these examples.

The loader supports a local `COMMUNITY_SCRIPTS_CORE_DIR` checkout or `COMMUNITY_SCRIPTS_CORE_URL`; the new helper files must exist in that source. For container-side installation, a local core directory must be accessible inside the container; a host-only path is insufficient. Set the core and scripts source URLs to your patched sources when testing a fork. These additions are not available from the default upstream URL until accepted and published there.

The backends take the shared host assignment lock, rediscover/recheck selected hardware, reject physical ownership conflicts, and save compatible selection/device manifests. Proxmox uses a pending journal; Incus records device ownership before additions so partial operations can be removed by the standalone utility. Creation and standalone setup share the same ownership checks and USB power policy. A source Proxmox container must be stopped/started to revoke its old live nodes before released tuners can be reassigned.

Creation uses the chosen current kernel device paths. On Proxmox, for persistent serial-based mappings across host renumbering, run the standalone utility's `--stable` setup against the running container after installation, retaining the same selected IDs. The community creation draft does not install the standalone host alias-refresh infrastructure itself. Incus does not currently support this serial-based stable mode; rerun the standalone script on the host to select devices again after adapter renumbering. Its `required=false` devices hotplug at their configured source paths. The Incus creation hook runs during hardware setup, independently of network checks, and validates every selected node before adding any of them.

## Plex installation and updates

The installer saves the container permission helper at `/usr/local/lib/community-scripts/dvb.func`. It grants `plex` membership in the groups owning the selected private nodes and checks read/write access. It preserves existing group numbers and does not chmod host-backed nodes. It restarts an already-running Plex service only if membership changed. Shared helper upgrades back up recognized owned files and refuse administrator-owned files.

Include `core/lib/dvb-banner.func` in the core source alongside the selection and permissions helpers. Plex installation persists it inside the container and installs `/etc/profile.d/10-dvb-details.sh`, with a physical-device roster at `/var/lib/dvb-passthrough/banner-devices.tsv`. Login shows product names and tuner counts once per physical device. Missing or partially available devices remain visible. The renderer reads local character-device metadata and sysfs registrations, opens no tuner, and is silent for noninteractive sessions. Its profile is independent of the standard Community Scripts banner.

The launcher's `update_script()` continues to upgrade Plex through APT, then checks the already-configured devices. It preserves the host selection and stable map, invokes an existing stable discovery helper if installed, and reuses the persisted permissions helper. Lost group membership is restored before the existing upstream package-update restart. Older containers without that permissions helper still receive an access check; containers without DVB continue through the ordinary update path. A failure is reported explicitly instead of claiming the complete update was verified.

When the banner module is installed, the update route refreshes the owned profile while preserving its existing roster, including currently missing devices. A missing profile is recreated. Administrator-owned files cause a clear failure rather than being overwritten. Both the installer and update route use backups inside `/var/backups/dvb-passthrough/`.

Updates run inside the container and do not discover host tuners, apply an `all` selection, write host configuration, or replace the stable mapping helper. Changing physical devices remains a host-side selection operation. The tests mock APT and systemd; they do not perform a real Plex upgrade or a channel/recording test.

## TVHeadend installation and updates

TVHeadend uses the same opt-in `var_dvb`, physical selection, permission helper, and separate DVB login banner. The default account is `hts`, resolved from the actual daemon UID or service defaults for custom installations. `var_dvb_gid` controls the private-node group during creation without rewriting any existing GID. The core patches carry `ENABLE_DVB` into both platforms' installer environments, so an explicitly enabled installation fails if assigned character nodes are missing.

The install hook runs after APT creates the service account and before the installer's explicit service start. It repairs missing membership, installs owned helpers with backups, and restarts a package-started service only if membership changed. It then verifies the running daemon's effective UID and groups as well as account read/write access.

The update hook refreshes an existing alias helper, preserves the banner roster, restores permissions before the existing upstream restart, and verifies the daemon afterward. Package/helper/service/access failures are reported distinctly. No second update restart is added. Containers without DVB keep the ordinary update path. Older containers without the permissions module still receive access checks using the running daemon's account.

The new tests execute the installer and update routes against isolated fake nodes and mocked package/service commands. They do not install TVHeadend on the test machine or prove RF reception. See [Plex and TVHeadend guide](../docs/plex-tvheadend-setup.md) for existing-container usage, split assignments, migration, and the manual HDHomeRun handoff to Plex.

DVB host management requires root on either backend. Non-root Incus DVB requests
are rejected before container creation; ordinary non-DVB builds retain non-root
support. The prepared Core branch exposes DVB helpers through `lib/tools.func`
and includes its regenerated API inventory. Installer hooks use the prepared
runtime, and emitted update helpers require no separate network download.
