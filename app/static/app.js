"use strict";

const $ = (sel, root = document) => root.querySelector(sel);
const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
const state = { meta: null, campaignId: null, detail: null, interval: "today", drafts: {}, syncedAt: null };
// drafts: { [streamId]: [op, ...] } - editor actions not pushed to Keitaro yet, in the order they were made:
//   {type: "add", id, name}  - new offer (or a removed one being returned);
//   {type: "remove", id}     - existing offer marked for removal

// Even split, remainder to the first offers: 3 -> 34/33/33 (same as the server and Keitaro).
const evenShares = (n, total = 100) => Array.from({ length: n }, (_, i) => Math.floor(total / n) + (i < total % n ? 1 : 0));
// What the stream will look like after the push: final offer ids and their (even) shares.
// Pinned offers keep their share, the rest of 100% is split evenly (same rules as app/shares.py).
// Returns {share: {id: %}, error}; on error the free offers get 0 and the push is blocked.
function sharesWithPins(ids, pinned) {
  const pins = Object.fromEntries(Object.entries(pinned).filter(([id]) => ids.includes(+id)).map(([id, v]) => [+id, v]));
  const free = ids.filter((id) => !(id in pins));
  const rest = 100 - Object.values(pins).reduce((a, v) => a + v, 0);
  let error = null;
  if (!free.length && rest !== 0) error = `Все офферы закреплены, их доли в сумме ${100 - rest}%, а нужно 100%. Открепите один из офферов.`;
  else if (free.length && rest < free.length) error = `Закреплённые офферы занимают ${100 - rest}%: остальным офферам не хватает доли. Открепите один из офферов.`;
  const even = error ? free.map(() => 0) : evenShares(free.length, rest);
  const share = { ...pins };
  free.forEach((id, i) => (share[id] = even[i]));
  return { share, error };
}

// "Статус": state of the offer in the Keitaro stream + its state in the draft.
function statusCell(kind, o) {
  const badges = {
    new: `<span class="tag new">новый</span>`,
    removing: `<span class="tag del">будет удалён</span>`,
    removed: `<span class="tag del">удалён</span>`,
  };
  const live = o.state === "active" ? `<span class="tag on">активен</span>` : `<span class="tag">выключен</span>`;
  const main = badges[kind] || live;
  const pin = o.pinned && !kind ? ` <span class="tag pin">закреплён</span>` : "";
  return `<td class="status">${main}${pin}</td>`;
}

// "Трендс": clicks in the selected period vs the previous period of the same length.
const TREND_LABEL = { today: "вчера к этому же времени", yesterday: "позавчера", "7_days_ago": "предыдущие 7 дней", "1_month_ago": "предыдущие 30 дней" };
function trendCell(t) {
  if (!t) return `<td class="num trend muted" title="Для периода «Всё время» тренда нет">—</td>`;
  const title = `Клики: ${t.current} сейчас, ${t.previous} — ${TREND_LABEL[state.interval] || "предыдущий период"}`;
  if (!t.current && !t.previous) return `<td class="num trend muted" title="${title}">—</td>`;
  if (t.change_pct === null) return `<td class="num trend up" title="${title}">↑ новый трафик</td>`;
  if (t.change_pct === 0) return `<td class="num trend muted" title="${title}">→ 0%</td>`;
  const up = t.change_pct > 0;
  return `<td class="num trend ${up ? "up" : "down"}" title="${title}">${up ? "↑" : "↓"} ${Math.abs(t.change_pct)}%</td>`;
}

function draftView(s) {
  const ops = state.drafts[s.id] || [];
  const removed = new Set(ops.filter((o) => o.type === "remove").map((o) => o.id));
  const added = ops.filter((o) => o.type === "add");
  const finalIds = [...s.offers.filter((o) => !removed.has(o.offer_id)).map((o) => o.offer_id), ...added.map((o) => o.id)];
  const pinned = Object.fromEntries(s.offers.filter((o) => o.pinned && !removed.has(o.offer_id)).map((o) => [o.offer_id, o.share]));
  const { share, error } = sharesWithPins(finalIds, pinned);
  return { ops, removed, added, finalIds, share, error, pending: ops.length > 0 };
}
// Pin icon for pinning an offer's share.
const PIN_ICON = `<svg class="ico" viewBox="0 0 24 24" width="16" height="16" fill="none" stroke="currentColor" stroke-width="2"
  stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M12 17v5"/>
  <path d="M9 10.76a2 2 0 0 1-1.11 1.79l-1.78.9A2 2 0 0 0 5 15.24V17h14v-1.76a2 2 0 0 0-1.11-1.79l-1.78-.9A2 2 0 0 1 15 10.76V6h1a2 2 0 0 0 0-4H8a2 2 0 0 0 0 4h1z"/></svg>`;
