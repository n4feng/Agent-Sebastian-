import copy
import io
import json
import os
import tempfile
import threading
import time
import unittest
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

from sebastian.agent import Agent
from sebastian.budget import Ledger
from sebastian.cli import Console
from sebastian.common import Rejected, atomic_json
from sebastian.config import DEFAULTS, Settings, validate
from sebastian.files import Workspace
from sebastian.gateway import Gateway
from sebastian.privacy import inspect
from sebastian.rpc import Client, Server
from sebastian.transport import normalize_response

NOW = datetime(2026, 9, 28, 12, tzinfo=timezone.utc).timestamp()


class FakeTransport:
    def __init__(self):
        self.calls = []
        self.failure = False
        self.response = {"id": "response-fixture", "status": "completed",
            "output": [{"type": "message", "content": [{"type": "output_text", "text": "Reviewed."}]}],
            "usage": {"input_tokens": 20, "output_tokens": 10}}
        self.local_responses = []

    def request(self, url, body=None, headers=None, timeout=180):
        self.calls.append((url, copy.deepcopy(body)))
        if "/engines/ps" in url:
            return []
        if "_configure" in url or "/unload" in url:
            return {}
        if "/chat/completions" in url:
            return {"choices": [self.local_responses.pop(0) if self.local_responses else
                    {"message": {"role": "assistant", "content": "Hello locally."}, "finish_reason": "stop"}]}
        if self.failure:
            raise Rejected("Simulated connection loss after dispatch")
        return copy.deepcopy(self.response)


class GatewayTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.directory = Path(self.temp.name)
        keys = self.directory / "keys"
        keys.mkdir()
        (keys / "openai.key").write_text("fixture-credential-1234567890")
        (keys / "anthropic.key").write_text("fixture-credential-0987654321")
        self.transport = FakeTransport()
        self.gateway = Gateway(self.directory / "state", keys, self.transport, now=lambda: NOW)
        for key, value in {"external.enabled": True, "budget.request_usd": 1, "budget.task_usd": 2,
                           "budget.day_usd": 3, "budget.month_usd": 10}.items():
            self.gateway.settings.set(key, value, human=True)
        self.task = self.gateway.ledger.task()

    def quote(self, prompt="Review this public example."):
        return self.gateway.prepare(self.task, prompt)

    def test_no_network_during_quote_and_exact_approved_payload(self):
        quote = self.quote()
        self.assertEqual(self.transport.calls, [])
        result = self.gateway.call("external.approve", {"ticket": quote["ticket"]}, control=True)
        self.assertEqual(self.transport.calls[0][1], quote["body"])
        self.assertEqual(result["text"], "Reviewed.")
        self.assertEqual(self.gateway.ledger.report()["unresolved_usd"], 0)
        with self.assertRaises(Rejected):
            self.gateway.approve_and_send(quote["ticket"])
        self.assertEqual(len(self.transport.calls), 1)

    def test_quote_cannot_be_mutated_from_caller(self):
        quote = self.quote()
        quote["body"]["input"][0]["content"] = "changed"
        # Public RPC serializes this; direct callers must not share the trusted snapshot either.
        self.assertNotEqual(self.gateway.tickets[quote["ticket"]]["body"]["input"][0]["content"], "changed")

    def test_public_interface_cannot_approve_or_raise_budget(self):
        quote = self.quote()
        with self.assertRaises(Rejected):
            self.gateway.call("external.approve", {"ticket": quote["ticket"]})
        with self.assertRaises(Rejected):
            self.gateway.call("settings.set", {"key": "budget.month_usd", "value": 100})
        self.assertEqual(self.transport.calls, [])

    def test_secret_and_known_key_block_before_network(self):
        for prompt in ("sk-proj-" + "a" * 40, "fixture-credential-1234567890", "password=supersecret1234"):
            with self.assertRaises(Rejected):
                self.quote(prompt)
        self.assertEqual(self.transport.calls, [])

    def test_confidential_source_taints_summary(self):
        self.gateway.ledger.mark(self.task, confidential=True)
        quote = self.quote("Anonymous summary with no identifying keywords.")
        self.assertIn("confidential or NDA material", quote["risk_reasons"])
        self.assertEqual(self.transport.calls, [])

    def test_changed_settings_or_sensitivity_invalidates_approval(self):
        quote = self.quote()
        self.gateway.settings.set("local.temperature", 0.4)
        with self.assertRaises(Rejected):
            self.gateway.approve_and_send(quote["ticket"])
        quote = self.quote()
        self.gateway.ledger.mark(self.task, confidential=True)
        with self.assertRaises(Rejected):
            self.gateway.approve_and_send(quote["ticket"])
        self.assertEqual(self.transport.calls, [])

    def test_closed_task_cannot_send(self):
        quote = self.quote()
        self.gateway.ledger.close_task(self.task)
        with self.assertRaises(Rejected):
            self.gateway.approve_and_send(quote["ticket"])

    def test_timeout_remains_reserved_across_restart(self):
        self.transport.failure = True
        quote = self.quote()
        with self.assertRaises(Rejected):
            self.gateway.approve_and_send(quote["ticket"])
        restarted = Ledger(self.directory / "state/ledger.sqlite3", now=lambda: NOW)
        self.assertEqual(restarted.report()["unresolved_usd"], quote["reserved_max_usd"])
        self.assertEqual(len(self.transport.calls), 1)

    def test_missing_usage_retains_reservation(self):
        self.transport.response.pop("usage")
        quote = self.quote()
        with self.assertRaises(Rejected):
            self.gateway.approve_and_send(quote["ticket"])
        self.assertGreater(self.gateway.ledger.report()["unresolved_usd"], 0)

    def test_stale_price_and_zero_budget_fail_closed(self):
        self.gateway.now = lambda: NOW + 8 * 86400
        with self.assertRaises(Rejected):
            self.quote()
        self.gateway.now = lambda: NOW
        self.gateway.settings.set("budget.request_usd", 0, human=True)
        with self.assertRaises(Rejected):
            self.quote()

    def test_approval_expires(self):
        quote = self.quote()
        self.gateway.now = lambda: NOW + 301
        with self.assertRaises(Rejected):
            self.gateway.approve_and_send(quote["ticket"])
        self.assertEqual(self.transport.calls, [])

    def test_usage_bound_violation_freezes_future_calls(self):
        self.transport.response["usage"]["output_tokens"] = 1000000
        with self.assertRaises(Rejected):
            self.gateway.approve_and_send(self.quote()["ticket"])
        self.assertTrue(self.gateway.ledger.report()["frozen"])
        with self.assertRaises(Rejected):
            self.gateway.approve_and_send(self.quote()["ticket"])
        self.assertEqual(len(self.transport.calls), 1)

    def test_local_configuration_and_switch_unloads_first(self):
        self.gateway.local([{"role": "user", "content": "hello"}], [])
        self.gateway.settings.set("local.model", "muse-glimmer-30b", human=True)
        self.gateway.local([{"role": "user", "content": "hello"}], [])
        paths = [url for url, _ in self.transport.calls]
        self.assertEqual(sum("/unload" in url for url in paths), 2)
        configs = [body for url, body in self.transport.calls if "_configure" in url]
        self.assertEqual(configs[0]["context-size"], 4096)
        self.assertIn("q8_0", configs[0]["runtime-flags"])
        self.assertIn("--parallel", configs[0]["runtime-flags"])
        self.assertIn("Muse-Glimmer", configs[1]["model"])

    def test_unix_rpc_and_console_end_to_end(self):
        public_path, control_path = str(self.directory / "public.sock"), str(self.directory / "control.sock")
        try:
            public = Server(public_path, self.gateway, False)
        except PermissionError:
            if os.environ.get("SEBASTIAN_REQUIRE_SOCKETS") == "1":
                raise
            self.skipTest("Execution environment prohibits Unix sockets; run this integration test on Docker/CI.")
        control = Server(control_path, self.gateway, True)
        for server in (public, control):
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            self.addCleanup(server.server_close)
            self.addCleanup(server.shutdown)
        lines, output = iter(["chat", "Hello", "exit", "setting set ui.language French", "exit"]), []
        console = Console(Client(public_path, control_path), ask=lambda _: next(lines), emit=output.append)
        console.run()
        self.assertIn("Hello locally.", output)
        self.assertEqual(self.gateway.settings.read()["ui.language"], "French")

    def test_console_agent_tools_with_in_process_gateway(self):
        class DirectClient:
            def call(_, method, params=None, control=False):
                return self.gateway.call(method, params or {}, control=control)
        (self.directory / "project").mkdir()
        (self.directory / "playground").mkdir()
        (self.directory / "playground/input.txt").write_text("Local fixture document.")
        self.transport.local_responses = [
            {"message": {"role":"assistant", "content":"", "tool_calls":[{"id":"read1", "type":"function", "function":{"name":"read_file", "arguments":'{"path":"playground/input.txt"}'}}]}, "finish_reason":"tool_calls"},
            {"message": {"role":"assistant", "content":"Read the local fixture."}, "finish_reason":"stop"}]
        lines, output = iter(["chat", "Read input.txt", "exit", "setting set ui.language French", "exit"]), []
        console = Console(DirectClient(), Workspace(self.directory / "project", self.directory / "playground"), ask=lambda _: next(lines), emit=output.append)
        console.run()
        self.assertIn("Read the local fixture.", output)
        self.assertEqual(self.gateway.settings.read()["ui.language"], "French")


