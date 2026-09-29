const BASE = "/api";
const RISK_COLOR = { fire: "#e85d4c", flood: "#3d8fd1", failure: "#d4a017", intrusion: "#9b6bdb" };
const DECISIONS = [
  { id: "dispatch", label: "Выезд" },
  { id: "false_alarm", label: "Ложное" },
  { id: "watch", label: "Наблюдение" },
  { id: "maintenance", label: "ТО" },
];
const ROLES = [
  { id: "dispatcher", label: "Диспетчер" },
  { id: "analyst", label: "Аналитик" },
  { id: "manager", label: "Руководитель" },
];
const THEME_KEY = "ods-theme";

function currentTheme() {
  return document.documentElement.getAttribute("data-theme") === "day" ? "day" : "night";
}

function applyTheme(theme) {
  const next = theme === "day" ? "day" : "night";
  document.documentElement.setAttribute("data-theme", next);
  try {
    localStorage.setItem(THEME_KEY, next);
  } catch {
    /* ignore */
  }
}

const state = {
  role: "dispatcher",
  tab: "board",
  who: "",
  error: null,
  notice: null,
  board: null,
  journal: [],
  filter: "all",
  statusFilter: "open",
  selected: null,
  objectCard: null,
  events: [],
  metrics: null,
  feedback: null,
  audit: [],
  decision: "dispatch",
  reason: "",
  ticket: true,
  busy: false,
};

function pct(value) {
  if (value == null || Number.isNaN(Number(value))) return "—";
  return `${Math.round(Number(value) * 100)}%`;
}

function esc(value) {
  return String(value ?? "")
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;");
}

async function request(path, init = {}) {
  const headers = new Headers(init.headers);
  headers.set("X-User-Login", state.role);
  if (init.body && !headers.has("Content-Type")) headers.set("Content-Type", "application/json");
  const response = await fetch(`${BASE}${path}`, { ...init, headers });
  const text = await response.text();
  const data = text ? JSON.parse(text) : null;
  if (!response.ok) {
    const detail = data && data.detail;
    const message =
      typeof detail === "string"
        ? detail
        : Array.isArray(detail)
          ? detail.map((item) => item.msg).join("; ")
          : `HTTP ${response.status}`;
    const err = new Error(message);
    err.status = response.status;
    throw err;
  }
  return data;
}

function riskTitle(code) {
  return (state.board && state.board.risk_titles && state.board.risk_titles[code]) || code;
}

async function refresh() {
  state.error = null;
  try {
    const me = await request("/auth/me");
    state.who = me.display_name;
    state.board = await request("/dashboard");
    const query = new URLSearchParams({ status: state.statusFilter, limit: "200" });
    if (state.filter !== "all") query.set("risk_type", state.filter);
    state.journal = await request(`/predictions?${query}`);
  } catch (err) {
    state.error = err.message;
  }
  render();
}

async function openCard(row) {
  state.selected = row;
  state.notice = null;
  state.decision = "dispatch";
  state.reason = "";
  state.ticket = true;
  if (!row.object_id) {
    state.objectCard = null;
    state.events = [];
    render();
    return;
  }
  try {
    const [card, events] = await Promise.all([
      request(`/objects/${row.object_id}`),
      request(`/events?object_id=${row.object_id}&limit=30`),
    ]);
    state.objectCard = card;
    state.events = events;
  } catch (err) {
    state.error = err.message;
  }
  render();
}

function listRows() {
  return state.journal;
}

function exportCsv() {
  const rows = state.journal;
  const lines = ["id;object;risk;probability;status;source;horizon"];
  for (const row of rows) {
    const name = String(row.object_name ?? row.object_id ?? "").replaceAll('"', '""');
    lines.push(
      [row.id, `"${name}"`, row.risk_title, Number(row.probability).toFixed(3), row.status, row.source, row.horizon_hours].join(";"),
    );
  }
  const blob = new Blob(["\uFEFF" + lines.join("\n")], { type: "text/csv;charset=utf-8" });
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url;
  a.download = "predictions.csv";
  a.click();
  URL.revokeObjectURL(url);
}

