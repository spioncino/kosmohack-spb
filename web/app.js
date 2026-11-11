const LEVEL_COLOR = { none: "#a5d6a7", elevated: "#ffe082", high: "#ef9a9a", critical: "#ce93d8", unknown: "#e0e0e0" };
const KIND_LABEL = { observation: "наблюдение", external_forecast: "внешний прогноз", computed: "наш расчёт" };
const STATE_LABEL = { active: "активен", frozen: "заморожен", disabled: "отключён" };
const STATE_ACTIONS = [["frozen", "заморозить"], ["disabled", "отключить"], ["active", "активировать"]];
const SAMPLE = new URLSearchParams(location.search).get("sample") === "1";
const SAMPLE_URL = "sample_result.json";
const POLL_MS = 1500;
let workerTick = null;

function isoZ(local) { return local ? local + ":00Z" : null; }
function ms(iso) { return new Date(iso).getTime(); }
function fmt(iso) { return iso ? iso.replace(/\.\d+/, "").replace("+00:00", "Z").replace("T", " ").replace(/:00Z$/, "Z") : ""; }
function esc(value) { return String(value).replace(/[&<>"]/g, c => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", "\"": "&quot;" })[c]); }
function hours(perLine, keys) { return Object.values(perLine).reduce((s, h) => s + keys.reduce((a, k) => a + h[k], 0), 0).toFixed(2); }
function evidenceLink(p) { return `<a href="#" data-raw="${esc(p.raw_id)}">открыть запись</a>`; }
function sleep(msec) { return new Promise(resolve => setTimeout(resolve, msec)); }

function ago(iso, now = Date.now()) {
  if (!iso) return "никогда";
  const min = Math.floor((now - ms(iso)) / 60000);
  if (min < 1) return "только что";
  if (min < 60) return `${min} мин назад`;
  if (min < 48 * 60) return `${Math.floor(min / 60)} ч назад`;
  return `${Math.floor(min / 1440)} дн назад`;
}

function cadence(seconds) {
  if (seconds >= 3600) { const h = seconds / 3600; return `${Number.isInteger(h) ? h : h.toFixed(1)} ч`; }
  return `${Math.round(seconds / 60)} мин`;
}

async function readError(r) {
  const contentType = r.headers.get("content-type") || "";
  if (contentType.includes("application/json")) {
    const data = await r.json();
    if (data.error) return data.error.message;
  }
  return `HTTP ${r.status}`;
}

function postJson(url, body) {
  return fetch(url, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) });
}

async function fetchHealth() {
  const box = document.getElementById("worker");
  try {
    const r = await fetch("/health");
    if (!r.ok) { box.textContent = "воркер: состояние неизвестно"; return null; }
    const health = await r.json();
    workerTick = health.worker_last_tick;
    box.textContent = workerTick ? `воркер: тик ${ago(workerTick)}` : "воркер: не запускался";
    return health;
  } catch (e) { box.textContent = "воркер: состояние неизвестно"; return null; }
}

function progressText(job) {
  if (job.status === "queued") return `задание ${job.id} в очереди (воркер: ${workerTick ? "тик " + ago(workerTick) : "не запускался"})`;
  const p = job.progress;
  return p ? `задание ${job.id}: ${p.step} ${(p.fraction * 100).toFixed(0)}%` : `задание ${job.id}: ${job.status}`;
}

async function waitJob(jobId, onProgress) {
  for (;;) {
    const r = await fetch(`/jobs/${jobId}`);
    if (!r.ok) throw new Error(await readError(r));
    const job = await r.json();
    if (job.status === "done" || job.status === "failed") return job;
    if (job.status === "queued") await fetchHealth();
    onProgress(job);
    await sleep(POLL_MS);
  }
}

async function load(body) {
  const status = document.getElementById("status");
  status.textContent = SAMPLE ? "загрузка примера…" : "постановка задания…";
  try {
    if (SAMPLE) {
      const r = await fetch(SAMPLE_URL);
      if (!r.ok) { status.textContent = "ошибка: " + await readError(r); return; }
      render(await r.json());
      status.textContent = "пример: ответ взят из sample_result.json, форма не использована";
      return;
    }
    const created = await postJson("/jobs", body);
    if (!created.ok) { status.textContent = "ошибка: " + await readError(created); return; }
    const job = await waitJob((await created.json()).job_id, j => { status.textContent = progressText(j); });
    if (job.status === "failed") { status.textContent = `задание ${job.id} не выполнено: ${job.error}`; return; }
    const r = await fetch(`/assessments/${job.assessment_id}`);
    if (!r.ok) { status.textContent = "результат недоступен: " + await readError(r); return; }
    render(await r.json());
    status.textContent = `готово, задание ${job.id}`;
  } catch (e) { status.textContent = "запрос не выполнен: " + e; }
}

