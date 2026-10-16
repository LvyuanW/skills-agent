"""Keyless gateway routing regression; standard-library tests need no Harbor."""
import json
import hashlib
import ast
from pathlib import Path
import tempfile
import unittest

from .gateway import audit_base, gateway_base


class GatewayTests(unittest.TestCase):
    def test_new_gateway_root_and_v1_use_protocol_specific_bases(self):
        for value in ("https://yuzhiapi.xyz", "https://yuzhiapi.xyz/",
                      "https://yuzhiapi.xyz/v1", "https://yuzhiapi.xyz/v1/"):
            with self.subTest(value=value):
                self.assertEqual(gateway_base(value, protocol="responses"), "https://yuzhiapi.xyz/v1")
                self.assertEqual(gateway_base(value, protocol="anthropic"), "https://yuzhiapi.xyz")

    def test_previous_explicitly_authorized_hosts_remain_supported(self):
        for host in ("api.deepseek.com", "rehdasu.cn", "yuzhiapi.xyz"):
            self.assertEqual(gateway_base(f"https://{host}/", protocol="responses"), f"https://{host}/v1")

    def test_credentials_downgrade_other_hosts_and_endpoint_paths_are_rejected(self):
        for value in ("http://yuzhiapi.xyz", "https://evil.example", "https://yuzhiapi.xyz.evil.example",
                      "https://user:secret@yuzhiapi.xyz", "https://yuzhiapi.xyz?key=secret",
                      "https://yuzhiapi.xyz/#fragment", "https://yuzhiapi.xyz:8443",
                      "https://yuzhiapi.xyz/v1/responses", " https://yuzhiapi.xyz", ""):
            with self.subTest(value=value), self.assertRaises(ValueError):
                gateway_base(value, protocol="responses")

    def test_only_explicit_audit_accepts_local_http(self):
        self.assertEqual(audit_base("http://172.17.0.1:8765", protocol="responses"),
                         "http://172.17.0.1:8765/v1")
        self.assertEqual(audit_base("http://127.0.0.1:8765/v1", protocol="anthropic"),
                         "http://127.0.0.1:8765")
        for value in ("http://yuzhiapi.xyz", "http://8.8.8.8:8765", "http://0.0.0.0:8765"):
            with self.subTest(value=value), self.assertRaises(ValueError):
                audit_base(value, protocol="responses")

    def test_public_configs_pin_new_gateway_and_existing_model_effort_versions(self):
        assets = Path(__file__).resolve().parents[1] / "assets"
        for name, version, protocol in (("codex", "0.149.1", "responses"), ("claude", "2.1.300", "anthropic")):
            config = json.loads((assets/name/"agent.json").read_text())
            self.assertEqual(config["kwargs"]["api_base"], gateway_base(protocol=protocol))
            self.assertEqual(config["kwargs"]["version"], version)
            self.assertEqual(config["kwargs"]["reasoning_effort"], "max")
            self.assertEqual(config["model_name"].removesuffix("[1m]"), "deepseek-v4.1-flash")
        codex_assets = assets / "codex"
        catalog_bytes = (codex_assets / "models.json").read_bytes()
        catalog = json.loads(catalog_bytes)
        model, = catalog["models"]
        self.assertEqual(model["slug"], "deepseek-v4.1-flash")
        self.assertEqual(model["context_window"], 1048576)
        self.assertEqual(model["default_reasoning_level"], "max")
        self.assertEqual([level["effort"] for level in model["supported_reasoning_levels"]], ["max"])
        actual_hash = hashlib.sha256(catalog_bytes).hexdigest()
        self.assertEqual(json.loads((codex_assets / "catalog-source.json").read_text())["experiment_catalog_sha256"], actual_hash)
        source = ast.parse((Path(__file__).parent / "codex.py").read_text())
        declared_hash, = [ast.literal_eval(node.value) for node in source.body
                          if isinstance(node, ast.Assign) and any(
                              isinstance(target, ast.Name) and target.id == "_CATALOG_SHA256"
                              for target in node.targets)]
        self.assertEqual(declared_hash, actual_hash)


