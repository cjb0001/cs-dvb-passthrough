"""Physical selection, community backend filtering, and menu behavior."""

import os
from pathlib import Path
import pty
import subprocess
import unittest

import test_stable
from test_passthrough import HELPERS, SCRIPT


LIBRARY = SCRIPT.parent / "community-scripts/core/lib/dvb-selection.func"


class SelectionTests(unittest.TestCase):
    def setUp(self):
        self.fixture = test_stable.StableTests("test_duplicate_serial_on_two_devices_is_rejected")
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)
        self.fixture.tuner("DUAL", [1, 2])
        self.fixture.tuner("SOLO", [3], port="1-2.3", pid="0264")

    def command(self, commands):
        return 'dvb_selection_defaults=(); dvb_select_all=false; dvb_discover_tuners\n' + commands

    def shell(self, commands):
        return self.fixture.shell(self.command(commands))

    def assert_ok(self, result):
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_shared_helpers_match_standalone_exactly(self):
        embedded = SCRIPT.read_text().split("# BEGIN shared physical tuner selection\n", 1)[1]
        embedded = embedded.split("# END shared physical tuner selection\n", 1)[0]
        library = LIBRARY.read_text()
        self.assertEqual(embedded, library[library.index("dvb_selection_error()"):])

    def test_cli_rejects_selection_in_unrelated_or_conflicting_operations(self):
        for args in [("--all-tuners", "--tuner", "SOLO"), ("--remove", "--tuner", "SOLO"),
                     ("--firmware", "--all-tuners"), ("--refresh-stable", "--tuner", "SOLO")]:
            with self.subTest(args=args):
                result = subprocess.run(["bash", str(SCRIPT), *args], capture_output=True, text=True)
                self.assertNotEqual(result.returncode, 0)
                self.assertNotIn("Neither Proxmox", result.stderr)

    def test_two_physical_devices_include_all_three_adapters(self):
        result = self.shell('printf "%s\\n" "${#dvb_tuner_roots[@]}" "${dvb_tuner_adapters[@]}"')
        self.assert_ok(result)
        self.assertEqual(result.stdout.splitlines(), ["2", "1,2", "3"])

    def test_one_selected_dual_device_includes_both_adapters_only(self):
        result = self.shell('dvb_select_tuners usb:2040:0265:DUAL; printf "%s\\n" "${dvb_selected_nodes[@]}"')
        self.assert_ok(result)
        self.assertEqual(len(result.stdout.splitlines()), 8)
        self.assertNotIn("adapter3/", result.stdout)

    def test_multiple_selectors_deduplicate_by_physical_device(self):
        result = self.shell('dvb_select_tuners DUAL usb:2040:0265:DUAL SOLO; printf "%s\\n" "${dvb_selected_ids[@]}"')
        self.assert_ok(result)
        self.assertEqual(result.stdout.splitlines(), ["usb:2040:0265:DUAL", "usb:2040:0264:SOLO"])

    def test_unattended_first_run_requires_explicit_multiple_device_selection(self):
        result = self.shell("dvb_select_tuners")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("Choose tuners explicitly", result.stderr)

    def test_saved_selection_does_not_expand_to_other_attached_devices(self):
        result = self.shell('dvb_selection_defaults=(SOLO); dvb_select_tuners; printf "%s\\n" "${dvb_selected_nodes[@]}"')
        self.assert_ok(result)
        self.assertEqual(len(result.stdout.splitlines()), 4)
        self.assertNotIn("adapter1/", result.stdout)

    def test_missing_saved_device_never_falls_back_to_all(self):
        result = self.shell("dvb_selection_defaults=(MISSING); dvb_select_tuners")
        self.assertNotEqual(result.returncode, 0)

    def test_explicit_all_includes_all_current_devices(self):
        result = self.shell('dvb_select_all=true; dvb_select_tuners; printf "%s\\n" "${#dvb_selected_ids[@]}" "${#dvb_selected_nodes[@]}"')
        self.assert_ok(result)
        self.assertEqual(result.stdout.splitlines(), ["2", "12"])

    def test_unknown_or_ambiguous_serial_is_rejected(self):
        self.assertNotEqual(self.shell("dvb_select_tuners MISSING").returncode, 0)
        self.fixture.tuner("DUAL", [4], port="2-4")
        result = self.shell("dvb_select_tuners DUAL")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("Ambiguous tuner", result.stderr)
        self.assert_ok(self.shell('dvb_select_tuners path:2-4'))

    def test_only_related_media_nodes_follow_selected_device(self):
        for name, port in [("media0", "1-1"), ("media1", "1-2.3")]:
            cls = self.fixture.sys / "class/media" / name
            cls.mkdir(parents=True)
            (cls / "device").symlink_to(self.fixture.sys / "devices" / port / (port + ":1.0"))
            (self.fixture.dev / name).symlink_to("/dev/null")
        result = self.shell('dvb_select_tuners SOLO; printf "%s\\n" "${dvb_selected_nodes[@]}"')
        self.assert_ok(result)
        self.assertIn("media1", result.stdout)
        self.assertNotIn("media0", result.stdout)

    def test_menu_checklist_selects_multiple_devices(self):
        result = self.shell('''
whiptail() { printf '1\\n2\\n' >&2; }
dvb_selection_ui=whiptail
dvb_select_tuners
printf '%s\\n' "${#dvb_selected_ids[@]}"
''')
        self.assert_ok(result)
        self.assertEqual(result.stdout.strip(), "2")

    def checklist_items(self, defaults, reply="2"):
        log = self.fixture.root / "checklist-arguments"
        result = self.shell(f'''
whiptail() {{ printf '%s\\n' "$@" > "{log}"; printf '%s\\n' '{reply}' >&2; }}
dvb_selection_ui=whiptail
dvb_selection_defaults=({defaults})
dvb_select_tuners
printf '%s\\n' "${{dvb_selected_ids[@]}}"
''')
        arguments = log.read_text().splitlines()
        items = arguments[-6:]
        return result, [(items[i], items[i+1], items[i+2]) for i in range(0, 6, 3)]

    def test_checklist_labels_new_device_and_keeps_saved_device_checked(self):
        result, items = self.checklist_items("SOLO")
        self.assert_ok(result)
        self.assertIn("[New]", items[0][1])
        self.assertEqual(items[0][2], "OFF")
        self.assertIn("[Saved]", items[1][1])
        self.assertEqual(items[1][2], "ON")
        self.assertEqual(result.stdout.strip(), "usb:2040:0264:SOLO")

    def test_initial_checklist_has_no_new_or_saved_labels(self):
        result, items = self.checklist_items("")
        self.assert_ok(result)
        for _, label, checked in items:
            self.assertNotIn("[New]", label)
            self.assertNotIn("[Saved]", label)
            self.assertEqual(checked, "OFF")

    def test_missing_saved_device_does_not_relabel_other_saved_devices_as_new(self):
        result, items = self.checklist_items("MISSING SOLO")
        self.assert_ok(result)
        self.assertIn("[Saved]", items[1][1])
        self.assertNotIn("[New]", items[1][1])
        self.assertEqual(items[1][2], "ON")
        self.assertIn("Selected tuner is unavailable", result.stderr)

    def test_saved_labels_follow_serial_after_kernel_renumbering_and_port_change(self):
        self.fixture.clear_hardware()
        self.fixture.tuner("DUAL", [4, 5], port="2-4")
        self.fixture.tuner("SOLO", [0], port="1-2.3", pid="0264")
        result, items = self.checklist_items("usb:2040:0265:DUAL")
        self.assert_ok(result)
        self.assertIn("[Saved]", items[1][1])
        self.assertIn("adapters 4,5", items[1][1])
        self.assertEqual(items[1][2], "ON")
        self.assertIn("[New]", items[0][1])

    def test_unchecking_every_checklist_item_does_not_reuse_saved_defaults(self):
        result = self.shell('''
whiptail() { :; }
dvb_selection_ui=whiptail
dvb_selection_defaults=(SOLO)
dvb_select_tuners
''')
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("Select at least one", result.stderr)

    def test_menu_cancel_is_distinct_from_selection_error(self):
        result = self.shell('whiptail() { return 1; }; dvb_selection_ui=whiptail; dvb_select_tuners')
        self.assertEqual(result.returncode, 2)

    def text_menu(self, commands, reply):
        master, slave = pty.openpty()
        try:
            script = HELPERS + '\nshopt -s nullglob\n' + self.command(commands)
            process = subprocess.Popen(["bash", "-c", script], stdin=slave,
                                       stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                       env=dict(os.environ, SYS_ROOT=str(self.fixture.sys), DEV_ROOT=str(self.fixture.dev)))
            os.write(master, reply)
            out, err = process.communicate(timeout=10)
            return process.returncode, out.decode(), err.decode()
        finally:
            os.close(master)
            os.close(slave)

    def test_text_menu_chooses_only_the_requested_physical_device(self):
        code, out, err = self.text_menu('dvb_select_tuners; printf "%s\\n" "${dvb_selected_ids[@]}"', b"2\n")
        self.assertEqual(code, 0, err)
        self.assertEqual(out.strip(), "usb:2040:0264:SOLO")

    def test_text_menu_labels_new_device_and_enter_keeps_only_saved_choice(self):
        code, out, err = self.text_menu('dvb_selection_defaults=(SOLO); dvb_select_tuners; printf "%s\\n" "${dvb_selected_ids[@]}"', b"\n")
        self.assertEqual(code, 0, err)
        self.assertEqual(out.strip(), "usb:2040:0264:SOLO")
        self.assertIn("serial DUAL [New]", err)
        self.assertIn("serial SOLO [Saved]", err)

    def test_text_menu_enter_cannot_silently_drop_a_missing_saved_device(self):
        code, _, err = self.text_menu('dvb_selection_defaults=(MISSING SOLO); dvb_select_tuners', b"\n")
        self.assertNotEqual(code, 0)
        self.assertNotIn("Enter keeps saved selection", err)

    def test_pve_entries_include_selected_nodes_and_preserve_gpu(self):
        config = self.fixture.root / "100.conf"
        config.write_text("dev0: /dev/dri/renderD128,gid=993\n")
        result = self.shell(f'dvb_select_tuners SOLO; dvb_community_pve_write "{config}"')
        self.assert_ok(result)
        text = config.read_text()
        self.assertIn("dev0: /dev/dri/renderD128,gid=993", text)
        self.assertEqual(text.count("mode=0660"), 4)
        self.assertIn("adapter3/", text)
        self.assertNotIn("adapter1/", text)
        self.assertNotIn("cgroup", text)
        self.assertNotIn("mount.entry", text)

    def test_existing_broad_pve_mount_is_not_silently_left_in_place(self):
        config = self.fixture.root / "100.conf"
        original = "lxc.mount.entry: /dev/dvb dev/dvb none bind,create=dir\n"
        config.write_text(original)
        self.assertNotEqual(self.shell(f'dvb_select_tuners SOLO; dvb_community_pve_write "{config}"').returncode, 0)
        self.assertEqual(config.read_text(), original)

    def test_incus_only_adds_selected_nodes_and_propagates_api_errors(self):
        log = self.fixture.root / "incus.log"
        result = self.shell(f'''incus() {{ printf '%s\\n' "$*" >> "{log}"; }}
dvb_select_tuners SOLO; dvb_community_incus_write plex''')
        self.assert_ok(result)
        lines = log.read_text().splitlines()
        self.assertEqual(len(lines), 4)
        self.assertTrue(all("adapter3/" in line and "mode=0660" in line for line in lines))
        self.assertNotEqual(self.shell("incus() { return 9; }; dvb_select_tuners SOLO; dvb_community_incus_write plex").returncode, 0)

    def test_incus_validates_all_selected_nodes_before_adding_any(self):
        log = self.fixture.root / "incus.log"
        result = self.shell(f'''incus() {{ printf '%s\\n' "$*" >> "{log}"; }}
dvb_select_tuners SOLO
dvb_selected_nodes+=(/device-no-longer-present)
dvb_community_incus_write plex''')
        self.assertNotEqual(result.returncode, 0)
        self.assertFalse(log.exists())

    def test_incus_empty_selection_is_an_error(self):
        self.assertNotEqual(self.shell('dvb_selected_nodes=(); dvb_community_incus_write plex').returncode, 0)

    def test_community_explicit_selection_is_reused_without_a_second_menu(self):
        result = self.shell('var_dvb_tuners=SOLO; dvb_community_select; printf "%s\\n" "$dvb_tuners"')
        self.assert_ok(result)
        self.assertEqual(result.stdout.strip(), "usb:2040:0264:SOLO")


if __name__ == "__main__":
    unittest.main()
