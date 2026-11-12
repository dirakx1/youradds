import { auth } from './auth.js?v=brand-10';
import { t, L, lang, locale, appPath, startI18n } from './i18n.js?v=brand-10';

const authPollers = new Set();

const $ = (s, el = document) => el.querySelector(s);
let currency = 'COP';   // overwritten from /api/health at boot
const money = n => { n = Number(n || 0); try { return new Intl.NumberFormat(locale, { style: 'currency', currency, currencyDisplay: 'narrowSymbol', maximumFractionDigits: currency === 'COP' ? 0 : 2 }).format(n); } catch { return '$' + n.toLocaleString(locale); } };
const esc = s => String(s ?? '').replace(/[&<>"']/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
const pill = p => `<span class="pill ${esc(p)}">${esc(p)}</span>`;

let brandCache = null;   // the saved brand ("Mi marca"), or null until the account has completed one
const brandText = {
  products: v => (v || []).map(p => [p.name, p.description, p.price].join(' | ').replace(/( \| )+$/, '')).join('\n'),
  reviews: v => (v || []).map(r => `${r.name}: ${r.text}`).join('\n'),
  palette: v => (v || []).join(', '),
};
const brandParse = {
  products: s => s.split('\n').map(l => l.split('|').map(x => x.trim())).filter(p => p[0]).map(p => ({ name: p[0], description: p[1] || '', price: p[2] || '' })),
  reviews: s => s.split('\n').map(l => { const i = l.indexOf(':'); return i > 0 ? { name: l.slice(0, i).trim(), text: l.slice(i + 1).trim() } : null; }).filter(r => r && r.text),
  palette: s => (s.match(/#[0-9a-fA-F]{6}/g) || []).slice(0, 3),
};

/** Brand colour picker: up to three colours, each with the native colour chooser and a hex box. Edits ``cols`` in place.
 *  Colour changes update the swatch and preview without re-rendering, so the browser's chooser stays open. */
function palettePicker(box, cols) {
  const DEFAULTS = ['#7a4a2b', '#f3e9dc', '#d9822b'], HEX = /^#[0-9a-fA-F]{6}$/;
  const preview = () => cols.length
    ? `<div class="pal-preview" style="background:linear-gradient(135deg,${esc(cols[0])},${esc(cols[0])} 55%,color-mix(in srgb,${esc(cols[0])} 35%,#000))"><b>${t('Your brand')}</b><span style="background:${esc(cols[2] || cols[1] || '#ffcd5a')}">${t('Buy now')}</span></div>`
    : `<p class="muted">${t('Pick at least one color, or skip this step.')}</p>`;
  const draw = () => {
    box.innerHTML = `<div class="pal-row">${cols.map((c, n) => `<div class="swatch">
        <input type="color" value="${esc(c)}" data-c="${n}" aria-label="${t('Color')} ${n + 1}">
        <input class="hex" value="${esc(c)}" maxlength="7" data-h="${n}" spellcheck="false" aria-label="${t('Color')} ${n + 1} hex">
        <button type="button" data-rm="${n}" aria-label="${t('Remove')}">✕</button></div>`).join('')}
      ${cols.length < 3 ? `<button type="button" class="addcol">+ ${t('Add color')}</button>` : ''}</div><div id="pal-prev">${preview()}</div>`;
  };
  box.oninput = e => {
    const n = e.target.dataset.c; if (n === undefined) return;
    cols[n] = e.target.value; $(`[data-h="${n}"]`, box).value = e.target.value; $('#pal-prev', box).innerHTML = preview();
  };
  box.onchange = e => {
    const n = e.target.dataset.h; if (n === undefined) return;
    let v = e.target.value.trim(); if (v && v[0] !== '#') v = '#' + v;
    if (HEX.test(v)) { cols[n] = v.toLowerCase(); $(`[data-c="${n}"]`, box).value = cols[n]; $('#pal-prev', box).innerHTML = preview(); }
    e.target.value = cols[n];
  };
  box.onclick = e => {
    const rm = e.target.closest('[data-rm]'); if (rm) { cols.splice(+rm.dataset.rm, 1); draw(); return; }
    if (e.target.closest('.addcol')) { cols.push(DEFAULTS.find(d => !cols.includes(d)) || DEFAULTS[cols.length]); draw(); }
  };
  draw();
}

/** Forms that used to ask for product / audience / tone / language / theme read them from the brand instead:
 *  hide and disable those fields (disabled fields are not submitted, so the server fills them) and make the product a list. */
function applyBrand(form) {
  const b = brandCache; if (!form || !b || !b.completed) return;
  for (const n of ['description', 'audience', 'tone', 'language', 'theme', 'price']) {
    const f = form.querySelector(`[name=${n}]`); if (!f) continue;
    f.disabled = true; const l = f.closest('label'); if (l) l.hidden = true;
  }
  const p = form.querySelector('[name=product]');
  if (p) { const sel = document.createElement('select'); sel.name = 'product'; sel.innerHTML = b.products.map(x => `<option>${esc(x.name)}`).join(''); p.replaceWith(sel); }
  const note = document.createElement('p'); note.className = 'muted'; note.style.gridColumn = '1/-1';
  note.innerHTML = `${t('Using your brand')}: <b>${esc(b.name)}</b> · <a href="#brand">${t('Edit')}</a>`;
  form.prepend(note);
}

async function api(path, opts = {}) {
  const token = await auth.token();
  if (!token && path !== '/auth/config') { showAuth(); throw new Error(t('Please sign in.')); }
  const headers = new Headers(opts.headers || {});
  if (!headers.has('Content-Type') && opts.body) headers.set('Content-Type', 'application/json');
  if (token) headers.set('Authorization', `Bearer ${token}`);
  const r = await fetch('/api' + path, {
    ...opts, headers,
    body: opts.body ? JSON.stringify(opts.body) : undefined,
  });
  if (r.status === 401 && !path.startsWith('/auth/')) { showAuth(); throw new Error(t('Your session ended. Please sign in again.')); }
  if (!r.ok) {
    const d = (await r.json().catch(() => ({}))).detail;
    throw new Error(t(Array.isArray(d) ? d.map(x => x.msg).join('; ') : d || r.statusText));
  }
  return r.status === 204 ? null : r.json();
}

function lineChart(data, keys, colors) {
  const W = 700, H = 200, P = 30;
  const max = Math.max(1, ...data.flatMap(d => keys.map(k => d[k] || 0)));
  const x = i => P + i * (W - P - 10) / Math.max(1, data.length - 1);
  const y = v => H - P - v / max * (H - P - 10);
  const lines = keys.map((k, j) =>
    `<polyline fill="none" stroke="${colors[j]}" stroke-width="2" points="${data.map((d, i) => `${x(i)},${y(d[k] || 0)}`).join(' ')}"/>`).join('');
  const labels = data.map((d, i) => i % 3 === 0 ? `<text x="${x(i)}" y="${H - 8}" text-anchor="middle">${d.day.slice(5)}</text>` : '').join('');
  return `<svg viewBox="0 0 ${W} ${H}" width="100%"><text x="2" y="12">${money(max)}</text>${lines}${labels}</svg>
    <div class="muted">${keys.map((k, j) => `<span style="color:${colors[j]}">●</span> ${k}`).join(' &nbsp; ')}</div>`;
}


// "Remove background" button next to a product-image input: replaces the chosen file with a transparent PNG.
async function cutoutButton(form) {
  const input = form.querySelector('input[name=img]'); if (!input) return;
  const wrap = document.createElement('div'); wrap.style.marginTop = '6px';
  wrap.innerHTML = '<button type="button" disabled>Remove background</button> <span class="muted"></span><div class="cutprev"></div>';
  input.after(wrap);
  const btn = $('button', wrap), note = $('span', wrap), prev = $('.cutprev', wrap);
  let ok = false;
  try { ok = (await api('/tools/cutout/status')).available; } catch { }
  if (!ok) { note.textContent = t('Not installed (pip install rembg onnxruntime opencv-python-headless numpy)'); return; }
  btn.disabled = false;
  const toDataURL = f => new Promise((res, rej) => { const r = new FileReader(); r.onload = () => res(r.result); r.onerror = rej; r.readAsDataURL(f); });
  btn.onclick = async () => {
    const f = input.files[0]; if (!f) return note.textContent = t('Choose an image first');
    if (f.size > 8e6) return note.textContent = t('Image over 8MB');
    btn.disabled = true; note.textContent = t('Removing background… (the first run downloads a ~180MB model, can take a minute)');
    try {
      const r = await api('/tools/cutout', { method: 'POST', body: { image: await toDataURL(f) } });
      const blob = await (await fetch(r.image)).blob();
      const dt = new DataTransfer(); dt.items.add(new File([blob], 'product.png', { type: 'image/png' })); input.files = dt.files;
      prev.innerHTML = `<img src="${r.image}" alt="" style="max-height:120px;margin-top:6px;border-radius:6px;background:repeating-conic-gradient(#8884 0 25%,transparent 0 50%) 0 0/14px 14px">`;
      note.textContent = t('Done ({w}×{h}) – this transparent PNG will be used.', { w: r.width, h: r.height });
    } catch (x) { note.textContent = x.message; }
    btn.disabled = false;
  };
}

async function creativesPanel(el, refresh) {
    const [opt, list, camps] = await Promise.all([api('/creatives/options'), api('/creatives'), api('/campaigns')]);
    const label = { meta: 'Meta', tiktok: 'TikTok', google: 'Google' };
    const gallery = items => items.map(c => `<div class="card" style="margin:0"><img src="/api/creatives/${c.id}/image" alt="" loading="lazy" style="width:100%;max-height:340px;object-fit:contain;border-radius:8px;background:#000">
      <p style="margin:8px 0 4px">${pill(c.platform)} <span class="muted">${esc(c.format)} · ${c.width}×${c.height}</span></p>
      ${c.link ? `<p style="margin:0 0 6px"><a class="cta-link" href="${esc(c.link)}" target="_blank" rel="noopener">${esc(c.cta || t('Open link'))} →</a> <span class="muted">${esc(c.link.replace(/^https?:\/\//, ''))}</span></p>` : ''}
      <div style="display:flex;gap:6px;flex-wrap:wrap"><a href="/api/creatives/${c.id}/image?download=true"><button type="button">Download</button></a>
      <select data-attach="${c.id}"><option value="">Attach to campaign…</option>${camps.filter(k => k.platform === c.platform).map(k => `<option value="${k.id}" ${k.id === c.campaign_id ? 'selected' : ''}>${esc(k.name)}</option>`).join('')}</select>
      <button type="button" data-del="${c.id}">✕</button></div></div>`).join('');
    el.innerHTML = `<div id="out" class="grid" style="grid-template-columns:repeat(auto-fill,minmax(240px,1fr))">${gallery(list) || `<p class="muted">${t('No creatives yet. Build a plan above and they appear here.')}</p>`}</div>
      <details class="card adv"><summary>${t('Create a creative manually (advanced)')}</summary><form id="cf" class="row">
        <label>Product name<input name="product" required maxlength="80"></label>
        <label>Description (facts only)<input name="description" maxlength="500" placeholder="What it is, flavor, size, materials…"></label>
        <label>Audience<input name="audience" maxlength="120"></label>
        <label>Language<select name="language">${Object.entries(opt.languages).map(([k, v]) => `<option value="${k}">${v}`).join('')}</select></label>
        <label>Tone<select name="tone"><option>friendly<option>bold<option>professional<option>playful<option>urgent</select></label>
        <label>Theme<select name="theme">${opt.themes.map(t => `<option>${t}`).join('')}</select></label>
        <label>${t('Button link: web page or WhatsApp number')}<input name="link" maxlength="300" placeholder="https://mitienda.co  ·  3001234567"></label>
        <label>Price badge (optional)<input name="price" maxlength="20" placeholder="$119.900 COP"></label>
        <label>Rating line (optional, must be true)<input name="social_proof" maxlength="50" placeholder="4.8/5 from 1,200 reviews"></label>
        <label>Product image (PNG, transparent bg works best)<input type="file" name="img" accept="image/png,image/jpeg,image/webp"></label>
        <label style="grid-column:1/-1">Real customer testimonials (optional, one per line: <code>Name: quote</code>)<textarea name="tests" rows="2" maxlength="600"></textarea></label>
        <fieldset style="grid-column:1/-1;border:0;padding:0;margin:0"><span class="muted">Platforms</span><br>
          ${Object.keys(opt.formats).map(p => `<label style="display:inline-block;margin-right:16px;width:auto"><input type="checkbox" name="pf" value="${p}" checked style="display:inline;width:auto"> ${label[p]} <span class="muted">(${Object.values(opt.formats[p]).map(s => s.join('×')).join(', ')})</span></label>`).join('')}</fieldset>
        <label style="grid-column:1/-1">${opt.scene_available ? `<input type="checkbox" name="scene" style="display:inline;width:auto"> AI scene backgrounds <span class="muted">(Higgsfield · a few cents per image, capped at $${opt.scene_cost_cap_usd}/run)</span>` : `<span class="muted">${t('AI scene backgrounds: add your Higgsfield API key on the <a href="#connections">Connections</a> page to enable')}</span>`}
          <input name="scene_hint" maxlength="200" placeholder="Scene idea, e.g. cozy bedroom at night (don't name the product)"></label>
        <button class="pri">Generate creatives</button></form>
        <p class="muted" style="margin:10px 0 0">Reviews, ratings and customer counts are never invented — they only appear if you enter them. 9:16 formats keep text inside platform safe zones.</p>
        <div id="cerr" class="toast"></div></details>`;
    cutoutButton($('#cf', el)); applyBrand($('#cf', el));
    const file = f => new Promise((res, rej) => { const r = new FileReader(); r.onload = () => res(r.result); r.onerror = rej; r.readAsDataURL(f); });
    $('#cf', el).onsubmit = async e => {
      e.preventDefault(); const fd = new FormData(e.target), btn = $('button.pri', e.target); $('#cerr', el).textContent = '';
      const platforms = fd.getAll('pf'); if (!platforms.length) return $('#cerr', el).textContent = t('Pick at least one platform');
      const testimonials = (fd.get('tests') || '').split('\n').map(l => l.trim()).filter(l => l.includes(':'))
        .map(l => ({ name: l.slice(0, l.indexOf(':')).trim(), text: l.slice(l.indexOf(':') + 1).trim() }));
      const body = { platforms, testimonials };
      for (const k of ['product', 'description', 'audience', 'language', 'tone', 'theme', 'price', 'social_proof', 'link']) body[k] = fd.get(k) || '';
      const f = fd.get('img'); if (f && f.size) { if (f.size > 8e6) return $('#cerr', el).textContent = t('Image over 8MB'); body.product_image = await file(f); }
      body.scene = fd.get('scene') === 'on'; body.scene_hint = fd.get('scene_hint') || '';
      btn.disabled = true; btn.textContent = t('Generating…');
      try { const r = await api('/creatives/generate', { method: 'POST', body }); if (r.warnings.length || r.scene_cost_usd) alert((r.scene_cost_usd ? t('Scene cost: ${c}', { c: r.scene_cost_usd }) + '\\n' : '') + r.warnings.join('\\n')); refresh(); }
      catch (x) { $('#cerr', el).textContent = x.message; btn.disabled = false; btn.textContent = t('Generate creatives'); }
    };
    el.onchange = async e => {
      const id = e.target.dataset.attach; if (!id || !e.target.value) return;
      await api(`/creatives/${id}/attach`, { method: 'POST', body: { campaign_id: +e.target.value } }); alert(t('Attached – campaign text updated'));
    };
    el.onclick = async e => {
      const b = e.target.closest('button[data-del]'); if (!b || !confirm(t('Delete creative?'))) return;
      await api('/creatives/' + b.dataset.del, { method: 'DELETE' }); refresh();
    };
}

async function copyPanel(el) {
    const { languages } = await api('/creatives/options');
    el.innerHTML = `<details class="card adv"><summary>${t('Write ad copy (advanced)')}</summary><form class="row" id="sf">
        <label>Platform<select name="platform"><option value="meta">Meta<option value="tiktok">TikTok<option value="google">Google</select></label>
        <label>Product<input name="product" required placeholder="Organic running shoes"></label>
        <label>Audience<input name="audience" value="runners 25-45"></label>
        <label>Tone<select name="tone"><option>friendly<option>bold<option>professional<option>playful<option>urgent</select></label>
        <label>Language<select name="language">${Object.entries(languages).map(([k, v]) => `<option value="${k}">${v}`).join('')}</select></label>
        <button class="pri">Generate</button></form><div id="sout"></div></details>`;
    applyBrand($('#sf', el));
    $('#sf', el).onsubmit = async e => {
      e.preventDefault(); const btn = $('button', e.target); btn.disabled = true;
      try {
        const r = await api('/ai/copy', { method: 'POST', body: Object.fromEntries(new FormData(e.target)) });
        $('#sout', el).innerHTML = (r.llm ? '' : '<p class="muted">Template mode – set ANTHROPIC_API_KEY for LLM copy.</p>') +
          r.variants.map(v => `<div class="card"><span class="muted">${esc(v.angle)}</span><h3>${esc(v.headline)}</h3><p>${esc(v.body)}</p><b>[${esc(v.cta)}]</b></div>`).join('');
      } catch (x) { $('#sout', el).innerHTML = `<p class="toast">${esc(x.message)}</p>`; }
      btn.disabled = false;
    };
}

async function goalPlanner(box, onCreated) {
    const GOALS = [['awareness', '👋', 'I want people to know me', 'Reach new people who could like your brand.'],
      ['sales', '🛒', 'I want people to buy', 'Get new customers and remind recent visitors.'],
      ['recover', '🔁', 'Win back those who did not buy', 'Talk again to people who showed interest and left.']];
    const OBJ = { awareness: 'Be known', traffic: 'Visits to your page', sales: 'Sales', leads: 'Contacts', whatsapp: 'WhatsApp chats' };
    const prods = brandCache && brandCache.completed ? brandCache.products.map(x => x.name) : [];
  box.innerHTML = `<div class="card"><h3 style="margin-top:0">${t('What do you want to achieve?')}</h3>
        <p class="muted">${t('Pick a goal and the app builds the campaign structure for you. You review it before anything is created.')}</p>
        <form id="goalf" class="stack">
          <div class="goals">${GOALS.map(([k, ic, ti, de], i) => `<label class="goal"><input type="radio" name="goal" value="${k}"${i === 1 ? ' checked' : ''}><span class="goal-ic">${ic}</span><b>${t(ti)}</b><small class="muted">${t(de)}</small></label>`).join('')}</div>
          <div class="row">
            <label>${t('Platform')}<select name="platform"><option value="meta">Meta (Facebook + Instagram)<option value="tiktok">TikTok<option value="google">Google</select></label>
            <label>${t('Product')}${prods.length ? `<select name="product">${prods.map(n => `<option>${esc(n)}`).join('')}</select>` : `<input name="product" required>`}</label>
            <label>${t('Total daily budget')} (${currency})<input name="daily_budget" type="number" min="1" step="any" value="${currency === 'COP' ? 50000 : 20}"></label>
            <label>${t('Page, store or WhatsApp number (for the button)')}<input name="landing_url" placeholder="https://  ·  3001234567"></label>
          </div>
          <button class="pri">${t('Build my plan')}</button> <span class="muted msgline" id="goalnote"></span></form>
        <div id="plan"></div></div>`;
    const gf = $('#goalf', box), plan = $('#plan', box), gnote = $('#goalnote', box);
    gf.onsubmit = async e => {
      e.preventDefault(); gnote.className = 'muted msgline'; gnote.textContent = t('Building your plan…'); plan.innerHTML = '';
      const v = Object.fromEntries(new FormData(gf)); v.daily_budget = +v.daily_budget;
      let r; try { r = await api('/campaigns/plan', { method: 'POST', body: v }); } catch (x) { gnote.className = 'toast msgline'; gnote.textContent = x.message; return; }
      gnote.textContent = '';
      plan.innerHTML = `<h3>${t('Your plan')}</h3><p>${esc(t(r.summary))}</p>
        <div class="plan">${r.campaigns.map(c => `<div class="plan-item"><b>${esc(c.name)}</b>
          <div class="muted">${esc(t(OBJ[c.objective] || c.objective))} · ${money(c.daily_budget)} ${t('per day')}</div>
          <div>${t('Who sees it')}: ${esc(c.audience)}</div><div>${t('Ad')}: “${esc(c.headline)}”</div><small class="muted">${esc(t(c.why))}</small></div>`).join('')}</div>
        <p class="muted">${t('Total')}: ${money(r.campaigns.reduce((a, c) => a + c.daily_budget, 0))} ${t('per day')}</p>
        <div class="plan-actions"><button class="pri" id="mkplan">${t('Create these campaigns')}</button><span class="muted msgline" id="plannote"></span></div>`;
      $('#mkplan', box).onclick = async ev => {
        ev.target.disabled = true; const pn = $('#plannote', box); let sync = '';
        try { for (const c of r.campaigns) { const { why, ...body } = c; const x = await api('/campaigns', { method: 'POST', body }); if (x.sync_error) sync = x.sync_error; } }
        catch (x) { pn.className = 'toast msgline'; pn.textContent = x.message; ev.target.disabled = false; return; }
        if (sync) alert(t('Saved locally; platform sync failed: {e}', { e: sync }));
        pn.textContent = t('Campaigns created. Making your creatives…');
        await onCreated(r, v);
      };
    };
}

async function videosPanel(el) {
    const opt = await api('/creatives/options');
    // one checkbox per output; every aspect ratio is a separate (billed) generation, so only 9:16 is pre-selected
    const outputs = [['meta:reel', 'Meta Reel (9:16)', true], ['meta:feed_square', 'Meta Feed (1:1)', false],
      ['tiktok:vertical', 'TikTok (9:16)', true], ['google:youtube_landscape', 'Google / YouTube (16:9)', false]];
    let timer = null;
    const mins = s => s == null ? '' : s < 60 ? `${s}s` : `${Math.floor(s / 60)}m ${s % 60}s`;
    const stateText = v => v.status === 'failed' ? '⚠ ' + esc(t(v.error || 'failed'))
      : v.status === 'compositing' ? t('Adding your product, text and CTA…')
      : v.provider_status === 'in_progress' ? t('Rendering at Higgsfield… {t} (usually a few minutes)', { t: mins(v.elapsed_s) })
      : v.provider_status === 'queued' ? t('Waiting in Higgsfield\'s queue… {t}', { t: mins(v.elapsed_s) })
      : t('Starting… {t}', { t: mins(v.elapsed_s) });
    const render = list => {
      $('#vlist', el).innerHTML = list.map(v => `<div class="card" style="margin:0">
        ${v.status === 'done' ? `<video src="/api/videos/${v.id}/file" controls preload="metadata" style="width:100%;max-height:340px;border-radius:8px;background:#000"></video>`
          : `<div style="height:200px;display:grid;place-items:center;background:#000;border-radius:8px;color:#aaa;padding:10px;text-align:center">${stateText(v)}</div>`}
        <p style="margin:8px 0 4px">${pill(v.platform)} <span class="muted">${esc(v.format)} · ${v.width}×${v.height} · ${v.duration}s · ~$${(v.cost_usd || 0).toFixed(2)}</span></p>
        <div style="display:flex;gap:6px;flex-wrap:wrap">${v.status === 'done' ? `<a href="/api/videos/${v.id}/file?download=true"><button type="button">Download</button></a>` : ''}
        ${v.resumable ? `<button type="button" data-resume="${v.id}">Check again</button>` : ''}
        ${v.status === 'generating' && v.provider_status === 'queued' ? `<button type="button" data-cancel="${v.id}">Cancel (refund)</button>` : ''}
        <button type="button" data-del="${v.id}">✕</button></div>
        ${v.request_id && v.status !== 'done' ? `<p class="muted" style="font-size:11px;margin:6px 0 0">${esc(t('Request {id}', { id: v.request_id }))}</p>` : ''}</div>`).join('') || '<p class="muted">No videos yet.</p>';
    };
    const refresh = async () => {
      if (location.hash !== '#studio' || !el.isConnected) { if (timer) { clearInterval(timer); authPollers.delete(timer); timer = null; } return; }
      const list = await api('/videos'); render(list);
      if (!list.some(v => ['queued', 'generating', 'compositing'].includes(v.status))) { if (timer) { clearInterval(timer); authPollers.delete(timer); timer = null; } }
    };
    const poll = () => { if (timer) { clearInterval(timer); authPollers.delete(timer); } timer = setInterval(refresh, 4000); authPollers.add(timer); };
    el.innerHTML = `<div id="vlist" class="grid" style="grid-template-columns:repeat(auto-fill,minmax(240px,1fr))"></div>
      <details class="card adv" id="vdet"><summary>${t('Make a video (AI background + your product, text and CTA)')}</summary>
      ${opt.scene_available ? '' : `<p class="toast">${t('Add your Higgsfield API key on the <a href="#connections">Connections</a> page to enable video.')}</p>`}
      <form id="vf" class="row">
        <label>Product name<input name="product" required maxlength="80"></label>
        <label>Description (facts only)<input name="description" maxlength="500"></label>
        <label>Audience<input name="audience" maxlength="120"></label>
        <label>Scene idea (don't name the product)<input name="scene_hint" maxlength="200" placeholder="cozy bedroom at night"></label>
        <label>Language<select name="language">${Object.entries(opt.languages).map(([k, v]) => `<option value="${k}">${v}`).join('')}</select></label>
        <label>Theme<select name="theme">${opt.themes.map(t => `<option>${t}`).join('')}</select></label>
        <label>Length<select name="duration"><option>4<option selected>5<option>8<option>10<option>15</select></label>
        <label>Quality<select name="resolution"><option value="480p">Draft 480p (~$0.21/s)<option value="720p" selected>Standard 720p (~$0.46/s)</select></label>
        <label>${t('Button link: web page or WhatsApp number')}<input name="link" maxlength="300" placeholder="https://mitienda.co  ·  3001234567"></label>
        <label>Price badge (optional)<input name="price" maxlength="20"></label>
        <label>Rating line (optional, must be true)<input name="social_proof" maxlength="50"></label>
        <label>Product image (PNG, transparent bg)<input type="file" name="img" accept="image/png,image/jpeg,image/webp"></label>
        <fieldset style="grid-column:1/-1;border:0;padding:0;margin:0"><span class="muted">Platforms</span><br>
          ${outputs.map(([v, text, on]) => `<label style="display:inline-block;margin-right:16px;width:auto"><input type="checkbox" name="pf" value="${v}" ${on ? 'checked' : ''} style="display:inline;width:auto"> ${text}</label>`).join('')}
          <br><span class="muted">9:16 serves Meta Reels and TikTok from one clip. Every extra aspect ratio is another generation: slower and billed.</span></fieldset>
        <button class="pri" ${opt.scene_available ? '' : 'disabled'}>Estimate &amp; generate</button></form>
        <p class="muted" style="margin:10px 0 0">One background clip is generated per aspect ratio and shared across platforms. Your real product photo is composited on top, never redrawn. No audio. You'll see the estimated cost and confirm before anything is billed.</p>
        <div id="verr" class="toast"></div></details>`;
    cutoutButton($('#vf', el)); applyBrand($('#vf', el));
    const file = f => new Promise((res, rej) => { const r = new FileReader(); r.onload = () => res(r.result); r.onerror = rej; r.readAsDataURL(f); });
    $('#vf', el).onsubmit = async e => {
      e.preventDefault(); const fd = new FormData(e.target), btn = $('button.pri', e.target); $('#verr', el).textContent = '';
      const formats = fd.getAll('pf'); if (!formats.length) return $('#verr', el).textContent = t('Pick at least one platform');
      const body = { platforms: [...new Set(formats.map(f => f.split(':')[0]))], formats, duration: +fd.get('duration') };
      for (const k of ['product', 'description', 'audience', 'language', 'theme', 'price', 'social_proof', 'scene_hint', 'resolution']) body[k] = fd.get(k) || '';
      const f = fd.get('img'); if (f && f.size) { if (f.size > 8e6) return $('#verr', el).textContent = t('Image over 8MB'); body.product_image = await file(f); }
      btn.disabled = true;
      try {
        const est = await api('/videos/estimate', { method: 'POST', body });
        if (est.total_usd > est.cap_usd) throw new Error(t('Estimated ${est} exceeds your cap of ${cap} (VIDEO_MAX_USD). Try fewer platforms, a shorter clip or Draft quality.', { est: est.total_usd.toFixed(2), cap: est.cap_usd.toFixed(2) }));
        if (!confirm(t('Estimated cost: ~${est} ({n} clip(s)). Billed to your Higgsfield balance. Generate?', { est: est.total_usd.toFixed(2), n: Object.keys(est.per_ratio).length }))) { btn.disabled = false; return; }
        await api('/videos/generate', { method: 'POST', body }); await refresh(); poll();
      } catch (x) { $('#verr', el).textContent = x.message; }
      btn.disabled = false;
    };
    el.onclick = async e => {
      const b = e.target.closest('button'); if (!b) return;
      try {
        if (b.dataset.resume) { await api(`/videos/${b.dataset.resume}/resume`, { method: 'POST' }); await refresh(); poll(); }
        else if (b.dataset.cancel) { if (!confirm(t('Cancel this clip? Higgsfield refunds it if still queued.'))) return; await api(`/videos/${b.dataset.cancel}/cancel`, { method: 'POST' }); refresh(); }
        else if (b.dataset.del && confirm(t('Delete video?'))) { await api('/videos/' + b.dataset.del, { method: 'DELETE' }); refresh(); }
      } catch (x) { alert(t(x.message)); }
    };
    await refresh(); const l = await api('/videos'); if (l.some(v => ['queued', 'generating', 'compositing'].includes(v.status))) poll();
}

const views = {
  async dashboard(el) {
    const d = await api('/dashboard'), tot = d.totals;
    const kpi = (l, v) => `<div class="card kpi"><small>${l}</small><b>${v}</b></div>`;
    const since = new Date(Date.now() - 6 * 864e5), pad = n => String(n).padStart(2, '0');
    const from = `${since.getFullYear()}-${pad(since.getMonth() + 1)}-${pad(since.getDate())}`;
    const week = d.daily.filter(r => r.day >= from).reduce((a, r) => ({ spend: a.spend + r.spend, revenue: a.revenue + r.revenue }), { spend: 0, revenue: 0 });
    const headline = week.spend || week.revenue
      ? t('This week you spent {spend} and sold {revenue}.', { spend: money(week.spend), revenue: money(week.revenue) })
      : t('Nothing spent or sold this week yet. Create a campaign to get started.');
    el.innerHTML = `<h2>Dashboard</h2>
      ${d.has_demo ? `<div class="card">${t('Showing <b>sample data</b> until you connect your own ad accounts. ')}<button id="rmdemo">Remove sample data</button></div>` : ''}
      <div class="card summary"><p class="big">${esc(headline)}</p></div>
      <div class="card"><small class="muted">${t('Spent and sold, last 14 days')}</small>${lineChart(d.daily, ['spend', 'revenue'], ['#e74c3c', '#2ecc71'])}</div>
      <details class="card adv"><summary>${t('Advanced detail (ROAS, CTR, CPA…)')}</summary>
        <div class="grid">${kpi('Spend', money(tot.spend))}${kpi('Revenue', money(tot.revenue))}${kpi('ROAS', tot.roas.toFixed(2) + 'x')}
        ${kpi('Conversions', tot.conversions)}${kpi('CTR', tot.ctr.toFixed(2) + '%')}${kpi('CPA', tot.cpa ? money(tot.cpa) : '–')}</div>
        <p class="muted">${t('ROAS: revenue per peso spent. CTR: share of people who clicked. CPA: cost per sale.')}</p>
        <div class="scroll"><table class="rtable"><thead><tr><th>Platform<th>Spend<th>Revenue<th>ROAS<th>Conversions<th>Clicks</tr></thead>
        ${d.by_platform.map(p => `<tr><td data-label="${t('Platform')}">${pill(p.platform)}<td data-label="${t('Spend')}">${money(p.spend)}<td data-label="${t('Revenue')}">${money(p.revenue)}<td data-label="ROAS">${(p.revenue / p.spend || 0).toFixed(2)}x<td data-label="${t('Conversions')}">${p.conversions}<td data-label="${t('Clicks')}">${p.clicks}`).join('')}</table></div>
      </details>`;
    const rm = $('#rmdemo', el);
    if (rm) rm.onclick = async () => { if (confirm(t('Delete the sample campaigns? Your real campaigns are kept.'))) { await api('/demo', { method: 'DELETE' }); views.dashboard(el); } };
  },

  async campaigns(el) {
    const rows = await api('/campaigns');
    el.innerHTML = `<h2>Campaigns</h2>
      <div class="card"><b>${t('Want new campaigns?')}</b> <span class="muted">${t('Pick a goal in the Studio and the app builds the campaigns and creatives for you.')}</span> <a href="#studio"><button class="pri" type="button">${t('Go to the Studio')}</button></a></div>
      <details class="card adv"><summary>${t('Create a campaign manually (advanced)')}</summary>
      <form class="row" id="f">
        <label>Platform<select name="platform"><option value="meta">Meta<option value="tiktok">TikTok<option value="google">Google</select></label>
        <label>Name<input name="name" required></label>
        <label>Objective<select name="objective"><option>sales<option>traffic<option>leads<option>awareness<option>whatsapp</select></label>
        <label>${t('Daily budget')} (${currency})<input name="daily_budget" type="number" min="1" step="any" value="${currency === 'COP' ? 50000 : 20}"></label>
        <label>Headline<input name="headline"></label>
        <label>Landing URL<input name="landing_url" type="url"></label>
        <button class="pri">Create campaign</button></form><div id="err" class="toast"></div></details>
      <div class="card scroll"><table class="rtable"><thead><tr><th>Name<th>Platform<th>Status<th>Budget/day<th>Spend 7d<th>CTR<th>CPA<th>ROAS<th></tr></thead>
      ${rows.map(c => `<tr><td data-label="${t('Name')}">${esc(c.name)}<td data-label="${t('Platform')}">${pill(c.platform)}<td data-label="${t('Status')}" class="st-${c.status}">${c.status}<td data-label="${t('Budget/day')}">${money(c.daily_budget)}
        <td data-label="${t('Spend 7d')}">${money(c.spend)}<td data-label="CTR">${c.ctr}%<td data-label="CPA">${c.cpa ? money(c.cpa) : '–'}<td data-label="ROAS">${c.roas}x
        <td data-label=""><button data-t="${c.id}" data-s="${c.status === 'active' ? 'paused' : 'active'}">${c.status === 'active' ? 'Pause' : 'Resume'}</button>
        <button data-d="${c.id}">✕</button>`).join('')}</table></div>`;
    $('#f').onsubmit = async e => {
      e.preventDefault();
      const b = Object.fromEntries(new FormData(e.target));
      b.daily_budget = +b.daily_budget;
      try { const r = await api('/campaigns', { method: 'POST', body: b }); if (r.sync_error) alert(t('Saved locally; platform sync failed: {e}', { e: r.sync_error })); views.campaigns(el); }
      catch (x) { $('#err').textContent = x.message; }
    };
    el.onclick = async e => {
      const b = e.target.closest('button'); if (!b) return;
      if (b.dataset.t) await api(`/campaigns/${b.dataset.t}/status?status=${b.dataset.s}`, { method: 'POST' });
      else if (b.dataset.d && confirm(t('Delete campaign?'))) await api('/campaigns/' + b.dataset.d, { method: 'DELETE' });
      else return;
      views.campaigns(el);
    };
  },

  async brand(el) {
    const { brand: saved, interview } = await api('/brand');
    brandCache = saved && saved.completed ? saved : brandCache;
    const state = { language: 'es', currency: 'COP', ...(saved || {}) };
    const val = f => brandText[f] ? brandText[f](state[f]) : (state[f] || '');
    const summary = () => `<div class="card"><h3>${esc(state.name)}</h3>
      <p>${esc(state.story)}</p>
      <p><b>${t('Ideal customer')}:</b> ${esc(state.ideal_customer)}<br><b>${t('Tone')}:</b> ${esc(state.tone || '–')}</p>
      <p><b>${t('Products')}:</b></p><ul>${state.products.map(p => `<li>${esc(p.name)} ${p.price ? '· ' + esc(p.price) : ''}</li>`).join('')}</ul>
      ${state.palette.length ? `<p>${state.palette.map(c => `<span style="display:inline-block;width:28px;height:28px;border-radius:6px;background:${esc(c)};margin-right:6px;vertical-align:middle" title="${esc(c)}"></span>`).join('')}</p>` : ''}
      <p class="muted">${t('Every creative, video, copy and WhatsApp reply now uses this. You never have to enter it again.')}</p>
      <button class="pri" id="go-creatives">${t('Create my first creatives')}</button> <button id="edit">${t('Edit my brand')}</button></div>`;
    const step = i => {
      const q = interview[i], last = i === interview.length - 1;
      const isPalette = q.field === 'palette';
      const cols = isPalette ? [...(state.palette || [])].slice(0, 3) : [];
      const field = isPalette
        ? `<div><b>${esc(q.question)}</b><div id="pal" class="pal"></div></div>`
        : `<label><b>${esc(q.question)}</b>
            <textarea name="a" rows="${['products', 'reviews', 'story'].includes(q.field) ? 6 : 3}" placeholder="${esc(q.hint)}">${esc(val(q.field))}</textarea></label>`;
      el.innerHTML = `<h2>${t('My brand')} <span class="muted">· ${i + 1}/${interview.length}</span></h2>
        <div class="card"><div style="height:6px;background:var(--line);border-radius:3px;margin-bottom:14px"><div style="height:6px;border-radius:3px;background:var(--acc);width:${Math.round((i + 1) / interview.length * 100)}%"></div></div>
          <form id="q" class="stack">${field}
          <div id="err" class="toast"></div>
          <div style="display:flex;gap:8px">${i ? `<button type="button" id="back">${t('Back')}</button>` : ''}<button class="pri">${last ? t('Finish') : t('Next')}</button></div></form></div>`;
      $('#back', el)?.addEventListener('click', () => step(i - 1));
      if (isPalette) palettePicker($('#pal', el), cols);
      $('#q', el).onsubmit = async e => {
        e.preventDefault();
        if (isPalette) state.palette = cols.map(c => c.toLowerCase());
        else { const raw = new FormData(e.target).get('a').trim(); state[q.field] = brandParse[q.field] ? brandParse[q.field](raw) : raw; }
        if (!last) return step(i + 1);
        try {
          const r = await api('/brand', { method: 'PUT', body: Object.fromEntries(['name', 'story', 'tone', 'language', 'currency', 'palette', 'fonts', 'ideal_customer', 'objections', 'limits', 'products', 'reviews'].map(k => [k, state[k] ?? (brandParse[k] ? [] : '')])) });
          Object.assign(state, r);
          if (!r.completed) { $('#err', el).textContent = t('Add at least your business name, one product and your ideal customer.'); return; }
          brandCache = r; done();
        } catch (x) { $('#err', el).textContent = x.message; }
      };
    };
    const done = () => {
      el.innerHTML = `<h2>${t('My brand')}</h2>${summary()}`;
      $('#edit', el).onclick = () => step(0);
      $('#go-creatives', el).onclick = () => { location.hash = '#studio'; };
    };
    saved && saved.completed ? done() : step(0);
  },

  async studio(el) {
    el.innerHTML = `<h2>Studio <span class="muted">· ${t('goal, campaigns and creatives in one place')}</span></h2>
      <div id="sp-goal"></div><h3>${t('Your creatives')}</h3><div id="sp-cr"></div><h3>${t('Your videos')}</h3><div id="sp-vid"></div><div id="sp-copy"></div>`;
    const crBox = $('#sp-cr', el), refresh = () => creativesPanel(crBox, refresh);
    await goalPlanner($('#sp-goal', el), async (plan, v) => {
      const pn = $('#plannote', el);
      try {
        const r = await api('/creatives/generate', { method: 'POST', body: { platforms: [v.platform], product: v.product, link: v.landing_url || '' } });
        pn.textContent = t('Done! Campaigns and creatives are ready. Attach a creative to each campaign below.');
        if (r.warnings && r.warnings.length) alert(r.warnings.join('\n'));
      } catch (x) { pn.className = 'toast msgline'; pn.textContent = t('Campaigns created, but creatives failed: {e}', { e: x.message }); }
      await refresh();
      pn.insertAdjacentHTML('beforebegin', `<button type="button" id="mkvid">${t('Also make a video')}</button>`);
      $('#mkvid', el).onclick = () => { const d = $('#vdet', el), f = $('#vf', el); d.open = true; const p = f.querySelector('[name=product]'); if (p && v.product) p.value = v.product; d.scrollIntoView({ behavior: 'smooth' }); };
    });
    await Promise.all([refresh(), videosPanel($('#sp-vid', el)), copyPanel($('#sp-copy', el))]);
  },
  creatives(el) { location.hash = '#studio'; },

  videos(el) { location.hash = '#studio'; },

  async optimizer(el) {
    const recs = await api('/ai/recommendations');
    el.innerHTML = `<h2>Optimizer</h2>` + (recs.length ? recs.map((r, i) => `
      <div class="card sev-${r.severity}"><b>${esc(r.campaign)}</b> ${pill(r.platform)} <span class="muted">· ${t(r.action)}</span>
      <p>${esc(r.reason)}</p>
      ${r.action === 'creative' ? '<a href="#studio">Open AI Studio →</a>' : `<button class="pri" data-i="${i}">${r.action === 'pause' ? t('Apply: pause campaign') : t('Apply: set budget {v}', { v: money(r.value) })}</button>`}</div>`).join('')
      : '<div class="card">No recommendations – all campaigns look healthy.</div>');
    el.onclick = async e => {
      const b = e.target.closest('button[data-i]'); if (!b) return;
      const r = recs[b.dataset.i];
      const res = await api('/ai/apply', { method: 'POST', body: { campaign_id: r.campaign_id, action: r.action, value: r.value } });
      if (res.sync_error) alert(t('Platform sync failed: {e}', { e: res.sync_error }));
      views.optimizer(el);
    };
  },


  async whatsapp(el) {
    const [st, leads, camps, cfg] = await Promise.all([api('/whatsapp/status'), api('/leads'), api('/campaigns'), api('/whatsapp/settings')]);
    const stage = s => `<span class="pill" style="background:${{ new: '#888', engaged: '#3498db', qualified: '#f39c12', won: '#27ae60', lost: '#7f8c8d' }[s]}">${s}</span>`;
    el.innerHTML = `<h2>WhatsApp Sales <span class="muted">· ${t('{mode} mode', { mode: t(st.mode) })}</span></h2>
      ${st.live ? '' : '<p class="muted">Demo mode: messages are simulated, nothing is sent. Add your WhatsApp credentials on the Connections page to go live.</p>'}
      <div class="grid wa-grid">
        <div>
          <div class="card scroll"><table class="rtable"><thead><tr><th>Lead<th>Stage<th>Campaign</tr></thead>
            ${leads.map(l => `<tr data-l="${l.id}" style="cursor:pointer"><td data-label="${t('Lead')}">${esc(l.name || l.phone)}${l.handoff ? ' 🧑' : ''}${l.opted_out ? ' 🚫' : ''}<td data-label="${t('Stage')}">${stage(l.stage)}<td data-label="${t('Campaign')}">${esc(l.campaign || '–')}`).join('') || '<tr><td colspan=3 class="muted">No leads yet</tr>'}</table></div>
          <div class="card"><b>Simulate a customer</b><form id="sim" class="row" style="margin-top:8px">
            <label>Phone<input name="phone" value="5511999990000" required></label>
            <label>From campaign<select name="campaign_id"><option value="">(none)</option>${camps.map(c => `<option value="${c.id}">${esc(c.name)}</option>`).join('')}</select></label>
            <label>Message<input name="text" value="${esc(t('Hi, how much is it?'))}" required></label><button class="pri">Send</button></form></div>
          <div class="card"><b>Sales knowledge</b> <span class="muted">(the only facts the AI may use)</span>
            <textarea id="kb" rows="5" style="width:100%;margin:8px 0">${esc(cfg.knowledge)}</textarea><button id="savekb">Save</button>
            <button id="fu">Run follow-ups now</button></div>
        </div>
        <div class="card" id="chatbox"><p class="muted">Select a lead.</p></div></div>`;
    const open = async id => {
      const [ms, l] = [await api(`/leads/${id}/messages`), leads.find(x => x.id === id)];
      $('#chatbox').innerHTML = `<b>${esc(l.name || l.phone)}</b> ${stage(l.stage)} ${l.value ? money(l.value) : ''}
        <div style="margin:10px 0;max-height:360px;overflow:auto">${ms.map(m => `<div class="msg ${m.direction === 'in' ? 'a' : 'u'}" title="${m.sender}">${esc(m.body)}</div>`).join('')}</div>
        <form id="reply" class="row" style="grid-template-columns:1fr auto"><input name="text" placeholder="Reply as human (pauses AI)…" required autocomplete="off"><button class="pri">Send</button></form>
        <p>${l.handoff ? `<button data-h="0">Give back to AI</button>` : `<button data-h="1">Take over</button>`}
        ${l.stage !== 'won' ? `<button data-won>Mark sold…</button>` : ''}</p>`;
      if (matchMedia('(max-width:700px)').matches) $('#chatbox').scrollIntoView({ behavior: 'smooth', block: 'start' });   // phones stack the chat below the lists
      $('#reply').onsubmit = async e => { e.preventDefault(); const r = await api(`/leads/${id}/send`, { method: 'POST', body: { text: e.target.text.value } }); if (r.error) alert(t(r.error)); views.whatsapp(el).then(() => open(id)); };
      $('#chatbox').onclick = async e => {
        const b = e.target.closest('button'); if (!b) return;
        if (b.dataset.h !== undefined) await api(`/leads/${id}/handoff?on=${b.dataset.h === '1'}`, { method: 'POST' });
        else if ('won' in b.dataset) { const v = parseFloat(prompt(t('Sale value ($)?'))); if (!(v > 0)) return; try { await api(`/leads/${id}/won`, { method: 'POST', body: { value: v } }); } catch (x) { return alert(t(x.message)); } }
        views.whatsapp(el).then(() => open(id));
      };
    };
    el.onclick = e => { const tr = e.target.closest('tr[data-l]'); if (tr) open(+tr.dataset.l); };
    $('#sim').onsubmit = async e => {
      e.preventDefault(); const b = Object.fromEntries(new FormData(e.target));
      b.campaign_id = b.campaign_id ? +b.campaign_id : null;
      const r = await api('/whatsapp/simulate', { method: 'POST', body: b });
      views.whatsapp(el).then(() => r.lead_id > 0 && open(r.lead_id));
    };
    $('#savekb').onclick = async () => { await api('/whatsapp/settings', { method: 'PUT', body: { knowledge: $('#kb').value } }); alert(t('Saved')); };
    $('#fu').onclick = async () => alert(t('{n} follow-ups sent', { n: (await api('/whatsapp/followups/run', { method: 'POST' })).sent }));
  },

  async chat(el) {
    const examples = ['Which campaign should I scale?', 'Which campaign is losing money and should I pause?', 'How did my ads perform this week?',
      'Where should I move my budget to sell more?', 'Which platform gives me the best return?', 'What should I improve first?'];
    el.innerHTML = `<h2>Copilot</h2>
      <p class="muted">${t('Ask about your campaigns in plain words. I use your last 7 days of data and your brand profile.')}</p>
      <div class="card" id="log" style="min-height:200px"><div class="msg a" id="hello">${esc(t('Hi! I can help you decide what to scale, pause or improve. Pick a question below or write your own.'))}</div></div>
      <div class="chips" id="chips"><span class="muted">${t('Try asking:')}</span>${examples.map(q => `<button type="button" class="chip">${esc(t(q))}</button>`).join('')}</div>
      <form class="row" id="f" style="grid-template-columns:1fr auto"><input name="q" placeholder="${esc(t('e.g. Which campaign should I scale?'))}" aria-label="${esc(t('Your question'))}" required autocomplete="off" maxlength="500"><button class="pri">Ask</button></form>`;
    const log = $('#log'), add = (c, text) => { const d = document.createElement('div'); d.className = 'msg ' + c; d.textContent = text; log.append(d); d.scrollIntoView({ block: 'nearest' }); return d; };
    const ask = async q => {
      $('#hello')?.remove(); add('u', q);
      const wait = add('a', t('Thinking…'));
      try { wait.textContent = (await api('/chat', { method: 'POST', body: { question: q } })).answer; } catch (x) { wait.textContent = t('Error: {e}', { e: t(x.message) }); }
    };
    $('#f').onsubmit = e => { e.preventDefault(); const q = e.target.q.value.trim(); if (!q) return; e.target.q.value = ''; ask(q); };
    el.querySelectorAll('.chip').forEach(c => c.onclick = () => ask(c.textContent));
    $('#f input').focus();
  },

  async connections(el) {
    const cs = await api('/connections');
    const hint = { meta: 'Also used for Instagram placements.', whatsapp: 'In Meta\'s WhatsApp webhook settings, set the callback URL and verify token shown below, and subscribe to "messages".' };
    el.innerHTML = `<h2>Connections</h2>
      <p class="muted">${t('These credentials belong to <b>your account only</b>. They are stored encrypted and never shown again after saving - leave a secret blank to keep it.')}</p>
      ${cs.map(c => `<form class="card conn" data-s="${c.service}"${c.connected ? ' data-has="1"' : ''}>
        <h3 style="margin-top:0">${esc(c.title)} <span class="conn-status muted" data-status>${c.connected ? '● checking…' : '● not connected'}</span></h3>
        ${hint[c.service] ? `<p class="muted">${hint[c.service]}</p>` : ''}
        ${c.service === 'meta' ? `<p><a href="/${document.documentElement.lang === 'en' ? 'en' : 'es'}/meta-token/" target="_blank" rel="noopener">${esc(t('How do I get my access token? Step-by-step guide'))}</a></p>` : ''}
        ${c.webhook_path ? `<p>Callback URL: <code>${esc(location.origin + c.webhook_path)}</code></p>` : ''}
        <div class="grid" style="grid-template-columns:repeat(auto-fit,minmax(240px,1fr))">${c.fields.map(f => `<label>${esc(t(f.label))}${f.required ? ' *' : ''}
          <input name="${f.name}" type="${f.secret ? 'password' : 'text'}" autocomplete="off" value="${esc(f.value)}"
            placeholder="${f.secret && f.set ? esc(f.masked) + esc(t(' (saved)')) : esc(t(f.hint || ''))}"></label>`).join('')}</div>
        <button class="pri" type="submit">Save</button>
        ${['meta', 'tiktok', 'google'].includes(c.service) && c.connected ? `<button type="button" data-import="${c.service}">Test &amp; import campaigns</button>` : ''}
        ${c.service === 'meta' && c.connected ? `<button type="button" data-sync="meta">${t('Sync results')}</button>` : ''}
        ${c.connected && !c.shared ? '<button type="button" data-clear>Disconnect</button>' : ''}
        <button type="button" data-recheck>Re-check</button>
        <span class="muted msgline"></span><p class="toast statusdetail"></p></form>`).join('')}`;
    const STATES = {
      ok: ['st-active', '● connected'], invalid: ['toast', '● credentials rejected'], no_balance: ['st-warn', '● connected, no balance'],
      error: ['st-warn', '● could not verify'], server_key: ['st-warn', '● server key set, sharing off'], not_connected: ['muted', '● not connected'] };
    const loadStatus = async force => {
      el.querySelectorAll('[data-status]').forEach(b => { if (b.closest('form').dataset.has) { b.className = 'conn-status muted'; b.textContent = t('● checking…'); } });
      let st = {};
      try { st = await api('/connections/status' + (force ? '?force=true' : '')); } catch { }
      el.querySelectorAll('form.conn').forEach(f => {
        const r = st[f.dataset.s], b = $('[data-status]', f);
        if (!r) { if (f.dataset.has) { b.className = 'conn-status st-warn'; b.textContent = t('● could not verify'); } return; }
        const [cls, txt] = STATES[r.state] || STATES.error;
        b.className = 'conn-status ' + cls;
        b.textContent = t(txt) + (r.source === 'shared' ? t(' (server key)') : '');
        b.title = t(r.detail || '');
        const d = $('.statusdetail', f); if (d) d.textContent = ['invalid', 'no_balance', 'error', 'server_key'].includes(r.state) ? t(r.detail) : '';
      });
    };
    el.querySelectorAll('form.conn').forEach(f => {
      const svc = f.dataset.s, note = $('.msgline', f);
      f.onsubmit = async e => {
        e.preventDefault();
        const values = Object.fromEntries(new FormData(f));
        try { await api('/connections/' + svc, { method: 'PUT', body: { values } }); views.connections(el); }
        catch (x) { note.textContent = x.message; note.className = 'toast msgline'; }
      };
      const imp = $('[data-import]', f), clr = $('[data-clear]', f);
      const syn = $('[data-sync]', f);
      $('[data-recheck]', f).onclick = () => loadStatus(true);
      if (imp) imp.onclick = async () => {
        note.className = 'muted msgline'; note.textContent = t('Contacting {svc}…', { svc });
        try { const r = await api(`/connections/${svc}/import`, { method: 'POST' }); note.textContent = t('Connection works. Imported {n} new campaign(s).', { n: r.imported }); if (svc === 'meta') await syncResults(note); }
        catch (x) { note.textContent = x.message; note.className = 'toast msgline'; }
      };
      const syncResults = async n => {
        n.className = 'muted msgline'; n.textContent += ' ' + t('Getting your results…');
        try {
          const r = await api('/connections/meta/sync-metrics', { method: 'POST' });
          n.textContent = t('Results updated: {n} day(s) of data.', { n: r.days }) + (r.unmatched ? ' ' + t('{n} campaign(s) in Meta are not in youradds yet: import them first.', { n: r.unmatched }) : '');
          if (r.currency_mismatch) { n.className = 'st-warn msgline'; n.textContent += ' ' + t('Your Meta account reports in {cur} but youradds shows {app}: amounts are not converted.', { cur: r.currency, app: currency }); }
        } catch (x) { n.className = 'toast msgline'; n.textContent = x.message; }
      };
      if (syn) syn.onclick = () => syncResults(note);
      if (clr) clr.onclick = async () => { if (confirm(t('Remove the saved {svc} credentials?', { svc }))) { await api('/connections/' + svc, { method: 'DELETE' }); views.connections(el); } };
    });
    loadStatus(false);
  },

  async profile(el) {
    const user = (await auth.init()).user;
    if (!user) throw new Error(t('Your session ended. Please sign in again.'));
    const email = user.primaryEmailAddress?.emailAddress || window.__youradsMe?.email || '';
    const fullName = user.fullName || [user.firstName, user.lastName].filter(Boolean).join(' ');
    const initials = (fullName || email || '?').trim().split(/\s+/).slice(0, 2).map(part => part[0]).join('').toUpperCase();
    const imageUrl = user.imageUrl && new URL(user.imageUrl).protocol === 'https:' ? user.imageUrl : '';
    el.innerHTML = `<h2>Profile</h2><p class="muted profile-intro">Manage the details associated with your account.</p>
      <div class="profile-settings-grid">
        <section class="card profile-settings-card"><h3>Personal information</h3><p class="muted">Your name is visible across youradds. Your email is managed by Clerk.</p>
          <form id="profile-name-form" class="stack"><label for="profile-name">Name<input id="profile-name" name="name" value="${esc(fullName)}" maxlength="80" autocomplete="name" required></label>
            <div class="profile-email"><span class="muted">Email address</span><strong>${esc(email || 'No primary email')}</strong></div>
            <p class="profile-status" id="profile-name-status" role="status" aria-live="polite"></p><button class="pri">Save changes</button></form>
        </section>
        <section class="card profile-settings-card"><h3>Profile picture</h3><p class="muted">Use a square image for the best fit.</p>
          <div class="profile-picture-row"><div class="profile-avatar-large" id="profile-avatar-preview">${imageUrl ? `<img src="${esc(imageUrl)}" alt="Profile picture">` : esc(initials)}</div>
            <div class="profile-picture-controls"><label class="profile-file-button" for="profile-photo-input">Choose image</label><input class="profile-file-input" id="profile-photo-input" type="file" accept="image/png,image/jpeg,image/gif,image/webp" aria-label="Choose profile picture">
              <small class="muted">PNG, JPG, GIF, or WebP</small><button id="save-profile-photo" type="button" class="pri" disabled>Save picture</button>
              <button id="remove-profile-photo" type="button" ${user.hasImage ? '' : 'hidden'}>Remove picture</button></div></div>
          <p class="profile-status" id="profile-photo-status" role="status" aria-live="polite"></p>
        </section>
      </div>`;

    const nameForm = $('#profile-name-form', el), nameStatus = $('#profile-name-status', el);
    nameForm.onsubmit = async e => {
      e.preventDefault();
      const button = nameForm.querySelector('button');
      authButton(button, true);
      nameStatus.textContent = '';
      nameStatus.classList.remove('toast');
      try {
        const updated = await auth.updateProfile(new FormData(nameForm).get('name'));
        syncProfileIdentity(updated);
        nameForm.elements.name.value = updated.fullName || [updated.firstName, updated.lastName].filter(Boolean).join(' ');
        nameStatus.textContent = 'Name updated.';
      } catch (x) { nameStatus.textContent = clerkError(x); nameStatus.classList.add('toast'); }
      finally { authButton(button, false); }
    };

    const photoInput = $('#profile-photo-input', el), savePhoto = $('#save-profile-photo', el);
    const photoStatus = $('#profile-photo-status', el), preview = $('#profile-avatar-preview', el);
    let previewUrl = '';
    photoInput.onchange = () => {
      const file = photoInput.files?.[0];
      photoStatus.textContent = '';
      photoStatus.classList.remove('toast');
      if (!file) { savePhoto.disabled = true; return; }
      if (!['image/png', 'image/jpeg', 'image/gif', 'image/webp'].includes(file.type)) { photoStatus.textContent = 'Choose a PNG, JPG, GIF, or WebP image.'; photoStatus.classList.add('toast'); photoInput.value = ''; savePhoto.disabled = true; return; }
      if (file.size > 10 * 1024 * 1024) { photoStatus.textContent = 'Choose an image smaller than 10 MB.'; photoStatus.classList.add('toast'); photoInput.value = ''; savePhoto.disabled = true; return; }
      if (previewUrl) URL.revokeObjectURL(previewUrl);
      previewUrl = URL.createObjectURL(file);
      preview.replaceChildren();
      const image = document.createElement('img'); image.src = previewUrl; image.alt = 'Selected profile picture'; preview.append(image);
      savePhoto.disabled = false;
    };
    savePhoto.onclick = async () => {
      const file = photoInput.files?.[0]; if (!file) return;
      let saved = false;
      authButton(savePhoto, true); photoStatus.textContent = ''; photoStatus.classList.remove('toast');
      try {
        await auth.setProfileImage(file);
        await user.reload();
        syncProfileIdentity(user);
        preview.replaceChildren();
        if (user.imageUrl) { const image = document.createElement('img'); image.src = user.imageUrl; image.alt = 'Profile picture'; preview.append(image); }
        $('#remove-profile-photo', el).hidden = !user.hasImage;
        photoInput.value = ''; photoStatus.textContent = 'Profile picture updated.';
        saved = true;
      } catch (x) { photoStatus.textContent = clerkError(x); photoStatus.classList.add('toast'); }
      finally { authButton(savePhoto, false); savePhoto.disabled = saved || !photoInput.files?.[0]; }
    };
    $('#remove-profile-photo', el).onclick = async e => {
      authButton(e.currentTarget, true); photoStatus.textContent = ''; photoStatus.classList.remove('toast');
      try {
        await auth.setProfileImage(null);
        await user.reload();
        syncProfileIdentity(user);
        preview.textContent = initials;
        e.currentTarget.hidden = true;
        photoStatus.textContent = 'Profile picture removed.';
      } catch (x) { photoStatus.textContent = clerkError(x); photoStatus.classList.add('toast'); }
      finally { authButton(e.currentTarget, false); }
    };
  },
};

// ---- accounts ----
function authButton(button, loading) {
  if (!button) return;
  button.classList.toggle('is-loading', loading);
  button.disabled = loading;
  button.setAttribute('aria-busy', String(loading));
}

function clerkError(error) {
  return error?.errors?.map(item => item.longMessage || item.message).filter(Boolean).join(' ') || error?.message || t('Could not update your profile. Please try again.');
}

function syncProfileIdentity(user, fallback = window.__youradsMe || {}) {
  const email = user?.primaryEmailAddress?.emailAddress || fallback.email || '';
  const displayName = user?.fullName || [user?.firstName, user?.lastName].filter(Boolean).join(' ') || fallback.name || email;
  window.__youradsMe = { ...fallback, name: displayName, email };
  $('#who-name').textContent = displayName;
  $('#who-email').textContent = email;
  $('#menu-name').textContent = displayName;
  $('#menu-email').textContent = email;
  const avatar = $('#avatar');
  if (avatar) {
    avatar.textContent = displayName.trim().split(/\s+/).slice(0, 2).map(part => part[0]).join('').toUpperCase() || '?';
    const imageUrl = user?.imageUrl;
    if (imageUrl && new URL(imageUrl).protocol === 'https:') {
      const image = document.createElement('img'); image.className = 'avatar-image'; image.alt = ''; image.src = imageUrl;
      image.onerror = () => image.remove(); avatar.append(image);
    }
  }
  return { displayName, email };
}

function showAuth() {
  document.body.classList.add('anon');
  const el = $('#view'); el.onclick = null;
  const path = appPath;
  let mode = path === '/sign-up' ? 'signup' : path === '/forgot-password' ? 'reset' : path === '/account' ? 'account' : 'signin';
  let busy = false, email = '', name = '';
  const error = (message) => { const node = $('#autherr', el); if (node) node.textContent = message; };
  const render = (screen = mode, stage = 'form') => {
    mode = screen;
    const heading = stage === 'verify' ? 'Verify your email' : stage === 'device' ? 'Verify your sign-in' : ({ signin: 'Welcome back', signup: 'Create your account', reset: 'Reset your password', account: 'Your account', callback: 'Finishing sign in', loading: 'Connecting securely' }[screen] || 'Sign in');
    const form = (screen === 'signin' || screen === 'signup') && stage === 'form';
    el.innerHTML = `<div class="card authbox${screen === 'callback' ? ' callback-card' : ''}"${screen === 'callback' ? ' data-state="loading" aria-busy="true"' : ''}>${screen === 'callback' ? '' : `<h2>${heading}</h2>`}${form ? '<p class="muted">Use youradds securely with email or Google.</p>' : ''}
      ${form ? `<button type="button" id="google" class="google-signin"><img src="https://developers.google.com/static/identity/images/g-logo.png" alt="" width="20" height="20">Continue with Google</button><p class="auth-divider"><span>OR</span></p>` : ''}
      <p class="toast" id="autherr" role="alert" aria-live="polite"></p>
      ${screen === 'signin' ? `<form id="authf" class="stack"><label>Email<input name="email" type="email" required autocomplete="email"></label><label>Password<input name="password" type="password" required autocomplete="current-password"></label><button class="pri">Sign in</button><a class="forgot-password" href="${L('/forgot-password')}">Forgot password?</a></form><p class="muted">New here? <a href="${L('/sign-up')}">Create an account</a></p>` : ''}
      ${screen === 'signup' && stage === 'form' ? `<form id="authf" class="stack"><div id="clerk-captcha"></div><label>Name (optional)<input name="name" autocomplete="name" maxlength="80"></label><label>Email<input name="email" type="email" required autocomplete="email"></label><label>Password<input name="password" type="password" required minlength="8" autocomplete="new-password"></label><label>Confirm password<input name="confirmPassword" type="password" required minlength="8" autocomplete="new-password"></label><label class="terms-consent"><input name="terms" type="checkbox" required><span>${t('I agree to the <a href="{terms}" target="_blank" rel="noopener">Terms of Service</a> and acknowledge the <a href="{privacy}" target="_blank" rel="noopener">Privacy Policy</a>.', { terms: L('/terms/'), privacy: L('/privacy/') })}</span></label><button class="pri">Create account</button></form><p class="muted">Already registered? <a href="${L('/sign-in')}">Sign in</a></p>` : ''}
      ${screen === 'signup' && stage === 'verify' ? `<form id="verifyf" class="stack"><p>${t('Enter the code sent to <b>{email}</b>.', { email: esc(email) })}</p><label>Email verification code<input name="code" inputmode="numeric" autocomplete="one-time-code" required></label><button class="pri">Verify email</button><button type="button" id="resend">Send a new code</button></form>` : ''}
      ${screen === 'signin' && stage === 'device' ? `<form id="devicef" class="stack"><p>Enter the verification code sent to your email.</p><label>Email verification code<input name="code" inputmode="numeric" autocomplete="one-time-code" required></label><button class="pri">Verify device</button><button type="button" id="resend">Send a new code</button></form>` : ''}
      ${screen === 'reset' ? `<form id="resetf" class="stack"><label>Email<input name="email" type="email" required autocomplete="email" value="${esc(email)}"></label>${stage !== 'form' ? `<label>Verification code<input name="code" inputmode="numeric" autocomplete="one-time-code" required></label><label>New password<input name="password" type="password" minlength="8" autocomplete="new-password" required></label><label>Confirm new password<input name="confirmPassword" type="password" minlength="8" autocomplete="new-password" required></label>` : ''}<button class="pri">${stage === 'form' ? 'Send reset code' : 'Reset password'}</button></form><a href="${L('/sign-in')}">Back to sign in</a>` : ''}
      ${screen === 'account' ? `<p>${esc(window.__youradsMe?.email || '')}</p><form id="passwordf" class="stack"><label>Current password<input name="current" type="password" autocomplete="current-password" required></label><label>New password<input name="next" type="password" minlength="8" autocomplete="new-password" required></label><button class="pri">Update password</button></form><button id="signout">Sign out</button>` : ''}
      ${screen === 'callback' ? `<div class="callback-brand" aria-label="youradds">your<span>adds</span></div><div class="callback-icon" aria-hidden="true"><span class="callback-spinner"></span><svg class="callback-check" viewBox="0 0 24 24" fill="none"><path d="m5 12.5 4.5 4L19 7" stroke="currentColor" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round"/></svg></div><p class="callback-eyebrow">SECURE SIGN-IN</p><h2 id="callback-title">Taking you back to youradds</h2><p class="callback-message" id="callback-message" aria-live="polite">Confirming your Google account and setting up your session.</p><p class="callback-detail">This usually takes just a moment. You can keep this page open.</p><div class="callback-error" id="callback-error" role="alert" aria-live="assertive"></div><div class="callback-actions" id="callback-actions" hidden><button type="button" id="retry" class="pri">Try Google sign-in again</button><a href="${L('/sign-in')}">Return to sign in</a></div>` : ''}
      </div>`;
    if (screen === 'loading') return;
    const google = $('#google', el); if (google) google.onclick = async () => { if (busy) return; if (mode === 'signup' && !$('#authf [name="terms"]', el)?.checked) { error(t('Please accept the Terms of Service and Privacy Policy to create an account.')); $('#authf [name="terms"]', el)?.focus(); return; } busy = true; authButton(google, true); try { await auth.google({ continueSignUp: mode === 'signup' }); } catch (x) { busy = false; authButton(google, false); error(x.message); } };
    const formEl = $('#authf', el); if (formEl) formEl.onsubmit = async e => {
      e.preventDefault(); if (busy) return;
      const values = Object.fromEntries(new FormData(formEl));
      if (mode === 'signup' && values.password !== values.confirmPassword) {
        error(t('Passwords do not match.')); $('#authf [name="confirmPassword"]', el)?.focus(); return;
      }
      busy = true;
      const submit = formEl.querySelector('[type="submit"],button:not([type])'); authButton(submit, true);
      email = values.email; name = values.name || '';
      try { if (mode === 'signup') { const result = await auth.signUp(email, values.password, name); if (result.needsVerification) render('signup', 'verify'); else await boot(); } else { const result = await auth.signIn(email, values.password); if (result.needsDeviceTrust) render('signin', 'device'); else await boot(); } }
      catch (x) { error(x.message); } finally { busy = false; authButton(submit, false); }
    };
    const verify = $('#verifyf', el); if (verify) verify.onsubmit = async e => { e.preventDefault(); if (busy) return; busy = true; const b = verify.querySelector('button[type="submit"],button:not([type])'); authButton(b, true); try { await auth.verifySignup(new FormData(verify).get('code')); await boot(); } catch (x) { error(x.message); } finally { busy = false; authButton(b, false); } };
    const resend = $('#resend', el); if (resend) resend.onclick = async () => { if (busy) return; busy = true; authButton(resend, true); try { await auth.resendSignupCode(); error(t('A new verification code was sent.')); } catch (x) { error(x.message); } finally { busy = false; authButton(resend, false); resend.disabled = true; setTimeout(() => { if (resend.isConnected) resend.disabled = false; }, 30000); } };
    const device = $('#devicef', el); if (device) device.onsubmit = async e => { e.preventDefault(); if (busy) return; busy = true; const b = device.querySelector('button[type="submit"],button:not([type])'); authButton(b, true); try { await auth.verifyDeviceTrust(new FormData(device).get('code')); await boot(); } catch (x) { error(x.message); } finally { busy = false; authButton(b, false); } };
    if (resend && mode === 'signin') resend.onclick = async () => { if (busy) return; busy = true; authButton(resend, true); try { await auth.resendDeviceTrustCode(); error(t('A new verification code was sent.')); } catch (x) { error(x.message); } finally { busy = false; authButton(resend, false); resend.disabled = true; setTimeout(() => { if (resend.isConnected) resend.disabled = false; }, 30000); } };
    const reset = $('#resetf', el); if (reset) reset.onsubmit = async e => {
      e.preventDefault(); if (busy) return;
      const v = Object.fromEntries(new FormData(reset));
      if (stage !== 'form' && v.password !== v.confirmPassword) {
        error(t('Passwords do not match.')); $('#resetf [name="confirmPassword"]', el)?.focus(); return;
      }
      busy = true; const b = reset.querySelector('button[type="submit"],button:not([type])'); authButton(b, true); email = v.email;
      try { const result = await auth.resetPassword(v.email, v.code, v.password); if (result.needsCode) { render('reset', 'code'); error(t('If the account exists, a reset code has been sent.')); } else if (result.needsPassword) render('reset', 'password'); else if (result.complete) await boot(); }
      catch (x) { error(x.message); } finally { busy = false; authButton(b, false); }
    };
    const password = $('#passwordf', el); if (password) password.onsubmit = async e => { e.preventDefault(); if (busy) return; busy = true; const b = $('button', password); authButton(b, true); try { await auth.updatePassword(new FormData(password).get('current'), new FormData(password).get('next')); password.reset(); error(t('Password updated.')); } catch (x) { error(x.message); } finally { busy = false; authButton(b, false); } };
    const signout = $('#signout', el); if (signout) signout.onclick = async () => { authButton(signout, true); try { await auth.signOut(); location.replace(L('/sign-in')); } catch (x) { authButton(signout, false); error(x.message); } };
  const retry = $('#retry', el); if (retry) retry.onclick = async () => { const card = $('.callback-card', el); card.dataset.state = 'loading'; card.setAttribute('aria-busy', 'true'); $('#callback-title', el).textContent = 'Taking you back to youradds'; $('#callback-message', el).textContent = 'Confirming your Google account and setting up your session.'; $('#callback-error', el).textContent = ''; $('#callback-actions', el).hidden = true; authButton(retry, true); try { await auth.google(); } catch (x) { card.dataset.state = 'error'; card.setAttribute('aria-busy', 'false'); $('#callback-title', el).textContent = 'We couldn’t finish sign-in'; $('#callback-message', el).textContent = 'Your Google account wasn’t connected to youradds.'; $('#callback-error', el).textContent = x.message || 'Please try again.'; $('#callback-actions', el).hidden = false; authButton(retry, false); } };
  };
  async function callback() {
    render('callback');
    const card = $('.callback-card', el);
    const showFailure = message => {
      card.dataset.state = 'error';
      card.setAttribute('aria-busy', 'false');
      $('#callback-title', el).textContent = 'We couldn’t finish sign-in';
      $('#callback-message', el).textContent = 'Your Google account wasn’t connected to youradds.';
      $('#callback-error', el).textContent = message;
      $('#callback-actions', el).hidden = false;
    };
    try {
      await auth.completeGoogle();
      if (await auth.token()) { location.replace(L('/')); return; }
      showFailure('Clerk returned without an active session. Please try again.');
    } catch (x) { showFailure(x.message || 'Google sign-in could not be completed. Please try again.'); }
  }
  if (mode === 'callback' || path === '/sso-callback') { mode = 'callback'; callback(); return; }
  if (mode === 'account') {
    render('loading');
    (async () => { try { await auth.init(); if (!await auth.token()) return location.replace(L('/sign-in'));
      window.__youradsMe = await api('/auth/me'); document.body.classList.remove('anon'); render('account');
    } catch (x) { render('account'); error(x.message); } })();
    return;
  }
  if (mode === 'reset') { render('reset'); return; }
  render(mode);
}

async function boot() {
  let me;
  try { await auth.init(); if (!await auth.token()) return showAuth(); me = await api('/auth/me'); }
  catch (e) { showAuth(); return null; }
  window.__youradsMe = me;
  try { currency = (await api('/health')).currency || currency; } catch {}
  try { const r = await api('/brand'); brandCache = r.brand && r.brand.completed ? r.brand : null; } catch {}
  if (appPath.startsWith('/sign-') || appPath === '/forgot-password') location.replace(L('/'));
  document.body.classList.remove('anon');
  syncProfileIdentity(auth.clerk?.user, me);
  route();
  return me;
}

async function enterAuthPage() {
  document.body.classList.add('anon');
  $('#view').innerHTML = '<div class="card authbox auth-route-checking" role="status" aria-live="polite" aria-busy="true"><span class="callback-spinner" aria-hidden="true"></span><h2>Checking your session</h2><p class="muted">Connecting securely…</p></div>';
  try {
    await auth.init();
    if (auth.clerk?.session) { location.replace(L('/')); return; }
  } catch { /* Keep the auth form available if Clerk cannot initialize. */ }
  showAuth();
}

async function route() {
  let name = (location.hash.slice(1) || 'dashboard');
  if (!brandCache && !['brand', 'connections', 'profile'].includes(name)) { name = 'brand'; history.replaceState(null, '', '#brand'); }   // first step for every account
  document.querySelectorAll('nav a').forEach(a => a.classList.toggle('on', a.hash === '#' + name));
  const el = $('#view'); el.onclick = null; el.innerHTML = '<p class="muted">Loading…</p>';
  try { await (views[name] || views.dashboard)(el); el.querySelectorAll('select[name=language]').forEach(sel => { if ([...sel.options].some(o => o.value === lang)) sel.value = lang; }); }
  catch (x) { el.innerHTML = `<p class="toast">${esc(x.message)}</p>`; }
}
addEventListener('hashchange', () => { if (!document.body.classList.contains('anon')) route(); });
addEventListener('yourads:signed-out', () => {
  for (const timer of authPollers) clearInterval(timer);
  authPollers.clear();
  $('#who-name').textContent = '';
  $('#who-email').textContent = '';
  $('#view').replaceChildren();
  document.body.classList.add('anon');
});
const profileButton = $('#profile-button'), profileMenu = $('#profile-menu');
$('#profile-settings').onclick = () => { profileButton.setAttribute('aria-expanded', 'false'); profileMenu.hidden = true; };
const navToggle = $('#nav-toggle'), primaryNav = $('#primary-nav'), navBackdrop = $('#nav-backdrop');
const mobileNav = matchMedia('(max-width:700px)');
function closeNav() { navToggle.setAttribute('aria-expanded', 'false'); navToggle.setAttribute('aria-label', t('Open navigation')); primaryNav.classList.remove('open'); primaryNav.inert = mobileNav.matches; navBackdrop.hidden = true; }
navToggle.onclick = () => {
  const open = navToggle.getAttribute('aria-expanded') !== 'true';
  navToggle.setAttribute('aria-expanded', String(open));
  navToggle.setAttribute('aria-label', open ? t('Close navigation') : t('Open navigation'));
  primaryNav.classList.toggle('open', open);
  primaryNav.inert = !open;
  navBackdrop.hidden = !open;
};
primaryNav.inert = mobileNav.matches;
mobileNav.addEventListener('change', e => {
  if (e.matches) primaryNav.inert = navToggle.getAttribute('aria-expanded') !== 'true';
  else closeNav();
});
navBackdrop.onclick = closeNav;
primaryNav.addEventListener('click', e => { if (e.target.closest('a') && mobileNav.matches) closeNav(); });
profileButton.onclick = () => {
  const open = profileButton.getAttribute('aria-expanded') !== 'true';
  profileButton.setAttribute('aria-expanded', String(open));
  profileMenu.hidden = !open;
};
document.addEventListener('click', e => {
  if (!e.target.closest('.user')) { profileButton.setAttribute('aria-expanded', 'false'); profileMenu.hidden = true; }
});
document.addEventListener('keydown', e => {
  if (e.key === 'Escape') { profileButton.setAttribute('aria-expanded', 'false'); profileMenu.hidden = true; closeNav(); profileButton.focus(); }
});
$('#logout').onclick = async e => { authButton(e.currentTarget, true); try { await auth.signOut(); location.replace(L('/sign-in')); } catch (x) { authButton(e.currentTarget, false); alert(t(x.message || 'Could not sign out. Please try again.')); } };
startI18n();
if (['/sign-in', '/sign-up', '/forgot-password'].includes(appPath)) enterAuthPage();
else if (appPath !== '/') showAuth();
else boot().catch(showAuth);
