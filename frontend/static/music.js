/* Lucky HQ — 음악부서
   - 직원 카드 / API 키 등록 / 작곡가 기획안 / Mureka 작곡 / 폴링
*/

const $ = (s) => document.querySelector(s);

async function fetchJSON(path, opts = {}) {
  const r = await fetch(path, {
    headers: { 'Content-Type': 'application/json', 'Accept': 'application/json' },
    ...opts,
  });
  const data = await r.json().catch(() => ({}));
  if (!r.ok) {
    const msg = (data && (data.detail || data.error)) || `HTTP ${r.status}`;
    throw new Error(typeof msg === 'string' ? msg : JSON.stringify(msg));
  }
  return data;
}

function fmtTime(iso) {
  if (!iso) return '--:--:--';
  return new Date(iso).toLocaleTimeString('ko-KR', { hour12: false });
}

function showToast(msg) {
  let el = document.querySelector('.toast');
  if (!el) {
    el = document.createElement('div');
    el.className = 'toast';
    document.body.appendChild(el);
  }
  el.textContent = msg;
  el.style.opacity = '1';
  clearTimeout(el._t);
  el._t = setTimeout(() => { el.style.opacity = '0'; }, 2400);
}

function statusBadge(status) {
  const map = {
    pending: { cls: 'run',  label: '준비 중' },
    running: { cls: 'run',  label: '작곡 중' },
    done:    { cls: 'ok',   label: '완료' },
    failed:  { cls: 'fail', label: '실패' },
  };
  const m = map[status] || { cls: '', label: status || '-' };
  return `<span class="badge ${m.cls}"><span class="dot"></span>${m.label}</span>`;
}

// ─────────────────────────────────────────────────
// 직원 카드
// ─────────────────────────────────────────────────
async function loadAgents() {
  try {
    const [agents, depts] = await Promise.all([
      fetchJSON('/api/agents'),
      fetchJSON('/api/departments'),
    ]);
    const music = depts.find(d => d.slug === 'music');
    const members = music ? agents.filter(a => a.department_id === music.id) : [];
    const wrap = $('#dept-agents');
    if (!members.length) {
      wrap.innerHTML = '<div class="empty">소속 직원이 없습니다.</div>';
      return;
    }
    wrap.innerHTML = members.map(a => `
      <div class="agent-card" data-voice="${(a.voice || '').replace(/"/g,'&quot;')}">
        <div class="av">${a.avatar || '🍀'}</div>
        <div class="info">
          <div class="name">${a.name}</div>
          <div class="status">${a.current_status || '대기'}</div>
        </div>
      </div>
    `).join('');
    wrap.querySelectorAll('.agent-card').forEach(el => {
      el.addEventListener('click', () => showToast(el.dataset.voice || ''));
    });
  } catch (e) { /* 무시 */ }
}

// ─────────────────────────────────────────────────
// API 키 — 음악부서 미니 표시 (관리는 /secrets)
// ─────────────────────────────────────────────────
const MUSIC_DEPT_KEYS = ['mureka_api_key', 'anthropic_api_key'];

async function loadSettings() {
  try {
    const items = await fetchJSON('/api/settings/catalog');
    const filtered = items.filter(i => MUSIC_DEPT_KEYS.includes(i.key));
    const wrap = $('#api-mini-list');
    if (!filtered.length) {
      wrap.innerHTML = '<div class="empty">관련 API가 없습니다.</div>';
      return;
    }
    wrap.innerHTML = filtered.map(s => `
      <div class="api-mini">
        <div class="info">
          <span class="label">${s.label}</span>
          <span class="val">${s.has_value ? s.value : '(미등록)'}</span>
        </div>
        <span class="badge ${s.has_value ? 'ok' : ''}">${s.has_value ? '등록됨' : '미등록'}</span>
      </div>
    `).join('');
  } catch (e) { /* 무시 */ }
}

