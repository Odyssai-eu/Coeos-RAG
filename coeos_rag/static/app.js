// Coeos-RAG operator console: plain HTML/CSS/JS, no build, no external resource.
// This service is reached through api() (same origin, bearer token). Each LightRAG
// instance is reached directly from the browser through lr() (no proxy, no token).
(() => {
  'use strict';

  // -- constants ---------------------------------------------------------------
  const SECTION_KEYS = ['overview', 'documents', 'query', 'models', 'journal'];
  const SECTION_TITLES = {
    overview: "Vue d'ensemble", documents: 'Documents', query: 'Requête', models: 'Modèles', journal: 'Journal',
  };
  const TOKEN_KEY = 'coeos_rag_token';
  const CORPUS_KEY = 'coeos_rag_corpus';
  const TOKEN_TEXT = "Nécessaire pour tout changement de modèle, d'endpoint ou d'embedder. Conservé pour cet onglet uniquement.";
  const OVERVIEW_MS = 10000;
  const JOB_MS = 1000;
  const DOCS_BUSY_MS = 4000;
  const DOCS_IDLE_MS = 20000;
  const PAGE_SIZE = 20;
  const SUMMARY_MAX = 140;
  const ACTION_LABELS = { llm: 'Modèle de langage', embedder: 'Embedder', certify: 'Certification', start: 'Démarrage' };
  const DOC_STATUS = {
    processed: ['traité', 'ok'],
    processing: ['en cours', 'info'],
    pending: ['en attente', 'warn'],
    preprocessed: ['prétraité', 'info'],
    failed: ['échec', 'bad'],
  };
  const FILTERS = [
    { value: '', key: 'all', label: 'Tous' },
    { value: 'processed', key: 'processed', label: 'Traités' },
    { value: 'processing', key: 'processing', label: 'En cours' },
    { value: 'pending', key: 'pending', label: 'En attente' },
    { value: 'failed', key: 'failed', label: 'En échec' },
  ];
  const QUERY_MODES = [
    ['mix', 'Mixte (graphe + vecteurs)'],
    ['hybrid', 'Hybride'],
    ['local', 'Local (entités)'],
    ['global', 'Global (relations)'],
    ['naive', 'Vectoriel seul'],
  ];

  // -- helpers -----------------------------------------------------------------
  const esc = v => String(v == null ? '' : v).replace(/[&<>"']/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
  const $ = sel => document.querySelector(sel);
  const pad = n => String(n).padStart(2, '0');
  const sleep = ms => new Promise(resolve => setTimeout(resolve, ms));

  function fmtInt(n) {
    if (n == null || n === '' || !Number.isFinite(Number(n))) return '-';
    return Number(n).toLocaleString('fr-FR');
  }

  function fmtDate(iso) {
    const raw = iso == null ? '' : String(iso);
    const day = /^(\d{4})-(\d{2})-(\d{2})$/.exec(raw);
    if (day) return `${day[3]}/${day[2]}/${day[1]}`;
    // The service writes UTC datetimes without a zone suffix: read them as UTC, not local.
    const zoned = /^\d{4}-\d{2}-\d{2}T[\d:.]+$/.test(raw) ? raw + 'Z' : raw;
    const d = raw ? new Date(zoned) : null;
    if (!d || Number.isNaN(d.getTime())) return '-';
    return `${pad(d.getDate())}/${pad(d.getMonth() + 1)}/${d.getFullYear()} ${pad(d.getHours())}:${pad(d.getMinutes())}`;
  }

  function fmtClock(d) {
    return `${pad(d.getHours())}:${pad(d.getMinutes())}:${pad(d.getSeconds())}`;
  }

  function clampInt(raw, min, max, fallback) {
    const n = Math.round(Number(raw));
    if (raw === '' || !Number.isFinite(n)) return fallback;
    return Math.min(max, Math.max(min, n));
  }

  function chip(text, kind) {
    return `<span class="chip${kind ? ' ' + kind : ''}">${esc(text)}</span>`;
  }

  function alertBox(kind, html) {
    return `<div class="alert ${kind}" role="${kind === 'bad' ? 'alert' : 'status'}">${html}</div>`;
  }

  function skeleton(lines) {
    return '<div class="stack">' + '<div class="skeleton"></div>'.repeat(lines) + '</div>';
  }

  function cardSkeleton() {
    return '<div class="card">' + skeleton(3) + '</div>';
  }

  function storeGet(kind, key) {
    try { return window[kind].getItem(key) || ''; } catch (e) { return ''; }
  }

  function storeSet(kind, key, value) {
    try {
      if (value) window[kind].setItem(key, value);
      else window[kind].removeItem(key);
    } catch (e) { /* storage unavailable: the console still works without it */ }
  }

  // Compare-then-set, so a poll that finds nothing new does not touch the DOM.
  function setHtml(el, html) {
    if (el && el.innerHTML !== html) el.innerHTML = html;
  }

  function regionEl(name) {
    return document.querySelector(`[data-region="${name}"]`);
  }

  // Only data-region elements are rewritten by update(). They never hold a field.
  function setRegion(name, html) {
    setHtml(regionEl(name), html);
  }

  // -- state -------------------------------------------------------------------
  function newDocsState(corpusId) {
    return { corpusId, filter: null, page: 1, data: null, pipeline: null, counts: null, error: null };
  }

  const state = {
    overview: null,
    overviewError: null,
    corpusId: null,
    section: 'overview',
    token: '',
    job: null,
    actions: { data: null, error: null },
    docs: newDocsState(null),
    lr: {},
  };

  // -- HTTP clients ------------------------------------------------------------
  function errorMessage(body, status) {
    const d = body && (body.detail !== undefined ? body.detail : body.message);
    if (typeof d === 'string' && d) return d;
    if (d != null) return JSON.stringify(d);
    return 'HTTP ' + status;
  }

  // 1. This service (same origin). Adds the bearer token when there is one.
  async function api(path, opts = {}) {
    const headers = {};
    const init = { method: opts.method || 'GET', headers };
    if (opts.body !== undefined) {
      headers['Content-Type'] = 'application/json';
      init.body = JSON.stringify(opts.body);
    }
    if (state.token) headers.Authorization = 'Bearer ' + state.token;
    let r;
    try {
      r = await fetch(path, init);
    } catch (e) {
      throw new Error('Coeos-RAG ne répond pas.');
    }
    const body = await r.json().catch(() => null);
    if (!r.ok) {
      const err = new Error(errorMessage(body, r.status));
      err.status = r.status;
      throw err;
    }
    return body;
  }

  // 2. A LightRAG instance, called DIRECTLY from the browser (no proxy, no token).
  function lrBase(c) {
    if (c.instance.public_url) return c.instance.public_url.replace(/\/+$/, '');
    const u = new URL(c.instance.url);
    if (['localhost', '127.0.0.1', '[::1]', '::1'].includes(u.hostname)) u.hostname = location.hostname;
    return u.origin;
  }

  async function lr(c, path, opts = {}) {
    const base = lrBase(c);
    const headers = {};
    const init = { method: opts.method || 'GET', headers };
    if (opts.body instanceof FormData) {
      // Never set Content-Type here: the browser must write the multipart boundary.
      init.body = opts.body;
    } else if (opts.body !== undefined) {
      headers['Content-Type'] = 'application/json';
      init.body = JSON.stringify(opts.body);
    }
    let r;
    try {
      r = await fetch(base + path, init);
    } catch (e) {
      throw new Error('Instance injoignable depuis ce navigateur (' + base + ')');
    }
    const body = await r.json().catch(() => null);
    if (!r.ok) throw new Error(errorMessage(body, r.status));
    return body;
  }

  // -- dialogs and toasts ------------------------------------------------------
  const dlg = $('#dialog');

  // Resolves with the typed value (field mode), true (OK) or false (cancel, Escape).
  function openDialog({ title, text = '', ok, danger = false, field = null }) {
    return new Promise(resolve => {
      if (dlg.open) { resolve(false); return; }
      let result = false;
      const form = document.createElement('form');
      form.method = 'dialog';
      const h = document.createElement('h2');
      h.textContent = title;
      form.append(h);
      if (text) {
        const p = document.createElement('p');
        p.textContent = text;
        form.append(p);
      }
      let input = null;
      if (field) {
        const wrap = document.createElement('div');
        wrap.className = 'field';
        const label = document.createElement('label');
        label.htmlFor = 'dialog-input';
        label.textContent = field;
        input = document.createElement('input');
        input.type = 'password';
        input.id = 'dialog-input';
        input.autocomplete = 'off';
        input.spellcheck = false;
        wrap.append(label, input);
        form.append(wrap);
      }
      const actions = document.createElement('div');
      actions.className = 'actions';
      const cancel = document.createElement('button');
      cancel.type = 'button';
      cancel.className = 'btn ghost';
      cancel.textContent = 'Annuler';
      const submit = document.createElement('button');
      submit.type = 'submit';
      submit.className = danger ? 'btn danger' : 'btn';
      submit.textContent = ok;
      actions.append(cancel, submit);
      form.append(actions);
      cancel.addEventListener('click', () => dlg.close());
      form.addEventListener('submit', ev => {
        ev.preventDefault();
        result = input ? input.value.trim() : true;
        dlg.close();
      });
      dlg.addEventListener('close', () => resolve(result), { once: true });
      dlg.replaceChildren(form);
      dlg.showModal();
      (input || cancel).focus();
    });
  }

  function confirmDialog(ask) {
    return openDialog({ title: ask.title, text: ask.text || '', ok: ask.ok, danger: !!ask.danger })
      .then(v => v === true);
  }

  function askToken() {
    return openDialog({ title: "Jeton d'administration", text: TOKEN_TEXT, ok: 'Enregistrer', field: 'Jeton' })
      .then(value => {
        if (typeof value !== 'string' || !value) return false;
        state.token = value;
        storeSet('sessionStorage', TOKEN_KEY, value);
        renderTopbar();
        return true;
      });
  }

  async function ensureToken() {
    return !!state.token || askToken();
  }

  function clearToken() {
    state.token = '';
    storeSet('sessionStorage', TOKEN_KEY, '');
    renderTopbar();
  }

  function toast(text, kind) {
    const box = $('#toasts');
    if (!box) return;
    const t = document.createElement('div');
    t.className = kind ? `toast ${kind}` : 'toast';
    t.textContent = text || '';
    box.append(t);
    setTimeout(() => t.remove(), 6000);
  }

  // -- shared view helpers -----------------------------------------------------
  function corpusList() {
    return (state.overview && state.overview.corpora) || [];
  }

  function currentCorpus() {
    return corpusList().find(c => c.id === state.corpusId) || null;
  }

  function corpusLabel(id) {
    const c = corpusList().find(x => x.id === id);
    return c ? c.label : (id || '-');
  }

  function currentModel(c) {
    return c.llm.live || c.llm.configured || '';
  }

  function jobRunning() {
    return !!state.job && state.job.status === 'running';
  }

  function canMutate() {
    return !!state.overview && !!state.overview.mutations_enabled && !jobRunning();
  }

  // Embedder vector-space chip, shared by the overview and the Models section.
  function spaceChip(c) {
    if (c.embedder.same_space === true) return chip("même espace que l'index", 'ok');
    if (c.embedder.same_space === false) return chip('espace différent', 'bad');
    if (!c.indexed_with.embedder.certified) return chip('non certifié', 'warn');
    return '';
  }

  function fillSelect(sel, models, cur, curReady) {
    if (!sel) return;
    const list = models.slice();
    if (cur && !list.some(m => m.id === cur)) list.unshift({ id: cur, ready: curReady });
    sel.replaceChildren(...list.map(m => {
      const o = document.createElement('option');
      o.value = m.id;
      o.textContent = m.ready === false ? `${m.id} (non chargé)` : m.id;
      o.selected = m.id === cur;
      return o;
    }));
  }

  // -- overview ----------------------------------------------------------------
  function corpusCard(c) {
    const i = c.instance;
    const n = i.counts;
    const count = k => (n ? (n[k] || 0) : null);
    const liveState = i.up ? chip('en ligne', 'ok') : chip(i.loaded ? 'ne répond pas' : 'arrêtée', 'bad');
    const busy = i.busy ? chip('extraction en cours', 'warn') : '';
    const version = i.version ? chip(`LightRAG ${i.version}`) : '';
    const modelState = c.llm.ready === true ? chip('prêt', 'ok')
      : c.llm.ready === false ? chip('non chargé', 'bad') : chip('inconnu');
    const emb = c.embedder.live || {};
    return `<article class="card">
      <h2>${esc(c.label)} ${liveState} ${busy} ${version}</h2>
      <div class="stats">
        <div class="stat"><b>${fmtInt(count('processed'))}</b><span>Traités</span></div>
        <div class="stat"><b>${fmtInt(count('failed'))}</b><span>En échec</span></div>
        <div class="stat"><b>${fmtInt(count('pending'))}</b><span>En attente</span></div>
        <div class="stat"><b>${fmtInt(count('processing'))}</b><span>En cours</span></div>
      </div>
      <dl class="kv mt">
        <dt>Modèle</dt><dd><span class="mono">${esc(currentModel(c) || '-')}</span> ${modelState}</dd>
        <dt>Endpoint</dt><dd><span class="mono">${esc(c.engine.endpoint.effective || '-')}</span></dd>
        <dt>Embedder</dt><dd><span class="mono">${esc(emb.model || '-')}</span> ${spaceChip(c)}</dd>
      </dl>
      <div class="card-foot row">
        <button type="button" class="btn ghost sm" data-action="go" data-section="documents" data-id="${esc(c.id)}">Documents</button>
        <button type="button" class="btn ghost sm" data-action="go" data-section="models" data-id="${esc(c.id)}">Modèles</button>
      </div>
    </article>`;
  }

  function overviewAlerts() {
    const out = [];
    corpusList().forEach(c => {
      const who = `<strong>${esc(c.label)}</strong>`;
      if (!c.instance.up) out.push(alertBox('bad', `${who} : l'instance ne répond pas.`));
      if (c.llm.ready === false) {
        out.push(alertBox('bad', `${who} : le modèle <span class="mono">${esc(c.llm.configured)}</span> n'est pas chargé sur l'endpoint. Les requêtes hybrides ne renverront aucun contexte.`));
      }
      if (c.embedder.alive === false) out.push(alertBox('bad', `${who} : l'embedder ne répond pas.`));
      if (c.embedder.same_space === false) {
        out.push(alertBox('bad', `${who} : l'embedder ne produit pas l'espace vectoriel de l'index. La recherche vectorielle renvoie du bruit.`));
      }
    });
    if (state.overview && !state.overview.mutations_enabled) {
      out.push(alertBox('warn', "Lecture seule : aucun jeton n'est configuré sur le serveur."));
    }
    return out.join('');
  }

  function mountOverview(el) {
    el.innerHTML = `
      <div class="page-head"><div><h1>Vue d'ensemble</h1><p>État des corpus et de leurs modèles.</p></div></div>
      <div class="stack">
        <div class="stack" data-region="alerts"></div>
        <div class="cols" data-region="corpora"></div>
      </div>`;
  }

  function updateOverview() {
    setRegion('alerts', overviewAlerts());
    const list = corpusList();
    setRegion('corpora', list.length
      ? list.map(corpusCard).join('')
      : '<div class="card empty span-all"><b>Aucun corpus</b>Aucun corpus n\'est enregistré sur ce service.</div>');
  }

  // -- documents (direct calls to the LightRAG instance) -----------------------
  let docsTimer = 0;
  let docsGen = 0;

  function startDocsLoop() {
    stopDocsLoop();
    docsTick(docsGen);
  }

  function stopDocsLoop() {
    docsGen += 1;
    clearTimeout(docsTimer);
  }

  async function docsTick(gen) {
    const delay = await loadDocs(gen);
    if (gen !== docsGen) return;
    docsTimer = setTimeout(() => docsTick(gen), delay);
  }

  // Reads pipeline and current page. Returns the delay before the next poll.
  async function loadDocs(gen = null) {
    const c = currentCorpus();
    if (!c) return DOCS_IDLE_MS;
    const d = state.docs;
    const filter = d.filter;
    const page = d.page;
    let health = null;
    let pipeline = null;
    let list = null;
    let error = null;
    try {
      [health, pipeline, list] = await Promise.all([
        lr(c, '/health'),
        lr(c, '/documents/pipeline_status'),
        lr(c, '/documents/paginated', {
          method: 'POST',
          body: { status_filter: filter, page, page_size: PAGE_SIZE, sort_field: 'updated_at', sort_direction: 'desc' },
        }),
      ]);
    } catch (e) {
      error = e.message;
    }
    if (gen !== null && gen !== docsGen) return DOCS_IDLE_MS;
    if (state.docs !== d || d.corpusId !== c.id) return DOCS_IDLE_MS;
    if (d.filter !== filter || d.page !== page) return DOCS_IDLE_MS;
    if (health) state.lr[c.id] = health;
    d.error = error;
    if (list) {
      d.data = list;
      d.counts = list.status_counts || null;
    }
    if (pipeline) d.pipeline = pipeline;
    if (state.section === 'documents') renderDocsRegions();
    const counts = d.counts || {};
    const busy = !!(d.pipeline && d.pipeline.busy) || (counts.pending || 0) + (counts.processing || 0) > 0;
    return busy ? DOCS_BUSY_MS : DOCS_IDLE_MS;
  }

  function mountDocuments(el) {
    const c = currentCorpus();
    state.docs = newDocsState(c.id);
    el.innerHTML = `
      <div class="page-head"><div><h1>Documents</h1><p>${esc(c.label)} : injecter des documents et suivre leur traitement.</p></div></div>
      <div class="stack">
        <section class="card">
          <h2>Injecter</h2>
          <div class="cols mt">
            <div class="stack">
              <div class="dropzone" id="dropzone" role="button" tabindex="0">
                <div>Déposez des fichiers ici ou cliquez pour choisir</div>
                <div class="small muted">PDF, DOCX, MD, TXT. Un envoi par fichier.</div>
              </div>
              <input type="file" id="file-input" multiple hidden>
              <ul class="upload-list" id="upload-list"></ul>
            </div>
            <form class="stack" id="text-form" novalidate>
              <div class="field">
                <label for="text-input">Texte</label>
                <textarea id="text-input"></textarea>
                <p class="error" id="text-error"></p>
              </div>
              <div class="field">
                <label for="source-input">Source</label>
                <input type="text" id="source-input" class="mono" autocomplete="off">
                <div class="help">Nom affiché dans la liste des documents. Facultatif.</div>
              </div>
              <div><button type="submit" class="btn" id="text-submit">Injecter le texte</button></div>
            </form>
          </div>
          <div class="card-foot"><button type="button" class="btn ghost" data-action="scan">Scanner le dossier d'entrée</button></div>
        </section>
        <section class="card">
          <h2>File de traitement</h2>
          <div class="stack mt" data-region="pipeline"></div>
        </section>
        <section class="card flush">
          <div class="list-head"><div class="filters" data-region="doc-filters"></div></div>
          <div class="mt" data-region="doc-table"></div>
          <div data-region="doc-pager"></div>
        </section>
      </div>`;
    bindDocuments(el);
  }

  function bindDocuments(el) {
    const dz = el.querySelector('#dropzone');
    const picker = el.querySelector('#file-input');
    dz.addEventListener('click', () => picker.click());
    dz.addEventListener('keydown', ev => {
      if (ev.key === 'Enter' || ev.key === ' ') {
        ev.preventDefault();
        picker.click();
      }
    });
    dz.addEventListener('dragover', ev => {
      ev.preventDefault();
      dz.classList.add('over');
    });
    dz.addEventListener('dragleave', () => dz.classList.remove('over'));
    dz.addEventListener('drop', ev => {
      ev.preventDefault();
      dz.classList.remove('over');
      if (ev.dataTransfer && ev.dataTransfer.files.length) uploadFiles(Array.from(ev.dataTransfer.files));
    });
    picker.addEventListener('change', () => {
      const files = Array.from(picker.files || []);
      picker.value = '';
      if (files.length) uploadFiles(files);
    });
    el.querySelector('#text-form').addEventListener('submit', ev => {
      ev.preventDefault();
      injectText();
    });
    el.querySelector('#text-input').addEventListener('input', () => {
      el.querySelector('#text-error').textContent = '';
    });
  }

  function setTag(node, cls, text) {
    node.className = cls;
    node.textContent = text;
  }

  async function uploadFiles(files) {
    const c = currentCorpus();
    const list = $('#upload-list');
    if (!c || !list) return;
    for (const file of files) {
      const li = document.createElement('li');
      const name = document.createElement('div');
      name.className = 'grow';
      name.textContent = file.name;
      const note = document.createElement('div');
      note.className = 'small muted';
      name.append(note);
      const tag = document.createElement('span');
      setTag(tag, 'chip info', 'envoi…');
      li.append(name, tag);
      list.append(li);
      const form = new FormData();
      form.append('file', file, file.name);
      try {
        const r = (await lr(c, '/documents/upload', { method: 'POST', body: form })) || {};
        if (r.status === 'success') setTag(tag, 'chip ok', 'accepté');
        else if (r.status === 'duplicated') setTag(tag, 'chip warn', 'doublon');
        else setTag(tag, 'chip bad', 'échec');
        note.textContent = r.message || '';
      } catch (e) {
        setTag(tag, 'chip bad', 'échec');
        note.textContent = e.message;
      }
    }
    loadDocs();
  }

  async function injectText() {
    const c = currentCorpus();
    const textEl = $('#text-input');
    const srcEl = $('#source-input');
    const errEl = $('#text-error');
    const btn = $('#text-submit');
    if (!c || !textEl) return;
    if (!textEl.value.trim()) {
      errEl.textContent = 'Le texte est vide.';
      textEl.focus();
      return;
    }
    btn.disabled = true;
    try {
      const r = (await lr(c, '/documents/text', {
        method: 'POST',
        body: { text: textEl.value, file_source: srcEl.value.trim() },
      })) || {};
      textEl.value = '';
      srcEl.value = '';
      toast(r.message, r.status === 'success' ? 'ok' : (r.status === 'failure' ? 'bad' : ''));
    } catch (e) {
      toast(e.message, 'bad');
    } finally {
      btn.disabled = false;
    }
    loadDocs();
  }

  // Scan, reprocess and cancel: a POST without body, then the returned message as a toast.
  async function docsCommand(path, btn) {
    const c = currentCorpus();
    if (!c) return;
    if (btn) btn.disabled = true;
    try {
      const r = (await lr(c, path, { method: 'POST' })) || {};
      toast(r.message, '');
    } catch (e) {
      toast(e.message, 'bad');
    } finally {
      if (btn) btn.disabled = false;
    }
    loadDocs();
  }

  async function cancelPipeline() {
    const ok = await confirmDialog({
      title: 'Annuler le traitement ?',
      text: "Le traitement en cours est interrompu sur l'instance.",
      ok: 'Annuler le traitement',
      danger: true,
    });
    if (ok) docsCommand('/documents/cancel_pipeline');
  }

  function setFilter(value) {
    state.docs.filter = value || null;
    state.docs.page = 1;
    renderDocsRegions();
    loadDocs();
  }

  function changePage(dir) {
    const d = state.docs;
    const total = d.data && d.data.pagination && d.data.pagination.total_pages;
    const next = d.page + dir;
    d.page = Math.max(1, total ? Math.min(total, next) : next);
    loadDocs();
  }

  function renderDocsRegions() {
    const d = state.docs;
    setRegion('pipeline', pipelineHtml(d));
    setRegion('doc-filters', filtersHtml(d.counts));
    setRegion('doc-table', tableHtml(d));
    setRegion('doc-pager', pagerHtml(d));
  }

  function pipelineHtml(d) {
    const banner = d.error ? alertBox('bad', esc(d.error)) : '';
    const p = d.pipeline;
    if (!p) return banner || skeleton(2);
    const counts = d.counts || {};
    const history = (p.history_messages || []).slice(-6);
    const actions = [];
    if (p.busy) actions.push('<button type="button" class="btn danger sm" data-action="cancel">Annuler le traitement</button>');
    if ((counts.failed || 0) > 0) actions.push('<button type="button" class="btn ghost sm" data-action="reprocess">Relancer les échecs</button>');
    return `${banner}
      <div class="row">${p.busy ? chip('en cours', 'warn') : chip('au repos')}${p.busy
        ? `<span class="small">${esc(p.job_name)} : lot ${esc(p.cur_batch)} sur ${esc(p.batchs)}</span>` : ''}</div>
      ${p.latest_message ? `<p class="small muted">${esc(p.latest_message)}</p>` : ''}
      ${history.length ? `<div class="log">${history.map(m => `<div>${esc(m)}</div>`).join('')}</div>` : ''}
      ${actions.length ? `<div class="row">${actions.join('')}</div>` : ''}`;
  }

  function filtersHtml(counts) {
    const current = state.docs.filter || '';
    return FILTERS.map(f => `<button type="button" data-action="filter" data-value="${esc(f.value)}" aria-pressed="${current === f.value}">${esc(f.label)} <span class="num">${fmtInt(counts ? counts[f.key] : null)}</span></button>`).join('');
  }

  function docStatus(status) {
    const m = DOC_STATUS[status];
    return m ? chip(m[0], m[1]) : chip(status || '-');
  }

  function docRow(x) {
    const text = String(x.content_summary || '');
    const summary = text.length > SUMMARY_MAX ? text.slice(0, SUMMARY_MAX) + '…' : text;
    return `<tr>
      <td><div class="doc-name">${esc(x.file_path || x.id)}</div>${summary ? `<div class="sub">${esc(summary)}</div>` : ''}${x.error_msg ? `<div class="err">${esc(x.error_msg)}</div>` : ''}</td>
      <td>${docStatus(x.status)}</td>
      <td class="right">${fmtInt(x.chunks_count)}</td>
      <td class="right">${fmtInt(x.content_length)}</td>
      <td class="right">${fmtDate(x.updated_at)}</td>
    </tr>`;
  }

  function tableHtml(d) {
    const banner = d.error ? `<div class="pad">${alertBox('bad', esc(d.error))}</div>` : '';
    if (!d.data) return banner || `<div class="pad">${skeleton(5)}</div>`;
    const docs = d.data.documents || [];
    if (!docs.length) {
      return banner + '<div class="empty"><b>Aucun document</b>Injectez un fichier ou un texte ci-dessus.</div>';
    }
    return banner + `<div class="table-scroll"><table>
      <thead><tr><th>Document</th><th>État</th><th class="right">Chunks</th><th class="right" title="En caractères">Taille</th><th class="right">Mis à jour</th></tr></thead>
      <tbody>${docs.map(docRow).join('')}</tbody>
    </table></div>`;
  }

  function pagerHtml(d) {
    const p = d.data && d.data.pagination;
    if (!p) return '';
    return `<div class="pager">
      <span class="small muted">${fmtInt(p.total_count)} documents</span>
      <div class="row">
        <span class="small muted">Page ${fmtInt(p.page)} sur ${fmtInt(p.total_pages)}</span>
        <button type="button" class="btn ghost sm" data-action="page" data-dir="-1"${p.has_prev ? '' : ' disabled'}>Précédent</button>
        <button type="button" class="btn ghost sm" data-action="page" data-dir="1"${p.has_next ? '' : ' disabled'}>Suivant</button>
      </div>
    </div>`;
  }

  function mountQuery(el) {
    const c = currentCorpus();
    const modes = QUERY_MODES.map(([v, label]) => `<option value="${v}"${v === 'mix' ? ' selected' : ''}>${esc(label)}</option>`).join('');
    el.innerHTML = `
      <div class="page-head"><div><h1>Requête</h1><p>${esc(c.label)} : interroger le corpus pour vérifier ce que le retrieval renvoie.</p></div></div>
      <div class="stack">
        <form class="card stack" id="query-form" novalidate>
          <div class="field">
            <label for="q-text">Question</label>
            <textarea id="q-text"></textarea>
            <p class="error" id="q-error"></p>
          </div>
          <div class="row">
            <div class="field grow">
              <label for="q-mode">Mode</label>
              <select id="q-mode">${modes}</select>
            </div>
            <div class="field narrow">
              <label for="q-topk">Résultats (top_k)</label>
              <input type="number" id="q-topk" min="1" max="200" step="1" value="40">
            </div>
          </div>
          <label class="check"><input type="checkbox" id="q-ctx"> Contexte seul, sans réponse rédigée</label>
          <div><button type="submit" class="btn" id="q-submit">Interroger</button></div>
        </form>
        <section class="card" id="q-answer">
          <div class="empty"><b>Aucune requête</b>Posez une question pour tester le corpus.</div>
        </section>
      </div>`;
    el.querySelector('#query-form').addEventListener('submit', ev => {
      ev.preventDefault();
      runQuery();
    });
  }

  async function runQuery() {
    const c = currentCorpus();
    const text = $('#q-text');
    const errEl = $('#q-error');
    const btn = $('#q-submit');
    const box = $('#q-answer');
    if (!c || !text || !box) return;
    const query = text.value.trim();
    if (!query) {
      errEl.textContent = 'La question est vide.';
      text.focus();
      return;
    }
    errEl.textContent = '';
    const mode = $('#q-mode').value;
    const topK = clampInt($('#q-topk').value, 1, 200, 40);
    const contextOnly = $('#q-ctx').checked;
    btn.disabled = true;
    btn.textContent = 'Recherche…';
    box.innerHTML = skeleton(3);
    const t0 = performance.now();
    try {
      const r = (await lr(c, '/query', {
        method: 'POST',
        body: { query, mode, top_k: topK, only_need_context: contextOnly, include_references: true },
      })) || {};
      const secs = ((performance.now() - t0) / 1000).toFixed(1).replace('.', ',');
      renderAnswer(box, r, mode, secs);
    } catch (e) {
      box.innerHTML = alertBox('bad', esc(e.message));
    } finally {
      btn.disabled = false;
      btn.textContent = 'Interroger';
    }
  }

  function renderAnswer(box, r, mode, secs) {
    box.innerHTML = `
      <div class="answer" id="q-response"></div>
      <p class="small muted mt">Réponse en ${secs} s, mode ${esc(mode)}</p>
      <h3 class="mt">Sources</h3>
      <div class="mt" id="q-sources"></div>`;
    box.querySelector('#q-response').textContent = r.response || '';
    const refs = Array.isArray(r.references) ? r.references : [];
    const out = box.querySelector('#q-sources');
    if (!refs.length) {
      out.innerHTML = '<p class="muted small">Aucune source renvoyée.</p>';
      return;
    }
    const list = document.createElement('div');
    list.className = 'log';
    refs.forEach(ref => {
      const line = document.createElement('div');
      line.className = 'mono';
      line.textContent = ref.file_path || ref.reference_id || '';
      list.append(line);
    });
    out.append(list);
  }

  // -- models (calls to this service; the instance is changed only through it) -
  function mountModels(el) {
    const c = currentCorpus();
    const emb = c.embedder.configured || {};
    el.innerHTML = `
      <div class="page-head"><div><h1>Modèles</h1><p>${esc(c.label)} : modèle de langage, endpoint et embedder de l'instance.</p></div></div>
      <div class="stack">
        <section class="card" data-region="job" hidden></section>
        <section class="card">
          <h2>Modèle de langage</h2>
          <div class="mt" data-region="llm-status"></div>
          <div class="stack mt">
            <div class="field">
              <label for="llm-endpoint">Endpoint</label>
              <div class="row">
                <input type="text" id="llm-endpoint" class="mono grow" autocomplete="off" value="${esc(c.engine.endpoint.effective || '')}">
                <button type="button" class="btn ghost" data-action="probe">Lister les modèles</button>
              </div>
              <div class="help">URL de base compatible OpenAI, en général terminée par /v1. Réseau privé uniquement.</div>
              <p class="error" id="probe-error"></p>
              <p class="help" id="probe-result"></p>
            </div>
            <div class="field">
              <label for="llm-model">Modèle</label>
              <select id="llm-model" class="mono"></select>
            </div>
            <label class="check"><input type="checkbox" id="llm-force"> Appliquer même si le modèle n'est pas chargé</label>
            <div class="row" data-region="llm-apply"></div>
          </div>
        </section>
        <section class="card">
          <h2>Embedder</h2>
          <div class="mt" data-region="emb-status"></div>
          <div class="stack mt">
            <div class="row">
              <div class="field grow">
                <label for="emb-url">Endpoint</label>
                <input type="text" id="emb-url" class="mono" autocomplete="off" value="${esc(emb.url || '')}">
              </div>
              <div class="field grow">
                <label for="emb-model">Modèle</label>
                <input type="text" id="emb-model" class="mono" autocomplete="off" value="${esc(emb.model || '')}">
              </div>
            </div>
            <div class="row" data-region="emb-apply"></div>
            <div class="help" data-region="emb-help"></div>
          </div>
        </section>
        <section class="card">
          <h2>Index</h2>
          <div class="mt" data-region="index-status"></div>
          <div class="row mt" data-region="index-actions"></div>
          <p class="small muted mt">LightRAG n'enregistre pas le modèle qui a construit un index. Cet historique est tenu par Coeos-RAG.</p>
        </section>
      </div>`;
    const ready = c.engine.ready_models;
    fillSelect(
      el.querySelector('#llm-model'),
      (ready || []).map(id => ({ id, ready: true })),
      currentModel(c),
      ready ? false : null,
    );
  }

  function versionAlert(version) {
    const m = /^(\d+)\.(\d+)/.exec(version || '');
    if (!m) return '';
    const major = Number(m[1]);
    const minor = Number(m[2]);
    if (major > 1 || (major === 1 && minor >= 5)) return '';
    return alertBox('info', `Cette instance (LightRAG ${esc(version)}) utilise un seul modèle pour l'extraction des documents et pour la requête. Les modèles séparés par rôle existent à partir de LightRAG 1.5.`);
  }

  function llmStatusHtml(c) {
    const l = c.llm;
    const e = c.engine;
    const sync = l.in_sync === false ? ' ' + chip('configuré ≠ en service', 'warn') : '';
    const reach = e.reachable ? chip('joignable', 'ok') : chip('injoignable', 'bad');
    const st = l.ready === true ? chip('prêt', 'ok')
      : l.ready === false ? chip('non chargé', 'bad') : chip('inconnu');
    const ver = versionAlert(c.instance.version);
    return `<dl class="kv">
      <dt>En service</dt><dd class="mono">${esc(l.live || '-')}</dd>
      <dt>Configuré</dt><dd><span class="mono">${esc(l.configured || '-')}</span>${sync}</dd>
      <dt>Endpoint</dt><dd><span class="mono">${esc(e.endpoint.effective || '-')}</span> ${reach}</dd>
      <dt>État</dt><dd>${st}</dd>
    </dl>${ver ? `<div class="mt">${ver}</div>` : ''}`;
  }

  function embStatusHtml(c) {
    const m = c.embedder;
    const live = m.live || {};
    const st = m.alive
      ? `en service, ${esc(Number(m.latency_ms).toLocaleString('fr-FR'))} ms, ${esc(m.dim)} dimensions ${spaceChip(c)}`
      : `ne répond pas : ${esc(m.error || '-')}`;
    return `<dl class="kv">
      <dt>État</dt><dd>${st}</dd>
      <dt>Poids</dt><dd class="mono">${esc(m.weights || '-')}</dd>
      <dt>En service</dt><dd class="mono">${esc(live.url || '-')} &middot; ${esc(live.model || '-')}</dd>
    </dl>`;
  }

  function indexStatusHtml(c) {
    const e = c.indexed_with.embedder;
    const cert = e.certified;
    const certHtml = cert
      ? `${chip('certifié', 'ok')} <span class="muted">médiane ${esc(cert.median_cos)}, minimum ${esc(cert.min_cos)} sur ${esc(cert.samples)} chunks, le ${fmtDate(cert.at)}</span>`
      : chip('non certifié', 'warn');
    const eras = c.indexed_with.llm_eras || [];
    const table = eras.length
      ? `<div class="table-scroll mt"><table>
          <thead><tr><th>Modèle d'extraction</th><th>Période</th><th class="right">Documents</th><th>Source</th></tr></thead>
          <tbody>${eras.map(x => `<tr>
            <td class="mono">${esc(x.model)}</td>
            <td>${fmtDate(x.start)} → ${x.end ? fmtDate(x.end) : 'maintenant'}</td>
            <td class="right">${fmtInt(x.docs)}</td>
            <td class="muted">${esc(x.source)}</td>
          </tr>`).join('')}</tbody>
        </table></div>`
      : '<p class="muted mt">Aucun historique enregistré.</p>';
    return `<dl class="kv">
      <dt>Embedder d'origine</dt><dd><span class="mono">${esc(e.weights || 'poids inconnus')}</span> · ${esc(e.dim)} dimensions</dd>
      <dt>Espace vectoriel</dt><dd>${certHtml}</dd>
    </dl>${table}`;
  }

  function indexActionsHtml(c, mut) {
    const dis = mut ? '' : ' disabled';
    const start = !c.instance.loaded && !c.instance.up
      ? `<button type="button" class="btn" data-action="start"${dis}>Démarrer l'instance</button>` : '';
    return `<button type="button" class="btn ghost" data-action="certify"${dis}>Certifier l'index</button>${start}`;
  }

  function jobHtml() {
    const j = state.job;
    if (!j) return '';
    const t0 = j.started || 0;
    const steps = (j.steps || []).map(s => {
      const at = t0 ? Math.max(0, Math.round(Number(s.t) - t0)) : 0;
      return `<li><span class="t">+${at}s</span><span class="${s.ok ? 'ok' : 'ko'}">${s.ok ? '✓' : '✕'}</span><span>${esc(s.step)} <span class="muted">${esc(s.detail)}</span></span></li>`;
    }).join('');
    const status = j.status === 'running' ? chip('en cours', 'info')
      : j.status === 'done' ? chip('terminé', 'ok') : chip('échec', 'bad');
    const what = ACTION_LABELS[j.kind] || j.kind || 'Action';
    const where = j.corpus ? ` sur ${corpusLabel(j.corpus)}` : '';
    const failure = j.status === 'failed' && j.error ? alertBox('bad', esc(j.error)) : '';
    return `<h2>${esc(what)}${esc(where)} ${status}</h2>
      ${steps ? `<ul class="steps mt">${steps}</ul>` : ''}
      ${failure ? `<div class="mt">${failure}</div>` : ''}`;
  }

  function updateModels() {
    const c = currentCorpus();
    const mut = canMutate();
    const certified = !!c.indexed_with.embedder.certified;
    const job = jobHtml();
    setRegion('job', job);
    const jobCard = regionEl('job');
    if (jobCard) jobCard.hidden = !job;
    setRegion('llm-status', llmStatusHtml(c));
    setRegion('llm-apply', `<button type="button" class="btn" data-action="apply-llm"${mut ? '' : ' disabled'}>Appliquer</button>`);
    setRegion('emb-status', embStatusHtml(c));
    setRegion('emb-apply', `<button type="button" class="btn" data-action="apply-emb"${mut && certified ? '' : ' disabled'}>Changer d'embedder</button>`);
    setRegion('emb-help', certified
      ? "Le nouvel endpoint doit produire le même espace vectoriel que l'index (vérifié par cosinus). Un autre espace exige une ré-indexation et est refusé."
      : "Certifiez d'abord l'index : son espace vectoriel doit être connu avant de changer d'embedder.");
    setRegion('index-status', indexStatusHtml(c));
    setRegion('index-actions', indexActionsHtml(c, mut));
  }

  async function probeEndpoint() {
    const c = currentCorpus();
    const errEl = $('#probe-error');
    const resEl = $('#probe-result');
    const sel = $('#llm-model');
    if (!c || !errEl) return;
    errEl.textContent = '';
    resEl.textContent = '';
    if (!(await ensureToken())) return;
    try {
      const r = await api('/api/endpoint/models', {
        method: 'POST',
        body: { url: $('#llm-endpoint').value.trim() },
      });
      if (!r.reachable) {
        errEl.textContent = 'Endpoint injoignable.';
        return;
      }
      const models = r.models || [];
      resEl.textContent = `${models.length} modèles publiés, ${models.filter(m => m.ready).length} prêts`;
      fillSelect(sel, models.map(m => ({ id: m.id, ready: !!m.ready })), currentModel(c), false);
    } catch (e) {
      if (e.status === 401) {
        clearToken();
        toast('Jeton refusé', 'bad');
      } else {
        errEl.textContent = e.message;
      }
    }
  }

  function applyLlm() {
    const c = currentCorpus();
    if (!c) return;
    const model = $('#llm-model').value;
    const typed = $('#llm-endpoint').value.trim().replace(/\/+$/, '');
    const current = (c.engine.endpoint.effective || '').replace(/\/+$/, '');
    const endpoint = typed && typed !== current ? typed : null;
    const force = $('#llm-force').checked;
    mutate(`/api/corpora/${encodeURIComponent(c.id)}/llm`, { model, endpoint, force }, {
      title: 'Appliquer ce réglage ?',
      text: `${c.label} redémarre (environ une minute). Le changement est refusé pendant une extraction, et annulé automatiquement si l'instance ne revient pas saine.`,
      ok: 'Appliquer',
    });
  }

  function applyEmbedder() {
    const c = currentCorpus();
    if (!c) return;
    const url = $('#emb-url').value.trim();
    const model = $('#emb-model').value.trim();
    mutate(`/api/corpora/${encodeURIComponent(c.id)}/embedder`, { url, model }, {
      title: "Changer d'embedder ?",
      text: `${c.label} redémarre. Le nouvel embedder n'est accepté que s'il produit le même espace vectoriel que l'index (vérifié par cosinus).`,
      ok: "Changer d'embedder",
    });
  }

  function certifyIndex() {
    const c = currentCorpus();
    if (!c) return;
    mutate(`/api/corpora/${encodeURIComponent(c.id)}/certify`, null, {
      title: "Certifier l'index ?",
      text: "Recalcule un échantillon de chunks stockés avec l'embedder de l'instance et le compare aux vecteurs stockés. Rien n'est redémarré.",
      ok: 'Certifier',
    });
  }

  function startInstance() {
    const c = currentCorpus();
    if (!c) return;
    mutate(`/api/corpora/${encodeURIComponent(c.id)}/start`, null, {
      title: "Démarrer l'instance ?",
      text: `${c.label} : l'instance arrêtée est relancée.`,
      ok: 'Démarrer',
    });
  }

  // Confirmation, then token if missing, then POST, then follow the job.
  async function mutate(path, body, ask) {
    if (!(await confirmDialog(ask))) return;
    if (!(await ensureToken())) return;
    let started;
    try {
      started = await api(path, { method: 'POST', body: body === null ? undefined : body });
    } catch (e) {
      failMutation(e);
      return;
    }
    state.job = { id: started.job_id, status: 'running', kind: '', corpus: '', started: 0, steps: [], result: null, error: null };
    render();
    trackJob(started.job_id);
  }

  function failMutation(e) {
    if (e.status === 401) {
      clearToken();
      toast('Jeton refusé', 'bad');
      return;
    }
    toast(e.message, 'bad');
  }

  async function trackJob(id) {
    for (;;) {
      let job;
      try {
        job = await api(`/api/jobs/${encodeURIComponent(id)}`);
      } catch (e) {
        state.job = Object.assign({}, state.job, { id, status: 'failed', error: `Suivi du job impossible : ${e.message}` });
        render();
        break;
      }
      state.job = job;
      render();
      if (job.status !== 'running') break;
      await sleep(JOB_MS);
    }
    await loadOverview();
    loadActions();
  }

  // -- journal -----------------------------------------------------------------
  function mountJournal(el) {
    state.actions = { data: null, error: null };
    el.innerHTML = `
      <div class="page-head"><div><h1>Journal</h1><p>Les changements appliqués par Coeos-RAG.</p></div></div>
      <section class="card flush"><div data-region="actions"></div></section>`;
  }

  function journalHtml() {
    const a = state.actions;
    const banner = a.error ? `<div class="pad">${alertBox('bad', esc(a.error))}</div>` : '';
    if (!a.data) return banner || `<div class="pad">${skeleton(4)}</div>`;
    if (!a.data.length) {
      return banner + '<div class="empty"><b>Aucune action</b>Les changements de modèle, d\'endpoint et d\'embedder apparaîtront ici.</div>';
    }
    const rows = a.data.map(x => `<tr>
      <td>${fmtDate(x.ts)}</td>
      <td>${esc(corpusLabel(x.corpus))}</td>
      <td>${esc(ACTION_LABELS[x.kind] || x.kind || '-')}</td>
      <td>${x.status === 'done' ? chip('terminé', 'ok') : chip('échec', 'bad')}${x.error ? `<div class="sub">${esc(x.error)}</div>` : ''}</td>
    </tr>`).join('');
    return banner + `<div class="table-scroll"><table>
      <thead><tr><th>Date</th><th>Corpus</th><th>Action</th><th>Résultat</th></tr></thead>
      <tbody>${rows}</tbody>
    </table></div>`;
  }

  function updateJournal() {
    setRegion('actions', journalHtml());
  }

  async function loadActions() {
    try {
      state.actions = { data: await api('/api/actions'), error: null };
    } catch (e) {
      state.actions = { data: state.actions.data, error: e.message };
    }
    if (state.section === 'journal') updateJournal();
  }

  // -- polling and loading -----------------------------------------------------
  async function loadOverview() {
    try {
      state.overview = await api('/api/overview');
      state.overviewError = null;
      const list = corpusList();
      if (!list.some(c => c.id === state.corpusId)) {
        state.corpusId = list.length ? list[0].id : null;
        storeSet('localStorage', CORPUS_KEY, state.corpusId);
      }
      const updated = $('#updated');
      if (updated) updated.textContent = `Mis à jour à ${fmtClock(new Date())}`;
    } catch (e) {
      state.overviewError = e.message;
    }
    render();
  }

  // The overview refresh is suspended while a job of this service runs.
  async function overviewTick() {
    if (!jobRunning()) await loadOverview();
    setTimeout(overviewTick, OVERVIEW_MS);
  }

  // -- topbar, nav, page error -------------------------------------------------
  function renderTopbar() {
    const html = corpusList().map(c => `<button type="button" role="tab" data-action="corpus" data-id="${esc(c.id)}" aria-selected="${c.id === state.corpusId}">${esc(c.label)} ${c.instance.up ? chip('en ligne', 'ok') : chip('arrêtée', 'bad')}</button>`).join('');
    setHtml($('#corpus-switch'), html);
    const tokenBtn = $('#token-btn');
    if (tokenBtn) tokenBtn.textContent = state.token ? 'Jeton enregistré' : 'Jeton';
  }

  function renderNav() {
    document.querySelectorAll('#nav a').forEach(a => {
      if (a.dataset.section === state.section) a.setAttribute('aria-current', 'page');
      else a.removeAttribute('aria-current');
    });
  }

  function renderPageError() {
    setRegion('page-error', state.overviewError
      ? alertBox('bad', `<strong>Coeos-RAG ne répond pas.</strong> ${esc(state.overviewError)}`)
      : '');
  }

  // -- sections ----------------------------------------------------------------
  const SECTIONS = {
    overview: { mount: mountOverview, update: updateOverview },
    documents: { mount: mountDocuments, update: renderDocsRegions },
    query: { mount: mountQuery, update: () => {} },
    models: { mount: mountModels, update: updateModels },
    journal: { mount: mountJournal, update: updateJournal },
  };

  // -- routing and rendering ---------------------------------------------------
  // mount() runs once per navigation, corpus change or first overview load.
  // update() runs on every render and rewrites only data-region elements.
  let mountKey = '';

  function loadingPage() {
    return `<div class="cols">${cardSkeleton()}${cardSkeleton()}</div>`;
  }

  function noCorpusPage() {
    return '<div class="card empty"><b>Aucun corpus</b>Aucun corpus n\'est enregistré sur ce service.</div>';
  }

  function render() {
    renderTopbar();
    renderNav();
    renderPageError();
    const page = $('#page');
    if (!page) return;
    const scoped = state.section === 'documents' || state.section === 'query' || state.section === 'models';
    if (!state.overview && state.section !== 'journal') {
      if (mountKey !== 'loading') {
        mountKey = 'loading';
        stopDocsLoop();
        page.innerHTML = loadingPage();
      }
      return;
    }
    const key = [state.section, scoped ? state.corpusId : '', state.overview ? 'data' : 'none'].join('|');
    if (key !== mountKey) {
      mountKey = key;
      stopDocsLoop();
      const noCorpus = scoped && !currentCorpus();
      if (noCorpus) page.innerHTML = noCorpusPage();
      else SECTIONS[state.section].mount(page);
      if (!noCorpus && state.section === 'documents') startDocsLoop();
      if (state.section === 'journal') loadActions();
    }
    if (scoped && !currentCorpus()) return;
    SECTIONS[state.section].update();
  }

  function route() {
    const m = /^#\/([a-z]+)$/.exec(location.hash);
    state.section = m && SECTION_KEYS.includes(m[1]) ? m[1] : 'overview';
    render();
  }

  function selectCorpus(id) {
    if (!id || id === state.corpusId) return;
    state.corpusId = id;
    storeSet('localStorage', CORPUS_KEY, id);
    render();
  }

  function goTo(section, id) {
    if (id && id !== state.corpusId) {
      state.corpusId = id;
      storeSet('localStorage', CORPUS_KEY, id);
    }
    if (location.hash === `#/${section}`) render();
    else location.hash = `#/${section}`;
  }

  // -- events ------------------------------------------------------------------
  function onClick(ev) {
    const b = ev.target.closest('[data-action]');
    if (!b || b.disabled) return;
    switch (b.dataset.action) {
      case 'token': askToken(); break;
      case 'corpus': selectCorpus(b.dataset.id); break;
      case 'go': goTo(b.dataset.section, b.dataset.id); break;
      case 'filter': setFilter(b.dataset.value); break;
      case 'page': changePage(Number(b.dataset.dir)); break;
      case 'scan': docsCommand('/documents/scan', b); break;
      case 'cancel': cancelPipeline(); break;
      case 'reprocess': docsCommand('/documents/reprocess_failed'); break;
      case 'probe': probeEndpoint(); break;
      case 'apply-llm': applyLlm(); break;
      case 'apply-emb': applyEmbedder(); break;
      case 'certify': certifyIndex(); break;
      case 'start': startInstance(); break;
      default: break;
    }
  }

  function boot() {
    state.token = storeGet('sessionStorage', TOKEN_KEY);
    state.corpusId = storeGet('localStorage', CORPUS_KEY) || null;
    $('#nav').innerHTML = SECTION_KEYS
      .map(k => `<a href="#/${k}" data-section="${k}">${esc(SECTION_TITLES[k])}</a>`).join('');
    $('#main').innerHTML = '<div data-region="page-error"></div><div id="page"></div>';
    document.addEventListener('click', onClick);
    window.addEventListener('hashchange', () => {
      route();
      $('#main').focus({ preventScroll: true });
      window.scrollTo(0, 0);
    });
    route();
    overviewTick();
  }

  boot();
})();
