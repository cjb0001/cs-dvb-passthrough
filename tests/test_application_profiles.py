"""Shared Plex and TVHeadend application profiles and defaults."""

import subprocess
import unittest

from test_passthrough import SCRIPT
import test_stable


class ApplicationProfileTests(unittest.TestCase):
    def setUp(self):
        self.fixture = test_stable.StableTests("test_duplicate_serial_on_two_devices_is_rejected")
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)

    def profile(self, commands):
        return self.fixture.shell("APPLICATION=''; SERVICE_USER=''; " + commands)

    def test_old_invocation_still_defaults_to_plex(self):
        result = self.profile('dvb_resolve_profile; printf "%s:%s" "$APPLICATION" "$SERVICE_USER"')
        self.assertEqual(result.stdout, "plex:plex", result.stderr)

    def test_tvheadend_defaults_to_hts_and_custom_user_wins(self):
        for override, expected in (("", "hts"), ("SERVICE_USER=recorder;", "recorder")):
            result = self.profile(f'APPLICATION=tvheadend; {override} dvb_resolve_profile; printf "%s" "$SERVICE_USER"')
            self.assertEqual(result.stdout, expected, result.stderr)

    def test_saved_profile_is_reused_but_an_explicit_app_changes_defaults(self):
        (self.fixture.state / "profile").write_text("tvheadend\trecorder\n")
        result = self.profile('dvb_resolve_profile; printf "%s:%s" "$APPLICATION" "$SERVICE_USER"')
        self.assertEqual(result.stdout, "tvheadend:recorder", result.stderr)
        result = self.profile('APPLICATION=plex; dvb_resolve_profile; printf "%s:%s" "$APPLICATION" "$SERVICE_USER"')
        self.assertEqual(result.stdout, "plex:plex", result.stderr)

    def test_profile_is_data_and_rejects_extra_lines(self):
        for value in ("tvheadend\t$(touch /bad)\n", "tvheadend\thts\nplex\tplex\n", "unknown\thts\n"):
            (self.fixture.state / "profile").write_text(value)
            self.assertNotEqual(self.profile("dvb_resolve_profile").returncode, 0)

    def test_cli_rejects_invalid_conflicting_and_unrelated_app_options(self):
        for args in (("--app", "invalid"), ("--app",), ("--app", "plex", "--app", "tvheadend"),
                     ("--firmware", "--app", "tvheadend")):
            result = subprocess.run(["bash", str(SCRIPT), *args], capture_output=True, text=True)
            self.assertNotEqual(result.returncode, 0)

    def test_shared_permissions_body_matches_standalone(self):
        library = (SCRIPT.parent / "community-scripts/core/lib/dvb.func").read_text()
        embedded = SCRIPT.read_text().split("# BEGIN shared container permissions\n", 1)[1].split("# END shared container permissions\n", 1)[0]
        self.assertEqual(embedded, library[library.index("setup_dvb()"):])

if __name__ == "__main__":
    unittest.main()
