# DVB tuner passthrough and optional host firmware setup

## Objective

Help a user configure a physical DVB tuner for Plex, Jellyfin, or Tvheadend in a Proxmox VE or Incus container. The standalone implementation is `dvb-passthrough.sh`. Proposed Community Scripts integrations remain separate draft work.

The utility has two independent operations:

1. **Container passthrough:** discover initialized host DVB character devices and related media nodes, show a plan, configure the chosen container, and verify service-user permissions where possible.
2. **Host firmware assistance:** diagnose recent missing-firmware requests for attached supported hardware and offer explicitly approved, verified runtime firmware downloads before container passthrough is possible.

Firmware support is optional because it changes the host and has a different lifecycle from container configuration. The Community Scripts proposal covers container passthrough; firmware assistance is provided by the standalone utility.

DVB host management requires root on Proxmox VE and Incus. Non-root Incus DVB
requests fail before container creation; ordinary builds without DVB retain
non-root support. The public install loader supplies DVB helpers and the API
inventory records them. Installers use the prepared runtime.

## Application profiles and exclusive assignments

`--app tvheadend` selects TVHeadend and resolves its actual running service account, normally `hts`; `--app plex` selects Plex. Explicit `--user` overrides account selection. Successful applications save the application/account in a data-only `profile` file beside the device manifests. Reruns without either option reuse it, while older targets still default to Plex. Stopped containers continue to require `--gid` and defer runtime verification. Firmware/removal do not require an application account.

Physical ownership is checked against saved selections, legacy stable maps, actual container mappings, and live device numbers in other containers. Incus includes local projects, inherited profiles, and numeric character mappings. The creation hooks and standalone utility recheck hardware and ownership under a shared host lock. This permits different physical devices in Plex and TVHeadend while refusing accidental overlap. Releasing configuration from a running Proxmox container requires a stop/start to revoke its old nodes before reassignment.

USB power policy is a union across managed assignments. Exact unique USB vendor/product/serial rules survive unplugging and port moves; path-identified devices use verified port/vendor/product rules. Selection changes preserve the previous policy until the new configuration commits, and removal retains other targets' reconnect rules. Changed shared power rules are backed up.

The Community Scripts TVHeadend installer and update route reuse the same permission and banner modules as Plex. They preserve numeric GIDs and repair only service-account membership. Installation detects missing explicitly requested devices. Updates repair membership before their existing service restart, retain host selection/aliases and the banner roster, and verify the running daemon's effective UID and groups. Standalone application never restarts the service and returns incomplete verification when its live identity/groups or device mappings are stale. See `plex-tvheadend-setup.md` and `../community-scripts/upstream-revisions.json` for usage and pinned patch baselines.

## Commands and consent

```bash
# Read-only firmware diagnosis; no container target is needed.
bash dvb-passthrough.sh --firmware

# Review each supported download and answer y to install; default is No.
bash dvb-passthrough.sh --install-firmware

# Preview installation, using only messages from the current boot.
bash dvb-passthrough.sh --install-firmware --dry-run --firmware-since "2 hours ago"

# Explicit unattended firmware installation.
bash dvb-passthrough.sh --install-firmware --yes

# Once hardware initializes, configure container passthrough separately.
bash dvb-passthrough.sh --user plex 101
```

`--yes` alone never enables firmware installation. Firmware mode cannot take a container target, `--gid`, or `--remove`. A dry run makes no download or configuration change. Choosing firmware installation does not authorize driver unloading, blacklist changes, or service/container restarts.

## Hardware identity and diagnosis

Passthrough discovery uses `/sys/class/dvb` and actual character nodes. Related `/dev/media*` devices are selected by common hardware ancestry, rather than exposing every media node on the host. Compatibility is determined by initialized kernel devices, not a universal vendor-to-driver table.