// Trash-can icon for deleting an offer (inline SVG: looks the same in every browser, follows the text color).
const TRASH_ICON = `<svg class="ico" viewBox="0 0 24 24" width="16" height="16" fill="none" stroke="currentColor" stroke-width="2"
  stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M3 6h18"/><path d="M8 6V4a1 1 0 0 1 1-1h6a1 1 0 0 1 1 1v2"/>
  <path d="M19 6l-1 14a2 2 0 0 1-2 2H8a2 2 0 0 1-2-2L5 6"/><path d="M10 11v6"/><path d="M14 11v6"/></svg>`;
const draftCount = () => Object.values(state.drafts).reduce((a, l) => a + l.length, 0);
window.addEventListener("beforeunload", (e) => { if (draftCount()) { e.preventDefault(); e.returnValue = ""; } });

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
  setTimeout(() => el.remove(), Math.max(isError ? 7000 : 3500, msg.length * 60));  // long messages stay longer
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

// Browser validation messages ("Please fill out this field") follow the browser language: show them in Russian.
// The message must be set before the browser validates the form, i.e. on the submit button click
// (Enter in a field also "clicks" it). Typing clears it so the field is not stuck as invalid.
function setRequiredMessages(form) {
  form.querySelectorAll("input[required]").forEach((i) => i.setCustomValidity(i.value.trim() ? "" : "Заполните это поле"));
}
document.querySelectorAll("form").forEach((form) => {
  form.querySelectorAll("[type=submit]").forEach((b) => b.addEventListener("click", () => setRequiredMessages(form)));
  form.querySelectorAll("input[required]").forEach((i) => i.addEventListener("input", () => i.setCustomValidity("")));
});

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
  if (id !== state.campaignId && draftCount()) {
    if (!confirm("Есть офферы, которые не запушены в Keitaro. Уйти и потерять их?")) {
      history.replaceState(null, "", `#edit/${state.campaignId}`);
      return;
    }
  }
  if (id !== state.campaignId) state.drafts = {};
  state.campaignId = id;
  document.querySelectorAll("#camp-list li").forEach((li) => li.classList.toggle("active", +li.dataset.id === id));
  const box = $("#detail");
  box.classList.add("loading");
  try {
    const d = await api("GET", `/api/campaigns/${id}?interval=${state.interval}`);
    if (state.campaignId !== id) return null;  // the user already opened another campaign
    renderDetail(d);
    return d;
  } catch (e) {
    box.innerHTML = `<div class="empty">${esc(e.message)}</div>`;
    return null;
  } finally { box.classList.remove("loading"); }
}

async function mutate(method, url, body, okMsg, onSuccess) {
  const box = $("#detail");
  box.classList.add("loading");
  try {
    const sep = url.includes("?") ? "&" : "?";
    const d = await api(method, `${url}${sep}interval=${state.interval}`, body);
    onSuccess?.();
    renderDetail(d);
    toast(okMsg);
  } catch (e) { toast(e.message, true); }
  finally { box.classList.remove("loading"); }
}

// ---------------------------------------------------------------- refresh from Keitaro
// "⟳ Обновить офферы" re-reads the campaign from Keitaro. Offers deleted directly in Keitaro
// come back from the server as removed (dimmed, 0%, "Вернуть") and are simply shown in the table.
function updateSyncLabel() {
  document.querySelectorAll(".sync-status").forEach((el) => {
    el.textContent = state.syncedAt ? `Данные из Keitaro на ${state.syncedAt.toLocaleTimeString()}` : "";
  });
}

async function refreshOffers() {
  // just re-read the campaign: whatever changed in Keitaro is shown in the table
  if (state.detail) await openCampaign(state.detail.id);
}

