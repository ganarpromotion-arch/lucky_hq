/* Lucky HQ — 음악제작 부서
   - 작곡 요청 / 폴링 / 결과 표시
*/
const $ = (s) => document.querySelector(s);

async function fetchJSON(path, opts) {
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

function statusBadge(status) {
  const map = {
    pending: { cls: 'run',  label: '준비 중'   },
    running: { cls: 'run',  label: '작곡 중'   },
    done:    { cls: 'ok',   label: '완료'     },
    failed:  { cls: 'fail', label: '실패'     },
  };
  const m = map[status] || { cls: 'idle', label: status || '-' };
  return `<span class="badge ${m.cls}"><span class="dot ${m.cls}"></span>${m.label}</span>`;
}

function renderJobs(jobs) {
  const wrap = $('#jobs');
  if (!jobs.length) {
    wrap.innerHTML = '<div class="empty">아직 의뢰한 곡이 없습니다.</div>';
    return;
  }
  wrap.innerHTML = jobs.map(j => {
    const title = (j.input && j.input.title) || `Job #${j.id}`;
    const style = (j.input && j.input.style) || '';
    const audio = j.audio_url
      ? `<div class="player"><audio controls preload="none" src="${j.audio_url}"></audio></div>`
      : '';
    const err = j.status === 'failed'
      ? `<div class="player" style="color: var(--danger); font-family: var(--font-mono); font-size: 11px;">에러: ${j.error || '알 수 없음'}</div>`
      : '';
    return `
      <div class="job" data-id="${j.id}">
        ${statusBadge(j.status)}
        <div class="meta">
          <div class="title">${title}</div>
          <div class="sub">${style ? style + ' · ' : ''}${fmtTime(j.created_at)}</div>
        </div>
        <div class="hint">#${j.id}</div>
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

    // 진행 중 작업이 있으면 그것만 별도 폴링 (서버에서 query 호출하도록 트리거)
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
  } catch (e) {
    // 무시
  }
}

async function onGenerate() {
  const lyrics = $('#f-lyrics').value.trim();
  const style  = $('#f-style').value.trim() || 'pop';
  const title  = $('#f-title').value.trim();

  if (!lyrics) {
    $('#form-hint').textContent = '⚠ 가사를 입력해주세요.';
    $('#form-hint').style.color = 'var(--danger)';
    return;
  }

  const btn = $('#btn-generate');
  btn.disabled = true;
  const orig = btn.textContent;
  btn.textContent = '▷ 의뢰 중…';

  try {
    await fetchJSON('/api/music/generate', {
      method: 'POST',
      body: JSON.stringify({ lyrics, style, title }),
    });
    $('#form-hint').textContent = '✓ 의뢰 완료. 작업 목록에서 진행 상황 확인';
    $('#form-hint').style.color = 'var(--clover-green)';
    await refreshJobs();
  } catch (e) {
    $('#form-hint').textContent = '✗ 실패: ' + e.message;
    $('#form-hint').style.color = 'var(--danger)';
  } finally {
    btn.disabled = false;
    btn.textContent = orig;
  }
}

(function main() {
  $('#btn-generate').addEventListener('click', onGenerate);
  refreshJobs();
  setInterval(refreshJobs, 5000);
})();