function schemaHtml(pickets, events, objectRisks) {
  const hot = new Map();
  for (const event of events) {
    if (!event.picket || !event.risk) continue;
    const set = hot.get(event.picket) || new Set();
    set.add(event.risk);
    hot.set(event.picket, set);
  }
  const numbered = pickets
    .filter((item) => item.picket !== "без пикета")
    .map((item) => ({ ...item, n: Number((item.picket.match(/\d+/) || [0])[0]) }))
    .sort((a, b) => a.n - b.n);
  const extra = pickets.find((item) => item.picket === "без пикета");
  const highlighted = numbered.filter((item) => hot.has(item.picket));
  const rest = numbered.filter((item) => !hot.has(item.picket));
  const shown =
    numbered.length <= 36
      ? numbered
      : [...highlighted, ...rest.slice(0, Math.max(0, 36 - highlighted.length))].sort((a, b) => a.n - b.n);
  const min = numbered[0] && numbered[0].n;
  const max = numbered.length && numbered[numbered.length - 1].n;
  const chips = shown
    .map((item) => {
      const risks = hot.get(item.picket);
      const cls = [
        "picket",
        risks && risks.has("fire") ? "is-fire" : "",
        risks && risks.has("flood") ? "is-flood" : "",
        risks && risks.has("intrusion") ? "is-guard" : "",
        risks ? "is-hot" : "",
      ]
        .filter(Boolean)
        .join(" ");
      return `<div class="${cls}" title="${esc((item.sensor_types || []).join(", "))}"><span>ПК${esc(item.picket)}</span><small>${item.n_channels}</small></div>`;
    })
    .join("");
  const more = numbered.length > shown.length ? `<div class="picket is-more">ещё ${numbered.length - shown.length}</div>` : "";
  return `
    <section class="schema">
      <header class="schema-head">
        <h3>Схема пикетов</h3>
        <p>Коллектор ${min != null ? `ПК${min} → ПК${max}` : "без разметки пикетов"} · ${numbered.length} пикетов · ${extra ? extra.n_channels + " каналов вне пикетов" : "все каналы привязаны"}</p>
        <p class="schema-note">Не карта Москвы: цвет — сработки на пикете. Риски объекта: ${objectRisks.length ? objectRisks.join(", ") : "нет открытых"}</p>
      </header>
      <div class="picket-line">${chips}${more}</div>
    </section>`;
}

function decisionHtml(pred) {
  if (pred.status !== "open") {
    return `<p class="closed">Решение уже записано: <strong>${esc(pred.status)}</strong> · ${pct(pred.probability)} на ${pred.horizon_hours}ч</p>`;
  }
  if (state.role === "analyst") {
    return `<p class="muted">Аналитик не закрывает карточки — переключитесь на диспетчера.</p>`;
  }
  const chips = DECISIONS.map(
    (item) =>
      `<label class="chip ${state.decision === item.id ? "on" : ""}"><input type="radio" name="decision" value="${item.id}" ${state.decision === item.id ? "checked" : ""}>${item.label}</label>`,
  ).join("");
  return `
    <form class="decision" id="decision-form">
      <div class="decision-row">${chips}</div>
      <textarea id="reason" rows="3" placeholder="Комментарий для журнала (дым на двух пикетах, насосы…)">${esc(state.reason)}</textarea>
      <label class="check"><input type="checkbox" id="make-ticket" ${state.ticket ? "checked" : ""} ${state.decision !== "dispatch" ? "disabled" : ""}> Создать черновик заявки и отправить в заглушку ОДС</label>
      <button type="submit" ${state.busy ? "disabled" : ""}>${state.busy ? "Запись…" : "Зафиксировать решение"}</button>
    </form>`;
}

function cardHtml() {
  const row = state.selected;
  if (!row) return `<p class="muted">Выберите прогноз слева или сценарий сверху.</p>`;
  const obj = state.objectCard;
  const crumb = obj
    ? [...(obj.ancestors || []).map((a) => a.name), obj.name].join(" / ")
    : row.object_name || "";
  const events = (state.events || [])
    .slice(0, 8)
    .map(
      (event) =>
        `<li><time>${esc(String(event.event_ts).replace("T", " ").slice(0, 16))}</time><span>${event.picket ? "ПК" + esc(event.picket) : "—"} · ${esc(event.sensor_type)} · ${esc(event.value_cat)}</span></li>`,
    )
    .join("");
  return `
    <p class="crumb">${esc(crumb)}</p>
    <h2>${esc(row.risk_title)}<small>${pct(row.probability)} на ${row.horizon_hours} ч</small></h2>
    <p class="explain">${esc(row.explanation)}</p>
    <p class="reco"><strong>Рекомендация.</strong> ${esc(row.recommendation)}</p>
    ${obj ? schemaHtml(obj.pickets || [], state.events, (obj.predictions || []).map((p) => p.risk_type)) : ""}
    <h3>Последние тревоги</h3>
    <ul class="events">${events || `<li class="muted">В витрине нет тревог по объекту.</li>`}</ul>
    ${decisionHtml(row)}`;
}

