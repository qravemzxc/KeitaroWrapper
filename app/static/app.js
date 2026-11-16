"use strict";

const $ = (sel, root = document) => root.querySelector(sel);
const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
const state = { meta: null, campaignId: null, detail: null, interval: "today" };

async function api(method, url, body) {
  const res = await fetch(url, {
    method,
    headers: body ? { "Content-Type": "application/json" } : {},
    body: body ? JSON.stringify(body) : undefined,
  });
  const data = res.status === 204 ? null : await res.json().catch(() => null);
  if (!res.ok) {
    let msg = data?.detail ?? res.statusText;
    if (Array.isArray(msg)) msg = msg.map((e) => `${e.loc?.slice(-1)[0]}: ${e.msg}`).join("; ");
    throw new Error(msg);
  }
  return data;
}

function toast(msg, isError = false) {
  const el = document.createElement("div");
  el.className = "toast" + (isError ? " error" : "");
  el.textContent = msg;
  $("#toast").append(el);
  setTimeout(() => el.remove(), isError ? 7000 : 3500);
}

// ---------------------------------------------------------------- offer autocomplete
function offerAutocomplete(container, onPick) {
  const input = $("input", container);
  const list = document.createElement("div");
  list.className = "ac-list";
  list.hidden = true;
  container.append(list);
  let items = [], hl = -1, timer, seq = 0;
  container.selected = null;

  const render = () => {
    list.innerHTML = items.length
      ? items.map((o, i) => `<div class="ac-item ${i === hl ? "hl" : ""}" data-i="${i}">
          <span class="id">${o.id}</span>${esc(o.name)}
          <span class="meta">${esc(o.group || "")}${o.country?.length ? " · " + esc(o.country.join(", ")) : ""}</span></div>`).join("")
      : `<div class="ac-empty">Ничего не найдено</div>`;
    list.hidden = false;
  };
  const search = async () => {
    const my = ++seq;
    try {
      const res = await api("GET", `/api/offers?q=${encodeURIComponent(input.value)}&limit=30`);
      if (my !== seq) return;
      items = res; hl = -1; render();
    } catch (e) { toast(e.message, true); }
  };
  const pick = (o) => {
    container.selected = o;
    input.value = `[${o.id}] ${o.name}`;
    list.hidden = true;
    onPick?.(o);
  };
  input.addEventListener("input", () => { container.selected = null; clearTimeout(timer); timer = setTimeout(search, 200); });
  input.addEventListener("focus", () => { if (!container.selected) search(); });
  input.addEventListener("keydown", (e) => {
    if (list.hidden) return;
    if (e.key === "ArrowDown") { hl = Math.min(hl + 1, items.length - 1); render(); e.preventDefault(); }
    else if (e.key === "ArrowUp") { hl = Math.max(hl - 1, 0); render(); e.preventDefault(); }
    else if (e.key === "Enter" && hl >= 0) { pick(items[hl]); e.preventDefault(); }
    else if (e.key === "Escape") list.hidden = true;
  });
  list.addEventListener("mousedown", (e) => {
    const it = e.target.closest(".ac-item");
    if (it) { e.preventDefault(); pick(items[+it.dataset.i]); }
  });
  input.addEventListener("blur", () => setTimeout(() => (list.hidden = true), 150));
  container.reset = () => { container.selected = null; input.value = ""; };
  return container;
}

// ---------------------------------------------------------------- tabs
function showTab() {
  const [tab, id] = (location.hash.slice(1) || "create").split("/");
  document.querySelectorAll(".tab").forEach((t) => t.classList.toggle("active", t.id === "tab-" + tab));
  document.querySelectorAll("nav a").forEach((a) => a.classList.toggle("active", a.dataset.tab === tab));
  if (tab === "edit") {
    loadCampaigns();
    if (id && +id !== state.campaignId) openCampaign(+id);
  }
}
window.addEventListener("hashchange", showTab);