try:
    from .codex import DeepSeekCodex
    from .claude import DeepSeekClaude
except ModuleNotFoundError as exc:
    if exc.name not in {"harbor", "pydantic"}:
        raise
    DeepSeekCodex = DeepSeekClaude = None


@unittest.skipUnless(DeepSeekCodex is not None, "Pinned Harbor interpreter required for adapter integration")
class AdapterGatewayTests(unittest.TestCase):
    def test_codex_effective_config_and_env_use_new_responses_base(self):
        with tempfile.TemporaryDirectory() as directory:
            agent = DeepSeekCodex(logs_dir=Path(directory))
            config = agent._build_effective_config("https://yuzhiapi.xyz/v1/")
            self.assertEqual(config["model_providers"]["deepseek"]["base_url"], "https://yuzhiapi.xyz/v1")
            self.assertEqual(config["model_providers"]["deepseek"]["wire_api"], "responses")
            self.assertEqual(config["model_reasoning_effort"], "max")
            self.assertEqual(config["model"], "deepseek-v4.1-flash")
            self.assertEqual(agent._extra_env["DEEPSEEK_BASE_URL"], "https://yuzhiapi.xyz/v1")
            with self.assertRaises(ValueError):
                agent._build_effective_config("https://rehdasu.cn/v1")

    def test_claude_new_root_and_old_explicit_override(self):
        with tempfile.TemporaryDirectory() as directory:
            agent = DeepSeekClaude(logs_dir=Path(directory), api_base="https://yuzhiapi.xyz/v1/")
            self.assertEqual(agent._extra_env["ANTHROPIC_BASE_URL"], "https://yuzhiapi.xyz")
            self.assertEqual(agent._extra_env["CLAUDE_CODE_EFFORT_LEVEL"], "max")
            self.assertEqual(agent._resolved_model_name(), "deepseek-v4.1-flash[1m]")
            legacy = DeepSeekClaude(logs_dir=Path(directory)/"legacy", api_base="https://rehdasu.cn")
            self.assertEqual(legacy._extra_env["ANTHROPIC_BASE_URL"], "https://rehdasu.cn")

    def test_conflicting_or_unapproved_env_cannot_redirect_agents(self):
        with tempfile.TemporaryDirectory() as directory:
            for cls, key in ((DeepSeekCodex, "DEEPSEEK_BASE_URL"), (DeepSeekClaude, "ANTHROPIC_BASE_URL")):
                with self.subTest(agent=cls.__name__), self.assertRaises(ValueError):
                    cls(logs_dir=Path(directory), api_base="https://yuzhiapi.xyz", extra_env={key: "https://evil.example"})
                with self.subTest(agent=cls.__name__), self.assertRaises(ValueError):
                    cls(logs_dir=Path(directory), api_base="https://yuzhiapi.xyz", extra_env={key: "https://rehdasu.cn"})
                with self.subTest(agent=cls.__name__), self.assertRaises(ValueError):
                    cls(logs_dir=Path(directory), model_name="deepseek-v4-1-flash-260910")

    def test_local_mock_is_explicit_and_protocol_path_is_not_duplicated(self):
        with tempfile.TemporaryDirectory() as directory:
            codex = DeepSeekCodex(logs_dir=Path(directory), audit_base_url="http://172.17.0.1:8765")
            self.assertEqual(codex._build_effective_config()["model_providers"]["deepseek"]["base_url"],
                             "http://172.17.0.1:8765/v1")
            claude = DeepSeekClaude(logs_dir=Path(directory)/"claude", audit_base_url="http://172.17.0.1:8765/v1")
            self.assertEqual(claude._extra_env["ANTHROPIC_BASE_URL"], "http://172.17.0.1:8765")


if __name__ == "__main__":
    unittest.main()