function filterText(f) {
  if (f.name === "country") return `Страны ${f.mode === "accept" ? "принимать" : "отклонять"}: ${(f.payload || []).join(", ")}`;
  return `${f.name} (${f.mode}): ${JSON.stringify(f.payload)}`;
}

// Drop draft actions that no longer make sense with fresh data from Keitaro:
// "remove" of an offer that is already gone, "add" of an offer that is already in the stream.
function pruneDrafts(d) {
  const dropped = [];
  for (const [sid, ops] of Object.entries(state.drafts)) {
    const s = d.streams.find((x) => x.id === +sid);
    const present = new Set((s?.offers || []).map((o) => o.offer_id));
    const keep = s ? ops.filter((op) => (op.type === "remove" ? present.has(op.id) : !present.has(op.id))) : [];
    ops.filter((op) => !keep.includes(op)).forEach((op) => dropped.push(op.id));
    if (keep.length) state.drafts[sid] = keep; else delete state.drafts[sid];
  }
  return dropped;
}

function renderDetail(d) {
  const dropped = pruneDrafts(d);
  if (dropped.length) toast(`Черновик скорректирован по данным Keitaro: офферы ${dropped.map((i) => "#" + i).join(", ")}`);
  state.detail = d;
  state.syncedAt = new Date();
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
  updateSyncLabel();

  box.querySelectorAll(".stream[data-offers]").forEach((el) => {
    const sid = +el.dataset.id;
    const ac = offerAutocomplete($(".ac", el));
    const stream = d.streams.find((x) => x.id === sid);
    const view = draftView(stream);
    const setOps = (ops) => {
      if (ops.length) state.drafts[sid] = ops; else delete state.drafts[sid];
      renderDetail(d);
    };

    // Nothing below talks to Keitaro: every action only changes the draft until "Запушить".
    $(".add-btn", el).onclick = () => {
      const o = ac.selected;
      if (!o) { toast("Выберите оффер из списка", true); return; }
      if (view.removed.has(o.id)) { setOps(view.ops.filter((op) => !(op.type === "remove" && op.id === o.id))); return; }
      if (view.finalIds.includes(o.id)) { toast(`Оффер ${o.id} уже есть в потоке`, true); return; }
      setOps([...view.ops, { type: "add", id: o.id, name: o.name }]);
    };
    el.querySelectorAll("[data-remove]").forEach((b) => b.onclick = () => {
      const id = +b.dataset.remove;
      if (view.finalIds.length <= 1) { toast("Нельзя убрать последний оффер: поток останется пустым", true); return; }
      const isNew = view.added.some((op) => op.id === id);
      // a new (not pushed) offer simply leaves the draft; an existing one is marked for removal
      setOps(isNew ? view.ops.filter((op) => !(op.type === "add" && op.id === id)) : [...view.ops, { type: "remove", id }]);
    });
    // pin / unpin: saved right away in the service DB, nothing is sent to Keitaro
    el.querySelectorAll("[data-pin]").forEach((b) => b.onclick = () => {
      const o = stream.offers.find((x) => x.offer_id === +b.dataset.pin);
      mutate(o.pinned ? "DELETE" : "PUT", `/api/campaigns/${d.id}/offers/${o.offer_id}/pin?stream_id=${sid}`, null,
        o.pinned ? `Доля оффера ${o.offer_id} откреплена, доли пересчитаны` : `Доля оффера ${o.offer_id} закреплена: ${o.share}%`);
    });
    el.querySelectorAll("[data-restore]").forEach((b) => b.onclick = () => {
      const id = +b.dataset.restore;
      setOps(view.ops.filter((op) => !(op.type === "remove" && op.id === id)));
    });
    el.querySelectorAll("[data-return]").forEach((b) => b.onclick = () => {
      const o = stream.removed_offers.find((x) => x.offer_id === +b.dataset.return);
      setOps([...view.ops, { type: "add", id: o.offer_id, name: o.name }]);
    });
    $(".refresh-offers-btn", el).onclick = () => refreshOffers();
    $(".push-btn", el)?.addEventListener("click", () => {
      const parts = [];
      if (view.added.length) parts.push(`добавлено ${view.added.length}`);
      if (view.removed.size) parts.push(`удалено ${view.removed.size}`);
      mutate("PUT", `/api/campaigns/${d.id}/offers`, { offer_ids: view.finalIds, stream_id: sid },
        `Запушено в Keitaro: ${view.finalIds.length} офферов (${parts.join(", ")})`,
        () => delete state.drafts[sid]);
    });
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
      // pinned offers keep the value typed in their field, the others share the rest evenly
      const pinned = Object.fromEntries(inputs.filter((i) => "pinned" in i.dataset).map((i) => [+i.dataset.offer, parseInt(i.value, 10) || 0]));
      const { share, error } = sharesWithPins(inputs.map((i) => +i.dataset.offer), pinned);
      if (error) { toast(error, true); return; }
      inputs.forEach((i) => (i.value = share[+i.dataset.offer]));
      updateSum();
    };
    $(".save-shares", el).onclick = () => mutate("PUT", `/api/campaigns/${d.id}/shares`, {
      stream_id: sid, offers: inputs.map((i) => ({ offer_id: +i.dataset.offer, share: +i.value })),
    }, "Доли сохранены");
  });
}