// ─────────────────────────────────────────────────
// 일일 큐레이터 (매일 아침 8시 자동)
// ─────────────────────────────────────────────────
async function loadDailyStatus() {
  try {
    const d = await fetchJSON('/api/music/daily/today');
    const wrap = $('#daily-status');
    if (!d.exists) {
      wrap.className = 'empty';
      wrap.textContent = '오늘 발송된 안이 없습니다. 매일 8시에 자동 발송됩니다.';
      return;
    }
    const statusLabel = {
      waiting: '<span class="badge tint">응답 대기</span>',
      chosen:  `<span class="badge ok">선택됨 → 배치 #${d.triggered_batch_id || '?'}</span>`,
      skipped: '<span class="badge">패스</span>',
      cancelled: '<span class="badge fail">취소</span>',
    }[d.status] || d.status;
    const sentTime = d.sent_at
      ? new Date(d.sent_at).toLocaleTimeString('ko-KR', { timeZone: 'Asia/Seoul' })
      : '-';

    let chosenInfo = '';
    if (d.chosen) {
      const lang = d.languages[d.chosen.language_idx];
      const mood = d.moods[d.chosen.mood_idx];
      const kw = d.keywords[d.chosen.keyword_idx];
      chosenInfo = `<div class="hint" style="margin-top:6px;">
        선택: ${kw} · ${mood} · ${lang}
      </div>`;
    }

    wrap.className = '';
    wrap.innerHTML = `
      <div style="display:flex; gap:12px; align-items:center; flex-wrap:wrap; margin-bottom:8px;">
        ${statusLabel}
        <span class="hint">발송 ${sentTime} · 안 #${d.id}</span>
      </div>
      ${chosenInfo}
    `;
  } catch (e) { /* 무시 */ }
}

async function onDailyTrigger() {
  if (!confirm('지금 일일 안을 텔레그램으로 발송할까요?\n(이미 오늘 발송됐으면 무시됩니다)')) return;
  const btn = $('#btn-daily-trigger');
  btn.disabled = true;
  const orig = btn.textContent;
  btn.textContent = '발송 중…';
  try {
    const r = await fetchJSON('/api/music/daily/trigger-now', { method: 'POST' });
    if (r.ok) {
      showToast(`발송 완료 (안 #${r.proposal_id}, ${r.sent_to}명에게)`);
    } else {
      showToast(r.reason === 'already_exists'
        ? `오늘 이미 발송됨 (안 #${r.proposal_id})`
        : '실패: ' + (r.reason || '알 수 없음'));
    }
    await loadDailyStatus();
  } catch (e) {
    showToast('실패: ' + e.message);
  } finally {
    btn.disabled = false;
    btn.textContent = orig;
  }
}

// ─────────────────────────────────────────────────
// 큐레이터 5x3 안 (수동)
// ─────────────────────────────────────────────────
const curatorPicks = { language: null, mood: null, keyword: null };

function renderPills(containerId, items, kind) {
  const wrap = document.getElementById(containerId);
  wrap.innerHTML = items.map((text, i) => `
    <button type="button" class="pill" data-kind="${kind}" data-value="${text.replace(/"/g, '&quot;')}">
      <span class="pill-num">${i + 1}</span>${text}
    </button>
  `).join('');
  wrap.querySelectorAll('.pill').forEach(btn => {
    btn.addEventListener('click', () => {
      // 같은 kind에서 다른 선택 해제
      wrap.querySelectorAll('.pill').forEach(b => b.classList.remove('on'));
      btn.classList.add('on');
      curatorPicks[kind] = btn.dataset.value;
      updateCuratorApply();
    });
  });
}

function updateCuratorApply() {
  const ready = curatorPicks.language && curatorPicks.mood && curatorPicks.keyword;
  $('#btn-curator-apply').disabled = !ready;
  if (ready) {
    $('#curator-hint').textContent = `${curatorPicks.keyword} · ${curatorPicks.mood} · ${curatorPicks.language}`;
    $('#curator-hint').className = 'hint';
  }
}

async function onCurator() {
  const btn = $('#btn-curator');
  btn.disabled = true;
  const orig = btn.textContent;
  btn.textContent = '큐레이터 작성 중…';
  try {
    const opt = await fetchJSON('/api/music/curator/options');
    renderPills('opt-languages', opt.languages || [], 'language');
    renderPills('opt-moods', opt.moods || [], 'mood');
    renderPills('opt-keywords', opt.keywords || [], 'keyword');
    $('#curator-options').style.display = 'block';
    curatorPicks.language = curatorPicks.mood = curatorPicks.keyword = null;
    updateCuratorApply();
    if (opt.source === 'fallback') {
      $('#curator-hint').textContent = '⚠ Gemini 실패 → 기본 안 표시';
      $('#curator-hint').className = 'hint fail';
    } else {
      $('#curator-hint').textContent = `${opt.today || '오늘'} 기준`;
      $('#curator-hint').className = 'hint';
    }
  } catch (e) {
    showToast('큐레이터 호출 실패: ' + e.message);
  } finally {
    btn.disabled = false;
    btn.textContent = orig;
  }
}

