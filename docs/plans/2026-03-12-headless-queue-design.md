# Headless / Queue-Driven Execution Design

Issue: https://github.com/cyberstorm-dev/matrix-tui/issues/282 (mirrors openclaw/nisto-home#282)

## Context
- Current entrypoint (`python -m matrix_agent`) boots the Matrix bot, GitHub webhook server, and TaskRunner; Matrix creds are required even if Matrix is unused.
- Channels are implemented for Matrix (`MatrixChannel`) and GitHub (`GitHubChannel`), both driving `TaskRunner.enqueue` with channel-specific result delivery.
- There is no CLI entrypoint for one-shot/headless workflows, and no queue-backed ingest path.

## Goals
- Run matrix-agent without Matrix: headless CLI to execute a named workflow with a provided payload.
- Queue-driven mode: consume jobs from Redis and publish results to Redis (separate channels for jobs/results).
- Preserve existing Matrix + GitHub behavior; headless is additive.

## Open Questions (need confirmation before implementation)
1) **Workflow routing** — what workflow names/payload schema should headless support? (e.g., map `workflow` to predefined prompt templates vs. free-form `message` text)
2) **Redis primitive** — prefer `XREADGROUP` (streams, ack/replay) or `BLPOP/BRPOP` (lists, simpler but lossy on crash)?
3) **Result channel** — write a hash per `correlation_id` (status, output, logs, timestamps) or push to a stream/list (allows subscribers)? TTL expectations?
4) **Sandbox reuse** — should headless jobs reuse per-workflow container names (idempotent) or always create ephemeral containers?
5) **Secrets/auth** — acceptable source for Redis credentials (env vars only?) and should payloads allow embedding tokens, or require them from env/config only?

## Options Considered
1) **Unified CLI with channel adapters (recommended)**
   - Add a Typer/argparse CLI (`matrix-agent headless ...`) that instantiates Settings/Decider/Sandbox/TaskRunner and uses new ChannelAdapters (`HeadlessChannel`, `RedisChannel`).
   - Pros: Reuses TaskRunner/Decider flow; minimal duplication; one codepath for Matrix and headless; easier to test.
   - Cons: Adds CLI surface and possibly Redis dependency.

2) **Standalone headless runner module**
   - Separate module that bypasses TaskRunner/Decider and shells directly into sandbox or Gemini runner.
   - Pros: Very small surface; fewer dependencies.
   - Cons: Duplicates orchestration, diverges from Matrix/GitHub behavior; higher maintenance risk.

3) **External shim publishing to Matrix**
   - Small CLI that writes jobs to a Matrix room the bot already listens to.
   - Pros: No new channels.
   - Cons: Defeats purpose (still requires Matrix); brittle and noisy.

**Recommendation:** Option 1 — extend existing architecture with explicit headless channels.

## Proposed Design (pending answers to questions)
### Settings/configuration
- Add flags/envs to `Settings`:
  - `headless_mode` (bool, default False) — skip Matrix login when true.
  - `headless_workflow` (string) — workflow name for one-shot CLI run.
  - `headless_payload_file` / `headless_payload_json` — inputs for one-shot mode.
  - Redis: `redis_url`, `redis_jobs_key`, `redis_results_key`, `redis_group`, `redis_consumer`, `redis_result_ttl_seconds`.
- Make Matrix creds optional when `headless_mode` or `redis_url` is set; validate configs accordingly.

### CLI surface
- Entry: `python -m matrix_agent headless run --workflow <name> --payload-file <path>|--payload-json '<json>' [--result-file <path>]`
- Queue: `python -m matrix_agent headless queue --workflow <default>|--workflow-from-payload --redis-url ... [--jobs-key matrix-agent:jobs --results-key matrix-agent:results --group matrix-agent --consumer <id>]`
- Keep `python -m matrix_agent` unchanged for Matrix+GitHub mode.

### Channel adapters
- **HeadlessChannel**: delivers updates/results to stdout (and optional file), returns status codes; `is_valid` always true for the single task.
- **RedisChannel**: consumes jobs from Redis, enqueues to TaskRunner with correlation id, publishes results/errors to Redis (`results_key` with structured JSON including correlation_id, status, output, started_at, finished_at). Uses stream group for ack/retry; configurable polling interval/backoff.

### Job schema
- Inbound JSON: `{ "correlation_id": str, "workflow": str, "payload": object|string, "reply_to"?: str, "artifacts"?: [str], "timeout_seconds"?: int }`
- Message sent to Decider: formatted string combining workflow + payload (keeps Decider interface intact). If `reply_to` is provided, use it as results key override.

### Execution flow
- `headless run`: parse payload, build `HeadlessChannel`, call `task_runner.enqueue(correlation_id or "headless-<ts>", message, channel)`, await completion and exit with status.
- `headless queue`: start Redis consumer task that reads jobs, enqueues with `RedisChannel`; TaskRunner workers execute; results publisher writes to Redis and acks message; retries on processing failure with exponential backoff + max attempts.
- Sandbox/container naming: default `headless-<correlation_id>` to allow optional reuse; configurable to always destroy on completion.

### Error handling & shutdown
- Honour existing `coding_timeout_seconds`; surface timeout status to Redis/CLI exit code.
- Graceful shutdown: cancel Redis consumer, stop TaskRunner workers, destroy containers; ensure in-flight jobs are either acked or left pending for replay.
- Input validation: reject missing workflow/payload; log and store error record in results channel.

### Testing
- Unit tests for: Settings validation (Matrix optional in headless), CLI arg parsing, Redis job parsing/result formatting (mocked Redis), TaskRunner + HeadlessChannel happy-path (using dummy Decider that echoes), retry/backoff logic.
- Integration-ish: in-memory fake Redis (fakeredis) to simulate queue roundtrip.

### Rollout
- Implement CLI + Settings + HeadlessChannel first (one-shot path).
- Add RedisChannel + consumer loop behind feature flags.
- Document examples in README (`Headless Usage`, `Redis Queue Usage`) including job schema and result format.

## Approval needed
Please confirm the open questions above and whether Redis Streams (with consumer groups) is acceptable; otherwise I will proceed with Option 1 using Streams by default and lists as a fallback flag.