function constraintsOf(f) {
  const fraction = (f.get("min_sunlit_fraction") || "").trim();
  const startInDaylight = f.get("start_in_daylight") === "on";
  if (fraction === "" && !startInDaylight) return null;
  return { min_sunlit_fraction: fraction === "" ? null : Number(fraction), start_in_daylight: startInDaylight };
}

function submit(ev) {
  ev.preventDefault();
  const f = new FormData(ev.target);
  const body = {
    mode: f.get("mode"), cutoff: f.get("mode") === "replay" ? isoZ(f.get("cutoff")) : null,
    eva_start: isoZ(f.get("eva_start")), duration_hours: Number(f.get("duration_hours")),
    search_start: isoZ(f.get("search_start")), search_end: isoZ(f.get("search_end")),
  };
  const constraints = constraintsOf(f);
  if (constraints) body.constraints = constraints;
  load(body);
}

function illuminationText(res) {
  const line = res.lines.find(l => l.line === "illumination");
  if (!line) return "Освещённость: линии illumination в ответе нет.";
  const m = line.metrics;
  if (line.availability !== "available" || m.sunlit_fraction == null) return `Освещённость: данные ${esc(line.availability)}.`;
  const req = res.requested_window.window;
  const atStart = line.intervals.find(iv => ms(iv.start) <= ms(req.start) && ms(req.start) < ms(iv.end));
  const start = atStart ? (atStart.level === "none" ? "на свету" : "в тени") : "вне интервалов линии";
  return `Освещённость за период поиска (${KIND_LABEL.computed}, условие работ, не опасность): доля света ${(m.sunlit_fraction * 100).toFixed(0)} %, ` +
    `самая длинная тень ${m.longest_shadow_minutes.toFixed(0)} мин; старт запрошенного окна ${start}.`;
}