function onCuratorApply() {
  if (!curatorPicks.keyword || !curatorPicks.mood || !curatorPicks.language) return;
  // 작곡가의 issue 입력에 합쳐 넣고 자동으로 기획 트리거
  const combined = `${curatorPicks.keyword} | 분위기: ${curatorPicks.mood} | 언어: ${curatorPicks.language}`;
  $('#f-issue').value = combined;
  $('#f-issue').scrollIntoView({ behavior: 'smooth', block: 'center' });
  // 자동으로 기획안 만들기 호출
  setTimeout(() => onComposePlan(), 400);
}

// ─────────────────────────────────────────────────
// 보관곡 (다운로드된 audio 재생 + 체크 후 영상 + 삭제)
// ─────────────────────────────────────────────────
const archivePicked = new Set();
let archivePolling = null;

function refreshArchivePickCount() {
  const n = archivePicked.size;
  const el = $('#archive-pick-count');
  if (el) el.textContent = String(n);
  const btn = $('#btn-archive-preview');
  if (btn) btn.disabled = (n === 0);
}

// 미리보기 상태: { [jobId]: { seed, imageUrl, title, mood } }
const thumbPreviews = {};

async function fetchThumbPreview(jobId, seed) {
  const body = (seed === undefined || seed === null) ? {} : { seed };
  return await fetchJSON(`/api/music/archive/thumbnail-preview/${jobId}`, {
    method: 'POST',
    body: JSON.stringify(body),
  });
}

function renderThumbPreviews() {
  const grid = $('#thumb-preview-grid');
  if (!grid) return;
  const ids = Object.keys(thumbPreviews);
  if (!ids.length) {
    grid.innerHTML = '<div class="empty">미리보기를 만드는 중…</div>';
    return;
  }
  grid.innerHTML = ids.map(jid => {
    const p = thumbPreviews[jid];
    return `
      <div class="thumb-card" data-thumb-job="${jid}" style="background:var(--surface); border-radius:var(--r-sm); overflow:hidden;">
        <div style="aspect-ratio:9/16; background:#000;">
          <img src="${p.imageUrl}" alt="thumb #${jid}" style="width:100%; height:100%; object-fit:cover; display:block;">
        </div>
        <div style="padding:8px 10px; display:flex; gap:8px; align-items:center; justify-content:space-between;">
          <div style="font-size:12px; line-height:1.3;">
            <strong>#${jid}</strong> · ${p.title || ''}<br>
            <span class="hint">${p.mood || ''} · seed ${p.seed}</span>
          </div>
          <button class="btn btn-sm" type="button" data-thumb-reroll="${jid}" title="다시 뽑기">↻</button>
        </div>
      </div>`;
  }).join('');
  document.querySelectorAll('[data-thumb-reroll]').forEach(btn => {
    btn.addEventListener('click', async () => {
      const jid = btn.dataset.thumbReroll;
      const orig = btn.textContent;
      btn.disabled = true;
      btn.textContent = '…';
      try {
        const p = await fetchThumbPreview(parseInt(jid, 10), null);
        thumbPreviews[jid] = {
          seed: p.seed,
          imageUrl: p.image_url,
          title: p.title, mood: p.mood,
        };
        renderThumbPreviews();
      } catch (e) {
        showToast('재생성 실패: ' + e.message);
        btn.disabled = false;
        btn.textContent = orig;
      }
    });
  });
}

async function onPreviewThumbnails() {
  if (!archivePicked.size) return;
  const ids = [...archivePicked].map(s => parseInt(s, 10)).filter(n => !isNaN(n));
  // 이전 미리보기 초기화
  Object.keys(thumbPreviews).forEach(k => delete thumbPreviews[k]);
  ids.forEach(id => { thumbPreviews[id] = { seed: 0, imageUrl: '', title: '…', mood: '' }; });

  $('#thumb-preview-panel').style.display = '';
  $('#thumb-preview-panel').scrollIntoView({ behavior: 'smooth', block: 'center' });
  renderThumbPreviews();

  // 병렬 fetch (각 곡당 PIL 한 번)
  await Promise.all(ids.map(async id => {
    try {
      const p = await fetchThumbPreview(id, null);
      thumbPreviews[id] = {
        seed: p.seed, imageUrl: p.image_url,
        title: p.title, mood: p.mood,
      };
    } catch (e) {
      thumbPreviews[id] = { seed: 0, imageUrl: '', title: '실패', mood: e.message };
    }
  }));
  renderThumbPreviews();
}