Selection is by physical USB device or PCI function, with all adapters of that device included. The text menu, community checklist, repeatable standalone `--tuner` options, and community `var_dvb_tuners` use the same shared helpers. USB serial identities are preferred; physical path IDs allow explicit selection when serials are unavailable or duplicated. Multi-device unattended runs require an explicit or saved selection. Only chosen devices and their related media nodes enter the passthrough plan and USB power policy. Retained stable mappings keep their existing slots when the user changes the selected set.

Plex's container-side update route preserves that host choice. It upgrades the package, invokes an existing stable discovery helper, and verifies access to the devices already passed through. The community permissions helper preserves group IDs, restores missing service-user membership when necessary, and restarts Plex only after such a membership change. It does not rediscover or attach host devices during an update. The community files remain integration drafts; full upstream builds and real package-upgrade tests are separate validation.

The standalone and community installation paths also install a separate DVB login profile. An authoritative host selection supplies the standalone roster; initial community setup derives its roster only from assigned character nodes. The renderer checks current registrations and USB/path identity, groups physical devices, and ignores directory aliases. It reports complete, partial, or missing availability without opening a frontend, contacting Plex, or using the network. It is silent outside interactive terminals and preserves the login shell's variables and options. Updates preserve the roster and refresh only owned banner files, with backups, so disconnected configured devices remain visible.

Proxmox `--stable` mode records USB vendor/product/serial identity and a complete adapter/component group in a per-container `stable-map`. Host aliases starting at `/dev/dvb/adapter1000/` keep native Proxmox `devN` paths consistent across kernel adapter renumbering. An installed copy of the script refreshes aliases on DVB udev events, with a bounded boot refresh ordered before `pve-guests.service`. It does not rewrite CT configuration during boot or call Proxmox from a udev worker. Native per-device permissions remain in use. Setup requires a running CT to install a container startup helper before Plex/Jellyfin/Tvheadend: it matches private stable nodes to current kernel registrations by major/minor number and creates the original device paths as compatibility symlinks for udev-based discovery. `/dev` is recreated at each boot, so these links are regenerated each time.

Multi-tuner channels use numeric registration order within their physical USB device, since they share one serial. Missing adapters/components, duplicate serials, stale major/minor values, and kernel-node collisions fail closed. Devices without usable serials, PCIe tuners, and related media nodes currently require ordinary mode. Reconnecting devices during container runtime may still require restarting the container to refresh its private nodes and cgroup device numbers. Application DVR identities imported from Windows are a separate migration issue.

Live TVHeadend validation found it enumerates both high-numbered stable directories and their compatibility links. Its boot helper therefore uses the private stable paths directly and omits the compatibility links required by Plex. It removes only recognized old links and refuses administrator links or real directory conflicts. Runtime verification rejects duplicate frontend device numbers. This keeps one TVHeadend adapter per physical frontend without broadening access or rewriting service/channel settings.

Firmware diagnosis operates before DVB nodes exist. It reads `journalctl` kernel messages from the current boot, restricted to the last 15 minutes by default. `--firmware-since` changes that window. The parser recognizes exact missing-file messages and direct firmware requests returning `-2`, associated with a driver and I2C client address. It ignores unrelated drivers, transport errors such as `-20`, and unsafe filenames containing path separators. A later `firmware version:` success message for that exact driver/client supersedes earlier failures, including missing optional firmware followed by a successful fallback.

Each request must resolve through the live `/sys/bus/i2c/devices` entry to an existing client under `/sys/devices`. The bound driver, or the client name when no driver is bound, must match. The utility walks the hardware ancestry to identify:

- USB vendor ID, product ID, device revision, and physical sysfs path; or
- PCI vendor/device IDs, subsystem vendor/device IDs, revision, and physical sysfs path.

Selection requires the exact filename requested by that driver. No firmware is inferred from a manufacturer name, a broad PCI multimedia class, or the mere presence of a loaded module. Hardware identity is checked again after confirmation. Missing client entries, unsupported filenames, unrecognized ancestry, or driver mismatches require manual review.

