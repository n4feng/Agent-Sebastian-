# Daily use of Agent Sebastian

Run commands from the project root with `./bin/sebastian`. The interface has a command prompt and a chat prompt. Your project is visible as `project/`; the sibling working folder is visible as `playground/`.

## Start and finish a session

```bash
./bin/sebastian start
./bin/sebastian
```

At `sebastian>`:

```text
chat
```

At `sebastian/chat>`, enter your task in ordinary text. Type `exit` to leave chat, then `exit` again to close the console. `quit` closes the console from either mode. You can enter chat directly with `./bin/sebastian chat`.

Use commands prefixed with `/` inside chat, such as `/setting list` or `/budget`. Bare `exit`, `quit`, and `clear` are also recognized in chat. Other unprefixed text is sent to the local agent as your instruction.

Ctrl-C stops waiting on the current turn and prevents the console from issuing its remaining tool calls. An already dispatched model or provider request may continue. Check `/budget` before repeating a commercial request. EOF/Ctrl-D closes the session.

## Common tasks

Try these instructions:

- “List the files in playground.”
- “Read playground/notes.md and summarize the next action.”
- “Create a folder playground/reports and write the summary to playground/reports/summary.md.”
- “Set my response language to French.”
- “Ask the configured external model to critique this short public explanation. Show me the payload and maximum cost first.”

The model may update allowed preferences through its settings tool. It cannot raise budgets, enable cloud access, switch to an arbitrary endpoint, read credentials, execute shell commands, or change its enforcement code. Use direct `setting` commands for operator-controlled changes.

File writes ask for `write` by default. Review the path and content before confirming. Existing files require an explicit overwrite argument; the model cannot overwrite one accidentally through the default call. Keep ordinary project backups: playground file overwrites are not a version-control system.

## Complete terminal command reference

| Command | Effect |
| --- | --- |
| `help` | Print command help |
| `chat` | Enter chat; creates a task if needed |
| `exit` | Leave chat; at the command prompt, quit |
| `quit` | Quit from either prompt |
| `clear` | Close the current task and start an empty chat |
| `setting list` | Display every persisted setting |
| `setting get KEY` | Display one setting |
| `setting set KEY VALUE` | Validate and persist a setting |
| `setting rollback` | Restore the previous complete configuration after confirmation |
| `models` | Display all registered local and external profiles |
| `status` | Show gateway health, settings, and budget state |
| `budget` | Display conservative accounted spend and unresolved reservations |
| `budget reconcile ID USD REFERENCE` | Settle an unresolved reservation after checking provider evidence |
| `prices` | Display rates, official sources, and verification dates |
| `prices verify MODEL INPUT OUTPUT CACHE_WRITE` | Record freshly verified rates and reset the seven-day expiry |
| `doctor` | Check key presence and Model Runner reachability/listings |
| `unload` | Unload registered Sebastian models from Model Runner |
| `audit` | Show recent events without full prompts, responses, or keys |

The same commands can be invoked from the host, for example `./bin/sebastian setting list`. Commands that change authority or accounting ask for terminal confirmation. A command printed by a model is not authorization and is not executed automatically.

## Host helper commands

| Command | Effect |
| --- | --- |
| `./bin/sebastian setup` | Create sibling playground, build image, and start gateway |
| `./bin/sebastian start` | Start the gateway and wait for health |
| `./bin/sebastian stop` | Stop the gateway, preserve volumes |
| `./bin/sebastian down` | Remove Compose containers/network, preserve volumes |
| `./bin/sebastian` or `./bin/sebastian console` | Open the terminal interface |
| `./bin/sebastian models pull PROFILE` | Download the selected Q4 model through Model Runner |
| `./bin/sebastian models pull all` | Download all three, sequentially |
| `./bin/sebastian keys set openai` | Enter/replace an OpenAI key invisibly in Docker |
| `./bin/sebastian keys set anthropic` | Enter/replace an Anthropic key invisibly in Docker |
| `./bin/sebastian keys list` | Show installed/absent status, never values |
| `./bin/sebastian keys remove PROVIDER` | Remove a provider key after confirmation |
| `./bin/sebastian logs` | Show the gateway's recent service logs |

Downloading models and installing keys happen outside the agent's tool interface. Stop or unload models before shutting down if you want memory released promptly.

## Every adjustable setting

Settings persist in the gateway-state Docker volume. JSON booleans are `true` and `false`; numeric values are numbers. For text values, quotes are optional unless the shell requires them.

| Key | Default | Allowed values | Model may change it |
| --- | --- | --- | --- |
| `local.model` | `qwen3.8-27b` | The three registered local profile IDs | No |
| `local.context_tokens` | `4096` | Integer 2048–8192 | No |
| `local.max_output_tokens` | `768` | Integer 128–2048, at most half context | Yes |
| `local.temperature` | `0.3` | Finite number 0–1.5 | Yes |
| `local.kv_cache` | `q8_0` | `q8_0`, `q4_0`, `f16`; backend support required | No |
| `local.idle_seconds` | `60` | Integer 0–300 | No |
| `agent.max_steps` | `12` | Integer 1–30 per user turn | Yes |
| `agent.max_seconds` | `600` | Integer 30–1800 per user turn | No |
| `tools.max_read_bytes` | `2048` | Integer 256–4096 per excerpt | Yes |
| `tools.confirm_writes` | `true` | Boolean; disabling requires operator confirmation | No |
| `ui.language` | `English` | Up to 40 letters/spaces/hyphens | Yes |
| `external.enabled` | `false` | Boolean; enabling requires confirmation | No |
| `external.model` | `gpt-5` | Registered external model ID | No |
| `external.max_output_tokens` | `2048` | Integer 256–8192 billable output tokens | No |
| `budget.request_usd` | `0` | Finite number 0–10000 | No |
| `budget.task_usd` | `0` | Finite number 0–10000 | No |
| `budget.day_usd` | `0` | Finite number 0–10000 | No |
| `budget.month_usd` | `0` | Finite number 0–10000 | No |

