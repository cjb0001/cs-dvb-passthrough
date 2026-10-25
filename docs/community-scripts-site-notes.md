# Submission notes for community-scripts.org

These notes are for maintainers of [community-scripts.org](https://community-scripts.org),
the Community Scripts application catalog and install-command generator. They
explain how to describe the proposed DVB feature on the Plex and TVHeadend
application pages.

This file is supporting material for the upstream submission. It is not a
configuration file, and users do not need it to run the utility. For usage, see
[Plex and TVHeadend setup](plex-tvheadend-setup.md).

## DVB settings belong to host setup

Core selects physical tuners before creating the container. The settings below
are read on the Proxmox VE or Incus host:

| Setting | Default | Purpose |
| --- | --- | --- |
| `var_dvb` | `no` | Enable optional DVB passthrough |
| `var_dvb_tuners` | empty | Physical identities, chosen interactively or supplied explicitly |
| `var_dvb_gid` | `44` | Group ID for private device nodes inside the container |

Core passes `ENABLE_DVB` to the application installer after resolving the host
hardware. The installer does not select physical tuners.

The site's `app_vars` metadata describes application installer inputs. These
host settings should instead be documented as an Advanced Setup capability,
like GPU passthrough. Any controls added to the site's command generator need
to follow that host capability model and be reviewed by the site maintainers.

## Suggested text for the application pages

> Optional DVB passthrough connects selected physical USB or PCI tuners to the
> container. All adapters of a selected device are included. DVB host management
> requires root on Proxmox VE and Incus. Ordinary Incus builds without DVB retain
> non-root support. Use the Advanced Setup tuner checklist or supply explicit
> host `var_dvb*` settings. Working host drivers and firmware are required.
> Configure channel scanning and DVR settings in Plex or TVHeadend afterward.

## Submission status

These site notes have not been submitted or published. No changes to the
community-scripts.org catalog or command generator are included in the prepared
Core and application PR branches.
