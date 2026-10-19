/*
 * Klimaplan-Karte – Klimageräte bedienen (Modus, Lüfter inkl. Auto, Soll),
 * Kühl-Zeitpläne je Raum und Hitzeschutz-Automatik.
 * Gehört zur Integration custom_components/klimaplan (wird von ihr ausgeliefert).
 */
const KP_VERSION = "0.3.3";
const DAYS = ["mon", "tue", "wed", "thu", "fri", "sat", "sun"];
const DAY_LABEL = { mon: "Mo", tue: "Di", wed: "Mi", thu: "Do", fri: "Fr", sat: "Sa", sun: "So" };
const DAY_LONG = { mon: "Montag", tue: "Dienstag", wed: "Mittwoch", thu: "Donnerstag", fri: "Freitag", sat: "Samstag", sun: "Sonntag" };
const WEEKDAYS = DAYS.slice(0, 5);
const WEEKEND = DAYS.slice(5);
const MODES = {
  cool: ["Kühlen", "mdi:snowflake"],
  dry: ["Trocknen", "mdi:water-percent"],
  fan_only: ["Lüften", "mdi:fan"],
  heat: ["Heizen", "mdi:fire"],
  auto: ["Auto", "mdi:thermostat-auto"],
  heat_cool: ["Auto", "mdi:thermostat-auto"],
};
const FAN_LABEL = (f) => (f === "auto" ? "Auto" : f === "low" ? "Niedrig" : f === "medium" ? "Mittel" : f === "high" ? "Hoch" : f);

const toMin = (s) => { const [h, m] = String(s).split(":").map(Number); return h * 60 + m; };
const fmtMin = (m) => `${String(Math.floor(m / 60)).padStart(2, "0")}:${String(m % 60).padStart(2, "0")}`;
const fmtT = (t, d = 1) => (t === null || t === undefined || t === "" || isNaN(t) ? "–" : Number(t).toLocaleString("de-DE", { minimumFractionDigits: d, maximumFractionDigits: d }));
const clamp = (v, a, b) => Math.min(b, Math.max(a, v));
const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
const todayKey = (d = new Date()) => DAYS[(d.getDay() + 6) % 7];
const num = (v) => (v === null || v === undefined || v === "" || isNaN(Number(v)) ? null : Number(v));

function tempClass(ist, soll) {
  if (ist === null) return "";
  if (soll !== null && ist >= soll + 2) return "hot";
  if (ist >= 26) return "warm";
  return "ok";
}

function dayText(iso) {
  const d = new Date(iso);
  const now = new Date();
  const time = d.toLocaleTimeString("de-DE", { hour: "2-digit", minute: "2-digit" });
  if (d.toDateString() === now.toDateString()) return `heute ${time}`;
  if (new Date(now.getTime() + 86400000).toDateString() === d.toDateString()) return `morgen ${time}`;
  return `${DAY_LABEL[todayKey(d)]} ${time}`;
}

class KlimaplanCard extends HTMLElement {
  constructor() {
    super();
    this.attachShadow({ mode: "open" });
    this._data = null;
    this._tab = "rooms";
    this._room = null;
    this._edit = null;      // {room, day, windows}
    this._settings = null;  // Entwurf Automatik
    this._pending = {};     // entity -> {temp, timer}
    this._busy = {};        // entity -> Zeitstempel letzter Klick (für optimistische Anzeige)
    this._unsub = null;
    this._error = null;
  }

  setConfig(config) {
    this._config = { ...config };
    if (["rooms", "plan", "auto"].includes(config.view)) this._tab = config.view;
  }

  static getStubConfig() { return {}; }
  getCardSize() { return 8; }

  set hass(hass) {
    const first = !this._hass;
    this._hass = hass;
    if (first) this._subscribe();
    else if (this._data && !this._edit && !this._settings) this._render();
  }

  connectedCallback() { if (this._hass && !this._unsub) this._subscribe(); }
  disconnectedCallback() {
    if (this._unsub) { this._unsub.then((u) => u && u()).catch(() => {}); this._unsub = null; }
  }

  _subscribe() {
    if (this._unsub || !this._hass) return;
    this._unsub = this._hass.connection
      .subscribeMessage((msg) => { this._data = msg; this._error = null; if (!this._edit && !this._settings) this._render(); }, { type: "klimaplan/subscribe" })
      .catch((err) => { this._error = err.message || String(err); this._unsub = null; this._render(); });
  }

  async _ws(payload) {
    try {
      const res = await this._hass.callWS(payload);
      this._data = res;
      this._error = null;
      this._render();
      return res;
    } catch (err) {
      this._error = err.message || String(err);
      this._render();
      throw err;
    }
  }

  async _service(domain, service, data) {
    try {
      await this._hass.callService(domain, service, data);
      this._error = null;
    } catch (err) {
      this._error = err.message || String(err);
      this._render();
    }
  }

  // --------------------------------------------------------------- Helfer
  _rooms() {
    const rooms = Object.entries(this._data?.rooms || {}).map(([id, r]) => ({ id, ...r }));
    const order = this._config.rooms;
    if (Array.isArray(order) && order.length) {
      return order.map((id) => rooms.find((r) => r.id === id || r.climate === id || r.name === id)).filter(Boolean);
    }
    // Räume mit Zeitplan zuerst
    return rooms.sort((a, b) => (Number(b.plan_enabled) - Number(a.plan_enabled)) || a.name.localeCompare(b.name, "de"));
  }

  _currentRoom() {
    const rooms = this._rooms();
    return rooms.find((r) => r.id === this._room) || rooms.find((r) => r.plan_enabled) || rooms[0] || null;
  }

  _st(entity) { return entity ? this._hass?.states?.[entity] : undefined; }

  _fanInfo(room) {
    const st = this._st(room.climate);
    const modes = (st?.attributes?.fan_modes || []).filter((f) => f !== "off");
    const hasAuto = modes.includes("auto");
    const sw = this._st(room.fan_auto_switch);
    const levels = modes.filter((f) => f !== "auto");
    const autoOn = hasAuto ? st?.attributes?.fan_mode === "auto" : sw?.state === "on";
    const canAuto = hasAuto || !!sw;
    return { levels, autoOn, canAuto, current: autoOn ? "auto" : st?.attributes?.fan_mode };
  }

  _soll(room) {
    const st = this._st(room.climate);
    const p = this._pending[room.climate];
    return p ? p.temp : num(st?.attributes?.temperature);
  }

