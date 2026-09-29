# Security boundaries and implementation notes

## Trusted components

The gateway, terminal program, packaged tool registry, Docker Desktop, and host operator are trusted. The LLM, its tool-call arguments, file contents, and provider replies are untrusted. This release does not run model-generated code or install plugins. The operator control socket is accessible to the trusted terminal program, but is not exposed as a model tool. If arbitrary Python/shell execution is added later, it must run in another container without the control socket.

The terminal has `network_mode: none`; it communicates through a Unix socket volume. The gateway alone has outbound connectivity. Its transport accepts fixed local-model and provider destinations, requires HTTPS externally, ignores proxy environment settings, rejects redirects, and does not retry. No raw URL-fetch tool is exposed. This is not an OS firewall around a compromised gateway: a gateway code-execution vulnerability could bypass its allowlist. The small dependency-free codebase and read-only runtime reduce the exposed surface but do not replace security review.

There is no Docker socket mount, host networking, privileged container, broad home-directory bind, or public gateway port. Model Runner is separate from the Linux containers and has its own macOS sandbox. It remains an unauthenticated local Docker service; other host/container users must not be treated as adversaries on this single-user deployment.

## Files and configuration

Agent tools accept only `project/...` and `playground/...`. Directory-relative descriptors and `O_NOFOLLOW` reject symlink traversal, including intermediate path races. File writes use a new temporary file and atomic rename/link within an already-open directory. Project/runtime code is read-only. The gateway's settings and accounting files are not mounted in the terminal; changes use validated RPC operations.

The only agent-editable settings are response language, local temperature/output limit, turn step limit, and read excerpt size. Budget changes, external enabling, model selection, memory parameters, write-review policy, price verification, accounting reconciliation, and rollback require the trusted control interface. Configuration writes preserve one previous valid version and use atomic replacement.

## Outbound privacy

Each cloud request is text-only and explicitly approved. Screening checks the final serialized body for recognizable credentials, configured key values, some common encodings, sensitivity keywords, and inherited chat restrictions. It runs before any external operation; no remote moderation, embeddings, or token-counting service receives the payload first.

The secret scanner is heuristic. It can have false positives and false negatives, especially for novel or deliberately fragmented encodings. Therefore every external payload is reviewed, and sources marked no-export are blocked. This release does not claim complete NDA classification, contractual compliance, or protection against a malicious human operator.

Full provider bodies are not included in exception messages. Audit records contain request/task IDs, model choices, risk reasons, configuration changes, and accounting amounts. They do not contain ordinary prompt/response content or keys. Docker Model Runner may maintain its own local inference records; inspect its settings and retention separately. Local model processing is not equivalent to “nothing is logged anywhere on the host.”

Keys live in a Docker named volume and enter only provider authentication headers in the gateway. The key utility is networkless. Volume storage is not encrypted by the application. Docker administrators and host administrators remain able to inspect it.

## Budget and failure behavior

All spending starts disabled. The SQLite ledger uses integer microdollars and `BEGIN IMMEDIATE` for reservations, preventing concurrent tasks from allocating the same remaining allowance. Reservations persist before transmission. Errors and missing usage retain reservations; no request is retried automatically. Late/unknown charges remain exposed across budget-window rollover. Settled usage above the reservation freezes further billing.

Input reservation is a conservative UTF-8 byte count of the fixed text request plus 4096 framing tokens, charged at the largest applicable input/cache-write rate. There are no provider tools, image/audio tokens, long prompts, automatic server-side task loops, or premium tiers enabled. The maximum outgoing text is 24,000 UTF-8 bytes; billable output is capped separately. This is a documented accounting assumption, not a guarantee against a provider changing tokenization/billing semantics. A strict financial guarantee also requires provider-side controls, accurate rates, and exclusive use of dedicated keys. Taxes and currency conversion are excluded.

The application deliberately reports `accounted_usd`, not an exact invoice total: cached input and other discounts are conservatively ignored. Operators can reconcile unresolved requests using provider evidence. A billing freeze is not automatically lifted; investigate and correct the violated assumption before adding any recovery mechanism.

## Local model resource controls

The registry fixes Q4 artifacts. Gateway configuration sets one inference slot, a 4096-token initial context, small batches, quantized KV cache, disabled extra prompt-cache RAM, and an idle-unload timeout. It unloads the registered profiles before a configuration/model switch and refuses to continue while another backend remains loaded. The request lock serializes inference within this gateway.

Other programs can still load models or consume Mac RAM outside this gateway. Use Model Runner exclusively for this installation, monitor host memory pressure, and validate every selected model/backend combination. Container memory limits do not constrain the native Metal inference process. Unsupported flags fail visibly; no silent F16 or cloud fallback occurs.

Model Runner's configuration/unload API is isolated in `gateway.py`. Its field names were checked against the [official source](https://github.com/docker/model-runner/tree/7eb71ce999001854fc20745a78e102ef92a04565/pkg/inference) and current documentation. These management APIs may change; the deployment smoke tests must confirm them on the installed release.

## Initial-release scope

Implemented: terminal UI, local tool loop, validated settings, three quantized profiles, two commercial provider adapters, exact-payload review, source restrictions, reservations/reconciliation, audit events, Docker-volume credentials, and documentation/diagrams.

Deferred: shell/code execution, browser automation, remote MCP, automatic semantic classifiers, recursive retrieval indexing, unattended background tasks, autonomous multi-agent work, automatic price discovery, and automatic action resumption. Durable financial/task metadata is present; full conversation recovery is not. Explicit local notes provide the initial memory mechanism.

## Verification

Tests cover mutation-resistant approval snapshots, control-interface separation, secret blocking, sensitivity propagation, configuration invalidation, stale prices, concurrent reservations, rollover exposure, timeouts, malformed usage, billing freezes, filesystem traversal/symlinks, CLI tool execution, and local model configuration calls. Unix-socket integration runs where the execution environment permits sockets. Actual Docker/Metal/model/provider tests are separate deployment gates, not simulated test results.
