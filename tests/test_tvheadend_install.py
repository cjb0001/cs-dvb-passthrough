"""Execute the real TVHeadend installer with isolated package/service commands."""

import os
from pathlib import Path
import subprocess
import unittest

import test_tvheadend_update


class TVHeadendInstallerTests(unittest.TestCase):
    def setUp(self):
        self.fixture = test_tvheadend_update.TVHeadendUpdateTests()
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)
        self.root = self.fixture.root
        self.core = self.root / "core/lib"
        self.core.mkdir(parents=True)
        self.helpers = self.root / "installed-helpers"
        self.profile = self.root / "profile.d/10-dvb-details.sh"
        community = test_tvheadend_update.COMMUNITY
        (self.core / "dvb.func").write_text(self.fixture.paths((community / "core/lib/dvb.func").read_text()))
        (self.core / "dvb-banner.func").write_text((community / "core/lib/dvb-banner.func").read_text())
        installer = (community / "ProxmoxVE/install/tvheadend-install.sh").read_text()
        self.installer = self.fixture.paths(installer).replace("~/tvheadend.creds", f'"{self.root}/test-credentials"')
        self.bootstrap = test_tvheadend_update.MOCKS + r'''
color() { :; }
verb_ip6() { :; }
catch_errors() { :; }
setting_up_container() { :; }
network_check() { :; }
update_os() {
  source "$COMMUNITY_SCRIPTS_CORE_DIR/lib/dvb-banner.func"
  source "$COMMUNITY_SCRIPTS_CORE_DIR/lib/dvb.func"
}
setup_deb822_repo() { :; }
get_os_info() { printf 'trixie\n'; }
debconf-set-selections() { cat >/dev/null; }
motd_ssh() { :; }
customize() { :; }
cleanup_lxc() { :; }
curl() { printf 'Unexpected helper download\n' >&2; return 98; }
'''

    def install(self, **extra):
        return subprocess.run(["bash", "-c", "set -euo pipefail\n" + self.installer],
                              env=dict(os.environ, FUNCTIONS_FILE_PATH=self.bootstrap, STD="",
                                       var_admin_user="test-admin", var_admin_pass="fixture-only",
                                       ACTION_LOG=str(self.fixture.log),
                                       COMMUNITY_SCRIPTS_CORE_DIR=str(self.core.parent),
                                       dvb_proc_root=str(self.root / "proc"),
                                       dvb_tvh_defaults=str(self.root / "defaults"),
                                       dvb_helper_dir=str(self.helpers),
                                       dvb_helper_backup_root=str(self.root / "helper-backups"),
                                       dvb_banner_state=str(self.fixture.roster_state),
                                       dvb_banner_profile=str(self.profile),
                                       dvb_banner_backup_root=str(self.root / "banner-backups"),
                                       SYS_ROOT=str(self.root / "sys"), DEV_ROOT=str(self.root / "dev"), **extra),
                              capture_output=True, text=True)

    def assert_ok(self, result):
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_no_dvb_installs_service_without_fetching_helpers(self):
        self.assert_ok(self.install())
        self.assertFalse(self.helpers.exists())
        self.assertFalse(self.profile.exists())
        self.assertIn("systemctl enable -q --now tvheadend", self.fixture.log.read_text())

    def test_enabled_but_missing_devices_fail_instead_of_silent_success(self):
        result = self.install(ENABLE_DVB="yes")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("no assigned character devices", result.stderr)
        self.assertNotIn("systemctl enable", self.fixture.log.read_text())

    def test_install_persists_shared_modules_and_profile_then_verifies_the_daemon(self):
        self.fixture.device()
        self.assert_ok(self.install(ENABLE_DVB="yes"))
        self.assertTrue((self.helpers / "dvb.func").is_file())
        self.assertTrue((self.helpers / "dvb-banner.func").is_file())
        self.assertIn("DVB Devices:", self.profile.read_text())
        log = self.fixture.log.read_text()
        self.assertLess(log.index("apt install"), log.index("runuser"))
        self.assertEqual(log.count("systemctl restart tvheadend"), 0)
        self.assertIn("systemctl show tvheadend.service -p MainPID", log)

    def test_changed_membership_restarts_an_already_running_daemon_once(self):
        self.fixture.device()
        self.assert_ok(self.install(CURRENT_GROUPS="990", ENABLE_DVB="yes"))
        log = self.fixture.log.read_text()
        self.assertIn("usermod -aG video hts", log)
        self.assertEqual(log.count("systemctl restart tvheadend"), 1)
        self.assertLess(log.index("usermod"), log.index("systemctl restart"))

    def test_package_failure_stops_before_helper_installation(self):
        self.fixture.device()
        result = self.install(FAIL_APT="true")
        self.assertNotEqual(result.returncode, 0)
        self.assertFalse(self.helpers.exists())
        self.assertNotIn("systemctl enable", self.fixture.log.read_text())

    def test_administrator_owned_helper_is_preserved(self):
        self.fixture.device()
        self.helpers.mkdir()
        existing = self.helpers / "dvb.func"
        existing.write_text("# custom administrator script\n")
        self.assertNotEqual(self.install().returncode, 0)
        self.assertEqual(existing.read_text(), "# custom administrator script\n")

    def test_owned_helper_upgrade_creates_a_backup(self):
        self.fixture.device()
        self.helpers.mkdir()
        existing = self.helpers / "dvb.func"
        original = "#!/bin/bash\n# Managed by dvb-passthrough.sh\n# old fixture\n"
        existing.write_text(original)
        self.assert_ok(self.install())
        backups = list((self.root / "helper-backups").glob("helpers.*/dvb.func"))
        self.assertEqual(len(backups), 1)
        self.assertEqual(backups[0].read_text(), original)


if __name__ == "__main__":
    unittest.main()