Firmware diagnosis supports for `si2168`, `si2157`, and `m88ds3103`. It does not claim universal firmware repair. Drivers may request firmware only when a tuner is used. If there is no usable recent request, the user can widen the current-boot window or reproduce the error when the tuner is idle. An empty report does not prove that a tuner is healthy. Other devices and log formats need additional verified mappings and tests.

## Firmware sources and verification

Prefer firmware supplied by compatible host distribution packages. Existing files always take precedence, including version-specific files, compressed forms, symlinks, and the kernel's configured custom firmware search directory. The installer does not overwrite them or take ownership of package-managed files.

This implementation provides curated per-file downloads as the fallback. It does not install packages, run APT updates, alter repositories, or add a PPA. In particular, generic installation of `firmware-misc-nonfree` is not part of the workflow: Proxmox ships `pve-firmware` and documents potential conflicts with additional firmware packages.

The catalog uses this immutable source snapshot:

`https://github.com/LibreELEC/dvb-firmware/tree/90261ae2934329f6ca84dd6c72d10d0777bf4b0e`

The same revision is referenced by LibreELEC's firmware packaging at the time of implementation. File bytes and SHA-256 values were checked against that snapshot on 2026-10-23. The Si2157 ROM50 filename and A30 filename in this snapshot contain identical bytes; each remains independently matched to the exact driver request.

| Driver | Filename | SHA-256 |
| --- | --- | --- |
| `si2168` | `dvb-demod-si2168-b40-01.fw` | `d25c7deb9f69dca232ce25ab108da8ff5013d6d39088e0ec3475d97ded8af718` |
| `si2168` | `dvb-demod-si2168-d60-01.fw` | `5e1c860e95a8d92b2716b0075dd00e55f99c2c368e35340f6b527c366ff27ee7` |
| `si2157` | `dvb-tuner-si2157-a30-01.fw` | `8b2af7f9714834106adc075bde60433a1f6d6812383e8c3f0d4e50212fb3eb5e` |
| `si2157` | `dvb_driver_si2157_rom50.fw` | `8b2af7f9714834106adc075bde60433a1f6d6812383e8c3f0d4e50212fb3eb5e` |
| `m88ds3103` | `dvb-demod-m88ds3103.fw` | `4767ab80ceba4a66315cbec2a07ae1f7ebbd19c5758fd098b932e02c9108eff9` |
| `m88ds3103` | `dvb-demod-m88ds3103b.fw` | `4336f79c0c706162f5e5bb65351ca4f24d1de10b9d908c427edfe339d843f7ac` |

Before confirmation, show the hardware identity, exact filename, pinned source URL, expected checksum, destination, and source license metadata. LibreELEC's package metadata labels the collection `LicenseRef-Free-to-use`. No separate per-file license is supplied for the six catalog entries; record that fact instead of assigning them this repository's MIT license. No firmware binaries are committed to this repository.

Installation downloads over HTTPS with HTTPS-only redirects, timeouts, retries, and a size limit. A temporary file on the destination filesystem is checked against the embedded SHA-256 value before installation. A mismatched checksum or interrupted transfer fails without publishing a firmware file. Atomic hard-link publication refuses to overwrite any existing destination, including a file created concurrently or a dangling symlink.

The utility keeps a verified copy and receipt under `/var/backups/dvb-passthrough/firmware.*`. The receipt records the filename, source URL, checksum, hardware identity, and license metadata. Container `--remove` does not remove shared firmware files.

Installing the file is not proof of successful hardware initialization. The final message instructs the user to reconnect an idle USB tuner, or schedule PCIe/host reinitialization as appropriate, and rerun discovery. No active tuner is opened, no shared driver is unloaded, and no host restart is initiated. Updating initramfs for devices that need firmware during early boot remains a separate host administration task.

## Drivers, blacklists, and permissions

