# Docker / containers standards reference (news-digest)

> As of 2026-07, verify before relying: BuildKit/buildx defaults, Compose spec, and base-image
> guidance below were web-checked mid-2026, but the ecosystem moves fast. Re-check
> `docker buildx version`, Compose plugin version, and base-image digests before pinning or asserting.

Scope: two services built from `newsroom/Dockerfile` (Python) and `circulation/Dockerfile` (Rust),
wired by root `docker-compose.yml`; CI runs in-Docker via `Dockerfile.ci` variants. Non-root
`appuser` (uid/gid **1001**) throughout, named volumes shared across services, `python:3.14-slim` base.
The existing `newsroom/Dockerfile` is the house exemplar — match it, don't regress it.

## 1. Build (multi-stage, BuildKit, layer cache)

- **BuildKit is the default builder** (since Docker 23); `docker build` == `docker buildx build`. Keep the
  `# syntax=docker/dockerfile:1` first-line directive — it floats to the latest stable frontend and unlocks
  cache mounts / `--chmod` / `COPY --link`. This repo already has it.
- **Multi-stage**: build deps in a `builder` stage, copy only artifacts into a clean runtime stage. Build
  tools (`uv`, compilers, apt lists, the uv cache) must NOT reach the final image. newsroom does this: venv
  built in `builder`, `COPY --from=builder` into runtime.
- **Layer-cache ordering — deps before source.** Copy manifests (`pyproject.toml`, `Cargo.toml`/lock) and
  install deps FIRST, then `COPY` source. Source changes every commit; deps rarely do. Inverting this
  re-runs the expensive install on every code edit.
- **Cache mounts** (`RUN --mount=type=cache,target=...`) persist a package-manager cache across builds even
  when the layer itself rebuilds — 10x+ on dep installs. newsroom uses `--mount=type=cache,target=/root/.cache/uv`;
  Rust builds want `target=/usr/local/cargo/registry` + `target=.../target`.
- **`.dockerignore` is mandatory** — keeps `.git/`, `data/`, `target/`, `.venv/`, caches, and `.env` out of the
  build context (smaller/faster context, no secret leakage). Verify it excludes anything with secrets or bulk.
- **Minimise layers with intent**: chain related `RUN` steps with `&&` and clean up in the SAME layer
  (`rm -rf /var/lib/apt/lists/*` must be in the same `RUN` as `apt-get install`, or the bloat is baked into
  the layer regardless of a later cleanup). Don't over-collapse to the point of killing cache reuse.
- **`COPY --chown`** at copy time (not a later `chown -R`) — a recursive chown after copying a ~500MB venv
  duplicates the whole tree into a new layer. newsroom copies the venv already-owned by appuser for exactly this.
- Independent stages build in parallel automatically; put unrelated stages side-by-side rather than serializing.
- **`docker buildx bake`** drives multi-target builds from an HCL/JSON file (`docker-bake.hcl`) — declare the
  newsroom + circulation + `Dockerfile.ci` variants once with shared args, and bake builds them in parallel with
  one command. Fits this repo's 2-service + CI-variant layout better than hand-invoking `docker build` per image.

## 2. Base images

- **Default to Debian `-slim`** (glibc, ~30–80MB, real package manager, debuggable). This repo is on
  `python:3.14-slim` deliberately: scientific-Python deps (sklearn/scipy/numpy) ship **manylinux (glibc)
  wheels**; no compiler needed. This is documented in the Dockerfile header — keep it.
- **Avoid Alpine for Python.** musl libc means manylinux wheels don't apply; pip falls back to compiling from
  source (needs gcc + headers, adds layers and minutes; can be 50x slower to build). musllinux wheels (PEP 656)
  exist but many authors don't publish them. Only choose Alpine if every dep compiles cleanly on musl.
- **Never mix glibc/musl across stages** — compiling against glibc then copying into an Alpine runtime yields
  cryptic loader errors or silently broken behaviour. Keep builder and runtime on the same libc (newsroom:
  both slim, same `/app/.venv` path → relocatable venv).
- **Distroless / scratch** = smallest attack surface, non-root by default, no shell → NOT debuggable
  (`docker exec` impossible) and no apt for CVE patching mid-life. Classic fit is a static Rust binary runtime
  stage; a poor fit where you need `bash`/`ripgrep` at runtime (newsroom does, so it stays slim). Their model is
  **rebuild-don't-patch**: no in-place CVE patching, you pull a freshly rebuilt image (Chainguard/Wolfi ship these
  daily) — good hygiene, but it assumes a bump workflow, not `apt-get upgrade`.