function render(res) {
  document.getElementById("result").hidden = false;
  document.getElementById("version").textContent = "алгоритм " + res.algorithm_version;
  document.getElementById("meta").textContent = `${res.request.mode}, момент данных: ${res.asof ? fmt(res.asof) : "все доступные сегодня"}, id ${res.id}`;
  const rec = res.recommendation;
  document.getElementById("recommendation").innerHTML = rec.insufficient
    ? `<strong>Оснований для выбора недостаточно.</strong> ${esc(rec.reason)}`
    : `<strong>Предпочтительное окно:</strong> ${fmt(rec.preferred.start)} – ${fmt(rec.preferred.end)}. ${esc(rec.reason)}` +
      (rec.equivalents.length ? ` Равнозначные: ${rec.equivalents.map(w => fmt(w.start)).join(", ")}.` : "");
  renderVerification(res.verification);
  renderTimeline(res);
  const warnings = document.getElementById("warnings");
  warnings.innerHTML = "";
  for (const line of res.lines) {
    const head = document.createElement("li");
    const metrics = Object.entries(line.metrics).map(([k, v]) => `${esc(k)} ${v}`).join(", ");
    head.innerHTML = `<strong>${esc(line.line)}</strong> (${esc(line.mechanism)}): данные ${esc(line.availability)}, уверенность ${esc(line.confidence.grade)}, зависит от: ${esc(line.confidence.depends_on)}` +
      (metrics ? `<br><span class="metrics">значения: ${metrics}</span>` : "");
    warnings.appendChild(head);
    if (line.line === "illumination") {
      // Тень это условие работ: интервалы видны на таймлайне и в блоке траектории, в предупреждения не попадают.
      head.innerHTML += `<br><span class="metrics">тень не предупреждение: в штрафе окон не участвует, применяется только как явно заданное ограничение</span>`;
      continue;
    }
    for (const iv of line.intervals.filter(i => i.level !== "none")) {
      const li = document.createElement("li");
      li.innerHTML = `<span class="level-${iv.level}">${iv.level}</span> ${fmt(iv.start)} – ${fmt(iv.end)} <span class="kind">${KIND_LABEL[iv.kind]}</span>
        <details><summary>правило и источники</summary><div>${esc(iv.rule)}</div>${iv.note ? `<div>${esc(iv.note)}</div>` : ""}<ul>${iv.provenance.map(p =>
        `<li>${esc(p.source)}/${esc(p.product)}, версия ${esc(p.data_version)}, доступно с ${p.published_at ? fmt(p.published_at) : "неизвестно"} (${esc(p.published_at_kind)}) ${evidenceLink(p)}</li>`).join("")}</ul></details>`;
      warnings.appendChild(li);
    }
  }
  const tbody = document.querySelector("#windows tbody");
  tbody.innerHTML = "";
  for (const s of res.windows) {
    const tr = document.createElement("tr");
    const preferred = rec.preferred && ms(s.window.start) === ms(rec.preferred.start);
    tr.className = preferred ? "preferred" : (s.excluded_reason ? "excluded" : "");
    tr.innerHTML = `<td>${fmt(s.window.start)}</td><td>${fmt(s.window.end)}</td><td>${hours(s.per_line, ["high", "critical"])}</td><td>${hours(s.per_line, ["elevated"])}</td><td>${(s.completeness * 100).toFixed(0)}%</td><td>${esc(s.excluded_reason || "")}</td>`;
    tbody.appendChild(tr);
  }
  const t = res.trajectory;
  document.getElementById("trajectory").innerHTML = `Источник: ${esc(t.provider)}, ${esc(t.provenance.source)}/${esc(t.provenance.product)}, эпоха ${fmt(t.epoch)}, ` +
    `${t.is_reconstruction ? "реконструкция" : "прогноз на момент данных"}${t.age_at_query_hours != null ? ", давность " + t.age_at_query_hours.toFixed(1) + " ч" : ""} ` +
    evidenceLink(t.provenance) + `<br>${illuminationText(res)}`;
  renderMap(res);
  document.getElementById("limitations").innerHTML = res.limitations.map(l => `<li>${esc(l)}</li>`).join("");
  document.getElementById("rules").innerHTML = res.rules.map(r => `<li>${esc(r)}</li>`).join("");
  document.getElementById("export").href = `/assessments/${res.id}/export`;
  document.getElementById("json").href = SAMPLE ? SAMPLE_URL : `/assessments/${res.id}`;
}

function minutes(value) { return value == null ? "не определена" : `${value.toFixed(0)} мин`; }
function windowText(w) { return w ? `${fmt(w.start)} – ${fmt(w.end)}` : "оснований для выбора нет"; }

function renderVerification(v) {
  const box = document.getElementById("verification");
  if (!v) { box.hidden = true; box.innerHTML = ""; return; }
  box.hidden = false;
  box.innerHTML = `<strong>Сверка с фактом</strong> <span class="kind">тот же расчёт по всем доступным сегодня данным, на рекомендацию не влияет</span>` +
    `<ul class="verification"><li>Заблаговременность: ${minutes(v.lead_time_minutes)}${v.first_observed_alarm ? ` (первый факт ≥ high ${fmt(v.first_observed_alarm)})` : ""}</li>` +
    `<li>Попадания ${v.hit_minutes.toFixed(0)} мин, пропуски ${v.miss_minutes.toFixed(0)} мин, ложные тревоги ${v.false_alarm_minutes.toFixed(0)} мин, ` +
    `спокойно у обоих ${v.quiet_minutes.toFixed(0)} мин, без факта ${v.unverified_minutes.toFixed(0)} мин, прогноз без уровня ${v.predicted_unknown_minutes.toFixed(0)} мин</li>` +
    `<li>Рекомендация ${v.window_choice_stable ? "совпала с фактом" : "не совпала с фактом"}: по факту ${windowText(v.observed_preferred)}; данные факта ${esc(v.observed_availability)}</li>` +
    `<li class="metrics">${esc(v.note)}</li></ul>`;
}

