"""Small, dependency-free primitives shared by the trusted components."""
import json
import os
import tempfile
from pathlib import Path


class Rejected(Exception):
    """A safe, user-facing error; never include a credential or provider body."""


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def atomic_json(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(dir=path.parent, prefix=".pending-")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            stream.write(json.dumps(value, indent=2, ensure_ascii=False) + "\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.chmod(temporary, 0o600)
        os.replace(temporary, path)
        directory = os.open(path.parent, os.O_RDONLY)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def text(value, maximum=24000):
    if not isinstance(value, str) or len(value.encode("utf-8")) > maximum:
        raise Rejected(f"Expected text of at most {maximum} UTF-8 bytes.")
    if "\x00" in value:
        raise Rejected("NUL bytes are not supported.")
    return value


def integer(value, low, high):
    if type(value) is not int or not low <= value <= high:
        raise Rejected(f"Expected an integer between {low} and {high}.")
    return value


def printable(value):
    """Prevent model/file text from injecting terminal escapes and controls."""
    return "".join(c if (c.isprintable() or c in "\n\t") else f"\\u{ord(c):04x}"
                   for c in str(value))