function renderStream(s, d) {
  const isOfferStream = s.schema === "landings" || s.offers.length > 0;
  const head = `<div class="stream-head"><span class="dot"></span><h3>${esc(s.name)} <span class="tag">поток ${s.position}</span></h3></div>`;
  const filters = s.filters.length
    ? `<div class="muted" style="margin-top:6px">Фильтры</div>${s.filters.map((f) => `<div class="filter">${esc(filterText(f))}</div>`).join("")}` : "";
  if (!isOfferStream) {
    return `<div class="stream" data-id="${s.id}">${head}<div class="stream-body">
      <div>${s.action_payload ? `→ <a href="${esc(s.action_payload)}" target="_blank">${esc(s.action_payload)}</a>` : esc(s.schema)}</div>${filters}</div></div>`;
  }
  const v = draftView(s);
  const pending = v.pending;
  const sum = pending ? 100 : s.offers.reduce((a, o) => a + o.share, 0);
  const rows = s.offers.map((o) => {
    const removed = v.removed.has(o.offer_id);
    const share = removed ? 0 : pending ? v.share[o.offer_id] : o.share;
    const shareCell = !pending
      ? `<input class="share-input" type="number" min="0" max="100" value="${o.share}" data-orig="${o.share}" data-offer="${o.offer_id}" ${o.pinned ? "data-pinned" : ""}> %`
      : `<span class="share-change">${o.share}% → <b>${share}%</b></span>`;
    const pinTitle = o.pinned ? "Открепить долю" : "Закрепить долю";
    const action = removed
      ? `<button class="btn small restore-btn" data-restore="${o.offer_id}" title="Оставить оффер в потоке">↩ Вернуть</button>`
      : `<span class="row-actions"><button class="icon-btn pin-btn ${o.pinned ? "on" : ""}" title="${pinTitle}" aria-label="${pinTitle}"
          aria-pressed="${o.pinned}" data-pin="${o.offer_id}">${PIN_ICON}</button><button class="icon-btn" title="Удалить оффер"
          aria-label="Удалить оффер" data-remove="${o.offer_id}">${TRASH_ICON}</button></span>`;
    return `<tr class="${removed ? "removed-row" : ""} ${o.pinned && !removed ? "pinned-row" : ""}">
      <td class="offer-name"><span class="id">[${o.offer_id}]</span>${esc(o.name)}</td>
      <td class="share">${shareCell}<div class="bar"><i style="width:${share}%"></i></div></td>
      <td class="num">${o.clicks}</td><td class="num">${o.unique}</td><td class="num">${o.bots}</td>
      ${statusCell(removed ? "removing" : null, o)}${trendCell(o.trend)}
      <td class="num">${action}</td>
    </tr>`;
  }).join("") + v.added.map((o) => {
    const share = v.share[o.id];
    return `<tr class="draft-row">
      <td class="offer-name"><span class="id">[${o.id}]</span>${esc(o.name)}</td>
      <td class="share"><span class="share-change"><b>${share}%</b></span><div class="bar"><i style="width:${share}%"></i></div></td>
      <td class="num muted" colspan="3">статистики пока нет</td>
      ${statusCell("new", {})}<td class="num trend muted">—</td>
      <td class="num"><button class="icon-btn" title="Отменить" data-remove="${o.id}">×</button></td>
    </tr>`;
  }).join("");
  const returning = new Set(v.added.map((o) => o.id));
  const removedRows = (s.removed_offers || []).filter((o) => !returning.has(o.offer_id)).map((o) => `<tr class="removed-row">
      <td class="offer-name"><span class="id">[${o.offer_id}]</span>${esc(o.name)}</td>
      <td class="share"><span class="share-change"><b>0%</b></span><div class="bar"><i style="width:0%"></i></div></td>
      <td class="num">${o.clicks}</td><td class="num">${o.unique}</td><td class="num">${o.bots}</td>
      ${statusCell("removed", o)}${trendCell(o.trend)}
      <td class="num"><button class="btn small restore-btn" data-return="${o.offer_id}" title="Вернуть оффер в поток">↩ Вернуть</button></td>
    </tr>`).join("");
  const pendingBar = pending ? `<div class="pending-bar">
      ${v.error ? `<span class="pin-error">${esc(v.error)}</span>` : ""}
      <button class="btn small primary push-btn" title="${v.error ? esc(v.error) : "Отправить в Keitaro итоговый список офферов"}" ${v.error ? "disabled" : ""}>⇪ Запушить в Keitaro</button></div>` : "";
  return `<div class="stream ${s.id === d.offer_stream_id ? "target" : ""} ${pending ? "pending" : ""}" data-id="${s.id}" data-offers>${head}<div class="stream-body">
    ${filters}
    <div class="offers-toolbar"><span class="muted sync-status"></span>
      <button class="btn small refresh-offers-btn" title="Перечитать офферы и статистику из Keitaro (черновик сохранится)">⟳ Обновить офферы</button></div>
    <table><thead><tr><th>Оффер</th><th>Доля</th><th class="num">Клики</th><th class="num">Уник.</th><th class="num">Боты</th><th>Статус</th><th class="num" title="Клики за выбранный период по сравнению с предыдущим таким же периодом">Трендс</th><th class="num">Действия</th></tr></thead>
      <tbody>${rows || `<tr><td colspan="8" class="muted">Офферов нет</td></tr>`}${removedRows}
      <tr class="sum-row"><td>Итого</td><td><span class="sum ${sum !== 100 && s.offers.length ? "sum-bad" : ""}">${sum}%</span></td><td colspan="6"></td></tr></tbody></table>
    <div class="share-actions"><span class="muted">Доли изменены вручную</span>
      <button class="btn small even-shares">Поровну</button><button class="btn small reset-shares">Отменить</button>
      <button class="btn small primary save-shares">Сохранить доли</button></div>
    ${pendingBar}
    <div class="add-row"><div class="ac"><input placeholder="Добавить оффер: ID или название"></div>
      <button class="btn primary add-btn">Добавить</button></div>
  </div></div>`;
}

