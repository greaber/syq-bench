# Agent guidance

syq-bench measures [syq](https://github.com/greaber/syq). Most of this file is
shared with syq's `AGENTS.md`; keep the two aligned when the shared parts
change. `CLAUDE.md` is a symlink to this file.

## The syq checkout is a sibling

syq-bench and syq live in sibling directories: `../syq` relative to this
repository's primary checkout (`~/repos/syq` next to `~/repos/syq-bench`).
Read syq's code, `README.md`, `SERVER-TUNING.md`, and `AGENTS.md` there when a
question is about syq's behavior; do not guess and do not vendor copies.

`../syq` is the user's development checkout. Do not edit, build into, switch
branches in, or otherwise disturb it. When a task needs a specific syq
revision or a build you control, make a private checkout or worktree in a
gitignored location (for example `.syq-checkouts/<rev>/` here) and point the
harness at that binary. Never rely on which branch `../syq` happens to have
checked out.

During the rename (as of 2026-08-28) the user's older checkout is at
`~/repos/pcp`; treat it as read-only and prefer `../syq`.

## Worktrees

Start any task that may change the repository in a task-specific git worktree,
before investigating or editing code for that task. Treat the primary checkout
as a coordination checkout on `master`; do not introduce working-tree changes
there. Do not edit tracked files, create commits, or switch branches there. If
it is already dirty, preserve and report the existing changes: inherited
dirtiness does not block creating or using a separate task worktree, and is not
permission to clean, reset, or stash them. Apart from administering branches
and worktrees, only writes to gitignored files are normally allowed there.

Check `git status` and `git worktree list` before choosing a worktree. A branch
and worktree should correspond 1:1 with a task or pull request. Also check the
primary checkout's `current-plans/` for plans or handoff notes that cover the
topic. The normal setup from the primary checkout is:

```bash
git worktree add .worktrees/<task> -b <task> master
ln -s ../../current-plans .worktrees/<task>/current-plans
cd .worktrees/<task>
```

Keep `current-plans/` shared by symlinking it from task worktrees as shown
above; do not copy it. From a worktree, syq is at `../../../syq`; prefer
resolving it from the primary checkout (`git rev-parse --git-common-dir`)
rather than hard-coding the depth.

Use plain git commands as shown above. Do not use the `EnterWorktree` or
`ExitWorktree` tools; they are denied in `.claude/settings.json`.

Continue in an existing worktree only when you created it for the current task
or the user explicitly identified it as the target. Never infer ownership from
a plausible branch name. Before every file edit or write, confirm that
`git rev-parse --show-toplevel` points at the task worktree.

## Documentation over agent memory

Prefer durable, committed documentation over private memory. Measurement decisions
and their rationale go in `BENCHMARKING.md`; guidance every session needs goes here;
plans and handoff notes that change too fast for git go in `current-plans/`
(gitignored by design; check it before starting work on a topic it covers).
Measured numbers go in `results/` as data, not in prose. Use memory only for
what fits none of those.

When writing any of these, record decisions as current state plus the
rationale at the time, not as timeless policy. An assumption encoded as a
requirement can outlive its premise and steer later work in the wrong
direction. `BENCHMARKING.md` is deliberately opinionated about *what* to measure and
neutral about vendors, frameworks, and other choices that have not been made.

## Branch synchronization and handoff

- Durable task work belongs in commits on the task branch. Changes intended for
  `master` go through a pull request; do not commit them directly on `master` or
  merge task branches from the coordination checkout. Do not merge a pull
  request unless the user explicitly asks.
- When the conversation is unambiguously about completing the pull request for
  the agent's own task branch, a bare instruction such as "merge" counts as an
  explicit request to merge that pull request into its configured base branch.
  Merging any other branch or pull request requires an instruction that names
  both the source and `master`; ask when the destination is unclear.
- Before rebasing, resetting, or otherwise synchronizing a task branch with
  advancing `master`, require its worktree to be clean, including staged and
  untracked changes. Prefer a checkpoint commit. Never use reset to discard
  task work. If a safety stash is necessary, name it for the task, include
  untracked files, restore and verify afterwards, and report any stash you
  retain.
- Any status report about branch or PR work states the short SHA it refers to,
  even when nothing changed ("unchanged at `ab12cd3`"). At review handoff,
  state the branch and SHA, whether the worktree is clean, and which checks
  passed, failed, or were not run. Review-ready and merge-ready are separate
  states.
- For a PR review, resolve the PR's `headRefOid` first and review that SHA
  unless a verified local commit is ahead of it; state which one you reviewed.
  If it matches the last SHA reviewed, say the PR is unchanged instead of
  reviewing again.
- Before removing a worktree or branch, require a clean worktree, no retained
  stash, and no commits still needing integration.

## Publication boundary

This is the public harness snapshot. Private experiments, original captures,
operational scripts, and encrypted account credentials belong in the separate
private workspace. Transfer selected changes onto a branch based on public
history; never merge private branches or push private refs into this repository.
Review commit messages as well as file contents. Public harness releases are
brought back into the private workspace to avoid divergent implementations.

## Working on syq-bench

- `BENCHMARKING.md` records what the tool is for and why; the code is authoritative
  for what it does today. Keep the user-facing README focused on built behavior;
  do not document behavior that has not been decided or built.
- Distinguish explicit requirements from assumptions and design choices. If a
  supposed requirement creates substantial complexity, question the premise.
  Ask the user when the answer would materially change the product.
- Prefer one clear implementation. Add fallbacks only for a concrete scenario.
- Keep the dependency list short; a user's `uvx` fetches every dependency
  before a benchmark runs.
- **Benchmarks touch real machines.** The harness must pass `--rm` or
  `--delete` (or run `rm -rf`) only on a tree it generated itself in this
  run under the scratch destination, must refuse a non-empty destination, must remove only what it created, and must check free space
  before writing. Do not use global `drop_caches` by default: the user's
  servers are shared; evict only fixture pages (see `BENCHMARKING.md`). Treat user
  `--source` directories and remote hosts as read-only except for the
  designated scratch destination. Exercise the harness against temporary
  directories and, for remote paths, against hosts the user has named for
  that purpose.
- **No infrastructure identifiers in git.** The repository is general harness
  tooling. The user's IPs, hostnames, ssh aliases, and per-host scripts never
  go into tracked files or commit messages; specs are committed only as
  templates with placeholder endpoints. Run results embed host identity, so
  `results/` is gitignored; environment-specific helpers and captured results
  live in `current-plans/` (gitignored). This is a hard requirement.
- **Numbers must be honest.** Every stored result records what could and could
  not be controlled (cache state, filesystem, host facts, tool versions).
  A run whose cache could not be dropped, whose checksum did not verify, or
  whose scale is too small to be meaningful must say so in the data, not
  silently look like a clean result.
- Anything that spends money (cloud provisioning) or opens network exposure
  (firewall rules, listeners) requires an explicit user instruction naming
  the provider/host, and must print an estimate and a destroy/rollback path
  before doing it.

- **`BENCHMARKING.md` is a living document, not a spec.** It records what was
  decided and why, as of a date. A pull request that changes a measurement or
  product decision recorded there (what is measured, how, the protocol,
  safety rules, what results contain) edits the statement in that same
  pull request; git keeps the history, so stale text is not preserved, and
  a stale statement is not a requirement to code or review against. A
  reviewer who finds code and design disagreeing should ask which is
  right, not assume the design is. Workflow guidance such as this file is
  not a design decision.

## Before handing a change to review

Most review findings so far were in these categories; check each before
asking for review, with a test where one is cheap:

- Every resource the harness creates or removes: what proves ownership
  before removal, what state is left if the step fails halfway, what
  happens if two copies run at once.
- Every new numeric control or field: zero, negative, NaN, infinity, and
  boundary values are rejected or handled explicitly.
- Every data-dependent claim or status message shown to a reader (verified,
  curtailed, cache state, "fastest", "too short"): the condition that
  selects it is computed from stored fields (fixed wording is fine), and
  there is a test for the case where it would be wrong. Static headings,
  units, and explanatory notes are not covered.
- Numbers are printed with enough precision to keep their meaning (a 0.3 s
  timeout must not print as 0 s; a 1.5x cutoff must not print as 2x).
- Checks run as separate commands whose exit status is honoured (never
  `pytest | tail && commit`), and every scripted edit is verified to have
  landed before it is committed. One concern per pull request.

**Do not drop agreed requirements silently**: If you agreed to implement a
user requirement and later conclude it is unsafe, incorrect, infeasible, or
should be deferred, stop and tell the user before proceeding. Explain the
technical reason and ask whether to change scope.

## Long-running commands

- Benchmarks run for minutes. Keep output live (`tee` when capturing; never
  pipe through `head`, `tail`, or `grep -q`), give every custom poll a hard
  deadline and periodic progress, and use exit status or the JSON result as
  the machine-readable contract rather than grepping table text.
- If the command runtime backgrounds a live command, keep monitoring the
  original handle; do not launch a second copy. When stopping an owned
  command, terminate and verify its whole process group so no `syq`, `rsync`,
  or remote `ssh` survives as a stale worker.
- From a long-lived tmux session, a fresh shell may have stale `SSH_*`
  variables. Before concluding a host is unreachable, restore them in the
  shell that will run `ssh`:

  ```bash
  eval "$(tmux show-env -s | grep -E '^(SSH_|unset SSH_)')"
  ```

## Verification

**Fix problems, don't skip work**: When a check fails because a tool is
missing, use the repository's pinned, project-local setup (`uv sync --group
dev`) and retry. Do not install tools globally, use unpinned sources, or
change system configuration without explicit approval. If the fix needs
privileges or credentials, ask.

The normal checks are:

```bash
uv run ruff check . && uv run ruff format --check . && uv run pytest
```

Add a smoke run of the harness at a tiny scale against a temporary directory
for changes that touch the runner, tools, or fixtures. State what was and was
not verified, especially for remote, cache-drop, and performance behavior:
a test passing on a laptop says nothing about the numbers.
