import argparse
import json
import shlex
import sys

from .agent import Agent
from .common import Rejected, printable
from .files import Workspace
from .rpc import Client

HELP = """chat                         Enter chat mode
exit                         Leave chat; at main prompt, quit
quit                         Quit from either mode
clear                        Start a new chat task and clear context
setting list                 Show all settings
setting get KEY              Show one setting
setting set KEY VALUE        Validate and update a setting (JSON values or text)
setting rollback             Restore the previous settings version
models                       Show local and external model profiles
status                       Show gateway health, current settings, and budget
budget                       Show accounted costs and unresolved reservations
budget reconcile ID USD REF  Record a verified provider charge for an unresolved call
prices                       Show rates, sources, and verification dates
prices verify MODEL IN OUT CACHE_WRITE
                             Record freshly reviewed USD per million token rates
doctor                       Check gateway, key presence, and local model service
unload                       Unload Sebastian models from unified memory
audit                        Show recent redacted gateway events
help                         Show this command list

Commands work at the main prompt. In chat use /setting, /budget, etc.
Bare exit, quit, and clear also work in chat. Ctrl-C cancels the current turn;
it cannot guarantee cancellation of an already dispatched provider request.
"""


class Console:
    def __init__(self, client=None, workspace=None, ask=input, emit=print):
        self.client = client or Client()
        self.workspace = workspace or Workspace()
        self.ask, self.emit = ask, emit
        self.agent = None

    def show(self, value):
        self.emit(printable(json.dumps(value, indent=2, ensure_ascii=False)))

    def confirmation(self, description):
        self.emit(description)
        return self.ask("Type confirm to apply: ").strip() == "confirm"

    def command(self, line):
        parts = shlex.split(line.lstrip("/"))
        if not parts:
            return True
        name, *args = parts
        if name in ("exit", "quit"):
            was_chat = self.agent is not None
            if self.agent:
                self.agent.close()
                self.agent = None
            return name != "quit" and was_chat
        if name == "chat":
            if not self.agent:
                self.agent = Agent(self.client, self.workspace, self.ask, self.emit)
            self.emit("Chat mode. Type exit to return to the command prompt.")
        elif name == "clear":
            if self.agent:
                self.agent.close()
            self.agent = Agent(self.client, self.workspace, self.ask, self.emit)
            self.emit("New chat task. Previous sensitivity state is not carried into this new empty context.")
        elif name == "help":
            self.emit(HELP)
        elif name == "setting":
            self.setting(args)
        elif name == "budget" and args:
            if len(args) != 4 or args[0] != "reconcile":
                raise Rejected("Use budget reconcile ID USD REFERENCE.")
            if self.confirmation("Only reconcile after checking the provider's charge; this can release a reservation."):
                self.show(self.client.call("budget.reconcile", {"request": args[1], "usd": float(args[2]), "evidence": args[3]}, control=True))
        elif name == "prices" and args:
            if len(args) != 5 or args[0] != "verify":
                raise Rejected("Use prices verify MODEL INPUT OUTPUT CACHE_WRITE.")
            prices = self.client.call("prices")
            if args[1] not in prices:
                raise Rejected("Unknown external model.")
            if self.confirmation("Verify standard short-context rates at " + prices[args[1]]["source"] + ". Acknowledging updates the seven-day freshness date."):
                self.show(self.client.call("prices.verify", {"model": args[1], "input": float(args[2]), "output": float(args[3]), "cache_write": float(args[4])}, control=True))
        elif name == "status":
            self.show({"health": self.client.call("health"), "settings": self.client.call("settings.get"), "budget": self.client.call("budget")})
        elif name in ("models", "budget", "prices", "doctor", "audit"):
            self.show(self.client.call(name, control=name in ("doctor", "audit")))
        elif name == "unload":
            self.show(self.client.call("local.unload", control=True))
        else:
            raise Rejected("Unknown command. Use help or chat.")
        return True

    def setting(self, args):
        if not args or args == ["list"]:
            self.show(self.client.call("settings.get"))
        elif args[0] == "get" and len(args) == 2:
            settings = self.client.call("settings.get")
            if args[1] not in settings:
                raise Rejected("Unknown setting.")
            self.show({args[1]: settings[args[1]]})
        elif args[0] == "set" and len(args) >= 3:
            raw = " ".join(args[2:])
            try:
                value = json.loads(raw)
            except ValueError:
                value = raw
            settings = self.client.call("settings.get")
            key = args[1]
            if key not in settings:
                raise Rejected("Unknown setting.")
            needs_confirmation = key.startswith("budget.") or key == "external.enabled" or key == "tools.confirm_writes"
            if needs_confirmation and not self.confirmation(f"Change {key}: {settings[key]} -> {value}"):
                self.emit("Cancelled.")
                return
            self.show(self.client.call("settings.set", {"key": key, "value": value}, control=True))
            if key.startswith("local."):
                self.emit("Local runtime changes apply on the next inference request.")
        elif args == ["rollback"]:
            if self.confirmation("Restore the previous settings, including any prior budget or external-call settings?"):
                self.show(self.client.call("settings.rollback", control=True))
        else:
            raise Rejected("Use setting list|get KEY|set KEY VALUE|rollback.")

    def run(self, initial=None):
        self.emit("Agent Sebastian 0.1 — type help or chat. External spending starts disabled.")
        if initial:
            self.command(initial)
        while True:
            try:
                line = self.ask("sebastian/chat> " if self.agent else "sebastian> ")
                if self.agent and not line.startswith("/") and line.strip() not in ("exit", "quit", "clear"):
                    if line.strip():
                        before = len(self.agent.messages)
                        try:
                            self.agent.run(line)
                        except BaseException:
                            # Drop an incomplete turn, retain source sensitivity and financial state.
                            self.agent.messages = self.agent.messages[:before]
                            raise
                elif not self.command(line):
                    break
            except Rejected as exc:
                self.emit("Stopped: " + printable(str(exc)))
            except ValueError:
                self.emit("Invalid command value or quoting. Use help.")
            except KeyboardInterrupt:
                self.emit("\nTurn interrupted. Use /budget to inspect any in-flight cloud reservation.")
            except EOFError:
                if self.agent:
                    self.agent.close()
                break


def main():
    parser = argparse.ArgumentParser(description="Agent Sebastian terminal interface")
    parser.add_argument("command", nargs="*", help="Optional command; without one open the console")
    parser.add_argument("--socket", default="/run/sebastian/agent.sock")
    parser.add_argument("--control", default="/run/sebastian-control/control.sock")
    parser.add_argument("--project", default="/workspace/project")
    parser.add_argument("--playground", default="/workspace/playground")
    args = parser.parse_args()
    console = Console(Client(args.socket, args.control), Workspace(args.project, args.playground))
    try:
        if args.command and args.command != ["chat"]:
            console.command(shlex.join(args.command))
        else:
            console.run("chat" if args.command else None)
    except Rejected as exc:
        print("Stopped: " + printable(str(exc)), file=sys.stderr)
        raise SystemExit(1)


if __name__ == "__main__":
    main()
