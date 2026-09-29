"""The sole network/credential owner. Run in a separate container."""
import copy
import json
import math
import os
import threading
import time
import uuid
from datetime import date, datetime, timezone
from pathlib import Path

from .budget import Ledger, cost, micros
from .common import Rejected, atomic_json, canonical, integer, text
from .config import Settings, catalog
from .privacy import inspect
from .transport import DMR, Transport, external_request, normalize_response


class Gateway:
    def __init__(self, directory, keys, transport=None, now=time.time):
        self.directory, self.keys, self.now = Path(directory), Path(keys), now
        self.settings = Settings(directory)
        self.ledger = Ledger(self.directory / "ledger.sqlite3", now=now)
        self.transport = transport or Transport()
        self.lock = threading.RLock()
        self.local_lock = threading.Lock()
        self.tickets = {}
        self.active_signature = None
        self.prices_path = self.directory / "prices.json"
        if not self.prices_path.exists():
            models = catalog()
            atomic_json(self.prices_path, {k: {**v, "verified_on": models["verified_on"]}
                                          for k, v in models["external"].items()})

    def prices(self):
        return json.loads(self.prices_path.read_text())

    def credential(self, provider):
        path = self.keys / (provider + ".key")
        if not path.exists():
            raise Rejected(f"No {provider} key is installed. Use the Docker keys command.")
        value = path.read_text().strip()
        if len(value) < 16 or any(c.isspace() for c in value):
            raise Rejected("Credential file is empty or malformed.")
        return value

    def known_secrets(self):
        result = []
        for provider in ("openai", "anthropic"):
            try:
                result.append(self.credential(provider))
            except Rejected:
                pass
        return result

    def price(self, model):
        if model not in catalog()["external"]:
            raise Rejected("External model is not registered.")
        row = self.prices().get(model)
        if not row:
            raise Rejected("Pricing is unavailable; verify it before sending.")
        try:
            age = (datetime.fromtimestamp(self.now(), timezone.utc).date() - date.fromisoformat(row["verified_on"])).days
            if not 0 <= age <= 7:
                raise Rejected("Pricing is stale. Review the official source and use prices verify.")
            for key in ("input", "output", "cache_write"):
                if type(row[key]) not in (int, float) or not math.isfinite(row[key]) or row[key] <= 0:
                    raise ValueError()
        except (KeyError, ValueError, TypeError):
            raise Rejected("Pricing is invalid; review it before sending.") from None
        return row

    def prepare(self, task, prompt, model=None):
        with self.lock:
            info = self.ledger.task_info(task)
            prompt = text(prompt)
            settings = self.settings.read()
            if not settings["external.enabled"]:
                raise Rejected("External calls are disabled. Enable them with a human setting command.")
            model = model or settings["external.model"]
            price = self.price(model)
            self.credential(price["provider"])
            endpoint, body = external_request(model, price["provider"], prompt, settings["external.max_output_tokens"])
            finding = inspect(canonical(body), known_secrets=self.known_secrets(),
                              restricted=bool(info["restricted"]), confidential=bool(info["confidential"]))
            if finding.decision == "block":
                self.ledger.event("export_blocked", {"task": task, "reasons": finding.reasons})
                raise Rejected("Outbound content blocked: " + ", ".join(finding.reasons))
            # Text-only, one message, no tools. Byte count plus fixed framing margin;
            # this deliberately over-reserves ordinary text. Unexpected usage freezes billing.
            incoming_bound = len(canonical(body).encode()) + 4096
            maximum = cost(incoming_bound, settings["external.max_output_tokens"], price)
            if maximum > micros(settings["budget.request_usd"]):
                raise Rejected("Estimated maximum exceeds the request budget; reduce the task or adjust settings.")
            identifier = uuid.uuid4().hex
            self.tickets = {k: v for k, v in self.tickets.items() if v["expires"] > self.now()}
            if len(self.tickets) >= 64:
                raise Rejected("Too many pending approvals; cancel old ones or wait for expiry.")
            self.tickets[identifier] = {"id": identifier, "task": task, "model": model,
                "endpoint": endpoint, "body": body, "price": copy.deepcopy(price),
                "settings": settings, "max_micros": maximum, "input_bound": incoming_bound,
                "expires": self.now() + 300, "reasons": finding.reasons}
            return {"ticket": identifier, "destination": endpoint, "model": model,
                    "body": copy.deepcopy(body), "risk_reasons": finding.reasons,
                    "reserved_max_usd": maximum / 1e6, "expires_in_seconds": 300}

    def approve_and_send(self, identifier):
        # Invoked only through the control socket by direct terminal approval.
        with self.lock:
            ticket = self.tickets.pop(identifier, None)
            if not ticket or ticket["expires"] < self.now():
                raise Rejected("Approval expired or was consumed; prepare a new request.")
            settings = self.settings.read()
            if settings != ticket["settings"] or self.price(ticket["model"]) != ticket["price"]:
                raise Rejected("Configuration or price changed; review a new quote.")
            info = self.ledger.task_info(ticket["task"])
            finding = inspect(canonical(ticket["body"]), known_secrets=self.known_secrets(),
                              restricted=bool(info["restricted"]), confidential=bool(info["confidential"]))
            if finding.decision == "block":
                raise Rejected("Content is now blocked; no request was sent.")
            if tuple(finding.reasons) != tuple(ticket["reasons"]):
                raise Rejected("Sensitivity changed; review a new quote.")
            provider = ticket["price"]["provider"]
            key = self.credential(provider)
            self.ledger.reserve(ticket["task"], ticket["model"], ticket["max_micros"], settings, identifier)
            self.ledger.event("export_approved", {"task": ticket["task"], "request": identifier,
                "model": ticket["model"], "maximum_usd": ticket["max_micros"] / 1e6,
                "reasons": finding.reasons})
        headers = {"Authorization": "Bearer " + key} if provider == "openai" else {
            "x-api-key": key, "anthropic-version": "2023-06-01"}
        try:
            response = self.transport.request(ticket["endpoint"], ticket["body"], headers)
            result = normalize_response(provider, response)
            accounted = cost(result["input_tokens"], result["output_tokens"], ticket["price"])
            self.ledger.settle(identifier, accounted, result["usage"], result["provider_id"])
            self.ledger.event("export_completed", {"request": identifier, "accounted_usd": accounted / 1e6})
            if inspect(result["text"], known_secrets=self.known_secrets()).decision == "block":
                result["text"] = "Provider output was withheld because it contains a possible credential. The provider call was still accounted."
            return {"text": result["text"], "status": result["status"], "request": identifier,
                    "accounted_usd": accounted / 1e6,
                    "accounting_note": "Conservative charge; input/cache discounts are not assumed."}
        except Exception:
            self.ledger.event("export_unresolved_or_frozen", {"request": identifier})
            raise Rejected(f"Request {identifier} did not complete normally. Check budget; do not blindly retry. Any unresolved reservation is retained.") from None

    def local(self, messages, tools):
        with self.local_lock:
            settings = self.settings.read()
            model = catalog()["local"][settings["local.model"]]["model"]
            if not isinstance(messages, list) or not 1 <= len(messages) <= 100:
                raise Rejected("Invalid local conversation.")
            if not isinstance(tools, list) or len(tools) > 16:
                raise Rejected("Invalid local tool definitions.")
            if len(canonical(messages).encode()) + len(canonical(tools).encode()) > 80_000:
                raise Rejected("Conversation is too large. Use clear or smaller file excerpts.")
            for item in messages:
                if not isinstance(item, dict) or item.get("role") not in ("system", "user", "assistant", "tool"):
                    raise Rejected("Invalid local message.")
                if item.get("content") is not None and not isinstance(item["content"], str):
                    raise Rejected("Only text content is supported.")
            signature = (model, settings["local.context_tokens"], settings["local.kv_cache"], settings["local.idle_seconds"])
            if signature != self.active_signature:
                self.transport.request(DMR + "/engines/unload", {"all": False, "backend": "llama.cpp",
                    "models": [entry["model"] for entry in catalog()["local"].values()]})
                running = self.transport.request(DMR + "/engines/ps")
                if running:
                    raise Rejected("Another Model Runner workload is still loaded. Stop it before switching models.")
                flags = ["--parallel", "1", "--batch-size", "128", "--ubatch-size", "64",
                         "--flash-attn", "on", "--cache-type-k", settings["local.kv_cache"],
                         "--cache-type-v", settings["local.kv_cache"], "--cache-ram", "0",
                         "--no-context-shift", "--jinja"]
                self.transport.request(DMR + "/engines/llama.cpp/_configure", {
                    "model": model, "context-size": settings["local.context_tokens"],
                    "runtime-flags": flags, "keep_alive": f"{settings['local.idle_seconds']}s"})
                self.active_signature = signature
            body = {"model": model, "messages": messages, "tools": tools,
                    "tool_choice": "auto", "parallel_tool_calls": False, "stream": False,
                    "max_tokens": settings["local.max_output_tokens"], "temperature": settings["local.temperature"]}
            try:
                response = self.transport.request(DMR + "/engines/v1/chat/completions", body, timeout=180)
                choice = response["choices"][0]
                return {"message": choice["message"], "finish_reason": choice.get("finish_reason")}
            except (KeyError, IndexError, TypeError):
                self.active_signature = None
                raise Rejected("Local server returned an incompatible response.") from None
            except Rejected:
                self.active_signature = None
                raise

    def call(self, method, params, *, control=False):
        if not isinstance(params, dict):
            raise Rejected("Invalid request parameters.")
        if method == "health":
            return {"ok": True, "version": "0.1.0"}
        if method == "settings.get":
            return self.settings.read()
        if method == "settings.set":
            with self.lock:
                result = self.settings.set(params["key"], params["value"], human=control)
                self.ledger.event("setting_changed", result)
                return result
        if method == "models":
            return catalog()
        if method == "task.new":
            return {"task": self.ledger.task()}
        if method == "task.mark":
            for key in ("restricted", "confidential"):
                if type(params.get(key, False)) is not bool:
                    raise Rejected("Sensitivity markers must be booleans.")
            self.ledger.mark(params["task"], params.get("restricted", False), params.get("confidential", False))
            return {"ok": True}
        if method == "task.close":
            self.ledger.close_task(params["task"])
            return {"ok": True}
        if method == "local.chat":
            return self.local(params["messages"], params["tools"])
        if method == "external.prepare":
            return self.prepare(params["task"], params["prompt"], params.get("model"))
        if method == "budget":
            return self.ledger.report()
        if method == "prices":
            return self.prices()
        if not control:
            raise Rejected("Operation requires the human control interface.")
        if method == "external.approve":
            return self.approve_and_send(params["ticket"])
        if method == "external.cancel":
            with self.lock:
                self.tickets.pop(params["ticket"], None)
            return {"cancelled": True}
        if method == "settings.rollback":
            with self.lock:
                result = self.settings.rollback()
                self.ledger.event("settings_rollback", {})
                return result
        if method == "prices.verify":
            with self.lock:
                model = params["model"]
                rows = self.prices()
                if model not in rows:
                    raise Rejected("Unknown model.")
                for key in ("input", "output", "cache_write"):
                    value = params[key]
                    if type(value) not in (int, float) or not math.isfinite(value) or not 0 < value <= 1000:
                        raise Rejected("Rates must be finite positive USD per million tokens.")
                    rows[model][key] = value
                rows[model]["verified_on"] = datetime.fromtimestamp(self.now(), timezone.utc).date().isoformat()
                atomic_json(self.prices_path, rows)
                self.ledger.event("price_verified_by_operator", {"model": model})
                return rows[model]
        if method == "budget.reconcile":
            value = params["usd"]
            if type(value) not in (int, float) or not math.isfinite(value) or value < 0:
                raise Rejected("Amount must be finite and nonnegative.")
            evidence = text(params["evidence"], 200)
            if len(evidence.strip()) < 8 or inspect(evidence).decision == "block":
                raise Rejected("Provide a non-sensitive provider receipt/reference, at least 8 characters.")
            self.ledger.settle(params["request"], micros(value), {"operator_evidence": evidence})
            self.ledger.event("manual_reconciliation", {"request": params["request"], "usd": value})
            return {"ok": True}
        if method == "audit":
            return self.ledger.audit()
        if method == "doctor":
            result = {"keys_installed": {p: (self.keys / (p + ".key")).exists() for p in ("openai", "anthropic")}}
            try:
                result["local_models"] = self.transport.request(DMR + "/engines/v1/models")
                result["running"] = self.transport.request(DMR + "/engines/ps")
            except Rejected as exc:
                result["local_error"] = str(exc)
            return result
        if method == "local.unload":
            with self.local_lock:
                self.active_signature = None
                return self.transport.request(DMR + "/engines/unload", {"backend": "llama.cpp", "all": False,
                    "models": [entry["model"] for entry in catalog()["local"].values()]})
        raise Rejected("Unknown command.")
