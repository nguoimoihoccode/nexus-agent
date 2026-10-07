"""Focused tests for the host-run launcher environment policy."""

import os
import subprocess
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
RUN_SCRIPT = ROOT / "run.sh"


class RunLauncherTests(unittest.TestCase):
    def run_no_proxy_helper(self, initial: str) -> str:
        result = subprocess.run(
            [
                "bash",
                "-c",
                'source "$1"; nexus_append_no_proxy "$2" localhost 127.0.0.1 ::1',
                "_",
                str(RUN_SCRIPT),
                initial,
            ],
            check=True,
            capture_output=True,
            text=True,
            env={"PATH": os.environ["PATH"]},
        )
        return result.stdout.strip()

    def test_no_proxy_helper_preserves_entries_and_adds_loopback(self) -> None:
        result = self.run_no_proxy_helper("internal.example,localhost")

        self.assertEqual(
            result,
            "internal.example,localhost,127.0.0.1,::1",
        )

    def test_no_proxy_helper_builds_loopback_default(self) -> None:
        self.assertEqual(
            self.run_no_proxy_helper(""),
            "localhost,127.0.0.1,::1",
        )

    def test_backend_keeps_proxy_and_disables_reload_by_default(self) -> None:
        script = RUN_SCRIPT.read_text(encoding="utf-8")

        self.assertNotIn("-u HTTP_PROXY", script)
        self.assertNotIn("-u HTTPS_PROXY", script)
        self.assertIn("0) backend_reload_args=(--no-reload)", script)
        self.assertIn('export NO_PROXY="$backend_no_proxy"', script)
        self.assertIn('export no_proxy="$backend_no_proxy"', script)


if __name__ == "__main__":
    unittest.main()