function renderTimeline(res) {
  const traces = [];
  for (const line of res.lines) {
    for (const iv of line.intervals) {
      traces.push({ type: "bar", orientation: "h", x: [ms(iv.end) - ms(iv.start)], base: [iv.start], y: [line.line],
        marker: { color: LEVEL_COLOR[iv.level] }, name: iv.level, hovertext: `${iv.level} ${KIND_LABEL[iv.kind]}<br>${fmt(iv.start)} – ${fmt(iv.end)}`, hoverinfo: "text", showlegend: false });
    }
  }
  if (res.verification) {
    const row = `${res.verification.line}: факт`;
    for (const iv of res.verification.observed) {
      traces.push({ type: "bar", orientation: "h", x: [ms(iv.end) - ms(iv.start)], base: [iv.start], y: [row],
        marker: { color: LEVEL_COLOR[iv.level], pattern: { shape: "/", fgcolor: "#37474f", size: 6, solidity: 0.3 }, line: { color: "#37474f", width: 1, dash: "dot" } },
        name: `факт ${iv.level}`, hovertext: `факт: ${iv.level} ${KIND_LABEL[iv.kind]}<br>${fmt(iv.start)} – ${fmt(iv.end)}`, hoverinfo: "text", showlegend: false });
    }
  }
  const req = res.requested_window.window;
  const shapes = [{ type: "rect", xref: "x", yref: "paper", x0: req.start, x1: req.end, y0: 0, y1: 1, line: { color: "#1565c0", width: 2 }, fillcolor: "rgba(21,101,192,0.08)" }];
  if (res.recommendation.preferred) {
    const p = res.recommendation.preferred;
    shapes.push({ type: "rect", xref: "x", yref: "paper", x0: p.start, x1: p.end, y0: 0, y1: 1, line: { color: "#2e7d32", width: 2, dash: "dot" } });
  }
  traces.push({ type: "scatter", mode: "lines", x: res.trajectory.samples.map(s => s.t), y: res.trajectory.samples.map(s => s.mlat_deg), yaxis: "y2", name: "AACGM широта, °", line: { color: "#455a64", width: 1 } });
  Plotly.newPlot("timeline", traces, { barmode: "overlay", height: 320, margin: { t: 10 }, xaxis: { type: "date" },
    yaxis: { title: "линии" }, yaxis2: { title: "AACGM, °", overlaying: "y", side: "right", range: [-90, 90] }, shapes }, { responsive: true });
}

function renderMap(res) {
  const req = res.requested_window.window;
  const inWin = res.trajectory.samples.filter(s => ms(s.t) >= ms(req.start) && ms(s.t) <= ms(req.end));
  const traces = [];
  for (const level of Object.keys(LEVEL_COLOR)) {
    const pts = inWin.filter(s => s.level === level);
    if (!pts.length) continue;
    traces.push({ type: "scattergeo", mode: "markers", lat: pts.map(s => s.lat_deg), lon: pts.map(s => s.lon_deg),
      marker: { color: LEVEL_COLOR[level], size: 5 }, name: `SEP: ${level}`,
      text: pts.map(s => `${fmt(s.t)}<br>${s.sunlit ? "свет" : "тень"}${s.in_saa ? ", ЮАА" : ""}<br>AACGM ${s.mlat_deg == null ? "н/д" : s.mlat_deg.toFixed(1) + "°"}`), hoverinfo: "text" });
  }
  const shadow = inWin.filter(s => !s.sunlit);
  if (shadow.length) traces.push({ type: "scattergeo", mode: "markers", lat: shadow.map(s => s.lat_deg), lon: shadow.map(s => s.lon_deg),
    marker: { color: "#37474f", size: 2 }, name: "тень", hoverinfo: "skip" });
  if (inWin.length) {
    const a = inWin[0], b = inWin[inWin.length - 1];
    traces.push({ type: "scattergeo", mode: "markers+text", lat: [a.lat_deg, b.lat_deg], lon: [a.lon_deg, b.lon_deg],
      text: ["выход " + fmt(a.t), "возврат " + fmt(b.t)], textposition: "top center", marker: { color: "#1565c0", size: 10, symbol: ["circle", "square"] }, name: "начало и конец ВКД" });
  }
  Plotly.newPlot("map", traces, { height: 420, margin: { t: 10, b: 10 }, geo: { projection: { type: "equirectangular" }, showland: true, landcolor: "#f0f0f0", showcountries: true, lataxis: { range: [-90, 90] }, lonaxis: { range: [-180, 180] } }, legend: { orientation: "h" } }, { responsive: true });
}

