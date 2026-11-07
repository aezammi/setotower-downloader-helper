import importlib.util
import socket
import unittest
from pathlib import Path
from unittest.mock import patch


HELPER = Path(__file__).parents[1] / "linux" / "setotower-helper.py"
WINDOWS_HELPER = Path(__file__).parents[1] / "windows" / "Helper.cs"
SPEC = importlib.util.spec_from_file_location("setotower_helper", HELPER)
helper = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
SPEC.loader.exec_module(helper)


class HelperContractTests(unittest.TestCase):
    def test_windows_progress_contract_is_exposed(self):
        source = WINDOWS_HELPER.read_text(encoding="utf-8")
        self.assertIn("current=j.Current,total=j.Total,percent=j.Percent", source)
        self.assertIn('SETOTOWER_PROGRESS:\\s*(\\d+)\\s*/\\s*(\\d+)', source)
        self.assertIn("job.Current=job.Total=job.Percent=100", source)

    def test_public_dns_is_allowed(self):
        public = [(None, None, None, None, ("93.184.216.34", 0))]
        with patch.object(helper.socket, "getaddrinfo", return_value=public):
            self.assertTrue(helper.valid_url("https://example.test/video.mp4"))

    def test_private_dns_is_rejected(self):
        private = [(None, None, None, None, ("10.0.0.2", 0))]
        with patch.object(helper.socket, "getaddrinfo", return_value=private):
            self.assertFalse(helper.valid_url("https://internal.example/video"))

    def test_unresolved_dns_fails_closed(self):
        with patch.object(helper.socket, "getaddrinfo", side_effect=socket.gaierror):
            self.assertFalse(helper.valid_url("https://unresolvable.example/video"))


if __name__ == "__main__":
    unittest.main()
