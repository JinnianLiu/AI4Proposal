/* AI4Proposal — front-end state machine (Alpine).
 *
 * Six steps: upload -> confirm parse -> direction -> topic -> generating -> result.
 * Everything the server knows is fetched; nothing here is persisted except the
 * id of a running job (see RESUME below).
 */

// A run takes 10-15 minutes, and losing the page used to lose sight of it: the
// job kept going and kept writing to outputs/_web/<id>/, but the browser had no
// way back and the finished proposal sat on disk unnoticed. The id now survives
// a reload in both the URL and localStorage — the URL so the tab can be shared
// or restored by the browser, localStorage so a plain reload of "/" still finds
// it. This does NOT survive a server restart: job state lives in the server's
// memory, so a restarted server answers 404 and we clear the stored id.
const RESUME_KEY = 'ai4proposal.job';

function loadResume() {
  const fromHash = new URLSearchParams(location.hash.slice(1)).get('job');
  try {
    const saved = JSON.parse(localStorage.getItem(RESUME_KEY) || 'null');
    if (fromHash) return saved && saved.id === fromHash ? saved : { id: fromHash };
    return saved && saved.id ? saved : null;
  } catch (e) {
    return fromHash ? { id: fromHash } : null;
  }
}

function saveResume(entry) {
  try { localStorage.setItem(RESUME_KEY, JSON.stringify(entry)); } catch (e) { /* private mode */ }
  history.replaceState(null, '', '#job=' + entry.id);
}

function clearResume() {
  try { localStorage.removeItem(RESUME_KEY); } catch (e) { /* ignore */ }
  history.replaceState(null, '', location.pathname);
}

// Three states, not two: "system" stamps nothing and lets prefers-color-scheme
// decide, which is what most visitors want and what the page loads with. An
// explicit choice stamps data-theme on <html> and outranks the OS in both
// directions. The initial read happens in an inline script in <head> — doing it
// here would paint one theme and then swap.
const THEME_KEY = 'ai4proposal.theme';
const THEME_ORDER = ['system', 'light', 'dark'];
const THEME_LABEL = { system: '主题：跟随系统', light: '主题：浅色', dark: '主题：深色' };

function readTheme() {
  try {
    const t = localStorage.getItem(THEME_KEY);
    return THEME_ORDER.includes(t) ? t : 'system';
  } catch (e) { return 'system'; }
}

function applyTheme(theme) {
  if (theme === 'system') delete document.documentElement.dataset.theme;
  else document.documentElement.dataset.theme = theme;
  try {
    if (theme === 'system') localStorage.removeItem(THEME_KEY);
    else localStorage.setItem(THEME_KEY, theme);
  } catch (e) { /* private mode */ }
}

