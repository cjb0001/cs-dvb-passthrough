"""Login banner against fake registrations and harmless character-node proxies."""

import os
from pathlib import Path
import pty
import subprocess
import tempfile
import unittest

from test_passthrough import HELPERS, SCRIPT


LIBRARY = SCRIPT.parent / "community-scripts/core/lib/dvb-banner.func"
HEADER = "# Managed by dvb-passthrough.sh\n"


class BannerTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="dvb-banner-")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.sys = self.root / "sys"
        self.dev = self.root / "dev"
        self.state = self.root / "state"
        self.state.mkdir()
        self.manifest = self.state / "banner-devices.tsv"
        self.profile = self.root / "profile.d/10-dvb-details.sh"
        self.dual = self.hardware("DUAL", "0265", "dualHD", "usb1/1-1")
        self.solo = self.hardware("SOLO", "0264", "soloHD", "usb1/1-2/1-2.3")
        self.node(1001, 1, "/dev/null", self.dual)
        self.node(1002, 2, "/dev/zero", self.dual)
        self.node(1000, 3, "/dev/full", self.solo)
        self.roster = (HEADER + "usb:2040:0265:DUAL\tdualHD\t/dev/dvb/adapter1001/frontend0,/dev/dvb/adapter1002/frontend0\n"
                       "usb:2040:0264:SOLO\tsoloHD\t/dev/dvb/adapter1000/frontend0\n")
        self.manifest.write_text(self.roster)

    def hardware(self, serial, pid, product, path):
        physical = self.sys / "devices" / path
        physical.mkdir(parents=True)
        for name, value in {"idVendor": "2040", "idProduct": pid, "serial": serial, "product": product}.items():
            (physical / name).write_text(value + "\n")
        interface = physical / (physical.name + ":1.0")
        interface.mkdir()
        return interface

    def node(self, stable, actual, proxy, interface):
        frontend = self.dev / "dvb" / f"adapter{stable}/frontend0"
        frontend.parent.mkdir(parents=True)
        frontend.symlink_to(proxy)
        registration = interface / "dvb" / f"dvb{actual}.frontend0"
        registration.mkdir(parents=True)
        (registration / "device").symlink_to(interface)
        number = os.stat(proxy).st_rdev
        char = self.sys / "dev/char" / f"{os.major(number)}:{os.minor(number)}"
        char.parent.mkdir(parents=True, exist_ok=True)
        char.symlink_to(registration)

    def shell(self, command):
        return subprocess.run(["bash", "-c", "set -euo pipefail\n" + LIBRARY.read_text() + "\n" + command],
                              env=dict(os.environ, SYS_ROOT=str(self.sys), DEV_ROOT=str(self.dev),
                                       dvb_banner_state=str(self.state), dvb_banner_profile=str(self.profile),
                                       dvb_banner_backup_root=str(self.root / "backups")),
                              capture_output=True, text=True)

    def assert_ok(self, result):
        self.assertEqual(result.returncode, 0, result.stderr)

    def render(self, *, tty=True, shell="bash", profile=None, interactive=True):
        if profile is None:
            emitted = self.shell("dvb_banner_profile")
            self.assert_ok(emitted)
            profile = emitted.stdout
        profile = profile.replace("sys=/sys\n", f"sys={self.sys}\n")
        profile = profile.replace("dev=/dev\n", f"dev={self.dev}\n")
        profile = profile.replace("manifest=/var/lib/dvb-passthrough/banner-devices.tsv", f"manifest={self.manifest}")
        file = self.root / "render-profile.sh"
        file.write_text(profile)
        command = f'. "{file}"'
        if shell == "bash":
            # Sourcing must not alter the caller's variables or shell options.
            command = ('set -euo pipefail; summary=keep; physical=keep; original_options=$(set +o); '
                       + command + '; test "$summary:$physical" = keep:keep; test "$(set +o)" = "$original_options"')
        if not tty:
            return subprocess.run([shell, "-c", command], capture_output=True, text=True)
        master, slave = pty.openpty()
        try:
            arguments = [shell]
            if shell == "bash":
                arguments += ["--noprofile", "--norc"]
            if interactive:
                arguments += ["-i"]
            process = subprocess.Popen([*arguments, "-c", command], stdin=subprocess.DEVNULL, stdout=slave, stderr=subprocess.PIPE)
            os.close(slave)
            slave = None
            _, err = process.communicate(timeout=10)
            data = b""
            while True:
                try:
                    part = os.read(master, 4096)
                except OSError:
                    break
                if not part:
                    break
                data += part
            return subprocess.CompletedProcess(process.args, process.returncode, data.decode().replace("\r\n", "\n"), err.decode())
        finally:
            os.close(master)
            if slave is not None:
                os.close(slave)

    def test_shared_banner_helpers_match_standalone(self):
        embedded = SCRIPT.read_text().split("# BEGIN shared DVB banner helpers\n", 1)[1].split("# END shared DVB banner helpers\n", 1)[0]
        library = LIBRARY.read_text()
        self.assertEqual(embedded, library[library.index("dvb_banner_profile()"):])

    def test_two_physical_devices_show_three_tuners_without_counting_aliases(self):
        for actual, stable in [(1, 1001), (2, 1002), (3, 1000)]:
            (self.dev / "dvb" / f"adapter{actual}").symlink_to(f"adapter{stable}")
        result = self.render()
        self.assert_ok(result)
        self.assertEqual(result.stdout, "  📺  \033[m\033[33mDVB Devices: \033[1;92mdualHD (2 tuners), soloHD (1 tuner)\033[m\n")

    def test_login_profile_works_in_posix_shell(self):
        result = self.render(shell="dash")
        self.assert_ok(result)
        self.assertIn("dualHD (2 tuners), soloHD (1 tuner)", result.stdout)

    def test_noninteractive_ssh_is_silent(self):
        result = self.render(tty=False)
        self.assert_ok(result)
        self.assertEqual(result.stdout, "")
        self.assertEqual(result.stderr, "")

    def test_noninteractive_shell_with_a_terminal_is_also_silent(self):
        result = self.render(interactive=False)
        self.assert_ok(result)
        self.assertEqual(result.stdout, "")

    def test_missing_configured_device_remains_visible(self):
        (self.sys / "dev/char/1:7").unlink()
        result = self.render()
        self.assert_ok(result)
        self.assertIn("soloHD (missing; 1 configured tuner)", result.stdout)
        self.assertIn("dualHD (2 tuners)", result.stdout)

    def test_partial_dual_tuner_is_reported(self):
        (self.sys / "dev/char/1:5").unlink()
        result = self.render()
        self.assert_ok(result)
        self.assertIn("dualHD (1/2 tuners available)", result.stdout)

    def test_reused_device_number_for_different_hardware_is_not_reported_available(self):
        other = self.hardware("OTHER", "0264", "other device", "usb2/2-1")
        (self.sys / "dev/char/1:7").unlink()
        registration = other / "dvb/dvb7.frontend0"
        registration.mkdir(parents=True)
        (registration / "device").symlink_to(other)
        (self.sys / "dev/char/1:7").symlink_to(registration)
        result = self.render()
        self.assert_ok(result)
        self.assertIn("soloHD (missing; 1 configured tuner)", result.stdout)
        self.assertNotIn("other device", result.stdout)

    def test_unassigned_host_device_does_not_enter_banner(self):
        self.hardware("UNASSIGNED", "9999", "unassigned", "usb3/3-1")
        result = self.render()
        self.assert_ok(result)
        self.assertNotIn("unassigned", result.stdout)

    def test_no_manifest_is_silent_and_does_not_break_login(self):
        self.manifest.unlink()
        result = self.render()
        self.assert_ok(result)
        self.assertEqual(result.stdout, "")

    def test_manifest_text_is_not_executed(self):
        marker = self.root / "should-not-exist"
        self.manifest.write_text(self.roster.replace("dualHD\t", f'$(touch {marker})\t'))
        result = self.render()
        self.assert_ok(result)
        self.assertFalse(marker.exists())
        self.assertIn("$(touch", result.stdout)

    def test_capture_uses_assigned_nodes_and_deduplicates_directory_aliases(self):
        for actual, stable in [(1, 1001), (2, 1002), (3, 1000)]:
            (self.dev / "dvb" / f"adapter{actual}").symlink_to(f"adapter{stable}")
        result = self.shell("dvb_banner_capture")
        self.assert_ok(result)
        rows = result.stdout.splitlines()[1:]
        self.assertEqual(len(rows), 2)
        dual = next(s for s in rows if "\tdualHD\t" in s)
        self.assertIn("/dev/dvb/adapter1001/frontend0,/dev/dvb/adapter1002/frontend0", dual)
        self.assertNotIn("/dev/dvb/adapter1/", result.stdout)

    def test_first_community_install_captures_assigned_physical_devices(self):
        self.manifest.unlink()
        self.assert_ok(self.shell("dvb_banner_install"))
        roster = self.manifest.read_text()
        self.assertIn("\tdualHD\t", roster)
        self.assertIn("\tsoloHD\t", roster)
        result = self.render(profile=self.profile.read_text())
        self.assert_ok(result)
        self.assertIn("dualHD (2 tuners)", result.stdout)
        self.assertIn("soloHD (1 tuner)", result.stdout)
        self.assertEqual(result.stdout.count("DVB Devices:"), 1)

    def test_update_install_preserves_roster_when_hardware_is_missing(self):
        (self.sys / "dev/char/1:7").unlink()
        original = self.manifest.read_bytes()
        self.assert_ok(self.shell("dvb_banner_install"))
        self.assertEqual(self.manifest.read_bytes(), original)
        self.assertTrue(self.profile.exists())
        self.assertIn("missing; 1 configured tuner", self.render(profile=self.profile.read_text()).stdout)

    def test_authoritative_selection_replaces_roster_and_backs_up_previous(self):
        replacement = self.root / "replacement.tsv"
        replacement.write_text(HEADER + self.roster.splitlines()[-1] + "\n")
        self.assert_ok(self.shell(f'dvb_banner_install --manifest-stdin < "{replacement}"'))
        self.assertEqual(self.manifest.read_bytes(), replacement.read_bytes())
        backups = list((self.root / "backups").glob("banner.*/banner-devices.tsv"))
        self.assertEqual(len(backups), 1)
        self.assertEqual(backups[0].read_text(), self.roster)
        result = self.render(profile=self.profile.read_text())
        self.assertIn("soloHD (1 tuner)", result.stdout)
        self.assertNotIn("dualHD", result.stdout)

    def test_unrecognized_profile_is_preserved(self):
        self.profile.parent.mkdir()
        self.profile.write_text("# administrator-owned\n")
        result = self.shell("dvb_banner_install")
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(self.profile.read_text(), "# administrator-owned\n")
        self.assertEqual(self.manifest.read_text(), self.roster)

    def test_invalid_roster_does_not_replace_existing(self):
        invalid = self.root / "invalid.tsv"
        invalid.write_text(HEADER + "usb:2040:0265:DUAL\tdualHD\t/dev/dvb/../../etc/passwd\n")
        result = self.shell(f'dvb_banner_install --manifest-stdin < "{invalid}"')
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(self.manifest.read_text(), self.roster)

    def test_emitted_module_can_be_sourced_and_install_a_banner(self):
        emitted = self.shell("dvb_banner_library")
        self.assert_ok(emitted)
        module = self.root / "module.func"
        module.write_text(emitted.stdout)
        self.assert_ok(self.shell(f'source "{module}"; dvb_banner_install'))

    def test_remove_cleans_only_managed_banner_files_and_preserves_base_banner(self):
        self.assert_ok(self.shell("dvb_banner_install"))
        module = self.root / "module.func"
        module.write_text(HEADER)
        base = self.profile.parent / "00_lxc-details.sh"
        base.write_text("# community base banner\n")
        helpers = HELPERS.replace("/etc/profile.d/10-dvb-details.sh", str(self.profile))
        helpers = helpers.replace("/var/lib/dvb-passthrough/banner-devices.tsv", str(self.manifest))
        helpers = helpers.replace("/usr/local/lib/community-scripts/dvb-banner.func", str(module))
        helpers = helpers.replace("/var/backups/dvb-passthrough", str(self.root / "backups"))
        result = subprocess.run(["bash", "-c", helpers + '\nCT_RUNNING=true; run_in_ct() { "$@"; }; dvb_remove_container_banner'],
                                capture_output=True, text=True)
        self.assert_ok(result)
        self.assertFalse(self.profile.exists())
        self.assertFalse(self.manifest.exists())
        self.assertFalse(module.exists())
        self.assertEqual(base.read_text(), "# community base banner\n")

    def test_standalone_roster_uses_selected_physical_devices_and_stable_paths(self):
        command = f'''
dvb_selected_indices=(0)
dvb_selected_ids=(usb:2040:0265:DUAL)
dvb_tuner_roots=("{self.dual.parent}" "{self.solo.parent}")
dvb_tuner_nodes=('/dev/dvb/adapter1/frontend0 /dev/dvb/adapter2/frontend0' '/dev/dvb/adapter3/frontend0')
STABLE=true
STABLE_SOURCES=(/dev/dvb/adapter1/frontend0 /dev/dvb/adapter2/frontend0 /dev/dvb/adapter3/frontend0)
STABLE_ALIASES=(/dev/dvb/adapter1001/frontend0 /dev/dvb/adapter1002/frontend0 /dev/dvb/adapter1000/frontend0)
dvb_banner_host_manifest
'''
        result = subprocess.run(["bash", "-c", HELPERS + "\n" + command], capture_output=True, text=True)
        self.assert_ok(result)
        self.assertEqual(result.stdout, HEADER + "usb:2040:0265:DUAL\tdualHD\t/dev/dvb/adapter1001/frontend0,/dev/dvb/adapter1002/frontend0\n")


if __name__ == "__main__":
    unittest.main()
