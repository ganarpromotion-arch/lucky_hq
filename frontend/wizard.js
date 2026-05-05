// 직원 추가 마법사
// 1단계: 자연어 → /api/commander/suggest-module 로 모듈 추천
// 2단계: 모듈 선택 → 모듈의 config_schema 기반 동적 폼 → POST /api/agents

let MODULES = [];

async function ensureModules() {
  if (MODULES.length === 0) {
    MODULES = await window.api("/api/agents/modules");
  }
}

window.openWizard = async function () {
  try {
    await ensureModules();
  } catch (e) {
    alert("모듈 카탈로그 로드 실패: " + e.message);
    return;
  }
  const dlg = document.getElementById("wizard");
  document.getElementById("step-1").hidden = false;
  document.getElementById("step-2").hidden = true;
  document.getElementById("wizard-prompt").value = "";
  document.getElementById("wizard-name").value = "";
  document.getElementById("wizard-cron").value = "";
  document.getElementById("wizard-tier").value = "";
  const sug = document.getElementById("wizard-suggestion");
  sug.hidden = true; sug.textContent = "";
  populateModuleSelect();
  dlg.showModal();
};

function populateModuleSelect(selected) {
  const sel = document.getElementById("wizard-module");
  sel.innerHTML = "";
  for (const m of MODULES) {
    const opt = document.createElement("option");
    opt.value = m.slug;
    opt.textContent = `${m.label} (${m.slug})`;
    if (selected && selected === m.slug) opt.selected = true;
    sel.appendChild(opt);
  }
  sel.removeEventListener("change", renderConfigFields);
  sel.addEventListener("change", renderConfigFields);
  renderConfigFields();
}

function currentModule() {
  const slug = document.getElementById("wizard-module").value;
  return MODULES.find(m => m.slug === slug);
}

function renderConfigFields() {
  const m = currentModule();
  const wrap = document.getElementById("wizard-config");
  const desc = document.getElementById("wizard-module-desc");
  wrap.innerHTML = "";
  if (!m) { desc.textContent = ""; return; }
  desc.textContent = m.description || "";

  // cron 기본값을 모듈 기본값으로 채워줌 (사용자가 비웠을 때)
  const cronInput = document.getElementById("wizard-cron");
  if (!cronInput.value && m.default_schedule_cron) {
    cronInput.placeholder = `${m.default_schedule_cron}  (모듈 기본 스케줄)`;
  }

  for (const f of m.config_schema || []) {
    const lbl = document.createElement("label");
    lbl.textContent = (f.label || f.key) + (f.required ? " *" : "");
    let input;
    if (f.type === "select") {
      input = document.createElement("select");
      for (const opt of f.options || []) {
        const o = document.createElement("option");
        o.value = opt; o.textContent = opt;
        if (f.default === opt) o.selected = true;
        input.appendChild(o);
      }
    } else if (f.type === "textarea") {
      input = document.createElement("textarea");
      if (f.default !== undefined) input.value = f.default;
      if (f.placeholder) input.placeholder = f.placeholder;
    } else {
      input = document.createElement("input");
      input.type = f.type === "number" ? "number" : "text";
      if (f.default !== undefined) input.value = f.default;
      if (f.placeholder) input.placeholder = f.placeholder;
    }
    input.dataset.cfgKey = f.key;
    input.dataset.cfgType = f.type || "text";
    wrap.appendChild(lbl);
    wrap.appendChild(input);
  }
}

// 1단계: 추천 받기
document.getElementById("wizard-suggest").addEventListener("click", async () => {
  const text = document.getElementById("wizard-prompt").value.trim();
  if (!text) { alert("어떤 직원이 필요한지 적어주세요."); return; }
  const sug = document.getElementById("wizard-suggestion");
  sug.hidden = false;
  sug.textContent = "추천 중…";
  try {
    const res = await window.api("/api/commander/suggest-module", {
      method: "POST",
      body: { text },
    });
    sug.textContent =
      `추천 모듈: ${res.module || "?"} (fit: ${res.fit || "?"})\n` +
      `이름 제안: ${res.name_suggestion || "-"}\n` +
      `이유: ${res.reason || "-"}` +
      (res.raw ? `\n\n[raw]\n${res.raw}` : "");
    if (res.module && MODULES.find(m => m.slug === res.module)) {
      document.getElementById("step-2").hidden = false;
      populateModuleSelect(res.module);
      if (res.name_suggestion) {
        document.getElementById("wizard-name").value = res.name_suggestion;
      }
    }
  } catch (e) {
    sug.textContent = "오류: " + e.message;
  }
});

// 1단계: 직접 고를게요
document.getElementById("wizard-skip").addEventListener("click", () => {
  document.getElementById("step-2").hidden = false;
  populateModuleSelect();
});

// 2단계: 뒤로
document.getElementById("wizard-back").addEventListener("click", () => {
  document.getElementById("step-2").hidden = true;
});

// 2단계: 생성
document.getElementById("wizard-create").addEventListener("click", async () => {
  const name = document.getElementById("wizard-name").value.trim();
  const module = document.getElementById("wizard-module").value;
  const schedule_cron = document.getElementById("wizard-cron").value.trim() || null;
  const tier = document.getElementById("wizard-tier").value || null;

  if (!name) { alert("이름을 입력하세요."); return; }
  if (!module) { alert("모듈을 선택하세요."); return; }

  const cfg = {};
  document.querySelectorAll("#wizard-config [data-cfg-key]").forEach(el => {
    let val = el.value;
    if (el.dataset.cfgType === "number") val = Number(val);
    cfg[el.dataset.cfgKey] = val;
  });

  try {
    const created = await window.api("/api/agents", {
      method: "POST",
      body: {
        name, module, schedule_cron,
        llm_tier: tier,
        config: cfg,
      },
    });
    document.getElementById("wizard").close();
    window.log(`✅ 직원 생성: ${created.name} (#${created.id})`, "ok");
    window.dispatchEvent(new Event("agents-changed"));
  } catch (e) {
    alert("생성 실패: " + e.message);
  }
});

document.getElementById("wizard-close").addEventListener("click", () => {
  document.getElementById("wizard").close();
});
