// ─── 공용 fetch 헬퍼 ──────────────────────────────────────────────
window.api = async function(path, opts = {}) {
  const init = {
    headers: { "Content-Type": "application/json" },
    method: opts.method || "GET",
  };
  if (opts.body !== undefined) init.body = JSON.stringify(opts.body);
  const r = await fetch(path, init);
  if (!r.ok) throw new Error(`${r.status} ${await r.text()}`);
  return r.json();
};

// ─── 로그 ────────────────────────────────────────────────────────
function log(msg, level = "info") {
  const ul = document.getElementById("log-list");
  const li = document.createElement("li");
  if (level === "error") li.classList.add("err");
  if (level === "ok") li.classList.add("ok");
  const t = new Date().toLocaleTimeString("ko-KR");
  li.textContent = `[${t}] ${msg}`;
  ul.prepend(li);
  // 최대 200개 유지
  while (ul.children.length > 200) ul.removeChild(ul.lastChild);
}
window.log = log;

// ─── 직원 리스트 ──────────────────────────────────────────────────
async function loadAgents() {
  const list = document.getElementById("agent-list");
  list.innerHTML = '<li class="empty">불러오는 중…</li>';
  try {
    const agents = await window.api("/api/agents");
    list.innerHTML = "";
    if (agents.length === 0) {
      list.innerHTML = '<li class="empty">아직 직원이 없습니다.<br/>+ 직원 추가를 눌러보세요.</li>';
      return;
    }
    for (const a of agents) {
      const li = document.createElement("li");
      const sched = a.schedule_cron ? `⏰ ${a.schedule_cron}` : "수동";
      li.innerHTML = `
        <div class="name">${escapeHtml(a.name)}</div>
        <div class="meta">${escapeHtml(a.module)} · ${a.llm_tier} · ${sched}</div>
        <div class="row">
          <button class="run" data-id="${a.id}">▶ 일하기</button>
          <button class="logs" data-id="${a.id}">기록</button>
        </div>
      `;
      list.appendChild(li);
    }
    list.querySelectorAll("button.run").forEach(b => b.addEventListener("click", onRun));
    list.querySelectorAll("button.logs").forEach(b => b.addEventListener("click", onLogs));
  } catch (e) {
    list.innerHTML = `<li class="empty err">로드 실패: ${escapeHtml(e.message)}</li>`;
    log(`직원 로드 실패: ${e.message}`, "error");
  }
}

async function onRun(e) {
  const btn = e.target;
  const id = btn.dataset.id;
  btn.disabled = true;
  btn.textContent = "실행중…";
  log(`agent:${id} 실행 요청`);
  try {
    const job = await window.api(`/api/agents/${id}/run`, { method: "POST", body: {} });
    log(`agent:${id} → job:${job.id} ${job.status}`, job.status === "done" ? "ok" : "error");
    if (job.error) log(`  error: ${job.error}`, "error");
    if (job.output) renderJob(job);
  } catch (err) {
    log(`agent:${id} 실행 오류: ${err.message}`, "error");
  } finally {
    btn.disabled = false;
    btn.textContent = "▶ 일하기";
  }
}

async function onLogs(e) {
  const id = e.target.dataset.id;
  try {
    const jobs = await window.api(`/api/agents/${id}/jobs?limit=10`);
    log(`agent:${id} 최근 ${jobs.length}건`);
    if (jobs[0]) renderJob(jobs[0]);
    for (const j of jobs.slice(0, 5)) {
      const when = j.finished_at || j.created_at;
      log(`  job:${j.id} ${j.status} (${j.trigger}) @ ${when?.slice(11,19) || "?"}`);
    }
  } catch (err) {
    log(`기록 로드 오류: ${err.message}`, "error");
  }
}

function renderJob(job) {
  const wrap = document.getElementById("job-detail");
  const title = document.getElementById("job-title");
  const body = document.getElementById("job-body");
  wrap.hidden = false;
  title.textContent = `Job #${job.id} — ${job.status} (${job.trigger})`;
  let text = "";
  if (job.error) text += `❌ ${job.error}\n\n`;
  if (job.output) {
    // content/report 같이 사람이 읽는 큰 필드는 위로
    const out = job.output;
    if (out.content) text += `📝 content:\n${out.content}\n\n`;
    if (out.report) text += `📰 report:\n${out.report}\n\n`;
    const others = Object.fromEntries(
      Object.entries(out).filter(([k]) => k !== "content" && k !== "report")
    );
    if (Object.keys(others).length) {
      text += `--- meta ---\n${JSON.stringify(others, null, 2)}`;
    }
  }
  body.textContent = text || "(빈 출력)";
}

function escapeHtml(s) {
  return String(s).replace(/[&<>"']/g, c => ({
    "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;"
  }[c]));
}

// ─── 이벤트 ───────────────────────────────────────────────────────
document.getElementById("btn-new-agent").addEventListener("click", () => window.openWizard());
document.getElementById("btn-refresh").addEventListener("click", loadAgents);
window.addEventListener("agents-changed", loadAgents);

// ─── 부트 ─────────────────────────────────────────────────────────
loadAgents();
log("Lucky HQ 콘솔 부팅 완료", "ok");
