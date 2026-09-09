// Run after building both sites. Requires Playwright and its Chromium browser.
// Usage: node scripts/check-sidebar-layout.cjs /path/to/syq/target/book
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const { chromium } = require('playwright');

assert(process.argv[2], 'Pass the built mdBook directory');
const roots = {
  bench: path.resolve(__dirname, '../site'),
  docs: path.resolve(process.argv[2]),
};

async function inspect(page, site) {
  return page.evaluate((site) => {
    const sidebar = document.querySelector(site === 'docs' ? '#mdbook-sidebar' : '#benchmark-sidebar');
    const box = sidebar.querySelector('.sidebar-scrollbox');
    const content = sidebar.querySelector('iframe')?.contentDocument || sidebar;
    const headings = [...content.querySelectorAll(site === 'docs' ? '.part-title' : '.page-link')];
    return {
      padding: getComputedStyle(box).padding,
      overflow: document.documentElement.scrollWidth > innerWidth,
      headings: headings.map(e => {
        const s = getComputedStyle(e), r = e.getBoundingClientRect();
        return { text: e.textContent, display: s.display, top: s.marginTop, bottom: s.marginBottom,
          font: s.fontSize, weight: s.fontWeight, y: r.y, height: r.height };
      }),
      clipped: [...sidebar.querySelectorAll('.nav-item')]
        .filter(e => e.scrollWidth > e.clientWidth).map(e => e.textContent),
    };
  }, site);
}

(async () => {
  const browser = await chromium.launch();
  const failures = [];
  try {
    for (const width of [320, 390, 620, 760, 820, 1440]) {
      for (const javaScriptEnabled of [true, false]) {
        for (const colorScheme of ['light', 'dark']) {
          const context = await browser.newContext({ viewport: { width, height: 1000 },
            javaScriptEnabled, colorScheme, reducedMotion: 'reduce' });
          // Serve only generated files through browser interception: no listener or public preview.
          await context.route('**/*', async route => {
            const url = new URL(route.request().url());
            const root = roots[url.hostname.split('.')[0]];
            const file = root && path.resolve(root, '.' + decodeURIComponent(url.pathname),
              url.pathname.endsWith('/') ? 'index.html' : '');
            if (!file || !file.startsWith(root + path.sep) || !fs.existsSync(file)) {
              await route.abort();
              return;
            }
            await route.fulfill({ path: file });
          });
          const page = await context.newPage();
          const errors = [];
          page.on('pageerror', error => errors.push(error.message));
          try {
            for (const site of Object.keys(roots)) {
              for (const file of site === 'bench' ? ['index.html', 'method.html', 'reproduce.html'] : ['index.html', 'reference.html']) {
                await page.goto(`https://${site}.invalid/${file}`);
                await page.evaluate(() => document.fonts.ready);
                if (javaScriptEnabled) {
                  const toggle = page.locator('.site-contents-toggle');
                  if (await toggle.getAttribute('aria-expanded') !== 'true') await toggle.click();
                }
                const label = `${site}/${file} ${width}px JS=${javaScriptEnabled} ${colorScheme}`;
                const check = async () => {
                  const state = await inspect(page, site);
                  assert(!state.overflow, `${label}: page overflow`);
                  assert.equal(state.padding, '24px 12px 24px 24px', label);
                  assert(state.headings.length > 1, `${label}: missing headings`);
                  for (const [i, h] of state.headings.entries()) {
                    assert.notEqual(h.display, 'inline', `${label}: ${h.text} must occupy its own row`);
                    assert.equal(h.top, site === 'bench' && i === 0 ? '0px' : '18px', `${label}: heading top margin`);
                    assert.equal(h.bottom, '4px', `${label}: heading bottom margin`);
                    assert.equal(h.font, '16px', label);
                    assert.equal(h.weight, '700', label);
                    if (i > 0) {
                      const previous = state.headings[i - 1];
                      assert(h.y >= previous.y + previous.height + 18, `${label}: overlapping heading rows`);
                    }
                  }
                  assert.deepEqual(state.clipped, [], `${label}: clipped navigation text`);
                };
                try {
                  await check();
                  if (javaScriptEnabled && width >= 620) {
                    const handle = page.getByRole('separator', { name: 'Contents width' });
                    const before = page.url();
                    for (let i = 0; i < 6; i++) await handle.press('ArrowLeft');
                    assert.equal(await handle.getAttribute('aria-valuenow'), '240');
                    await check();
                    await handle.press('Home');
                    assert.equal(page.url(), before, 'Resizing must not navigate');
                  }
                  if (process.env.SIDEBAR_SCREENSHOTS && javaScriptEnabled &&
                      [390, 1440].includes(width) && file === 'index.html') {
                    fs.mkdirSync(process.env.SIDEBAR_SCREENSHOTS, { recursive: true });
                    await page.screenshot({ path: path.join(process.env.SIDEBAR_SCREENSHOTS,
                      `${site}-${width}-${colorScheme}.png`) });
                  }
                } catch (error) { failures.push(error.message); }
              }
            }
            assert.deepEqual(errors, []);
            console.log(`Checked ${width}px JS=${javaScriptEnabled} ${colorScheme}`);
          } finally { await context.close(); }
        }
      }
    }
    assert.deepEqual(failures, [], 'Sidebar layout regressions');
  } finally { await browser.close(); }
})().catch(error => { console.error(error); process.exitCode = 1; });
