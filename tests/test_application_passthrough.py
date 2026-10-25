"""Standalone Plex and TVHeadend passthrough with isolated platform commands."""

import os
import subprocess
import unittest

from test_passthrough import SCRIPT
import test_stable


class ApplicationPassthroughTests(unittest.TestCase):
    def setUp(self):
        self.fixture = test_stable.StableTests("test_duplicate_serial_on_two_devices_is_rejected")
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)
        self.fixture.tuner("SOLO", [3], pid="0264")
        self.root = self.fixture.root
        self.pve = self.root / "pve/lxc"
        self.pve.mkdir(parents=True)
        self.config = self.pve / "303.conf"
        self.config.write_text("rootfs: storage:root\n")
        self.state = self.root / "state/proxmox/303"
        self.rules = self.root / "udev/rules.d"
        self.rules.mkdir(parents=True)
        self.bin = self.root / "bin"
        self.bin.mkdir()
        pct = self.bin / "pct"
        pct.write_text(r'''#!/bin/bash
printf 'pct %s\n' "$*" >> "$ACTION_LOG"
case "$1" in
  status) printf 'status: %s\n' "${MOCK_CT_STATE:-running}";;
  help) printf '%s\n' '--dev0';;
  config) exit 0;;
  list) printf '303 running\n';;
  exec)
    shift 3
    case "$1" in
      id) printf '44\n';;
      test|stat) "$@";;
      runuser) exit 0;;
      bash)
        if [[ $* == *dvb_tvheadend_user* ]]; then printf 'hts\n'
        elif [[ $* == *dvb_verify_service* && ${FAIL_DAEMON:-false} == true ]]; then exit 9
        else cat >/dev/null; fi
        ;;
      sh) cat >/dev/null;;
      *) exit 98;;
    esac
    ;;
  *) exit 98;;
esac
''')
        pct.chmod(0o755)
        udevadm = self.bin / "udevadm"
        udevadm.write_text("#!/bin/sh\nexit 0\n")
        udevadm.chmod(0o755)
        text = SCRIPT.read_text()
        text = text.replace('if [[ $EUID -ne 0 ]]; then', 'if false; then')
        for old, new in (("/etc/pve", str(self.root / "pve")),
                         ("/var/lib/dvb-passthrough", str(self.root / "state")),
                         ("/var/backups/dvb-passthrough", str(self.root / "backups")),
                         ("/etc/udev", str(self.root / "udev")),
                         ("/etc/modules-load.d", str(self.root / "modules")),
                         ("/run/lock/dvb-passthrough", str(self.root / "operation"))):
            text = text.replace(old, new)
        text = text.replace("SYS_ROOT=/sys", f'SYS_ROOT="{self.fixture.sys}"')
        text = text.replace("DEV_ROOT=/dev", f'DEV_ROOT="{self.fixture.dev}"')
        text = text.replace("find /sys/class/dvb", f'find "{self.fixture.sys}/class/dvb"')
        text = text.replace('^/dev/dvb/adapter[0-9]+/frontend[0-9]+$', f'^{self.fixture.dev}/dvb/adapter[0-9]+/frontend[0-9]+$')
        text = text.replace('[[ ! -d "/dev/dvb" ]]', f'[[ ! -d "{self.fixture.dev}/dvb" ]]')
        self.script = self.root / "script.sh"
        self.script.write_text(text)
        self.log = self.root / "actions"

    def run_script(self, *args, **extra):
        return subprocess.run(["bash", str(self.script), *args], input="", capture_output=True, text=True,
                              env=dict(os.environ, PATH=str(self.bin) + ":" + os.environ["PATH"],
                                       ACTION_LOG=str(self.log), **extra))

    def assert_ok(self, result):
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_tvheadend_dry_run_is_read_only_and_resolves_account(self):
        result = self.run_script("--app", "tvheadend", "--dry-run", "303")
        self.assert_ok(result)
        self.assertIn("application tvheadend, user hts, GID 44", result.stdout)
        self.assertFalse(self.state.exists())
        self.assertFalse((self.rules / "99-tvtuner-power.rules").exists())
        self.assertEqual(self.config.read_text(), "rootfs: storage:root\n")

    def test_apply_saves_profile_and_rerun_reuses_it(self):
        self.assert_ok(self.run_script("--app", "tvheadend", "--yes", "303"))
        self.assertEqual((self.state / "profile").read_text(), "tvheadend\thts\n")
        self.assertEqual((self.state / "devices").read_text().count("mode=0660"), 4)
        result = self.run_script("--dry-run", "303")
        self.assert_ok(result)
        self.assertIn("application tvheadend, user hts", result.stdout)
        original = self.config.read_bytes()
        self.assert_ok(self.run_script("--yes", "303"))
        self.assertEqual(self.config.read_bytes(), original)

    def test_explicit_custom_user_is_kept(self):
        self.assert_ok(self.run_script("--app", "tvheadend", "--user", "recorder", "--yes", "303"))
        self.assertEqual((self.state / "profile").read_text(), "tvheadend\trecorder\n")

    def test_stopped_tvheadend_needs_gid_but_not_a_running_service(self):
        result = self.run_script("--app", "tvheadend", "--dry-run", "303", MOCK_CT_STATE="stopped")
        self.assertNotEqual(result.returncode, 0)
        result = self.run_script("--app", "tvheadend", "--gid", "44", "--yes", "303", MOCK_CT_STATE="stopped")
        self.assert_ok(result)
        self.assertIn("verification deferred", result.stderr)

    def test_daemon_failure_returns_incomplete_verification_after_saving_config(self):
        result = self.run_script("--app", "tvheadend", "--yes", "303", FAIL_DAEMON="true")
        self.assertEqual(result.returncode, 2, result.stdout + result.stderr)
        self.assertTrue((self.state / "profile").exists())

    def test_remove_does_not_need_service_user_and_removes_profile(self):
        self.assert_ok(self.run_script("--app", "tvheadend", "--yes", "303"))
        self.assert_ok(self.run_script("--remove", "--yes", "303"))
        self.assertFalse((self.state / "profile").exists())
        self.assertNotIn("mode=0660", self.config.read_text())

    def test_legacy_plex_apply_still_works(self):
        result = self.run_script("--yes", "303")
        self.assert_ok(result)
        self.assertEqual((self.state / "profile").read_text(), "plex\tplex\n")

    def test_unmanaged_target_directory_mount_is_rejected_before_changes(self):
        original = f"lxc.mount.entry: {self.fixture.dev}/dvb dev/dvb none bind,create=dir\n"
        self.config.write_text(original)
        self.assertNotEqual(self.run_script("--app", "tvheadend", "--yes", "303").returncode, 0)
        self.assertEqual(self.config.read_text(), original)
        self.assertFalse(self.state.exists())

    def test_no_hardware_does_not_replace_container_config_or_saved_profiles(self):
        self.fixture.clear_hardware()
        self.assertNotEqual(self.run_script("--app", "tvheadend", "--yes", "303").returncode, 0)
        self.assertEqual(self.config.read_text(), "rootfs: storage:root\n")
        self.assertFalse((self.state / "profile").exists())

    def test_changing_owned_mapping_reports_the_required_source_stop_start(self):
        self.fixture.tuner("DUAL", [1, 2], port="1-2", pid="0265")
        self.assert_ok(self.run_script("--app", "tvheadend", "--all-tuners", "--yes", "303"))
        result = self.run_script("--tuner", "SOLO", "--yes", "303")
        self.assertEqual(result.returncode, 2, result.stdout + result.stderr)
        self.assertIn("revoke its old live nodes", result.stderr)
        self.assertEqual((self.state / "selection").read_text(), "usb:2040:0264:SOLO\n")

if __name__ == "__main__":
    unittest.main()
