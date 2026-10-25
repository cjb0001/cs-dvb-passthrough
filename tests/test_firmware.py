"""Firmware diagnosis and installation using fake sysfs, logs, and downloads."""

import hashlib
import os
from pathlib import Path
import subprocess
import tempfile
import unittest

from test_passthrough import HELPERS, SCRIPT


FILENAME = "dvb-demod-si2168-b40-01.fw"
PAYLOAD = b"verified firmware fixture\x00\xff\n"
MOCKS = r'''
journalctl() {
  printf '%s\n' "$@" > "$JOURNAL_ARGS"
  [[ ${JOURNAL_FAILURE:-0} == 0 ]] || return 9
  cat "$JOURNAL_FILE"
}
curl() {
  local output=''
  printf '%s\n' "$@" >> "$CURL_ARGS"
  while (($#)); do
    if [[ $1 == --output ]]; then output=$2; shift; fi
    shift
  done
  cat "$PAYLOAD_FILE" > "$output"
  [[ ${CURL_FAILURE:-0} == 0 ]] || return 9
  if [[ ${CONCURRENT_FILE:-false} == true ]]; then
    printf 'concurrent package file\n' > "$FIRMWARE_DIR/$TEST_FILENAME"
  fi
}
'''

FIXTURE_CATALOG = r'''
firmware_catalog() {
  [[ $1 == "$TEST_FILENAME" ]] || return 1
  FW_DRIVER=si2168
  FW_SHA256=$EXPECTED_HASH
  FW_URL="https://firmware.example.test/0123456789012345678901234567890123456789/$1"
  FW_LICENSE='Test fixture; no production download'
}
'''


class FirmwareTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="dvb-firmware-test-")
        self.addCleanup(self.temporary.cleanup)
        self.directory = Path(self.temporary.name)
        self.sys = self.directory / "sys"
        self.hardware = self.sys / "devices/pci0000:00/usb1/1-2"
        self.client = self.hardware / "1-2:1.0/i2c-6/6-0064"
        self.client.mkdir(parents=True)
        (self.client / "name").write_text("si2168\n")
        for name, value in (("idVendor", "2040"), ("idProduct", "0265"), ("bcdDevice", "0100")):
            (self.hardware / name).write_text(value + "\n")
        self.i2c = self.sys / "bus/i2c/devices"
        self.i2c.mkdir(parents=True)
        (self.i2c / "6-0064").symlink_to(self.client)
        self.firmware = self.directory / "firmware"
        self.backups = self.directory / "backups"
        self.journal = self.directory / "journal.txt"
        self.journal.write_text(f"si2168 6-0064: Direct firmware load for {FILENAME} failed with error -2\n")
        self.payload = self.directory / "payload"
        self.payload.write_bytes(PAYLOAD)

    def run_shell(self, commands="firmware_workflow", *, input_text=None, real_catalog=False, **environment):
        fixture = "" if real_catalog else FIXTURE_CATALOG
        return subprocess.run(
            ["bash", "-c", HELPERS + "\n" + MOCKS + "\n" + fixture + "\n" + commands],
            env=dict(
                os.environ,
                SYS_ROOT=str(self.sys),
                FIRMWARE_DIR=str(self.firmware),
                FIRMWARE_BACKUP_ROOT=str(self.backups),
                OPERATION_LOCK=str(self.directory / "operation.lock"),
                JOURNAL_FILE=str(self.journal),
                JOURNAL_ARGS=str(self.directory / "journal.args"),
                CURL_ARGS=str(self.directory / "curl.args"),
                PAYLOAD_FILE=str(self.payload),
                TEST_FILENAME=FILENAME,
                EXPECTED_HASH=hashlib.sha256(PAYLOAD).hexdigest(),
                **dict({
                    "DRY_RUN": "false", "INSTALL_FIRMWARE": "false",
                    "ASSUME_YES": "false", "FIRMWARE_SINCE": "15 minutes ago",
                }, **environment),
            ),
            capture_output=True,
            text=True,
            input=input_text,
        )

    def assert_success(self, result):
        self.assertEqual(result.returncode, 0, result.stderr)

    def assert_no_download(self):
        self.assertFalse((self.directory / "curl.args").exists())
        self.assertFalse(self.backups.exists())

    def test_parser_ignores_unrelated_errors_successes_and_paths(self):
        self.journal.write_text(
            f"si2168 6-0064: Direct firmware load for {FILENAME} failed with error -2\n"
            f"si2168 6-0064: firmware file '{FILENAME}' not found\n"
            f"si2168 6-0064: Direct firmware load for {FILENAME} failed with error -20\n"
            f"si2168 6-0064: downloading firmware from file '{FILENAME}'\n"
            "si2168 6-0064: firmware file '../escape.fw' not found\n"
            f"unrelated 6-0064: firmware file '{FILENAME}' not found\n"
        )
        result = self.run_shell('firmware_requests < "$JOURNAL_FILE"')
        self.assert_success(result)
        self.assertEqual(result.stdout.splitlines(), [f"si2168\t6-0064\t{FILENAME}"])

    def test_successful_fallback_supersedes_earlier_failure_for_same_client(self):
        self.journal.write_text(
            f"si2168 6-0064: firmware file '{FILENAME}' not found\n"
            f"si2168 7-0064: firmware file '{FILENAME}' not found\n"
            "si2168 6-0064: firmware version: B 4.0.11\n"
        )
        result = self.run_shell('firmware_requests < "$JOURNAL_FILE"')
        self.assert_success(result)
        self.assertEqual(result.stdout.splitlines(), [f"si2168\t7-0064\t{FILENAME}"])

    def test_later_failure_after_success_is_reported(self):
        self.journal.write_text(
            "si2168 6-0064: firmware version: B 4.0.11\n"
            f"si2168 6-0064: firmware file '{FILENAME}' not found\n"
        )
        result = self.run_shell('firmware_requests < "$JOURNAL_FILE"')
        self.assert_success(result)
        self.assertEqual(result.stdout.splitlines(), [f"si2168\t6-0064\t{FILENAME}"])

    def test_diagnosis_correlates_live_usb_identity_without_writes(self):
        result = self.run_shell(ASSUME_YES="true")
        self.assert_success(result)
        self.assertIn("USB 2040:0265 revision 0100", result.stdout)
        self.assertIn("Diagnosis only", result.stdout)
        self.assert_no_download()
        self.assertFalse(self.firmware.exists())
        self.assertFalse((self.directory / "operation.lock").exists())
        args = (self.directory / "journal.args").read_text()
        self.assertIn("--boot=0", args)
        self.assertIn("--since=15 minutes ago", args)

    def test_pci_subsystem_identity_is_reported(self):
        for name in ("idVendor", "idProduct", "bcdDevice"):
            (self.hardware / name).unlink()
        pci = self.sys / "bus/pci"
        pci.mkdir(parents=True)
        (self.hardware / "subsystem").symlink_to(pci)
        for name, value in (
            ("vendor", "0x14f1"), ("device", "0x8852"), ("revision", "0x04"),
            ("subsystem_vendor", "0x0070"), ("subsystem_device", "0x6b28"),
        ):
            (self.hardware / name).write_text(value + "\n")
        result = self.run_shell()
        self.assert_success(result)
        self.assertIn("subsystem 0x0070:0x6b28 revision 0x04", result.stdout)

    def test_detached_and_wrong_driver_hardware_are_not_installed(self):
        (self.client / "name").write_text("unrelated\n")
        result = self.run_shell(INSTALL_FIRMWARE="true", ASSUME_YES="true")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("manual review", result.stderr)
        self.assert_no_download()
        (self.i2c / "6-0064").unlink()
        result = self.run_shell(INSTALL_FIRMWARE="true", ASSUME_YES="true")
        self.assertNotEqual(result.returncode, 0)
        self.assert_no_download()

    def test_unsupported_filename_requires_manual_review(self):
        self.journal.write_text("si2168 6-0064: firmware file 'unknown.fw' not found\n")
        result = self.run_shell(INSTALL_FIRMWARE="true", ASSUME_YES="true")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("No verified download mapping", result.stderr)
        self.assert_no_download()

    def test_install_dry_run_has_no_download_or_filesystem_writes(self):
        result = self.run_shell(INSTALL_FIRMWARE="true", ASSUME_YES="true", DRY_RUN="true")
        self.assert_success(result)
        self.assertIn("Destination:", result.stdout)
        self.assert_no_download()
        self.assertFalse(self.firmware.exists())
        self.assertFalse((self.directory / "operation.lock").exists())

    def test_download_prompt_defaults_to_decline(self):
        result = self.run_shell(INSTALL_FIRMWARE="true", input_text="\n")
        self.assert_success(result)
        self.assert_no_download()
        self.assertFalse(self.firmware.exists())

    def test_confirmed_install_verifies_and_records_provenance(self):
        result = self.run_shell(INSTALL_FIRMWARE="true", input_text="y\n")
        self.assert_success(result)
        target = self.firmware / FILENAME
        self.assertEqual(target.read_bytes(), PAYLOAD)
        self.assertEqual(target.stat().st_mode & 0o777, 0o644)
        self.assertFalse(list(self.firmware.glob(".dvb-firmware.*")))
        receipts = list(self.backups.glob("firmware.*/receipt.txt"))
        self.assertEqual(len(receipts), 1)
        self.assertIn(hashlib.sha256(PAYLOAD).hexdigest(), receipts[0].read_text())
        self.assertIn("USB 2040:0265", receipts[0].read_text())
        self.assertIn("initialization is not verified", result.stderr)

    def test_checksum_failure_never_publishes_firmware(self):
        self.payload.write_bytes(b"corrupt download")
        result = self.run_shell(INSTALL_FIRMWARE="true", ASSUME_YES="true")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("checksum verification failed", result.stderr)
        self.assertFalse((self.firmware / FILENAME).exists())
        self.assertEqual(list(self.firmware.iterdir()), [])
        self.assertFalse(self.backups.exists())

    def test_failed_download_is_cleaned_up_and_retry_succeeds(self):
        result = self.run_shell(INSTALL_FIRMWARE="true", ASSUME_YES="true", CURL_FAILURE="9")
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(list(self.firmware.iterdir()), [])
        result = self.run_shell(INSTALL_FIRMWARE="true", ASSUME_YES="true")
        self.assert_success(result)
        self.assertEqual((self.firmware / FILENAME).read_bytes(), PAYLOAD)

    def test_existing_compressed_versioned_or_custom_firmware_is_preserved(self):
        release = subprocess.check_output(["uname", "-r"], text=True).strip()
        custom = self.directory / "custom"
        parameter = self.sys / "module/firmware_class/parameters/path"
        parameter.parent.mkdir(parents=True)
        parameter.write_text(str(custom))
        for base, suffix in ((self.firmware, ""), (self.firmware / "updates" / release, ".xz"), (custom, ".zst")):
            with self.subTest(path=base, suffix=suffix):
                base.mkdir(parents=True, exist_ok=True)
                existing = base / (FILENAME + suffix)
                existing.write_bytes(b"distribution firmware")
                result = self.run_shell(INSTALL_FIRMWARE="true", ASSUME_YES="true")
                self.assert_success(result)
                self.assertEqual(existing.read_bytes(), b"distribution firmware")
                self.assert_no_download()
                existing.unlink()

    def test_concurrent_destination_is_not_overwritten(self):
        result = self.run_shell(INSTALL_FIRMWARE="true", ASSUME_YES="true", CONCURRENT_FILE="true")
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual((self.firmware / FILENAME).read_text(), "concurrent package file\n")
        self.assertFalse(list(self.firmware.glob(".dvb-firmware.*")))

    def test_dangling_firmware_symlink_is_preserved(self):
        self.firmware.mkdir()
        target = self.firmware / FILENAME
        target.symlink_to(self.directory / "missing-package-file")
        result = self.run_shell(INSTALL_FIRMWARE="true", ASSUME_YES="true")
        self.assert_success(result)
        self.assertTrue(target.is_symlink())
        self.assert_no_download()

    def test_hardware_identity_is_checked_after_confirmation(self):
        commands = r'''
read() {
  if [[ $* == *'Download and install'* ]]; then
    builtin read "$@" || return
    printf 'ffff\n' > "$TEST_HARDWARE/idProduct"
  else
    builtin read "$@"
  fi
}
firmware_workflow
'''
        result = self.run_shell(
            commands, INSTALL_FIRMWARE="true", input_text="y\n", TEST_HARDWARE=str(self.hardware)
        )
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("Hardware changed", result.stderr)
        self.assert_no_download()

    def test_journal_errors_do_not_trigger_downloads(self):
        result = self.run_shell(INSTALL_FIRMWARE="true", ASSUME_YES="true", JOURNAL_FAILURE="9")
        self.assertNotEqual(result.returncode, 0)
        self.assert_no_download()

    def test_cli_rejects_combining_firmware_with_container_operations(self):
        for args in (("--firmware", "101"), ("--install-firmware", "--remove")):
            with self.subTest(args=args):
                result = subprocess.run(["bash", str(SCRIPT), *args], capture_output=True, text=True)
                self.assertNotEqual(result.returncode, 0)
                self.assertIn("separate host operation", result.stderr)


if __name__ == "__main__":
    unittest.main()
