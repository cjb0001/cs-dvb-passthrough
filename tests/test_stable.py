"""Serial mappings against fake sysfs and harmless /dev/null character nodes."""

import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest

from test_passthrough import HELPERS


class StableTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="dvb-stable-")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.sys = self.root / "sys"
        self.dev = self.root / "dev"
        self.state = self.root / "state/proxmox/100"
        self.state.mkdir(parents=True)
        (self.sys / "class/dvb").mkdir(parents=True)

    def tuner(self, serial, adapters, port="1-1", pid="0265"):
        usb = self.sys / "devices" / port
        interface = usb / (port + ":1.0")
        interface.mkdir(parents=True, exist_ok=True)
        for key, value in {"idVendor": "2040", "idProduct": pid, "serial": serial}.items():
            (usb / key).write_text(value + "\n")
        for adapter in adapters:
            for component in ("demux0", "dvr0", "frontend0", "net0"):
                cls = self.sys / "class/dvb" / f"dvb{adapter}.{component}"
                cls.mkdir()
                (cls / "device").symlink_to(interface)
                (cls / "dev").write_text("1:3\n")
                node = self.dev / "dvb" / f"adapter{adapter}" / component
                node.parent.mkdir(parents=True, exist_ok=True)
                node.symlink_to("/dev/null")

    def clear_hardware(self):
        for cls in (self.sys / "class/dvb").iterdir():
            shutil.rmtree(cls)
        # Host aliases remain, just as they do across removal events.
        for adapter in (self.dev / "dvb").iterdir():
            if int(adapter.name.removeprefix("adapter")) < 1000:
                shutil.rmtree(adapter)

    def shell(self, commands):
        return subprocess.run(
            ["bash", "-c", HELPERS + "\nshopt -s nullglob\n" + commands],
            env=dict(os.environ, SYS_ROOT=str(self.sys), DEV_ROOT=str(self.dev),
                     STABLE_ROOT=str(self.root / "state"), STATE=str(self.state)),
            capture_output=True, text=True,
        )

    def plan(self, adapters):
        devices = " ".join(f"/dev/dvb/adapter{a}/{c}" for a in adapters
                           for c in ("demux0", "dvr0", "frontend0", "net0"))
        result = self.shell(f'PASSTHROUGH_DEVICES=({devices}); stable_plan; printf "%s\\n" "${{STABLE_PLAN[@]}}"')
        self.assertEqual(result.returncode, 0, result.stderr)
        return result.stdout

    def save_plan(self, adapters):
        plan = self.plan(adapters)
        (self.state / "stable-map").write_text(plan)
        return plan

    def assert_ok(self, result):
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_renumbering_and_usb_port_move_preserve_physical_mapping(self):
        self.tuner("DUAL", [1, 2])
        self.tuner("SOLO", [3], port="1-2.3", pid="0264")
        plan = self.save_plan([1, 2, 3])
        self.assert_ok(self.shell("stable_refresh_all"))
        self.clear_hardware()
        # Swap the physical-device order and move the dualHD to another port.
        self.tuner("SOLO", [0], port="1-2.3", pid="0264")
        self.tuner("DUAL", [4, 5], port="2-4")
        self.assert_ok(self.shell("stable_refresh_all"))
        for line in plan.splitlines():
            slot, _, _, serial, channel, _, _ = line.split()
            expected = 0 if serial == "SOLO" else [4, 5][int(channel)]
            for component in ("demux0", "dvr0", "frontend0", "net0"):
                alias = self.dev / "dvb" / f"adapter{slot}" / component
                self.assertEqual(os.readlink(alias), str(self.dev / "dvb" / f"adapter{expected}" / component))
        self.assertEqual((self.state / "stable-map").read_text(), plan)

    def test_incomplete_dual_tuner_removes_aliases_instead_of_shifting_channel(self):
        self.tuner("DUAL", [1, 2])
        self.save_plan([1, 2])
        self.assert_ok(self.shell("stable_refresh_all"))
        self.clear_hardware()
        self.tuner("DUAL", [7])
        result = self.shell("stable_refresh_all")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("expected 2 adapters, found 1", result.stderr)
        self.assertEqual(list((self.dev / "dvb").glob("adapter1???/*")), [])

    def test_duplicate_serial_on_two_devices_is_rejected(self):
        self.tuner("SAME", [1])
        self.tuner("SAME", [2], port="2-1")
        result = self.shell("stable_inventory")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("Duplicate USB serial", result.stderr)

    def test_duplicate_serial_invalidates_existing_aliases(self):
        self.tuner("SAME", [1])
        self.save_plan([1])
        self.assert_ok(self.shell("stable_refresh_all"))
        self.tuner("SAME", [2], port="2-1")
        result = self.shell("stable_refresh_all")
        self.assertNotEqual(result.returncode, 0)
        self.assertFalse((self.dev / "dvb/adapter1000/frontend0").is_symlink())

    def test_unselected_tuner_does_not_enter_a_saved_map(self):
        self.tuner("SELECTED", [1])
        plan = self.save_plan([1])
        self.tuner("OTHER", [0], port="2-1")
        self.assert_ok(self.shell("stable_refresh_all"))
        self.assertEqual(self.plan([1]), plan)
        self.assertEqual(os.readlink(self.dev / "dvb/adapter1000/frontend0"),
                         str(self.dev / "dvb/adapter1/frontend0"))

    def test_changing_selection_preserves_retained_slots(self):
        self.tuner("SOLO", [1], pid="0264")
        self.tuner("DUAL", [2, 3], port="2-1")
        old = self.save_plan([1, 2, 3])
        plan = self.plan([2, 3])
        self.assertEqual(plan, "".join(s + "\n" for s in old.splitlines() if " DUAL " in s))
        self.assertNotIn("SOLO", plan)
        new = self.plan([1])
        self.assertEqual(new, "".join(s + "\n" for s in old.splitlines() if " SOLO " in s))

    def test_pruning_old_selection_only_removes_deselected_aliases(self):
        self.tuner("SOLO", [1], pid="0264")
        self.tuner("DUAL", [2, 3], port="2-1")
        old = self.save_plan([1, 2, 3])
        previous = self.root / "previous-map"
        previous.write_text(old)
        self.assert_ok(self.shell("stable_refresh_all"))
        new = self.plan([2, 3])
        (self.state / "stable-map").write_text(new)
        helpers = HELPERS.replace("/run/lock/dvb-passthrough-stable.lock", str(self.root / "stable.lock"))
        result = subprocess.run(["bash", "-c", helpers + f'\nshopt -s nullglob\nstable_prune_previous "{previous}"'],
                                env=dict(os.environ, SYS_ROOT=str(self.sys), DEV_ROOT=str(self.dev),
                                         STATE=str(self.state)), capture_output=True, text=True)
        self.assert_ok(result)
        self.assertFalse((self.dev / "dvb/adapter1000").exists())
        self.assertTrue((self.dev / "dvb/adapter1001/frontend0").is_symlink())
        self.assertTrue((self.dev / "dvb/adapter1002/frontend0").is_symlink())

    def test_new_selection_allocates_fresh_slots_without_replacing_retained(self):
        self.tuner("SOLO", [1])
        original = self.save_plan([1])
        self.tuner("DUAL", [2, 3], port="2-1")
        plan = self.plan([1, 2, 3])
        self.assertIn(original.strip(), plan)
        self.assertIn("1001 2040 0265 DUAL 0 2", plan)
        self.assertIn("1002 2040 0265 DUAL 1 2", plan)

    def test_stable_plan_refuses_partial_physical_device_selection(self):
        self.tuner("DUAL", [1, 2])
        result = self.shell('PASSTHROUGH_DEVICES=(/dev/dvb/adapter1/frontend0); stable_plan')
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("Select every adapter", result.stderr)

    def test_stable_plan_does_not_shrink_saved_group_during_partial_initialization(self):
        self.tuner("DUAL", [1, 2])
        self.save_plan([1, 2])
        self.clear_hardware()
        self.tuner("DUAL", [7])
        result = self.shell('PASSTHROUGH_DEVICES=(/dev/dvb/adapter7/frontend0); stable_plan')
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("Incomplete or changed adapter group", result.stderr)

    def test_leftover_character_node_without_registration_is_rejected(self):
        self.tuner("SOLO", [1])
        self.save_plan([1])
        self.assert_ok(self.shell("stable_refresh_all"))
        (self.sys / "class/dvb/dvb1.demux0/dev").write_text("212:0\n")
        result = self.shell("stable_refresh_all")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("Stale host node", result.stderr)
        self.assertFalse((self.dev / "dvb/adapter1000/frontend0").is_symlink())

    def test_missing_component_fails_without_leaving_old_aliases(self):
        self.tuner("SOLO", [1])
        self.save_plan([1])
        self.assert_ok(self.shell("stable_refresh_all"))
        shutil.rmtree(self.sys / "class/dvb/dvb1.net0")
        result = self.shell("stable_refresh_all")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("Missing registered node", result.stderr)
        self.assertFalse((self.dev / "dvb/adapter1000/frontend0").is_symlink())

    def test_foreign_regular_file_is_not_overwritten(self):
        self.tuner("SOLO", [1])
        self.save_plan([1])
        alias = self.dev / "dvb/adapter1000/frontend0"
        alias.parent.mkdir()
        alias.write_text("leave alone")
        result = self.shell("stable_refresh_all")
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(alias.read_text(), "leave alone")

    def test_kernel_adapter_collision_is_rejected(self):
        self.tuner("SOLO", [1])
        self.save_plan([1])
        self.tuner("OTHER", [1000], port="2-1")
        result = self.shell("stable_refresh_all")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("conflicts with kernel adapter", result.stderr)
        self.assertTrue((self.dev / "dvb/adapter1000/frontend0").is_symlink())

    def test_plan_is_read_only_and_reserves_other_container_slots(self):
        self.tuner("SOLO", [1])
        other = self.state.parent / "200"
        other.mkdir()
        (other / "stable-map").write_text("1000 2040 0265 OTHER 0 1 frontend0\n")
        plan = self.plan([1])
        self.assertTrue(plan.startswith("1001 "))
        self.assertFalse((self.state / "stable-map").exists())
        self.assertFalse((self.dev / "dvb/adapter1001").exists())

    def test_invalid_and_incomplete_manifests_are_rejected_without_writes(self):
        for plan in (
            "1000 2040 0265 SERIAL 0 2 frontend0\n",
            "1000 2040 0265 SERIAL 0 1 ../../etc/passwd\n",
            "1000 2040 0265 SERIAL 0 1 frontend0\n1000 2040 0265 OTHER 0 1 frontend0\n",
        ):
            with self.subTest(plan=plan):
                (self.state / "stable-map").write_text(plan)
                self.assertNotEqual(self.shell("stable_refresh_all").returncode, 0)
                self.assertFalse((self.dev / "dvb").exists())

    def guest_helper(self):
        emitted = self.shell("stable_guest_helper")
        self.assert_ok(emitted)
        script = emitted.stdout.replace("SYS_ROOT=/sys\n", 'SYS_ROOT="$TEST_SYS"\n')
        script = script.replace("DEV_ROOT=/dev\n", 'DEV_ROOT="$TEST_DEV"\n')
        return subprocess.run(["bash", "-c", script],
                              env=dict(os.environ, TEST_SYS=str(self.sys), TEST_DEV=str(self.dev)),
                              capture_output=True, text=True)

    def test_guest_discovery_path_matches_private_device_number(self):
        self.tuner("SOLO", [7])
        self.save_plan([7])
        self.assert_ok(self.shell("stable_refresh_all"))
        # Model the CT: Proxmox creates a private stable node, but no kernel path.
        for component in ("demux0", "dvr0", "frontend0", "net0"):
            alias = self.dev / "dvb/adapter1000" / component
            alias.unlink()
            alias.symlink_to("/dev/null")
        shutil.rmtree(self.dev / "dvb/adapter7")
        self.assert_ok(self.guest_helper())
        self.assertEqual(os.readlink(self.dev / "dvb/adapter7"), "adapter1000")
        self.assertTrue((self.dev / "dvb/adapter7/frontend0").is_char_device())
        self.assert_ok(self.guest_helper())  # Safe on repeated service starts.

    def test_guest_helper_refuses_a_conflicting_path(self):
        self.tuner("SOLO", [7])
        self.save_plan([7])
        self.assert_ok(self.shell("stable_refresh_all"))
        (self.dev / "dvb/adapter1000/frontend0").unlink()
        (self.dev / "dvb/adapter1000/frontend0").symlink_to("/dev/null")
        shutil.rmtree(self.dev / "dvb/adapter7")
        (self.dev / "dvb/adapter7").symlink_to("adapter9999")
        self.assertNotEqual(self.guest_helper().returncode, 0)
        self.assertEqual(os.readlink(self.dev / "dvb/adapter7"), "adapter9999")

    def test_guest_helper_rejects_unregistered_private_nodes(self):
        self.tuner("SOLO", [7])
        self.save_plan([7])
        self.assert_ok(self.shell("stable_refresh_all"))
        (self.sys / "class/dvb/dvb7.frontend0/dev").write_text("212:55\n")
        result = self.guest_helper()
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("No kernel registration", result.stderr)


if __name__ == "__main__":
    unittest.main()