function onCancelPreview() {
  $('#thumb-preview-panel').style.display = 'none';
  Object.keys(thumbPreviews).forEach(k => delete thumbPreviews[k]);
}

async function onEncodeWithPreviews() {
  const picks = Object.entries(thumbPreviews)
    .filter(([, v]) => v && v.seed)
    .map(([jid, v]) => ({ job_id: parseInt(jid, 10), seed: v.seed }));
  if (!picks.length) {
    showToast('미리보기가 아직 준비되지 않았습니다');
    return;
  }
  if (!confirm(`${picks.length}곡을 보이는 표지 그대로 영상으로 만듭니다.\n곡당 약 30~60초 인코딩.\n\n진행할까요?`)) return;

  const btn = $('#btn-thumb-encode');
  const status = $('#archive-video-status');
  btn.disabled = true;
  const orig = btn.textContent;
  btn.textContent = '의뢰 중…';
  status.style.display = '';
  status.className = '';
  status.textContent = `🎬 ${picks.length}곡 영상 인코딩 시작 — 텔레그램에 순차 전송됩니다.`;

  try {
    const res = await fetchJSON('/api/music/archive/make-videos', {
      method: 'POST',
      body: JSON.stringify({ picks }),
    });
    status.innerHTML = `✓ ${res.queued}곡 큐 등록 완료. 인코딩 끝나면 다운로드 버튼이 활성화됩니다.`;
    archivePicked.clear();
    onArchivePickNone();
    onCancelPreview();
    setTimeout(loadArchive, 600);
  } catch (e) {
    status.className = 'empty';
    status.textContent = '✗ 실패: ' + e.message;
  } finally {
    btn.textContent = orig;
    btn.disabled = false;
    refreshArchivePickCount();
  }
}

async function loadArchive() {
  try {
    const items = await fetchJSON('/api/music/archive?limit=20');
    const wrap = $('#archive-list');
    if (!items.length) {
      wrap.innerHTML = '<div class="empty">아직 보관된 곡이 없습니다. 곡이 완성되면 자동으로 보관됩니다.</div>';
      archivePicked.clear();
      refreshArchivePickCount();
      return;
    }
    // 사라진 곡은 picked에서 제거
    const liveIds = new Set(items.map(it => String(it.id)));
    [...archivePicked].forEach(id => { if (!liveIds.has(id)) archivePicked.delete(id); });

    const reviewLabel = (s) => ({
      approved: '<span class="badge ok">채택</span>',
      rejected: '<span class="badge fail">거절</span>',
      pending_review: '<span class="badge tint">검토 대기</span>',
    })[s] || '';
    let anyRendering = false;
    wrap.innerHTML = items.map(it => {
      const checked = archivePicked.has(String(it.id)) ? 'checked' : '';
      const v = it.video;
      let videoHtml = '';
      if (v) {
        if (v.status === 'rendering') {
          anyRendering = true;
          videoHtml = `<div class="archive-video-row" style="margin-top:8px;">
            <span class="badge run"><span class="dot"></span>영상 인코딩 중…</span>
          </div>`;
        } else if (v.status === 'done' && v.download_url) {
          videoHtml = `<div class="archive-video-row" style="margin-top:8px; display:flex; gap:10px; align-items:center; flex-wrap:wrap;">
            <a class="btn btn-sm btn-primary" href="${v.download_url}" download>🎬 영상 다운로드 (mp4)</a>
            <span class="hint">${v.size_mb}MB · ${v.duration_sec}s</span>
          </div>`;
        } else if (v.status === 'failed') {
          videoHtml = `<div class="archive-video-row" style="margin-top:8px;">
            <span class="badge fail">영상 실패</span>
            <span class="hint" style="margin-left:8px;">${(v.error || '').slice(0, 120)}</span>
          </div>`;
        }
      }
      return `
      <div class="archive-item">
        <div class="archive-head" style="display:flex; gap:10px; align-items:center;">
          <input type="checkbox" class="archive-pick" data-archive-pick="${it.id}" ${checked}
                 style="width:18px; height:18px; cursor:pointer;" title="이 곡을 영상으로 만들기">
          <div class="archive-title" style="flex:1;">
            <strong>#${it.id} · ${it.title}</strong>
            ${reviewLabel(it.review_status)}
          </div>
          <button class="btn btn-sm btn-danger" data-archive-del="${it.id}">삭제</button>
        </div>
        ${it.issue ? `<div class="archive-issue">${it.issue}</div>` : ''}
        <div class="archive-meta">${it.style || ''} · ${it.size_kb}KB</div>
        <audio controls preload="none" src="${it.audio_url}" style="width: 100%; margin-top: 8px;"></audio>
        ${videoHtml}
      </div>`;
    }).join('');

    // 영상 인코딩 중인 곡이 있으면 6초마다 자동 새로고침 (완료되면 다운로드 버튼 보이도록)
    if (anyRendering && !archivePolling) {
      archivePolling = setInterval(loadArchive, 6000);
    } else if (!anyRendering && archivePolling) {
      clearInterval(archivePolling); archivePolling = null;
    }
    document.querySelectorAll('[data-archive-del]').forEach(btn => {
      btn.addEventListener('click', () => onDeleteArchive(btn.dataset.archiveDel));
    });
    document.querySelectorAll('[data-archive-pick]').forEach(cb => {
      cb.addEventListener('change', () => {
        const id = cb.dataset.archivePick;
        if (cb.checked) archivePicked.add(id); else archivePicked.delete(id);
        refreshArchivePickCount();
      });
    });
    refreshArchivePickCount();
  } catch (e) {
    $('#archive-list').innerHTML = `<div class="empty">불러오기 실패: ${e.message}</div>`;
  }
}

