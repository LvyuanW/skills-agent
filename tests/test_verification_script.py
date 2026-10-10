import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


SCRIPT = (Path(__file__).resolve().parents[1] / "skills_agent" / "builtin_skills"
          / "code-repair" / "scripts" / "run_check.py")


class VerificationScriptTests(unittest.TestCase):
    def run_check(self, log, source, *arguments):
        return subprocess.run(
            [sys.executable, str(SCRIPT), str(log), sys.executable, "-c", source, *arguments],
            capture_output=True, text=True,
        )

    def test_success_retains_full_stdout_and_stderr_while_displaying_only_tail(self):
        with tempfile.TemporaryDirectory() as directory:
            log = Path(directory) / "full output.log"
            first = "first line outside the displayed tail\n"
            middle = "".join(f"evidence {index}: {'x' * 200}\n" for index in range(80))
            last = "最后的 stderr\n"
            result = self.run_check(log,
                "import os, sys; os.write(1, sys.argv[1].encode()); os.write(2, sys.argv[2].encode())",
                first + middle, last)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(log.read_bytes(), (first + middle + last).encode())
            self.assertNotIn(first.strip(), result.stdout)
            self.assertIn(last.strip(), result.stdout)
            self.assertIn(str(log), result.stdout)

    def test_failure_status_survives_success_looking_last_line(self):
        with tempfile.TemporaryDirectory() as directory:
            log = Path(directory) / "failed.log"
            result = self.run_check(log,
                "import os, sys; os.write(2, b'assertion failed\\n'); "
                "os.write(1, b'SUCCESS\\n'); sys.exit(7)")
            self.assertEqual(result.returncode, 7, result.stderr)
            self.assertEqual(log.read_bytes(), b"assertion failed\nSUCCESS\n")
            self.assertIn("SUCCESS", result.stdout)


if __name__ == "__main__":
    unittest.main()