// ---------------------------------------------------------------- part 1: creator
async function initMeta() {
  try {
    state.meta = await api("GET", "/api/meta");
  } catch (e) {
    toast("Не удалось загрузить справочники Keitaro: " + e.message, true);
    return;
  }
  const m = state.meta;
  $("#prefix").textContent = m.prefix;
  document.querySelectorAll(".prefix-label").forEach((el) => (el.textContent = m.prefix + "*"));
  const fill = (name, items, def) => {
    $(`select[name=${name}]`).innerHTML = items.map((i) =>
      `<option value="${i.id}" ${i.id === def ? "selected" : ""}>${esc(i.name)} (#${i.id})</option>`).join("");
  };
  fill("domain_id", m.domains, m.defaults.domain_id);
  fill("group_id", m.groups, m.defaults.group_id);
  fill("traffic_source_id", m.traffic_sources, m.defaults.traffic_source_id);
}

const createAc = offerAutocomplete($("#create-offer-ac"));

$("#create-form").addEventListener("submit", async (e) => {
  e.preventDefault();
  const f = e.target;
  const out = $("#create-result");
  if (!createAc.selected) { toast("Выберите оффер из списка", true); return; }
  const body = {
    name: f.name.value,
    geo: f.geo.value,
    offer_id: createAc.selected.id,
    domain_id: +f.domain_id.value || null,
    group_id: +f.group_id.value || null,
    traffic_source_id: +f.traffic_source_id.value || null,
  };
  const btn = $("button[type=submit]", f);
  btn.disabled = true; btn.textContent = "Создаём…";
  try {
    const r = await api("POST", "/api/campaigns", body);
    out.innerHTML = `<div class="result"><b>Кампания создана</b>
      <dl><dt>ID</dt><dd>${r.id}</dd><dt>Название</dt><dd>${esc(r.name)}</dd>
      <dt>Ссылка</dt><dd><a href="${esc(r.url)}" target="_blank">${esc(r.url)}</a></dd>
      <dt>Группа</dt><dd>${esc(r.group)}</dd>
      <dt>Поток 1</dt><dd>${esc(r.geo.join(", "))} → Google</dd>
      <dt>Поток 2</dt><dd>[${r.offer.id}] ${esc(r.offer.name)} — 100%</dd></dl>
      <p><a href="#edit/${r.id}">Открыть в редакторе →</a></p></div>`;
    f.name.value = ""; f.geo.value = ""; createAc.reset();
  } catch (err) {
    out.innerHTML = `<div class="result error"><b>Ошибка:</b> ${esc(err.message)}</div>`;
  } finally {
    btn.disabled = false; btn.textContent = "Создать";
  }
});

// ---------------------------------------------------------------- part 2: editor
let listTimer;
async function loadCampaigns() {
  const q = $("#camp-search").value;
  const mine = $("#camp-mine").checked;
  try {
    const list = await api("GET", `/api/campaigns?mine=${mine}&q=${encodeURIComponent(q)}`);
    $("#camp-list").innerHTML = list.length
      ? list.map((c) => `<li data-id="${c.id}" class="${c.id === state.campaignId ? "active" : ""}">
          ${esc(c.name)}<div class="cid">#${c.id} · ${esc(c.group || "без группы")}${c.state !== "active" ? " · " + esc(c.state) : ""}</div></li>`).join("")
      : `<li class="muted">Нет кампаний${/^\d+$/.test(q.trim()) ? ` — <a href="#edit/${q.trim()}">открыть #${esc(q.trim())}</a>` : ""}</li>`;
  } catch (e) { toast(e.message, true); }
}
$("#camp-search").addEventListener("input", () => { clearTimeout(listTimer); listTimer = setTimeout(loadCampaigns, 250); });
$("#camp-mine").addEventListener("change", loadCampaigns);
$("#camp-list").addEventListener("click", (e) => {
  const li = e.target.closest("li[data-id]");
  if (li) location.hash = "edit/" + li.dataset.id;
});

async function openCampaign(id) {
  state.campaignId = id;
  document.querySelectorAll("#camp-list li").forEach((li) => li.classList.toggle("active", +li.dataset.id === id));
  const box = $("#detail");
  box.classList.add("loading");
  try {
    const d = await api("GET", `/api/campaigns/${id}?interval=${state.interval}`);
    renderDetail(d);
  } catch (e) {
    box.innerHTML = `<div class="empty">${esc(e.message)}</div>`;
  } finally { box.classList.remove("loading"); }
}