  // --------------------------------------------------------------- Rendering
  _render() {
    if (!this._config) return;
    const title = this._config.title ?? "Kühlung";
    let body;
    if (this._error && !this._data) body = `<div class="err">Klimaplan nicht erreichbar: ${esc(this._error)}</div>`;
    else if (!this._data) body = `<div class="muted pad">Lade …</div>`;
    else if (!Object.keys(this._data.rooms).length) body = `<div class="muted pad">Noch keine Räume. Über den Dienst <code>klimaplan.add_room</code> anlegen.</div>`;
    else body = this._tab === "plan" ? this._renderPlan() : this._tab === "auto" ? this._renderAuto() : this._renderRooms();
    const tabs = this._config.hide_tabs ? "" : `
      <div class="tabs">
        ${[["rooms", "Räume", "mdi:air-conditioner"], ["plan", "Zeitplan", "mdi:calendar-clock"], ["auto", "Automatik", "mdi:robot-outline"]]
          .map(([k, l, i]) => `<button class="tab ${this._tab === k ? "on" : ""}" data-tab="${k}"><ha-icon icon="${i}"></ha-icon>${l}</button>`).join("")}
      </div>`;
    const html = `
      <div class="head">
        <div class="title">${esc(title)}</div>
        ${this._data ? this._renderHead() : ""}
      </div>
      ${this._data ? this._renderBanner() : ""}
      ${this._error && this._data ? `<div class="err">${esc(this._error)}</div>` : ""}
      ${tabs}
      <div class="body">${body}</div>
      ${this._edit ? this._renderEditor() : ""}`;
    if (html === this._lastHtml && this._card?.isConnected) return;
    this._lastHtml = html;
    if (!this._card || this._card.getRootNode() !== this.shadowRoot) {
      this.shadowRoot.innerHTML = `<style>${STYLE}</style><ha-card></ha-card>`;
      this._card = this.shadowRoot.querySelector("ha-card");
    }
    const card = this._card;
    const keep = this._snapshot(card);
    card.style.minHeight = keep.height ? `${keep.height}px` : "";
    card.innerHTML = html;
    this._bind();
    this._restore(card, keep);
  }

  _snapshot(card) {
    const scrollers = [];
    let n = this;
    while (n) {
      if (n.scrollTop || n.scrollLeft) scrollers.push([n, n.scrollTop, n.scrollLeft]);
      n = n.parentNode || n.host;
    }
    const inner = [...card.querySelectorAll(".tabs, .chips, .dialog")].map((el) => [el.scrollLeft, el.scrollTop]);
    const act = this.shadowRoot.activeElement;
    let focus = null;
    if (act && act !== card) {
      focus = act.tagName + [...act.attributes].filter((a) => a.name.startsWith("data-")).map((a) => `[${a.name}="${CSS.escape(a.value)}"]`).join("");
    }
    return { x: window.scrollX, y: window.scrollY, scrollers, inner, focus, height: card.offsetHeight };
  }

  _restore(card, k) {
    const apply = () => {
      for (const [el, top, left] of k.scrollers) { if (el.scrollTop !== top) el.scrollTop = top; if (el.scrollLeft !== left) el.scrollLeft = left; }
      if (window.scrollY !== k.y || window.scrollX !== k.x) window.scrollTo(k.x, k.y);
      [...card.querySelectorAll(".tabs, .chips, .dialog")].forEach((el, i) => { if (k.inner[i]) { el.scrollLeft = k.inner[i][0]; el.scrollTop = k.inner[i][1]; } });
    };
    if (k.focus) {
      try { card.querySelector(k.focus)?.focus({ preventScroll: true }); } catch (e) { /* ungültiger Selektor */ }
    }
    apply();
    requestAnimationFrame(() => { apply(); card.style.minHeight = ""; });
  }

  _renderHead() {
    const d = this._data;
    const s = d.settings;
    return `
      <div class="hright">
        <span class="out ${d.hot ? "hot" : ""}" title="Außentemperatur">
          <ha-icon icon="${d.hot ? "mdi:weather-sunny-alert" : "mdi:thermometer"}"></ha-icon>${fmtT(d.outside)} °C
        </span>
        <button class="pill btn ${s.enabled ? "on" : ""}" data-set="enabled" title="Automatik insgesamt">
          <ha-icon icon="${s.enabled ? "mdi:robot" : "mdi:robot-off-outline"}"></ha-icon>Automatik ${s.enabled ? "an" : "aus"}
        </button>
      </div>`;
  }

  _renderBanner() {
    const d = this._data;
    if (d.blocked) {
      const name = this._st(d.options.block_entity)?.attributes?.friendly_name || d.options.block_entity;
      return `<div class="banner">Kühl-Automatik gesperrt, solange <b>${esc(name)}</b> an ist. Bedienung von Hand geht weiter.</div>`;
    }
    return "";
  }

  // ---- Räume
  _renderRooms() {
    return `${this._renderConditions(true)}
      <div class="grid">${this._rooms().map((r) => this._renderTile(r)).join("")}</div>
      <div class="hint foot">Bedienen geht immer von Hand. Die Automatik schaltet nur Geräte ein, die aus sind, und nur ihre eigenen wieder aus.
        Schaltest du ein automatisch gestartetes Gerät aus oder änderst den Modus, pausiert sie in dem Raum bis zum nächsten Zeitfenster.
        Raumname antippen öffnet den Zeitplan.</div>`;
  }

  _status(room) {
    const s = room.status;
    const st = this._st(room.climate);
    const hvac = st?.state ?? s.hvac;
    const t = s.target;
    const src = t ? (t.source === "plan" ? `Plan bis ${t.until}` : "Hitzeschutz") : "";
    if (hvac === "unavailable" || hvac === "unknown" || !st) return ["off", "mdi:lan-disconnect", "nicht erreichbar"];
    if (s.heizplan && hvac && hvac !== "off") return ["heizplan", "mdi:fire", `Heizplan · ${s.heizplan}`];
    if (s.active) return ["auto", "mdi:robot", `Automatik · ${src}`];
    if (hvac && hvac !== "off") return ["manual", "mdi:hand-back-right", "Manuell"];
    if (s.paused) return ["pause", "mdi:pause-circle-outline", "Pausiert bis Wechsel"];
    if (t && s.waiting) return ["wait", "mdi:timer-sand", `wartet · an ab ${fmtT(t.start_at)} °C`];
    return ["off", "mdi:power", "Aus"];
  }

  _planLine(room) {
    const s = room.status;
    const set = this._data.settings;
    const d = set.start_delta;
    if (!room.plan_enabled || !set.plan_enabled) return "Zeitplan: aus";
    if (s.window) return `Zeitplan jetzt bis ${s.window.end}: ab ${fmtT(s.window.temp + d)} °C innen → ${fmtT(s.window.temp)} °C`;
    if (s.next_window) return `Zeitplan ${dayText(s.next_window.at)}–${s.next_window.end}: ab ${fmtT(s.next_window.temp + d)} °C innen → ${fmtT(s.next_window.temp)} °C`;
    return "Zeitplan: keine Fenster";
  }