function onArchivePickAll() {
  document.querySelectorAll('[data-archive-pick]').forEach(cb => {
    cb.checked = true;
    archivePicked.add(cb.dataset.archivePick);
  });
  refreshArchivePickCount();
}

function onArchivePickNone() {
  document.querySelectorAll('[data-archive-pick]').forEach(cb => { cb.checked = false; });
  archivePicked.clear();
  refreshArchivePickCount();
}

// ─────────────────────────────────────────────────
// 큐레이터 교육 (lessons)
// ─────────────────────────────────────────────────
const LESSON_KIND_LABEL = {
  prefer: '🟢 좋아함',
  avoid: '🔴 피할 것',
  example: '⭐ 예시',
  rule: '📌 원칙',
};

async function loadLessons() {
  const wrap = $('#lesson-list');
  if (!wrap) return;
  try {
    const rows = await fetchJSON('/api/music/curator/lessons');
    if (!rows.length) {
      wrap.innerHTML = '<div class="empty">아직 교육 자료가 없습니다. 위에서 한 줄씩 추가하세요.</div>';
      return;
    }
    wrap.innerHTML = rows.map(r => `
      <div class="lesson-item" style="display:grid; grid-template-columns: 110px 1fr 70px 60px 70px; gap:8px; align-items:center; padding:8px 10px; border-radius:var(--r-sm); background:var(--surface-soft); margin-bottom:6px;">
        <span class="badge ${r.active ? 'tint' : ''}">${LESSON_KIND_LABEL[r.kind] || r.kind}</span>
        <span style="font-size:13px;">${r.text}</span>
        <span class="hint" title="강조 강도">${'★'.repeat(r.weight)}</span>
        <span class="hint" title="이번까지 사용 횟수">×${r.used_count}</span>
        <span style="display:flex; gap:4px; justify-content:flex-end;">
          <button class="btn btn-sm" type="button" data-lesson-toggle="${r.id}" data-active="${r.active ? 1 : 0}"
                  title="${r.active ? '비활성화' : '활성화'}">${r.active ? '⏸' : '▶'}</button>
          <button class="btn btn-sm btn-danger" type="button" data-lesson-del="${r.id}">🗑</button>
        </span>
      </div>
    `).join('');
    document.querySelectorAll('[data-lesson-del]').forEach(btn => {
      btn.addEventListener('click', () => onDeleteLesson(btn.dataset.lessonDel));
    });
    document.querySelectorAll('[data-lesson-toggle]').forEach(btn => {
      btn.addEventListener('click', () => onToggleLesson(btn.dataset.lessonToggle, btn.dataset.active === '1'));
    });
  } catch (e) {
    wrap.innerHTML = `<div class="empty">불러오기 실패: ${e.message}</div>`;
  }
}

