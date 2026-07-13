/* Lucky Studio — 수면·앰비언트 롱폼
   주제 → 원본 곡(Mureka) → 자동보관 → 1시간 루프 영상 + 영어 메타데이터.
   백엔드 계약:
     POST /api/music/compose-plan   {issue} → {title,lyrics,style,mood,keyword,source}
     POST /api/music/generate       {issue,mood,keyword,title,style,lyrics} → job
     GET  /api/music/jobs/{id}       → job (running이면 Mureka 폴링)
     POST /api/music/archive/{id}    → 보관(다운로드)
     POST /api/music/longform        {job_id,target_min,niche} → release
     GET  /api/music/longform/{id}   → release 상태
     GET  /api/music/longform        → release 목록 (갤러리)
     GET  /api/music/curator/options → {moods,keywords}
*/

const el = (id) => document.getElementById(id);
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

async function api(path, opts = {}) {
  const r = await fetch(path, {
    headers: { 'Content-Type': 'application/json', 'Accept': 'application/json' },
    ...opts,
  });
  const data = await r.json().catch(() => ({}));
  if (!r.ok) {
    const m = (data && (data.detail || data.error)) || `HTTP ${r.status}`;
    throw new Error(typeof m === 'string' ? m : JSON.stringify(m));
  }
  return data;
}
function hint(id, msg, kind = '') {
  const h = el(id); if (!h) return;
  h.textContent = msg || ''; h.className = 'hint' + (kind ? ' ' + kind : '');
}
function spinnerRow(t) { return `<div class="spin-row"><span class="spinner"></span><span>${t}</span></div>`; }
function esc(s) {
  return String(s || '').replace(/[&<>"']/g, (c) => (
    { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
}

/* ── 상태 ── */
const state = { songType: 'instrumental', niche: 'sleep', genre: 'ballad',
                language: 'Korean', era: 'modern', targetMin: 60, job: null, selected: new Set() };

/* 장르/연대 → Mureka 스타일 프롬프트 조각 */
const GENRE_STYLE = {
  ballad: 'emotional ballad, piano and strings, heartfelt expressive vocals, slow and moving',
  dance: 'upbeat dance pop, energetic beat, catchy synths, bright and danceable',
  citypop: 'city pop, groovy funky bassline, retro synths, smooth polished vocals',
  rnb: 'smooth R&B, soulful vocals, mellow groove, warm keys',
  rock: 'rock band, electric guitars, driving drums, powerful vocals',
  acoustic: 'acoustic singer-songwriter, warm acoustic guitar, intimate gentle vocals',
  lofi: 'lofi hip hop, chill mellow beat, soft relaxed vocals',
  jazz: 'smooth jazz lounge, piano and sax, silky vocals',
  pop: 'modern pop, catchy melody, polished production, clear vocals',
};
const ERA_STYLE = {
  modern: 'modern 2020s production, clean and polished',
  '2000s': '2000s pop style, early digital production',
  '90s': '1990s style, warm analog-digital blend',
  '80s': '1980s synth-pop style, retro synths, gated reverb drums, nostalgic',
  '70s': '1970s retro style, vintage analog warmth, classic instruments',
};
/* 무가사 니치별 스타일 (수면=피아노 / 집중=로파이 / 시네마틱=앰비언트) */
const NICHE_STYLE = {
  sleep: 'soft flowing legato acoustic piano, gentle rolling arpeggios, smooth connected phrases, '
    + 'warm sustain pedal, continuous and dreamy, soft rounded gentle touch, calm and soothing, '
    + 'solo grand piano, no staccato, no sharp attack, no percussive notes, no rain, no synth, no noise, peaceful sleep',
  study: 'lofi hip hop, chill mellow beat, warm soft keys, mellow jazzy chords, gentle vinyl crackle, '
    + 'relaxed and focused, steady soft groove, instrumental, no vocals, calm study and concentration vibe',
  cinematic: 'cinematic ambient soundscape, soft atmospheric strings and warm pads, gentle evolving textures, '
    + 'emotional and spacious, film score feel, slow and calm, instrumental, no drums, deep reverb',
};
function vocalStyle() {
  return `${GENRE_STYLE[state.genre] || 'pop'}, ${ERA_STYLE[state.era] || ''}, ${state.language} lyrics and vocals`;
}
function vocalBrief(theme) {
  return `${theme}. Write song lyrics in ${state.language} for a ${state.genre} song in ${state.era} style. `
    + `Singable with clear verses and a catchy chorus.`;
}
const ORDER = ['theme', 'plan', 'song', 'longform'];
function setStep(step) {
  const idx = ORDER.indexOf(step);
  document.querySelectorAll('#steps li').forEach((li) => {
    const i = ORDER.indexOf(li.dataset.step);
    li.classList.toggle('on', i === idx);
    li.classList.toggle('done', i < idx);
  });
}
const unlock = (id) => el(id).classList.remove('is-locked');

/* ── 세그먼트 버튼 (니치 / 길이) ── */
function bindSeg(segId, attr, onPick) {
  const seg = el(segId);
  seg.querySelectorAll('.seg-btn').forEach((b) => {
    b.addEventListener('click', () => {
      seg.querySelectorAll('.seg-btn').forEach((x) => x.classList.remove('on'));
      b.classList.add('on');
      onPick(b.dataset[attr]);
    });
  });
}

/* ── 서버 상태 ── */
async function checkHealth() {
  try { await api('/api/health'); el('health-ind').textContent = '● 연결됨'; el('health-ind').className = 'ind ok'; }
  catch { el('health-ind').textContent = '● 연결 끊김'; el('health-ind').className = 'ind bad'; }
}

/* ── ① 추천 ── */
async function loadSuggestions() {
  const box = el('suggest-box'); box.classList.remove('hidden');
  box.innerHTML = '<div class="empty">추천 불러오는 중…</div>';
  try {
    const o = await api('/api/music/curator/options');
    const chips = (arr, cls) => (arr || []).map(
      (v) => `<button class="chip ${cls}" data-v="${encodeURIComponent(v)}">${esc(v)}</button>`).join('');
    box.innerHTML = `
      <div class="suggest-row"><span class="suggest-label">분위기</span>${chips(o.moods, 'mood')}</div>
      <div class="suggest-row"><span class="suggest-label">키워드</span>${chips(o.keywords, 'kw')}</div>
      <div class="suggest-note">칩을 누르면 주제 칸에 채워집니다.</div>`;
    box.querySelectorAll('.chip').forEach((c) => c.addEventListener('click', () => {
      const v = decodeURIComponent(c.dataset.v);
      const t = el('theme'); t.value = (t.value ? t.value.trim() + ' · ' : '') + v; t.focus();
    }));
  } catch (e) { box.innerHTML = `<div class="empty">추천 실패: ${esc(e.message)}</div>`; }
}

/* ── ② 기획안 ── */
async function makePlan() {
  const issue = el('theme').value.trim();
  if (!issue) { hint('theme-hint', '주제를 먼저 입력하세요', 'fail'); return; }
  const btn = el('btn-plan'); btn.disabled = true; hint('theme-hint', '기획안 만드는 중…');
  const isVocal = state.songType === 'vocal';
  try {
    const issueToSend = isVocal ? vocalBrief(issue) : issue;
    const p = await api('/api/music/compose-plan', { method: 'POST', body: JSON.stringify({ issue: issueToSend }) });
    el('p-title').value = p.title || '';
    if (isVocal) {
      // 가사 있는 노래: 장르·연대·언어 기반 보컬 스타일 + LLM이 쓴 가사
      el('p-style').value = vocalStyle();
      el('p-lyrics').value = p.lyrics || '';
    } else {
      // 무가사: 니치별 스타일 (수면=피아노 / 집중=로파이 / 시네마틱=앰비언트)
      el('p-style').value = NICHE_STYLE[state.niche] || NICHE_STYLE.sleep;
      el('p-lyrics').value = '';
    }
    el('p-mood').value = p.mood || '';
    el('p-keyword').value = p.keyword || '';
    const src = el('plan-src');
    src.textContent = p.source === 'llm' ? 'AI 작성' : '규칙 기반';
    src.className = 'src-badge ' + (p.source === 'llm' ? 'ok' : 'warn');
    unlock('card-plan'); setStep('plan'); hint('theme-hint', '');
    el('card-plan').scrollIntoView({ behavior: 'smooth', block: 'start' });
  } catch (e) { hint('theme-hint', '실패: ' + e.message, 'fail'); }
  finally { btn.disabled = false; }
}

/* ── ③ 곡 생성 + 폴링 + 자동 보관 ── */
async function generateSong() {
  const isVocal = state.songType === 'vocal';
  let style = el('p-style').value.trim() || 'ambient, calm';
  let lyrics = el('p-lyrics').value.trim();
  let provider;
  if (isVocal) {
    provider = 'mureka';                             // 보컬 노래
    if (!lyrics) { hint('plan-hint', '가사가 비어 있습니다', 'fail'); return; }
  } else {
    provider = 'stability_audio';                    // 무가사 앰비언트
    lyrics = '[instrumental]';
    if (!/instrumental|no vocals/i.test(style)) style += ', instrumental, no vocals';
  }
  const payload = {
    issue: el('theme').value.trim(),
    title: el('p-title').value.trim(),
    style, mood: el('p-mood').value.trim(), keyword: el('p-keyword').value.trim(), lyrics, provider,
  };

  const btn = el('btn-generate'); btn.disabled = true; hint('plan-hint', '');
  unlock('card-song'); setStep('song');
  el('card-song').scrollIntoView({ behavior: 'smooth', block: 'start' });
  const box = el('song-status'); box.innerHTML = spinnerRow('곡 생성 요청 중…');
  try {
    let job = await api('/api/music/generate', { method: 'POST', body: JSON.stringify(payload) });
    state.job = { id: job.id, title: payload.title || `#${job.id}` };
    if (job.status === 'failed') throw new Error(job.error || '생성 실패');

    box.innerHTML = spinnerRow(`#${job.id} Mureka 작업 대기 중… (수 분)`);
    const deadline = Date.now() + 4 * 60 * 1000;
    while (job.status === 'running' || job.status === 'pending') {
      if (Date.now() > deadline) throw new Error('시간 초과 — 나중에 보관함에서 확인하세요');
      await sleep(5000);
      job = await api(`/api/music/jobs/${job.id}`);
      box.innerHTML = spinnerRow(`#${job.id} 생성 중… (${job.status})`);
    }
    if (job.status !== 'done') throw new Error(job.error || `상태: ${job.status}`);

    if (!job.archived) {                     // Stable Audio는 생성 즉시 보관됨 → 건너뜀
      box.innerHTML = spinnerRow('곡 완성 · 보관 중…');
      await api(`/api/music/archive/${job.id}`, { method: 'POST' });
    }
    box.innerHTML = `<div class="ok-row">✅ 원본 곡 완성 (#${job.id})</div>`;
    el('song-player').innerHTML = `<audio controls preload="none" src="/api/music/audio/${job.id}"></audio>`;

    unlock('card-longform'); setStep('longform');
    state.selected.add(job.id);              // 방금 만든 곡 자동 선택
    await renderTrackList();
    hint('longform-hint', '곡을 더 만들거나, 선택한 곡으로 롱폼을 렌더하세요', 'ok');
    el('card-longform').scrollIntoView({ behavior: 'smooth', block: 'start' });
  } catch (e) {
    box.innerHTML = `<div class="fail-row">✗ ${esc(e.message)}</div>`;
    hint('plan-hint', '실패: ' + e.message, 'fail');
  } finally { btn.disabled = false; refreshGallery(); }
}

/* ── ④ 완성 곡 목록 (컴필레이션 소스 선택) ── */
async function renderTrackList() {
  const wrap = el('track-list');
  try {
    const rows = await api('/api/music/archive');
    if (!rows.length) { wrap.innerHTML = '<div class="empty">완성된 곡이 없습니다.</div>'; updateTrackCount(); return; }
    unlock('card-longform');                 // 완성 곡이 있으면 컴필레이션 단계 열기
    wrap.innerHTML = rows.map((r) => `
      <label class="track ${state.selected.has(r.id) ? 'sel' : ''}">
        <input type="checkbox" data-jid="${r.id}" ${state.selected.has(r.id) ? 'checked' : ''}>
        <span class="track-title">${esc(r.title)}</span>
        <span class="track-meta">${esc(r.mood || '')}</span>
        <audio controls preload="none" src="${r.audio_url}"></audio>
      </label>`).join('');
    wrap.querySelectorAll('input[data-jid]').forEach((cb) => cb.addEventListener('change', () => {
      const id = parseInt(cb.dataset.jid, 10);
      if (cb.checked) state.selected.add(id); else state.selected.delete(id);
      cb.closest('.track').classList.toggle('sel', cb.checked);
      updateTrackCount();
    }));
  } catch (e) { wrap.innerHTML = `<div class="empty">곡 목록 로드 실패: ${esc(e.message)}</div>`; }
  updateTrackCount();
}
function updateTrackCount() {
  el('track-count').textContent = state.selected.size;
  el('btn-longform').disabled = state.selected.size === 0;
}

/* ── 비슷한 곡 자동생성 + 채우기 ── */
function autofillCount() {
  const isVocal = state.songType === 'vocal';
  const perTrack = isVocal ? 150 : 180;                 // 곡당 대략 길이(초)
  const needed = Math.round((state.targetMin * 60) / perTrack);
  const cap = isVocal ? 8 : 15;                          // 비용/시간 상한
  return Math.max(2, Math.min(needed, cap));
}
function updateAutofillLabel() {
  const c = el('autofill-count'); if (c) c.textContent = autofillCount() + '';
}
const AF_MOODS = ['gentle', 'warm', 'dreamy', 'soft', 'tender', 'nostalgic',
                  'calm', 'serene', 'mellow', 'quiet', 'soothing', 'peaceful',
                  'late night', 'early dawn', 'moonlit'];

async function autoFill() {
  const isVocal = state.songType === 'vocal';
  const count = autofillCount();
  const baseStyle = isVocal ? vocalStyle() : (NICHE_STYLE[state.niche] || NICHE_STYLE.sleep);
  const theme = el('theme').value.trim() || (isVocal ? 'a heartfelt emotional song' : 'calm and peaceful');
  const btnA = el('btn-autofill'), btnL = el('btn-longform');
  btnA.disabled = true; btnL.disabled = true;
  const box = el('longform-status'); box.classList.remove('hidden');
  const made = [];
  try {
    for (let i = 0; i < count; i++) {
      box.innerHTML = spinnerRow(`비슷한 곡 자동 생성 중… ${i + 1}/${count}` + (isVocal ? ' (보컬은 곡당 수십 초)' : ''));
      const variant = AF_MOODS[i % AF_MOODS.length];
      let style, lyrics = '[instrumental]', provider = 'stability_audio', title = `Track ${i + 1}`;
      if (isVocal) {
        provider = 'mureka';
        const p = await api('/api/music/compose-plan', { method: 'POST', body: JSON.stringify({ issue: vocalBrief(theme) }) });
        lyrics = p.lyrics || ''; title = p.title || `Track ${i + 1}`;
        style = `${variant}, ${baseStyle}`;
        if (!lyrics) continue;
      } else {
        style = `${variant}, ${baseStyle}, instrumental, no vocals`;
      }
      let job = await api('/api/music/generate', {
        method: 'POST',
        body: JSON.stringify({ issue: theme, title, style, mood: variant, keyword: '', lyrics, provider }),
      });
      const deadline = Date.now() + 4 * 60 * 1000;
      while ((job.status === 'running' || job.status === 'pending') && Date.now() < deadline) {
        await sleep(5000); job = await api(`/api/music/jobs/${job.id}`);
      }
      if (job.status === 'done') {
        if (!job.archived) { try { await api(`/api/music/archive/${job.id}`, { method: 'POST' }); } catch {} }
        state.selected.add(job.id); made.push(job.id);
      } else if (job.error) {
        box.innerHTML = `<div class="fail-row">✗ ${esc(job.error)}</div>`;
        await renderTrackList(); btnA.disabled = false; return;
      }
    }
    await renderTrackList();
    if (!made.length) { box.innerHTML = `<div class="fail-row">✗ 곡 생성 실패 (키/크레딧 확인)</div>`; return; }
    box.innerHTML = `<div class="ok-row">✅ ${made.length}곡 생성 완료 · 롱폼 렌더 시작…</div>`;
    await makeLongform();
  } catch (e) {
    box.innerHTML = `<div class="fail-row">✗ ${esc(e.message)}</div>`;
  } finally {
    btnA.disabled = false;
  }
}

/* ── 롱폼 렌더 + 폴링 ── */
async function makeLongform() {
  const ids = [...state.selected];
  if (!ids.length) { hint('longform-hint', '곡을 하나 이상 선택하세요', 'fail'); return; }
  const btn = el('btn-longform'); btn.disabled = true;
  const box = el('longform-status'); box.classList.remove('hidden');
  box.innerHTML = spinnerRow(`${ids.length}곡으로 렌더 대기열에 등록 중…`);
  try {
    let rel = await api('/api/music/longform', {
      method: 'POST',
      body: JSON.stringify({ job_ids: ids, target_min: state.targetMin, niche: state.niche }),
    });
    box.innerHTML = spinnerRow(`🌙 ${state.targetMin}분 영상 렌더 중… (보통 1~2분)`);
    const deadline = Date.now() + 8 * 60 * 1000;
    while (rel.status === 'pending' || rel.status === 'rendering') {
      if (Date.now() > deadline) { box.innerHTML = spinnerRow('시간이 걸립니다 — 아래 보관함에서 갱신됩니다.'); break; }
      await sleep(5000);
      rel = await api(`/api/music/longform/${rel.id}`);
    }
    if (rel.status === 'done') {
      box.innerHTML = `<div class="ok-row">✅ 영상 완성 · ${Math.round(rel.duration_sec/60)}분 · ${rel.size_mb}MB
        <a class="btn btn-primary btn-sm" href="${rel.download_url}" download>⬇ mp4 다운로드</a></div>
        <div class="muted-note">아래 보관함에서 영어 제목·설명·태그를 복사해 유튜브에 올리세요.</div>`;
    } else if (rel.status === 'failed') {
      throw new Error(rel.error || '렌더 실패');
    }
  } catch (e) {
    box.innerHTML = `<div class="fail-row">✗ ${esc(e.message)}</div>`;
  } finally { btn.disabled = false; refreshGallery(); }
}

/* ── 보관함 (롱폼 릴리스 + 메타데이터 복사) ── */
async function refreshGallery() {
  const g = el('gallery');
  try {
    const rows = await api('/api/music/longform');
    if (!rows.length) { g.innerHTML = '<div class="empty">아직 완성된 영상이 없습니다.</div>'; return; }
    g.innerHTML = rows.map((r) => {
      const cover = r.cover_url ? `<img class="g-cover" src="${r.cover_url}" alt="cover" loading="lazy">` : '<div class="g-cover ph"></div>';
      let action = '';
      if (r.status === 'done') action = `<a class="btn btn-sm btn-primary" href="${r.download_url}" download>⬇ mp4 (${r.size_mb}MB)</a>`;
      else if (r.status === 'failed') action = `<span class="v-fail">렌더 실패</span>`;
      else action = `<span class="v-render">🌙 렌더 중…</span>`;
      const meta = r.status === 'done' ? `
        <details class="yt">
          <summary>📋 유튜브 메타데이터 (영어)</summary>
          <div class="yt-row"><span class="yt-k">제목</span>
            <code id="ytt-${r.id}">${esc(r.yt_title)}</code>
            <button class="btn btn-sm" data-copy="ytt-${r.id}">복사</button></div>
          <div class="yt-row"><span class="yt-k">설명</span>
            <pre id="ytd-${r.id}">${esc(r.yt_description)}</pre>
            <button class="btn btn-sm" data-copy="ytd-${r.id}">복사</button></div>
          <div class="yt-row"><span class="yt-k">태그</span>
            <code id="ytg-${r.id}">${esc((r.yt_tags||[]).join(', '))}</code>
            <button class="btn btn-sm" data-copy="ytg-${r.id}">복사</button></div>
        </details>` : '';
      return `
        <div class="g-item lf">
          ${cover}
          <div class="g-main">
            <div class="g-title">${esc(r.yt_title || r.theme || ('#'+r.id))}</div>
            <div class="g-meta">${esc(r.niche)} · ${r.track_count||1}곡 · ${r.target_sec/60|0}분 목표 · ${esc(r.theme).slice(0,40)}</div>
            ${meta}
          </div>
          <div class="g-actions">${action}</div>
        </div>`;
    }).join('');
    g.querySelectorAll('[data-copy]').forEach((b) => b.addEventListener('click', async () => {
      const src = el(b.dataset.copy); const text = src.textContent;
      try { await navigator.clipboard.writeText(text); b.textContent = '복사됨 ✓'; setTimeout(() => b.textContent = '복사', 1500); }
      catch { b.textContent = '실패'; }
    }));
  } catch (e) { g.innerHTML = `<div class="empty">보관함 로드 실패: ${esc(e.message)}</div>`; }
}

/* ── 바인딩 ── */
bindSeg('niche-seg', 'niche', (v) => { state.niche = v; });
bindSeg('len-seg', 'min', (v) => { state.targetMin = parseInt(v, 10); updateAutofillLabel(); });
bindSeg('type-seg', 'type', (v) => {
  state.songType = v;
  updateAutofillLabel();
  el('instrumental-opts').classList.toggle('hidden', v !== 'instrumental');
  el('vocal-opts').classList.toggle('hidden', v !== 'vocal');
  const cb = el('p-instrumental'); if (cb) { cb.checked = (v === 'instrumental'); el('lyrics-field').style.opacity = v === 'vocal' ? '1' : '.5'; }
  // 무가사면 니치=sleep 유지, 가사면 테마 placeholder 힌트
  el('theme').placeholder = v === 'vocal'
    ? '예: 첫사랑의 설렘 / 이별 후의 밤 / 여름밤 드라이브'
    : '예: 비 내리는 깊은 밤, 마음이 가라앉는 잔잔한 앰비언트';
});
bindSeg('genre-seg', 'genre', (v) => { state.genre = v; });
bindSeg('lang-seg', 'lang', (v) => { state.language = v; });
bindSeg('era-seg', 'era', (v) => { state.era = v; });
el('p-instrumental').addEventListener('change', (e) => {
  el('lyrics-field').style.opacity = e.target.checked ? '.5' : '1';
});
el('btn-suggest').addEventListener('click', loadSuggestions);
el('btn-plan').addEventListener('click', makePlan);
el('btn-generate').addEventListener('click', generateSong);
el('btn-longform').addEventListener('click', makeLongform);
el('btn-autofill').addEventListener('click', autoFill);
el('btn-more').addEventListener('click', () => {
  // 새 트랙 하나 더: 주제 단계로 올라가 입력만 비움 (선택 곡·니치는 유지)
  el('p-title').value = ''; el('p-lyrics').value = '';
  setStep('theme');
  el('card-theme').scrollIntoView({ behavior: 'smooth', block: 'start' });
  el('theme').focus();
});

checkHealth();
updateAutofillLabel();
loadSuggestions();   // 추천을 기본으로 자동 표시 (버튼 안 눌러도)
renderTrackList();   // 기존 보관곡이 있으면 목록 표시
refreshGallery();
