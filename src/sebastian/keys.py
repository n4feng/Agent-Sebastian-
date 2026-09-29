"""Operator-only key entry. Run in a networkless container with only the key volume."""
import argparse
import getpass
import os
import sys
import tempfile
from pathlib import Path


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("action", choices=("set", "list", "remove"))
    parser.add_argument("provider", nargs="?", choices=("openai", "anthropic"))
    parser.add_argument("--directory", default="/var/lib/sebastian/keys")
    args = parser.parse_args()
    directory = Path(args.directory)
    directory.mkdir(parents=True, exist_ok=True, mode=0o700)
    if args.action == "list":
        for provider in ("openai", "anthropic"):
            print(provider + ": " + ("installed" if (directory / (provider + ".key")).exists() else "absent"))
        return
    if not args.provider:
        parser.error("provider is required")
    if not sys.stdin.isatty():
        parser.error("use an interactive terminal; keys must not be passed through command arguments or pipes")
    destination = directory / (args.provider + ".key")
    if args.action == "remove":
        if input("Type remove to delete this provider key: ").strip() == "remove":
            destination.unlink(missing_ok=True)
            print("Key removed.")
        return
    value = getpass.getpass(args.provider + " API key (hidden): ").strip()
    if len(value) < 16 or any(c.isspace() for c in value):
        parser.error("key is empty or malformed")
    fd, temporary = tempfile.mkstemp(dir=directory, prefix=".new-")
    try:
        with os.fdopen(fd, "w") as stream:
            stream.write(value)
            stream.flush()
            os.fsync(stream.fileno())
        os.chmod(temporary, 0o600)
        os.replace(temporary, destination)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)
    print("Key installed in the Docker volume. Value was not printed.")


if __name__ == "__main__":
    main()
