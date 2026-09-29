import copy
import json
import math
import threading
from importlib.resources import files
from pathlib import Path

from .common import Rejected, atomic_json, integer


def catalog():
    return json.loads(files("sebastian").joinpath("data/models.json").read_text())


DEFAULTS = {
    "local.model": "qwen3.8-27b",
    "local.context_tokens": 4096,
    "local.max_output_tokens": 768,
    "local.temperature": 0.3,
    "local.kv_cache": "q8_0",
    "local.idle_seconds": 60,
    "agent.max_steps": 12,
    "agent.max_seconds": 600,
    "tools.max_read_bytes": 2048,
    "tools.confirm_writes": True,
    "ui.language": "English",
    "external.enabled": False,
    "external.model": "gpt-5",
    "external.max_output_tokens": 2048,
    "budget.request_usd": 0.0,
    "budget.task_usd": 0.0,
    "budget.day_usd": 0.0,
    "budget.month_usd": 0.0,
}
MODEL_EDITABLE = {
    "local.temperature", "local.max_output_tokens", "agent.max_steps",
    "tools.max_read_bytes", "ui.language",
}
RANGES = {
    "local.context_tokens": (2048, 8192),
    "local.max_output_tokens": (128, 2048),
    "local.idle_seconds": (0, 300),
    "agent.max_steps": (1, 30),
    "agent.max_seconds": (30, 1800),
    "tools.max_read_bytes": (256, 4096),
    "external.max_output_tokens": (256, 8192),
}


def validate(settings):
    if set(settings) != set(DEFAULTS):
        raise Rejected("Configuration contains missing or unknown settings.")
    models = catalog()
    if settings["local.model"] not in models["local"]:
        raise Rejected("Unknown local model profile.")
    if settings["external.model"] not in models["external"]:
        raise Rejected("Unknown external model profile.")
    for key, bounds in RANGES.items():
        integer(settings[key], *bounds)
    if settings["local.max_output_tokens"] > settings["local.context_tokens"] // 2:
        raise Rejected("Output limit must leave at least half the local context for input.")
    if settings["local.kv_cache"] not in ("q8_0", "q4_0", "f16"):
        raise Rejected("KV cache must be q8_0, q4_0, or f16.")
    value = settings["local.temperature"]
    if type(value) not in (int, float) or not math.isfinite(value) or not 0 <= value <= 1.5:
        raise Rejected("Temperature must be finite and between 0 and 1.5.")
    for key in ("external.enabled", "tools.confirm_writes"):
        if type(settings[key]) is not bool:
            raise Rejected(f"{key} must be true or false.")
    language = settings["ui.language"]
    if not isinstance(language, str) or len(language) > 40 or not language.replace("-", "").replace(" ", "").isalpha():
        raise Rejected("Language must be a short name containing letters and spaces.")
    for key in DEFAULTS:
        if key.startswith("budget."):
            value = settings[key]
            if type(value) not in (int, float) or not math.isfinite(value) or not 0 <= value <= 10000:
                raise Rejected("Budget must be a finite USD amount from 0 to 10000.")
    return settings


class Settings:
    def __init__(self, directory):
        self.path = Path(directory) / "settings.json"
        self.previous = self.path.with_name("settings.previous.json")
        self.lock = threading.RLock()
        if not self.path.exists():
            atomic_json(self.path, DEFAULTS)
        self.read()

    def read(self):
        with self.lock:
            try:
                return validate(json.loads(self.path.read_text()))
            except (OSError, ValueError) as exc:
                raise Rejected("Settings are unreadable; restore the previous valid version.") from exc

    def set(self, key, value, *, human=False):
        with self.lock:
            if not human and key not in MODEL_EDITABLE:
                raise Rejected("This setting requires a direct human setting command.")
            old = self.read()
            if key not in old:
                raise Rejected("Unknown setting; use setting list.")
            new = copy.deepcopy(old)
            new[key] = value
            validate(new)
            atomic_json(self.previous, old)
            atomic_json(self.path, new)
            return {"setting": key, "previous": old[key], "value": value}

    def rollback(self):
        with self.lock:
            if not self.previous.exists():
                raise Rejected("No previous configuration exists.")
            previous = validate(json.loads(self.previous.read_text()))
            current = self.read()
            atomic_json(self.path, previous)
            atomic_json(self.previous, current)
            return previous