function analystHtml() {
  const m = state.metrics;
  let metricsBlock = "";
  if (state.role === "dispatcher") metricsBlock = `<p class="muted">Для метрик моделей переключитесь на аналитика.</p>`;
  if (m && m.available === false) metricsBlock += `<p class="error">${esc(m.detail)}</p>`;
  if (m && m.available) {
    metricsBlock += `<h2>LightGBM, test 2026</h2><p class="muted">обучено ${esc(m.trained_at)}${m.retrained_at ? " · дообучение " + esc(m.retrained_at) : ""}</p><table><thead><tr><th>Риск</th><th>Precision</th><th>Recall</th><th>PR-AUC</th><th>Порог</th></tr></thead><tbody>${(m.test_lgbm || [])
      .map((row) => `<tr><td>${esc(row.title)}</td><td>${pct(row.precision)}</td><td>${pct(row.recall)}</td><td>${Number(row.pr_auc).toFixed(3)}</td><td>${row.calibrated_threshold != null ? Number(row.calibrated_threshold).toFixed(3) : row.threshold != null ? Number(row.threshold).toFixed(3) : "—"}</td></tr>`)
      .join("")}</tbody></table>`;
    if (m.calibration && m.calibration.length) {
      metricsBlock += `<h2>Калибровка порогов</h2><p class="muted">Ложные поднимают порог, выезды слегка опускают. Пишется в Postgres, не в metrics.json.</p><table><thead><tr><th>Риск</th><th>Порог</th><th>Выезды</th><th>Ложные</th></tr></thead><tbody>${m.calibration
        .map((row) => `<tr><td>${esc(row.title)}</td><td>${Number(row.threshold).toFixed(3)}</td><td>${row.n_dispatch}</td><td>${row.n_false_alarm}</td></tr>`)
        .join("")}</tbody></table>`;
    }
  }
  const canCalibrate = state.role === "analyst" || state.role === "manager";
  const calBtn = canCalibrate
    ? `<p><button class="ghost" id="calibrate" ${state.busy ? "disabled" : ""}>Калибровать пороги</button></p>`
    : `<p class="muted">Калибровка порогов — роль аналитика или руководителя.</p>`;
  const fb = state.feedback
    ? `<h2>Решения диспетчера</h2><p>Открыто ${state.feedback.open}, закрыто ${state.feedback.closed}. ${(state.feedback.decisions || []).map((d) => `${d.title}: ${d.n}`).join(" · ") || "пока пусто"}</p>`
    : "";
  const audit =
    state.role === "manager" && state.audit.length
      ? `<h2>Аудит</h2><ul class="events">${state.audit.map((row) => `<li><time>${esc(String(row.created_at || "").replace("T", " ").slice(0, 16))}</time><span>${esc(row.user_login)} · ${esc(row.action)}</span></li>`).join("")}</ul>`
      : "";
  return `<section class="analyst"><p class="note">Метки эвристические: модель предсказывает сработку подсистемы, а не подтверждённый пожар. Решения диспетчера копятся в журнале обратной связи.</p>${metricsBlock}${calBtn}${fb}${audit}</section>`;
}

