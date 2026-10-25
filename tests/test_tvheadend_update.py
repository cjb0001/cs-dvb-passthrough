"""TVHeadend package updates retain host choices and refresh daemon groups."""

import os
from pathlib import Path
import subprocess
import tempfile
import unittest

from test_passthrough import SCRIPT


COMMUNITY = SCRIPT.parent / "community-scripts"
SHIM = COMMUNITY / "ProxmoxVE/ct/tvheadend.sh"
MOCKS = r'''
header_info() { :; }
check_container_storage() { :; }
check_container_resources() { :; }
msg_info() { :; }
msg_ok() { printf '%s\n' "$*"; }
msg_error() { printf '%s\n' "$*" >&2; }
apt() { printf 'apt %s\n' "$*" >> "$ACTION_LOG"; [[ ${FAIL_APT:-false} == false ]]; }
systemctl() {
  printf 'systemctl %s\n' "$*" >> "$ACTION_LOG"
  case "$1" in
    show)
      if [[ $* == *MainPID* ]]; then printf '%s\n' "${PID_VALUE:-123}"
      elif [[ $* == *User* ]]; then printf '%s\n' "${UNIT_USER:-}"; fi
      ;;
    restart|enable)
      [[ ${FAIL_RESTART:-false} == false ]] || return 9
      printf 'Uid:\t%s\t%s\t%s\t%s\nGid:\t990\t990\t990\t990\nGroups:\t%s\n' \
        "${DAEMON_UID:-990}" "${DAEMON_UID:-990}" "${DAEMON_UID:-990}" "${DAEMON_UID:-990}" "${CURRENT_GROUPS:-990 44}" > "$dvb_proc_root/123/status"
      ;;
    is-active) [[ ${FAIL_ACTIVE:-false} == false ]];;
  esac
}
runuser() {
  printf 'runuser %s\n' "$*" >> "$ACTION_LOG"
  [[ ${FAIL_ACCESS:-false} == false && " ${CURRENT_GROUPS:-990 44} " == *' 44 '* ]]
}
id() {
  case "${1:-}" in
    -u) printf '990\n';;
    -G) printf '%s\n' "${CURRENT_GROUPS:-990 44}";;
  esac
  return 0
}
stat() { printf '44\n'; }
getent() {
  if [[ $1 == passwd ]]; then printf 'hts:x:990:990::/var/lib/tvheadend:/usr/sbin/nologin\n'
  else printf 'video:x:44:\n'; fi
}
groupadd() { return 99; }
usermod() { printf 'usermod %s\n' "$*" >> "$ACTION_LOG"; CURRENT_GROUPS='990 44'; }
pct() { printf 'Unexpected host access\n' >&2; return 98; }
incus() { return 98; }
dvb_discover_tuners() { return 98; }
'''


class TVHeadendUpdateTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="tvheadend-update-")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.devices = self.root / "dev/dvb"
        self.service_state = self.root / "tvheadend"
        self.service_state.mkdir()
        self.proc = self.root / "proc/123"
        self.proc.mkdir(parents=True)
        (self.proc / "status").write_text("Uid:\t990\t990\t990\t990\nGid:\t990\t990\t990\t990\nGroups:\t990 44\n")
        self.module = self.root / "dvb.func"
        self.banner = self.root / "dvb-banner.func"
        self.helper = self.root / "aliases"
        self.roster_state = self.root / "banner-state"
        self.log = self.root / "actions"
        self.log.write_text("")
        self.selection = self.root / "host-selection"
        self.selection.write_text("usb:2040:0264:SOLO\n")
        source = SHIM.read_text()
        self.functions = source[source.index("function prepare_tvheadend_dvb()"):].split("\nstart\n", 1)[0]
        self.functions = self.paths(self.functions)

    def paths(self, source):
        return (source.replace("/usr/local/lib/community-scripts/dvb.func", str(self.module))
                .replace("/usr/local/lib/community-scripts/dvb-banner.func", str(self.banner))
                .replace("/usr/local/sbin/dvb-passthrough-aliases", str(self.helper))
                .replace("/var/lib/dvb-passthrough", str(self.roster_state))
                .replace("/var/lib/tvheadend", str(self.service_state))
                .replace("/dev/dvb", str(self.devices))
                .replace("/dev/media", str(self.root / "dev/media"))
                .replace('"/proc/$pid/status"', f'"{self.root}/proc/$pid/status"'))

    def device(self):
        node = self.devices / "adapter0/frontend0"
        node.parent.mkdir(parents=True, exist_ok=True)
        node.symlink_to("/dev/null")
        return node

    def install_module(self):
        self.module.write_text(self.paths((COMMUNITY / "core/lib/dvb.func").read_text()))

    def shell(self, commands="update_script", **environment):
        return subprocess.run(["bash", "-c", "set -euo pipefail\n" + MOCKS + self.functions + "\n" + commands],
                              env=dict(os.environ, ACTION_LOG=str(self.log), dvb_proc_root=str(self.root / "proc"),
                                       dvb_banner_state=str(self.roster_state),
                                       dvb_tvh_defaults=str(self.root / "defaults"), STD="", **environment),
                              capture_output=True, text=True)

    def assert_ok(self, result):
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_update_without_dvb_keeps_ordinary_upstream_restart(self):
        self.assert_ok(self.shell())
        log = self.log.read_text()
        self.assertIn("apt install -y tvheadend", log)
        self.assertEqual(log.count("systemctl restart tvheadend"), 1)
        self.assertNotIn("runuser", log)

    def test_update_preserves_host_selection_and_repairs_groups_before_one_restart(self):
        self.device()
        self.install_module()
        original = self.selection.read_bytes()
        result = self.shell(CURRENT_GROUPS="990")
        self.assert_ok(result)
        log = self.log.read_text()
        self.assertIn("usermod -aG video hts", log)
        self.assertLess(log.index("usermod"), log.index("systemctl restart"))
        self.assertEqual(log.count("systemctl restart tvheadend"), 1)
        self.assertEqual(self.selection.read_bytes(), original)
        self.assertIn("runuser -u hts", log)

    def test_current_groups_do_not_trigger_membership_changes(self):
        self.device()
        self.install_module()
        self.assert_ok(self.shell())
        self.assertNotIn("usermod", self.log.read_text())

    def test_package_failure_stops_before_helpers_and_restart_even_in_conditional(self):
        self.device()
        self.install_module()
        result = self.shell("if update_script; then exit 0; else exit 17; fi", FAIL_APT="true")
        self.assertNotEqual(result.returncode, 0)
        self.assertNotIn("systemctl restart", self.log.read_text())
        self.assertNotIn("runuser", self.log.read_text())

    def test_restart_and_inactive_service_failures_are_reported(self):
        for failure in ("FAIL_RESTART", "FAIL_ACTIVE"):
            with self.subTest(failure=failure):
                self.assertNotEqual(self.shell(**{failure: "true"}).returncode, 0)

    def test_permission_failure_does_not_report_verified_success(self):
        self.device()
        self.install_module()
        result = self.shell(FAIL_ACCESS="true")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("DVB preparation failed", result.stderr)
        self.assertNotIn("Updated successfully!", result.stdout)

    def test_existing_alias_helper_runs_before_permissions_and_is_preserved(self):
        self.device()
        self.install_module()
        self.helper.write_text('#!/bin/sh\nprintf "aliases\\n" >> "$ACTION_LOG"\n')
        self.helper.chmod(0o755)
        original = self.helper.read_bytes()
        self.assert_ok(self.shell())
        log = self.log.read_text()
        self.assertLess(log.index("aliases"), log.index("runuser"))
        self.assertEqual(self.helper.read_bytes(), original)

    def test_older_container_without_persisted_permissions_module_is_checked(self):
        self.device()
        self.assert_ok(self.shell())
        self.assertIn("runuser -u hts", self.log.read_text())

    def test_daemon_uid_mismatch_after_restart_is_reported(self):
        self.device()
        self.install_module()
        result = self.shell(DAEMON_UID="991")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("running as UID 991", result.stderr)

    def test_missing_configured_frontend_is_not_treated_as_no_dvb(self):
        self.install_module()
        self.roster_state.mkdir()
        (self.roster_state / "banner-devices.tsv").write_text(
            f"# Managed by dvb-passthrough.sh\nusb:2040:0264:SOLO\tSolo\t{self.devices}/adapter0/frontend0\n")
        result = self.shell()
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("Configured DVB frontend missing", result.stderr)

    def test_default_account_resolver_reads_custom_options_as_data(self):
        self.install_module()
        defaults = self.root / "defaults"
        defaults.write_text('OPTIONS="-u recorder -g video"\n')
        result = self.shell(f'source "{self.module}"; dvb_tvheadend_user', PID_VALUE="0")
        self.assert_ok(result)
        self.assertEqual(result.stdout.strip(), "recorder")

    def test_submission_patch_contains_the_actual_update_helpers(self):
        patch = (COMMUNITY / "ProxmoxVE/ct/tvheadend_dvb.patch").read_text()
        added = "\n".join(line[1:] for line in patch.splitlines() if line.startswith("+") and not line.startswith("+++"))
        source = SHIM.read_text()
        helpers = source[source.index("function prepare_tvheadend_dvb()"):source.index("function update_script()")].strip()
        self.assertIn(helpers, added)


if __name__ == "__main__":
    unittest.main()