  _protectLine(room) {
    const set = this._data.settings;
    if (!room.protect || !set.protect_enabled) return "Hitzeschutz: aus";
    return `Hitzeschutz: ab ${fmtT(set.protect_on)} °C innen → ${fmtT(set.protect_temp)} °C`;
  }

  _renderTile(room) {
    const st = this._st(room.climate);
    const a = st?.attributes || {};
    const hvac = st?.state ?? room.status.hvac;
    const on = hvac && !["off", "unavailable", "unknown"].includes(hvac);
    const ist = num(a.current_temperature ?? room.status.current_temp);
    const soll = this._soll(room);
    const [cls, icon, label] = this._status(room);
    const fan = this._fanInfo(room);
    const modes = (a.hvac_modes || []).filter((m) => m !== "off" && MODES[m]);
    const target = room.status.target;
    return `
      <div class="tile ${on ? "running " + hvac : ""}">
        <div class="trow">
          <button class="link" data-open="${room.id}" title="Zeitplan öffnen">${esc(room.name)}</button>
          <span class="badge ${cls}"><ha-icon icon="${icon}"></ha-icon>${esc(label)}</span>
        </div>
        <div class="main">
          <div class="ist ${tempClass(ist, target ? target.temp : null)}">
            <div class="lbl">Ist</div><div class="big">${fmtT(ist)}<small>°C</small></div>
          </div>
          <div class="soll">
            <div class="lbl">Soll${target && !on ? ` · Ziel ${fmtT(target.temp)}` : ""}</div>
            <div class="stepper">
              <button class="round" data-temp="-" data-ent="${room.climate}" aria-label="kälter">−</button>
              <span class="mid ${this._pending[room.climate] ? "pending" : ""}">${fmtT(soll)}</span>
              <button class="round" data-temp="+" data-ent="${room.climate}" aria-label="wärmer">+</button>
            </div>
          </div>
          <button class="power ${on ? "on" : ""}" data-power="${room.climate}" title="${on ? "Ausschalten" : "Kühlen einschalten"}">
            <ha-icon icon="mdi:power"></ha-icon>
          </button>
        </div>
        <div class="chips modes">
          ${modes.map((m) => `<button class="chip ${hvac === m ? "on " + m : ""}" data-mode="${m}" data-ent="${room.climate}">
            <ha-icon icon="${MODES[m][1]}"></ha-icon>${MODES[m][0]}</button>`).join("")}
        </div>
        <div class="chips fans">
          <ha-icon class="fanicon" icon="mdi:fan"></ha-icon>
          ${fan.canAuto ? `<button class="chip sm ${fan.autoOn ? "on" : ""}" data-fan="auto" data-room="${room.id}">Auto</button>` : ""}
          ${fan.levels.map((f) => `<button class="chip sm ${!fan.autoOn && fan.current === f ? "on" : ""}" data-fan="${esc(f)}" data-room="${room.id}">${esc(FAN_LABEL(f))}</button>`).join("")}
        </div>
        <div class="next"><ha-icon icon="mdi:shield-sun-outline"></ha-icon>${esc(this._protectLine(room))}</div>
        <div class="next"><ha-icon icon="mdi:calendar-clock"></ha-icon>${esc(this._planLine(room))}</div>
      </div>`;
  }

  // ---- Zeitplan
  _renderPlan() {
    const room = this._currentRoom();
    if (!room) return "";
    const today = todayKey();
    const nowMin = new Date().getHours() * 60 + new Date().getMinutes();
    const rows = DAYS.map((d) => {
      const wins = room.plan[d] || [];
      const blocks = wins.map((w) => {
        const s = toMin(w.start), e = toMin(w.end);
        return `<div class="win" style="left:${s / 14.4}%;width:${(e - s) / 14.4}%" title="${w.start}–${w.end} · ${fmtT(w.temp)} °C · Lüfter ${esc(FAN_LABEL(w.fan || "–"))}">
          <span>${fmtT(w.temp, w.temp % 1 ? 1 : 0)}°</span></div>`;
      }).join("");
      const text = wins.length ? wins.map((w) => `${w.start}–${w.end}`).join(", ") : "kein Fenster";
      return `
        <button class="row ${d === today ? "today" : ""}" data-day="${d}" title="${DAY_LONG[d]} bearbeiten">
          <span class="day">${DAY_LABEL[d]}</span>
          <span class="bar">${blocks}${d === today ? `<i class="now" style="left:${nowMin / 14.4}%"></i>` : ""}</span>
          <span class="wtext">${esc(text)}</span>
        </button>`;
    }).join("");
    return `
<div class="sd plantop">Tag antippen zum Bearbeiten. Im blauen Fenster kühlt der Raum, wenn die Grundbedingungen erfüllt sind (Hitzetag, heute schon jemand da),
        ${this._data.settings.plan_needs_home ? "gerade jemand zu Hause und " : ""}innen mindestens ${fmtT(this._data.settings.start_delta)} K über dem Fenster-Soll.
        Außerhalb der Fenster greift nur der Hitzeschutz (ab ${fmtT(this._data.settings.protect_on)} °C innen).</div>
      <div class="chips">
        ${this._rooms().map((r) => `<button class="chip ${r.id === room.id ? "on" : ""}" data-room-chip="${r.id}">
          ${r.plan_enabled ? `<ha-icon icon="mdi:calendar-check"></ha-icon>` : ""}${esc(r.name)}</button>`).join("")}
      </div>
      <div class="srow">
        <label class="toggle"><input type="checkbox" data-room-flag="plan_enabled" data-room="${room.id}" ${room.plan_enabled ? "checked" : ""}>
          Zeitplan für ${esc(room.name)}</label>
        <label class="toggle"><input type="checkbox" data-room-flag="protect" data-room="${room.id}" ${room.protect ? "checked" : ""}>
          Hitzeschutz</label>
      </div>
      <div class="week ${room.plan_enabled ? "" : "dim"}">
        <div class="scale"><span></span><span class="ticks">${[0, 6, 12, 18, 24].map((h) => `<span style="left:${h / 0.24}%">${h}</span>`).join("")}</span><span class="wtext"></span></div>
        ${rows}
      </div>
      ${room.plan_enabled ? "" : `<div class="sd">Zeitplan für ${esc(room.name)} ist aus – hier greift nur der Hitzeschutz. Haken oben setzen, um die Fenster zu nutzen.</div>`}
`;
  }

