"""Plex's container-side update route preserves host tuner choices."""

import os
from pathlib import Path
import subprocess
import tempfile
import unittest

from test_passthrough import SCRIPT


COMMUNITY = SCRIPT.parent / "community-scripts"
SHIM = COMMUNITY / "ProxmoxVE/ct/plex.sh"
MOCKS = r'''
header_info() { :; }
apt_update_safe() { apt update; }
check_container_storage() { :; }
check_container_resources() { :; }
msg_info() { :; }
msg_ok() { :; }
msg_error() { printf '%s\n' "$*" >&2; }
dpkg() { return 0; }
apt() { printf 'apt %s\n' "$*" >> "$ACTION_LOG"; [[ ${FAIL_APT:-false} == false ]]; }
systemctl() { printf 'systemctl %s\n' "$*" >> "$ACTION_LOG"; }
runuser() {
  printf 'runuser %s\n' "$*" >> "$ACTION_LOG"
  [[ ${FAIL_ACCESS:-false} == false && " ${CURRENT_GROUPS:-990 44} " == *" 44 "* ]]
}
id() { [[ ${1:-} != -G ]] || printf '%s\n' "${CURRENT_GROUPS:-990 44}"; return 0; }
stat() { printf '44\n'; }
getent() {
  [[ ${MISSING_GROUP:-false} == false ]] || return 2
  printf 'video:x:44:\n'
}
groupadd() { printf 'groupadd %s\n' "$*" >> "$ACTION_LOG"; }
usermod() { printf 'usermod %s\n' "$*" >> "$ACTION_LOG"; CURRENT_GROUPS="${CURRENT_GROUPS:-990} 44"; }
pct() { echo 'Update attempted host discovery' >&2; return 85; }
incus() { return 85; }
dvb_discover_tuners() { return 85; }
'''


class PlexUpdateTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="plex-update-")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.log = self.root / "actions.log"
        self.dev = self.root / "dev/dvb"
        self.permissions = self.root / "dvb.func"
        self.helper = self.root / "dvb-passthrough-aliases"
        self.banner_module = self.root / "dvb-banner.func"
        self.banner_state = self.root / "banner-state"
        self.banner_profile = self.root / "profile.d/10-dvb-details.sh"
        self.selection = self.root / "host-selection"
        self.selection.write_text("usb:2040:0264:SOLO\n")
        self.map = self.root / "host-stable-map"
        self.map.write_text("1000 2040 0264 SOLO 0 1 frontend0\n")
        source = SHIM.read_text()
        self.functions = source[source.index("function verify_plex_dvb_access()"):].split("\nstart\n", 1)[0]
        self.functions = self.functions.replace("/usr/local/sbin/dvb-passthrough-aliases", str(self.helper))
        self.functions = self.functions.replace("/usr/local/lib/community-scripts/dvb.func", str(self.permissions))
        self.functions = self.functions.replace("/usr/local/lib/community-scripts/dvb-banner.func", str(self.banner_module))
        self.functions = self.functions.replace("/dev/dvb", str(self.dev))
        self.functions = self.functions.replace("/etc/apt/sources.list.d", str(self.root / "apt"))

    def device(self):
        node = self.dev / "adapter1000/frontend0"
        node.parent.mkdir(parents=True)
        node.symlink_to("/dev/null")

    def install_helpers(self):
        lib = (COMMUNITY / "core/lib/dvb.func").read_text()
        lib = lib.replace("/dev/dvb", str(self.dev)).replace("/dev/media", str(self.root / "dev/media"))
        self.permissions.write_text(lib)
        self.helper.write_text('#!/bin/sh\nprintf "discovery-helper\\n" >> "$ACTION_LOG"\n')
        self.helper.chmod(0o755)

    def update(self, **extra):
        return subprocess.run(["bash", "-c", "set -euo pipefail\n" + MOCKS + self.functions + "\nif update_script; then exit 0; else exit 1; fi\n"],
                              env=dict(os.environ, APP="Plex", STD="", ACTION_LOG=str(self.log),
                                       dvb_banner_state=str(self.banner_state), dvb_banner_profile=str(self.banner_profile),
                                       dvb_banner_backup_root=str(self.root / "banner-backups"), **extra),
                              capture_output=True, text=True)

    def assert_ok(self, result):
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_update_preserves_selection_map_helper_and_existing_membership(self):
        self.device()
        self.install_helpers()
        original = {p: p.read_bytes() for p in [self.selection, self.map, self.permissions, self.helper]}
        self.assert_ok(self.update())
        log = self.log.read_text()
        self.assertIn("apt install -y plexmediaserver", log)
        self.assertIn("discovery-helper", log)
        self.assertIn("runuser -u plex -- test -r", log)
        self.assertNotIn("usermod", log)
        self.assertEqual(log.count("systemctl restart plexmediaserver"), 1)
        for p, data in original.items():
            self.assertEqual(p.read_bytes(), data)

    def test_update_restores_membership_before_the_upstream_restart(self):
        self.device()
        self.install_helpers()
        self.assert_ok(self.update(CURRENT_GROUPS="990"))
        log = self.log.read_text()
        self.assertIn("usermod -aG video plex", log)
        self.assertEqual(log.count("systemctl restart plexmediaserver"), 1)

    def test_missing_group_is_created_without_rewriting_an_existing_gid(self):
        self.device()
        self.install_helpers()
        self.assert_ok(self.update(CURRENT_GROUPS="990", MISSING_GROUP="true"))
        log = self.log.read_text()
        self.assertIn("groupadd -g 44 dvb_44", log)
        self.assertIn("usermod -aG dvb_44 plex", log)
        self.assertNotIn("groupmod", log)

    def test_older_container_without_persisted_permissions_helper_is_checked(self):
        self.device()
        self.assert_ok(self.update())
        self.assertIn("runuser -u plex", self.log.read_text())

    def test_no_tuner_configuration_does_not_block_an_ordinary_plex_update(self):
        self.assert_ok(self.update())
        self.assertNotIn("runuser", self.log.read_text())

    def install_banner_module(self):
        self.banner_module.write_text((COMMUNITY / "core/lib/dvb-banner.func").read_text())
        self.banner_state.mkdir()
        roster = self.banner_state / "banner-devices.tsv"
        roster.write_text("# Managed by dvb-passthrough.sh\nusb:2040:0264:SOLO\tsoloHD\t/dev/dvb/adapter1000/frontend0\n")
        return roster

    def test_update_preserves_banner_roster_and_restores_missing_profile(self):
        self.device()
        roster = self.install_banner_module()
        original = roster.read_bytes()
        self.assert_ok(self.update())
        self.assertEqual(roster.read_bytes(), original)
        self.assertIn("DVB Devices:", self.banner_profile.read_text())
        self.assertTrue(self.banner_profile.stat().st_mode & 0o111)

    def test_update_keeps_missing_devices_in_the_banner_roster(self):
        roster = self.install_banner_module()
        original = roster.read_bytes()
        self.assert_ok(self.update())
        self.assertEqual(roster.read_bytes(), original)

    def test_update_does_not_overwrite_an_administrator_banner(self):
        self.install_banner_module()
        self.banner_profile.parent.mkdir()
        self.banner_profile.write_text("# administrator custom banner\n")
        result = self.update()
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(self.banner_profile.read_text(), "# administrator custom banner\n")

    def test_access_failure_is_reported_instead_of_claiming_success(self):
        self.device()
        result = self.update(FAIL_ACCESS="true")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("Plex DVB preparation failed", result.stderr)

    def test_package_failure_stops_before_tuner_checks_even_in_conditional_route(self):
        self.device()
        self.install_helpers()
        result = self.update(FAIL_APT="true")
        self.assertNotEqual(result.returncode, 0)
        log = self.log.read_text()
        self.assertNotIn("discovery-helper", log)
        self.assertNotIn("runuser", log)
        self.assertNotIn("systemctl restart", log)

    def test_installed_discovery_helper_failure_is_reported(self):
        self.device()
        self.helper.write_text("#!/bin/sh\nexit 12\n")
        self.helper.chmod(0o755)
        result = self.update()
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("Plex DVB preparation failed", result.stderr)

    def test_permissions_helper_does_not_add_root_group_to_gain_device_access(self):
        self.device()
        self.install_helpers()
        self.functions = 'stat() { printf "0\\n"; }\n' + self.functions
        result = self.update(CURRENT_GROUPS="990")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("Refusing to grant root-group", result.stderr)
        self.assertNotIn("usermod", self.log.read_text())

    def test_shim_update_helper_is_included_verbatim_in_the_submission_patch(self):
        patch = (COMMUNITY / "ProxmoxVE/ct/plex_dvb.patch").read_text().splitlines()
        added = "\n".join(line[1:] for line in patch if line.startswith("+") and not line.startswith("+++"))
        source = SHIM.read_text()
        function = source[source.index("function verify_plex_dvb_access()"):source.index("function update_script()")].strip()
        self.assertIn(function, added)
        self.assertIn("+  verify_plex_dvb_access ||", "\n".join(patch))


if __name__ == "__main__":
    unittest.main()
