"""TVHeadend scans native stable paths; Plex still needs compatibility links."""

import os
from pathlib import Path
import subprocess
import unittest

import test_tvheadend_update
from test_passthrough import HELPERS


class ApplicationStableDiscoveryTests(unittest.TestCase):
    def setUp(self):
        self.fixture = test_tvheadend_update.TVHeadendUpdateTests()
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)
        self.fixture.install_module()
        self.root = self.fixture.root
        self.sys = self.root / "sys"
        self.dev = self.root / "boot-dev"
        for actual, slot, number, source in ((1, 1001, "1:3", "/dev/null"), (2, 1002, "1:5", "/dev/zero")):
            registration = self.sys / "class/dvb" / f"dvb{actual}.frontend0"
            registration.mkdir(parents=True)
            (registration / "dev").write_text(number + "\n")
            private = self.dev / "dvb" / f"adapter{slot}"
            private.mkdir(parents=True)
            (private / "frontend0").symlink_to(source)

    def boot(self, application):
        emitted = subprocess.run(["bash", "-c", HELPERS + f"\nAPPLICATION={application}; stable_guest_helper"], capture_output=True, text=True)
        self.assertEqual(emitted.returncode, 0, emitted.stderr)
        helper = emitted.stdout.replace("SYS_ROOT=/sys", f'SYS_ROOT="{self.sys}"').replace("DEV_ROOT=/dev", f'DEV_ROOT="{self.dev}"')
        script = self.root / "boot-helper.sh"
        script.write_text(helper)
        return subprocess.run(["bash", str(script)], capture_output=True, text=True)

    def assert_ok(self, result):
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_tvheadend_keeps_only_private_stable_directories(self):
        self.assert_ok(self.boot("tvheadend"))
        self.assertFalse((self.dev / "dvb/adapter1").exists())
        self.assertFalse((self.dev / "dvb/adapter2").exists())
        self.assertTrue((self.dev / "dvb/adapter1001/frontend0").is_char_device())
        self.assertTrue((self.dev / "dvb/adapter1002/frontend0").is_char_device())

    def test_plex_keeps_original_kernel_compatibility_links(self):
        self.assert_ok(self.boot("plex"))
        self.assertEqual(os.readlink(self.dev / "dvb/adapter1"), "adapter1001")
        self.assertEqual(os.readlink(self.dev / "dvb/adapter2"), "adapter1002")

    def test_tvheadend_removes_only_recognized_old_compatibility_links(self):
        (self.dev / "dvb/adapter1").symlink_to("adapter1001")
        (self.dev / "dvb/adapter2").symlink_to("adapter1002")
        self.assert_ok(self.boot("tvheadend"))
        self.assertFalse((self.dev / "dvb/adapter1").is_symlink())
        self.assertFalse((self.dev / "dvb/adapter2").is_symlink())

    def test_unrecognized_symlink_is_preserved(self):
        link = self.dev / "dvb/adapter1"
        link.symlink_to("adapter9999")
        self.assertNotEqual(self.boot("tvheadend").returncode, 0)
        self.assertEqual(os.readlink(link), "adapter9999")

    def test_real_unmanaged_kernel_directory_is_preserved(self):
        directory = self.dev / "dvb/adapter1"
        directory.mkdir()
        (directory / "frontend0").symlink_to("/dev/null")
        self.assertNotEqual(self.boot("tvheadend").returncode, 0)
        self.assertTrue((directory / "frontend0").is_char_device())

    def verify(self):
        return self.fixture.shell(f'''source "{self.fixture.module}"
stat() {{ if [[ $2 == %g ]]; then printf '44\\n'; else command stat "$@"; fi; }}
dvb_verify_service hts tvheadend.service''')

    def test_runtime_verification_reports_duplicate_frontend_paths(self):
        self.fixture.device()
        duplicate = self.fixture.devices / "adapter1001/frontend0"
        duplicate.parent.mkdir()
        duplicate.symlink_to("/dev/null")
        result = self.verify()
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("Duplicate TVHeadend discovery paths", result.stderr)

    def test_runtime_verification_accepts_distinct_frontends(self):
        self.fixture.device()
        second = self.fixture.devices / "adapter1002/frontend0"
        second.parent.mkdir()
        second.symlink_to("/dev/zero")
        self.assert_ok(self.verify())


if __name__ == "__main__":
    unittest.main()
