// Progressive enhancement only: anchors and the report still work without JS.
(() => {
  const entries = [...document.querySelectorAll('.nav-item[href^="#"]')]
    .map(link => ({link, section: document.getElementById(link.getAttribute('href').slice(1))}))
    .filter(entry => entry.section);
  if (!entries.length) return;

  let pending = false;
  function update() {
    pending = false;
    const readingLine = Math.min(160, window.innerHeight / 4);
    let current = null;
    for (const entry of entries) {
      if (entry.section.getBoundingClientRect().top <= readingLine) current = entry;
    }
    for (const entry of entries) {
      if (entry === current) entry.link.setAttribute('aria-current', 'location');
      else entry.link.removeAttribute('aria-current');
    }
  }
  function schedule() {
    if (pending) return;
    pending = true;
    requestAnimationFrame(update);
  }
  window.addEventListener('scroll', schedule, {passive: true});
  window.addEventListener('resize', schedule);
  window.addEventListener('hashchange', schedule);
  window.addEventListener('pageshow', schedule);
  document.addEventListener('toggle', schedule, true);
  new ResizeObserver(schedule).observe(document.querySelector('main'));
  document.fonts.ready.then(schedule);
  schedule();
})();

// A wide hover/focus target also makes the measurements available by keyboard or tap.
(() => {
  const tracks = [...document.querySelectorAll('button.track')];
  function position(track) {
    const box = track.getBoundingClientRect();
    const tip = track.querySelector('.measurements');
    tip.style.display = 'block';
    const left = Math.max(16, Math.min(box.left, innerWidth - tip.offsetWidth - 16));
    track.style.setProperty('--tip-left', `${left - box.left}px`);
    tip.style.display = '';
  }
  for (const track of tracks) {
    for (const event of ['pointerenter', 'focus', 'click']) {
      track.addEventListener(event, () => {
        track.classList.remove('dismissed');
        position(track);
      });
    }
  }
  document.addEventListener('keydown', event => {
    if (event.key === 'Escape') tracks.forEach(track => track.classList.add('dismissed'));
  });
  window.addEventListener('resize', () => tracks.forEach(position));
})();
