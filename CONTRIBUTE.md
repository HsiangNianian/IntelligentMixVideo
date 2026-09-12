# Contributing to IntelligentMixVideo

Thank you for contributing. Use English for commit messages, pull request titles,
and pull request descriptions. Keep discussions respectful and focused on the work.

## Before You Start

Check existing issues and pull requests before starting. Report bugs with the
application version or commit, operating system and architecture, reproduction
steps, expected behavior, actual behavior, and relevant logs. Remove credentials
and personal data from logs. Discuss substantial features or architectural changes
in an issue before implementing them.

The current application is a minimal desktop client displaying local date and time.
The long-term roadmap does not imply that cloud editing or agent features already
exist. The Python server provides a minimal FastAPI application with example user
routes and no user storage. Run `uv run server` from `server/` to start the API.

## Development Setup

Fork the repository, clone your fork, and create a focused branch from the current
contribution base. Check the upstream repository's current default branch or the
branch named by a maintainer; do not assume it is always `main` or `dev`.
Use descriptive branch names such as `fix/clock-cleanup` or `docs/setup-guide`.

Install the Bun version specified in `client/package.json`, Rust stable, and the
[Tauri platform prerequisites](https://v2.tauri.app/start/prerequisites/).
Python work requires Python 3.12 or later.

```sh
cd client
bun install --frozen-lockfile
bun run tauri dev
```

`bun run dev` starts only the frontend. React components live in
`client/src/components/`; Rust and Tauri configuration live in `client/src-tauri/`.
Keep components focused and clean up timers and subscriptions on unmount.
Follow existing formatting and keep TypeScript strict checks passing.

Maintain `client/bun.lock` and `client/src-tauri/Cargo.lock`. Do not introduce other
JavaScript package manager lockfiles, commit secrets, or include generated
`node_modules/`, `dist/`, or `target/` directories.

## Validation

Run checks appropriate to the files you change and report their actual results.

| Change | Command | Working directory |
| --- | --- | --- |
| Frontend | `bun run build` | `client/` |
| Rust formatting | `cargo fmt --manifest-path client/src-tauri/Cargo.toml --check` | Repository root |
| Desktop compilation and packaging | `bun run tauri build` | `client/` |
| Release scripts and recovery | `bun test ./.github/scripts` | Repository root |
| Release version injection | `bun .github/scripts/release-smoke.mjs` | Repository root |
| GitHub workflows | `actionlint .github/workflows/client-build.yml .github/workflows/release.yml .github/workflows/validation.yml` | Repository root |
| Python packaging | `uv build --project server --out-dir server/dist` | Repository root |
| API routes | `uv run --locked --project server python -m unittest discover -s server/tests -v` | Repository root |
| Repository hygiene | `uvx pre-commit run --all-files` | Repository root |
| All changes | `git diff --check HEAD` | Repository root |

Install [uv](https://docs.astral.sh/uv/) for Python packaging and local pre-commit
checks. `.pre-commit-config.yaml` is shared with pre-commit.ci; run
`uvx pre-commit validate-config` when changing it. Optionally run
`uvx pre-commit install` to enable local Git hooks. These hooks check file hygiene
and syntax; application builds and release validation run separately in Actions.

For desktop changes, also launch the app and check affected behavior. A frontend
build does not validate the native application or other platforms. State checks
you could not run and why. Documentation-only changes need content, link, and diff
checks, without a full application build.

There is no configured frontend test runner or coverage threshold. Add meaningful
regression coverage for behavior changes where an appropriate test seam exists;
document any new test runner and its invocation.

## Commit Message Format

Follow [Conventional Commits 1.0.0](https://www.conventionalcommits.org/en/v1.0.0/).
Each commit should represent one coherent change. Use this structure:

```text
type(scope): short imperative description

Optional body explaining the reason and relevant implementation choices.

Optional footers, such as Refs: #123
```

Use lowercase types: `feat`, `fix`, `docs`, `refactor`, `perf`, `test`, `build`,
`ci`, `style`, `chore`, or `revert`. Use `style` for formatting-only changes.
Scopes are optional; examples include `client`, `server`, `release`, and `deps`.
Prefer a subject of at most 72 characters, without a trailing period.

```text
feat(client): add a timezone selector
fix(client): clear the clock interval on unmount
docs: add contribution and licensing guidelines
ci(release): validate installer versions
```

Mark incompatible changes with `!` before the colon and explain the impact and
migration in a `BREAKING CHANGE:` footer:

```text
feat(client)!: replace the settings file format

BREAKING CHANGE: Existing settings must be migrated to the new JSON format.
```

Use `Refs: #123` to reference an issue and `Closes #123` when the change resolves it.
These conventions support the automatically generated release changelog.

## Pull Request Format and Review

Use a Conventional Commit title, for example `fix(client): clear the clock interval
on unmount`. Fill in the [pull request template](.github/pull_request_template.md):

- **Summary:** explain the problem and resulting behavior.
- **Related issues:** link relevant issues or state that none apply.
- **Validation:** list commands, results, manual checks, and tested platforms.
- **Screenshots:** show UI changes, or mark this section not applicable.
- **Compatibility:** describe breaking changes, migration steps, and limitations.

Keep each PR focused and use a draft PR for unfinished work. Update relevant
documentation and respond to review feedback. Applicable CI checks must pass before
merge; disclose checks skipped by path filters. If a maintainer squashes the PR,
the final commit message should retain its Conventional Commit format and any
breaking-change footer.

Release versions come from `vX.Y.Z` tags and are injected only in CI. Do not
manually synchronize release versions, move published tags, or edit generated
release entries in `CHANGELOG.md` as part of an ordinary contribution.

## License

IntelligentMixVideo is licensed under the GNU Affero General Public License,
version 3 only (`AGPL-3.0-only`); see [LICENSE.md](LICENSE.md).
By submitting a contribution, you agree to license it under these same terms.
Submit only material you have the right to contribute and preserve applicable
copyright and third-party license notices.