async function mutate(method, url, body, okMsg) {
  const box = $("#detail");
  box.classList.add("loading");
  try {
    const sep = url.includes("?") ? "&" : "?";
    const d = await api(method, `${url}${sep}interval=${state.interval}`, body);
    renderDetail(d);
    toast(okMsg);
  } catch (e) { toast(e.message, true); }
  finally { box.classList.remove("loading"); }
}

function filterText(f) {
  if (f.name === "country") return `Страны ${f.mode === "accept" ? "принимать" : "отклонять"}: ${(f.payload || []).join(", ")}`;
  return `${f.name} (${f.mode}): ${JSON.stringify(f.payload)}`;
}

function renderDetail(d) {
  state.detail = d;
  const intervals = state.meta?.intervals || { today: "Сегодня" };
  const box = $("#detail");
  box.innerHTML = `
    <div class="detail-head">
      <div style="flex:1;min-width:260px">
        <h2>${esc(d.name)} <span class="tag ${d.state === "active" ? "on" : ""}">${esc(d.state)}</span></h2>
        <div class="muted">#${d.id} · группа ${esc(d.group || "—")}</div>
        <div class="url-row"><input readonly value="${esc(d.url || "домен не задан")}">
          <button class="btn small" id="copy-url">Копировать</button></div>
      </div>
      <div class="toolbar">
        <select id="interval">${Object.entries(intervals).map(([k, v]) => `<option value="${k}" ${k === state.interval ? "selected" : ""}>${v}</option>`).join("")}</select>
        <button class="btn small" id="refresh" title="Обновить">⟳</button>
      </div>
    </div>
    ${d.stats_error ? `<div class="warn-box">Статистика недоступна: ${esc(d.stats_error)}</div>` : ""}
    ${d.streams.map((s) => renderStream(s, d)).join("") || `<div class="empty">В кампании нет потоков</div>`}
    <details class="history" id="history"><summary>История изменений через сервис</summary><ul></ul></details>`;

  $("#copy-url").onclick = () => navigator.clipboard?.writeText(d.url || "").then(() => toast("Ссылка скопирована"));
  $("#interval").onchange = (e) => { state.interval = e.target.value; openCampaign(d.id); };
  $("#refresh").onclick = () => openCampaign(d.id);
  $("#history").addEventListener("toggle", loadHistory);

  box.querySelectorAll("[data-remove]").forEach((b) => b.onclick = () => {
    const [sid, oid] = b.dataset.remove.split(":").map(Number);
    const offer = d.streams.find((s) => s.id === sid).offers.find((o) => o.offer_id === oid);
    if (!confirm(`Удалить оффер [${oid}] ${offer.name}?\nДоли оставшихся офферов будут пересчитаны поровну.`)) return;
    mutate("DELETE", `/api/campaigns/${d.id}/offers/${oid}?stream_id=${sid}`, null, `Оффер ${oid} удалён`);
  });

  box.querySelectorAll(".stream[data-offers]").forEach((el) => {
    const sid = +el.dataset.id;
    const ac = offerAutocomplete($(".ac", el));
    $(".add-btn", el).onclick = () => {
      if (!ac.selected) { toast("Выберите оффер из списка", true); return; }
      mutate("POST", `/api/campaigns/${d.id}/offers`, { offer_id: ac.selected.id, stream_id: sid }, `Оффер ${ac.selected.id} добавлен`);
    };
    const inputs = [...el.querySelectorAll(".share-input")];
    const updateSum = () => {
      const sum = inputs.reduce((a, i) => a + (parseInt(i.value, 10) || 0), 0);
      const dirty = inputs.some((i) => +i.value !== +i.dataset.orig);
      inputs.forEach((i) => i.classList.toggle("dirty", +i.value !== +i.dataset.orig));
      const sumEl = $(".sum", el);
      sumEl.textContent = sum + "%";
      sumEl.classList.toggle("sum-bad", sum !== 100);
      $(".share-actions", el).classList.toggle("show", dirty);
      $(".save-shares", el).disabled = sum !== 100;
    };
    inputs.forEach((i) => i.addEventListener("input", updateSum));
    $(".reset-shares", el).onclick = () => { inputs.forEach((i) => (i.value = i.dataset.orig)); updateSum(); };
    $(".even-shares", el).onclick = () => {
      const n = inputs.length, base = Math.floor(100 / n), rest = 100 % n;
      inputs.forEach((i, k) => (i.value = base + (k < rest ? 1 : 0)));
      updateSum();
    };
    $(".save-shares", el).onclick = () => mutate("PUT", `/api/campaigns/${d.id}/shares`, {
      stream_id: sid, offers: inputs.map((i) => ({ offer_id: +i.dataset.offer, share: +i.value })),
    }, "Доли сохранены");
  });
}

