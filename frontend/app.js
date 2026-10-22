const $ = (s, el = document) => el.querySelector(s);
const money = n => '$' + Number(n || 0).toLocaleString(undefined, { maximumFractionDigits: 2 });
const esc = s => String(s ?? '').replace(/[&<>"']/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
const pill = p => `<span class="pill ${esc(p)}">${esc(p)}</span>`;

async function api(path, opts = {}) {
  const r = await fetch('/api' + path, {
    headers: { 'Content-Type': 'application/json' }, ...opts,
    body: opts.body ? JSON.stringify(opts.body) : undefined,
  });
  if (r.status === 401 && !path.startsWith('/auth/')) { showAuth(); throw new Error('Please log in'); }
  if (!r.ok) {
    const d = (await r.json().catch(() => ({}))).detail;
    throw new Error(Array.isArray(d) ? d.map(x => x.msg).join('; ') : d || r.statusText);
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
  if (!ok) { note.textContent = 'Not installed (pip install rembg onnxruntime opencv-python-headless numpy)'; return; }
  btn.disabled = false;
  const toDataURL = f => new Promise((res, rej) => { const r = new FileReader(); r.onload = () => res(r.result); r.onerror = rej; r.readAsDataURL(f); });
  btn.onclick = async () => {
    const f = input.files[0]; if (!f) return note.textContent = 'Choose an image first';
    if (f.size > 8e6) return note.textContent = 'Image over 8MB';
    btn.disabled = true; note.textContent = 'Removing background… (the first run downloads a ~180MB model, can take a minute)';
    try {
      const r = await api('/tools/cutout', { method: 'POST', body: { image: await toDataURL(f) } });
      const blob = await (await fetch(r.image)).blob();
      const dt = new DataTransfer(); dt.items.add(new File([blob], 'product.png', { type: 'image/png' })); input.files = dt.files;
      prev.innerHTML = `<img src="${r.image}" alt="" style="max-height:120px;margin-top:6px;border-radius:6px;background:repeating-conic-gradient(#8884 0 25%,transparent 0 50%) 0 0/14px 14px">`;
      note.textContent = `Done (${r.width}×${r.height}) – this transparent PNG will be used.`;
    } catch (x) { note.textContent = x.message; }
    btn.disabled = false;
  };
}

const views = {
  async dashboard(el) {
    const d = await api('/dashboard'), t = d.totals;
    const kpi = (l, v) => `<div class="card kpi"><small>${l}</small><b>${v}</b></div>`;
    el.innerHTML = `<h2>Dashboard <span class="muted">· last 14 days</span></h2>
      ${d.has_demo ? '<div class="card">Showing <b>sample data</b> until you connect your own ad accounts. <button id="rmdemo">Remove sample data</button></div>' : ''}
      <div class="grid">${kpi('Spend', money(t.spend))}${kpi('Revenue', money(t.revenue))}${kpi('ROAS', t.roas.toFixed(2) + 'x')}
      ${kpi('Conversions', t.conversions)}${kpi('CTR', t.ctr.toFixed(2) + '%')}${kpi('CPA', t.cpa ? money(t.cpa) : '–')}</div>
      <div class="card">${lineChart(d.daily, ['spend', 'revenue'], ['#e74c3c', '#2ecc71'])}</div>
      <div class="card scroll"><table><tr><th>Platform<th>Spend<th>Revenue<th>ROAS<th>Conversions<th>Clicks</tr>
      ${d.by_platform.map(p => `<tr><td>${pill(p.platform)}<td>${money(p.spend)}<td>${money(p.revenue)}<td>${(p.revenue / p.spend || 0).toFixed(2)}x<td>${p.conversions}<td>${p.clicks}`).join('')}</table></div>`;
    const rm = $('#rmdemo', el);
    if (rm) rm.onclick = async () => { if (confirm('Delete the sample campaigns? Your real campaigns are kept.')) { await api('/demo', { method: 'DELETE' }); views.dashboard(el); } };
  },

  async campaigns(el) {
    const rows = await api('/campaigns');
    el.innerHTML = `<h2>Campaigns</h2>
      <div class="card"><form class="row" id="f">
        <label>Platform<select name="platform"><option value="meta">Meta<option value="tiktok">TikTok<option value="google">Google</select></label>
        <label>Name<input name="name" required></label>
        <label>Objective<select name="objective"><option>sales<option>traffic<option>leads<option>awareness<option>whatsapp</select></label>
        <label>Daily budget ($)<input name="daily_budget" type="number" min="1" step="0.01" value="20"></label>
        <label>Headline<input name="headline"></label>
        <label>Landing URL<input name="landing_url" type="url"></label>
        <button class="pri">Create campaign</button></form><div id="err" class="toast"></div></div>
      <div class="card scroll"><table><tr><th>Name<th>Platform<th>Status<th>Budget/day<th>Spend 7d<th>CTR<th>CPA<th>ROAS<th></tr>
      ${rows.map(c => `<tr><td>${esc(c.name)}<td>${pill(c.platform)}<td class="st-${c.status}">${c.status}<td>${money(c.daily_budget)}
        <td>${money(c.spend)}<td>${c.ctr}%<td>${c.cpa ? money(c.cpa) : '–'}<td>${c.roas}x
        <td><button data-t="${c.id}" data-s="${c.status === 'active' ? 'paused' : 'active'}">${c.status === 'active' ? 'Pause' : 'Resume'}</button>
        <button data-d="${c.id}">✕</button>`).join('')}</table></div>`;
    $('#f').onsubmit = async e => {
      e.preventDefault();
      const b = Object.fromEntries(new FormData(e.target));
      b.daily_budget = +b.daily_budget;
      try { const r = await api('/campaigns', { method: 'POST', body: b }); if (r.sync_error) alert('Saved locally; platform sync failed: ' + r.sync_error); views.campaigns(el); }
      catch (x) { $('#err').textContent = x.message; }
    };
    el.onclick = async e => {
      const b = e.target.closest('button'); if (!b) return;
      if (b.dataset.t) await api(`/campaigns/${b.dataset.t}/status?status=${b.dataset.s}`, { method: 'POST' });
      else if (b.dataset.d && confirm('Delete campaign?')) await api('/campaigns/' + b.dataset.d, { method: 'DELETE' });
      else return;
      views.campaigns(el);
    };
  },

  async studio(el) {
    el.innerHTML = `<h2>AI Studio <span class="muted">· ad copy generator</span></h2>
      <div class="card"><form class="row" id="f">
        <label>Platform<select name="platform"><option value="meta">Meta<option value="tiktok">TikTok<option value="google">Google</select></label>
        <label>Product<input name="product" required placeholder="Organic running shoes"></label>
        <label>Audience<input name="audience" value="runners 25-45"></label>
        <label>Tone<select name="tone"><option>friendly<option>bold<option>professional<option>playful<option>urgent</select></label>
        <button class="pri">Generate</button></form></div><div id="out"></div>`;
    $('#f').onsubmit = async e => {
      e.preventDefault(); const btn = $('button', e.target); btn.disabled = true;
      try {
        const r = await api('/ai/copy', { method: 'POST', body: Object.fromEntries(new FormData(e.target)) });
        $('#out').innerHTML = (r.llm ? '' : '<p class="muted">Template mode – set ANTHROPIC_API_KEY for LLM copy.</p>') +
          r.variants.map(v => `<div class="card"><span class="muted">${esc(v.angle)}</span><h3>${esc(v.headline)}</h3><p>${esc(v.body)}</p><b>[${esc(v.cta)}]</b></div>`).join('');
      } catch (x) { $('#out').innerHTML = `<p class="toast">${esc(x.message)}</p>`; }
      btn.disabled = false;
    };
  },


  async creatives(el) {
    const [opt, list, camps] = await Promise.all([api('/creatives/options'), api('/creatives'), api('/campaigns')]);
    const label = { meta: 'Meta', tiktok: 'TikTok', google: 'Google' };
    const gallery = items => items.map(c => `<div class="card" style="margin:0"><img src="/api/creatives/${c.id}/image" alt="" loading="lazy" style="width:100%;max-height:340px;object-fit:contain;border-radius:8px;background:#000">
      <p style="margin:8px 0 4px">${pill(c.platform)} <span class="muted">${esc(c.format)} · ${c.width}×${c.height}</span></p>
      <div style="display:flex;gap:6px;flex-wrap:wrap"><a href="/api/creatives/${c.id}/image?download=true"><button type="button">Download</button></a>
      <select data-attach="${c.id}"><option value="">Attach to campaign…</option>${camps.filter(k => k.platform === c.platform).map(k => `<option value="${k.id}" ${k.id === c.campaign_id ? 'selected' : ''}>${esc(k.name)}</option>`).join('')}</select>
      <button type="button" data-del="${c.id}">✕</button></div></div>`).join('');
    el.innerHTML = `<h2>Creative Studio <span class="muted">· image ads sized for each platform</span></h2>
      <div class="card"><form id="f" class="row">
        <label>Product name<input name="product" required maxlength="80"></label>
        <label>Description (facts only)<input name="description" maxlength="500" placeholder="What it is, flavor, size, materials…"></label>
        <label>Audience<input name="audience" maxlength="120"></label>
        <label>Language<select name="language">${Object.entries(opt.languages).map(([k, v]) => `<option value="${k}">${v}`).join('')}</select></label>
        <label>Tone<select name="tone"><option>friendly<option>bold<option>professional<option>playful<option>urgent</select></label>
        <label>Theme<select name="theme">${opt.themes.map(t => `<option>${t}`).join('')}</select></label>
        <label>Price badge (optional)<input name="price" maxlength="20" placeholder="$119 USD"></label>
        <label>Rating line (optional, must be true)<input name="social_proof" maxlength="50" placeholder="4.8/5 from 1,200 reviews"></label>
        <label>Product image (PNG, transparent bg works best)<input type="file" name="img" accept="image/png,image/jpeg,image/webp"></label>
        <label style="grid-column:1/-1">Real customer testimonials (optional, one per line: <code>Name: quote</code>)<textarea name="tests" rows="2" maxlength="600"></textarea></label>
        <fieldset style="grid-column:1/-1;border:0;padding:0;margin:0"><span class="muted">Platforms</span><br>
          ${Object.keys(opt.formats).map(p => `<label style="display:inline-block;margin-right:16px;width:auto"><input type="checkbox" name="pf" value="${p}" checked style="display:inline;width:auto"> ${label[p]} <span class="muted">(${Object.values(opt.formats[p]).map(s => s.join('×')).join(', ')})</span></label>`).join('')}</fieldset>
        <label style="grid-column:1/-1">${opt.scene_available ? `<input type="checkbox" name="scene" style="display:inline;width:auto"> AI scene backgrounds <span class="muted">(Higgsfield · a few cents per image, capped at $${opt.scene_cost_cap_usd}/run)</span>` : '<span class="muted">AI scene backgrounds: add your Higgsfield API key on the <a href="#connections">Connections</a> page to enable</span>'}
          <input name="scene_hint" maxlength="200" placeholder="Scene idea, e.g. cozy bedroom at night (don't name the product)"></label>
        <button class="pri">Generate creatives</button></form>
        <p class="muted" style="margin:10px 0 0">Reviews, ratings and customer counts are never invented — they only appear if you enter them. 9:16 formats keep text inside platform safe zones.</p>
        <div id="err" class="toast"></div></div>
      <div id="out" class="grid" style="grid-template-columns:repeat(auto-fill,minmax(240px,1fr))">${gallery(list)}</div>`;
    cutoutButton($('#f'));
    const file = f => new Promise((res, rej) => { const r = new FileReader(); r.onload = () => res(r.result); r.onerror = rej; r.readAsDataURL(f); });
    $('#f').onsubmit = async e => {
      e.preventDefault(); const fd = new FormData(e.target), btn = $('button.pri', e.target); $('#err').textContent = '';
      const platforms = fd.getAll('pf'); if (!platforms.length) return $('#err').textContent = 'Pick at least one platform';
      const testimonials = (fd.get('tests') || '').split('\n').map(l => l.trim()).filter(l => l.includes(':'))
        .map(l => ({ name: l.slice(0, l.indexOf(':')).trim(), text: l.slice(l.indexOf(':') + 1).trim() }));
      const body = { platforms, testimonials };
      for (const k of ['product', 'description', 'audience', 'language', 'tone', 'theme', 'price', 'social_proof']) body[k] = fd.get(k) || '';
      const f = fd.get('img'); if (f && f.size) { if (f.size > 8e6) return $('#err').textContent = 'Image over 8MB'; body.product_image = await file(f); }
      body.scene = fd.get('scene') === 'on'; body.scene_hint = fd.get('scene_hint') || '';
      btn.disabled = true; btn.textContent = 'Generating…';
      try { const r = await api('/creatives/generate', { method: 'POST', body }); if (r.warnings.length || r.scene_cost_usd) alert((r.scene_cost_usd ? 'Scene cost: $' + r.scene_cost_usd + '\\n' : '') + r.warnings.join('\\n')); views.creatives(el); }
      catch (x) { $('#err').textContent = x.message; btn.disabled = false; btn.textContent = 'Generate creatives'; }
    };
    el.onchange = async e => {
      const id = e.target.dataset.attach; if (!id || !e.target.value) return;
      await api(`/creatives/${id}/attach`, { method: 'POST', body: { campaign_id: +e.target.value } }); alert('Attached – campaign text updated');
    };
    el.onclick = async e => {
      const b = e.target.closest('button[data-del]'); if (!b || !confirm('Delete creative?')) return;
      await api('/creatives/' + b.dataset.del, { method: 'DELETE' }); views.creatives(el);
    };
  },


  async videos(el) {
    const opt = await api('/creatives/options');
    const label = { meta: 'Meta (Reel 9:16, Feed 1:1)', tiktok: 'TikTok (9:16)', google: 'Google / YouTube (16:9)' };
    let timer = null;
    const mins = s => s == null ? '' : s < 60 ? `${s}s` : `${Math.floor(s / 60)}m ${s % 60}s`;
    const stateText = v => v.status === 'failed' ? '⚠ ' + esc(v.error || 'failed')
      : v.status === 'compositing' ? 'Adding your product, text and CTA…'
      : v.provider_status === 'in_progress' ? `Rendering at Higgsfield… ${mins(v.elapsed_s)} (usually a few minutes)`
      : v.provider_status === 'queued' ? `Waiting in Higgsfield's queue… ${mins(v.elapsed_s)}`
      : `Starting… ${mins(v.elapsed_s)}`;
    const render = list => {
      $('#vlist').innerHTML = list.map(v => `<div class="card" style="margin:0">
        ${v.status === 'done' ? `<video src="/api/videos/${v.id}/file" controls preload="metadata" style="width:100%;max-height:340px;border-radius:8px;background:#000"></video>`
          : `<div style="height:200px;display:grid;place-items:center;background:#000;border-radius:8px;color:#aaa;padding:10px;text-align:center">${stateText(v)}</div>`}
        <p style="margin:8px 0 4px">${pill(v.platform)} <span class="muted">${esc(v.format)} · ${v.width}×${v.height} · ${v.duration}s · ~$${(v.cost_usd || 0).toFixed(2)}</span></p>
        <div style="display:flex;gap:6px;flex-wrap:wrap">${v.status === 'done' ? `<a href="/api/videos/${v.id}/file?download=true"><button type="button">Download</button></a>` : ''}
        ${v.resumable ? `<button type="button" data-resume="${v.id}">Check again</button>` : ''}
        ${v.status === 'generating' && v.provider_status === 'queued' ? `<button type="button" data-cancel="${v.id}">Cancel (refund)</button>` : ''}
        <button type="button" data-del="${v.id}">✕</button></div>
        ${v.request_id && v.status !== 'done' ? `<p class="muted" style="font-size:11px;margin:6px 0 0">Request ${esc(v.request_id)}</p>` : ''}</div>`).join('') || '<p class="muted">No videos yet.</p>';
    };
    const refresh = async () => {
      if (location.hash !== '#videos') return clearInterval(timer);
      const list = await api('/videos'); render(list);
      if (!list.some(v => ['queued', 'generating', 'compositing'].includes(v.status))) { clearInterval(timer); timer = null; }
    };
    const poll = () => { clearInterval(timer); timer = setInterval(refresh, 4000); };
    el.innerHTML = `<h2>Video Studio <span class="muted">· AI background video + your product, text and CTA</span></h2>
      ${opt.scene_available ? '' : '<p class="toast">Add your Higgsfield API key on the <a href="#connections">Connections</a> page to enable video.</p>'}
      <div class="card"><form id="f" class="row">
        <label>Product name<input name="product" required maxlength="80"></label>
        <label>Description (facts only)<input name="description" maxlength="500"></label>
        <label>Audience<input name="audience" maxlength="120"></label>
        <label>Scene idea (don't name the product)<input name="scene_hint" maxlength="200" placeholder="cozy bedroom at night"></label>
        <label>Language<select name="language">${Object.entries(opt.languages).map(([k, v]) => `<option value="${k}">${v}`).join('')}</select></label>
        <label>Theme<select name="theme">${opt.themes.map(t => `<option>${t}`).join('')}</select></label>
        <label>Length<select name="duration"><option>4<option selected>5<option>8<option>10<option>15</select></label>
        <label>Quality<select name="resolution"><option value="480p">Draft 480p (~$0.21/s)<option value="720p" selected>Standard 720p (~$0.46/s)</select></label>
        <label>Price badge (optional)<input name="price" maxlength="20"></label>
        <label>Rating line (optional, must be true)<input name="social_proof" maxlength="50"></label>
        <label>Product image (PNG, transparent bg)<input type="file" name="img" accept="image/png,image/jpeg,image/webp"></label>
        <fieldset style="grid-column:1/-1;border:0;padding:0;margin:0"><span class="muted">Platforms</span><br>
          ${Object.keys(label).map(p => `<label style="display:inline-block;margin-right:16px;width:auto"><input type="checkbox" name="pf" value="${p}" checked style="display:inline;width:auto"> ${label[p]}</label>`).join('')}</fieldset>
        <button class="pri" ${opt.scene_available ? '' : 'disabled'}>Estimate &amp; generate</button></form>
        <p class="muted" style="margin:10px 0 0">One background clip is generated per aspect ratio and shared across platforms. Your real product photo is composited on top, never redrawn. No audio. You'll see the estimated cost and confirm before anything is billed.</p>
        <div id="err" class="toast"></div></div>
      <div id="vlist" class="grid" style="grid-template-columns:repeat(auto-fill,minmax(240px,1fr))"></div>`;
    cutoutButton($('#f'));
    const file = f => new Promise((res, rej) => { const r = new FileReader(); r.onload = () => res(r.result); r.onerror = rej; r.readAsDataURL(f); });
    $('#f').onsubmit = async e => {
      e.preventDefault(); const fd = new FormData(e.target), btn = $('button.pri', e.target); $('#err').textContent = '';
      const platforms = fd.getAll('pf'); if (!platforms.length) return $('#err').textContent = 'Pick at least one platform';
      const body = { platforms, duration: +fd.get('duration') };
      for (const k of ['product', 'description', 'audience', 'language', 'theme', 'price', 'social_proof', 'scene_hint', 'resolution']) body[k] = fd.get(k) || '';
      const f = fd.get('img'); if (f && f.size) { if (f.size > 8e6) return $('#err').textContent = 'Image over 8MB'; body.product_image = await file(f); }
      btn.disabled = true;
      try {
        const est = await api('/videos/estimate', { method: 'POST', body });
        if (est.total_usd > est.cap_usd) throw new Error(`Estimated $${est.total_usd.toFixed(2)} exceeds your cap of $${est.cap_usd.toFixed(2)} (VIDEO_MAX_USD). Try fewer platforms, a shorter clip or Draft quality.`);
        if (!confirm(`Estimated cost: ~$${est.total_usd.toFixed(2)} (${Object.keys(est.per_ratio).length} clip(s)). Billed to your Higgsfield balance. Generate?`)) { btn.disabled = false; return; }
        await api('/videos/generate', { method: 'POST', body }); await refresh(); poll();
      } catch (x) { $('#err').textContent = x.message; }
      btn.disabled = false;
    };
    el.onclick = async e => {
      const b = e.target.closest('button'); if (!b) return;
      try {
        if (b.dataset.resume) { await api(`/videos/${b.dataset.resume}/resume`, { method: 'POST' }); await refresh(); poll(); }
        else if (b.dataset.cancel) { if (!confirm('Cancel this clip? Higgsfield refunds it if still queued.')) return; await api(`/videos/${b.dataset.cancel}/cancel`, { method: 'POST' }); refresh(); }
        else if (b.dataset.del && confirm('Delete video?')) { await api('/videos/' + b.dataset.del, { method: 'DELETE' }); refresh(); }
      } catch (x) { alert(x.message); }
    };
    await refresh(); const l = await api('/videos'); if (l.some(v => ['queued', 'generating', 'compositing'].includes(v.status))) poll();
  },

  async optimizer(el) {
    const recs = await api('/ai/recommendations');
    el.innerHTML = `<h2>Optimizer</h2>` + (recs.length ? recs.map((r, i) => `
      <div class="card sev-${r.severity}"><b>${esc(r.campaign)}</b> ${pill(r.platform)} <span class="muted">· ${r.action}</span>
      <p>${esc(r.reason)}</p>
      ${r.action === 'creative' ? '<a href="#studio">Open AI Studio →</a>' : `<button class="pri" data-i="${i}">Apply: ${r.action === 'pause' ? 'pause campaign' : 'set budget ' + money(r.value)}</button>`}</div>`).join('')
      : '<div class="card">No recommendations – all campaigns look healthy.</div>');
    el.onclick = async e => {
      const b = e.target.closest('button[data-i]'); if (!b) return;
      const r = recs[b.dataset.i];
      const res = await api('/ai/apply', { method: 'POST', body: { campaign_id: r.campaign_id, action: r.action, value: r.value } });
      if (res.sync_error) alert('Platform sync failed: ' + res.sync_error);
      views.optimizer(el);
    };
  },


  async whatsapp(el) {
    const [st, leads, camps, cfg] = await Promise.all([api('/whatsapp/status'), api('/leads'), api('/campaigns'), api('/whatsapp/settings')]);
    const stage = s => `<span class="pill" style="background:${{ new: '#888', engaged: '#3498db', qualified: '#f39c12', won: '#27ae60', lost: '#7f8c8d' }[s]}">${s}</span>`;
    el.innerHTML = `<h2>WhatsApp Sales <span class="muted">· ${st.mode} mode</span></h2>
      ${st.live ? '' : '<p class="muted">Demo mode: messages are simulated, nothing is sent. Add your WhatsApp credentials on the Connections page to go live.</p>'}
      <div class="grid" style="grid-template-columns:1fr 1.4fr;align-items:start">
        <div>
          <div class="card scroll"><table><tr><th>Lead<th>Stage<th>Campaign</tr>
            ${leads.map(l => `<tr data-l="${l.id}" style="cursor:pointer"><td>${esc(l.name || l.phone)}${l.handoff ? ' 🧑' : ''}${l.opted_out ? ' 🚫' : ''}<td>${stage(l.stage)}<td>${esc(l.campaign || '–')}`).join('') || '<tr><td colspan=3 class="muted">No leads yet</tr>'}</table></div>
          <div class="card"><b>Simulate a customer</b><form id="sim" class="row" style="margin-top:8px">
            <label>Phone<input name="phone" value="5511999990000" required></label>
            <label>From campaign<select name="campaign_id"><option value="">(none)</option>${camps.map(c => `<option value="${c.id}">${esc(c.name)}</option>`).join('')}</select></label>
            <label>Message<input name="text" value="Hi, how much is it?" required></label><button class="pri">Send</button></form></div>
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
      $('#reply').onsubmit = async e => { e.preventDefault(); const r = await api(`/leads/${id}/send`, { method: 'POST', body: { text: e.target.text.value } }); if (r.error) alert(r.error); views.whatsapp(el).then(() => open(id)); };
      $('#chatbox').onclick = async e => {
        const b = e.target.closest('button'); if (!b) return;
        if (b.dataset.h !== undefined) await api(`/leads/${id}/handoff?on=${b.dataset.h === '1'}`, { method: 'POST' });
        else if ('won' in b.dataset) { const v = parseFloat(prompt('Sale value ($)?')); if (!(v > 0)) return; try { await api(`/leads/${id}/won`, { method: 'POST', body: { value: v } }); } catch (x) { return alert(x.message); } }
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
    $('#savekb').onclick = async () => { await api('/whatsapp/settings', { method: 'PUT', body: { knowledge: $('#kb').value } }); alert('Saved'); };
    $('#fu').onclick = async () => alert((await api('/whatsapp/followups/run', { method: 'POST' })).sent + ' follow-ups sent');
  },

  async chat(el) {
    el.innerHTML = `<h2>Copilot</h2><div class="card" id="log" style="min-height:200px"></div>
      <form class="row" id="f" style="grid-template-columns:1fr auto"><input name="q" placeholder="Which campaign should I scale?" required autocomplete="off"><button class="pri">Ask</button></form>`;
    const log = $('#log'), add = (c, t) => { const d = document.createElement('div'); d.className = 'msg ' + c; d.textContent = t; log.append(d); };
    $('#f').onsubmit = async e => {
      e.preventDefault(); const q = e.target.q.value; e.target.q.value = ''; add('u', q);
      try { add('a', (await api('/chat', { method: 'POST', body: { question: q } })).answer); } catch (x) { add('a', 'Error: ' + x.message); }
    };
  },

  async connections(el) {
    const cs = await api('/connections');
    const hint = { meta: 'Also used for Instagram placements.', whatsapp: 'In Meta\'s WhatsApp webhook settings, set the callback URL and verify token shown below, and subscribe to "messages".' };
    el.innerHTML = `<h2>Connections</h2>
      <p class="muted">These credentials belong to <b>your account only</b>. They are stored encrypted and never shown again after saving - leave a secret blank to keep it.</p>
      ${cs.map(c => `<form class="card conn" data-s="${c.service}">
        <h3 style="margin-top:0">${esc(c.title)} ${c.connected ? '<span class="st-active">● connected' + (c.shared ? ' (server key)' : '') + '</span>' : '<span class="muted">● not connected</span>'}</h3>
        ${hint[c.service] ? `<p class="muted">${hint[c.service]}</p>` : ''}
        ${c.webhook_path ? `<p>Callback URL: <code>${esc(location.origin + c.webhook_path)}</code></p>` : ''}
        <div class="grid" style="grid-template-columns:repeat(auto-fit,minmax(240px,1fr))">${c.fields.map(f => `<label>${esc(f.label)}${f.required ? ' *' : ''}
          <input name="${f.name}" type="${f.secret ? 'password' : 'text'}" autocomplete="off" value="${esc(f.value)}"
            placeholder="${f.secret && f.set ? esc(f.masked) + ' (saved)' : esc(f.hint || '')}"></label>`).join('')}</div>
        <button class="pri" type="submit">Save</button>
        ${['meta', 'tiktok', 'google'].includes(c.service) && c.connected ? `<button type="button" data-import="${c.service}">Test &amp; import campaigns</button>` : ''}
        ${c.connected && !c.shared ? '<button type="button" data-clear>Disconnect</button>' : ''}
        <span class="muted msgline"></span></form>`).join('')}`;
    el.querySelectorAll('form.conn').forEach(f => {
      const svc = f.dataset.s, note = $('.msgline', f);
      f.onsubmit = async e => {
        e.preventDefault();
        const values = Object.fromEntries(new FormData(f));
        try { await api('/connections/' + svc, { method: 'PUT', body: { values } }); views.connections(el); }
        catch (x) { note.textContent = x.message; note.className = 'toast msgline'; }
      };
      const imp = $('[data-import]', f), clr = $('[data-clear]', f);
      if (imp) imp.onclick = async () => {
        note.className = 'muted msgline'; note.textContent = 'Contacting ' + svc + '…';
        try { const r = await api(`/connections/${svc}/import`, { method: 'POST' }); note.textContent = `Connection works. Imported ${r.imported} new campaign(s).`; }
        catch (x) { note.textContent = x.message; note.className = 'toast msgline'; }
      };
      if (clr) clr.onclick = async () => { if (confirm('Remove the saved ' + svc + ' credentials?')) { await api('/connections/' + svc, { method: 'DELETE' }); views.connections(el); } };
    });
  },
};

// ---- accounts ----
function showAuth(mode = 'login') {
  document.body.classList.add('anon');
  const el = $('#view'); el.onclick = null;
  const reg = mode === 'register';
  el.innerHTML = `<div class="card authbox"><h2>${reg ? 'Create your account' : 'Log in'}</h2>
    <form id="authf" class="stack">
      ${reg ? '<label>Name<input name="name" autocomplete="name" maxlength="80"></label>' : ''}
      <label>Email<input name="email" type="email" required autocomplete="email"></label>
      <label>Password${reg ? ' (min 8 characters)' : ''}<input name="password" type="password" required minlength="${reg ? 8 : 1}" autocomplete="${reg ? 'new-password' : 'current-password'}"></label>
      <button class="pri" type="submit">${reg ? 'Sign up' : 'Log in'}</button>
      <span class="toast" id="autherr"></span></form>
    <p class="muted">${reg ? 'Have an account? <a href="#" id="swap">Log in</a>' : 'New here? <a href="#" id="swap">Create an account</a>'}</p></div>`;
  $('#swap').onclick = e => { e.preventDefault(); showAuth(reg ? 'login' : 'register'); };
  $('#authf').onsubmit = async e => {
    e.preventDefault();
    try { await api('/auth/' + mode, { method: 'POST', body: Object.fromEntries(new FormData(e.target)) }); await boot(); }
    catch (x) { $('#autherr').textContent = x.message; }
  };
}

async function boot() {
  let me;
  try { me = await api('/auth/me'); } catch { return showAuth(); }
  document.body.classList.remove('anon');
  $('#who').textContent = me.name || me.email;
  route();
}

async function route() {
  const name = (location.hash.slice(1) || 'dashboard');
  document.querySelectorAll('nav a').forEach(a => a.classList.toggle('on', a.hash === '#' + name));
  const el = $('#view'); el.onclick = null; el.innerHTML = '<p class="muted">Loading…</p>';
  try { await (views[name] || views.dashboard)(el); }
  catch (x) { el.innerHTML = `<p class="toast">${esc(x.message)}</p>`; }
}
addEventListener('hashchange', () => { if (!document.body.classList.contains('anon')) route(); });
$('#logout').onclick = async () => { await api('/auth/logout', { method: 'POST' }); showAuth(); };
boot();