function render() {
  const root = document.getElementById("root");
  const board = state.board;
  const kpis = board
    ? `<section class="kpis">
        <article><span>Открыто</span><strong>${board.open_total}</strong></article>
        <article><span>Объектов</span><strong>${board.objects_at_risk}</strong></article>
        ${Object.entries(board.open_by_risk || {})
          .map(([code, n]) => `<article style="border-color:${RISK_COLOR[code] || "var(--border)"}"><span>${esc(riskTitle(code))}</span><strong>${n}</strong></article>`)
          .join("")}
      </section>`
    : "";
  const critical =
    board && board.critical && board.critical.length
      ? `<section class="critical"><h2>Критичные (≥ 80%)</h2><ul>${board.critical
          .slice(0, 6)
          .map((row) => `<li><button data-open="${row.id}">${esc(row.object_name)} · ${esc(row.risk_title)} · ${pct(row.probability)}</button></li>`)
          .join("")}</ul></section>`
      : "";
  const rows = listRows();
  const list = rows
    .map((row) => {
      const on = state.selected && state.selected.id === row.id ? "on" : "";
      return `<li><button class="pred ${on}" data-open="${row.id}"><i style="background:${RISK_COLOR[row.risk_type] || "var(--muted)"}"></i><span class="pred-main"><strong>${esc(row.object_name || "объект " + row.object_id)}</strong><em>${esc(row.risk_title)} · ${esc(row.source)} · ${esc(row.status)}</em></span><b>${pct(row.probability)}</b></button></li>`;
    })
    .join("");
  root.innerHTML = `
    <header class="top">
      <div>
        <h1>Москоллектор · ОДС</h1>
        <p>Прогноз инцидентов на 24 часа. Сервис не управляет оборудованием.</p>
      </div>
      <div class="top-actions">
        <label>Роль
          <select id="role">${ROLES.map((r) => `<option value="${r.id}" ${state.role === r.id ? "selected" : ""}>${r.label}</option>`).join("")}</select>
        </label>
        <button type="button" class="ghost theme-toggle" id="theme-toggle">${currentTheme() === "day" ? "Ночь" : "День"}</button>
        <span class="who">${esc(state.who)}</span>
      </div>
    </header>
    <nav class="tabs">
      <button data-tab="board" class="${state.tab === "board" ? "on" : ""}">Дашборд</button>
      <button data-tab="journal" class="${state.tab === "journal" ? "on" : ""}">Журнал</button>
      <button data-tab="analyst" class="${state.tab === "analyst" ? "on" : ""}">Аналитика</button>
      <div class="grow"></div>
      <button class="ghost" data-scenario="fire">Сценарий: пожар</button>
      <button class="ghost" data-scenario="flood">Сценарий: подтопление</button>
      ${state.role !== "analyst" ? `<button class="ghost" id="stream-tick">Пачка СМВУ</button>` : ""}
    </nav>
    ${state.error ? `<p class="banner error">${esc(state.error)}</p>` : ""}
    ${state.notice ? `<p class="banner ok">${esc(state.notice)}</p>` : ""}
    ${state.tab !== "analyst" ? kpis + critical : ""}
    ${state.tab !== "analyst" && board && board.alarm_window ? `<p class="muted">Окно витрины: ${esc(String(board.alarm_window.from || "").replace("T", " ").slice(0, 16))} → ${esc(String(board.alarm_window.to || "").replace("T", " ").slice(0, 16))}${board.season && board.season.flood_season ? " · сезон паводков" : ""}${board.season && board.season.heating_season ? " · отопительный сезон" : ""} · SLO 5 мин</p>` : ""}
    ${
      state.tab === "analyst"
        ? analystHtml()
        : `<div class="layout">
            <section class="list-pane">
              <div class="filters">
                <select id="filter">
                  <option value="all">Все риски</option>
                  <option value="fire">Пожар</option>
                  <option value="flood">Подтопление</option>
                  <option value="failure">Отказ</option>
                  <option value="intrusion">НСД</option>
                </select>
                <select id="status">
                  <option value="open">Открытые</option>
                  <option value="all">Все статусы</option>
                  <option value="dispatch">Выезд</option>
                  <option value="false_alarm">Ложное</option>
                  <option value="watch">Наблюдение</option>
                  <option value="maintenance">ТО</option>
                </select>
                <button class="ghost" id="csv">CSV</button>
                <button class="ghost" id="pdf">PDF / печать</button>
              </div>
              <ul class="pred-list">${list || `<li class="muted">Нет прогнозов по фильтру.</li>`}</ul>
            </section>
            <section class="card-pane print-card">${cardHtml()}</section>
          </div>`
    }
  `;
  const filterEl = document.getElementById("filter");
  const statusEl = document.getElementById("status");
  if (filterEl) filterEl.value = state.filter;
  if (statusEl) statusEl.value = state.statusFilter;
  bind();
}

