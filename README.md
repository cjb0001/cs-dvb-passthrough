# DVB TV Tuner Passthrough for Proxmox VE and Incus

`dvb-passthrough.sh` configures initialized USB or PCIe DVB tuners for an existing container, including Plex, Jellyfin, or Tvheadend containers. It also provides a separate host firmware diagnosis and optional installation workflow for supported tuners that cannot initialize.

## Requirements

- Run as root on the host with Bash and the usual Linux utilities, including `udevadm` and `flock`.
- Proxmox VE must support native `devN` container entries. Incus uses `unix-char` devices. If both platforms are detected, Proxmox takes precedence.
- Container passthrough requires working host drivers and firmware, with character devices under `/dev/dvb/adapter*`. Firmware diagnosis can run before those nodes exist and needs no container target.
- The container must have the intended service user. `--app tvheadend` resolves the TVHeadend account (normally `hts`); `--app plex` uses `plex`. An explicit `--user` overrides either. Reruns reuse the saved profile; targets without one still default to Plex. For a stopped container, supply its intended device group with `--gid`.
- Proxmox configurations with locks, snapshots, or pending sections are rejected for manual review. Avoid concurrent edits in the Proxmox UI while running this utility.

Hardware discovery uses kernel registrations rather than a vendor allowlist. Compatibility depends on the host driver and firmware for the exact device revision. Network tuners such as HDHomeRun do not need this device passthrough.

## Project status

The standalone utility and regression suite are ready for independent use and
review. Files under `community-scripts/` are a proposed upstream integration
against the pinned revisions in `community-scripts/upstream-revisions.json`.
They have not been merged upstream. See the [PR proposal](docs/pull-request-proposal.md)
and [validation report](docs/validation.md) for tested behavior and remaining
live validation.

## Usage

Copy the script to the host, then run:

```bash
# Select a container and review the plan interactively.
bash dvb-passthrough.sh

# Preview an existing Proxmox container without changing configuration.
bash dvb-passthrough.sh --dry-run 101

# Configure an Incus container for Jellyfin after reviewing the plan.
bash dvb-passthrough.sh --user jellyfin media

# Preview device connection for an existing TVHeadend container.
bash dvb-passthrough.sh --app tvheadend --dry-run 303

# Connect explicitly selected physical devices to TVHeadend on Proxmox.
# Release their old container assignments first; see docs/plex-tvheadend-setup.md.
bash dvb-passthrough.sh --app tvheadend --stable \
  --tuner EXAMPLE_SOLO_SERIAL --tuner EXAMPLE_DUAL_SERIAL 303

# Configure a stopped container using an existing in-container group ID.
bash dvb-passthrough.sh --gid 44 101

# Apply without the confirmation prompt.
bash dvb-passthrough.sh --all-tuners --yes 101

# Connect only the soloHD, using its persistent USB identity.
bash dvb-passthrough.sh --stable --tuner usb:2040:0264:EXAMPLE_SOLO_SERIAL 100

# Connect both physical devices (the dualHD includes its two adapters).
bash dvb-passthrough.sh --stable --tuner EXAMPLE_SOLO_SERIAL --tuner EXAMPLE_DUAL_SERIAL 100

# Keep USB tuners identified by serial across host adapter renumbering.
bash dvb-passthrough.sh --stable --user plex 101

# Remove the container entries tracked by this utility.
bash dvb-passthrough.sh --remove 101

# Diagnose recent missing-firmware requests without downloading anything.
bash dvb-passthrough.sh --firmware

# Review supported downloads; each installation defaults to No.
bash dvb-passthrough.sh --install-firmware

# Preview installation using a longer current-boot diagnostic window.
bash dvb-passthrough.sh --install-firmware --dry-run --firmware-since "2 hours ago"
```

