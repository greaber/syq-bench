# syq-bench

Measures [syq](https://github.com/greaber/syq) against rsync, cp, and friends
on your own hardware, across syq versions, and on reference machines.

The harness runs local and remote copy comparisons and builds static reports.
The interface is still evolving. See [BENCHMARKING.md](BENCHMARKING.md) for the measurement decisions
and limitations.

[Published benchmark results](https://greaber.github.io/syq-bench/)

Start with the [syq 0.5.2 recipes](specs/release-052/README.md) to repeat a
published comparison, or adapt a template under `specs/` for your own scratch
directories. Always inspect the plan with `uv run syq-bench run SPEC --dry-run`
before running a new spec.

Development needs Python 3.13 or later and [uv](https://docs.astral.sh/uv/).

```bash
uv sync --group dev
uv run ruff check . && uv run ruff format --check . && uv run pytest
```

The first reproducible public-cloud campaign targets Fly.io. Its declarative
config, credential setup, local cost plan, and recovery procedure are in
[`providers/fly/`](providers/fly/README.md).

A manual two-host Hetzner Cloud recipe and a large/mixed/small-file worker
sweep are in [`providers/hetzner/`](providers/hetzner/README.md).

Generate the static comparison page from one or more result files or
directories:

```bash
uv run syq-bench report results/ -o results/index.html
```

The output is a self-contained file suitable for local viewing or static
hosting. Results include host and endpoint identities, so do not publish it
until its inputs are explicitly safe to make public; see [BENCHMARKING.md](BENCHMARKING.md).

The public comparison layout is also available for your own results:

```bash
uv run syq-bench report results/ --public -o results/index.html
```

Use `--primary-tool syq-auto` for the Fly acceptance recipe. Each comparison
uses one run; controls and earlier runs remain inspectable. `--public` changes
the layout, not the privacy of the input data.
For an output named `first.html`, its companion pages are
`first.html.method.html` and `first.html.reproduce.html`; multiple reports
can coexist in one directory without changing each other's navigation.

Rebuild the published page without cloud access:

```bash
uv run python scripts/build_site.py
```

See [`site/README.md`](site/README.md) for the selected public data and build
process.

See [PUBLICATION.md](PUBLICATION.md) for release provenance and publishing results.

Licensed under [MIT](LICENSE). Bundled fonts retain their own licenses.