function bind() {
  document.getElementById("theme-toggle")?.addEventListener("click", () => {
    applyTheme(currentTheme() === "night" ? "day" : "night");
    const btn = document.getElementById("theme-toggle");
    if (btn) btn.textContent = currentTheme() === "day" ? "Ночь" : "День";
  });
  document.getElementById("role")?.addEventListener("change", async (e) => {
    state.role = e.target.value;
    state.selected = null;
    await refresh();
    if (state.tab === "analyst") await loadAnalyst();
  });
  document.querySelectorAll("[data-tab]").forEach((btn) =>
    btn.addEventListener("click", async () => {
      state.tab = btn.getAttribute("data-tab");
      if (state.tab === "analyst") await loadAnalyst();
      else render();
    }),
  );
  document.querySelectorAll("[data-scenario]").forEach((btn) =>
    btn.addEventListener("click", () => applyScenario(btn.getAttribute("data-scenario"))),
  );
  document.querySelectorAll("[data-open]").forEach((btn) =>
    btn.addEventListener("click", () => {
      const id = Number(btn.getAttribute("data-open"));
      const row =
        (state.board && state.board.critical.concat(state.board.top).find((item) => item.id === id)) ||
        state.journal.find((item) => item.id === id);
      if (row) openCard(row);
    }),
  );
  document.getElementById("filter")?.addEventListener("change", (e) => {
    state.filter = e.target.value;
    refresh();
  });
  document.getElementById("status")?.addEventListener("change", (e) => {
    state.statusFilter = e.target.value;
    refresh();
  });
  document.getElementById("csv")?.addEventListener("click", exportCsv);
  document.getElementById("pdf")?.addEventListener("click", () => window.print());
  document.getElementById("stream-tick")?.addEventListener("click", runStreamTick);
  document.getElementById("calibrate")?.addEventListener("click", runCalibrate);
  document.querySelectorAll("input[name=decision]").forEach((input) =>
    input.addEventListener("change", (e) => {
      state.decision = e.target.value;
      render();
    }),
  );
  document.getElementById("reason")?.addEventListener("input", (e) => {
    state.reason = e.target.value;
  });
  document.getElementById("make-ticket")?.addEventListener("change", (e) => {
    state.ticket = e.target.checked;
  });
  document.getElementById("decision-form")?.addEventListener("submit", submitDecision);
}

async function runStreamTick() {
  state.busy = true;
  state.error = null;
  render();
  try {
    const risk = state.filter === "all" ? "mix" : state.filter;
    const data = await request(`/stream/tick?scenario=${encodeURIComponent(risk)}&n=8`, { method: "POST" });
    state.notice = `Пачка СМВУ: +${data.inserted} тревог, ${data.rules_written} правил, ${data.latency_ms} мс (SLO 5 мин).`;
    await refresh();
    const first = (data.predictions || [])[0];
    if (first) await openCard(first);
  } catch (err) {
    state.error = err.message;
  }
  state.busy = false;
  render();
}

async function runCalibrate() {
  state.busy = true;
  state.error = null;
  render();
  try {
    const data = await request("/analytics/retrain", { method: "POST" });
    const bits = (data.applied || []).map((row) => `${row.risk}: ${row.base_threshold}→${row.threshold}`).join(", ");
    state.notice = `Пороги калиброваны. ${bits || "без изменений"}`;
    await loadAnalyst();
  } catch (err) {
    state.error = err.message;
  }
  state.busy = false;
  render();
}

async function applyScenario(risk) {
  state.tab = "board";
  state.filter = risk;
  state.statusFilter = "open";
  await refresh();
  const pool = ((state.board && state.board.top) || []).filter((row) => row.risk_type === risk && row.status === "open");
  const first = pool[0] || state.journal.find((row) => row.risk_type === risk);
  if (first) await openCard(first);
  else {
    state.notice = risk === "fire" ? "Нет открытых карточек «пожар»." : "Нет открытых карточек «подтопление».";
    render();
  }
}

async function submitDecision(event) {
  event.preventDefault();
  if (!state.selected) return;
  state.busy = true;
  render();
  try {
    const updated = await request(`/predictions/${state.selected.id}/decision`, {
      method: "PATCH",
      body: JSON.stringify({
        decision: state.decision,
        reason: state.reason.trim() || undefined,
        create_ticket: state.ticket && state.decision === "dispatch",
      }),
    });
    let notice = `Решение записано.`;
    if (updated.ticket_id) {
      try {
        const sent = await request(`/integrations/tickets?draft_id=${updated.ticket_id}`, { method: "POST" });
        notice += ` Заявка ${sent.external_id}.`;
      } catch {
        notice += ` Черновик заявки №${updated.ticket_id}.`;
      }
    }
    state.selected = updated;
    state.notice = notice;
    await refresh();
  } catch (err) {
    state.error = err.message;
    state.busy = false;
    render();
    return;
  }
  state.busy = false;
  render();
}

async function loadAnalyst() {
  try {
    state.metrics = await request("/analytics/metrics");
  } catch (err) {
    state.metrics = { available: false, detail: err.message };
  }
  try {
    state.feedback = await request("/analytics/feedback");
  } catch {
    state.feedback = null;
  }
  if (state.role === "manager") {
    try {
      state.audit = await request("/audit?limit=30");
    } catch {
      state.audit = [];
    }
  }
  render();
}

refresh();
