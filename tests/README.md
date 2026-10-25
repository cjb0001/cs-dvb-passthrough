# Regression tests

Run the complete suite on Linux or WSL from the repository root:

```bash
python3 -m unittest discover -s tests -v
```

Tests use isolated fixtures and mocked hardware/platform/package commands. They do not change a live host or container.

Names beginning with `test_application_` cover behavior shared by Plex and TVHeadend. Application-specific installer/update tests name the application explicitly.

| File | Coverage |
| --- | --- |
| `test_passthrough.py` | Core passthrough ownership, configuration and recovery |
| `test_selection.py` | Physical tuner selection, menus and PVE/Incus creation configuration |
| `test_device_assignments.py` | Cross-container conflicts, assignment recovery and shared USB power policy |
| `test_application_profiles.py` | Plex/TVHeadend defaults, saved profiles and account overrides |
| `test_application_passthrough.py` | Full standalone TVHeadend flows and legacy Plex compatibility |
| `test_stable.py` | USB serial identity, adapter renumbering and stable mapping lifecycle |
| `test_application_stable_discovery.py` | TVHeadend native stable paths, Plex compatibility links and duplicate detection |
| `test_plex_update.py` | Plex updates, permissions, group recovery and preserved selection/banner |
| `test_tvheadend_install.py` | TVHeadend installer and device permission preparation |
| `test_tvheadend_update.py` | TVHeadend updates, daemon permissions and preserved host choices |
| `test_banner.py` | Shared login banner, physical-device counts and missing hardware |
| `test_firmware.py` | Firmware diagnosis and installation |

Plex installer behavior also has coverage in the shared passthrough/banner tests; there is no separate full Plex installer execution suite equivalent to `test_tvheadend_install.py`.