async function onAddLesson() {
  const text = $('#lesson-text').value.trim();
  if (!text) { showToast('내용을 입력하세요'); return; }
  const body = {
    kind: $('#lesson-kind').value,
    text,
    weight: parseInt($('#lesson-weight').value, 10) || 1,
    active: $('#lesson-active').checked,
  };
  try {
    await fetchJSON('/api/music/curator/lessons', {
      method: 'POST',
      body: JSON.stringify(body),
    });
    $('#lesson-text').value = '';
    showToast('교육 자료 추가됨');
    await loadLessons();
  } catch (e) {
    showToast('추가 실패: ' + e.message);
  }
}

async function onDeleteLesson(id) {
  if (!confirm(`교육 자료 #${id}을(를) 삭제할까요?`)) return;
  try {
    await fetchJSON(`/api/music/curator/lessons/${id}`, { method: 'DELETE' });
    await loadLessons();
  } catch (e) {
    showToast('삭제 실패: ' + e.message);
  }
}

async function onToggleLesson(id, currentlyActive) {
  // 현재 row를 다시 읽지 않고 PUT — 서버는 전체 필드 받아야 하므로 list에서 가져오기
  try {
    const rows = await fetchJSON('/api/music/curator/lessons');
    const r = rows.find(x => x.id === parseInt(id, 10));
    if (!r) return;
    await fetchJSON(`/api/music/curator/lessons/${id}`, {
      method: 'PUT',
      body: JSON.stringify({
        kind: r.kind, text: r.text, weight: r.weight,
        active: !currentlyActive,
      }),
    });
    await loadLessons();
  } catch (e) {
    showToast('변경 실패: ' + e.message);
  }
}

async function onDeleteArchive(jobId) {
  if (!confirm(`곡 #${jobId}을(를) 삭제할까요? 파일과 보관 기록이 모두 사라집니다.`)) return;
  try {
    await fetchJSON(`/api/music/archive/${jobId}`, { method: 'DELETE' });
    archivePicked.delete(String(jobId));
    await loadArchive();
    showToast(`곡 #${jobId} 삭제됨`);
  } catch (e) {
    showToast('삭제 실패: ' + e.message);
  }
}

// ─────────────────────────────────────────────────
// 자동 배치 (테스트 버튼)
// ─────────────────────────────────────────────────
let activeBatchId = null;
let batchPolling = null;

function renderBatchStatus(b) {
  if (!b) {
    $('#batch-status').className = 'empty';
    $('#batch-status').textContent = '아직 실행하지 않았습니다.';
    return;
  }
  const labelMap = {
    pending: '대기 중',
    running: '제작 중',
    reporting: '텔레그램 보고 중',
    done: '완료',
    failed: '실패',
  };
  const statusLabel = labelMap[b.status] || b.status;
  const issuesHtml = (b.issues || []).map((iss, i) => {
    const job = (b.jobs || []).find(j => j.issue === iss);
    let stat = '⏳';
    if (job) {
      stat = ({ pending: '⏳', running: '🎵', done: '✅', failed: '❌' })[job.status] || '⏳';
    }
    return `<div style="padding:6px 10px; font-size:13px; display:flex; gap:10px;">
      <span>${stat}</span>
      <span style="flex:1;">${i + 1}. ${iss}</span>
      ${job && job.title ? `<span style="color:var(--text-soft);">${job.title}</span>` : ''}
    </div>`;
  }).join('');

  $('#batch-status').className = '';
  $('#batch-status').innerHTML = `
    <div style="display:flex; gap:14px; align-items:center; margin-bottom:14px; flex-wrap: wrap;">
      <span class="badge ${b.status === 'done' ? 'ok' : (b.status === 'failed' ? 'fail' : 'run')}">
        <span class="dot"></span>${statusLabel}
      </span>
      ${b.make_video ? '<span class="badge tint">🎬 영상 mp4</span>' : '<span class="badge">🎵 audio</span>'}
      <span class="hint">배치 #${b.id} · 성공 ${b.completed_count}/${b.target_count} · 실패 ${b.failed_count}</span>
    </div>
    <div style="background: var(--surface-soft); border-radius: var(--r-md); padding: 8px;">
      ${issuesHtml || '<div class="empty">이슈 정보 없음</div>'}
    </div>
    ${b.error ? `<div class="job-err" style="margin-top:10px; padding:10px; background:var(--danger-soft); color:var(--danger); border-radius:var(--r-sm); font-size:12px;">${b.error}</div>` : ''}
  `;
}

async function pollBatch() {
  if (!activeBatchId) return;
  try {
    const b = await fetchJSON(`/api/music/batches/${activeBatchId}`);
    renderBatchStatus(b);
    if (b.status === 'done' || b.status === 'failed') {
      clearInterval(batchPolling);
      batchPolling = null;
      // 완료되면 잡 목록 새로고침
      refreshJobs();
    }
  } catch (e) { /* 무시 */ }
}