class LedgerTests(unittest.TestCase):
    def test_atomic_concurrency_and_day_rollover(self):
        with tempfile.TemporaryDirectory() as directory:
            clock = [NOW]
            ledger = Ledger(Path(directory) / "ledger.db", now=lambda: clock[0])
            task = ledger.task()
            settings = {**DEFAULTS, "budget.request_usd": 1, "budget.task_usd": 1, "budget.day_usd": 1, "budget.month_usd": 1}
            def reserve(_):
                try:
                    ledger.reserve(task, "fixture", 600000, settings)
                    return True
                except Rejected:
                    return False
            with ThreadPoolExecutor(max_workers=2) as pool:
                results = list(pool.map(reserve, range(2)))
            self.assertEqual(sum(results), 1)
            clock[0] += 40 * 86400
            with self.assertRaises(Rejected):
                ledger.reserve(ledger.task(), "fixture", 600000, settings)


class ConfigurationTests(unittest.TestCase):
    def test_invalid_changes_do_not_damage_previous_and_rollback(self):
        with tempfile.TemporaryDirectory() as directory:
            settings = Settings(directory)
            with self.assertRaises(Rejected):
                settings.set("local.context_tokens", 999999, human=True)
            self.assertEqual(settings.read(), DEFAULTS)
            settings.set("local.temperature", 0.8)
            self.assertEqual(settings.rollback()["local.temperature"], 0.3)
            for value in (float("nan"), float("inf"), -1, True):
                with self.assertRaises(Rejected):
                    settings.set("budget.month_usd", value, human=True)


class PrivacyTests(unittest.TestCase):
    def test_encoded_key_private_key_and_false_negatives_handled_by_review(self):
        import base64
        key = "sk-proj-" + "a" * 40
        self.assertEqual(inspect(base64.b64encode(key.encode()).decode()).decision, "block")
        self.assertEqual(inspect("-----BEGIN RSA PRIVATE KEY-----").decision, "block")
        self.assertEqual(inspect("Ordinary text with no risk keyword.").decision, "review")


class WorkspaceTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        (self.root / "project").mkdir()
        (self.root / "playground").mkdir()
        self.workspace = Workspace(self.root / "project", self.root / "playground")

    def test_paths_symlinks_and_outside_write(self):
        (self.root / "outside.txt").write_text("private")
        (self.root / "playground/link.txt").symlink_to(self.root / "outside.txt")
        (self.root / "playground/linkdir").symlink_to(self.root)
        for path in ("/etc/passwd", "playground/../outside.txt", "project/.git/config", "playground/linkdir/outside.txt", "playground/link.txt"):
            with self.assertRaises((Rejected, OSError)):
                self.workspace.read(path)
        with self.assertRaises(Rejected):
            self.workspace.write("project/code.py", "changed")
        with self.assertRaises(Rejected):
            self.workspace.write("playground/link.txt", "changed", overwrite=True)
        self.assertEqual((self.root / "outside.txt").read_text(), "private")

    def test_file_write_requires_overwrite_and_handles_unicode(self):
        self.workspace.write("playground/notes.md", "你好 world")
        with self.assertRaises(Rejected):
            self.workspace.write("playground/notes.md", "new")
        self.workspace.write("playground/notes.md", "new", overwrite=True)
        self.assertEqual(self.workspace.read("playground/notes.md")["text"], "new")

    def test_unsupported_binary_and_large_files(self):
        (self.root / "playground/image.png").write_bytes(b"abc")
        (self.root / "playground/large.txt").write_bytes(b"a" * 300000)
        (self.root / "playground/bad.txt").write_bytes(b"\x00\xff")
        for name in ("image.png", "large.txt", "bad.txt"):
            with self.assertRaises(Rejected):
                self.workspace.read("playground/" + name)


class ProviderTests(unittest.TestCase):
    def test_anthropic_cache_usage_and_text_normalization(self):
        result = normalize_response("anthropic", {"content": [{"type":"text", "text":"hello"}],
            "usage": {"input_tokens":10, "output_tokens":20, "cache_read_input_tokens":5, "cache_creation_input_tokens":6}})
        self.assertEqual(result["input_tokens"], 21)
        self.assertEqual(result["text"], "hello")


if __name__ == "__main__":
    unittest.main()