- **Docker Hardened Images (DHI) went free + open source (Apache-2.0) in Dec 2025** — ~1000+ minimal, rootless,
  low/zero-CVE images (with SBOM/VEX attestations) on Docker Hub, no licensing gate. Treat this as a **mainstream
  free option**, not a compliance-only niche; the paid tier is now just enterprise extras (custom builds, extended
  lifecycle support). Note DHI ships on **both Debian and Alpine** bases and Wolfi/Chainguard offer non-static
  runtimes too — so "hardened/minimal" no longer implies "distroless = static Rust only". A hardened **glibc slim**
  base can be a drop-in for newsroom's Python runtime without the musl-wheel problem in §2's Alpine warning.
- **Pin base images.** Tag pin at minimum; **digest-pin (`@sha256:...`)** for reproducibility/supply-chain,
  with the human tag in a trailing comment. Trade-off: digests don't auto-get patches — pair with a bump
  workflow (Renovate/Dependabot). Note the SDK-bundled tools float; app deps are pinned via `constraints-prod.txt`.
- **Keep images small**: `--no-install-recommends` on apt, purge unused essentials when justified (newsroom
  force-removes `perl-base` — nothing depends on it and it only ships CVEs), one venv layer, no build tools in runtime.

## 3. Security

- **Run as non-root.** Create a fixed-UID user and `USER appuser` before the entrypoint. Fixed uid/gid **1001**
  here is load-bearing for shared-volume ownership (see §4) — don't let it drift, and don't rely on the base
  image's default nonroot uid when volumes are shared.
- **No secrets in layers or build-args.** `ARG`/`ENV` and every intermediate layer are readable in the image
  history — never bake API keys. Pass secrets at RUN time via `--mount=type=secret` (build) or env/`env_file`
  at runtime (compose injects `ANTHROPIC_API_KEY`, `RESEND_API_KEY`, etc. from the host env — they never enter an image).
