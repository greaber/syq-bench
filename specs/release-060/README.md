# Release 0.6.0 homepage comparisons

The four recipes retain the displayed tools, workload shape, seed, timing,
verification, and cache choices. Replace all placeholder paths and endpoints
with empty generated scratch destinations and installed tool paths.

Measurements used the official Linux x86-64 release executable. Each current
release and competitor setting ran three times in rotated order. Two 0.5.2
controls (official download and the historical custom build) ran immediately
after the first automatic syq copy; these drift checks are retained separately
and do not contribute to the 0.6.0 chart. The sanitized capture records their
position in `publication.original_round_order`.

GNU time wrapped each timed command; remote resource helpers wrapped their
respective executable with GNU time. Replace resource-helper placeholders
with equivalent wrappers if collecting those counters. Host snapshots and
checksum verification were outside the copy timer. Resource diagnostics and
raw host observations remain private. File contents were verified; regular
file metadata is governed by the displayed archival flags. Completion excludes
a final flush. See each page's environment and uncontrolled-state notes.

The corresponding measurement modules are byte-identical to the public
harness revision identified in each capture. These recipes document the
selected comparisons; they do not provision the original machines.
