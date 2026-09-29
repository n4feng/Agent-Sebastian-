# Deploy Agent Sebastian on an M3 Pro Mac

This manual targets a Mac with **36 GB unified memory**. Install the terminal and gateway in Docker and use Docker Model Runner for local inference. The models run one at a time. Cloud calls remain disabled until you install keys and explicitly set spending allowances.

## 1 Install the baseline software

Use **Docker Desktop 4.93.0 for Mac with Apple chip**, the release listed on 28 September 2026, or a later patched release after repeating the smoke tests. Its release notes list Docker Engine 29.8.1. Use the Docker Compose v2 and Model Runner CLI plugins bundled with Desktop; record their actual versions instead of separately installing older plugins. This is the intended baseline, not a claim that this repository was tested on that Docker release.

Official downloads and references:

- [Docker Desktop Mac installation](https://docs.docker.com/desktop/setup/install/mac-install/)
- [Docker Desktop release notes](https://docs.docker.com/desktop/release-notes/)
- [Docker Model Runner overview](https://docs.docker.com/ai/model-runner/)
- [Enable Model Runner](https://docs.docker.com/ai/model-runner/get-started/)

Use a macOS version supported by that Desktop release. Confirm Apple Silicon in About This Mac. Install Git if it is not already available. Host Python is optional: the deployed application uses Python 3.12 inside the image.

Start Docker Desktop. In Settings, enable Docker Model Runner and its Apple Silicon GPU support. Disable Docker Offload or any cloud inference fallback, and turn off usage statistics if you want to avoid model-name telemetry. Do not enable a public Model Runner TCP listener; the gateway uses `model-runner.docker.internal` from a container. Do not expose an unauthenticated model endpoint to your LAN.

Record:

```bash
docker version
docker compose version
docker model version
```

Docker's documentation explains that Model Runner uses a native sandbox on macOS, rather than giving an ordinary Linux container direct Metal GPU access. Its model memory is therefore **not included in the gateway container's 384 MiB limit**.

## 2 Allocate memory conservatively

In Docker Desktop Settings > Resources, start with **4 GB for the Linux VM**, 2 CPUs, and no unrelated containers. Leave Kubernetes disabled unless needed separately. The application services are capped at 384 MiB for the gateway, 512 MiB for the terminal, and 128 MiB for the temporary key utility. Their image is built for `linux/arm64`.

Use these starting inference settings:

| Setting | Initial value | Purpose |
| --- | --- | --- |
| Weight format | Q4_K_M for Qwen/Muse; QAT Q4_0 for Gemma | Fit weights with room for macOS and Docker |
| Context | 4096 tokens | Bound prompt plus generated tokens |
| Maximum generated tokens | 768 | Leave most of the context for input and tools |
| Parallel inference slots | 1 | Avoid duplicated KV allocations |
| KV cache K and V | q8_0 | Reduce cache size with a conservative precision choice |
| Batch / microbatch | 128 / 64 | Limit temporary computation buffers |
| Reusable prompt cache RAM | 0 | Avoid additional retained prompt-cache capacity |
| Idle residency | 60 seconds | Release model memory after inactivity |

The Q4 weight sizes are planning estimates: roughly 16–18 GB for Qwen and 17–20 GB for Muse/Gemma, depending on the published artifact. Actual model files, vision components, compute buffers, and KV cache add overhead. A 4-bit setting applies to weights; it does not make every runtime tensor 4-bit. KV cache precision is a separate setting.

Keep at least 7–9 GB of practical headroom for macOS and other apps, in addition to the Docker VM. This is a capacity plan, not a measured guarantee. Close memory-heavy browsers, IDEs, and other inference servers during the initial test. Do not increase context to a model's advertised maximum. If your other Docker workloads need more than 4 GB, reduce context or use a smaller model profile before loading them.

## 3 Clone into a dedicated parent folder

For example:

```bash
mkdir -p ~/SebastianWorkspace
cd ~/SebastianWorkspace
git clone https://github.com/n4feng/Agent-Sebastian-.git
cd Agent-Sebastian-
```

If the implementation pull request is not merged, check out its branch:

```bash
git switch feat/local-agent-foundation
```

The helper creates `~/SebastianWorkspace/playground`, exactly one level outside the project root. Use a real directory, not a symlink. Put only material the agent is permitted to use in that folder. If you want another parent location, move both sibling directories together.

The host bind mounts are:

| Host path | Terminal path | Access |
| --- | --- | --- |
| `Agent-Sebastian-/` | `/workspace/project` | Read-only |
| `playground/` beside it | `/workspace/playground` | Read and write |

The gateway receives neither bind mount. It sees only its packaged code, state, keys, and IPC volumes. The model receives text supplied through the gateway; it has no filesystem tool of its own. Docker Desktop itself remains a privileged host application under your control; this design restricts the containers and model tools, not the Mac administrator.

## 4 Build and start the services

```bash
./bin/sebastian setup
./bin/sebastian status
```

`setup` creates the sibling playground, reports Docker component versions, builds the image, and starts the gateway. It does not download model weights or call a paid model.

The Docker image currently uses the moving patch tag `python:3.12-slim-bookworm`. For a reproducible production installation, record the resolved image digest and pin it after your validation; no unverified digest has been invented in this repository.

The launcher maps your Mac UID/GID into the terminal container so it can write to the sibling folder. The gateway and key utility retain UID 10001. A supplementary group permits socket access without granting a credential mount. If the playground bind mount produces a permission error, make sure your Mac user can write to the sibling folder and Docker Desktop has file-sharing permission for this dedicated parent. Do not mount your entire home directory to work around it.

If you invoke `docker compose` directly instead of the helper and need file writes, set `SEBASTIAN_UID` and `SEBASTIAN_GID` to your host IDs first:

```bash
export SEBASTIAN_UID="$(id -u)"
export SEBASTIAN_GID="$(id -g)"
```

## 5 Download the requested local models

Start with Qwen alone:

```bash
./bin/sebastian models pull qwen3.8-27b
```

Then add the others when needed:

```bash
./bin/sebastian models pull muse-glimmer-30b
./bin/sebastian models pull gemma4-31b-it
```

Or download all three sequentially:

```bash
./bin/sebastian models pull all
```

The exact selected references are:

| Terminal profile | Model Runner reference |
| --- | --- |
| `qwen3.8-27b` | `hf.co/ggml-org/Qwen3.8-27B-GGUF:Q4_K_M` |
| `muse-glimmer-30b` | `hf.co/meta-models/Muse-Glimmer-30B-GGUF:Q4_K_M` |
| `gemma4-31b-it` | `hf.co/google/gemma-4-31B-it-qat-q4_0-gguf:Q4_0` |

These references were checked against the publishers' model pages on 28 September 2026. Qwen uses the GGUF conversion published by ggml-org; Muse and Gemma use their publishers' GGUF repositories. Review the model license and any gated-download terms before pulling. If authentication is required, complete it with the host download tool; do not put a Hugging Face token in chat or the agent configuration.

Model downloads are intentionally operator actions. The agent cannot download arbitrary models. Tags can change upstream; record `docker model inspect` output for each downloaded artifact if you need exact reproducibility.

Allow about 60 GB for three Q4 artifacts and **80–100 GB free disk space** for download/extraction and Docker overhead. This is a planning allowance. Disk storage is not the RAM cache: pulling all three does not authorize loading all three together.

## 6 Verify local chat and tool use

```bash
./bin/sebastian doctor
./bin/sebastian chat
```

Try:

> List the files under playground. Then create notes.md there containing a one-sentence introduction to this workspace.

Inspect the proposed file change and type `write` to apply it. Then ask:

> Read playground/notes.md and summarize it.

Confirm the file exists in the host sibling directory. Try a request to read `/etc/passwd` or `project/../...`; it must be rejected by the tool layer.

Switch models at the main prompt:

```text
setting set local.model muse-glimmer-30b
chat
```

In chat use:

```text
/setting set local.model gemma4-31b-it
```

On the next inference call, the gateway unloads the registered Sebastian profiles, confirms no other Model Runner backend remains loaded, and configures the selected model. A failed unload/configuration blocks inference; it does not fall back to a cloud provider or silently increase precision/context. Use Model Runner exclusively for Sebastian during these tests. Other host inference applications are outside the gateway's control.

## 7 Check actual memory and compatibility on your Mac

For each model separately:

1. Open Activity Monitor > Memory and note Memory Pressure and Swap Used before loading.
2. Run several local chat turns, a file read, and a file write. Verify the model generates actual structured tool calls instead of merely describing them.
3. Check `docker stats --no-stream` for the application containers and `docker model ps` for loaded models.
4. Confirm memory pressure stays green and swap does not keep growing over repeated turns. Check at least one longer prompt near your intended working size.
5. Run `/unload`, wait for release, and test the next model.

If the inference backend rejects q8_0 cache for a model, try `setting set local.kv_cache f16` **together with** `setting set local.context_tokens 2048` and `setting set local.max_output_tokens 512`. This trades cache precision for context length. If supported and quality remains acceptable, q4_0 is another smaller-cache option. Do not silently ignore backend errors.

If memory pressure becomes yellow/red, stop the task, unload the model, close other memory-heavy processes, and reduce context before trying again. If a 30B profile still does not fit reliably in your normal workload, a smaller model is the correct next choice; this code cannot reserve macOS unified memory against unrelated apps.

Model Runner's bundled llama.cpp version must support these architectures and the selected runtime flags. `doctor` confirms reachability, not tool quality. If inference fails, review `docker model logs` and the model publisher's current runtime instructions. Do not work around failures by enabling an unreviewed cloud fallback.

## 8 Install commercial API keys inside Docker

Use the key utility, which runs with networking disabled and mounts only the credentials volume:

```bash
./bin/sebastian keys set openai
./bin/sebastian keys set anthropic
./bin/sebastian keys list
```

The terminal prompt hides input. Keys are stored as mode-0600 files in the Docker-managed `agent-sebastian_credentials` volume and are mounted read-only into the gateway. They are not placed in the image, Compose YAML, environment variables, project directory, shell history, or terminal agent container.

Docker volumes are **not an encrypted secret vault**. A Mac administrator or someone with Docker-daemon access can read them. Use FileVault and protect Docker access. Rotate keys with the same `keys set` command; the gateway reads the current value on each request. Never paste keys into chat.

Remove a key when needed:

```bash
./bin/sebastian keys remove openai
```

This requires typing `remove` in the operator utility. Existing unsettled provider charges still need reconciliation.

## 9 Set a budget and verify prices before enabling cloud calls

All four allowances begin at **USD 0**. Choose your own limits. The values below are examples only:

```text
setting set budget.request_usd 0.25
setting set budget.task_usd 1
setting set budget.day_usd 2
setting set budget.month_usd 10
prices
```

Budget changes require typing `confirm`. Open the official source displayed by `prices`, check the exact model ID and standard short-context rates, then record them. Example syntax, using the documented GPT-5 rates at the time of authoring:

```text
prices verify gpt-5 1.25 10 1.25
setting set external.model gpt-5
setting set external.enabled true
```

`prices verify` arguments are input, output, and maximum cache-write rate, all in USD per million tokens. The code reserves and accounts all input at the larger of input and cache-write rates. This intentionally overstates some actual charges. Each price record expires after seven days; the gateway checks freshness before quoting and again before dispatch. It does not scrape pricing pages or guess new rates.

The budget covers direct provider usage in USD, before taxes and foreign-exchange conversion. Use dedicated provider projects/keys and set provider-side limits as an extra layer. Charges from other programs using the same account are outside this ledger.

First test with a short public prompt. Review the exact payload, destination, model, risk reasons, and maximum reservation. Type the displayed one-time `approve XXXXXXXX` phrase only if you authorize that export. This sends a real billed request. No live paid request was made while implementing this repository.

## 10 Validate isolation and accounting

From the host:

```bash
docker compose config --quiet
docker compose run --rm --no-deps --entrypoint python terminal -c 'from pathlib import Path; assert not list(Path("/var/lib/sebastian/keys").glob("*.key")); print("No credentials visible")'
docker compose run --rm --no-deps --entrypoint python terminal -c 'import socket; socket.create_connection(("1.1.1.1", 443), timeout=2)'
```

The last command should fail because the terminal has no network interface route. Do not interpret an unrelated connection failure as a complete security audit; also inspect the rendered Compose configuration. Verify that no Docker socket or broad host folder appears under volumes.

Run the automated tests in a local Python 3.12 environment or use the repository CI. The tests use synthetic credentials and provider fixtures only. For a real cloud request, compare `budget` with the provider dashboard and confirm that recorded accounting is at least the expected token charge. If usage is absent, the reservation remains unresolved; do not erase it just because the client timed out.

## 11 Daily startup, shutdown, and upgrades

```bash
./bin/sebastian start
./bin/sebastian
./bin/sebastian unload
./bin/sebastian stop
```

`exit` quits the terminal session; it does not stop the gateway. `stop` stops the gateway; it does not necessarily unload a host model immediately. Unload first, or allow the idle timer to release it. `down` removes the Compose containers and network while preserving named volumes.

Never use `docker compose down -v` as routine cleanup: it deletes credentials, accounting records, and settings. Back up the gateway-state volume before upgrades, and protect any credentials backup as a secret. Do not restore an old ledger snapshot after spending more money; doing so would hide newer charges. Record unresolved reservations before maintenance.

For an update, finish or cancel active work, inspect pending charges, unload the model, stop the gateway, review/pull the code change, and run `setup` to rebuild. Re-run the local model and isolation smoke tests after changes to Docker Desktop, model artifacts, or inference flags.

## Troubleshooting

| Symptom | Action |
| --- | --- |
| `docker model` missing | Enable Model Runner and check the bundled plugin installation |
| Gateway cannot reach Model Runner | Check Docker Desktop integration and `doctor`; do not expose it publicly |
| Model not found | Pull the exact profile from Step 5; inspect publisher access requirements |
| Another model is loaded | Stop the other Model Runner workload; use exclusive inference during this release |
| Context limit reached | Use `/clear`, smaller excerpts, or a narrower task |
| Tool calls are plain text or rejected | Verify bundled backend/template support; do not treat plain text as executable commands |
| Pricing stale | Review the official source and run `prices verify` |
| Request budget exceeded | Reduce output/task size or deliberately raise your chosen allowance |
| Connection lost after paid dispatch | Inspect `budget`; reconcile against provider evidence before retrying |
| Billing frozen | Investigate the price/usage-bound violation; no automatic unfreeze is provided |
| Secret blocked | Remove the secret from the task; a human cannot approve that payload unchanged |

## Deployment evidence to record

Keep the Docker Desktop/Engine/Compose/Model Runner versions, model inspect output, selected settings, peak observed memory pressure/swap, local tool-call results, and container isolation checks. The repository tests establish application behavior with fixtures; these host checks establish whether this particular Mac installation is ready for daily use.
