"""Run with python3 -m unittest discover -s tests -p test_dvb_contract.py."""
import os
from pathlib import Path
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]


class DVBContractTests(unittest.TestCase):
    def shell(self, script, *args):
        return subprocess.run(["bash", "-euc", script, "dvb-test", *args], cwd=ROOT,
                              capture_output=True, text=True)

    def test_public_loader_emits_self_contained_offline_helpers(self):
        with tempfile.TemporaryDirectory() as temporary:
            result = self.shell('''
COMMUNITY_SCRIPTS_CORE_DIR="$PWD"
_cs_source_func() { source "$COMMUNITY_SCRIPTS_CORE_DIR/$1"; }
curl() { echo 'unexpected network request' >&2; return 99; }
source lib/tools.func
declare -F dvb_require_nodes >/dev/null
declare -F dvb_prepare_container >/dev/null
dvb_permissions_library > "$1/permissions"
dvb_banner_library > "$1/banner"
bash -euc 'source "$1"; source "$2"; declare -F setup_dvb; declare -F dvb_banner_install' _ "$1/permissions" "$1/banner"
''', temporary)
            self.assertEqual(result.returncode, 0, result.stderr)

    @unittest.skipIf(os.geteuid() == 0, "Run this boundary check as a non-root operator")
    def test_nonroot_incus_dvb_fails_before_host_or_container_changes(self):
        result = self.shell('''
source lib/dvb-selection.func
source incus/backend.func
msg_error() { printf '%s\n' "$*" >&2; }
incus() { echo 'unexpected container mutation' >&2; return 99; }
var_dvb=yes
incus_create_lxc_container
''')
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("requires root", result.stderr)
        self.assertNotIn("unexpected container mutation", result.stderr)


if __name__ == "__main__":
    unittest.main()