  _renderEditor() {
    const e = this._edit;
    const room = this._data.rooms[e.room];
    const st = this._st(room.climate);
    const fans = ["auto", ...((st?.attributes?.fan_modes || []).filter((f) => f !== "off" && f !== "auto"))];
    const list = e.windows.map((w, i) => `
      <div class="wrow">
        <input type="time" step="900" value="${w.start}" data-w="${i}" data-k="start">
        <span>–</span>
        <input type="time" step="900" value="${w.end}" data-w="${i}" data-k="end">
        <span class="stepper sm">
          <button class="round sm" data-wt="${i}" data-d="-0.5">−</button>
          <span class="wv">${fmtT(w.temp)}°</span>
          <button class="round sm" data-wt="${i}" data-d="0.5">+</button>
        </span>
        <select data-w="${i}" data-k="fan" title="Lüfter">
          ${fans.map((f) => `<option value="${esc(f)}" ${(w.fan || "auto") === f ? "selected" : ""}>${esc(FAN_LABEL(f))}</option>`).join("")}
        </select>
        <button class="txt danger" data-wdel="${i}" title="Fenster löschen"><ha-icon icon="mdi:delete-outline"></ha-icon></button>
      </div>`).join("");
    const copy = [["", "nur dieser Tag"], ["weekdays", "auch Mo–Fr"], ["weekend", "auch Sa–So"], ["all", "alle Tage"]];
    return `
      <div class="overlay" data-close>
        <div class="dialog" role="dialog">
          <div class="dtitle">${esc(room.name)} · ${DAY_LONG[e.day]}</div>
          ${list || `<div class="muted">Kein Kühlfenster – an diesem Tag nur Hitzeschutz.</div>`}
          <button class="txt" data-wadd><ha-icon icon="mdi:plus"></ha-icon>Fenster hinzufügen</button>
          <div class="field"><label>Übernehmen</label>
            <select data-copy>${copy.map(([k, l]) => `<option value="${k}" ${e.copy === k ? "selected" : ""}>${l}</option>`).join("")}</select>
          </div>
          ${e.error ? `<div class="err">${esc(e.error)}</div>` : ""}
          <div class="dact">
            <button class="txt" data-cancel>Abbrechen</button>
            <button class="txt primary" data-save>Speichern</button>
          </div>
        </div>
      </div>`;
  }

  // ---- Automatik
  _renderAuto() {
    const s = this._settings || this._data.settings;
    const d = this._data;
    const dirty = !!this._settings;
    const num = (k, label, unit, step, min, max, help) => `
      <label class="sfield">${label}
        <span class="inp"><input type="number" data-s="${k}" value="${s[k]}" step="${step}" min="${min}" max="${max}"><em>${unit}</em></span>
        ${help ? `<small>${help}</small>` : ""}
      </label>`;
    const sw = (k, label) => `<label class="toggle"><input type="checkbox" data-s="${k}" ${s[k] ? "checked" : ""}><b>${label}</b></label>`;
    const ent = (e) => (e ? esc(this._st(e)?.attributes?.friendly_name || e) : "–");
    const motion = d.options.motion_entities || [];
    const rooms = this._rooms().map((r) => `
      <tr><td>${esc(r.name)}</td>
        <td><input type="checkbox" data-room-flag="plan_enabled" data-room="${r.id}" ${r.plan_enabled ? "checked" : ""} aria-label="Zeitplan ${esc(r.name)}"></td>
        <td><input type="checkbox" data-room-flag="protect" data-room="${r.id}" ${r.protect ? "checked" : ""} aria-label="Hitzeschutz ${esc(r.name)}"></td>
        <td class="muted">${r.status.target ? `${fmtT(r.status.target.temp)} °C · ${r.status.target.source === "plan" ? "Zeitplan" : "Hitzeschutz"}` : "–"}</td></tr>`).join("");
    return `
      <div class="autotab">
        <div class="sec">
          <div class="sh"><ha-icon icon="mdi:clipboard-check-outline"></ha-icon>Heute · Grundbedingungen</div>
          ${this._renderConditions()}
        </div>

        <div class="grp">
          <div class="gh">A · Grundbedingungen <small>gelten für Hitzeschutz <b>und</b> Zeitpläne – erst wenn alle erfüllt sind, darf überhaupt automatisch gekühlt werden</small></div>
          <div class="gsd">Dazu: Automatik an und Sperre (${esc(this._st(d.options.block_entity)?.attributes?.friendly_name || "keine")}) aus. Den aktuellen Stand siehst du oben unter „Heute“.</div>
        <div class="sec">
          <div class="sh"><ha-icon icon="mdi:white-balance-sunny"></ha-icon>1 · Hitzetag</div>
          <div class="sd">Hitzetag ist, wenn es <b>außen heute</b> schon über der Grenze war${d.options.forecast_entity ? " oder die Vorhersage für heute darüber liegt" : ""}.
            Er gilt dann <b>den ganzen Tag bis Mitternacht</b> – kühlt es abends ab, laufen Zeitplan und Hitzeschutz trotzdem weiter.
            Ohne Hitzetag kühlt die Automatik gar nicht – weder Hitzeschutz noch Zeitplan.</div>
          <div class="sgrid">
            ${num("outside_limit", "Hitzetag, wenn außen über", "°C", 0.5, 15, 45)}
            ${d.options.forecast_entity ? `<label class="toggle"><input type="checkbox" data-s="use_forecast" ${s.use_forecast ? "checked" : ""}>
              <span>auch nach Vorhersage <small class="muted">– schon morgens Hitzetag, wenn heute über ${fmtT(s.outside_limit, 0)} °C angesagt sind (gut für Wochenend-Pläne ab 10 Uhr)</small></span></label>` : ""}
          </div>
          <div class="hint">Heute: gemessen max. ${fmtT(d.measured_max)} °C${d.options.forecast_entity ? ` · Vorhersage ${fmtT(d.forecast_max)} °C` : ""} · jetzt ${fmtT(d.outside)} °C</div>
        </div>

        <div class="sec">
          <div class="sh"><ha-icon icon="mdi:home-account"></ha-icon>2 · Heute jemand da (kein Urlaub)</div>
          <div class="sd">Ohne Urlaub: an diesem Tag war schon jemand da – eine Person war zu Hause
            ${motion.length ? `oder mindestens ${s.motion_min} verschiedene Bewegungsmelder haben ausgelöst` : ""}. Danach dürfen Hitzeschutz und Zeitplan auch laufen, wenn ihr tagsüber weg seid.</div>
          <div class="sgrid">
            <label class="sfield">Tag beginnt um
              <input type="time" data-s="presence_cutoff" value="${esc(s.presence_cutoff)}">
              <small>00:00 = ganzer Kalendertag</small>
            </label>
            ${motion.length ? num("motion_min", "oder Melder ausgelöst, mindestens", "Stück", 1, 0, 10, "0 = Melder nicht benutzen") : ""}
          </div>
        </div>

        </div>

        <div class="grp">
          <div class="gh">B · Regeln <small>gelten zusätzlich zu den Grundbedingungen, je Raum – trifft beides zu, zählt der niedrigere Soll</small></div>
        <div class="sec">
          <div class="sh"><ha-icon icon="mdi:shield-sun-outline"></ha-icon>3 · Hitzeschutz ${sw("protect_enabled", "an")}</div>
          <div class="sd">Ganztägig: Wird es <b>innen</b> zu warm, kühlt der Raum herunter – auch wenn gerade niemand da ist. Gilt für Räume mit Haken „Hitzeschutz“ (unten).</div>
          <div class="sgrid">
            ${num("protect_on", "Einschalten ab innen", "°C", 0.5, 18, 40)}
            ${num("protect_temp", "dann kühlen auf", "°C", 0.5, 16, 30)}
            <label class="sfield">Lüfter
              <select data-s="protect_fan">${["auto", "1", "2", "3", "4"].map((f) => `<option value="${f}" ${(s.protect_fan || "auto") === f ? "selected" : ""}>${FAN_LABEL(f)}</option>`).join("")}</select>
            </label>
          </div>
        </div>

        <div class="sec">
          <div class="sh"><ha-icon icon="mdi:calendar-clock"></ha-icon>4 · Zeitpläne ${sw("plan_enabled", "an")}</div>
          <div class="sd">Nur in den Kühlfenstern je Raum (Tab „Zeitplan“), früher und kühler als der Hitzeschutz.</div>
          <div class="sgrid">
            ${num("start_delta", "Einschalten ab Plan-Soll plus", "K", 0.5, 0, 10, `z. B. Plan 23 °C → an ab ${fmtT(23 + Number(s.start_delta))} °C`)}
          </div>
          <label class="toggle"><input type="checkbox" data-s="plan_needs_home" ${s.plan_needs_home ? "checked" : ""}>
            <span>Nur wenn gerade jemand zu Hause ist <small class="muted">${s.plan_needs_home ? "– sind alle weg, endet der Zeitplan" : "– aus: Zeitplan läuft auch, wenn alle unterwegs sind (z. B. vorkühlen)"}</small></span></label>
        </div>

        </div>

        ${dirty ? `<div class="savebar"><span>Änderungen noch nicht gespeichert</span><span><button class="txt" data-s-cancel>Verwerfen</button><button class="txt primary" data-s-save>Speichern</button></span></div>` : ""}

        <div class="sec">
          <div class="sh"><ha-icon icon="mdi:floor-plan"></ha-icon>Räume <small class="muted">· wirkt sofort</small></div>
          <table class="rtable">
            <thead><tr><th>Raum</th><th>Zeitplan</th><th>Hitzeschutz</th><th>Ziel jetzt</th></tr></thead>
            <tbody>${rooms}</tbody>
          </table>
        </div>

        <div class="sd">Gelten Zeitplan und Hitzeschutz gleichzeitig, zählt der niedrigere Soll.
          Klimaplan schaltet nur Geräte ein, die aus sind, und nur selbst eingeschaltete wieder aus.
          Von Hand ausgeschaltet oder Modus geändert → Pause in diesem Raum bis zum nächsten Zeitfenster bzw. bis der Hitzeschutz endet.</div>
        <div class="hint">Verknüpft: Außen ${ent(d.options.outside_entity)} · Personen ${(d.options.persons || []).map(ent).join(", ") || "–"}
          ${motion.length ? ` · Melder ${motion.map(ent).join(", ")}` : ""}
          · Sperre ${ent(d.options.block_entity)}${d.options.vacation_entity ? ` · Urlaub ${ent(d.options.vacation_entity)}` : ""}.
          Ändern: Einstellungen → Geräte &amp; Dienste → Klimaplan → Konfigurieren.</div>
      </div>`;
  }