async function openEvidence(rawId) {
  const status = document.getElementById("status");
  let data;
  try {
    const r = await fetch(`/evidence/${rawId}`);
    if (!r.ok) { status.textContent = "запись недоступна: " + await readError(r); return; }
    data = await r.json();
  } catch (e) { status.textContent = "запись недоступна: " + e; return; }
  const box = document.getElementById("evidence");
  box.hidden = false;
  document.getElementById("evidence-meta").textContent = JSON.stringify(data.record, null, 1);
  document.getElementById("evidence-body").textContent = data.content_text != null ? data.content_text.slice(0, 20000) : `двоичный файл, ${data.record.size} байт: ${data.download_url}`;
  box.scrollIntoView();
}

function sourceRow(s) {
  const tr = document.createElement("tr");
  tr.className = `state-${s.state}`;
  const actions = STATE_ACTIONS.filter(([state]) => state !== s.state)
    .map(([state, label]) => `<button type="button" data-source="${esc(s.name)}" data-state="${state}">${label}</button>`);
  actions.push(`<button type="button" data-source="${esc(s.name)}" data-refresh="1"${s.state === "active" ? "" : " disabled"}>обновить</button>`);
  tr.innerHTML = `<td>${esc(s.name)}</td><td>${s.products.map(esc).join(", ")}</td><td>${cadence(s.cadence_s)}</td>` +
    `<td>${STATE_LABEL[s.state] || esc(s.state)}</td><td>${esc(s.reason || "")}</td>` +
    `<td title="${esc(s.last_success_at || "")}">${ago(s.last_success_at)}</td>` +
    `<td title="${esc(s.last_record_fetched_at || "")}">${ago(s.last_record_fetched_at)}</td>` +
    `<td>${s.records}</td><td class="error">${esc(s.last_error || "")}</td><td>${actions.join(" ")}</td>`;
  return tr;
}

async function loadSources(note) {
  const meta = document.getElementById("sources-meta");
  const tbody = document.querySelector("#sources-table tbody");
  if (SAMPLE) { meta.textContent = "в режиме примера API недоступен"; return; }
  await fetchHealth();
  let list;
  try {
    const r = await fetch("/sources");
    if (!r.ok) { meta.textContent = "недоступно: " + await readError(r); return; }
    list = await r.json();
  } catch (e) { meta.textContent = "недоступно: " + e; return; }
  tbody.innerHTML = "";
  for (const s of list) tbody.appendChild(sourceRow(s));
  meta.textContent = `${list.length} источников, список на ${fmt(new Date().toISOString())}${note ? "; " + note : ""}`;
}

async function setSourceState(name, state) {
  const reason = document.getElementById("reason").value.trim() || "ручное переключение из панели";
  try {
    const r = await postJson(`/sources/${name}/state`, { state, reason });
    if (!r.ok) { await loadSources(`${name}: ошибка: ${await readError(r)}`); return; }
    await loadSources(`${name}: ${STATE_LABEL[state]}`);
  } catch (e) { await loadSources(`${name}: ${e}`); }
}

async function refreshSource(name) {
  const meta = document.getElementById("sources-meta");
  try {
    const created = await fetch(`/sources/${name}/refresh`, { method: "POST" });
    if (!created.ok) { await loadSources(`${name}: ошибка: ${await readError(created)}`); return; }
    const jobId = (await created.json()).job_id;
    const job = await waitJob(jobId, j => { meta.textContent = `${name}: ${progressText(j)}`; });
    if (job.status === "failed") { await loadSources(`${name}: задание ${jobId} не выполнено: ${job.error}`); return; }
    await loadSources(`${name}: обновлено, ${job.progress ? job.progress.step : "готово"}`);
  } catch (e) { await loadSources(`${name}: ${e}`); }
}

document.getElementById("query").addEventListener("submit", submit);
document.querySelector("#query [name=mode]").addEventListener("change", ev => {
  document.querySelector("#query [name=cutoff]").disabled = ev.target.value !== "replay";
});
document.body.addEventListener("click", ev => {
  const a = ev.target.closest("a[data-raw]");
  if (a) { ev.preventDefault(); openEvidence(a.dataset.raw); }
});
document.getElementById("close-evidence").addEventListener("click", () => { document.getElementById("evidence").hidden = true; });
document.getElementById("reload-sources").addEventListener("click", () => loadSources());
document.getElementById("sources-table").addEventListener("click", ev => {
  const button = ev.target.closest("button[data-source]");
  if (!button) return;
  if (button.dataset.refresh) refreshSource(button.dataset.source);
  else setSourceState(button.dataset.source, button.dataset.state);
});
if (SAMPLE) load(null);
loadSources();