Changing memory settings takes effect on the next local inference request. The maximum allowed context of 8192 is a guardrail, not a recommendation for this Mac. Start at 4096. Weight quantization is fixed by the profile's downloaded artifact; changing KV precision does not change weight precision.

`agent.max_seconds` is checked between actions and after human review; a network request already in progress may run to its own 180-second timeout. It is not an operating-system deadline for the provider. Local inference uses one slot, batch size 128, microbatch size 64, and no additional prompt-cache RAM; these are fixed in this release.

`ui.language` controls the model's response language; command names and system messages remain English. Reducing step limits or read sizes can help with slow models and short contexts. The context admission estimate is approximate; the backend's explicit context allocation is the memory control.

Example:

```text
setting set local.model muse-glimmer-30b
setting set local.context_tokens 4096
setting set local.max_output_tokens 512
setting set ui.language English
setting list
```

If a schema check rejects an edit, the existing settings remain intact. `setting rollback` restores the last complete version, including budgets and external settings, so it always asks for confirmation.

## Commercial model profiles

| Provider | Prepared IDs | Interface |
| --- | --- | --- |
| OpenAI | `gpt-5`, `gpt-5-codex`, `gpt-5.3-codex`, `gpt-5.6-sol`, `gpt-6-astra` | Responses API |
| Anthropic | `claude-sonnet-4-6`, `claude-opus-4-6`, `claude-sonnet-5`, `claude-opus-5-5` | Messages API |

The list includes the requested starting generations and current named successors. It is not a promise that every account has access or that an older ID will remain available. Intermediate releases and future IDs can be added to the registry after checking their API compatibility and prices. No automatic “latest” alias or paid fallback is used. This release sends text consultations only, without provider tools, image inputs, background jobs, or automatic retries.

These are API integrations; a ChatGPT or Claude web subscription is not used as the billing mechanism. The gateway does not store your web login. The exact provider ID and rates are displayed for each proposed request.

## External approval flow

When the agent uses `delegate`, the gateway assembles a single text message and checks it locally. It includes only the proposed subtask text; full chat history is not automatically attached.

- Recognized credentials and no-export sources block transmission.
- Confidential/NDA markers and inherited source labels produce visible review reasons.
- Text with no detected keyword still requires human export review in this release.
- The terminal displays the actual request body, destination, model, maximum reservation, and expiry.
- Type the one-time `approve XXXXXXXX` phrase to send; Enter rejects it.
- Any settings, pricing, or sensitivity change invalidates the old review. Tickets expire after five minutes and can be used once.

For redaction, reject the request and ask the local model to prepare a smaller or anonymized version. That creates a new payload and a new review. Do not approve confidential/NDA material unless you have authority to disclose it. Approval does not change the underlying confidentiality obligation.

Only the operator's actual terminal input can approve. The model cannot call an approval tool. A secret block cannot be overridden by typing an approval phrase.

## Spending and recovery

Every request reserves its conservative maximum before dispatch. Request, chat-task, daily, and monthly allowances must all have room. Daily/monthly boundaries use **America/Halifax**. Unresolved reservations from earlier days or months still reduce today's available allowance.

The byte-based input bound includes a framing margin; output is explicitly capped. All input is accounted at the maximum configured input/cache-write rate, so `accounted_usd` can exceed the actual discounted bill. Billing accuracy depends on verified rates and provider usage semantics. If the provider reports usage beyond the reservation, billing freezes for investigation; this release does not promise control of taxes, FX, or other applications' spending.

After a timeout, `budget` shows a retained reservation. Check your provider usage dashboard/request records. Once you know the final charge, run:

```text
budget reconcile REQUEST_ID 0.012 provider-receipt-reference
```

Use a non-sensitive reference with at least eight characters. Type `confirm` only after verifying the evidence. Do not use zero merely because the result was lost. A duplicate approval is rejected; repeating the original task creates a separate potentially billable request.

`clear` or a new chat creates a new task allowance; daily and monthly limits continue to apply. Settings, tasks' metadata, and the financial ledger survive restart. Full chat history and pending approval bodies are held only in memory and are not automatically resumed after interruption. An old model action is never replayed on startup.

## File tools and local memory

Read/list/search operations are limited to project and playground paths. Hidden components, parent traversal, symlinks, credential directories, binary formats, and non-regular files are rejected. Reads support regular UTF-8 text files up to 256 KiB; the model gets bounded excerpts. The `read_file` tool can continue at a specified line. Directory search currently checks the first configured excerpt of up to 40 files in one directory; it is not a full recursive repository index.

Put `confidential` or `restricted` in a folder name for a simple source label. The literal `[NO_EXPORT]` marker blocks cloud delegation for the current chat. Secret-bearing file content is withheld from the model; the chat remains restricted afterward, even if the current summary no longer mentions the source. Start a fresh chat only for genuinely separate work with empty context.

For persistent notes, ask the agent to write a file such as `playground/memory.md`, then explicitly read it in a later chat. There is no automatic cloud memory, remote embedding service, vector database, or transcript upload. Review notes before saving them, since the playground is ordinary host storage.

## What remains a human responsibility

Choose actual budget amounts, verify provider rates and data-handling terms, accept model licenses, validate the Mac's memory behavior, and protect Docker access. Detection rules cannot identify every semantic secret or arbitrary encoding; mandatory review and restricted-source handling are deliberate protections against that limit. Broader tools and autonomous execution should be added only with corresponding isolation and tests.