  // Ampel: warum kühlt die Automatik gerade (nicht)?
  _renderConditions(compact = false) {
    const d = this._data;
    const s = d.settings;
    const ent = (e) => (e ? esc(this._st(e)?.attributes?.friendly_name || e) : "");
    const persons = (d.options.persons || []).filter((p) => this._st(p)?.state === "home").map(ent);
    const motionSeen = (d.motion_today || []).length;
    const motionAll = (d.options.motion_entities || []).length;
    let who = persons.length ? `${persons.join(", ")} zu Hause` : d.present_today ? "heute schon jemand da gewesen" : "heute noch niemand da";
    if (motionAll && s.motion_min > 0) who += ` · Melder heute ${motionSeen}/${s.motion_min}`;
    const items = [
      [s.enabled, "Automatik", s.enabled ? "an" : "aus – nur Bedienung von Hand"],
      [d.hot, "Hitzetag", `heute max. ${fmtT(d.measured_max)} °C${d.options.forecast_entity && s.use_forecast ? `, Vorhersage ${fmtT(d.forecast_max)} °C` : ""} – Grenze ${fmtT(s.outside_limit, 0)} °C`],
      [d.present_today, "Heute jemand da", who],
      [!d.blocked, "Sperre", d.blocked ? `${ent(d.options.block_entity)} ist an` : "frei"],
    ];
    const ok = items.every((i) => i[0]);
    if (compact) {
      return `<div class="conds compact">
        ${items.map(([on, l, t]) => `<span class="cond ${on ? "yes" : "no"}" title="${esc(t)}"><ha-icon icon="${on ? "mdi:check-circle" : "mdi:close-circle"}"></ha-icon><b>${l}</b>${on ? "" : `<span>${t}</span>`}</span>`).join("")}
        <div class="cres">${ok ? "Grundbedingungen erfüllt – Räume kühlen, sobald Hitzeschutz oder Zeitplan ihre Schwelle erreichen." : "Grundbedingung nicht erfüllt (rot) – heute kühlt nichts automatisch. Details im Tab „Automatik“."}</div>
      </div>`;
    }
    return `<div class="conds ${ok ? "ok" : ""}">
      ${items.map(([on, l, t]) => `<div class="cond ${on ? "yes" : "no"}"><ha-icon icon="${on ? "mdi:check-circle" : "mdi:close-circle"}"></ha-icon><div><b>${l}</b><span>${t}</span></div></div>`).join("")}
      <div class="cres">${ok ? "Alle Grundbedingungen erfüllt – Hitzeschutz und Zeitpläne dürfen kühlen." : "Eine Grundbedingung fehlt (rot) – weder Hitzeschutz noch Zeitplan kühlen automatisch."}</div>
    </div>`;
  }