The utility does not delete administrator blacklists or guess which modules to load from a vendor-wide match. Driver preparation remains manual in this version. Future assisted driver loading must resolve the exact device's module aliases, respect existing configuration, and have its own explicit scope and tests.

Proxmox passthrough uses native `devN` entries, with UID `0`, a selected in-container GID, and mode `0660`. Incus uses `unix-char` entries with the same permissions and `required=false`. Matching numeric host/container GIDs is not treated as an unprivileged-container ID mapping. The utility does not rewrite `/etc/group` or apply global `0666` host permissions.

Owned Proxmox device entries are tracked independently of comment positions, with pending-write recovery. Incus reruns reconcile stale tracked entries; removal records progress after each successful deletion and tolerates entries already absent. Unmanaged entries are preserved. Legacy Proxmox ownership that has become ambiguous requires the recovery procedure in README.md.

Verification checks character-device visibility and service-user read/write permissions for running containers. Failed verification is reported with exit code `2` rather than a success claim. Stopped containers defer verification. Channel tuning and recording require a separate user test; configuration never restarts a media service automatically.

For TVHeadend, standalone verification additionally checks the daemon's actual effective UID and device groups. Its installer/update hooks can restart services as described above; the no-restart rule applies to the standalone host utility. Network streaming from TVHeadend into Plex uses the optional native HDHomeRun server interface or a separately configured bridge. The device-connection workflow does not configure those application features.

## Validation and catalog maintenance

Run `python3 -m unittest discover -s tests -v` on Linux or WSL. Tests use temporary files, mocked platform/network commands, and fake sysfs/journal data; they do not modify real firmware or containers.

Coverage includes existing passthrough recovery, hardware/driver matching, current-boot journal arguments, false-positive suppression after successful fallback, unsupported requests, confirmation defaulting to No, read-only previews, existing firmware preservation, checksum rejection, interrupted downloads, concurrent destination creation, and provenance receipts.

Before expanding the firmware catalog:

1. Verify the filename and requesting driver against kernel source and a real hardware report; record exact model/revision details in the change.
2. Review source provenance and the available license metadata.
3. Pin an immutable source revision and independently compute the file's SHA-256 value.
4. Add driver/log-format and failure-path coverage, and run ShellCheck and Bash syntax checks.
5. Validate installation and actual initialization on affected hardware. Unit tests do not establish hardware compatibility.

## Upstream submission scope

The proposed first upstream change is optional tuner discovery, passthrough, and actionable diagnosis, integrated with the current Community Scripts loading and backend interfaces. A separate proposal can add host firmware installation, making its support matrix, provenance, and host changes reviewable independently. Firmware is default-off in both cases.

Files under `../community-scripts/` and `pull-request-proposal.md` describe the current locally tested Plex/TVHeadend integration drafts, rebased against the revisions in `../community-scripts/upstream-revisions.json`. Full live creation/update and hardware assignment validation are required before submission. No upstream acceptance or rejection of firmware installation is assumed.

## Sources

- [Hauppauge Linux support and firmware instructions](https://www.hauppauge.com/pages/support/support_linux.html): vendor instructions include the Si2168 and Montage firmware families and links to the DVB firmware collections.
- [LibreELEC firmware package metadata](https://github.com/LibreELEC/LibreELEC.tv/blob/master/packages/linux-firmware/dvb-firmware/package.mk): collection provenance and license label, checked during implementation.
- [Pinned LibreELEC firmware snapshot](https://github.com/LibreELEC/dvb-firmware/tree/90261ae2934329f6ca84dd6c72d10d0777bf4b0e).
- [Linux firmware search paths](https://docs.kernel.org/driver-api/firmware/fw_search_path.html).
- [Proxmox runtime firmware documentation](https://github.com/proxmox/pve-docs/blob/master/firmware-updates.adoc#runtime-firmware-files).
- [Community Scripts core contribution guidance](https://github.com/community-scripts/core/blob/main/docs/contributing.md).
