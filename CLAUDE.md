# Autonomy

Design package, contract spec, core library (Python and Go) and the laptop lab for the Autonomy platform. `edge_server`, `processor` and `farm_automation` consume it: edge_server pins `core/python` by commit, the lab builds all four side by side. Overview: README.md, `design/README.md`.

## Commands
- No root build. Python 3.10+; no network here, so there is no `pip install`: the tests below run from source (pytest, jsonschema, PyYAML, cryptography are already installed). CI uses `pip install ".[test]"` in `core/python` and `./spec[test]` in `spec`.
- Core tests: `cd core/python && python3 -m pytest -q` (246 passed, 2 skipped, 18 s; `pythonpath = src` is configured). One file: `python3 -m pytest -q tests/test_peer.py`; one test: `tests/test_peer.py::test_reboot_is_allowed_with_a_grant_and_a_code`.
- Cedar policy tests skip without `cedarpy` (CI installs `.[test,cedar]`): `tests/test_pdp_gateway.py` runs only its non-Cedar half here (unverified with Cedar).
- Core lint and types (CI): `ruff check src tests`; `ruff format --check src tests`; `mypy src` (needs `types-PyYAML`; without it station.py fails on `yaml`).
- Go core: `cd core/go && go vet ./... && go test -race -count=1 ./...`; `gofmt -l .` must print nothing. Python and Go must agree: `cd core/python && python3 -m pytest -q tests/test_cross_go.py`.
- Spec: `cd spec && python3 -m pytest -q`; `PYTHONPATH=. python3 -m autonomy_conformance selftest`; from root `python3 spec/tools/gen_fixtures.py --check` (drop `--check` to rewrite).
- Docs: `python3 tools/check_docs.py` (links, ADR index). Secrets: from the repo root, `PYTHONPATH=core/python/src python3 -m autonomy_core.cli secrets scan . --allowlist .secrets-allowlist`.
- Lab (needs Docker, unverified here): `ops/lab/lab.sh up --mock-telegram [--farm|--farm-config|--nodes]`, `verify`, `reset`. `bash -n ops/lab/lab.sh` is verified. Guide: `ops/lab/README.md`.

## Architecture
- `design/`: architecture, component contracts, roadmap (M0-M23), `design/adr/` (ADR-001..018, listed in `adr/README.md`), `lab.md`, `node-mesh.md`.
- `spec/`: JSON Schemas (`schemas/v0/`), fixtures, vectors, examples and the `autonomy_conformance` CLI. `spec/tools/gen_fixtures.py` generates fixtures, vectors and examples.
- `core/python/src/autonomy_core/`: envelope, signing, replay, outbox, audit, registry, PDP and gateway (Cedar), approval, evaluator and verifier, models and agents, `peer.py` (node peer requests), station (read-only Home Assistant observer: `wsclient`, `world_ha`, `eventlog`). `core/go/` mirrors envelope, JCS, signing and registry.
- `ops/lab/`: `lab.sh` orchestrates the whole system in Docker (`authority/`, `farm/` with an HA stand-in and a real-HA path). `ops/ha-addon/autonomy-station/`: the Field Station add-on. `ops/hub/`: hub compose.
- Flow: components exchange signed envelopes (CloudEvents, Ed25519, JCS canonical JSON) validated against `spec/`; proposals go through the PDP and gateway under owner-signed grants, then approval, action, verification and an audit chain.

## Conventions
- Contract changes: edit `spec/schemas/v0`, add a case to `gen_fixtures.py`, regenerate, add a `spec/CHANGELOG.md` entry, and register signed documents in `spec/autonomy_conformance/crypto.py` and `core/.../signing.py`. Backward compatibility with the previous `spec-v0.*` tag is a CI job.
- Python line length 110, `mypy --strict`; ruff format here, not black.
- Every new ADR needs a row in `design/adr/README.md` and a link from `design/README.md` if it matters; `check_docs.py` enforces the index.
- Shell scripts that are run need the executable bit (ruff EXE001).
- Branch + PR to `main`, squash-merge after green CI, subject like `lab: ...`, `feat: ...`, `design: ...` with the PR number. Do not name a branch `dependabot`.

## Gotchas
- edge_server pins this repo's `core/python` by commit in four files; after a core change that edge_server needs, bump the pin there too (see edge_server/CLAUDE.md).
- Generated, do not edit by hand: `spec/fixtures`, `spec/vectors`, `spec/examples` (CI runs `gen_fixtures.py --check`). The public test keys in `spec/fixtures/keys` are for tests only.
- `.secrets-allowlist` lists test data that looks like a secret; a scan run outside the root path misreports it.
- The lab's full job needs the sibling checkouts (`LAB_REPOS`, default parent folder); `--farm-config` also needs a private `farm_automation` clone and the `LAB_REPOS_TOKEN` secret in CI.
- Tags are pushed by the owner only: `spec-vX.Y.Z` (the release job checks pyproject, `SPEC_VERSION`, `__version__` and the changelog agree) and `core-vX.Y.Z`. Cloud sessions cannot push tags or delete branches; `gh pr create` fails there, so use `gh api repos/realsystem/autonomy/pulls`.
- Docs lag: README says spec and core are v0.1.0; main is spec 0.6.0 and core 0.7.0 (both "unreleased" in their changelogs).

## Definition of done
- Core and spec tests pass; `ruff check`, `ruff format --check`, `mypy src`, `gofmt -l`, `go vet`, `go test -race` clean for what you touched.
- `gen_fixtures.py --check`, `check_docs.py` and the secret scan pass.
- Lab or `ops/` changes: the `lab` workflow's script checks pass; Docker jobs run in CI (cannot run in a cloud session).

## GitHub Free-plan limits
This repo is public, so GitHub Actions minutes are free here. The account is on the GitHub Free plan and these limits still apply.

- **Workflows:** keep jobs lean. Use `ubuntu-latest`, add `concurrency` with `cancel-in-progress: true` and `timeout-minutes` to new jobs, and set `retention-days` (3-7) on uploaded artifacts. The account-wide cap is 20 concurrent jobs (5 macOS); extra jobs queue.
- **Files:** never commit a file over 50 MiB (GitHub warns) or 100 MiB (the push is blocked). Keep datasets, video, model weights and build output out of git; use Git LFS only if truly needed.
- **GitHub API:** 5,000 requests/hour authenticated (60 unauthenticated), search 30/min, plus secondary limits. Scripts or agents that call the API must paginate, use conditional requests, and not fan out across repos in parallel.
