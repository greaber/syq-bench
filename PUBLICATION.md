# Publishing benchmark results

As of 2026-09-09, experiments run in a private workspace. The public repository
contains the released harness, portable recipes and reviewed measurements.

## Release identity

Published comparisons normally describe an actual syq release. Experiments may
measure an unreleased commit first. Record the exact tested source revision,
executable hashes, build settings and tool versions in the original capture.
Do not claim to have tested a downloadable release binary when a custom build
was measured.

Reuse pre-release measurements when the release contains identical benchmark-
relevant code. Review the intervening source, dependency and build changes.
Documentation or SDK-only changes can be irrelevant when the benchmark exercises
the native CLI. Record the equivalence review privately and retain the tested
revision in the published provenance; a release label does not replace it.
If equivalence is uncertain, rerun affected comparisons or keep the measurements
as private experiments. Older published results may remain under their actual
release labels; do not silently relabel them as the newest release.

## Harness identity and procedure

Retain the original harness revision privately. Before publishing, transfer the
corresponding measurement code into public history and compare it byte-for-byte.
The public capture can reference that public commit, with the private-to-public
mapping and comparison evidence retained privately. A public commit identifies
equivalent source, not an assertion that its Git metadata existed at run time.
Report-layout or documentation edits alone do not require new measurements.

Record the resolved workload, tool arguments, seed, sizes, repetitions, timing
boundaries, verification, cache preparation and flushing policy. A recipe path
alone is insufficient. Changes to these controls or their implementation can
change the comparison and need review; do not treat them as cosmetic harness
changes. Preserve failed, short and unfavorable rows in selected captures.

## Coverage and publication

Run targeted comparisons and regression controls for the area being changed.
Refreshing the four homepage comparisons is the default for a release-page
update, not a requirement to run the full campaign for every release. A partial
refresh is valid if every comparison clearly identifies the release measured.
The wider results page can retain older releases with their original identities.
Do not pool measurements from different workloads, environments or versions.
The current catalog's acceptance and source-revision checks still apply; mixed-
release page support must be implemented and tested before using that option.

Sanitize explicitly selected captures and inspect free text as well as structured
fields. Public recipes contain placeholders. Private host identifiers, original
logs, account credentials and operational state stay out of public Git history.
Standalone investigation tools and unpublished experiments remain private.

Transfer selected harness changes onto a branch based on public history and
review both content and commit messages before a public PR. Never merge private
branches wholesale or push private refs. Bring public harness releases back into
the private workspace to avoid diverging implementations.

## Rclone presentation preview (2026-09-11)

Prepare the separate page's presentation before collecting replacement results.
Existing screening measurements remain labelled as a preview; they are not the
three-run publication campaign. Replace short workloads with calibrated longer
ones and collect three verified repetitions per tool/setting before reporting.
Do not merge/deploy this preview as the completed results page.

The standalone preparation recipe is an inspectable proposed replacement for
the old experimental setup; do not claim it generated the old numbers. Its cache
and metadata semantics must be recorded when used. Experimental adapters are not
yet public; that limitation stays explicit. Matching public measurement code is
still required for a full reproducible release. Existing release-page acceptance
and the private/public provenance boundary remain unchanged.