| Option | Behavior |
| --- | --- |
| `--dry-run` | Discover devices and display the proposed target, GID, and paths without making changes. With `--remove`, report the removal intent. |
| `--yes` | Skip confirmation for the explicitly selected operation, including `--install-firmware`. It never enables firmware installation by itself. Supply a target for container operations to avoid target selection. |
| `--verbose` | Show each DVB node's hardware ancestry. |
| `--tuner ID` | Select one physical device by USB identity, unique serial, or displayed path ID. Repeat to select multiple devices. |
| `--all-tuners` | Explicitly select every currently discovered physical DVB device. Cannot be combined with `--tuner`. |
| `--stable` | Proxmox: persist USB serial mappings using stable numbered DVB aliases. Requires unique serials; see below. Existing mappings are reused automatically on reruns. |
| `--app plex\|tvheadend` | Select the application defaults and save the profile for reruns. TVHeadend also checks the actual running daemon's identity and device groups. |
| `--user NAME` | Override the service account, resolve its primary GID, and verify access as that user. Supports custom TVHeadend accounts and other media applications. |
| `--gid NUMBER` | Set the in-container device group explicitly. Required for stopped containers. Does not create groups or change user membership. |
| `--remove`, `-r` | Remove tracked container entries. Does not require tuner discovery or a service-user GID. |
| `--no-restart` | Accepted for compatibility. Every operation already avoids automatic restarts. |
| `--firmware` | Diagnose recent missing-firmware requests and display supported downloads. No network requests or host changes. |
| `--install-firmware` | Run the same diagnosis and offer supported downloads with explicit confirmation. Runs separately from container operations. |
| `--firmware-since TIME` | Set the journal window for firmware diagnosis, default `15 minutes ago`. Only the current boot is searched. |
| `--help`, `-h` | Print usage. |

Incus targets are container names on the local daemon. The standalone utility forces the local Unix socket, pins its selected project for the operation, and rejects VMs before changing devices. Run it on the machine hosting the container and tuners; clustered instances on another member are not supported. Hardware discovery runs locally.

### Choosing physical tuners

To use the standalone terminal checklist, set `dvb_selection_ui=whiptail`:

```bash
dvb_selection_ui=whiptail bash dvb-passthrough.sh --dry-run 101
```


Interactive runs list physical USB/PCI devices, serials, and their current adapter numbers. Enter one or more device numbers, or `all`. For example, a dualHD plus a soloHD appear as **two physical devices**, containing three DVB adapters in total. Choosing the dualHD includes both of its adapters and only media nodes related to that physical device.

When a saved selection exists, menus mark its available devices `[Saved]` and other available devices `[New]`. New means outside this target's saved selection. The community checklist keeps saved devices checked and new devices unchecked. First-time setup has no status labels. Labels follow USB identity across adapter renumbering; a missing saved device does not remove the saved status of other devices. Confirm the remaining choices explicitly when a saved device is missing.

Selections are saved in the target's tracking directory as `selection`. USB identities use `usb:VENDOR:PRODUCT:SERIAL` when unique. Devices without a usable unique serial use the displayed `path:` ID, which depends on physical topology. A bare serial is accepted only when unambiguous. Stable mode still requires unique serials.

Physical devices are assigned exclusively across containers. Known saved assignments appear in the selection labels. Before changes, the utility checks saved selections, legacy stable maps, actual container device mappings, and live nodes in other running containers. Incus checks expanded devices across local projects, including inherited profiles and numeric character-device mappings. Conflicts and ambiguous old mappings stop setup with the source owner identified. `--all-tuners` reports conflicts rather than taking another container's devices. Assignment writes share a host lock with the Community Scripts hooks and recheck the selected hardware under that lock.

The application/account profile is stored as data in `profile` beside the device manifests. Explicit `--app` chooses that application's default account; explicit `--user` wins. Without either option, the saved application/account is reused. No credentials are stored. Removal does not need the account or a running media service.

Enter keeps an existing selection in the text menu; the community checklist permits changing checked devices. Unattended runs reuse the saved selection. A first unattended run with multiple physical devices must specify `--tuner` or `--all-tuners`; `--yes` only skips confirmation and does not select every tuner. A missing saved device causes an error rather than attaching another device. Explicit selection can replace the saved choice. Adding or removing devices retains stable slots for devices that remain selected; running containers may need a restart before the new set takes effect.

## What changes

- **Container devices:** Proxmox receives native `devN` entries; Incus receives named `unix-char` devices. Devices use UID `0`, the chosen container GID, and mode `0660`. Host group IDs and container group membership are not rewritten.
- **USB power policy:** `/etc/udev/rules.d/99-tvtuner-power.rules` covers the union of all managed container assignments. Unique USB identities use exact vendor/product/serial matches, including reconnect rules for missing devices. Path identities use physical USB port and vendor/product IDs, retaining verified rules while disconnected. The script also sets selected devices' current `power/control` to `on`. Changed rules are backed up. Changing or removing one assignment retains rules needed by the others.
- **Legacy permissions:** The exact old global `0666` permission rule emitted by this utility is removed if present. An unrecognized permissions file causes an error. Removing the old rule does not immediately restore existing host node modes; review those separately or reconnect/reboot when appropriate.
- **Backups:** Container configuration, existing tracking files, and affected host files are backed up under `/var/backups/dvb-passthrough/`. The script prints the backup locations. Errors stop the operation; backups support manual recovery rather than automatic rollback.

