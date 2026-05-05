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
// API 키 (Setting) — 일반화: data-save / data-clear 속성으로 처리
// ─────────────────────────────────────────────────
const SECRET_LABELS = {
  mureka_api_key: 'Mureka',
  anthropic_api_key: 'Anthropic',
  openai_api_key: 'OpenAI',
};

async function loadSettings() {
  try {
    const list = await fetchJSON('/api/settings');
    const wrap = $('#settings-list');
    if (!list.length) {
      wrap.innerHTML = '<div class="empty">아직 등록된 키가 없습니다. (env에 있으면 자동 사용됨)</div>';
      return;
    }
    wrap.innerHTML = list.map(s => {
      const label = SECRET_LABELS[s.key] || s.key;
      return `
        <div class="settings-row">
          <div>
            <div class="key">${label} <small style="color: var(--text-dim);">${s.key}</small></div>
            <div class="val ${s.has_value ? '' : 'empty-val'}">${s.has_value ? s.value : '(미등록)'}</div>
          </div>
          <span class="badge ${s.has_value ? 'ok' : ''}">${s.has_value ? '등록됨' : '미등록'}</span>
        </div>
      `;
    }).join('');
  } catch (e) { /* 무시 */ }
}

async function saveSecret(key, inputId) {
  const input = document.getElementById(inputId);
  const v = input.value.trim();
  const hint = document.getElementById(`hint-${key}`);
  if (!v) { hint.textContent = '값을 입력해주세요'; hint.className = 'hint fail'; return; }
  try {
    await fetchJSON(`/api/settings/${key}`, {
      method: 'PUT',
      body: JSON.stringify({ value: v, is_secret: true }),
    });
    input.value = '';
    hint.textContent = '✓ 저장됨'; hint.className = 'hint ok';
    await loadSettings();
  } catch (e) {
    hint.textContent = '✗ ' + e.message; hint.className = 'hint fail';
  }
}

async function clearSecret(key) {
  if (!confirm(`${SECRET_LABELS[key] || key} 키를 삭제할까요? (env에 키가 있으면 그게 자동 사용됨)`)) return;
  const hint = document.getElementById(`hint-${key}`);
  try {
    await fetchJSON(`/api/settings/${key}`, { method: 'DELETE' });
    hint.textContent = '✓ 삭제됨'; hint.className = 'hint ok';
    await loadSettings();
  } catch (e) {
    hint.textContent = '✗ ' + e.message; hint.className = 'hint fail';
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

  // API 키 저장/삭제 버튼 일반화
  document.querySelectorAll('[data-save]').forEach(btn => {
    btn.addEventListener('click', () => saveSecret(btn.dataset.save, btn.dataset.input));
  });
  document.querySelectorAll('[data-clear]').forEach(btn => {
    btn.addEventListener('click', () => clearSecret(btn.dataset.clear));
  });

  loadAgents();
  loadSettings();
  refreshJobs();
  setInterval(loadAgents, 8000);
  setInterval(refreshJobs, 5000);
})();
