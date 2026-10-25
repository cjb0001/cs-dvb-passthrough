"""Regression tests for ownership and recovery, without host/container changes."""

import os
from pathlib import Path
import subprocess
import tempfile
import unittest


SCRIPT = Path(__file__).resolve().parents[1] / "dvb-passthrough.sh"
HELPERS = SCRIPT.read_text().split("\nDRY_RUN=false\n", 1)[0]
NODE0 = "dev0: path=/dev/dvb/adapter0/frontend0,uid=0,gid=1000,mode=0660"
NODE1 = "dev0: path=/dev/dvb/adapter1/frontend0,uid=0,gid=1000,mode=0660"
BEGIN = "# --- DVB TV Tuner Passthrough ---"
END = "# --- End DVB TV Tuner Passthrough ---"
ROOTFS = "rootfs: local:100/vm-100-disk-0.raw"

MOCKS = r'''
pct() { return "${PCT_FAILURE:-0}"; }
incus() {
  local file
  if [[ $2 == show ]]; then
    [[ ${FAIL_SHOW:-0} == 0 ]] || return 9
    printf 'devices:\n'
    [[ -z ${INHERITED_DEVICE:-} ]] || printf '  %s:\n    type: unix-char\n' "$INHERITED_DEVICE"
    return 0
  fi
  case "${3:-}" in
    list)
      for file in "$MOCK_DB"/*; do
        [[ ! -f $file ]] || basename "$file"
      done
      ;;
    add)
      [[ $5 != "${FAIL_ADD:-}" ]] || return 9
      [[ ! -f $MOCK_DB/$5 ]] || return 1
      printf '%s\n' "$@" > "$MOCK_DB/$5"
      ;;
    set)
      [[ -f $MOCK_DB/$5 ]] || return 1
      printf '%s\n' "$@" > "$MOCK_DB/$5"
      ;;
    remove)
      [[ $5 != "${FAIL_REMOVE:-}" ]] || return 9
      [[ -f $MOCK_DB/$5 ]] || return 1
      rm -- "$MOCK_DB/$5"
      ;;
    *) return 98 ;;
  esac
  return 0
}
'''


class PassthroughTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="dvb-regression-")
        self.addCleanup(self.temporary.cleanup)
        self.directory = Path(self.temporary.name)
        self.state = self.directory / "state"
        self.state.mkdir()
        self.database = self.directory / "db"
        self.database.mkdir()
        self.configuration = self.directory / "container.conf"
        self.configuration.write_text(ROOTFS + "\n")

    def run_shell(self, commands, **environment):
        return subprocess.run(
            ["bash", "-c", HELPERS + "\n" + MOCKS + "\n" + commands],
            env=dict(
                os.environ,
                STATE=str(self.state),
                CONF_FILE=str(self.configuration),
                MOCK_DB=str(self.database),
                TARGET_CT="100",
                CT_GID="1000",
                **environment,
            ),
            capture_output=True,
            text=True,
        )

    def assert_success(self, result):
        self.assertEqual(result.returncode, 0, result.stderr)

    def set_tracking(self, *entries):
        (self.state / "devices").write_text("".join(entry + "\n" for entry in entries))

    def tracked(self):
        return (self.state / "devices").read_text().splitlines()

    def configured(self):
        return sorted(path.name for path in self.database.iterdir())

    def test_incus_profile_collision_prevents_removing_existing_devices(self):
        self.set_tracking("dvbpt_dvb_adapter9_frontend0")
        (self.database / "dvbpt_dvb_adapter9_frontend0").write_text("original")
        result = self.run_shell('PASSTHROUGH_DEVICES=(/dev/dvb/adapter0/frontend0); configure_incus_devices',
                                INHERITED_DEVICE="dvbpt_dvb_adapter0_frontend0")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("Inherited profile device collision", result.stderr)
        self.assertEqual(self.configured(), ["dvbpt_dvb_adapter9_frontend0"])

    def test_incus_failed_expanded_query_prevents_mutations(self):
        result = self.run_shell('set -o pipefail; PASSTHROUGH_DEVICES=(/dev/dvb/adapter0/frontend0); configure_incus_devices', FAIL_SHOW="1")
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(self.configured(), [])

    def incus_context(self, instance="RUNNING,CONTAINER", project="default"):
        return self.run_shell('''incus() {
  if [[ $1 == project ]]; then printf '%s\\n' "$PROJECT_REPLY"
  elif [[ $1 == list ]]; then printf '%s\\n' "$INSTANCE_REPLY"
  else return 98; fi
}
incus_target_context || exit
printf '%s\\n' "$STATE" "$CT_RUNNING"
''', PROJECT_REPLY=project, INSTANCE_REPLY=instance)

    def test_incus_project_scopes_ownership(self):
        result = self.incus_context(project="media")
        self.assert_success(result)
        self.assertEqual(result.stdout.splitlines(), ["/var/lib/dvb-passthrough/incus/media/100", "true"])
        result = self.incus_context(instance="STOPPED,CONTAINER")
        self.assert_success(result)
        self.assertEqual(result.stdout.splitlines(), ["/var/lib/dvb-passthrough/100", "false"])

    def test_incus_ephemeral_container_is_accepted(self):
        self.assert_success(self.incus_context(instance="RUNNING,CONTAINER (EPHEMERAL)"))

    def test_incus_vm_missing_and_ambiguous_targets_are_rejected(self):
        for instance in ("RUNNING,VIRTUAL-MACHINE", "", "RUNNING,CONTAINER\nSTOPPED,CONTAINER"):
            with self.subTest(instance=instance):
                self.assertNotEqual(self.incus_context(instance=instance).returncode, 0)

    def test_incus_exec_forwards_stdin_without_terminal_allocation(self):
        result = self.run_shell('''incus() { printf '%s\\n' "$*"; cat; }
PLATFORM=incus
run_in_ct sh -c cat <<<'manifest contents'
''')
        self.assert_success(result)
        self.assertEqual(result.stdout.splitlines(), ["exec 100 -T -- sh -c cat", "manifest contents"])

    def test_bash_syntax(self):
        self.assert_success(subprocess.run(["bash", "-n", str(SCRIPT)], capture_output=True, text=True))

    def test_pve_import_then_serialization_and_removal(self):
        self.configuration.write_text(f"{ROOTFS}\n{BEGIN}\n{NODE0}\n{END}\n")
        result = self.run_shell('''
BEFORE=$(sha256sum "$CONF_FILE")
CLEAN=$(pve_clean_config "$CONF_FILE")
ENTRY=$(pve_tracked_entries "$CONF_FILE")
pve_save_config "$CLEAN"$'\n'"$ENTRY" "$ENTRY"
''')
        self.assert_success(result)
        self.assertEqual(self.tracked(), [NODE0])
        # Proxmox gathers comments at the top and sorts properties below them.
        self.configuration.write_text(f"{BEGIN}\n{END}\n{NODE0}\n{ROOTFS}\n")
        result = self.run_shell('''
BEFORE=$(sha256sum "$CONF_FILE")
CLEAN=$(pve_clean_config "$CONF_FILE")
pve_save_config "$CLEAN"
''')
        self.assert_success(result)
        self.assertEqual(self.configuration.read_text(), ROOTFS + "\n")
        self.assertEqual(self.tracked(), [])

    def test_pve_reordered_properties_and_unnamed_path(self):
        self.set_tracking(NODE0)
        self.configuration.write_text(
            "# user description\n\n"
            "dev0: /dev/dvb/adapter0/frontend0,mode=0660,gid=1000,uid=0\n"
            + ROOTFS + "\n"
        )
        result = self.run_shell('pve_clean_config "$CONF_FILE"')
        self.assert_success(result)
        self.assertEqual(result.stdout, "# user description\n\n" + ROOTFS + "\n")

    def test_pve_changed_tracked_slot_is_preserved(self):
        self.set_tracking(NODE0)
        original = NODE1 + "\n" + ROOTFS + "\n"
        self.configuration.write_text(original)
        result = self.run_shell('pve_clean_config "$CONF_FILE"')
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("changed outside", result.stderr)
        self.assertEqual(self.configuration.read_text(), original)

    def test_pve_unmanaged_devices_survive(self):
        self.set_tracking(NODE0)
        unrelated = "dev1: path=/dev/media9,uid=0,gid=44,mode=0660"
        self.configuration.write_text(f"{NODE0}\n{unrelated}\n{ROOTFS}\n")
        result = self.run_shell('pve_clean_config "$CONF_FILE"')
        self.assert_success(result)
        self.assertEqual(result.stdout, unrelated + "\n" + ROOTFS + "\n")

    def test_pve_interrupted_save_is_recoverable(self):
        self.set_tracking(NODE0)
        self.configuration.write_text(NODE0 + "\n" + ROOTFS + "\n")
        result = self.run_shell('''
BEFORE=$(sha256sum "$CONF_FILE")
CLEAN=$(pve_clean_config "$CONF_FILE")
pve_save_config "$CLEAN"$'\n'"$NEW_ENTRY" "$NEW_ENTRY"
''', NEW_ENTRY=NODE1, PCT_FAILURE="9")
        self.assertNotEqual(result.returncode, 0)
        self.assertTrue((self.state / "devices.pending").exists())
        self.assertIn(NODE1, self.configuration.read_text())
        result = self.run_shell('''
BEFORE=$(sha256sum "$CONF_FILE")
CLEAN=$(pve_clean_config "$CONF_FILE")
pve_save_config "$CLEAN"
''')
        self.assert_success(result)
        self.assertEqual(self.configuration.read_text(), ROOTFS + "\n")
        self.assertEqual(self.tracked(), [])
        self.assertFalse((self.state / "devices.pending").exists())

    def test_pve_concurrent_config_change_prevents_write(self):
        original = self.configuration.read_text()
        result = self.run_shell('BEFORE=outdated; pve_save_config "replacement"')
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(self.configuration.read_text(), original)

    def test_pve_unsafe_legacy_blocks_are_rejected(self):
        for original in (
            f"{END}\n{ROOTFS}\n{BEGIN}\n",
            f"{BEGIN}\n{ROOTFS}\n{END}\n",
            f"{BEGIN}\n{END}\n{NODE0}\n{ROOTFS}\n",
            f"{BEGIN}\n{NODE0}\n",
        ):
            with self.subTest(configuration=original):
                self.configuration.write_text(original)
                result = self.run_shell('pve_clean_config "$CONF_FILE"')
                self.assertNotEqual(result.returncode, 0)
                self.assertEqual(self.configuration.read_text(), original)

    def test_incus_rediscovery_removes_stale_entries(self):
        old = "dvbpt_dvb_adapter0_frontend0"
        current = "dvbpt_dvb_adapter1_frontend0"
        self.set_tracking(old)
        (self.database / old).touch()
        (self.database / "manual").touch()
        result = self.run_shell('''
PASSTHROUGH_DEVICES=(/dev/dvb/adapter1/frontend0)
configure_incus_devices
''')
        self.assert_success(result)
        self.assertEqual(self.configured(), [current, "manual"])
        self.assertEqual(self.tracked(), [current])
        self.assertIn("required=false", (self.database / current).read_text())

    def test_incus_partial_removal_can_be_retried(self):
        first, second = "dvbpt_first", "dvbpt_second"
        self.set_tracking(first, second)
        for key in (first, second):
            (self.database / key).touch()
        commands = '''
load_incus_tracking
for key in "${TRACKED_DEVICES[@]}"; do remove_incus_device "$key"; done
'''
        result = self.run_shell(commands, FAIL_REMOVE=second)
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(self.tracked(), [second])
        result = self.run_shell(commands)
        self.assert_success(result)
        self.assertEqual(self.configured(), [])
        self.assertEqual(self.tracked(), [])

    def test_incus_absent_and_duplicate_entries_are_tolerated(self):
        self.set_tracking("dvbpt_absent", "dvbpt_absent", "dvbpt_present")
        (self.database / "dvbpt_present").touch()
        result = self.run_shell('''
load_incus_tracking
for key in "${TRACKED_DEVICES[@]}"; do remove_incus_device "$key"; done
''')
        self.assert_success(result)
        self.assertEqual(self.configured(), [])
        self.assertEqual(self.tracked(), [])

    def test_incus_failed_add_can_be_retried_without_duplicate_tracking(self):
        key = "dvbpt_dvb_adapter0_frontend0"
        commands = 'PASSTHROUGH_DEVICES=(/dev/dvb/adapter0/frontend0); configure_incus_devices'
        result = self.run_shell(commands, FAIL_ADD=key)
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(self.tracked(), [key])
        result = self.run_shell(commands)
        self.assert_success(result)
        self.assertEqual(self.tracked(), [key])
        self.assertEqual(self.configured(), [key])

    def test_incus_unmanaged_collision_prevents_all_device_changes(self):
        stale, collision = "dvbpt_stale", "dvbpt_dvb_adapter0_frontend0"
        self.set_tracking(stale)
        for key in (stale, collision):
            (self.database / key).touch()
        result = self.run_shell(
            'PASSTHROUGH_DEVICES=(/dev/dvb/adapter0/frontend0); configure_incus_devices'
        )
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(self.tracked(), [stale])
        self.assertEqual(self.configured(), sorted([stale, collision]))

    def test_incus_existing_managed_devices_become_optional(self):
        key = "dvbpt_dvb_adapter0_frontend0"
        self.set_tracking(key)
        (self.database / key).touch()
        result = self.run_shell(
            'PASSTHROUGH_DEVICES=(/dev/dvb/adapter0/frontend0); configure_incus_devices'
        )
        self.assert_success(result)
        self.assertIn("required=false", (self.database / key).read_text())


if __name__ == "__main__":
    unittest.main()