Firmware is downloaded only with `--install-firmware`, following confirmation or an explicit `--yes`. The script does not load or unload drivers, change module blacklists, install packages, change repositories, rewrite group IDs, restart containers, or restart media services.

### DVB login banner

For a running container, setup installs a separate `/etc/profile.d/10-dvb-details.sh` login profile and records the selected physical devices in `/var/lib/dvb-passthrough/banner-devices.tsv`. For example:

```text
  📺  DVB Devices: dualHD (2 tuners), soloHD (1 tuner)
```

The line groups frontend nodes by assigned physical device and avoids counting compatibility aliases twice. It checks current kernel registrations and device identity without opening tuners, calling Plex, or using the network. A partially available dual tuner shows `1/2 tuners available`; a fully missing device remains listed as `missing; N configured tuners`. This reports availability, not reception or recording health. Noninteractive shells and SSH commands produce no banner output.

The renderer is self-contained, supports POSIX login shells, and runs in a subshell to preserve the caller's settings. Existing banner files are backed up inside the container under `/var/backups/dvb-passthrough/`; unrecognized files are refused. The helper module is saved at `/usr/local/lib/community-scripts/dvb-banner.func`. Community Plex installation and update drafts use the same helper, preserve the configured roster during package updates, and can restore a missing profile. The general Community Scripts banner is kept separate.

For running containers, setup also persists the shared permission/runtime helper at `/usr/local/lib/community-scripts/dvb.func`. TVHeadend installer/update hooks use it to resolve the daemon account, repair membership, and verify effective process groups. Standalone setup does not change group membership or restart the daemon.

For stopped containers, helper installation is deferred: rerun setup after starting the container. `--remove` cleans up the managed banner profile, roster, banner module, and permissions module when the container is running. Removal while stopped prints a cleanup notice; after starting it, remove those four managed files inside the container. The unrelated Community Scripts banner and stable startup helper are retained.

If no initialized DVB devices are discovered, an apply reconciles recognized managed USB power rules and stops with an error, retaining reconnect rules for saved assignments. It does not replace existing container device entries in that case. A dry run reports this behavior without changing files.

## Tracking, reruns, and removal

Proxmox ownership is recorded in `/var/lib/dvb-passthrough/proxmox/CTID/devices`. This manifest contains the full `devN: ...` lines owned by the utility. Tracking survives Proxmox moving comments or sorting configuration entries. Pending writes are journaled in `devices.pending` so interrupted operations can be retried. If a tracked slot has been edited to a different value, the script stops for review rather than deleting the changed entry.

Incus default-project ownership remains in `/var/lib/dvb-passthrough/NAME/devices`, one device name per line, for compatibility with earlier runs. Other projects use `/var/lib/dvb-passthrough/incus/PROJECT/NAME/devices`, keeping same-named containers separate. Desired device names inherited from profiles are refused before configuration changes; the script does not override profile devices. After successful discovery, rerunning removes tracked devices absent from the new plan and adds or updates current devices. Devices use `source` plus `required=false`, so an absent tuner does not prevent container startup and Incus hotplugs a returning node at that source path. This follows kernel paths: adapter renumbering still requires rerunning host-side selection. The serial-based `--stable` mode is currently Proxmox-only. Removal updates tracking after each successful deletion and tolerates devices already absent, allowing retries after partial failure.

`--remove` removes tracked container configuration and application selection/profile data, reconciles the shared USB power rules, and cleans up managed container helpers where possible. Legacy firmware, module changes, group changes, or host permission changes from older versions remain. A running Proxmox container retains its old live nodes until it is stopped/started. Reassignment checks detect those nodes even after configuration removal. See [Plex and TVHeadend guide](docs/plex-tvheadend-setup.md) for split assignments and migration.

Retain the manifests alongside container backups; they identify which entries the script owns. Without `--stable`, rerun after moving USB ports or changing adapter numbering: ordinary passthrough follows discovered paths and does not guarantee stable physical tuner identity when Linux reuses adapter numbers.

### Persistent USB tuner identity (Proxmox)

Use `--stable` to store vendor ID, product ID, USB serial, adapter count, and selected device components in `/var/lib/dvb-passthrough/proxmox/CTID/stable-map`. Each adapter receives an alias starting at `/dev/dvb/adapter1000/`, independent of its current kernel adapter number. Slots are reserved across managed containers. Proxmox follows the host aliases and creates private character nodes at these same paths inside the container, retaining UID/GID and `0660` permissions. No entire-device-directory mount or wildcard cgroup access is added.