async function loadLatestBatch() {
  try {
    const list = await fetchJSON('/api/music/batches?limit=1');
    if (list.length) {
      activeBatchId = list[0].id;
      const b = await fetchJSON(`/api/music/batches/${activeBatchId}`);
      renderBatchStatus(b);
      if (b.status === 'pending' || b.status === 'running' || b.status === 'reporting') {
        if (!batchPolling) batchPolling = setInterval(pollBatch, 4000);
      }
    }
  } catch (e) { /* 무시 */ }
}

async function onTestBatch(targetCount, makeVideo) {
  // 영상은 이제 보관곡 체크 후에만 만든다 — 배치는 audio 전용
  const eta = targetCount === 1 ? '약 1~2분' : '약 6~10분';
  if (!confirm(`${targetCount}곡 (audio)\n예상: ${eta}\n\n곡이 완성되면 보관곡에서 체크해서 영상으로 만들 수 있습니다.\n시작할까요?`)) return;

  const buttons = document.querySelectorAll('[data-batch]');
  buttons.forEach(b => b.disabled = true);
  try {
    const b = await fetchJSON('/api/music/batches', {
      method: 'POST',
      body: JSON.stringify({
        target_count: targetCount,
        trigger: 'test_button',
        make_video: !!makeVideo,
      }),
    });
    activeBatchId = b.id;
    renderBatchStatus(b);
    if (batchPolling) clearInterval(batchPolling);
    batchPolling = setInterval(pollBatch, 4000);
    showToast('배치 시작됨. 진행은 위에서 확인됩니다.');
  } catch (e) {
    showToast('실패: ' + e.message);
  } finally {
    buttons.forEach(b => b.disabled = false);
  }
}


// ─────────────────────────────────────────────────
// 작곡가 기획안 (단발 — 기존 기능 유지)
// ─────────────────────────────────────────────────
async function onComposePlan() {
  const issue = $('#f-issue').value.trim();
  const hint = $('#plan-hint');
  if (!issue) {
    hint.textContent = '⚠ 이슈를 입력해주세요'; hint.className = 'hint fail';
    return;
  }
  const btn = $('#btn-plan');
  btn.disabled = true;
  const orig = btn.textContent;
  btn.textContent = '작곡가 생각 중…';
  hint.textContent = '작곡가가 LLM으로 가사를 작성 중 (10~25초 소요)';
  hint.className = 'hint';
  try {
    const plan = await fetchJSON('/api/music/compose-plan', {
      method: 'POST',
      body: JSON.stringify({ issue }),
    });
    $('#f-title').value  = plan.title || '';
    $('#f-style').value  = plan.style || '';
    $('#f-lyrics').value = plan.lyrics || '';
    const sourceLabel = {
      llm: '🤖 LLM 작성',
      rule: '📋 룰 기반',
      rule_fallback: '📋 룰 기반 (LLM 실패)',
    }[plan.source] || plan.source || '-';
    $('#plan-tag-source').textContent  = sourceLabel;
    $('#plan-tag-mood').textContent    = `mood: ${plan.mood || '-'}`;
    $('#plan-tag-keyword').textContent = `keyword: ${plan.keyword || '-'}`;
    $('#plan-tags').style.display = 'flex';
    if (plan.source === 'rule_fallback') {
      hint.textContent = '⚠ LLM 실패 → 룰 기반으로 작성됨. ANTHROPIC_API_KEY 확인하세요'; hint.className = 'hint fail';
    } else {
      hint.textContent = '✓ 아래에 채웠습니다. 자유롭게 수정 후 [작곡 시작]'; hint.className = 'hint ok';
    }
  } catch (e) {
    hint.textContent = '✗ ' + e.message; hint.className = 'hint fail';
  } finally {
    btn.disabled = false;
    btn.textContent = orig;
  }
}

