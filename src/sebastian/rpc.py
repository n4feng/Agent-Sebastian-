"""Bounded JSON messages over local Unix sockets; no agent IP network needed."""
import argparse
import json
import os
import socket
import socketserver
import threading
from pathlib import Path

from .common import Rejected, canonical

MAX_MESSAGE = 2_000_000


class Client:
    def __init__(self, socket_path="/run/sebastian/agent.sock", control_path="/run/sebastian-control/control.sock"):
        self.socket_path, self.control_path = socket_path, control_path

    def call(self, method, params=None, *, control=False):
        data = (canonical({"method": method, "params": params or {}}) + "\n").encode()
        if len(data) > MAX_MESSAGE:
            raise Rejected("Request is too large.")
        try:
            with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as connection:
                connection.settimeout(200)
                connection.connect(self.control_path if control else self.socket_path)
                connection.sendall(data)
                with connection.makefile("rb") as stream:
                    raw = stream.readline(MAX_MESSAGE + 1)
            if len(raw) > MAX_MESSAGE or not raw.endswith(b"\n"):
                raise Rejected("Invalid gateway response.")
            result = json.loads(raw)
        except (OSError, ValueError):
            raise Rejected("Gateway unavailable or request interrupted. Check status and outstanding reservations before retrying.") from None
        if "error" in result:
            raise Rejected(result["error"])
        return result["result"]


class Server(socketserver.ThreadingUnixStreamServer):
    daemon_threads = True
    request_queue_size = 4

    def __init__(self, path, gateway, control):
        self.gateway, self.control = gateway, control
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        Path(path).unlink(missing_ok=True)
        super().__init__(path, Handler)
        os.chmod(path, 0o660)


class Handler(socketserver.StreamRequestHandler):
    def handle(self):
        self.request.settimeout(210)
        try:
            raw = self.rfile.readline(MAX_MESSAGE + 1)
            if len(raw) > MAX_MESSAGE or not raw.endswith(b"\n"):
                raise Rejected("Invalid request size.")
            value = json.loads(raw)
            result = self.server.gateway.call(value["method"], value.get("params", {}), control=self.server.control)
            reply = {"result": result}
        except Rejected as exc:
            reply = {"error": str(exc)}
        except Exception:
            reply = {"error": "Operation failed validation or could not complete. Sensitive details omitted."}
        try:
            self.wfile.write((canonical(reply) + "\n").encode())
        except (BrokenPipeError, OSError):
            pass


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--state", default="/var/lib/sebastian/gateway")
    parser.add_argument("--keys", default="/var/lib/sebastian/keys")
    parser.add_argument("--socket", default="/run/sebastian/agent.sock")
    parser.add_argument("--control", default="/run/sebastian-control/control.sock")
    parser.add_argument("--health", action="store_true")
    args = parser.parse_args()
    if args.health:
        Client(args.socket, args.control).call("health")
        return
    from .gateway import Gateway
    gateway = Gateway(args.state, args.keys)
    public = Server(args.socket, gateway, False)
    control = Server(args.control, gateway, True)
    threading.Thread(target=public.serve_forever, daemon=True).start()
    print("Sebastian gateway ready. Payloads and credentials are not logged.", flush=True)
    try:
        control.serve_forever()
    finally:
        public.shutdown()
        control.server_close()
        public.server_close()


if __name__ == "__main__":
    main()
