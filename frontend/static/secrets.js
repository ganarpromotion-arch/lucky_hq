/* Lucky HQ — API 통합 관리
   카탈로그 기반: 서버에서 등록 가능한 키 목록과 현재 상태를 받아와서 렌더링.
   각 키는 두 모드:
   - 등록됨 → 마스킹 + [수정] 버튼만
   - 미등록 또는 수정중 → 입력창 + [저장] / [취소]
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

const editing = new Set();   // 수정 모드인 키들

function renderItem(item) {
  const isEditing = editing.has(item.key) || !item.has_value;
  const usedBy = (item.used_by || []).join(', ');
  return `
    <div class="secret-item ${item.has_value ? 'is-set' : 'is-empty'}">
      <div class="secret-head">
        <div class="secret-id">
          <div class="secret-label">
            ${item.label}
            ${item.required ? '<span class="badge tint">필수</span>' : ''}
            ${item.has_value ? '<span class="badge ok">등록됨</span>' : '<span class="badge">미등록</span>'}
          </div>
          <div class="secret-key">${item.key}${usedBy ? ` · ${usedBy}` : ''}</div>
        </div>
        ${item.has_value && !isEditing
          ? `<button class="btn btn-sm" data-edit="${item.key}">수정</button>`
          : ''}
      </div>
      <div class="secret-desc">${item.description}${item.docs_url ? ` · <a href="${item.docs_url}" target="_blank" rel="noopener">발급 페이지 ↗</a>` : ''}</div>

      ${isEditing ? `
        <div class="secret-edit">
          <input id="in-${item.key}" type="password" class="input mono" placeholder="키를 붙여넣고 저장" autocomplete="off">
          <div class="secret-actions">
            <button class="btn btn-primary btn-sm" data-save="${item.key}">저장</button>
            ${item.has_value
              ? `<button class="btn btn-danger btn-sm" data-clear="${item.key}">삭제</button>`
              : ''}
            ${item.has_value && editing.has(item.key)
              ? `<button class="btn btn-ghost btn-sm" data-cancel="${item.key}">취소</button>`
              : ''}
            <span class="hint" id="hint-${item.key}"></span>
          </div>
        </div>
      ` : `
        <div class="secret-collapsed">
          <span class="secret-value mono">${item.has_value ? item.value : '(미등록)'}</span>
        </div>
      `}
    </div>
  `;
}

async function load() {
  try {
    const items = await fetchJSON('/api/settings/catalog');
    const wrap = $('#secrets-list');
    if (!items.length) {
      wrap.innerHTML = '<div class="empty">등록 가능한 API가 없습니다.</div>';
      return;
    }
    wrap.innerHTML = items.map(renderItem).join('');
    bindActions();
  } catch (e) {
    $('#secrets-list').innerHTML = `<div class="empty">불러오기 실패: ${e.message}</div>`;
  }
}

function bindActions() {
  document.querySelectorAll('[data-edit]').forEach(btn => {
    btn.addEventListener('click', async () => {
      editing.add(btn.dataset.edit);
      await load();
      const inp = document.getElementById(`in-${btn.dataset.edit}`);
      if (inp) inp.focus();
    });
  });
  document.querySelectorAll('[data-cancel]').forEach(btn => {
    btn.addEventListener('click', async () => {
      editing.delete(btn.dataset.cancel);
      await load();
    });
  });
  document.querySelectorAll('[data-save]').forEach(btn => {
    btn.addEventListener('click', () => save(btn.dataset.save));
  });
  document.querySelectorAll('[data-clear]').forEach(btn => {
    btn.addEventListener('click', () => clear(btn.dataset.clear));
  });
}

async function save(key) {
  const input = document.getElementById(`in-${key}`);
  const hint = document.getElementById(`hint-${key}`);
  const v = input.value.trim();
  if (!v) { hint.textContent = '값을 입력해주세요'; hint.className = 'hint fail'; return; }
  try {
    await fetchJSON(`/api/settings/${key}`, {
      method: 'PUT',
      body: JSON.stringify({ value: v, is_secret: true }),
    });
    editing.delete(key);
    await load();
  } catch (e) {
    hint.textContent = '✗ ' + e.message; hint.className = 'hint fail';
  }
}

async function clear(key) {
  if (!confirm(`${key} 를 삭제할까요?`)) return;
  try {
    await fetchJSON(`/api/settings/${key}`, { method: 'DELETE' });
    editing.delete(key);
    await load();
  } catch (e) {
    const hint = document.getElementById(`hint-${key}`);
    if (hint) { hint.textContent = '✗ ' + e.message; hint.className = 'hint fail'; }
  }
}

load();
