# Isolated QQ development agent

This directory contains the host controller for the private QQ-to-draft-PR development feature. It embeds `@earendil-works/pi-agent-core`; it never launches the interactive Pi CLI. The production bot only loads the disabled-by-default gateway in `src/plugins/dev_agent_gateway`.

## Safety boundary

- The Node controller and its SQLite database run on the host. Model and GitHub App credentials stay here.
- One Gondolin VM contains two or three unprivileged slot users (three by default). All model-issued reads, edits, searches, commands, dependency installs, migrations, tests, and staging runtimes run in that VM.
- Each chat owner is either `private:<user_id>` or `group:<group_id>`. SQLite foreign keys and every service lookup use `(owner_chat_key, session_ref)` together.
- Active workspaces are mode `0700` and owned by their slot user. Suspended workspaces are checkpointed and returned to root ownership. Host transcript directories use stable, distinct Unix UIDs and mode `0700`.
- Guest HTTP/HTTPS/WebSocket egress is allowed only to public addresses. Loopback, private, link-local/metadata, CGNAT/Tailscale, multicast, and local IPv6 ranges are denied; arbitrary TCP and non-DNS UDP have no mapping.
- A stable per-slot OneBot proxy checks inbound owner assignment before a staging runtime sees an event. It accepts only explicit `/test` commands from its assigned private or group chat, strips the `/test` prefix, and applies outbound allow/deny rules. Empty outbound allowlists are unrestricted; denylists take precedence.
- Publication is host-only. The broker can push only `refs/heads/agent/*`, and creates or updates one draft PR. There is no merge, tag, release, deployment, production restart, or production hot-reload path.

The feature fails closed when the controller or VM is unavailable. Ordinary production traffic is unaffected because the gateway only claims exact development/test commands or routes explicitly confirmed by the controller.

## Development

Node 24 or newer and pnpm 10.33.2 are required.

```sh
cd dev-agent
pnpm install --frozen-lockfile --ignore-scripts
pnpm check
```

The pinned runtime packages are:

- `@earendil-works/pi-agent-core@0.80.6`
- `@earendil-works/pi-ai@0.80.6`
- `@earendil-works/gondolin@0.12.0`

Gateway tests run with the repository test suite:

```sh
UV_CACHE_DIR=/tmp/uv-cache uv run pytest tests/test_dev_agent_gateway.py
```

## Build the Gondolin image

The image includes Alpine, Python 3.12, uv, Git, PostgreSQL 17, build/native dependencies, and the root-owned staging/proxy programs. It also bakes a root-owned read-only Python environment from the repository `uv.lock`. Sessions use that environment while their combined `pyproject.toml` and `uv.lock` hash matches; changing either file creates a session-local `.venv`. Rebuild the image whenever the lockfile on `main` changes.

```sh
cd dev-agent
sudo install -d -m 0700 /root/.cache/gondolin/tmp
sudo -H env TMPDIR=/root/.cache/gondolin/tmp \
  pnpm exec gondolin build --config image/build-config.json --tag ttd-dev-agent:latest
```

The build runs as root because its post-build commands use a chroot. An executable
`TMPDIR` is required on hosts where `/tmp` is mounted `noexec`. Gondolin requires
QEMU/KVM (or a separately configured supported backend). Network-policy integration
tests should be run on the deployment host after the image is built: public HTTPS
and WebSocket requests must work, while localhost, RFC1918, metadata, Docker,
CGNAT/Tailscale, raw TCP, and non-DNS UDP probes must fail.

## Host configuration

1. Create a GitHub App installed only on `trytodupe/ttd-bot` with Metadata read, Contents write, and Pull requests write permissions. Store its private key outside the repository.
2. Copy `controller.env.example` to `/etc/ttd-dev-agent/controller.env`, mode `0600`. Set the model key, GitHub App values, two or three high-entropy ingress secrets matching `TTD_DEV_MAX_SLOTS`, and the numeric gid used by the production `ttd` process.
3. Configure the same number of SnowLuma reverse-WebSocket clients, one per authenticated `/slot/0` through `/slot/N` route. Each client supplies `Authorization: Bearer <matching secret>`. Put a TLS reverse proxy in front of the loopback Gondolin ingress if SnowLuma is remote.
4. Install `ttd-dev-agent.openrc` as `/etc/init.d/ttd-dev-agent` and `ttd-dev-agent.confd` as `/etc/conf.d/ttd-dev-agent`, install dependencies with pnpm, build the image, and start the service. The conf.d setting enables service-cgroup cleanup so a forced stop cannot leave Gondolin QEMU children behind.
5. Keep both flags false initially:
   - controller: `TTD_DEV_AGENT_ENABLED=false`
   - production gateway: `DEV_AGENT_ENABLED=false`

The OpenRC controller intentionally runs as the trusted host broker because it boots the VM, owns publication credentials, and assigns distinct transcript UIDs. The guest never receives the model key, GitHub key, or installation token.

## Rollout

Enable the controller first and confirm every configured slot proxy is authenticated and healthy. Then set `DEV_AGENT_ENABLED=true` for the production bot and restart it. Use the superuser-only commands to allow the owner before any other chat:

```text
/dev-admin access inbound allow user <id>
/dev-admin access inbound allow group <id>
/dev-admin access inbound deny user <id>
/dev-admin access outbound deny group <id>
/dev-admin model xiaomi-token-plan-cn mimo-v2.5-pro high
/dev-admin slots
/dev-admin kill on|off|status
```

Inbound access is default-deny. A denied user overrides an allowed group. Outbound access is unrestricted when its destination-type allowlist is empty; a configured deny always wins.

User commands are `/dev [help]`, `/dev <prompt>`, `/dev new <prompt>`, `/dev sessions [page]`, `/dev resume <ref>`, `/dev status`, `/dev stop`, `/dev compact`, `/dev publish`, and `/dev abandon confirm`. `/dev status` includes the Pi work state and cumulative token usage. After `/dev publish` activates staging, `/test <message>` goes only to the current chat's assigned staging proxy. Ordinary messages and QQ replies are never treated as implicit agent or staging input; follow-ups must use `/dev` and all staging tests must use `/test`.

## Persistent state and recovery

Host SQLite stores owners, tasks, sessions, slots, message routes, staging releases, pull requests, ACLs, and the durable outbox. Owner directories contain Pi transcripts and trusted workspace backup archives, including required localstore data. Workspaces are saved after each Pi turn, session changes, publication, and graceful shutdown.

The VM disk is never checkpointed. Every controller start creates a clean VM, and an occupied session is restored only when its chat next uses `/dev` or `/test`. One PostgreSQL server in the VM owns a separate password-protected role and database for each slot; cross-database access is revoked. Slot databases are reset and migrated when sessions change, so staging database contents are disposable and are not restored with a session.

Each slot has one staging runtime. `/dev publish` runs import/compile checks, tests for changed plugins plus changed/new tests, pre-commit, isolated migrations, and a health check. Unrelated existing plugin test failures do not block publication. Activation stops the prior runtime before starting the new version; if migration or startup fails, staging stays unavailable and the failure is sent to both QQ and Pi for a targeted fix or explicit revert. Source changes are never automatically discarded.

Merged or closed PRs are polled once per minute. Their sessions become immutable, their slots are released, and a later resume creates a continuation branch from the final session state. Opening a draft PR does not release a slot.

Do not copy QQ identifiers or transcripts into branch names, commits, or PR bodies. Branches use random task IDs, session references are opaque, and the publisher redacts owner IDs from optional PR metadata.
