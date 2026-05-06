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

async function onCheckBilling() {
  const hint = $('#billing-hint');
  const btn = $('#btn-billing');
  btn.disabled = true;
  hint.textContent = 'Mureka에 잔량 조회 중…'; hint.className = 'hint';
  try {
    const res = await fetchJSON('/api/music/mureka-billing');
    if (!res.ok) {
      hint.textContent = `✗ ${res.status_code || '?'} · ${res.error || '실패'}`;
      hint.className = 'hint fail';
      return;
    }
    const d = res.data || {};
    const parts = [];
    if (d.balance !== undefined) parts.push(`잔액: ${d.balance}`);
    if (d.credits !== undefined) parts.push(`크레딧: ${d.credits}`);
    if (d.plan) parts.push(`플랜: ${d.plan}`);
    if (d.concurrency !== undefined) parts.push(`동시: ${d.concurrency}`);
    const summary = parts.length ? parts.join(' · ') : JSON.stringify(d).slice(0, 200);
    hint.textContent = `✓ 키 유효 — ${summary}`;
    hint.className = 'hint ok';
  } catch (e) {
    hint.textContent = '✗ ' + e.message;
    hint.className = 'hint fail';
  } finally {
    btn.disabled = false;
  }
}

// ─────────────────────────────────────────────────
// 작곡가 기획안
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
    const variants = Array.isArray(j.audio_urls) ? j.audio_urls : [];
    const players = variants.length
      ? variants.map((v, i) => `
          <div class="player" style="margin-top:8px;">
            <div style="font-size:12px; color:#666; margin-bottom:4px;">버전 ${i + 1}${v.duration_ms ? ` · ${Math.round(v.duration_ms / 1000)}초` : ''}</div>
            <audio controls preload="none" src="${v.url}"></audio>
            ${v.url ? `<a class="btn btn-sm" href="${v.url}" download style="margin-left:6px;">⬇ mp3</a>` : ''}
            ${v.flac_url ? `<a class="btn btn-sm" href="${v.flac_url}" download style="margin-left:4px;">⬇ flac</a>` : ''}
          </div>`).join('')
      : (j.audio_url
          ? `<div class="player"><audio controls preload="none" src="${j.audio_url}"></audio></div>`
          : '');
    const showRefresh = j.status === 'done' && !variants.length && !j.audio_url;
    const refreshBtn = (j.status === 'done' || j.status === 'failed') && j.external_id
      ? `<button class="btn btn-sm js-refresh" data-id="${j.id}" style="margin-top:6px;">🔄 Mureka에서 다시 가져오기</button>`
      : '';
    const stuckHint = showRefresh
      ? `<div class="hint" style="margin-top:6px;">완료됐는데 오디오가 안 보이면 위 버튼을 눌러보세요</div>`
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
        ${players}
        ${refreshBtn}
        ${stuckHint}
        ${err}
      </div>
    `;
  }).join('');

  wrap.querySelectorAll('.js-refresh').forEach(btn => {
    btn.addEventListener('click', async () => {
      const id = btn.dataset.id;
      btn.disabled = true;
      const orig = btn.textContent;
      btn.textContent = '가져오는 중…';
      try {
        await fetchJSON(`/api/music/jobs/${id}/refresh`, { method: 'POST' });
        await refreshJobs();
      } catch (e) {
        showToast('✗ ' + e.message);
      } finally {
        btn.disabled = false;
        btn.textContent = orig;
      }
    });
  });
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

// ─────────────────────────────────────────────────
// 오늘의 배치 (큐레이터 → 작사가 → Mureka 10곡)
// ─────────────────────────────────────────────────
function fmtKstFromIso(iso) {
  if (!iso) return '';
  try {
    return new Date(iso).toLocaleString('ko-KR', {
      timeZone: 'Asia/Seoul',
      hour12: false,
      month: '2-digit', day: '2-digit',
      hour: '2-digit', minute: '2-digit',
    });
  } catch (_) { return iso; }
}

function deadlineCountdown(iso) {
  if (!iso) return '';
  const ms = new Date(iso).getTime() - Date.now();
  if (Number.isNaN(ms)) return '';
  if (ms <= 0) return '검토 마감 지남';
  const min = Math.floor(ms / 60000);
  const sec = Math.floor((ms % 60000) / 1000);
  if (min <= 0) return `검토 마감까지 ${sec}초`;
  return `검토 마감까지 ${min}분 ${sec}초`;
}

function renderBatchJob(j) {
  const title = (j.input && j.input.title) || `곡 #${j.id}`;
  const style = (j.input && j.input.style) || '';
  const issue = (j.input && j.input.issue) || '';
  const concept = (j.input && j.input.concept) || '';
  const variants = Array.isArray(j.audio_urls) ? j.audio_urls : [];
  const isRemoved = !!j.removed_at;
  const players = variants.length
    ? variants.map((v, i) => `
        <div class="player" style="margin-top:6px;">
          <div style="font-size:12px; color:#666; margin-bottom:3px;">버전 ${i + 1}${v.duration_ms ? ` · ${Math.round(v.duration_ms / 1000)}초` : ''}</div>
          <audio controls preload="none" src="${v.url}"></audio>
        </div>`).join('')
    : (j.audio_url
        ? `<div class="player"><audio controls preload="none" src="${j.audio_url}"></audio></div>`
        : '');
  const action = isRemoved
    ? `<button class="btn btn-sm js-restore" data-id="${j.id}">↩ 복원 (${j.removed_by || '?'})</button>`
    : (j.status === 'done'
        ? `<button class="btn btn-sm btn-danger js-exclude" data-id="${j.id}">❌ 제외</button>`
        : '');
  const errLine = j.status === 'failed'
    ? `<div class="err">에러: ${j.error || '알 수 없음'}</div>`
    : '';
  return `
    <div class="job ${isRemoved ? 'is-removed' : ''}" data-id="${j.id}" style="${isRemoved ? 'opacity:0.5;' : ''}">
      ${statusBadge(j.status)}
      <div class="meta">
        <div class="title">${title}${isRemoved ? ' <span class="badge fail">제외됨</span>' : ''}</div>
        <div class="sub">${style}${issue ? ' · ' + issue : ''}</div>
        ${concept && concept !== style ? `<div class="sub" style="font-size:11px; color:#888;">컨셉: ${concept}</div>` : ''}
      </div>
      <div class="id">#${j.id}</div>
      ${players}
      <div style="margin-top:6px;">${action}</div>
      ${errLine}
    </div>
  `;
}