// ─────────────────────────────────────────────────
// Mureka 작곡 + 폴링
// ─────────────────────────────────────────────────
function renderJobs(jobs) {
  const wrap = $('#jobs');
  if (!jobs.length) {
    wrap.innerHTML = '<div class="empty">아직 의뢰한 곡이 없습니다.</div>';
    return;
  }
  wrap.innerHTML = jobs.map(j => {
    const title = (j.input && j.input.title) || `곡 #${j.id}`;
    const style = (j.input && j.input.style) || '';
    const audio = j.audio_url
      ? `<div class="player"><audio controls preload="none" src="${j.audio_url}"></audio></div>`
      : '';
    const err = j.status === 'failed'
      ? `<div class="err">에러: ${j.error || '알 수 없음'}</div>`
      : '';
    return `
      <div class="job" data-id="${j.id}">
        ${statusBadge(j.status)}
        <div class="meta">
          <div class="title">${title}</div>
          <div class="sub">${style ? style + ' · ' : ''}${fmtTime(j.created_at)}</div>
        </div>
        <div class="id">#${j.id}</div>
        ${audio}
        ${err}
      </div>
    `;
  }).join('');
}

let polling = null;
async function refreshJobs() {
  try {
    const jobs = await fetchJSON('/api/music/jobs?limit=20');
    renderJobs(jobs);
    const running = jobs.filter(j => j.status === 'running' || j.status === 'pending');
    if (running.length && !polling) {
      polling = setInterval(async () => {
        try {
          await Promise.all(running.map(j => fetchJSON(`/api/music/jobs/${j.id}`)));
          await refreshJobs();
        } catch (_) {}
      }, 4000);
    } else if (!running.length && polling) {
      clearInterval(polling);
      polling = null;
    }
  } catch (e) { /* 무시 */ }
}

async function onGenerate() {
  const lyrics = $('#f-lyrics').value.trim();
  const style  = $('#f-style').value.trim() || 'pop';
  const title  = $('#f-title').value.trim();
  const hint = $('#gen-hint');
  if (!lyrics) {
    hint.textContent = '⚠ 가사가 비어있습니다'; hint.className = 'hint fail';
    return;
  }
  const btn = $('#btn-generate');
  btn.disabled = true;
  const orig = btn.textContent;
  btn.textContent = '의뢰 중…';
  try {
    await fetchJSON('/api/music/generate', {
      method: 'POST',
      body: JSON.stringify({ lyrics, style, title }),
    });
    hint.textContent = '✓ 의뢰 완료. 작업 목록에서 진행 확인'; hint.className = 'hint ok';
    await refreshJobs();
  } catch (e) {
    hint.textContent = '✗ ' + e.message; hint.className = 'hint fail';
  } finally {
    btn.disabled = false;
    btn.textContent = orig;
  }
}

(function main() {
  $('#btn-plan').addEventListener('click', onComposePlan);
  $('#btn-generate').addEventListener('click', onGenerate);
  $('#btn-curator').addEventListener('click', onCurator);
  $('#btn-curator-apply').addEventListener('click', onCuratorApply);
  $('#btn-daily-trigger').addEventListener('click', onDailyTrigger);

  // 보관곡 체크 → 미리보기 → 영상 만들기 토글바
  const btnAll = $('#btn-archive-all'); if (btnAll) btnAll.addEventListener('click', onArchivePickAll);
  const btnNone = $('#btn-archive-none'); if (btnNone) btnNone.addEventListener('click', onArchivePickNone);
  const btnPrev = $('#btn-archive-preview'); if (btnPrev) btnPrev.addEventListener('click', onPreviewThumbnails);
  const btnEnc = $('#btn-thumb-encode'); if (btnEnc) btnEnc.addEventListener('click', onEncodeWithPreviews);
  const btnPCancel = $('#btn-thumb-cancel'); if (btnPCancel) btnPCancel.addEventListener('click', onCancelPreview);

  // 4개 배치 버튼 일반화
  document.querySelectorAll('[data-batch]').forEach(btn => {
    btn.addEventListener('click', () => {
      const count = parseInt(btn.dataset.batch, 10);
      const video = btn.dataset.video === 'true';
      onTestBatch(count, video);
    });
  });

  // 큐레이터 교육
  const btnLessonAdd = $('#btn-lesson-add'); if (btnLessonAdd) btnLessonAdd.addEventListener('click', onAddLesson);
  const lessonTxt = $('#lesson-text');
  if (lessonTxt) lessonTxt.addEventListener('keydown', e => { if (e.key === 'Enter') onAddLesson(); });

  loadAgents();
  loadSettings();
  loadLatestBatch();
  loadArchive();
  loadLessons();
  loadDailyStatus();
  refreshJobs();
  setInterval(loadAgents, 8000);
  setInterval(loadSettings, 12000);
  setInterval(loadArchive, 15000);
  setInterval(loadDailyStatus, 30000);
  setInterval(refreshJobs, 5000);
})();
