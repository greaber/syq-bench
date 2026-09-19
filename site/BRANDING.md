# Shared site UI toolkit

As of 2026-09-06, both repos use the same toolkit for fonts, colors, homepage
titles/buttons, sidebars and header controls. The inventory is site-ui.json
under src/syq_bench here and theme in syq. Keep every listed pair identical.

The toolkit uses Open Sans prose at 20px, a Manrope wordmark, and IBM Plex
Mono commands and numbers. The syq wordmark stays blue on both sites.
Benchmarks use golden-orange background tints and a matching saturated
favicon (#ff9d00). The `bench-brand.css` adapter supplies the light/dark
surfaces and neutral text/control colors. Logos, syq bars and labels stay
blue; competitor bars and status colors keep the shared palette.
Both favicons use the Manrope “s”, blue for docs and golden orange for benchmarks.

Sidebars default to 300px, with 24px left padding,
12px right padding, 15px links and 16px section headings. Contents toggles an
animated sidebar. Drag its edge to resize, use arrow keys while the edge is
focused, or double-click / press Home to reset. Width is bounded to 240–480px
and constrained to the viewport; the phone drawer leaves 32px outside.
Width and desktop visibility persist when browser storage is available.

Header controls are Contents, Theme and Search on both sites. Theme offers
System, Light and Dark. Search uses mdBook's full-text index for docs and an
embedded page text index for benchmark reports. Bench search works
offline, including reports saved under custom names. Animation respects reduced
motion. Without JavaScript, native navigation remains available.

## Inventory and adapters

Shared files, relative to src/syq_bench here and theme in syq:

- brand.css: palette, fonts, header, homepage titles and buttons.
- sidebar.css / sidebar.js: sidebar layout, resizing, persistence, animation.
- controls.css / controls.js: shared header UI.
- site-nav.html here / header.hbs in syq: site links.
- fonts/: fonts and licenses.
- site-ui.json / check-site-ui.py: inventory and comparison command.

Each repo commits its own copies and builds independently. There is no package
release, network fetch or cross-repo dependency at build time. Benchmark HTML
embeds the assets; mdBook resolves fonts to its hashed resources.

public.css/public.py and theme/docs.css/docs.js are adapters for content and
mdBook integration. mdBook still generates chapters and search results, handles
code copying and syntax highlighting, and supplies the no-JS toolbar.
The toolkit replaces its toggle and resize handle and reconciles native touch
gestures. Native search/theme handlers are used behind the shared controls.

## Updating

Use task worktrees. Edit the toolkit here, copy each changed inventory entry
to the syq task tree, and compare before handing either PR to review:

    python3 src/syq_bench/check-site-ui.py src/syq_bench /path/to/syq-task/theme

The command fails for missing or different files, including extra font files.
It can also run from the syq copy with the same two arguments. Add new shared
files to both inventories. Update both repositories in paired PRs.

Identical files do not guarantee identical rendered styles: mdBook selectors
can override the toolkit, and benchmark anchors need explicit block layout to
receive vertical margins. The shared sidebar stylesheet sets heading spacing
and page-link rows explicitly for both renderers.

After building both sites, run the browser layout regression check with Node,
Playwright and its Chromium browser available (use NODE_PATH if Playwright is
installed outside this checkout):

    node scripts/check-sidebar-layout.cjs /path/to/syq-task/target/book

This checks actual heading rows, margins, typography, padding and overflow on
all benchmark pages and two docs pages, including 240px resized sidebars,
phone/desktop widths, light/dark and the no-JS iframe fallback. It intercepts
generated files inside the browser without opening a listener. Set
SIDEBAR_SCREENSHOTS to an ignored directory to capture desktop/phone views.

Rebuild benchmarks with uv run python scripts/build_site.py and run Ruff/pytest.
Build syq with pinned mdBook and run python3 scripts/check-doc-links.py.
Check both sites on desktop/phone, with/without JavaScript, light/dark/system,
and reduced motion. Check resizing, bounds, persistence, keyboard controls,
animation, search hits/misses, chapter links and code copying.
The shared header GitHub link points to syq; the benchmark footer separately
links to Benchmarks on GitHub.
