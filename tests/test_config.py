"""Đọc cấu hình: file .env, ghi đè bằng biến VSKR__* và bộ nhớ đệm của load_config."""
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from vskr.config import load_config, load_local_environment


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


class TestEnvOverride(unittest.TestCase):
    def test_override_types_and_names(self):
        env = {
            "VSKR__LLM__BASE_URL": "http://ollama:11434",
            "VSKR__RETRIEVAL__TOP_K": "8",
            "VSKR__ASR__UNLOAD_OLLAMA_FIRST": "false",
            "VSKR__RETRIEVAL__MIN_VECTOR_SCORE": "0.8",
            "VSKR__ASR__DEVICE": "cpu",
            "VSKR__PATHS__DATA_DIR": "/app/data",
            "VSKR__BAD": "x",  # thiếu khóa: bị bỏ qua
            "VSKR__A__B__C": "x",  # quá sâu: bị bỏ qua
        }
        with mock.patch.dict(os.environ, env):
            cfg = load_config()
        self.assertEqual(cfg["llm"]["base_url"], "http://ollama:11434")
        self.assertEqual(cfg["retrieval"]["top_k"], 8)
        self.assertIs(cfg["asr"]["unload_ollama_first"], False)
        self.assertAlmostEqual(cfg["retrieval"]["min_vector_score"], 0.8)
        self.assertEqual(cfg["asr"]["device"], "cpu")
        self.assertEqual(cfg["paths"]["data_dir"], "/app/data")
        self.assertNotIn("bad", cfg)
        self.assertNotIn("a", cfg)

    def test_no_env_keeps_yaml(self):
        clean = {k: v for k, v in os.environ.items() if not k.startswith("VSKR__")}
        with mock.patch.dict(os.environ, clean, clear=True):
            cfg = load_config()
        self.assertEqual(cfg["llm"]["base_url"], "http://localhost:11434")


class TestConfigCache(unittest.TestCase):
    def test_first_call_does_not_hand_out_the_cached_object(self):
        from vskr import config

        config._config_cache.clear()
        first = load_config()
        first["chunk"]["window_s"] = -1
        self.assertNotEqual(load_config()["chunk"]["window_s"], -1)


if __name__ == "__main__":
    unittest.main()