  // --------------------------------------------------------------- Ereignisse
  _bind() {
    const $ = (sel) => this._card.querySelectorAll(sel);
    $("[data-tab]").forEach((el) => el.addEventListener("click", () => { this._tab = el.dataset.tab; this._settings = null; this._render(); }));
    $("[data-open]").forEach((el) => el.addEventListener("click", () => { this._room = el.dataset.open; this._tab = "plan"; this._render(); }));
    $("[data-room-chip]").forEach((el) => el.addEventListener("click", () => { this._room = el.dataset.roomChip; this._render(); }));
    $("[data-set]").forEach((el) => el.addEventListener("click", () => {
      const k = el.dataset.set;
      this._ws({ type: "klimaplan/set_settings", [k]: !this._data.settings[k] }).catch(() => {});
    }));

    // Bedienung
    $("[data-temp]").forEach((el) => el.addEventListener("click", () => this._stepTemp(el.dataset.ent, el.dataset.temp === "+" ? 1 : -1)));
    $("[data-power]").forEach((el) => el.addEventListener("click", () => {
      const ent = el.dataset.power;
      const st = this._st(ent);
      const on = st && !["off", "unavailable", "unknown"].includes(st.state);
      this._service("climate", "set_hvac_mode", { entity_id: ent, hvac_mode: on ? "off" : "cool" });
    }));
    $("[data-mode]").forEach((el) => el.addEventListener("click", () =>
      this._service("climate", "set_hvac_mode", { entity_id: el.dataset.ent, hvac_mode: el.dataset.mode })));
    $("[data-fan]").forEach((el) => el.addEventListener("click", () =>
      this._ws({ type: "klimaplan/set_fan", room_id: el.dataset.room, fan: el.dataset.fan }).catch(() => {})));

    // Zeitplan
    $("[data-room-flag]").forEach((el) => el.addEventListener("change", () =>
      this._ws({ type: "klimaplan/update_room", room_id: el.dataset.room, [el.dataset.roomFlag]: el.checked }).catch(() => {})));
    $("[data-day]").forEach((el) => el.addEventListener("click", () => {
      const room = this._currentRoom();
      this._edit = { room: room.id, day: el.dataset.day, windows: (room.plan[el.dataset.day] || []).map((w) => ({ ...w })), copy: "" };
      this._render();
    }));
    $("[data-close]").forEach((el) => el.addEventListener("click", (ev) => { if (ev.target === el) { this._edit = null; this._render(); } }));
    $("[data-cancel]").forEach((el) => el.addEventListener("click", () => { this._edit = null; this._render(); }));
    $("[data-w]").forEach((el) => el.addEventListener("change", () => { this._edit.windows[Number(el.dataset.w)][el.dataset.k] = el.value; }));
    $("[data-wt]").forEach((el) => el.addEventListener("click", () => {
      const w = this._edit.windows[Number(el.dataset.wt)];
      w.temp = clamp(Number(w.temp) + Number(el.dataset.d), 16, 30);
      this._render();
    }));
    $("[data-wdel]").forEach((el) => el.addEventListener("click", () => { this._edit.windows.splice(Number(el.dataset.wdel), 1); this._render(); }));
    $("[data-wadd]").forEach((el) => el.addEventListener("click", () => {
      const ws = this._edit.windows;
      const last = ws.length ? toMin(ws[ws.length - 1].end) : 18 * 60;
      const start = Math.min(last, 22 * 60);
      ws.push({ start: fmtMin(start), end: fmtMin(Math.min(start + 180, 23 * 60 + 45)), temp: ws.length ? ws[ws.length - 1].temp : 23, fan: "auto" });
      this._render();
    }));
    $("[data-copy]").forEach((el) => el.addEventListener("change", () => { this._edit.copy = el.value; }));
    $("[data-save]").forEach((el) => el.addEventListener("click", () => this._saveDay()));

    // Automatik
    $("[data-s]").forEach((el) => el.addEventListener("change", () => {
      this._settings = this._settings || { ...this._data.settings };
      this._settings[el.dataset.s] = el.type === "checkbox" ? el.checked : el.type === "number" ? Number(el.value) : el.value;
      this._render();
    }));
    $("[data-s-cancel]").forEach((el) => el.addEventListener("click", () => { this._settings = null; this._render(); }));
    $("[data-s-save]").forEach((el) => el.addEventListener("click", () => {
      const { enabled, ...rest } = this._settings;
      this._settings = null;
      this._ws({ type: "klimaplan/set_settings", ...rest }).catch(() => {});
    }));
  }

  _stepTemp(ent, dir) {
    const st = this._st(ent);
    if (!st) return;
    const a = st.attributes;
    const step = Number(a.target_temp_step) || 0.5;
    const cur = this._pending[ent]?.temp ?? num(a.temperature) ?? 24;
    const val = clamp(Math.round((cur + dir * step) / step) * step, Number(a.min_temp ?? 16), Number(a.max_temp ?? 30));
    const p = this._pending[ent] || {};
    clearTimeout(p.timer);
    p.temp = val;
    // mehrere Klicks sammeln, dann einmal senden
    p.timer = setTimeout(async () => {
      await this._service("climate", "set_temperature", { entity_id: ent, temperature: val });
      setTimeout(() => { if (this._pending[ent] === p) { delete this._pending[ent]; this._render(); } }, 2500);
    }, 700);
    this._pending[ent] = p;
    this._render();
  }

  async _saveDay() {
    const e = this._edit;
    const copyTo = e.copy === "weekdays" ? WEEKDAYS : e.copy === "weekend" ? WEEKEND : e.copy === "all" ? DAYS : [];
    try {
      await this._hass.callWS({
        type: "klimaplan/set_day", room_id: e.room, day: e.day,
        windows: e.windows.map((w) => ({ start: w.start, end: w.end, temp: Number(w.temp), fan: w.fan || "auto" })),
        copy_to: copyTo.filter((d) => d !== e.day),
      }).then((res) => { this._data = res; });
      this._edit = null;
      this._error = null;
    } catch (err) {
      e.error = err.message || String(err);
    }
    this._render();
  }
}