Stable setup requires a running container. It also installs `/usr/local/sbin/dvb-passthrough-aliases` and enables `dvb-passthrough-aliases.service` inside it, ordered before Plex, Jellyfin, and Tvheadend. Some applications discover devices through udev's original kernel paths. This helper matches those paths to the private nodes by major/minor number and creates compatibility symlinks under `/dev/dvb` at each container boot. It grants access to no additional devices. Existing helper files are backed up under the container's `/var/backups/dvb-passthrough/`; unrecognized files are refused.

TVHeadend scans stable directories directly and would count their kernel compatibility links as duplicate tuners. With `--app tvheadend`, the boot helper keeps only the private stable paths and removes recognized old compatibility links. Plex and other application profiles retain the kernel links they need. Unrecognized links or conflicting real directories are refused. TVHeadend runtime verification also detects duplicate frontend device numbers. The vendor unit, startup options, and channel/network configuration remain unchanged.

The installed copy `/usr/local/libexec/dvb-passthrough` refreshes aliases on DVB udev events, using `/etc/udev/rules.d/98-dvb-passthrough-stable.rules`. A systemd oneshot, `dvb-passthrough-stable.service`, retries for up to 60 seconds before `pve-guests.service` starts guests. The dependency is ordered with `Wants`, so a missing tuner does not prevent unrelated VMs from starting. A container requiring a missing device can fail startup. Refresh never rewrites container configuration or restarts anything. To diagnose manually:

```bash
/usr/local/libexec/dvb-passthrough --refresh-stable
systemctl status dvb-passthrough-stable.service
cat /var/lib/dvb-passthrough/proxmox/101/stable-map
```

Multi-tuner devices with one USB serial are mapped by numeric driver registration order within that physical device. All expected adapters and components must be present before aliases are published. This preserves identity when unrelated devices shift the kernel numbers; it cannot distinguish equivalent internal tuners if a future driver changes their registration order. Duplicate USB serials, incomplete groups, stale nodes, and collisions with real kernel adapter paths are rejected. Missing devices clear their manifest's aliases instead of silently substituting other hardware.

This mode currently supports serial-identified USB DVB nodes on Proxmox. PCIe tuners, USB devices without usable unique serials, and related `/dev/media*` nodes require ordinary mode or manual mapping. A dry run resolves and prints the plan without creating aliases, services, or state. Reruns retain the saved selection unless you choose different devices. Existing stable maps from earlier versions supply the initial default selection automatically.

Switching to stable paths requires a container restart and may require reselecting the tuner in the media application's DVR settings. Reconnecting hardware while a container is running may also require a restart because its existing character nodes and cgroup permissions remain tied to the old device numbers. `--remove` removes that container's saved map and host aliases, while retaining the shared host infrastructure and container startup helper. The container helper has no work when no stable nodes are passed through. Keep a copy of `stable-map` with the other tracking files when restoring or moving a container; the host infrastructure must also be installed on the destination.

### Migrating older Proxmox runs

An intact legacy comment block is imported automatically if it contains only the native DVB entries emitted by the old script. Other configuration lines between the markers cause a refusal rather than being deleted.

If Proxmox has already moved both old markers above the device entries and no manifest exists, ownership is ambiguous. The script stops. Compare the current config with the original operation backup, then copy **only the verified managed `devN: ...` lines** into `/var/lib/dvb-passthrough/proxmox/CTID/devices` before retrying. Create the directory with mode `0700` and the manifest with mode `0600`, owned by root. Do not copy unrelated device entries. The script accepts property reordering and Proxmox's unnamed path syntax when comparing tracked lines.

## Firmware and verification

Run `--firmware` to diagnose, then `--install-firmware` to review and approve supported downloads. These host operations take no container target and cannot be combined with `--remove` or `--gid`. Diagnosis requires `journalctl`; downloads additionally require `curl` and HTTPS access to `raw.githubusercontent.com`.

The workflow reads recent kernel messages from the current boot, matches each request to an existing I2C client and its USB or PCIe ancestry, and displays the hardware IDs and revision. A later `firmware version:` success message for the same client clears earlier failures, including failures resolved by fallback firmware. Detached clients, unknown firmware, or unresolved hardware require manual review. No device is selected solely by its vendor ID. If there are no usable recent messages, widen `--firmware-since` or reproduce the failure when the tuner is idle; an empty diagnosis does not prove the hardware is healthy.

The initial verified catalog contains:

| Driver | Exact requested filenames |
| --- | --- |
| `si2168` | `dvb-demod-si2168-b40-01.fw`, `dvb-demod-si2168-d60-01.fw` |
| `si2157` | `dvb-tuner-si2157-a30-01.fw`, `dvb_driver_si2157_rom50.fw` |
| `m88ds3103` | `dvb-demod-m88ds3103.fw`, `dvb-demod-m88ds3103b.fw` |