function renderStream(s, d) {
  const isOfferStream = s.schema === "landings" || s.offers.length > 0;
  const head = `<div class="stream-head"><span class="dot"></span><h3>${esc(s.name)} <span class="tag">поток ${s.position}</span></h3>
      <div class="stat"><b>${s.clicks}</b>клики</div><div class="stat"><b>${s.unique}</b>уник.</div><div class="stat"><b>${s.bots}</b>боты</div></div>`;
  const filters = s.filters.length
    ? `<div class="muted" style="margin-top:6px">Фильтры</div>${s.filters.map((f) => `<div class="filter">${esc(filterText(f))}</div>`).join("")}` : "";
  if (!isOfferStream) {
    return `<div class="stream" data-id="${s.id}">${head}<div class="stream-body">
      <div>${s.action_payload ? `→ <a href="${esc(s.action_payload)}" target="_blank">${esc(s.action_payload)}</a>` : esc(s.schema)}</div>${filters}</div></div>`;
  }
  const sum = s.offers.reduce((a, o) => a + o.share, 0);
  const rows = s.offers.map((o) => `<tr>
      <td class="offer-name"><span class="id">[${o.offer_id}]</span>${esc(o.name)}${o.state !== "active" ? ` <span class="tag">${esc(o.state)}</span>` : ""}</td>
      <td class="share"><input class="share-input" type="number" min="0" max="100" value="${o.share}" data-orig="${o.share}" data-offer="${o.offer_id}"> %
        <div class="bar"><i style="width:${o.share}%"></i></div></td>
      <td class="num">${o.clicks}</td><td class="num">${o.unique}</td><td class="num">${o.conversions}</td><td class="num">${o.revenue.toFixed(2)}</td>
      <td class="num"><button class="icon-btn" title="Удалить оффер" data-remove="${s.id}:${o.offer_id}" ${s.offers.length === 1 ? "disabled" : ""}>×</button></td>
    </tr>`).join("");
  return `<div class="stream ${s.id === d.offer_stream_id ? "target" : ""}" data-id="${s.id}" data-offers>${head}<div class="stream-body">
    ${filters}
    <table><thead><tr><th>Оффер</th><th>Доля</th><th class="num">Клики</th><th class="num">Уник.</th><th class="num">Конв.</th><th class="num">Доход</th><th></th></tr></thead>
      <tbody>${rows || `<tr><td colspan="7" class="muted">Офферов нет</td></tr>`}
      <tr class="sum-row"><td>Итого</td><td><span class="sum ${sum !== 100 && s.offers.length ? "sum-bad" : ""}">${sum}%</span></td><td colspan="5"></td></tr></tbody></table>
    <div class="share-actions"><span class="muted">Доли изменены вручную</span>
      <button class="btn small even-shares">Поровну</button><button class="btn small reset-shares">Отменить</button>
      <button class="btn small primary save-shares">Сохранить доли</button></div>
    <div class="add-row"><div class="ac"><input placeholder="Добавить оффер: ID или название"></div>
      <button class="btn primary add-btn">Добавить</button></div>
  </div></div>`;
}

async function loadHistory(e) {
  if (!e.target.open) return;
  const ul = $("ul", e.target);
  try {
    const rows = await api("GET", `/api/campaigns/${state.campaignId}/history`);
    const label = { create_campaign: "создание", add_offer: "добавлен оффер", remove_offer: "удалён оффер", set_shares: "изменены доли" };
    ul.innerHTML = rows.length ? rows.map((r) => `<li>${esc(r.created_at.replace("T", " "))} — ${label[r.action] || r.action}
      ${r.payload.offer_id ? "#" + r.payload.offer_id : ""} → ${esc((r.payload.result || []).map((o) => `${o.offer_id}:${o.share}%`).join(", "))}</li>`).join("")
      : `<li>Изменений через сервис ещё не было</li>`;
  } catch (err) { ul.innerHTML = `<li>${esc(err.message)}</li>`; }
}

initMeta();
showTab();
