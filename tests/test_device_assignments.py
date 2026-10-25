"""Cross-container ownership, assignment recovery and shared USB power policy."""

import unittest
import os

import test_stable


class AssignmentTests(unittest.TestCase):
    def setUp(self):
        self.fixture = test_stable.StableTests("test_duplicate_serial_on_two_devices_is_rejected")
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)
        self.fixture.tuner("DUAL", [1, 2])
        self.fixture.tuner("SOLO", [3], port="1-2.3", pid="0264")
        # Distinguish physical devices for major/minor overlap checks.
        for path in (self.fixture.dev / "dvb/adapter3").iterdir():
            path.unlink()
            path.symlink_to("/dev/zero")
        self.root = self.fixture.root
        self.pve = self.root / "pve"
        self.pve.mkdir()
        self.state = self.root / "state/proxmox/303"
        self.state.mkdir()
        self.rules = self.root / "power.rules"

    def shell(self, commands):
        return self.fixture.shell(f'''STATE="{self.state}"
dvb_pve_config_dir="{self.pve}"
dvb_power_rules="{self.rules}"
dvb_power_backup_root="{self.root}/backups"
dvb_operation_lock="{self.root}/operation.lock"
dvb_selection_defaults=(); dvb_select_all=false
udevadm() {{ :; }}
pct() {{ case "$1" in status) printf 'status: %s\\n' "${{CT_STATUS:-stopped}}";; exec) printf '%s' "${{LIVE_NUMBERS:-}}";; *) return 98;; esac; }}
dvb_discover_tuners
{commands}''')

    def saved(self, name, id):
        directory = self.root / "state" / name
        directory.mkdir(parents=True, exist_ok=True)
        (directory / "selection").write_text(id + "\n")
        return directory

    def assert_ok(self, result):
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_saved_owner_rejects_same_device_but_allows_split_selection(self):
        self.saved("proxmox/100", "usb:2040:0265:DUAL")
        self.assert_ok(self.shell("dvb_select_tuners SOLO; dvb_assignment_preflight proxmox 303"))
        result = self.shell("dvb_select_tuners DUAL; dvb_assignment_preflight proxmox 303")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("proxmox/100", result.stderr)
        self.assertNotEqual(self.shell("dvb_select_all=true; dvb_select_tuners; dvb_assignment_preflight proxmox 303").returncode, 0)

    def test_current_target_can_reuse_its_saved_selection(self):
        (self.state / "selection").write_text("usb:2040:0264:SOLO\n")
        self.assert_ok(self.shell("dvb_select_tuners SOLO; dvb_assignment_preflight proxmox 303"))

    def test_legacy_stable_map_still_reserves_physical_identity(self):
        directory = self.root / "state/proxmox/100"
        (directory / "stable-map").write_text("1000 2040 0264 SOLO 0 1 frontend0\n")
        self.assertNotEqual(self.shell("dvb_select_tuners SOLO; dvb_assignment_preflight proxmox 303").returncode, 0)

    def test_menu_labels_known_owners(self):
        self.saved("proxmox/100", "usb:2040:0264:SOLO")
        result = self.shell('printf "%s\\n" "${dvb_tuner_labels[@]}"')
        self.assertIn("[Assigned: proxmox/100]", result.stdout)

    def test_unmanaged_native_paths_and_aliases_are_detected(self):
        alias = self.root / "private-alias"
        alias.symlink_to("/dev/zero")
        for value in (str(self.fixture.dev / "dvb/adapter3/frontend0"), str(alias)):
            (self.pve / "100.conf").write_text(f"dev4: gid=44,{value},mode=0660\n")
            self.assertNotEqual(self.shell("dvb_select_tuners SOLO; dvb_assignment_preflight proxmox 303").returncode, 0)

    def test_broad_mount_is_a_conflict_but_unrelated_devices_are_preserved(self):
        config = self.pve / "100.conf"
        config.write_text(f"lxc.mount.entry: {self.fixture.dev}/dvb dev/dvb none bind,create=dir\n")
        self.assertNotEqual(self.shell("dvb_select_tuners SOLO; dvb_assignment_preflight proxmox 303").returncode, 0)
        config.write_text("dev0: /dev/dri/renderD128,gid=993\n")
        self.assert_ok(self.shell("dvb_select_tuners SOLO; dvb_assignment_preflight proxmox 303"))
        self.assertEqual(config.read_text(), "dev0: /dev/dri/renderD128,gid=993\n")

    def test_live_nodes_require_source_stop_start_after_config_removal(self):
        (self.pve / "100.conf").write_text("rootfs: storage:root\n")
        result = self.shell("dvb_select_tuners SOLO; CT_STATUS=running; LIVE_NUMBERS=1:5; dvb_assignment_preflight proxmox 303")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("still has a live node", result.stderr)
        self.assert_ok(self.shell("dvb_select_tuners SOLO; CT_STATUS=running; LIVE_NUMBERS=1:3; dvb_assignment_preflight proxmox 303"))

    def test_unavailable_runtime_inventory_fails_closed(self):
        (self.pve / "100.conf").write_text("")
        self.assertNotEqual(self.shell("dvb_select_tuners SOLO; pct() { return 9; }; dvb_assignment_preflight proxmox 303").returncode, 0)
        self.assertFalse(self.rules.exists())

    def test_incus_expanded_profile_source_in_another_project_is_checked(self):
        result = self.shell(f'''dvb_incus_query() {{
  case "$*" in
    'project list -c n --format csv') printf 'other\\n';;
    '--project other list -c nts --format csv') printf 'receiver,CONTAINER,STOPPED\\n';;
    '--project other config show receiver --expanded') printf 'devices:\\n  inherited:\\n    source: "{self.fixture.dev}/dvb"\\n    type: disk\\n';;
    *) return 98;;
  esac
}}
dvb_select_tuners SOLO; INCUS_PROJECT=default; dvb_assignment_preflight incus 303''')
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("Incus other/receiver", result.stderr)

    def test_incus_query_errors_are_not_treated_as_no_assignments(self):
        self.assertNotEqual(self.shell("dvb_select_tuners SOLO; dvb_incus_query() { return 9; }; dvb_assignment_preflight incus 303").returncode, 0)

    def test_numeric_character_mapping_is_detected_without_a_source_path(self):
        result = self.shell('''dvb_select_tuners SOLO
sources=$(printf 'devices:\\n  numeric:\\n    type: unix-char\\n    major: 1\\n    minor: 5\\n' | dvb_incus_device_sources)
dvb_check_source "$sources" 'Incus other/receiver' ''')
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("already exposes", result.stderr)

    def test_power_policy_is_union_and_survives_a_missing_saved_device(self):
        self.saved("proxmox/100", "usb:2040:0265:DUAL")
        self.assert_ok(self.shell("dvb_select_tuners SOLO; dvb_power_update"))
        text = self.rules.read_text()
        self.assertIn('ATTR{serial}=="DUAL"', text)
        self.assertIn('ATTR{serial}=="SOLO"', text)
        # A missing saved USB device retains a serial-specific reconnect rule.
        self.fixture.clear_hardware()
        self.assert_ok(self.shell("dvb_selected_indices=(); dvb_power_update false"))
        text = self.rules.read_text()
        self.assertIn('ATTR{serial}=="DUAL"', text)
        self.assertNotIn('ATTR{serial}=="SOLO"', text)

    def test_replacing_target_keeps_old_power_policy_until_selection_commit(self):
        (self.state / "selection").write_text("usb:2040:0265:DUAL\n")
        self.assert_ok(self.shell("dvb_select_tuners SOLO; dvb_power_update"))
        self.assertIn('ATTR{serial}=="DUAL"', self.rules.read_text())
        (self.state / "selection").write_text("usb:2040:0264:SOLO\n")
        self.assert_ok(self.shell("dvb_select_tuners SOLO; dvb_power_update"))
        self.assertNotIn('ATTR{serial}=="DUAL"', self.rules.read_text())

    def test_remove_preserves_other_target_power_rules(self):
        self.saved("proxmox/100", "usb:2040:0265:DUAL")
        self.assert_ok(self.shell("dvb_selected_indices=(); dvb_power_update false"))
        self.assertIn('ATTR{serial}=="DUAL"', self.rules.read_text())
        self.assertNotIn('ATTR{serial}=="SOLO"', self.rules.read_text())

    def test_only_current_and_saved_usb_devices_have_power_control_changed(self):
        self.fixture.tuner("OTHER", [4], port="1-4", pid="0264")
        for port in ("1-1", "1-2.3", "1-4"):
            control = self.fixture.sys / "devices" / port / "power/control"
            control.parent.mkdir()
            control.write_text("auto\n")
        self.saved("proxmox/100", "usb:2040:0265:DUAL")
        self.assert_ok(self.shell("dvb_select_tuners SOLO; dvb_power_update"))
        for port in ("1-1", "1-2.3"):
            self.assertEqual((self.fixture.sys / "devices" / port / "power/control").read_text(), "on\n")
        self.assertEqual((self.fixture.sys / "devices/1-4/power/control").read_text(), "auto\n")
        self.assertNotIn('ATTR{serial}=="OTHER"', self.rules.read_text())

    def test_unrecognized_power_file_is_preserved(self):
        self.rules.write_text("# administrator rules\n")
        self.assertNotEqual(self.shell("dvb_select_tuners SOLO; dvb_power_update").returncode, 0)
        self.assertEqual(self.rules.read_text(), "# administrator rules\n")

    def test_missing_path_selected_usb_retains_its_verified_port_rule(self):
        (self.fixture.sys / "devices/1-1/serial").unlink()
        self.saved("proxmox/100", "path:1-1")
        self.assert_ok(self.shell("dvb_select_tuners SOLO; dvb_power_update"))
        self.assertIn('KERNEL=="1-1"', self.rules.read_text())
        self.fixture.clear_hardware()
        self.assert_ok(self.shell("dvb_selected_indices=(); dvb_power_update false"))
        self.assertIn('KERNEL=="1-1"', self.rules.read_text())

    def test_creation_writes_compatible_manifests_under_the_shared_lock(self):
        config = self.pve / "303.conf"
        config.write_text("dev0: /dev/dri/renderD128,gid=993\n")
        result = self.shell(f'dvb_select_tuners SOLO; CTID=303; APP=Tvheadend; dvb_community_pve_config "{config}"')
        self.assert_ok(result)
        self.assertIn("usb:2040:0264:SOLO", (self.state / "selection").read_text())
        self.assertEqual((self.state / "devices").read_text().count("mode=0660"), 4)
        self.assertFalse((self.state / "devices.pending").exists())
        self.assertEqual((self.state / "profile").read_text(), "tvheadend\thts\n")

    def test_creation_rechecks_claims_and_does_not_write_on_conflict(self):
        config = self.pve / "303.conf"
        config.write_text("rootfs: storage:root\n")
        self.saved("proxmox/100", "usb:2040:0264:SOLO")
        self.assertNotEqual(self.shell(f'dvb_select_tuners SOLO; CTID=303; dvb_community_pve_config "{config}"').returncode, 0)
        self.assertEqual(config.read_text(), "rootfs: storage:root\n")
        self.assertFalse((self.state / "selection").exists())

    def test_creation_refuses_a_concurrent_assignment_operation(self):
        config = self.pve / "303.conf"
        config.write_text("rootfs: storage:root\n")
        result = self.shell(f'''exec 8>"$dvb_operation_lock"; flock -n 8
dvb_select_tuners SOLO; CTID=303; dvb_community_pve_config "{config}"''')
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("Another DVB operation", result.stderr)
        self.assertEqual(config.read_text(), "rootfs: storage:root\n")

    @unittest.skipIf(os.geteuid() == 0, "Non-root boundary test")
    def test_nonroot_incus_refuses_before_device_mutation(self):
        result, log = self.incus_creation()
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("requires root", result.stderr)
        self.assertFalse(log.exists())
        self.assertFalse((self.root / "operation.lock").exists())

    def incus_creation(self, expanded="devices:\\n"):
        log = self.root / "incus-additions"
        result = self.shell(f'''dvb_incus_query() {{
  case "$*" in
    'project list -c n --format csv') printf 'default\\n';;
    '--project default list -c nts --format csv') printf 'receiver,CONTAINER,RUNNING\\n';;
    *) return 98;;
  esac
}}
incus() {{
  if [[ $* == 'config show receiver --expanded' ]]; then printf '{expanded}'; return; fi
  [[ -f "{self.root}/state/receiver/devices" ]] || return 99
  grep -qxF "$5" "{self.root}/state/receiver/devices" || return 99
  printf '%s\\n' "$*" >> "{log}"
}}
dvb_select_tuners SOLO; APP=Tvheadend; INCUS_PROJECT=default; dvb_community_incus_config receiver''')
        return result, log

    @unittest.skipUnless(os.geteuid() == 0, "Privileged DVB assignment requires root")
    def test_incus_creation_journals_owned_devices_before_adding_them(self):
        result, log = self.incus_creation()
        self.assert_ok(result)
        self.assertEqual(len(log.read_text().splitlines()), 4)
        directory = self.root / "state/receiver"
        self.assertEqual((directory / "selection").read_text(), "usb:2040:0264:SOLO\n")
        self.assertEqual((directory / "profile").read_text(), "tvheadend\thts\n")

    @unittest.skipUnless(os.geteuid() == 0, "Privileged DVB assignment requires root")
    def test_incus_creation_preserves_existing_broad_target_mount(self):
        result, log = self.incus_creation(f'devices:\\n  broad:\\n    type: disk\\n    source: {self.fixture.dev}/dvb\\n')
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("already exposes", result.stderr)
        self.assertFalse(log.exists())
        self.assertFalse((self.root / "state/receiver/devices").exists())

if __name__ == "__main__":
    unittest.main()