const STYLE = `
  :host { display:block; container-type: inline-size; --kp-cool:#0288d1; --kp-cool-bg:rgba(2,136,209,.14); --kp-sun:#f57c00; }
  ha-card { display:block; padding: 12px 16px 16px; position: relative; overflow: hidden; min-height: 120px; }
  ha-icon { --mdc-icon-size:18px; }
  .head { display:flex; flex-wrap:wrap; align-items:center; justify-content:space-between; gap:8px; }
  .title { font-size: 1.3em; font-weight: 500; }
  .hright { display:flex; align-items:center; gap:6px; flex-wrap:wrap; }
  .out { display:inline-flex; align-items:center; gap:4px; font-size:1.15em; font-weight:600; margin-right:4px; }
  .out.hot { color: var(--kp-sun); }
  .pill { display:inline-flex; align-items:center; gap:4px; font-size:.82em; padding:4px 10px; border-radius:14px;
    background: rgba(127,127,127,.13); color: var(--secondary-text-color); white-space:nowrap; border:none; font-family:inherit; }
  .pill ha-icon { --mdc-icon-size:16px; }
  .pill.on { background: var(--kp-cool-bg); color: var(--kp-cool); font-weight:600; }
  .pill.sun.on { background: rgba(245,124,0,.16); color: var(--kp-sun); }
  .pill.btn { cursor:pointer; }
  .pill.btn.on { background: var(--kp-cool); color:#fff; }
  .tab, .chip { border:1px solid var(--divider-color); background:var(--card-background-color); color:var(--primary-text-color);
    padding:6px 12px; border-radius:16px; cursor:pointer; font:inherit; font-size:.9em; display:inline-flex; align-items:center; gap:5px; }
  .tab.on, .chip.on { background:var(--kp-cool); color:#fff; border-color:var(--kp-cool); }
  .chip.on.heat { background:#e8590c; border-color:#e8590c; }
  .chip.on.dry { background:#00897b; border-color:#00897b; }
  .chip.on.fan_only { background:#546e7a; border-color:#546e7a; }
  .chip.sm { padding:4px 11px; min-width:36px; justify-content:center; }
  .tabs { display:flex; gap:6px; margin:12px 0 10px; }
  .banner { margin-top:10px; padding:8px 12px; border-radius:8px; background: rgba(255,152,0,.15); }
  .err { margin-top:10px; padding:8px 12px; border-radius:8px; background: rgba(219,68,55,.15); }
  .muted { color: var(--secondary-text-color); }
  .pad { padding: 16px 0; }
  .hint { font-size:.8em; color:var(--secondary-text-color); margin-top:10px; line-height:1.4; }
  .grid { display:grid; grid-template-columns: repeat(auto-fill, minmax(260px, 1fr)); gap: 12px; }
  .tile { border:1px solid var(--divider-color); border-radius:14px; padding:10px 12px; display:flex; flex-direction:column; gap:8px; transition: background .3s; }
  .tile.running.cool { background: linear-gradient(160deg, var(--kp-cool-bg), transparent 70%); border-color: rgba(2,136,209,.45); }
  .tile.running.heat { background: linear-gradient(160deg, rgba(232,89,12,.12), transparent 70%); }
  .tile.running.dry, .tile.running.fan_only { background: linear-gradient(160deg, rgba(0,137,123,.1), transparent 70%); }
  .trow { display:flex; justify-content:space-between; align-items:center; gap:8px; }
  .link { border:none; background:none; padding:0; color:var(--primary-text-color); cursor:pointer; font:inherit; font-weight:600; font-size:1.05em; text-align:left; }
  .badge { font-size:.75em; padding:3px 8px; border-radius:10px; background: rgba(127,127,127,.15); white-space:nowrap; display:inline-flex; align-items:center; gap:3px; }
  .badge ha-icon { --mdc-icon-size:14px; width:14px; height:14px; flex:none; }
  .badge.auto { background: var(--kp-cool-bg); color: var(--kp-cool); font-weight:600; }
  .badge.manual { background: rgba(255,152,0,.2); }
  .badge.heizplan { background: rgba(232,89,12,.16); color:#c2410c; font-weight:600; }
  .badge.pause { background: rgba(156,39,176,.14); }
  .badge.wait { background: rgba(3,169,244,.1); }
  .main { display:flex; align-items:flex-end; gap:16px; }
  .lbl { font-size:.75em; color:var(--secondary-text-color); }
  .big { font-size:1.8em; font-weight:500; line-height:1.1; }
  .big small { font-size:.5em; margin-left:2px; color:var(--secondary-text-color); }
  .ist.hot .big { color:#e8590c; } .ist.warm .big { color:#f9a825; }
  .soll { flex:1; }
  .stepper { display:flex; align-items:center; gap:6px; }
  .mid { font-size:1.35em; font-weight:500; min-width:52px; text-align:center; }
  .mid.pending { color: var(--kp-cool); }
  .round { width:36px; height:36px; border-radius:50%; border:1px solid var(--divider-color); background:var(--card-background-color);
    color:var(--primary-text-color); cursor:pointer; font-size:1.2em; line-height:1; padding:0; }
  .round.sm { width:28px; height:28px; font-size:1em; }
  .round:active { background: var(--kp-cool-bg); }
  .power { width:44px; height:44px; border-radius:50%; border:1px solid var(--divider-color); background:var(--card-background-color);
    color:var(--secondary-text-color); cursor:pointer; display:flex; align-items:center; justify-content:center; padding:0; }
  .power ha-icon { --mdc-icon-size:24px; }
  .power.on { background: var(--kp-cool); color:#fff; border-color: var(--kp-cool); }
  .chips { display:flex; flex-wrap:wrap; gap:6px; align-items:center; }
  .chips.modes { display:grid; grid-template-columns: repeat(auto-fit, minmax(0, 1fr)); gap:5px; }
  .chips.modes .chip { justify-content:center; padding:6px 4px; font-size:.85em; min-width:0; }
  .chips.fans .chip.sm { flex:1; max-width:56px; }
  .fanicon { color: var(--secondary-text-color); }
  .next { font-size:.82em; color:var(--secondary-text-color); display:flex; align-items:center; gap:5px; }
  .next ha-icon { --mdc-icon-size:15px; }
  .foot { margin-top:12px; }
  .txt { border:none; background:none; color:var(--kp-cool); cursor:pointer; font:inherit; padding:6px 8px; border-radius:6px; display:inline-flex; align-items:center; gap:4px; }
  .txt:hover { background: rgba(127,127,127,.12); }
  .txt.primary { background:var(--kp-cool); color:#fff; }
  .txt.danger { color: var(--error-color, #db4437); padding:4px; }
  .srow { display:flex; gap:18px; flex-wrap:wrap; margin: 10px 0; }
  .toggle { display:inline-flex; align-items:center; gap:6px; cursor:pointer; font-size:.95em; }
  .toggle input { width:18px; height:18px; flex:none; margin:0; accent-color: var(--kp-cool); }
  .toggle.big { border:1px solid var(--divider-color); border-radius:10px; padding:8px 10px; align-items:flex-start; }
  .toggle.big span { display:flex; flex-direction:column; }
  .toggle.big small { color: var(--secondary-text-color); font-size:.8em; }
  .week { display:flex; flex-direction:column; gap:5px; }
  .week.dim { opacity:.5; }
  .row { display:flex; align-items:center; gap:8px; border:none; background:none; padding:2px 0; cursor:pointer; font:inherit; color:inherit; text-align:left; width:100%; }
  .row:hover .bar { background: rgba(127,127,127,.2); }
  .row.today .day { color: var(--kp-cool); font-weight:700; }
  .day { width:24px; flex:none; font-size:.9em; }
  .bar { position:relative; height:28px; border-radius:6px; background: rgba(127,127,127,.12); flex:1; overflow:hidden; }
  .win { position:absolute; top:0; bottom:0; background: linear-gradient(180deg, #29b6f6, #0288d1); color:#fff; font-size:.78em; font-weight:600;
    display:flex; align-items:center; justify-content:center; border-radius:4px; }
  .now { position:absolute; top:0; bottom:0; width:2px; background: var(--primary-text-color); opacity:.75; }
  .wtext { width:120px; flex:none; font-size:.78em; color: var(--secondary-text-color); white-space:nowrap; overflow:hidden; text-overflow:ellipsis; }
  .scale { display:flex; gap:8px; font-size:.72em; color:var(--secondary-text-color); }
  .scale > span:first-child { width:24px; }
  .ticks { position:relative; flex:1; height:14px; }
  .ticks span { position:absolute; transform:translateX(-50%); }
  .ticks span:first-child { transform:none; } .ticks span:last-child { transform:translateX(-100%); }
  .overlay { position:absolute; inset:0; background: rgba(0,0,0,.35); display:flex; align-items:center; justify-content:center; z-index:10; }
  .dialog { background: var(--card-background-color); border-radius:12px; padding:16px; width: min(560px, 96%); max-height: 90%; overflow:auto; box-shadow: 0 6px 24px rgba(0,0,0,.3); box-sizing:border-box; }
  .dtitle { font-weight:600; margin-bottom:12px; }
  .wrow { display:flex; align-items:center; gap:6px; margin:8px 0; flex-wrap:wrap; }
  .wv { min-width:42px; text-align:center; font-weight:600; }
  input[type=time], input[type=number], select { font:inherit; padding:6px; border-radius:6px; border:1px solid var(--divider-color);
    background:var(--card-background-color); color:var(--primary-text-color); min-width:0; }
  .field { display:flex; align-items:center; gap:10px; margin:12px 0 4px; }
  .field label { color: var(--secondary-text-color); font-size:.9em; }
  .dact { display:flex; justify-content:space-between; align-items:center; margin-top:12px; }
  .plantop { margin-bottom:10px; }
  .grp { display:flex; flex-direction:column; gap:10px; padding:10px; border-radius:14px; background: rgba(127,127,127,.06); }
  .gh { font-weight:700; font-size:1.02em; display:flex; flex-direction:column; gap:2px; }
  .gh small { font-weight:400; font-size:.8em; color: var(--secondary-text-color); }
  .gsd { font-size:.82em; color: var(--secondary-text-color); margin-top:-4px; }
  .grp .sec { background: var(--card-background-color); }
  .autotab { display:flex; flex-direction:column; gap:14px; }
  .sec { border:1px solid var(--divider-color); border-radius:12px; padding:10px 12px; display:flex; flex-direction:column; gap:8px; }
  .sh { display:flex; align-items:center; gap:8px; font-weight:600; }
  .sh ha-icon { color: var(--kp-cool); }
  .sh .toggle { margin-left:auto; font-weight:500; }
  .sd { font-size:.85em; color: var(--secondary-text-color); line-height:1.4; }
  .sfield small { font-size:.85em; }
  .savebar { position:sticky; bottom:0; z-index:5; display:flex; justify-content:space-between; align-items:center; gap:8px; flex-wrap:wrap;
    padding:8px 12px; border-radius:10px; background: var(--card-background-color); border:2px solid var(--kp-cool); box-shadow:0 2px 10px rgba(0,0,0,.15); }
  .conds { display:grid; grid-template-columns: repeat(auto-fit, minmax(210px, 1fr)); gap:6px; margin-bottom:12px; }
  .autotab .conds { margin-bottom:0; }
  .cond { display:flex; align-items:center; gap:6px; font-size:.85em; padding:6px 8px; border-radius:8px; background: rgba(219,68,55,.1); min-width:0; }
  .cond.yes { background: rgba(67,160,71,.1); }
  .cond ha-icon { --mdc-icon-size:18px; color:#d32f2f; flex:none; }
  .cond.yes ha-icon { color:#43a047; }
  .cond > div { display:flex; flex-direction:column; min-width:0; }
  .cond span { color: var(--secondary-text-color); }
  .conds.compact { display:flex; flex-wrap:wrap; gap:6px; }
  .conds.compact .cond { padding:4px 9px; border-radius:14px; }
  .conds.compact .cond span { margin-left:4px; }
  .conds.compact .cres { flex-basis:100%; }
  .cres { grid-column: 1 / -1; font-size:.8em; color: var(--secondary-text-color); }
  .rule { display:flex; gap:8px; font-size:.88em; line-height:1.45; padding:10px 12px; border-radius:10px; background: var(--kp-cool-bg); }
  .rule ha-icon { color: var(--kp-cool); flex:none; }
  .tgrid { display:grid; grid-template-columns: repeat(auto-fill, minmax(220px, 1fr)); gap:10px; }
  .sgrid { display:grid; grid-template-columns: repeat(auto-fill, minmax(200px, 1fr)); gap:10px; }
  .sfield { display:flex; flex-direction:column; gap:4px; font-size:.85em; color:var(--secondary-text-color); }
  .sfield .inp { display:flex; align-items:center; gap:4px; }
  .sfield .inp input { flex:1; width:100%; }
  .sfield em { font-style:normal; }
  .sfield input, .sfield select { font-size:1.05em; padding:8px; border-radius:8px; }
  .rtable { width:100%; border-collapse:collapse; font-size:.9em; }
  .rtable th { text-align:left; font-weight:500; color:var(--secondary-text-color); font-size:.85em; padding:4px; }
  .rtable td { padding:6px 4px; border-top:1px solid var(--divider-color); }
  .rtable input { width:18px; height:18px; accent-color: var(--kp-cool); }
  @container (max-width: 520px) {
    ha-card { padding: 10px 10px 12px; }
    .title { font-size: 1.15em; }
    .tabs { flex-wrap:nowrap; overflow-x:auto; scrollbar-width:none; }
    .tabs::-webkit-scrollbar { display:none; }
    .tab, .chip { white-space:nowrap; flex:none; }
    .grid { grid-template-columns: 1fr; gap: 8px; }
    .wtext { display:none; }
    .big { font-size:1.55em; }
  }
`;

if (!customElements.get("klimaplan-card")) {
  customElements.define("klimaplan-card", KlimaplanCard);
  window.customCards = window.customCards || [];
  window.customCards.push({ type: "klimaplan-card", name: "Klimaplan", description: "Klimageräte bedienen, Kühlpläne, Hitzeschutz", preview: false });
  console.info(`%c KLIMAPLAN %c ${KP_VERSION} `, "background:#0288d1;color:#fff", "");
}
