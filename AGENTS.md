# achizitii-deschise

Date deschise despre achizițiile publice din România — prețuri unitare comparabile, format OCDS. Open data on Romanian public procurement: comparable unit prices, OCDS format.

## Commands

Run from the repository root. See [CONTRIBUTING.md](CONTRIBUTING.md) for prerequisites,
fixture scope and browser checks. npm installs browser tooling, not the Python project.

| Task | Command |
|---|---|
| install | `python3.12 -m venv .venv && .venv/bin/python -m pip install -e '.[dev]'` |
| offline fixture checks | `.venv/bin/python scripts/check_offline.py` |
| full tests | `./scripts/fetch_bundle.sh && .venv/bin/python -m pytest -q` |
| lint | `.venv/bin/ruff check .` |
| repository size | `.venv/bin/python scripts/check_repo_size.py` |

## Review and CI

`dev` is the contribution base; `main` is production. Agents must never merge
pull requests or deploy, even when their credentials permit it. Open a PR to `dev`.
The aggregate `verify` job requires every correctness dependency to succeed;
skipped, cancelled and failed jobs fail the gate. Repository rules are managed
separately; the presence of this workflow does not itself enforce branch protection.

## Working rules

- Branch from `dev` with an approved prefix: `feat/`, `fix/`, `chore/`, `docs/`,
  `sec/`, `adr/`. Land back into `dev` through a pull request.
- Conventional Commits. Imperative subject, lower case, no trailing full stop,
  72 characters hard limit. The body explains *why*; the diff already shows what.
- Never modify vendored third-party sources. Fix the environment instead.
- Contributor setup and fixture checks need no secrets or 1Password. Maintainers
  supply publishing credentials at runtime; never put credentials in files or commits.
- Verify before claiming completion. A merged pull request is not a deployment,
  and a git tag is not a publication.

Read [docs/CONTRIBUTOR_DOMAIN.md](docs/CONTRIBUTOR_DOMAIN.md) before changing domain logic.
All contributor requirements are in this repository; no private handbook is needed.
Maintainers create worktrees with `wt new <name> origin/dev` under
`<repo>/.worktrees/<name>`; contributors without `wt` can use a separate clone.
