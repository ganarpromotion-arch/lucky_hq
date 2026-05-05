/* Lucky HQ — 메인 콘솔
   - 활성 직원 + 활성 부서만 보여줌
   - 부서 카드 클릭 → /dept/{slug}
*/

const $ = (s) => document.querySelector(s);

async function fetchJSON(path) {
  const r = await fetch(path, { headers: { 'Accept': 'application/json' } });
  if (!r.ok) throw new Error(`${path} → ${r.status}`);
  return r.json();
}

async function tickHealth() {
  const ind = $('#health-ind');
  try {
    await fetchJSON('/api/health');
    ind.textContent = '연결됨';
    ind.classList.remove('fail'); ind.classList.add('ok');
  } catch {
    ind.textContent = '연결 끊김';
    ind.classList.remove('ok'); ind.classList.add('fail');
  }
}

async function render() {
  try {
    const [agents, depts] = await Promise.all([
      fetchJSON('/api/agents'),     // is_active=true 만
      fetchJSON('/api/departments'),
    ]);

    // 활성 직원이 속한 부서만
    const activeDeptIds = new Set(agents.filter(a => a.department_id).map(a => a.department_id));
    const visibleDepts = depts.filter(d => activeDeptIds.has(d.id));

    if (!visibleDepts.length) {
      $('#dept-section').innerHTML = '<div class="empty">활성 부서가 없습니다.</div>';
      return;
    }

    $('#dept-section').innerHTML = visibleDepts.map(d => {
      const members = agents.filter(a => a.department_id === d.id);
      return `
        <a class="dept-card" href="/dept/${d.slug}">
          <div class="head">
            <div>
              <h2>${d.name}</h2>
              <div class="desc">${d.description || ''}</div>
            </div>
            <span class="badge tint">${d.status}</span>
          </div>
          <div class="members">
            ${members.map(m => `
              <span class="member">
                <span class="av">${m.avatar || '🍀'}</span>
                <span>${m.name}</span>
              </span>
            `).join('')}
          </div>
        </a>
      `;
    }).join('');

  } catch (e) {
    $('#dept-section').innerHTML = `<div class="empty">불러오기 실패: ${e.message}</div>`;
  }
}

(async function main() {
  await tickHealth();
  setInterval(tickHealth, 15000);
  await render();
  setInterval(render, 8000);
})();
