# Connecting DVB devices to Plex and TVHeadend

The utility connects initialized USB/PCIe tuners to existing Proxmox or Incus containers. Community Scripts patches add the same opt-in device selection during Plex and TVHeadend creation. TVHeadend networks, channel scanning, EPG, recordings, and Plex's DVR setup remain application configuration.

## Existing Plex and TVHeadend containers

Run on the container host as root. Choose the application profile:

| Application | Profile | Default service account |
| --- | --- | --- |
| Plex | `--app plex` | `plex` |
| TVHeadend | `--app tvheadend` | Resolved from the service, normally `hts` |

Container IDs, names and tuner identities below are examples. Use your own
container and the physical IDs shown by discovery.

```bash
# Read-only previews; select physical tuners interactively.
bash dvb-passthrough.sh --app plex --dry-run 100
bash dvb-passthrough.sh --app tvheadend --dry-run 303

# Assign the single tuner directly to Plex using stable Proxmox paths.
bash dvb-passthrough.sh --app plex --stable --tuner EXAMPLE_SOLO_SERIAL 100

# Assign the separate dual tuner to TVHeadend using stable Proxmox paths.
bash dvb-passthrough.sh --app tvheadend --stable \
  --tuner EXAMPLE_DUAL_SERIAL 303

# Ordinary device paths on Incus; stable mode is Proxmox-only.
bash dvb-passthrough.sh --app tvheadend --tuner SERIAL tv-container

# Custom service account, or a stopped container with a known device GID.
bash dvb-passthrough.sh --app tvheadend --user recorder --gid 44 303
```

Use the IDs displayed by discovery. `--tuner` is repeatable; `--all-tuners` explicitly requests every discovered physical device and fails on conflicting assignments. The dualHD is one physical selection with two adapters. Splitting its internal adapters between containers is outside this workflow.

TVHeadend normally uses `hts` and the `video` group. For a running service, the helper reads its actual effective UID, including custom systemd configuration. For a stopped service it checks the unit's `User` and safely parses `OPTIONS` from `/etc/default/tvheadend` without executing that file. An explicit `--user` overrides discovery. For stopped containers, `--gid` is required.

The standalone utility assigns private nodes with mode 0660 and the resolved numeric group. It saves the application/account for future runs and verifies service-account read/write access. TVHeadend additionally checks the running daemon's effective UID, groups and duplicate frontend paths. Exit code 2 means configuration was saved but runtime verification is incomplete; it does not mean tuning or recording was tested. No service or container restart is performed by standalone setup.

## Split assignment and migration

Each physical tuner has one container owner. A split setup can give soloHD to Plex and dualHD to TVHeadend. Saved ownership is shown in menus; actual configuration, inherited Incus devices, and other containers' live nodes are checked before applying changes. New assignments are rechecked under the same host lock used by the creation hooks.

To move hardware between containers in either direction:

1. Save the source and destination container configurations and their host tracking directories under `/var/lib/dvb-passthrough/`. Schedule the move when tuners are idle.
2. Rerun the source container's profile with **only the physical devices it should retain**. To release all tracked devices, use `--remove SOURCE_CTID`. Selective removal uses a retained selection, not `--remove --tuner`.
3. Stop/start the source Proxmox container to revoke its old live nodes. Restarting only the application does not release those nodes.
4. Preview and apply the released devices to the destination using `--app plex` or `--app tvheadend`. Start/restart the destination container if required for new native devices.
5. Rerun verification, then check the tuners in Plex's Live TV & DVR setup or TVHeadend's **Configuration > DVB Inputs > TV adapters**. Complete channel and DVR configuration in that application.

Existing unmanaged broad device mappings or ambiguous old aliases require review. The utility preserves unrelated configuration and does not automatically take ownership of administrator mappings. Restore the saved configuration and tracking files to reverse a migration; stop/start affected containers and verify the restored ownership before using the tuners.

Stable USB mappings retain their existing limitations: usable unique serials, complete physical adapter groups, and no related `/dev/media*` nodes. Use ordinary mode where those requirements are not met. Moving ports or renumbering ordinary adapters requires another host-side discovery run. Reconnecting a running Proxmox container's tuners can require a container restart to refresh device numbers.

For TVHeadend stable mode, the boot helper keeps the private stable adapter paths directly and omits the kernel compatibility links used by Plex. TVHeadend scans both kinds of directory and would otherwise list each frontend twice. Recognized old compatibility links are removed; administrator links and real directories are preserved and reported as conflicts. Runtime verification detects duplicate frontend device numbers and requests a container restart after switching discovery modes. No TVHeadend unit, startup options, or channel configuration is rewritten.

## Community Scripts creation and updates

See the [Community Scripts integration notes](../community-scripts/README.md)
for the pinned patch baselines and application instructions. Both launchers
default to `var_dvb=no`; Advanced Setup offers physical selection. Unattended
creation can use:

```bash
var_dvb=yes
var_dvb_tuners=usb:2040:0265:EXAMPLE_DUAL_SERIAL
var_dvb_gid=44
```

The host writes compatible selection/device manifests for later standalone reconciliation. The installer runs the same permission helper for `plex` or the resolved TVHeadend account, preserves existing GIDs, installs the separate DVB banner, and restarts an already-running service only if changed group membership requires it. An explicitly enabled DVB installation fails clearly if no assigned nodes are visible.

Both application update routes preserve host selection and stable mappings. It refreshes existing aliases and owned banner files, repairs missing membership before its existing service restart, then checks application device access; TVHeadend also verifies the running daemon. Neither adds a second update restart and does not attach host devices. Older containers without a persisted permissions module receive an account/access check using the Plex account or the resolved TVHeadend daemon identity.

## Feeding TVHeadend into Plex

```text
Physical DVB devices -> TVHeadend -> HDHomeRun HTTP interface -> Plex
```

Plex receives network streams in this setup; those same physical devices belong to TVHeadend alone. A separate Plex-owned tuner can continue using direct passthrough.

For a TVHeadend build with native HDHomeRun server support:

1. Confirm the installed build exposes HDHomeRun server emulation, then enable it in TVHeadend's expert General configuration.
2. Configure the desired networks, scan/map channels, and give them suitable channel numbers in TVHeadend.
3. Grant streaming access to the Plex server's source IP through an appropriate TVHeadend access rule. HDHomeRun clients generally do not supply credentials; scope the rule to the intended Plex server.
4. Check the native `/discover.json` and `/lineup.json` endpoints from Plex's network. Native emulation uses TVHeadend's configured HTTP port, normally 9981.
5. Add that address in Plex's existing **HDHomeRun Device Address** field. For LXC 303, the expected address is `192.0.2.20:9981`, after checking its build, port and access configuration.

TVHeadend documents [the native HDHomeRun endpoints](https://docs.tvheadend.org/documentation/development/json-api/other-functions#hdhomerun). If the installed build lacks suitable native support, a bridge such as [Antennas](https://github.com/jfarseneau/antennas) is an alternative. The passthrough scripts do not install a bridge or change network streaming, access rules, channels, or DVR settings.
