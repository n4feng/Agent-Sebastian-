# Agent Sebastian

A terminal agent for a 36 GB Apple Silicon Mac, with interchangeable local models and explicitly approved commercial-model consultations.

The terminal and gateway run in Docker. Docker Model Runner runs the inference engine in its native macOS sandbox so it can use the Apple GPU. The terminal container has **no IP network**, no API keys, and no Docker socket. All inference passes through a separate gateway.

**Start with [the Docker deployment manual](docs/deployment-macos.md), then [the daily-use manual](docs/user-manual.md).** The [security and implementation notes](docs/security.md) describe what is enforced and what remains a deployment assumption.

## Included

- Local profiles: Qwen3.8-27B Q4_K_M, Muse Glimmer-30B Q4_K_M, Gemma 4 31B IT QAT Q4_0.
- One local inference slot; 4,096-token context; quantized KV cache; bounded batch sizes; unload before model changes.
- `chat`, `exit`, `setting`, `models`, `budget`, `prices`, `doctor`, `audit`, and `unload` commands.
- Read/list/search tools, approved writes into `../playground`, and validated self-service preference updates.
- OpenAI Responses and Anthropic Messages adapters, including GPT-5 and Codex profiles and Sonnet/Opus profiles from 4.6 onward.
- Credentials entered invisibly into a Docker volume, mounted only into the gateway and operator key utility.
- Mandatory human review of each external payload; detected secrets are blocked.
- Transactional spending reservations, conservative accounting, no automatic retries, seven-day price freshness checks, and zero default spending allowance.
- Two editable diagrams: [system architecture](docs/diagrams/system-architecture.drawio) and [external request controls](docs/diagrams/outbound-control.drawio).

## Initial commands

After installing the Docker version and enabling Model Runner as described in the deployment manual:

```bash
./bin/sebastian setup
./bin/sebastian models pull qwen3.8-27b
./bin/sebastian chat
```

In chat, type `exit` to return to the command prompt. Type `exit` again to quit. Use `/setting list` in chat or `setting list` at the command prompt.

## Filesystem layout

Create a dedicated parent directory containing `Agent-Sebastian-` and its sibling `playground`. The terminal receives exactly two host filesystem bind mounts: the project, read-only, and `../playground`, writable. The parent directory, home directory, SSH files, cloud credentials, and Docker socket are not mounted. Docker-managed state, credentials, and IPC volumes are additional internal storage.

## Validation status

The automated tests exercise the application with simulated local and commercial providers; they do not download weights or spend API credits. The development workspace has no Docker daemon, Apple GPU, or usable Unix sockets. Mac memory stability, actual model tool calling, container startup, and live provider access must therefore pass the deployment smoke tests before relying on the installation. No measured tokens-per-second or peak-memory claim is made.

```bash
PYTHONPATH=src python3 -m unittest discover -s tests -v
```

This initial release supports UTF-8 text tasks and controlled file operations. It does not execute generated programs, browse the web, install arbitrary MCP servers, or automatically resume interrupted actions. The tool registry and model adapters are separated so those features can be added deliberately.
