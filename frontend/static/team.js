/* Lucky HQ — 팀 관리 */
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

// ── 가입 코드 ───────────────────────────────────────
async function loadCodes() {
  try {
    const list = await fetchJSON('/api/team/codes');
    const wrap = $('#codes-list');
    if (!list.length) {
      wrap.innerHTML = '<div class="empty">발급된 코드가 없습니다.</div>';
      return;
    }
    wrap.innerHTML = list.map(c => {
      const exp = c.expires_at ? new Date(c.expires_at).toLocaleString('ko-KR', { timeZone: 'Asia/Seoul' }) : '무기한';
      return `
        <div class="code-item">
          <div class="code-main">
            <span class="code-value">${c.code}</span>
            <span class="badge tint">${c.role_label}</span>
            <span class="hint">~${exp}</span>
          </div>
          <button class="btn btn-sm btn-danger" data-revoke="${c.code}">폐기</button>
        </div>
      `;
    }).join('');
    document.querySelectorAll('[data-revoke]').forEach(b => {
      b.addEventListener('click', () => onRevoke(b.dataset.revoke));
    });
  } catch (e) {
    $('#codes-list').innerHTML = `<div class="empty">불러오기 실패: ${e.message}</div>`;
  }
}

async function onCreateCode() {
  const role = $('#role-select').value;
  const expires = parseInt($('#hours-select').value, 10);
  const btn = $('#btn-create-code');
  btn.disabled = true;
  try {
    const r = await fetchJSON('/api/team/codes', {
      method: 'POST',
      body: JSON.stringify({ role, expires_hours: expires }),
    });
    alert(`코드 발급 완료\n\n코드: ${r.code}\n역할: ${r.role_label}\n\n${r.instructions}`);
    await loadCodes();
  } catch (e) {
    alert('실패: ' + e.message);
  } finally {
    btn.disabled = false;
  }
}

async function onRevoke(code) {
  if (!confirm(`코드 ${code} 폐기?`)) return;
  try {
    await fetchJSON(`/api/team/codes/${code}`, { method: 'DELETE' });
    await loadCodes();
  } catch (e) {
    alert('실패: ' + e.message);
  }
}

// ── 멤버 ───────────────────────────────────────
async function loadMembers() {
  try {
    const list = await fetchJSON('/api/team/members');
    const wrap = $('#members-list');
    if (!list.length) {
      wrap.innerHTML = '<div class="empty">멤버가 없습니다.</div>';
      return;
    }
    wrap.innerHTML = list.map(m => {
      const roles = ['manager', 'operator', 'approver', 'viewer', 'guest'];
      const labels = { manager: '최고 팀장', operator: '운영자', approver: '승인자', viewer: '뷰어', guest: '게스트' };
      const isOwner = m.role === 'owner';
      const roleSel = isOwner
        ? '<span class="badge tint">오너 (env)</span>'
        : `<select class="input role-select" data-role="${m.chat_id}" style="width: 130px;">
            ${roles.map(r => `<option value="${r}" ${r === m.role ? 'selected' : ''}>${labels[r]}</option>`).join('')}
          </select>`;
      const actions = isOwner ? '' : `<button class="btn btn-sm btn-danger" data-remove="${m.chat_id}">제거</button>`;
      return `
        <div class="member-item">
          <div class="member-info">
            <div class="member-name">${m.nickname || m.first_name || '(이름 없음)'} ${m.username ? `<span class="hint">@${m.username}</span>` : ''}</div>
            <div class="hint">chat_id: <code>${m.chat_id}</code></div>
          </div>
          <div class="member-controls">
            ${roleSel}
            ${actions}
          </div>
        </div>
      `;
    }).join('');
    document.querySelectorAll('.role-select').forEach(sel => {
      sel.addEventListener('change', () => onRoleChange(sel.dataset.role, sel.value));
    });
    document.querySelectorAll('[data-remove]').forEach(b => {
      b.addEventListener('click', () => onRemoveMember(b.dataset.remove));
    });
  } catch (e) {
    $('#members-list').innerHTML = `<div class="empty">불러오기 실패: ${e.message}</div>`;
  }
}

async function onRoleChange(chatId, role) {
  try {
    await fetchJSON(`/api/team/members/${chatId}`, {
      method: 'PATCH',
      body: JSON.stringify({ role }),
    });
  } catch (e) {
    alert('역할 변경 실패: ' + e.message);
    await loadMembers();
  }
}

async function onRemoveMember(chatId) {
  if (!confirm(`멤버 (${chatId}) 제거?`)) return;
  try {
    await fetchJSON(`/api/team/members/${chatId}`, { method: 'DELETE' });
    await loadMembers();
  } catch (e) {
    alert('제거 실패: ' + e.message);
  }
}

(function main() {
  $('#btn-create-code').addEventListener('click', onCreateCode);
  loadCodes();
  loadMembers();
  setInterval(loadMembers, 15000);
  setInterval(loadCodes, 30000);
})();