let batchData = null;

async function refreshBatch() {
  try {
    const b = await fetchJSON('/api/music/batches/today/current');
    batchData = b;
    const card = $('#batch-card');
    if (!b) {
      card.style.display = 'none';
      return;
    }
    card.style.display = '';
    const counts = b.counts || {};
    const meta = [
      `${b.run_date}`,
      `상태: ${b.status}`,
      `완료 ${counts.done || 0}/${counts.total || 0}`,
      counts.failed ? `실패 ${counts.failed}` : '',
      counts.removed ? `제외 ${counts.removed}` : '',
    ].filter(Boolean).join(' · ');
    $('#batch-meta').textContent = meta;
    const dl = $('#batch-deadline');
    if (b.deadline_at && b.status === 'awaiting_review') {
      dl.style.display = '';
      dl.textContent = deadlineCountdown(b.deadline_at);
    } else {
      dl.style.display = 'none';
    }
    // 큐레이션 테마 요약
    const themes = (b.curated_themes && b.curated_themes.themes) || [];
    if (themes.length) {
      $('#batch-themes').innerHTML = `
        <details>
          <summary style="cursor:pointer; color:#666; font-size:13px;">큐레이션 테마 ${themes.length}개 보기 (${(b.curated_themes && b.curated_themes.source) || '-'})</summary>
          <ol style="margin:8px 0 0 20px; font-size:13px; color:#555;">
            ${themes.map(t => `<li>${t.issue}${t.concept ? ` <span style="color:#999;">— ${t.concept}</span>` : ''}</li>`).join('')}
          </ol>
        </details>`;
    } else {
      $('#batch-themes').innerHTML = '';
    }
    // 곡 목록
    const jobsWrap = $('#batch-jobs');
    if (!b.jobs || !b.jobs.length) {
      jobsWrap.innerHTML = '<div class="empty">아직 곡이 없습니다.</div>';
    } else {
      jobsWrap.innerHTML = b.jobs.map(renderBatchJob).join('');
      jobsWrap.querySelectorAll('.js-exclude').forEach(btn => {
        btn.addEventListener('click', async () => {
          if (!confirm(`#${btn.dataset.id} 를 빼시겠어요? (한 명이 ❌하면 즉시 제외)`)) return;
          btn.disabled = true;
          try {
            await fetchJSON(`/api/music/jobs/${btn.dataset.id}/exclude`, {
              method: 'POST',
              body: JSON.stringify({ by: 'owner' }),
            });
            await refreshBatch();
          } catch (e) { showToast('✗ ' + e.message); btn.disabled = false; }
        });
      });
      jobsWrap.querySelectorAll('.js-restore').forEach(btn => {
        btn.addEventListener('click', async () => {
          btn.disabled = true;
          try {
            await fetchJSON(`/api/music/jobs/${btn.dataset.id}/restore`, { method: 'POST' });
            await refreshBatch();
          } catch (e) { showToast('✗ ' + e.message); btn.disabled = false; }
        });
      });
    }
  } catch (_) { /* 무시 */ }
}

async function onRunBatchNow() {
  if (!confirm('지금 일일 배치를 1회 실행할까요? Mureka 호출이 발생합니다.')) return;
  const btn = $('#btn-batch-now');
  btn.disabled = true;
  const orig = btn.textContent;
  btn.textContent = '실행 중… (10~20분)';
  try {
    const r = await fetchJSON('/api/music/batches/run-now', { method: 'POST' });
    showToast(`배치 #${r.batch_id} · 완료 ${r.done}/${r.created} · 실패 ${r.failed}${r.skipped_reason ? ' · skip: ' + r.skipped_reason : ''}`);
  } catch (e) {
    showToast('✗ ' + e.message);
  } finally {
    btn.disabled = false;
    btn.textContent = orig;
    await refreshBatch();
  }
}

(function main() {
  $('#btn-plan').addEventListener('click', onComposePlan);
  $('#btn-generate').addEventListener('click', onGenerate);
  $('#btn-billing').addEventListener('click', onCheckBilling);
  const bn = $('#btn-batch-now');
  if (bn) bn.addEventListener('click', onRunBatchNow);

  loadAgents();
  loadSettings();
  refreshJobs();
  refreshBatch();
  setInterval(loadAgents, 8000);
  setInterval(loadSettings, 12000);
  setInterval(refreshJobs, 5000);
  setInterval(refreshBatch, 5000);  // 카운트다운/진행상황 갱신
})();
