"""Exercise application updates without tuners using isolated command mocks."""
from pathlib import Path
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]


class DVBUpdateTests(unittest.TestCase):
    def test_ordinary_updates_keep_the_upstream_restart(self):
        for app, service in (("plex", "plexmediaserver"), ("tvheadend", "tvheadend")):
            with self.subTest(app=app), tempfile.TemporaryDirectory() as temporary:
                (Path(temporary) / "var/lib/tvheadend").mkdir(parents=True)
                source = (ROOT / "ct" / (app + ".sh")).read_text()
                start = source.index("function verify_plex_dvb_access()" if app == "plex"
                                     else "function prepare_tvheadend_dvb()")
                source = source[start:].split("\nstart\n", 1)[0]
                for path in ("/dev/dvb", "/usr/local/sbin/dvb-passthrough-aliases",
                             "/usr/local/lib/community-scripts", "/var/lib/dvb-passthrough",
                             "/etc/apt/sources.list.d", "/usr/local/bin/plexupdate", "/opt/plexupdate",
                             "/var/lib/tvheadend"):
                    source = source.replace(path, temporary + path)
                mocks = '''
APP=fixture
STD=''
header_info() { :; }
check_container_storage() { :; }
check_container_resources() { :; }
msg_info() { :; }
msg_ok() { :; }
msg_error() { printf '%s\n' "$*" >&2; }
dpkg() { return 0; }
apt_update_safe() { :; }
setup_deb822_repo() { :; }
apt() { echo "apt $*"; }
systemctl() { echo "systemctl $*"; }
curl() { echo 'unexpected network' >&2; return 99; }
'''
                result = subprocess.run(["bash", "-euc", mocks + source + "\nupdate_script"],
                                        capture_output=True, text=True)
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertEqual(result.stdout.count("systemctl restart " + service), 1)
                self.assertIn("install -y " + service, result.stdout)


if __name__ == "__main__":
    unittest.main()
