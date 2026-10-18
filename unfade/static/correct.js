// Correction previews: blinking clipping marks, clipping shares, histogram and live slider preview.

// Must match apply() in unfade/color_methods.py: levels per channel, warm/cool gains, clip,
// then the strength mixes the result with the original pixel. Pure, so
// tests/test_preview_parity.py runs this very function under Node.
function adjustedPixels(corrected, original, a) {
  const out = new Uint8ClampedArray(corrected.data);
  const low = a.black / 255;
  const high = 1 - a.white / 255;
  const gains = [1 + a.temperature / 100, 1, 1 - a.temperature / 100];
  const k = a.strength / 100;
  const c = corrected.data;
  const o = original.data;
  for (let i = 0; i < c.length; i += 4) {
    for (let ch = 0; ch < 3; ch++) {
      let v = (c[i + ch] / 255 - low) / (high - low) * gains[ch];
      v = Math.min(1, Math.max(0, v));
      const base = o[i + ch] / 255;
      out[i + ch] = Math.round((base + k * (v - base)) * 255);
    }
  }
  return out;
}
if (typeof module === 'object' && module.exports) module.exports = {adjustedPixels};

(() => {
  if (typeof document === 'undefined') return;
  const t = (text, values = {}) => ((window.unfadeText || {})[text] || text).replace(/\{(\w+)\}/g, (_, key) => values[key]);
  // Finnish writes decimals with a comma.
  const decimal = (text) => document.documentElement.lang === 'fi' ? text.replace('.', ',') : text;
  const showOriginal = document.getElementById('show-original');
  const showClipping = document.getElementById('show-clipping');
  if (!showClipping) return;
  const reduceMotion = window.matchMedia('(prefers-reduced-motion: reduce)');
  const images = new Map();
  const views = [];

  // A preview that fails to load (a dropped connection, a source still being fetched) is tried
  // again after 1 s and 3 s before the card says it is missing.
  function load(url) {
    if (!images.has(url)) {
      images.set(url, new Promise((resolve, reject) => {
        const attempt = (left, wait) => {
          const image = new Image();
          image.onload = () => resolve(image);
          image.onerror = () => left > 0
            ? setTimeout(() => attempt(left - 1, wait * 3), wait)
            : reject(new Error(`preview ${url} did not load`));
          image.src = left === 2 ? url : `${url}${url.includes('?') ? '&' : '?'}retry=${2 - left}`;
        };
        attempt(2, 1000);
      }));
    }
    return images.get(url);
  }

  function pixels(image) {
    const canvas = document.createElement('canvas');
    canvas.width = image.naturalWidth;
    canvas.height = image.naturalHeight;
    const context = canvas.getContext('2d', {willReadFrequently: true});
    context.drawImage(image, 0, 0);
    return context.getImageData(0, 0, canvas.width, canvas.height);
  }

  function render(view) {
    if (!view.corrected || !view.original) return;
    const {width, height} = view.corrected;
    const source = showOriginal.checked
      ? view.original.data
      : view.adjustments ? adjustedPixels(view.corrected, view.original, view.adjustments()) : view.corrected.data;
    const plain = new ImageData(new Uint8ClampedArray(source), width, height);
    const marked = new ImageData(new Uint8ClampedArray(source), width, height);
    const bins = [new Array(64).fill(0), new Array(64).fill(0), new Array(64).fill(0)];
    const x0 = Math.floor(width / 10), x1 = Math.floor(width * 9 / 10);
    const y0 = Math.floor(height / 10), y1 = Math.floor(height * 9 / 10);
    const mark = showClipping.checked && !showOriginal.checked;
    let high = 0, low = 0, counted = 0, anyMark = false;
    const p = marked.data;
    for (let y = 0; y < height; y++) {
      for (let x = 0; x < width; x++) {
        const i = (y * width + x) * 4;
        const isHigh = p[i] >= 254 || p[i + 1] >= 254 || p[i + 2] >= 254;
        const isLow = p[i] <= 1 && p[i + 1] <= 1 && p[i + 2] <= 1;
        if (y >= y0 && y < y1 && x >= x0 && x < x1) {
          counted++;
          if (isHigh) high++; else if (isLow) low++;
          for (let ch = 0; ch < 3; ch++) bins[ch][p[i + ch] >> 2]++;
        }
        if (mark && (isHigh || isLow)) {
          anyMark = true;
          p[i] = isHigh ? 255 : 0; p[i + 1] = isHigh ? 0 : 255; p[i + 2] = 255;
        }
      }
    }
    view.canvas.width = width;
    view.canvas.height = height;
    view.frames = anyMark ? [plain, marked] : [plain];
    view.phase = view.frames.length - 1;
    view.canvas.getContext('2d').putImageData(view.frames[view.phase], 0, 0);
    const share = (n) => Math.round(n / Math.max(counted, 1) * 1000) / 10;
    view.onStats({high: share(high), low: share(low), bins});
  }

  const formatShare = (value) => `${decimal(String(value))} %`;

  function attach(canvas, onStats, adjustments) {
    const view = {canvas, onStats, adjustments};
    views.push(view);
    Promise.all([load(canvas.dataset.src), load(canvas.dataset.original)]).then(([corrected, original]) => {
      view.corrected = pixels(corrected);
      view.original = pixels(original);
      render(view);
    }).catch(() => onStats(null));
    return view;
  }

  document.querySelectorAll('.correction-card .clip-canvas').forEach(canvas => {
    const badge = canvas.closest('.correction-card').querySelector('.clip-badge');
    attach(canvas, stats => {
      if (!stats) { badge.textContent = t('preview missing'); return; }
      const total = Math.round((stats.high + stats.low) * 10) / 10;
      badge.textContent = showOriginal.checked ? t('original') : `${total > 1 ? '⚠ ' : ''}${t('clips {share}', {share: formatShare(total)})}`;
      badge.classList.toggle('warn', total > 1 && !showOriginal.checked);
    });
  });

  const detail = document.getElementById('detail-canvas');
  const form = document.getElementById('adjust-form');
  if (detail && form) {
    const sliders = [...form.querySelectorAll('input[type=range]')];
    const values = () => Object.fromEntries(sliders.map(input => [input.name, Number(input.value)]));
    const view = attach(detail, stats => {
      if (!stats) return;
      document.getElementById('clip-high').textContent = formatShare(stats.high);
      document.getElementById('clip-low').textContent = formatShare(stats.low);
      document.querySelectorAll('.histogram polyline').forEach(line => {
        const counts = stats.bins[Number(line.dataset.channel)];
        const top = Math.max(1, ...counts);
        line.setAttribute('points', counts.map((n, j) => `${j * 4 + 2},${(80 - n / top * 76).toFixed(1)}`).join(' '));
      });
    }, values);
    let pending = false;
    sliders.forEach(input => input.addEventListener('input', () => {
      const output = form.querySelector(`output[for="${input.id}"]`);
      output.textContent = input.name === 'strength' ? `${input.value} %` : input.value;
      if (!pending) {
        pending = true;
        requestAnimationFrame(() => { pending = false; render(view); });
      }
    }));
  }

  [showOriginal, showClipping].forEach(toggle => toggle.addEventListener('change', () => views.forEach(render)));

  // Alternate plain and marked frames so clipped areas blink; steady marks when motion is reduced.
  setInterval(() => {
    views.forEach(view => {
      if (!view.frames || view.frames.length < 2) return;
      view.phase = reduceMotion.matches ? 1 : 1 - view.phase;
      view.canvas.getContext('2d').putImageData(view.frames[view.phase], 0, 0);
    });
  }, 500);

  // ΔE per method is fitted on the server for the whole group, so it is fetched after load.
  const deltaSource = document.querySelector('[data-delta-e-url]');
  if (deltaSource) {
    const table = document.getElementById('delta-e-table');
    const fill = (attribute, values) => document.querySelectorAll(`[${attribute}]`).forEach(cell => {
      const value = values ? values[cell.getAttribute(attribute)] : undefined;
      cell.textContent = typeof value === 'number' ? decimal(value.toFixed(1)) : '–';
    });
    const show = (data) => {
      const average = data && data.average;
      fill('data-delta-e-average', average && average.original);
      const reading = data && table ? data.items[table.dataset.itemId] : null;
      fill('data-delta-e-original', reading && reading.original);
      fill('data-delta-e-pair', reading && reading.pairs);
    };
    fetch(deltaSource.dataset.deltaEUrl)
      .then(response => response.ok ? response.json() : null)
      .then(show, () => show(null));
  }

  document.querySelectorAll('[data-auto-submit]').forEach(input => input.addEventListener('change', () => input.form.requestSubmit()));
})();