- **Drop Linux capabilities** and add back only what's needed: `cap_drop: [ALL]` (+ `security_opt:
  ["no-new-privileges:true"]`) in compose. A web server rarely needs any caps.
- **Read-only rootfs where possible**: `read_only: true` + explicit `tmpfs`/named-volume mounts for the paths
  that must be writable (`/app/data`, session/history dirs). Forces writes to declared volumes; catches
  accidental in-image mutation. circulation is a good candidate (only `/data` is written).
- **Scan images** (Trivy is the de-facto OSS scanner: OS + language CVEs, misconfig, embedded secrets) as a CI/pre-deploy gate.
- **Lint + bloat, not just CVEs** — Trivy covers vulns but nothing here catches Dockerfile mistakes or layer
  fat. Add **`hadolint`** (Dockerfile linter; also runs ShellCheck over `RUN` bodies) as a CI gate, and
  **`dive`** to inspect per-layer size and spot wasted bytes / files that shouldn't ship. Both fit the
  in-Docker CI flow.
- **SBOM + provenance attestations**: `docker buildx build --sbom=true --provenance=true` attaches them to the
  image manifest (OCI attestations) keyed by digest — verify as a pipeline gate. Set OCI `LABEL`s
  (`org.opencontainers.image.source`, `.licenses`, …) as newsroom does for traceability.

## 4. Runtime (PID 1, signals, health, volume uid)

- **`ENTRYPOINT`/`CMD` in exec form** (`["prog","arg"]`), never shell form — shell form runs your process as a
  child of `/bin/sh`, which doesn't forward `SIGTERM`, so `docker stop` hangs then `SIGKILL`s. If the entrypoint
  is a shell script (newsroom's is), it must `exec` the final process so it inherits PID 1 and receives signals.
- **`tini`/`--init` for PID-1 reaping** when your process spawns children and doesn't reap zombies (the Claude
  CLI subprocess pattern is a candidate). `docker run --init` or compose `init: true` injects tini without editing the image.
- **`HEALTHCHECK`** for long-running services (circulation): a cheap `CMD` hitting a health route so the
  orchestrator sees real liveness, not just "process exists". Batch/one-shot jobs (newsroom) don't need one.
- **Resource limits**: prod host is a 4GB Hetzner box — set `mem_limit`/`cpus` (compose `deploy.resources` or
  top-level `mem_limit`) so a runaway build/embedding step can't OOM the host. Measure before tuning.
- **Shared-volume uid gotcha (this repo's recurring bite):** a named volume shared across services/hosts stores
  numeric uid/gid on disk, not usernames. If two images write the same volume under different uids, you get
  `Permission denied`. Everything writing `news-digest-data` / `claude-sessions` / `bash-history` MUST run as
  uid 1001. When creating the runtime dirs, `chown appuser:appuser` only the new dirs (not a recursive tree chown).
  A bind-mounted host dir (`./data:/app/data`) inherits host ownership — ensure host uid matches or writes fail.

## 5. Compose v2 idioms + dev/prod parity

- **Compose V2** (the `docker compose` plugin) is the standard; the standalone `docker-compose` v1 binary is
  deprecated/EOL. **Omit the top-level `version:` field** — it's obsolete and now warns; the Compose Specification
  is inferred. This repo's compose is already version-less. Good.
- **`compose.yaml` is now the canonical filename** — Compose prefers it over the legacy `docker-compose.yml`
  (still supported for back-compat). New projects should use `compose.yaml`; renaming an existing one is optional
  churn, not required.
- **`docker compose watch`** (`develop.watch` in the service, GA since Compose 2.22) is the modern dev inner-loop:
  declare `sync`/`rebuild` actions and edits on the host sync into (or rebuild) the container automatically — a
  cleaner story than ad-hoc bind-mounts for live-reload during local development.
- **`docker init`** scaffolds a starter `Dockerfile` + `compose.yaml` + `.dockerignore` for a new service and
  **supports Python** — a reasonable starting point for a third service, though tune it to match the newsroom
  exemplar (uid 1001, slim base, cache mounts) rather than shipping the defaults.
- **`depends_on` with `condition`** (`service_healthy` / `service_completed_successfully`) for real ordering —
  bare `depends_on` only waits for container start, not readiness. Pair with a `HEALTHCHECK`.
- **`env_file:` / env passthrough**: list bare var names (`- ANTHROPIC_API_KEY`) to pass host env through
  without hardcoding — the repo does this. Keep `.env` gitignored and out of the build context.
- **`profiles:`** to gate optional services (e.g. only spin up CI or a one-off tool when asked) so `docker compose
  up` stays lean. The `ci` / `ci-rust` services here are natural profile candidates.
- **Read-only bind mounts** (`:ro`) for inputs the container must not mutate (`digest.css`, `prompts`, `sources.json`
  are mounted `:ro`); use `rw` only where a route actually writes (circulation's `/data` records feedback votes).
- **Dev/prod parity**: same base image + same Dockerfile across dev and prod; layer environment differences via
  compose override files / env, not divergent images. CI-in-Docker (`Dockerfile.ci`) keeps the CI toolchain
  identical to what devs run — preserve that. Prod pins deps (`constraints-prod.txt`); CI floats on purpose so a
  bad upstream release fails CI before it reaches a prod build.

## 6. Pitfalls / anti-patterns

- **`latest` tag** — non-deterministic; a rebuild silently pulls a different base. Pin tag+digest.
- **apt cache bloat** — forgetting `--no-install-recommends` and/or not `rm -rf /var/lib/apt/lists/*` in the
  same `RUN`. Cleaning in a later layer does nothing; the bytes are already committed.
- **Running as root** — the default if you never add `USER`. Every container here should end on `USER appuser`.
- **COPY-order cache invalidation** — `COPY . .` early (or copying source before installing deps) busts the cache
  on every commit and re-runs installs. Copy manifests → install → copy source.
- **Fat images** — shipping build tools, test deps, `.git`, or the whole context into runtime. Use multi-stage +
  `.dockerignore`; the runtime stage should contain only what runs.
- **Secrets in build args / ENV / layers** — visible in `docker history`. Use runtime env or `--mount=type=secret`.
- **Recursive `chown -R` after a big COPY** — duplicates the layer. Use `COPY --chown`.
- **Shell-form entrypoint / no `exec`** — breaks signal forwarding; `docker stop` becomes a 10s timeout then kill.
- **Assuming username == uid on shared volumes** — it's the number that's stored; mismatched uid → permission errors.
