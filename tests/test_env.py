import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from vskr.config import load_local_environment


class TestLocalEnvironment(unittest.TestCase):
    def test_env_file_loads_admin_values_without_overriding_exported_variables(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            env_file = Path(temp_dir) / ".env"
            env_file.write_text(
                "VSKR_ADMIN_USER=file-admin\n"
                "VSKR_ADMIN_PASSWORD_HASH=$argon2id$v=19$example\n",
                encoding="utf-8",
            )
            with mock.patch.dict(os.environ, {"VSKR_ADMIN_USER": "process-admin"}, clear=True):
                load_local_environment(temp_dir)
                self.assertEqual(os.environ["VSKR_ADMIN_USER"], "process-admin")
                self.assertEqual(os.environ["VSKR_ADMIN_PASSWORD_HASH"], "$argon2id$v=19$example")


if __name__ == "__main__":
    unittest.main()