These are runtime firmware files, not device-flashing utilities. Downloads use [LibreELEC's firmware snapshot](https://github.com/LibreELEC/dvb-firmware/tree/90261ae2934329f6ca84dd6c72d10d0777bf4b0e), with individual SHA-256 values embedded in the script. Each plan displays the source, checksum, destination, and license metadata. LibreELEC labels its firmware package `LicenseRef-Free-to-use`; no separate per-file license is supplied for these six entries. This metadata is recorded as upstream provenance, not replaced with this project's MIT license. See [design document](docs/design.md) for source details and catalog maintenance requirements.

Prefer compatible firmware supplied by the host distribution. Existing files are preserved, including compressed and version-specific firmware and firmware in the kernel's configured custom search path. This version offers per-file downloads as a fallback; it does not automate APT transactions. In particular, do not blindly install Debian firmware packages on Proxmox: [Proxmox documents possible package conflicts](https://github.com/proxmox/pve-docs/blob/master/firmware-updates.adoc#runtime-firmware-files).

Approved downloads are staged on the destination filesystem, checked before publication, and installed without overwriting an existing destination. A verified copy and receipt are saved under `/var/backups/dvb-passthrough/firmware.*`. No firmware binaries are bundled in this repository. An interrupted download leaves no partially installed file and can be retried.

Installing files does not prove the driver has loaded them. Reconnect the affected USB tuner when idle, or schedule PCIe/host reinitialization as appropriate, then rerun passthrough. Driver loading and blacklist changes remain manual. Both firmware diagnosis and `--install-firmware --dry-run` perform no downloads or configuration changes.

For a running container, the script checks that each planned path is a character device, its major/minor numbers match the host, and the selected service user has read/write permission. This requires `runuser` inside the container. It does not open an active tuner, tune a channel, or test recording. Proxmox configuration changes may require a scheduled container restart before devices become visible; verification is deferred for stopped containers.

Exit codes:

- `0`: completed, previewed, or cancelled by the user; verification is deferred when the container is stopped. Firmware diagnosis can report no usable requests without establishing hardware health.
- `1`: validation/discovery failure. External-command failures may propagate their own nonzero status.
- `2`: configuration saved, but runtime verification is incomplete. Review the warnings, schedule a restart if needed, and recheck access.

## Repository contents

- [`dvb-passthrough.sh`](dvb-passthrough.sh): the standalone utility documented above.
- [`tests/test_passthrough.py`](tests/test_passthrough.py): isolated regression tests using temporary files and mocked platform commands. Run on Linux/WSL with `python3 -m unittest discover -s tests -v`.
- [`tests/test_firmware.py`](tests/test_firmware.py): firmware regression tests using fake sysfs, kernel logs, and downloads; included in the same test command.
- [`tests/test_stable.py`](tests/test_stable.py): serial identity, renumbering, incomplete-device, stale-node, and container discovery-path tests using fake sysfs and harmless character devices.
- [`tests/test_selection.py`](tests/test_selection.py): shared physical selection, text/checklist menus, and selected-only PVE/Incus configuration.
- [`tests/test_plex_update.py`](tests/test_plex_update.py): Plex package updates, preserved selection/helpers, permission failures, and group recovery with mocked package and service commands.
- [`tests/test_banner.py`](tests/test_banner.py): physical-device counts, alias deduplication, missing/partial hardware, device-number reuse, POSIX/quiet login behavior, and banner ownership/backups.
- [`tests/README.md`](tests/README.md): complete test inventory, including shared application behavior and explicit Plex/TVHeadend coverage.
- [`tools/README.md`](tools/README.md): helper synchronization and Community Scripts patch generation/validation commands.
- [`community-scripts/`](community-scripts/): proposed helper-script integrations using the same physical selection helpers. See its [integration notes](community-scripts/README.md); these remain local draft patches, not a deployed upstream feature.
- [`docs/design.md`](docs/design.md): current design, firmware provenance, and proposed upstream submission scope.
- [`docs/plex-tvheadend-setup.md`](docs/plex-tvheadend-setup.md): Plex and TVHeadend setup, split assignments, migration, and network streaming.
- [`docs/validation.md`](docs/validation.md): hardware validation performed and its limits.
- [`docs/pull-request-proposal.md`](docs/pull-request-proposal.md): draft upstream PR description and validation summary.
- [`CONTRIBUTING.md`](CONTRIBUTING.md): development, test, and patch-generation guidance.

The local proposal does not establish that an upstream Community Scripts installation supports `var_dvb`; applying or submitting those integrations is separate work.