function agent() {
  return {
    steps: ['上传文件', '核对解析', '选择方向', '确定课题', '生成', '完成'],
    dims: [{ k: 'scientific_quality', name: '科学质量', w: .20 },
           { k: 'innovation',         name: '创新性',   w: .18 },
           { k: 'feasibility',        name: '可行性',   w: .16 },
           { k: 'alignment',          name: '一致性',   w: .14 },
           { k: 'impact',             name: '学术影响', w: .12 },
           { k: 'compliance',         name: '规范性',   w: .11 },
           { k: 'clarity',            name: '清晰度',   w: .09 }],
    cfg: { debug: false, stages: [], model: '' },
    // The landing page is not step 0. It shows what the system produces and has
    // no upload box on it; the wizard begins only once someone asks for it, so
    // `home` gates the whole wizard rather than being its first screen.
    home: true,
    step: 0, busy: false, err: '', dragging: false,
    pending: [], call: null, structure: null, uploadedTopic: null,
    dirQuery: '', topicMode: 'ai', guidance: '', showPaste: false, pasted: '',
    own: { title: '', background: '', challengesText: '' },
    direction: null, topics: [], topic: null,
    job: null, stage: '', result: null, tab: 'doc',
    t0: 0, elapsed: '0:00', timer: null, resuming: false,
    theme: readTheme(),
    samples: [], sampleId: null, lightbox: null, copied: false,
    progress: null, draft: null,

    async boot() {
      try { this.cfg = await (await fetch('/api/config')).json(); } catch (e) { /* keep defaults */ }
      try { this.samples = (await (await fetch('/api/samples')).json()).samples || []; }
      catch (e) { this.samples = []; }
      await this.tryResume();
    },

    // ── navigation ─────────────────────────────────────────────────────────
    enterWizard() {
      this.home = false; this.step = 0; this.err = '';
      window.scrollTo({ top: 0, behavior: 'instant' });
    },
    goHome() { this.reset(); },

    // ── theme ──────────────────────────────────────────────────────────────
    cycleTheme() {
      this.theme = THEME_ORDER[(THEME_ORDER.indexOf(this.theme) + 1) % THEME_ORDER.length];
      applyTheme(this.theme);
    },
    themeLabel() { return THEME_LABEL[this.theme]; },

    // ── resume ─────────────────────────────────────────────────────────────
    async tryResume() {
      const saved = loadResume();
      if (!saved || !saved.id) return;
      this.resuming = true;
      try {
        const r = await fetch('/api/job/' + saved.id);
        if (!r.ok) { clearResume(); return; }        // server restarted, or unknown id
        const j = await r.json();
        this.job = saved.id;
        this.stage = j.stage || '';
        // The server records the title when the job starts, so a link with only
        // #job=<id> — a shared tab, a restored session — still shows what is
        // being written. localStorage is the fallback, not the source.
        this.topic = (j.title || saved.title) ? { title: j.title || saved.title } : null;
        this.t0 = saved.t0 || Date.now();
        this.home = false;
        if (j.status === 'done') {
          this.result = j.result; this.tab = 'doc'; this.step = 5;
        } else if (j.status === 'failed') {
          this.step = 4; this.err = '生成失败：\n' + (j.error || '未知错误');
        } else {
          this.step = 4;
          this.timer = setInterval(() => this.poll(), this.cfg.debug ? 800 : 3000);
          this.poll();
        }
      } catch (e) {
        clearResume();
      } finally {
        this.resuming = false;
      }
    },

    // ── intake ─────────────────────────────────────────────────────────────
    addFiles(list) { this.err = ''; for (const f of list) this.pending.push(f); },

    filteredDirs() {
      const q = this.dirQuery.trim().toLowerCase();
      const all = this.call?.directions || [];
      return q ? all.filter(d => ((d.name || '') + ' ' + (d.detail || '')).toLowerCase().includes(q)) : all;
    },

    useOwn() {
      const t = { title: this.own.title.trim(), domain: 'general',
                  background: this.own.background.trim(),
                  challenges: this.own.challengesText.split(/\r?\n/).map(s => s.trim()).filter(Boolean),
                  fit: '', source: 'own' };
      this.topics = [t, ...this.topics.filter(x => x.source !== 'own')];
      this.topic = t;
    },

    // A failed request does not always carry a JSON body — an unhandled server
    // error or a proxy returns plain text, and r.json() then throws a parse error
    // that tells the user nothing about what actually went wrong.
    async body(r, fallback) {
      const raw = await r.text();
      let j = null; try { j = JSON.parse(raw); } catch (e) { /* not JSON */ }
      if (!r.ok) throw new Error((j && j.detail) || raw.slice(0, 300) || `${fallback}（HTTP ${r.status}）`);
      if (!j) throw new Error(fallback + '：服务端返回非 JSON');
      return j;
    },

    async doUpload() {
      this.busy = true; this.err = '';
      try {
        const fd = new FormData();
        this.pending.forEach(f => fd.append('files', f));
        fd.append('pasted', this.pasted);
        const j = await this.body(await fetch('/api/upload', { method: 'POST', body: fd }), '上传失败');
        const bad = (j.files || []).filter(f => f.error);
        if (bad.length) this.err = bad.map(f => `${f.filename}：${f.error}`).join('\n');
        this.call = j.call; this.structure = j.structure; this.uploadedTopic = j.topic;
        if (!this.call) throw new Error(this.err || '未解析出资助指南');
        this.pending = []; this.step = 1;
      } catch (e) { this.err = e.message; }
      finally { this.busy = false; }
    },

    budgetText() {
      const b = this.call?.budget || {}; const bits = [];
      if (b.amount) bits.push((b.is_cap ? '上限 ' : '') + Number(b.amount).toLocaleString() + ' ' + (b.currency || ''));
      if (b.dur) bits.push('周期 ' + b.dur + ' 个月');
      return bits.join(' · ');
    },

    structureText() {
      const s = this.structure || this.call?.structure;
      return s?.core_sections?.length ? s.core_sections.map(x => x.name).join(' / ')
                                      : '指南未规定，由系统规划';
    },

    async autoDirection() {
      this.busy = true; this.err = '';
      try {
        const j = await this.body(await fetch('/api/direction', {
          method: 'POST', headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ call: this.call, mode: 'auto' }) }), '选择失败');
        this.direction = j.direction; this.topics = []; this.topic = null;
      } catch (e) { this.err = e.message; } finally { this.busy = false; }
    },

    goTopics() {
      this.step = 3;
      if (this.uploadedTopic && !this.topics.length) {
        this.topics = [this.uploadedTopic]; this.topic = this.uploadedTopic;
      }
    },

    async loadTopics() {
      this.busy = true; this.err = '';
      try {
        const j = await this.body(await fetch('/api/topics', {
          method: 'POST', headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ call: this.call, direction: this.direction, n: 3, guidance: this.guidance }) }),
          '生成失败');
        const kept = this.topics.filter(x => x.source === 'uploaded' || x.source === 'own');
        this.topics = [...kept, ...j.topics]; this.topic = null;
      } catch (e) { this.err = e.message; } finally { this.busy = false; }
    },

    // ── run ────────────────────────────────────────────────────────────────
    async start() {
      this.err = '';
      try {
        const j = await this.body(await fetch('/api/run', {
          method: 'POST', headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ call: this.call, direction: this.direction, topic: this.topic,
                                 structure: this.structure, review: true }) }), '启动失败');
        this.job = j.job_id; this.stage = 'queued'; this.step = 4; this.t0 = Date.now();
        // Persist before the first poll: a reload one second later must still find it.
        saveResume({ id: this.job, title: this.topic?.title || '', t0: this.t0 });
        this.timer = setInterval(() => this.poll(), this.cfg.debug ? 800 : 3000);
        this.poll();
      } catch (e) { this.err = e.message; }
    },

    stageState(i) {
      const ids = this.cfg.stages.map(s => s.id);
      const cur = ids.indexOf(this.stage);
      if (this.stage === 'failed') return i === Math.max(cur, 0) ? 'err' : (i < cur ? 'ok' : 'pend');
      if (this.stage === 'done') return 'ok';
      if (cur < 0) return i === 0 ? 'run' : 'pend';
      return i < cur ? 'ok' : i === cur ? 'run' : 'pend';
    },

    // ── live progress ──────────────────────────────────────────────────────
    // The writing stage is twelve of the fifteen minutes. The server parses the
    // pipeline's stdout, so this is a chapter list that fills in rather than one
    // dot that blinks for a quarter of an hour.
    chapterRows() {
      const p = this.progress || {};
      const done = p.done || [];
      const total = Math.max(p.total || 0, done.length);
      const rows = [];
      for (let i = 0; i < total; i++) {
        const d = done[i];
        rows.push({
          index: i,
          name: d ? d.name : (p.names || [])[i] || `第 ${i + 1} 章`,
          done: !!d,
          running: !d && i === done.length,
        });
      }
      return rows;
    },

    async openDraft(row) {
      if (!row.done) return;
      if (this.draft && this.draft.index === row.index) { this.draft = null; return; }
      try {
        const j = await this.body(await fetch(`/api/job/${this.job}/section/${row.index}`), '读取失败');
        this.draft = { index: row.index, name: row.name, html: j.html };
      } catch (e) { this.err = e.message; }
    },

    async poll() {
      try {
        const j = await (await fetch('/api/job/' + this.job)).json();
        this.stage = j.stage;
        this.progress = j.progress || null;
        const s = (Date.now() - this.t0) / 1000;
        this.elapsed = Math.floor(s / 60) + ':' + String(Math.floor(s % 60)).padStart(2, '0');
        if (j.status === 'failed') {
          clearInterval(this.timer);
          this.err = '生成失败：\n' + (j.error || '未知错误');
          return;
        }
        if (j.status === 'done') {
          clearInterval(this.timer);
          this.result = j.result; this.tab = 'doc'; this.step = 5;
        }
      } catch (e) { /* transient; the next tick retries */ }
    },

    // ── samples ────────────────────────────────────────────────────────────
    // A sample lands on the same result screen a real run does. Anything that
    // differs would let the showcase drift away from the product it showcases,
    // so only the URLs the screen fetches from change.
    async openSample(id) {
      this.err = '';
      try {
        const j = await this.body(await fetch('/api/sample/' + id), '示例加载失败');
        this.home = false;
        this.sampleId = id;
        this.job = null;
        this.topic = { title: j.title };
        this.result = j.result;
        this.tab = 'doc';
        this.step = 5;
        window.scrollTo({ top: 0, behavior: 'instant' });
      } catch (e) { this.err = e.message; }
    },

    figureBase() {
      return this.sampleId ? `/api/sample-figure/${this.sampleId}` : `/api/figure/${this.job}`;
    },

    // ── result ─────────────────────────────────────────────────────────────
    tabs() {
      const t = [{ id: 'doc', label: '申请书正文' }];
      if (this.result?.figures?.length) t.push({ id: 'figs', label: `配图 ${this.result.figures.length}` });
      if (this.result?.evaluation) t.push({ id: 'eval', label: '评审报告' });
      return t;
    },

    // Section navigation for the document tab. Headings carry ids because the
    // server renders with markdown's `toc` extension.
    toc() {
      const html = this.result?.html;
      if (!html) return [];
      const doc = new DOMParser().parseFromString(html, 'text/html');
      return [...doc.querySelectorAll('h2[id]')].map(h => ({ id: h.id, text: h.textContent.trim() }));
    },

    scrollToSection(id) {
      const box = this.$refs.docbox;
      const target = box?.querySelector('#' + CSS.escape(id));
      if (target) box.scrollTo({ top: target.offsetTop - box.offsetTop - 8, behavior: 'smooth' });
    },

    async copyMarkdown() {
      try {
        await navigator.clipboard.writeText(this.result?.markdown || '');
        this.copied = true;
        setTimeout(() => { this.copied = false; }, 1800);
      } catch (e) { this.err = '复制失败：浏览器拒绝剪贴板访问'; }
    },

    // The multimodal figure judge's finding for one image, matched on the id the
    // filename carries (fig_01.png -> fig_01).
    findingFor(filename) {
      const id = String(filename).replace(/\.png$/i, '');
      return (this.result?.evaluation?.figure_findings || [])
        .find(f => String(f.figure_id) === id) || null;
    },
    figVerdict(v) {
      return { good: '良好', acceptable: '尚可', partial: '部分匹配',
               mismatch: '不匹配', poor: '较差' }[v] || v || '—';
    },
    figTone(v) {
      if (v === 'good') return 'text-ok';
      if (v === 'mismatch' || v === 'poor') return 'text-bad';
      return 'text-warn';
    },

    // What actually capped the verdict. decide_verdict gates on these four
    // things, and until now the page showed the verdict without ever saying
    // which gate produced it — the one question a "修改后重投" leaves you with.
    gates() {
      const e = this.result?.evaluation || {};
      const out = [];
      (e.requirement_coverage || []).filter(c => c && c.covered === false)
        .forEach(c => out.push({ kind: '指南要求未落实', text: c.requirement || '' }));
      (e.section_checklist || []).filter(s => s && s.present === false)
        .forEach(s => out.push({ kind: '缺失章节', text: (s.section || '') + (s.note ? ' — ' + s.note : '') }));
      (e.placeholders || []).forEach(p => out.push({ kind: '占位符残留', text: String(p) }));
      const sc = e.schedule_check || {};
      if (sc.consistent === false) {
        out.push({ kind: '周期不符',
                   text: `指南规定 ${sc.required || '?'}，正文规划 ${sc.planned || '?'}` });
      }
      return out.filter(g => g.text);
    },

    // Static class strings: Tailwind cannot see class names assembled at runtime.
    verdictBadge(v) {
      const m = {
        recommend_submit: ['建议提交',     'border-ok/40 bg-ok/10 text-ok'],
        revise_resubmit:  ['修改后重投',   'border-warn/40 bg-warn/10 text-warn'],
        reject:           ['不予推荐',     'border-bad/40 bg-bad/10 text-bad'],
      };
      const [label, cls] = m[v] || [v, 'border-warn/40 bg-warn/10 text-warn'];
      return `<span class="text-[12.5px] px-3 py-1 rounded-full border ${cls}">${label}</span>`;
    },

    flags() {
      const e = this.result?.evaluation || {}; const out = [];
      [['过度声称', e.over_claims], ['跑题内容', e.off_topic], ['存疑指标', e.risky_targets],
       ['占位符残留', e.placeholders], ['违反约束', e.constraint_violations]]
        .forEach(([kind, arr]) => (arr || []).forEach(text => out.push({ kind, text })));
      return out;
    },

    // Downloads used to navigate the tab (location.href): a missing file replaced
    // the result page with FastAPI's raw JSON error, and the user had to press
    // Back to return. Fetch it instead and report failure in place.
    async dl(kind) {
      this.err = '';
      try {
        const base = this.sampleId ? `/api/sample-download/${this.sampleId}`
                                   : `/api/download/${this.job}`;
        const r = await fetch(`${base}/${kind}`);
        if (!r.ok) {
          let detail = '';
          try { detail = (await r.json()).detail || ''; } catch (e) { /* not JSON */ }
          throw new Error(detail || `下载失败（HTTP ${r.status}）`);
        }
        const blob = await r.blob();
        const name = (r.headers.get('content-disposition') || '').match(/filename\*?=(?:UTF-8'')?"?([^";]+)/i);
        const url = URL.createObjectURL(blob);
        const a = Object.assign(document.createElement('a'),
                                { href: url, download: decodeURIComponent(name?.[1] || `proposal.${kind}`) });
        document.body.appendChild(a); a.click(); a.remove();
        setTimeout(() => URL.revokeObjectURL(url), 5000);
      } catch (e) { this.err = e.message; }
    },

    reset() {
      clearInterval(this.timer);
      clearResume();
      Object.assign(this, { home: true, step: 0, pending: [], call: null, structure: null, uploadedTopic: null,
                            direction: null, topics: [], topic: null, job: null, stage: '',
                            result: null, err: '', dirQuery: '', topicMode: 'ai', guidance: '',
                            showPaste: false, pasted: '', elapsed: '0:00', sampleId: null,
                            lightbox: null, copied: false, progress: null, draft: null,
                            own: { title: '', background: '', challengesText: '' } });
      window.scrollTo({ top: 0, behavior: 'instant' });
    },
  };
}
