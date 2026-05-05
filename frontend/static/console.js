/* Lucky HQ — 메인 콘솔 동작
   - 직원/부서/로그 폴링
   - 직원 클릭 → 부서 페이지 이동 (있으면) / 콘솔에 voice 표시 (없으면)
*/

const $ = (sel) => document.querySelector(sel);
const $$ = (sel) => Array.from(document.querySelectorAll(sel));

const state = {
  agents: [],
  departments: [],
  deptBySlug: {},
  deptById: {},
  logs: [],
};

async function fetchJSON(path) {
  const r = await fetch(path, { headers: { 'Accept': 'application/json' } });
  if (!r.ok) throw new Error(`${path} → ${r.status}`);
  return r.json();
}

function isBusyStatus(s) {
  if (!s) return false;
  const t = String(s).trim();
  return t && t !== "대기" && t !== "idle";
}

function navigateForAgent(agent) {
  // 부서 소속 직원 → 부서 페이지로
  const dept = agent.department_id ? state.deptById[agent.department_id] : null;
  if (dept) {
    window.location.href = `/dept/${dept.slug}`;
    return;
  }
  // 부서 없는 직원 (지휘관·관제 등) → 토스트만
  toast(`${agent.name} — ${agent.voice || agent.role}`);
}

function toast(msg) {
  let el = document.querySelector('.toast');
  if (!el) {
    el = document.createElement('div');
    el.className = 'toast';
    el.style.cssText = `
      position: fixed; bottom: 24px; left: 50%; transform: translateX(-50%);
      background: var(--bg-raised); color: var(--text);
      border: 1px solid var(--clover-gold);
      box-shadow: 3px 3px 0 0 var(--border);
      padding: 10px 16px; font-size: 13px; z-index: 999;
      max-width: 480px; text-align: center;
    `;
    document.body.appendChild(el);
  }
  el.textContent = msg;
  el.style.opacity = '1';
  clearTimeout(el._t);
  el._t = setTimeout(() => { el.style.opacity = '0'; el.style.transition = 'opacity 0.4s'; }, 2600);
}

function renderAgents() {
  // 좌측 명단
  const left = $('#agent-list');
  if (!state.agents.length) {
    left.innerHTML = '<div class="log-empty">직원 없음</div>';
  } else {
    left.innerHTML = state.agents.map(a => {
      const hasDept = !!a.department_id;
      const busy = isBusyStatus(a.current_status);
      return `
        <div class="agent-row ${hasDept ? 'has-dept' : ''}" data-slug="${a.slug}">
          <div class="av">${a.avatar || '🍀'}</div>
          <div class="info">
            <div class="name">${a.name}</div>
            <div class="status ${busy ? 'run' : ''}">${a.current_status || '대기'}</div>
          </div>
          <span class="dot ${busy ? 'run' : 'idle'}"></span>
        </div>
      `;
    }).join('');
  }
  $('#agent-count').textContent = `${state.agents.length}명`;

  // 중앙 픽셀 오피스
  const office = $('#office');
  office.innerHTML = state.agents.map(a => {
    const hasDept = !!a.department_id;
    const busy = isBusyStatus(a.current_status);
    return `
      <div class="desk ${hasDept ? 'has-dept' : ''} ${busy ? 'is-busy' : ''}" data-slug="${a.slug}" title="${a.voice || ''}">
        <div class="stat">${busy ? 'BUSY' : 'IDLE'}</div>
        <div class="av">${a.avatar || '🍀'}</div>
        <div class="role">${a.name}</div>
      </div>
    `;
  }).join('');

  // 클릭 바인딩
  const handler = (e) => {
    const el = e.currentTarget;
    const slug = el.dataset.slug;
    const agent = state.agents.find(x => x.slug === slug);
    if (agent) navigateForAgent(agent);
  };
  $$('.agent-row').forEach(el => el.addEventListener('click', handler));
  $$('.desk').forEach(el => el.addEventListener('click', handler));
}

function renderLogs() {
  const list = $('#log-list');
  $('#log-count').textContent = `${state.logs.length}건`;
  if (!state.logs.length) {
    list.innerHTML = '<div class="log-empty">아직 로그가 없습니다.</div>';
    return;
  }
  list.innerHTML = state.logs.map(l => {
    const t = l.created_at ? new Date(l.created_at).toLocaleTimeString('ko-KR', { hour12: false }).slice(0,8) : '--:--:--';
    return `
      <div class="log-row">
        <span class="t">${t}</span>
        <span class="a">${l.actor || '-'}</span>
        <span class="m" title="${l.action} ${l.target||''}">${l.action}${l.target ? ' ▸ ' + l.target : ''}</span>
      </div>
    `;
  }).join('');
}

async function tickHealth() {
  try {
    await fetchJSON('/api/health');
    $('#health-ind').textContent = '● 연결됨';
    $('#health-ind').style.color = 'var(--clover-green)';
  } catch (_) {
    $('#health-ind').textContent = '● 연결 끊김';
    $('#health-ind').style.color = 'var(--danger)';
  }
}

async function tickAgents() {
  try {
    const [agents, depts] = await Promise.all([
      fetchJSON('/api/agents'),
      fetchJSON('/api/departments'),
    ]);
    state.agents = agents;
    state.departments = depts;
    state.deptBySlug = Object.fromEntries(depts.map(d => [d.slug, d]));
    state.deptById = Object.fromEntries(depts.map(d => [d.id, d]));
    renderAgents();
  } catch (e) { /* 무시 (다음 틱에 재시도) */ }
}

async function tickLogs() {
  try {
    state.logs = await fetchJSON('/api/logs?limit=80');
    renderLogs();
  } catch (e) { /* 무시 */ }
}

function tickClock() {
  const now = new Date();
  const hh = String(now.getHours()).padStart(2,'0');
  const mm = String(now.getMinutes()).padStart(2,'0');
  const ss = String(now.getSeconds()).padStart(2,'0');
  $('#clock').textContent = `${hh}:${mm}:${ss}`;
}

// 부팅
(async function main() {
  tickClock();
  setInterval(tickClock, 1000);

  await tickHealth();
  setInterval(tickHealth, 15000);

  await Promise.all([tickAgents(), tickLogs()]);
  setInterval(tickAgents, 5000);
  setInterval(tickLogs, 3000);
})();