async function loadHistory(e) {
  if (!e.target.open) return;
  const ul = $("ul", e.target);
  try {
    const rows = await api("GET", `/api/campaigns/${state.campaignId}/history`);
    const label = { create_campaign: "создание", add_offer: "добавлен оффер", remove_offer: "удалён оффер", set_shares: "изменены доли", change_offers: "изменены офферы",
      pin_offer: "закреплена доля оффера", unpin_offer: "откреплена доля оффера" };
    ul.innerHTML = rows.length ? rows.map((r) => /pin_offer$/.test(r.action)
      ? `<li>${esc(r.created_at.replace("T", " "))} — ${label[r.action]} #${r.payload.offer_id} (${r.payload.share}%)</li>`
      : `<li>${esc(r.created_at.replace("T", " "))} — ${label[r.action] || r.action}
      ${[...(r.payload.offer_ids || (r.payload.offer_id && r.action === "add_offer" ? [r.payload.offer_id] : [])).map((i) => "+#" + i),
         ...(r.payload.removed_offer_ids || (r.payload.offer_id && r.action === "remove_offer" ? [r.payload.offer_id] : [])).map((i) => "−#" + i)].join(", ")} → ${esc((r.payload.result || []).map((o) => `${o.offer_id}:${o.share}%`).join(", "))}</li>`).join("")
      : `<li>Изменений через сервис ещё не было</li>`;
  } catch (err) { ul.innerHTML = `<li>${esc(err.message)}</li>`; }
}

initMeta();
showTab();
