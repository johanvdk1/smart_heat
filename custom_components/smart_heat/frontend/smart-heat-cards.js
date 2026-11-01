// Smart Heat cards (served by the integration; no build step).
//   type: custom:smart-heat-thermostat-card   zone: woonkamer     target temperature by time, control, plan
//   type: custom:smart-heat-schedule-card     zone: woonkamer     the zone's week schedule
const DAYS = ["mon", "tue", "wed", "thu", "fri", "sat", "sun"];
const DAY_NAMES = { mon: "Mon", tue: "Tue", wed: "Wed", thu: "Thu", fri: "Fri", sat: "Sat", sun: "Sun" };
const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
const hm = (iso) => (iso ? new Date(iso).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" }) : "");
const dayOf = (iso) => {
  if (!iso) return "";
  const d = new Date(iso), t = new Date();
  const k = (x) => x.toDateString();
  if (k(d) === k(t)) return "today";
  t.setDate(t.getDate() + 1);
  return k(d) === k(t) ? "tomorrow" : d.toLocaleDateString([], { weekday: "short", day: "numeric", month: "short" });
};
const fmt = (v, n = 1) => (v === null || v === undefined || v === "" || isNaN(v) ? "–" : Number(v).toFixed(n));

const STYLE = `
  :host { display: block; }
  ha-card { padding: 16px; }
  .title { font-size: 1.2em; font-weight: 500; margin-bottom: 8px; display: flex; justify-content: space-between; align-items: center; }
  .muted { color: var(--secondary-text-color); font-size: 0.9em; }
  .err { color: var(--error-color); font-size: 0.9em; margin-top: 6px; }
  .row { display: flex; align-items: center; gap: 8px; margin: 8px 0; flex-wrap: wrap; }
  button { font: inherit; border: 1px solid var(--divider-color); background: var(--card-background-color); color: var(--primary-text-color);
           border-radius: 6px; padding: 4px 10px; cursor: pointer; }
  button.primary { background: var(--primary-color); color: var(--text-primary-color, #fff); border-color: var(--primary-color); }
  input { font: inherit; padding: 3px 6px; border: 1px solid var(--divider-color); border-radius: 6px;
          background: var(--card-background-color); color: var(--primary-text-color); }
`;

class SmartHeatBase extends HTMLElement {
  setConfig(config) {
    if (!config.zone) throw new Error("zone is required");
    this._config = config;
    this._info = null;
  }
  set hass(hass) {
    this._hass = hass;
    if (!this._unsub && !this._subscribing) this._subscribe();
    const key = this._key();                 // re-render only when something this card shows has changed
    if (key !== this._lastKey) { this._lastKey = key; this._render(); }
  }
  _key() { return ""; }
  async _subscribe() {
    this._subscribing = true;
    try {
      this._unsub = await this._hass.connection.subscribeMessage((m) => {
        this._info = (m.zones || []).find((z) => z.zone === this._config.zone) || { missing: true };
        this._render();
      }, { type: "smart_heat/subscribe" });
    } catch (e) {
      this._error = e.message || String(e);
      this._render();
    }
    this._subscribing = false;
  }
  disconnectedCallback() {
    if (this._unsub) { this._unsub(); this._unsub = null; }
  }
  connectedCallback() {
    if (this._hass && !this._unsub && !this._subscribing) this._subscribe();
  }
  async _ws(msg) {
    this._error = "";
    try {
      await this._hass.callWS(msg);
    } catch (e) {
      this._error = e.message || String(e);
    }
    this._render();
  }
  _root() {
    if (!this.shadowRoot) this.attachShadow({ mode: "open" });
    return this.shadowRoot;
  }
  _name() {
    return this._config.title || this._config.zone.replace(/_/g, " / ");
  }
  getCardSize() { return 4; }
}

class SmartHeatThermostatCard extends SmartHeatBase {
  static getStubConfig() { return { zone: "" }; }
  _key() {
    const i = this._info, h = this._hass;
    if (!i || i.missing || !h) return "";
    const ref = i.rooms.find((r) => r.room === i.reference_room) || i.rooms[0];
    const r = h.states[ref.temperature], c = h.states[ref.climate];
    return `${r && r.state}|${c && c.attributes.temperature}`;
  }
  _render() {
    const root = this._root(), i = this._info, h = this._hass;
    if (!h) return;
    if (!i || i.missing) {
      root.innerHTML = `<style>${STYLE}</style><ha-card><div class="title">${esc(this._name())}</div>
        <div class="muted">${i && i.missing ? "Unknown zone." : "Loading…"}</div>${this._error ? `<div class="err">${esc(this._error)}</div>` : ""}</ha-card>`;
      return;
    }
    const ref = i.rooms.find((r) => r.room === i.reference_room) || i.rooms[0];
    const room = h.states[ref.temperature], clim = h.states[ref.climate];
    const p = i.plan || {}, q = i.params || {}, req = i.request;
    if (this._temp === undefined) this._temp = req ? req.temperature : (p.target ?? (clim ? clim.attributes.temperature : 20));
    if (this._time === undefined) this._time = req ? hm(req.ready_at) : "";
    let plan;
    if (p.preheat) plan = `Heating to ${fmt(p.target)} °C, ready ${dayOf(p.ready_at)} ${hm(p.ready_at)} (started ${hm(p.start_at)})`;
    else if (p.ready_at) plan = `${fmt(p.target)} °C ${dayOf(p.ready_at)} at ${hm(p.ready_at)} (${esc(p.source)}): start ${hm(p.start_at)}, ${fmt(p.minutes, 0)} min${p.capped ? ", more than 4 h needed" : ""}`;
    else plan = esc(p.reason || "No rise in the next 24 h");
    root.innerHTML = `<style>${STYLE}
        .big { font-size: 2.6em; font-weight: 300; line-height: 1; }
        .sp { font-size: 1.1em; }
        .stepper button { width: 34px; }
        .val { min-width: 4.2em; text-align: center; font-size: 1.2em; }
        .plan { margin: 10px 0; }
      </style>
      <ha-card>
        <div class="title">${esc(this._name())}
          <label class="muted">Control <input type="checkbox" id="ctl" ${i.control ? "checked" : ""}></label></div>
        <div class="row"><span class="big">${room ? fmt(room.state) : "–"} °C</span>
          <span class="sp muted">set ${clim ? fmt(clim.attributes.temperature) : "–"} °C</span></div>
        <div class="plan">${plan}${p.reason && p.ready_at ? `<div class="muted">${esc(p.reason)}</div>` : ""}
          ${i.control ? "" : `<div class="muted">Shadow: control is off, nothing is switched.</div>`}</div>
        <div class="row stepper">
          <button id="dn">−</button><span class="val">${fmt(this._temp)} °C</span><button id="up">+</button>
          <span>by</span><input type="time" id="time" value="${esc(this._time)}">
          <button class="primary" id="set">Set</button></div>
        ${req ? `<div class="row muted">Request: ${fmt(req.temperature)} °C ${dayOf(req.ready_at)} at ${hm(req.ready_at)} <button id="cancel">Cancel</button></div>` : ""}
        <div class="muted">Learned from ${q.n ?? 0} warm-ups: dead time ${fmt(q.dead_time, 0)} min · ${fmt(q.per_k)} min per K · ${fmt(q.per_k_out, 2)} min per K outside</div>
        ${this._error ? `<div class="err">${esc(this._error)}</div>` : ""}
      </ha-card>`;
    const $ = (id) => root.getElementById(id);
    $("ctl").addEventListener("change", (e) => this._ws({ type: "smart_heat/control/set", zone: i.zone, on: e.target.checked }));
    $("dn").addEventListener("click", () => { this._temp = Math.max(5, this._temp - 0.5); this._render(); });
    $("up").addEventListener("click", () => { this._temp = Math.min(23, this._temp + 0.5); this._render(); });
    $("time").addEventListener("input", (e) => { this._time = e.target.value; });
    $("set").addEventListener("click", () => {
      if (!this._time) { this._error = "Choose a time."; this._render(); return; }
      this._ws({ type: "smart_heat/request/set", zone: i.zone, temperature: this._temp, ready_at: this._time });
    });
    if (req) $("cancel").addEventListener("click", () => { this._time = ""; this._ws({ type: "smart_heat/request/clear", zone: i.zone }); });
  }
}

class SmartHeatScheduleCard extends SmartHeatBase {
  static getStubConfig() { return { zone: "" }; }
  getCardSize() { return 7; }
  _draftFrom(info) {
    const s = info.schedule || { base: 16, days: {} };
    return { base: s.base, days: Object.fromEntries(DAYS.map((d) => [d, (s.days[d] || []).map((p) => ({ ...p }))])) };
  }
  _render() {
    const root = this._root(), i = this._info;
    if (!this._hass) return;
    if (!i || i.missing) {
      root.innerHTML = `<style>${STYLE}</style><ha-card><div class="title">${esc(this._name())}</div>
        <div class="muted">${i && i.missing ? "Unknown zone." : "Loading…"}</div>${this._error ? `<div class="err">${esc(this._error)}</div>` : ""}</ha-card>`;
      return;
    }
    if (!this._draft || !this._dirty) this._draft = this._draftFrom(i);
    if (!this._day) this._day = DAYS[(new Date().getDay() + 6) % 7];
    const d = this._draft;
    const bar = (day) => d.days[day].map((p) => {
      const m = (t) => { const [a, b] = t.split(":").map(Number); return a * 60 + b; };
      const l = (m(p.start) / 1440) * 100, w = ((m(p.end) - m(p.start)) / 1440) * 100;
      return `<div class="blk" style="left:${l}%;width:${w}%" title="${esc(p.start)}–${esc(p.end)} ${fmt(p.temperature)} °C">${w > 9 ? fmt(p.temperature) : ""}</div>`;
    }).join("");
    const rows = d.days[this._day].map((p, k) => `<div class="row">
        <input type="time" data-k="${k}" data-f="start" value="${esc(p.start)}"> –
        <input type="time" data-k="${k}" data-f="end" value="${esc(p.end)}">
        <input type="number" step="0.5" min="5" max="23" data-k="${k}" data-f="temperature" value="${esc(p.temperature)}" style="width:5em"> °C
        <button data-del="${k}">✕</button></div>`).join("");
    root.innerHTML = `<style>${STYLE}
        .day { display: grid; grid-template-columns: 3em 1fr; align-items: center; gap: 6px; margin: 3px 0; cursor: pointer; }
        .day.sel .lbl { font-weight: 600; color: var(--primary-color); }
        .track { position: relative; height: 22px; background: var(--secondary-background-color); border-radius: 4px; overflow: hidden; }
        .blk { position: absolute; top: 0; bottom: 0; background: var(--state-climate-heat-color, #ff8100); color: #fff;
               font-size: 0.75em; display: flex; align-items: center; justify-content: center; border-radius: 3px; }
        .hours { display: grid; grid-template-columns: 3em 1fr; font-size: 0.7em; }
        .hours div:last-child { display: flex; justify-content: space-between; }
        .ed { border-top: 1px solid var(--divider-color); margin-top: 10px; padding-top: 6px; }
      </style>
      <ha-card>
        <div class="title">${esc(this._name())} – schedule ${this._dirty ? `<span class="muted">unsaved</span>` : ""}</div>
        <div class="row">Outside the periods <input type="number" step="0.5" min="5" max="23" id="base" value="${esc(d.base)}" style="width:5em"> °C</div>
        <div class="hours muted"><span></span><div><span>0</span><span>6</span><span>12</span><span>18</span><span>24</span></div></div>
        ${DAYS.map((day) => `<div class="day ${day === this._day ? "sel" : ""}" data-day="${day}">
          <span class="lbl">${DAY_NAMES[day]}</span><div class="track">${bar(day)}</div></div>`).join("")}
        <div class="ed"><div class="muted">${DAY_NAMES[this._day]}: the room is at the temperature from the start of each period.</div>
          ${rows || `<div class="muted">No periods.</div>`}
          <div class="row"><button id="add">+ Period</button><button id="wk">Copy to Mon–Fri</button>
            <button id="all">Copy to all days</button><span style="flex:1"></span>
            ${this._dirty ? `<button id="undo">Undo</button>` : ""}<button class="primary" id="save">Save</button></div>
        </div>
        ${this._error ? `<div class="err">${esc(this._error)}</div>` : ""}
      </ha-card>`;
    const $ = (id) => root.getElementById(id);
    const dirty = () => { this._dirty = true; };
    root.querySelectorAll(".day").forEach((el) => el.addEventListener("click", () => { this._day = el.dataset.day; this._render(); }));
    $("base").addEventListener("change", (e) => { d.base = Number(e.target.value); dirty(); this._render(); });
    root.querySelectorAll("input[data-k]").forEach((el) => el.addEventListener("change", (e) => {
      const p = d.days[this._day][Number(el.dataset.k)];
      p[el.dataset.f] = el.dataset.f === "temperature" ? Number(e.target.value) : e.target.value;
      dirty(); this._render();
    }));
    root.querySelectorAll("button[data-del]").forEach((el) => el.addEventListener("click", () => {
      d.days[this._day].splice(Number(el.dataset.del), 1); dirty(); this._render();
    }));
    $("add").addEventListener("click", () => {
      const list = d.days[this._day], last = list[list.length - 1];
      list.push(last ? { start: last.end, end: "23:00", temperature: last.temperature } : { start: "07:00", end: "09:00", temperature: 20 });
      dirty(); this._render();
    });
    const copy = (targets) => { targets.forEach((t) => { if (t !== this._day) d.days[t] = d.days[this._day].map((p) => ({ ...p })); }); dirty(); this._render(); };
    $("wk").addEventListener("click", () => copy(DAYS.slice(0, 5)));
    $("all").addEventListener("click", () => copy(DAYS));
    if (this._dirty) $("undo").addEventListener("click", () => { this._dirty = false; this._error = ""; this._render(); });
    $("save").addEventListener("click", async () => {
      await this._ws({ type: "smart_heat/schedule/set", zone: i.zone, schedule: d });
      if (!this._error) { this._dirty = false; this._render(); }
    });
  }
}

customElements.get("smart-heat-thermostat-card") || customElements.define("smart-heat-thermostat-card", SmartHeatThermostatCard);
customElements.get("smart-heat-schedule-card") || customElements.define("smart-heat-schedule-card", SmartHeatScheduleCard);
window.customCards = window.customCards || [];
window.customCards.push(
  { type: "smart-heat-thermostat-card", name: "Smart Heat thermostat", description: "A zone at a temperature by a time; control and plan." },
  { type: "smart-heat-schedule-card", name: "Smart Heat schedule", description: "A zone's week schedule." },
);
