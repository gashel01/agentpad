// agentpad web UI -- no external dependency except three.js (vendor/, served locally):
// fetch()/WebSocket + the real 3D model of the v2 case (pad3d.js, exported from Blender).

// A physical key has TWO independent identities, wired separately by
// hand and each confirmed by a direct test on the real pad --
// they do NOT coincide (except the 2 corners 0 and 11, by chance):
//  - keyId: id in the switch matrix, the one the firmware sends
//    on a press ({t:"key", id:keyId}) and the one used in the
//    action bindings (config "keys"). Confirmed by live capture of the 12
//    real press events (websocket), one per key, in physical reading
//    order -> column-major: 0,3,6,9 / 1,4,7,10 / 2,5,8,11.
//  - ledId: id in the SK6812 chain, the one used in the "colors" config
//    and in the LED frame sent to the hardware. Confirmed by lighting
//    individual LEDs + describing their physical position
//    -> plain back row (0,1,2,3), REVERSED middle row
//    (7,6,5,4), plain front row (8,9,10,11).
// Each entry below is therefore ONE physical position (x,y) + its two
// ids: keyId drives the click/selection/press flash/bindings,
// ledId drives only the color. The rest of the code translates between
// the two via KEY_TO_LED whenever it needs to talk to the hardware/LED config.
const COL_X = [-28.575, -9.525, 9.525, 28.575];
const ROW_Y = { back: 4.05, mid: -15, front: -34.05 };
const KEYS = [
  { keyId: 0, ledId: 0, x: COL_X[0], y: ROW_Y.back },
  { keyId: 3, ledId: 1, x: COL_X[1], y: ROW_Y.back },
  { keyId: 6, ledId: 2, x: COL_X[2], y: ROW_Y.back },
  { keyId: 9, ledId: 3, x: COL_X[3], y: ROW_Y.back },
  { keyId: 1, ledId: 7, x: COL_X[0], y: ROW_Y.mid },
  { keyId: 4, ledId: 6, x: COL_X[1], y: ROW_Y.mid },
  { keyId: 7, ledId: 5, x: COL_X[2], y: ROW_Y.mid },
  { keyId: 10, ledId: 4, x: COL_X[3], y: ROW_Y.mid },
  { keyId: 2, ledId: 8, x: COL_X[0], y: ROW_Y.front },
  { keyId: 5, ledId: 9, x: COL_X[1], y: ROW_Y.front },
  { keyId: 8, ledId: 10, x: COL_X[2], y: ROW_Y.front },
  { keyId: 11, ledId: 11, x: COL_X[3], y: ROW_Y.front },
];
const KEY_TO_LED = Object.fromEntries(KEYS.map((k) => [k.keyId, k.ledId]));

// The RING (ids 12-23) is a separate thing: no key, no "press" click,
// just status LEDs around the case. V2 case: halo of
// 3 LEDs per wall (16.67 mm pitch), under the plate. Same direction as
// v1 (right -> front -> left -> back, no crossing) -- to be
// re-verified with the LED test once the halo is wired, same as the keys.
const RING_PITCH = 1000 / 60;
const RING_POS_MM = {
  12: [46.65, 15.67], 13: [46.65, -1], 14: [46.65, -17.67],
  15: [RING_PITCH, -48.65], 16: [0, -48.65], 17: [-RING_PITCH, -48.65],
  18: [-46.65, -17.67], 19: [-46.65, -1], 20: [-46.65, 15.67],
  21: [-RING_PITCH, 46.65], 22: [0, 46.65], 23: [RING_PITCH, 46.65],
};
// agent rows in slot order (1-4 back, 5-8 middle): this is the order in which
// the server populates the 8 top keys with detected sessions
const AGENT_KEY_ORDER = [0, 3, 6, 9, 1, 4, 7, 10];
const STATUS_COLOR = { running: "#12b9ff", waiting: "#ff8a1f", attention: "#ffc21a", finished: "#2fd36b", done: "#2fd36b", error: "#ff3b30", offline: "#5b5f66" };
const STATUS_LABEL = { running: "Active", waiting: "Idle", attention: "Needs you", finished: "Done", done: "Finished", error: "Error", offline: "Offline" };

// ---- calibrated physical layout (server: /api/state "layout") ----------
// keys: position "r{row}c{col}" -> key id; leds: LED id -> position ("r..c.." or "h0".."h11")
const KEY_POSITIONS = ["r0c0", "r0c1", "r0c2", "r0c3", "r1c0", "r1c1", "r1c2", "r1c3", "r2c0", "r2c1", "r2c2", "r2c3"];
const HALO_POSITIONS = Array.from({ length: 12 }, (_, i) => "h" + i);
let layout = null;
const ledAtPos = {};
function applyLayout(lay) {
  if (!lay) return;
  layout = lay;
  for (const k in ledAtPos) delete ledAtPos[k];
  for (const led in lay.leds) ledAtPos[lay.leds[led]] = Number(led);
  for (const k in KEY_TO_LED) delete KEY_TO_LED[k];
  for (const pos in lay.keys) if (pos in ledAtPos) KEY_TO_LED[lay.keys[pos]] = ledAtPos[pos];
  AGENT_KEY_ORDER.length = 0;
  for (const pos of KEY_POSITIONS.slice(0, 8)) if (pos in lay.keys) AGENT_KEY_ORDER.push(lay.keys[pos]);
  window.Pad3D.setLayout(lay);
  buildRingLeds();
}

// Halo LEDs: one dot per position (h0..h11, clockwise seen from above), to set them one by one
function buildRingLeds() {
  const box = document.getElementById("ringLeds");
  if (!box) return;                       // dots removed from the scene (the halo is chosen on the 3D model)
  box.innerHTML = "";
  const ids = layout ? HALO_POSITIONS.map((p) => ledAtPos[p]).filter((v) => v != null).map(String) : Object.keys(RING_POS_MM);
  for (const id of ids) {
    const b = document.createElement("button");
    b.type = "button";
    b.className = "ring-led";
    b.dataset.kind = "led";
    b.dataset.id = id;
    b.setAttribute("aria-label", "Halo LED " + id);
    b.addEventListener("click", () => selectElement("led", id));
    box.appendChild(b);
  }
}
buildRingLeds();

window.Pad3D.init({
  canvas: document.getElementById("pad3d"),
  modelUrl: "models/agentpad.glb",
  onHover: (h) => showLegend(h),
  onPick: (kind, id, pos) => {
    if (calib.onPick && calib.onPick(kind, id, pos)) return;     // LED calibration in progress
    if (kind === "halo") {
      // first the halo overview (all points), then a click on a point picks the LED
      if (currentView !== "halo" || !pos) selectElement("global", "ring");
      else if (ledAtPos[pos] != null) selectElement("led", String(ledAtPos[pos]));
      return;
    }
    selectElement(kind, id);
  },
  onReady: () => { document.getElementById("padMsg").hidden = true; },
  onError: (e) => { document.getElementById("padMsg").textContent = "3D model unavailable: " + (e && e.message ? e.message : e); },
});

// ---- state -----------------------------------------------------------------
let state = { connected: false, profile: "claude_code", bindings: { keys: {}, encoders: {}, colors: {} }, animation: { name: "none", params: {}, zone: "all" }, num_leds: 24 };
let selected = null; // {kind, id}
let ws = null;
let agentSessions = []; // local cache, updated by /api/agents and by the "agents" ws
let macroSteps = []; // steps of the macro currently being edited in the panel

const $ = (id) => document.getElementById(id);
function toast(msg) {
  const t = $("toast");
  t.textContent = msg;
  t.classList.add("show");
  clearTimeout(toast._t);
  toast._t = setTimeout(() => t.classList.remove("show"), 2200);
}

const NOT_DIRTY = ["/profile", "/live/focus", "/live/answer", "/states/preview", "/preview_led", "/preview_led_all", "/calibration/mode", "/calibration/solo", "/connect", "/disconnect", "/save", "/widget/open", "/widget/visible", "/agents/launch", "/agents/focus", "/agents/kill", "/hooks"];
async function api(path, body) {
  if (body && !NOT_DIRTY.includes(path)) markDirty(true);
  const opts = body ? { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) } : {};
  const res = await fetch("/api" + path, opts);
  if (!res.ok) throw new Error(await res.text());
  return res.json();
}

async function refreshPorts() {
  const ports = await api("/ports");
  const sel = $("portSelect");
  sel.innerHTML = "";
  if (ports.length === 0) {
    const o = document.createElement("option");
    o.textContent = "No port detected";
    sel.appendChild(o);
    return;
  }
  for (const p of ports) {
    const o = document.createElement("option");
    o.value = p.device;
    o.textContent = `${p.device} — ${p.description}`;
    sel.appendChild(o);
  }
}

function renderConnState() {
  $("topDot").className = "conn-dot" + (state.connected ? " on" : "");
  $("topText").textContent = state.connected ? `Pad connected · ${state.port}` : "Pad not connected";
  const dot = $("connDot");
  dot.className = "conn-dot" + (state.connected ? " on" : "");
  $("connTitle").textContent = state.connected ? state.port : "Not connected";
  $("connSub").textContent = state.device_info && state.device_info.fw
    ? `${state.device_info.fw} v${state.device_info.ver} · ${state.device_info.keys} keys, ${state.device_info.leds} LEDs`
    : (state.connected ? "Pad connected" : "Plug the pad in via USB");
  $("connectBtn").textContent = state.connected ? "Disconnect" : "Connect";
}

function colorToHex(c) {
  const [r, g, b] = c;
  return "#" + [r, g, b].map((v) => v.toString(16).padStart(2, "0")).join("");
}
function hexToRgb(hex) {
  const n = parseInt(hex.slice(1), 16);
  return [(n >> 16) & 255, (n >> 8) & 255, n & 255];
}
// SK6812 RGBW LEDs: 4 channels. What we see = RGB + white W; what we send = the
// neutral part of the color on the dedicated white LED (W = min(R,G,B)), the rest in RGB -- cleaner
// whites and less current than a white made of red+green+blue.
function ledVisible(c) {
  const w = c[3] || 0;
  return [0, 1, 2].map((i) => Math.min(255, (c[i] || 0) + w));
}
function rgbToRgbw([r, g, b]) {
  const w = Math.min(r, g, b);
  return [r - w, g - w, b - w, w];
}

function colorFor(id) {
  const colors = state.bindings.colors || {};
  if (colors[id]) return colors[id];
  if ((state.key_color_defaults || {})[id]) return state.key_color_defaults[id];
  if (Number(id) >= 12) return colors.ring || [20, 20, 20, 0];   // the halo never reuses the keys' color
  return colors.default || [20, 20, 20, 0];
}

function rgbCss(c) {
  const [r, g, b] = ledVisible(c);                 // the white channel counts, otherwise a pure W white would come out black
  return `rgb(${r},${g},${b})`;
}

function renderDeviceState() {
  // halo dots: config color (the live frame refreshes them afterward)
  document.querySelectorAll(".ring-led").forEach((dot) => {
    dot.style.background = rgbCss(colorFor(dot.dataset.id));
  });
  // global swatches in the sidebar
  const colors = state.bindings.colors || {};
  const defC = colors.default || [20, 20, 20, 0];
  const ringC = colors.ring || defC;
  document.querySelector("#globalDefault .global-color-swatch").style.background = rgbCss(defC);
  document.querySelector("#globalRing .global-color-swatch").style.background = rgbCss(ringC);
}

// bottom row: each key has a role, we call it by name
const FRONT_KEY_NAMES = { r2c0: "Bolt", r2c1: "Confirm", r2c2: "Cancel", r2c3: "Mic" };
function keyName(id) {
  const pos = layout ? Object.keys(layout.keys).find((p) => String(layout.keys[p]) === String(id)) : null;
  if (pos && FRONT_KEY_NAMES[pos]) return FRONT_KEY_NAMES[pos];
  const m = pos && /^r([01])c(\d)$/.exec(pos);            // agent keys: the number printed on the keycap
  if (m) return `Agent ${Number(m[1]) * 4 + Number(m[2]) + 1}`;
  return `Key ${id}`;
}

// ---- agent mode: bottom row actions, as roles ----------------------------
// In agent mode, a key isn't programmed like a macro: it's given a role for
// Claude Code. Each role = a regular binding (combo/text), stored as before.
const ROLES = [
  { id: "enter", name: "Confirm", sub: "Enter", b: { action: "combo", value: "enter" } },
  { id: "esc", name: "Cancel", sub: "Esc", b: { action: "combo", value: "esc" } },
  { id: "interrupt", name: "Interrupt", sub: "Ctrl+C", b: { action: "combo", value: "ctrl+c" } },
  { id: "mode", name: "Switch mode", sub: "Shift+Tab", b: { action: "combo", value: "shift+tab" } },
  { id: "dictate", name: "Dictation", sub: "hold · Claude Code dictation", b: { action: "mic", value: "" } },
  { id: "command", name: "Command", sub: "types text, e.g. /review", b: { action: "text" } },
  { id: "none", name: "Nothing", sub: "key disabled", b: { action: "none", value: "" } },
];
function roleOf(b) {
  if (!b || !b.action) return null;
  if (b.action === "combo" && ["cmd+h", "win+h"].includes(String(b.value || "").toLowerCase())) return "dictate";   // old value
  if (b.action === "text") return "command";
  return (ROLES.find((r) => r.b.action === b.action && String(r.b.value || "") === String(b.value || "")) || {}).id || "custom";
}
function describeBinding(b) {
  if (!b || !b.action) return "none";
  const r = ROLES.find((x) => x.id === roleOf(b));
  if (r && r.id === "command") return `Command “${String(b.value || "").trim()}”`;
  if (r) return `${r.name} (${r.sub})`;
  return b.action === "macro" ? "macro" : `${b.action} ${b.value || ""}`;
}
let roleDraft = null;
function setRole(id) {
  roleDraft = id;
  document.querySelectorAll("#roleGrid .role-btn").forEach((x) => x.classList.toggle("active", x.dataset.role === id));
  $("roleCommand").hidden = id !== "command";
}
function renderRoleSection(keyId) {
  const own = (state.bindings.keys || {})[keyId], def = (state.key_action_defaults || {})[keyId];
  const cur = own || def;
  const grid = $("roleGrid");
  grid.innerHTML = "";
  const list = roleOf(cur) === "custom" ? [...ROLES, { id: "custom", name: "Custom", sub: describeBinding(cur) }] : ROLES;
  for (const r of list) {
    const btn = document.createElement("button");
    btn.type = "button"; btn.className = "role-btn"; btn.dataset.role = r.id;
    btn.innerHTML = `<span class="role-name">${escapeHtml(r.name)}</span><span class="role-sub">${escapeHtml(r.sub)}</span>`;
    if (r.id !== "custom") btn.addEventListener("click", () => setRole(r.id));
    grid.appendChild(btn);
  }
  $("roleCommand").value = cur && cur.action === "text" ? String(cur.value || "").replace(/\n$/, "") : "";
  setRole(roleOf(cur) || "none");
  $("roleDefaultRow").hidden = !def;
  $("roleDefaultText").textContent = def ? describeBinding(def) : "";
  $("roleReset").disabled = !own;
}
function roleBinding() {
  if (roleDraft === "custom") return null;                      // inchange
  if (roleDraft === "command") {
    const v = $("roleCommand").value.trim();
    return v ? { action: "text", value: v + "\n" } : { action: "none", value: "" };
  }
  return (ROLES.find((r) => r.id === roleDraft) || ROLES[ROLES.length - 1]).b;
}
$("roleReset").addEventListener("click", async () => {
  if (!selected || selected.kind !== "key") return;
  await api("/binding/key", { id: selected.id, action: null, value: null });
  await loadState();
  selectElement("key", selected.id);
  toast("Default action restored");
});

// ---- knob in agent mode: rotation, click, double-click, each with a choice and a default ----
const KNOB_ROT = [
  { id: "auto", name: "Auto", sub: "Claude choices, otherwise volume", cw: { action: "auto", value: "" }, ccw: { action: "auto", value: "" } },
  { id: "volume", name: "Volume", sub: "+ / −", cw: { action: "combo", value: "volume_up" }, ccw: { action: "combo", value: "volume_down" } },
  { id: "arrows", name: "Navigate", sub: "↑ / ↓", cw: { action: "combo", value: "down" }, ccw: { action: "combo", value: "up" } },
  { id: "pages", name: "Scroll", sub: "pages", cw: { action: "combo", value: "page_down" }, ccw: { action: "combo", value: "page_up" } },
  { id: "none", name: "Nothing", sub: "disabled", cw: { action: "none", value: "" }, ccw: { action: "none", value: "" } },
];
const KNOB_BTN = [
  { id: "none", name: "Nothing", sub: "disabled", b: { action: "none", value: "" } },
  { id: "sessions", name: "Sessions", sub: "show / hide", b: { action: "sessions", value: "" } },
  { id: "mute", name: "Mute", sub: "sound off", b: { action: "combo", value: "volume_mute" } },
  { id: "play", name: "Play", sub: "play / pause", b: { action: "combo", value: "play_pause" } },
  { id: "enter", name: "Confirm", sub: "Enter", b: { action: "combo", value: "enter" } },
  { id: "esc", name: "Cancel", sub: "Esc", b: { action: "combo", value: "esc" } },
  { id: "dictate", name: "Dictation", sub: "Win+H", b: { action: "combo", value: "cmd+h" } },
];
const sameB = (a, b) => !!a && !!b && a.action === b.action && String(a.value || "") === String(b.value || "");
const knob = { rot: null, press: null, double: null };
function knobCurrent() {
  const own = (state.bindings.encoders || {})["0"] || {}, def = state.encoder_defaults || {};
  const pick = (n) => own[n] || def[n];
  return { own, def, cw: pick("cw"), ccw: pick("ccw"), press: pick("press"), double: pick("double") };
}
function knobGrid(box, list, cur, onPick) {
  box.innerHTML = "";
  for (const r of list) {
    const b = document.createElement("button");
    b.type = "button"; b.className = "role-btn" + (r.id === cur ? " active" : ""); b.dataset.role = r.id;
    b.innerHTML = `<span class="role-name">${escapeHtml(r.name)}</span><span class="role-sub">${escapeHtml(r.sub)}</span>`;
    b.addEventListener("click", () => { onPick(r.id); box.querySelectorAll(".role-btn").forEach((x) => x.classList.toggle("active", x === b)); });
    box.appendChild(b);
  }
}
function renderKnobSection() {
  const c = knobCurrent();
  const rotOf = (cw, ccw) => (KNOB_ROT.find((r) => sameB(r.cw, cw) && sameB(r.ccw, ccw)) || {}).id || null;
  const btnOf = (b) => (KNOB_BTN.find((r) => sameB(r.b, b)) || {}).id || null;
  knob.rot = rotOf(c.cw, c.ccw); knob.press = btnOf(c.press); knob.double = btnOf(c.double);
  knobGrid($("knobRot"), KNOB_ROT, knob.rot, (id) => (knob.rot = id));
  knobGrid($("knobPress"), KNOB_BTN, knob.press, (id) => (knob.press = id));
  knobGrid($("knobDouble"), KNOB_BTN, knob.double, (id) => (knob.double = id));
  const nm = (list, id) => { const r = list.find((x) => x.id === id); return r ? `${r.name} (${r.sub})` : "—"; };
  $("knobRotDef").textContent = nm(KNOB_ROT, rotOf(c.def.cw, c.def.ccw));
  $("knobPressDef").textContent = nm(KNOB_BTN, btnOf(c.def.press));
  $("knobDoubleDef").textContent = nm(KNOB_BTN, btnOf(c.def.double));
  document.querySelector('[data-knob-reset="rot"]').disabled = !(c.own.cw || c.own.ccw);
  document.querySelector('[data-knob-reset="press"]').disabled = !c.own.press;
  document.querySelector('[data-knob-reset="double"]').disabled = !c.own.double;
}
function knobBody() {
  // equal to the default -> remove the own setting ({}), to keep the config clean
  const c = knobCurrent();
  const part = (name, b) => (!b ? null : sameB(b, c.def[name]) ? (c.own[name] ? {} : null) : b);
  const rot = KNOB_ROT.find((r) => r.id === knob.rot), pr = KNOB_BTN.find((r) => r.id === knob.press), db = KNOB_BTN.find((r) => r.id === knob.double);
  return { id: "0", cw: part("cw", rot && rot.cw), ccw: part("ccw", rot && rot.ccw), press: part("press", pr && pr.b), double: part("double", db && db.b) };
}
document.querySelectorAll("[data-knob-reset]").forEach((btn) => btn.addEventListener("click", async () => {
  const w = btn.dataset.knobReset;
  await api("/binding/encoder", w === "rot" ? { id: "0", cw: {}, ccw: {} } : { id: "0", [w]: {} });
  await loadState();
  renderKnobSection();
  toast("Default setting restored");
}));

function selectElement(kind, id) {
  selected = { kind, id };
  document.querySelectorAll(".ring-led").forEach((n) => n.classList.toggle("selected", kind === "led" && n.dataset.id === String(id)));
  window.Pad3D.select(kind, id);
  if (!calib.open) setView(...viewFor(kind, id));
  renderStageTitle();
  renderRail();
  renderPanelHead();

  $("panelEmpty").hidden = true;
  $("panelContent").hidden = false;

  const bindingSection = $("bindingSection");
  const encSection = $("encSection");
  const colorSection = $("colorSection");

  if (kind === "key" || kind === "joy") {
    $("panelTitle").textContent = kind === "joy" ? "Joystick (click)" : keyName(id);
    $("panelSub").textContent = kind === "joy" ? "joystick button · id 110" : `key id ${id} · LED id ${KEY_TO_LED[Number(id)]}`;
    bindingSection.hidden = false;
    encSection.hidden = true;
    colorSection.hidden = Number(id) >= 100 || isAgentKey(id); // no LED (encoder/joystick); agent key = its states
    const b = (state.bindings.keys || {})[id] || {};
    $("actionType").value = b.action || "";
    if (b.action === "macro") {
      macroSteps = Array.isArray(b.value) ? JSON.parse(JSON.stringify(b.value)) : [];
    } else {
      macroSteps = [];
      $("actionValue").value = b.action === "agent" ? "" : (b.value || "");
    }
    populateAgentActionSlot();
    if (b.action === "agent") $("agentActionSlot").value = String(b.value || "");
    updateActionHint();
  } else if (kind === "enc") {
    $("panelTitle").textContent = "Encoder";
    $("panelSub").textContent = "rotation + click (id 100)";
    bindingSection.hidden = true;
    encSection.hidden = false;
    colorSection.hidden = true;
    const e = (state.bindings.encoders || {})["0"] || {};
    if (state.profile === "claude_code") { encSection.hidden = true; $("panelSub").textContent = "rotation, click and double-click"; }
    $("encCw").value = (e.cw || {}).value || "";
    $("encCcw").value = (e.ccw || {}).value || "";
    $("encPress").value = (e.press || {}).value || "";
  } else if (kind === "led") {
    $("panelTitle").textContent = `Ring LED ${id}`;
    $("panelSub").textContent = `led id ${id}`;
    bindingSection.hidden = true;
    encSection.hidden = true;
    colorSection.hidden = false;
  } else if (kind === "state") {
    const [g, n] = splitStateId(id), meta = stateMeta(n);
    $("panelTitle").textContent = meta[1];
    $("panelSub").textContent = (isKeyGroup(g) ? `key ${g.slice(3)} · ` : g === "halo" ? "halo · " : "agent keys · ") + meta[2];
    bindingSection.hidden = true;
    encSection.hidden = true;
    colorSection.hidden = false;
  } else if (kind === "global") {
    $("panelTitle").textContent = id === "default" ? "Default color (keys)" : "Ring color";
    $("panelSub").textContent = id === "default" ? "applies to every key without its own color" : "applies to all LEDs id >= 12 without their own color";
    bindingSection.hidden = true;
    encSection.hidden = true;
    colorSection.hidden = false;
  }

  // agent mode: no macro editor. Agent key and clicks = fixed role; bottom row = roles
  const cc = state.profile === "claude_code";
  const agentKey = kind === "key" && isAgentKey(id);
  const roleKey = cc && kind === "key" && !agentKey && Number(id) < 100;
  $("reservedSection").hidden = kind !== "joy";
  if (kind === "joy") $("reservedText").textContent = "Click: opens the radial menu, where the joystick points. In Agents mode, up/down scrolls the foreground session.";
  if (agentKey || roleKey || kind === "joy") bindingSection.hidden = true;
  $("roleSection").hidden = !roleKey;
  $("keyBrightRow").hidden = !roleKey;
  if (roleKey) {
    const v = ((state.zone_brightness || {}).keys || {})[id] ?? 100;
    $("keyBright").value = v; $("keyBrightVal").textContent = v + " %";
  }
  $("knobSection").hidden = !(cc && kind === "enc");
  if (!$("knobSection").hidden) renderKnobSection();
  if (roleKey) renderRoleSection(id);
  $("stateSection").hidden = kind !== "state";
  $("keyStateSection").hidden = !(kind === "key" && isAgentKey(id));
  if (!$("keyStateSection").hidden) {
    renderKeyStateList(id);
    const a = agentForKey(id);
    $("keyFocusBtn").hidden = !(a && a.alive);
    $("keyFocusBtn").onclick = () => a && api("/agents/focus", { slot: a.slot });
  }
  if (kind === "state") loadStateEditor(id);
  renderStateList();

  document.querySelectorAll(".global-color").forEach((n) => n.classList.remove("selected"));
  if (kind === "global") {
    document.getElementById(id === "default" ? "globalDefault" : "globalRing").classList.add("selected");
  }

  if (!colorSection.hidden && kind !== "state") {
    const lookupId = kind === "key" ? KEY_TO_LED[Number(id)] : id;
    setColorInputs(colorFor(lookupId));
  }
  renderColorDefault();
}

// "Default / Restore" row: key or LED that has its own color, when a default exists
function colorTarget() {
  if (!selected) return null;
  if (selected.kind === "key") return String(KEY_TO_LED[Number(selected.id)]);
  if (selected.kind === "led") return String(selected.id);
  return null;
}
function renderColorDefault() {
  const id = colorTarget(), row = $("colorDefaultRow");
  if (!id || $("colorSection").hidden) { row.hidden = true; return; }
  const own = (state.bindings.colors || {})[id];
  const def = (state.key_color_defaults || {})[id] || (Number(id) >= 12 ? (state.bindings.colors || {}).ring : (state.bindings.colors || {}).default) || [20, 20, 20, 0];
  row.hidden = false;
  $("colorDefaultSwatch").style.setProperty("--sw", def.slice(0, 4).some((v) => v) ? rgbCss(def) : "#1b1d20");
  $("colorDefaultText").textContent = (state.key_color_defaults || {})[id] ? "agent mode" : Number(id) >= 12 ? "halo color" : "base color";
  $("colorReset").disabled = !own;
}
$("colorReset").addEventListener("click", async () => {
  const id = colorTarget();
  if (!id) return;
  await api("/color/reset", { id });
  await loadState();
  selectElement(selected.kind, selected.id);
  toast("Default color restored");
});

function setColorInputs(c) {
  $("colorPicker").value = colorToHex(ledVisible(c));
  $("chR").value = c[0]; $("chRVal").textContent = c[0];
  $("chG").value = c[1]; $("chGVal").textContent = c[1];
  $("chB").value = c[2]; $("chBVal").textContent = c[2];
  $("chW").value = c[3] || 0; $("chWVal").textContent = c[3] || 0;
}

function currentColorFromInputs() {
  return [Number($("chR").value), Number($("chG").value), Number($("chB").value), Number($("chW").value)];
}

// ---- color: sync hex <-> sliders, immediate local preview + debounced
// hardware preview (to avoid spamming the serial port on every pixel of
// slider movement -- see the write timeout bug already fixed
// in serial_link.py, better to stay cautious on send frequency)
let previewDebounceTimer = null;
function livePreviewColor() {
  if (!selected) return;
  const c = currentColorFromInputs();
  // instant local preview on the 3D pad, even without hardware connected: overrides the
  // frame for 1.5 s (otherwise the server's 10/s broadcast would overwrite it right away)
  const until = performance.now() + 1500;
  if (selected.kind === "state") { previewStateDraft(); return; }
  if (selected.kind === "led") localOverride[selected.id] = { c, until };
  else if (selected.kind === "key") localOverride[KEY_TO_LED[Number(selected.id)]] = { c, until };
  else if (selected.kind === "global") {
    const sw = document.querySelector(`#${selected.id === "default" ? "globalDefault" : "globalRing"} .global-color-swatch`);
    if (sw) sw.style.background = rgbCss(c);
    const ids = selected.id === "default" ? KEYS.map((k) => k.ledId) : Object.keys(RING_POS_MM).map(Number);
    for (const i of ids) localOverride[i] = { c, until };
  }
  if (lastFrame) applyLiveFrame(lastFrame);
  // hardware preview, debounce ~70ms -- previewId must be the REAL id of
  // the selected element (fixed bug: before, anything that wasn't
  // "led" fell back to the hardcoded id0, so the hardware preview
  // never lit the right key as soon as a key other than 0 was
  // selected).
  clearTimeout(previewDebounceTimer);
  previewDebounceTimer = setTimeout(() => {
    if (!state.connected) return;
    if (selected.kind === "led") {
      api("/preview_led", { id: Number(selected.id), color: c }).catch(() => {});
    } else if (selected.kind === "key") {
      // the hardware speaks in LED id (chain), not key id (matrix)
      api("/preview_led", { id: KEY_TO_LED[Number(selected.id)], color: c }).catch(() => {});
    } else if (selected.kind === "global") {
      api("/preview_led_all", { color: c, zone: selected.id === "ring" ? "ring" : "keys" }).catch(() => {});
    }
  }, 70);
}

$("colorPicker").addEventListener("input", () => {
  const [r, g, b, w] = rgbToRgbw(hexToRgb($("colorPicker").value));
  $("chR").value = r; $("chRVal").textContent = r;
  $("chG").value = g; $("chGVal").textContent = g;
  $("chB").value = b; $("chBVal").textContent = b;
  $("chW").value = w; $("chWVal").textContent = w;
  livePreviewColor();
});
["chR", "chG", "chB", "chW"].forEach((id) => {
  $(id).addEventListener("input", () => {
    $(id + "Val").textContent = $(id).value;
    $("colorPicker").value = colorToHex(ledVisible(currentColorFromInputs()));
    livePreviewColor();
  });
});

document.querySelectorAll(".global-color").forEach((n) => {
  n.addEventListener("click", () => selectElement("global", n.dataset.which));
});

$("testAllBtn").addEventListener("click", async () => {
  try {
    await api("/preview_led_all", { color: [255, 255, 255, 120] });
    toast("Test sent");
  } catch (err) {
    toast("Not connected?");
  }
});

function updateActionHint() {
  const type = $("actionType").value;
  const label = $("valueLabel");
  const hint = $("actionHint");
  $("simpleValueSection").hidden = !(type === "combo" || type === "text" || type === "");
  $("macroSection").hidden = type !== "macro";
  $("agentActionSection").hidden = type !== "agent";
  if (type === "combo") {
    label.textContent = "Combo (separator +)";
    hint.textContent = "e.g. ctrl+shift+p, enter, esc, up";
  } else if (type === "text") {
    label.textContent = "Text to type";
    hint.textContent = "e.g. git status\\n (the \\n submits the command)";
  } else {
    label.textContent = "Value";
    hint.textContent = "";
  }
  if (type === "macro") renderMacroSteps();
  syncActionUi();
}
$("actionType").addEventListener("change", updateActionHint);

// ---- macros -----------------------------------------------------------
const MACRO_STEP_LABELS = { key: "Key to press (e.g. ctrl+c)", text: "Text to type", wait: "Delay (ms)", exec: "Command to run" };

function renderMacroSteps() {
  const box = $("macroSteps");
  box.innerHTML = "";
  macroSteps.forEach((step, i) => {
    const row = document.createElement("div");
    row.className = "macro-step";
    const typeSel = document.createElement("select");
    typeSel.className = "select";
    for (const t of ["key", "text", "wait", "exec"]) {
      const o = document.createElement("option");
      o.value = t;
      o.textContent = { key: "Key", text: "Text", wait: "Wait", exec: "Command" }[t];
      if (t === step.type) o.selected = true;
      typeSel.appendChild(o);
    }
    const valInput = document.createElement("input");
    valInput.className = "input";
    valInput.placeholder = MACRO_STEP_LABELS[step.type] || "";
    valInput.value = step.value ?? "";
    valInput.type = step.type === "wait" ? "number" : "text";
    const delayInput = document.createElement("input");
    delayInput.className = "input macro-delay";
    delayInput.type = "number";
    delayInput.title = "delay after this step (ms)";
    delayInput.placeholder = "+ms";
    delayInput.value = step.delay_after_ms || "";
    const rm = document.createElement("button");
    rm.type = "button";
    rm.textContent = "✕";
    rm.title = "remove this step";

    typeSel.addEventListener("change", () => { step.type = typeSel.value; valInput.placeholder = MACRO_STEP_LABELS[step.type] || ""; valInput.type = step.type === "wait" ? "number" : "text"; });
    valInput.addEventListener("input", () => { step.value = valInput.type === "number" ? Number(valInput.value) : valInput.value; });
    delayInput.addEventListener("input", () => { step.delay_after_ms = delayInput.value ? Number(delayInput.value) : undefined; });
    rm.addEventListener("click", () => { macroSteps.splice(i, 1); renderMacroSteps(); });

    row.appendChild(typeSel);
    row.appendChild(valInput);
    row.appendChild(delayInput);
    row.appendChild(rm);
    box.appendChild(row);
  });
}
$("macroAddStep").addEventListener("click", () => {
  macroSteps.push({ type: "key", value: "" });
  renderMacroSteps();
});

// ---- agents -------------------------------------------------------------
function populateAgentActionSlot() {
  const sel = $("agentActionSlot");
  const prev = sel.value;
  sel.innerHTML = "";
  if (!agentSessions.length) {
    const o = document.createElement("option");
    o.value = "";
    o.textContent = "— no session configured —";
    sel.appendChild(o);
    return;
  }
  for (const s of agentSessions) {
    const o = document.createElement("option");
    o.value = s.slot;
    o.textContent = s.name || `slot ${s.slot}`;
    sel.appendChild(o);
  }
  if (prev) sel.value = prev;
}

// Sessions: only the ones detected automatically (scan_system server-side). No
// manually declared session, and none linked to a key: the project choice is made via the radial menu.
function detectedSessions() {
  return agentSessions.filter((s) => s.auto).sort((a, b) => (a.pid || 0) - (b.pid || 0));
}

function renderAgentsList() {
  const box = $("agentsList");
  if (!box) return;                          // sessions live in the scene rail
  box.innerHTML = "";
  const list = detectedSessions();
  if (!list.length) {
    const p = document.createElement("div");
    p.className = "hint";
    p.textContent = "No Claude Code session detected yet.";
    box.appendChild(p);
    return;
  }
  for (const s of list) {
    const row = document.createElement("div");
    row.className = "agent-row";
    row.innerHTML = `
      <div class="agent-dot ${escapeHtml(s.status)}"></div>
      <div class="agent-info">
        <div class="agent-name">${escapeHtml(s.name || ("session " + s.slot))}</div>
        <div class="agent-sub">${escapeHtml(STATUS_LABEL[s.status] || s.status)}${s.cwd ? " · " + escapeHtml(s.cwd) : ""}</div>
      </div>
      <div class="agent-actions">
        <button data-act="focus" title="Bring to front" aria-label="Bring to front" ${s.alive ? "" : "disabled"}>◱</button>
      </div>`;
    row.querySelector('[data-act="focus"]').addEventListener("click", (e) => { e.stopPropagation(); api("/agents/focus", { slot: s.slot }); });
    box.appendChild(row);
  }
}

function escapeHtml(s) {
  return String(s).replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
}

async function refreshAgents() {
  agentSessions = await api("/agents");
  renderAgentsList();
  populateAgentActionSlot();
  renderRail();
}

$("widgetOpenBtn").addEventListener("click", async () => {
  try {
    const res = await api("/widget/open");
    toast(res.ok ? "Widget opened" : res.error || "Error");
  } catch (err) {
    toast("Error: " + err.message);
  }
});

// ---- animation ----------------------------------------------------------
let animDebounceTimer = null;
function currentAnimState() {
  return {
    name: $("animName").value,
    zone: document.querySelector("#animZone .profile-btn.active")?.dataset.zone || "all",
    params: { color: rgbToRgbw(hexToRgb($("animColor").value)), speed: Number($("animSpeed").value) },
  };
}
function renderAnimControls() {
  const a = state.animation || { name: "none", params: {}, zone: "all" };
  $("animName").value = a.name || "none";
  const c = (a.params || {}).color || [91, 140, 255];
  $("animColor").value = colorToHex(ledVisible(c));
  $("animSpeed").value = (a.params || {}).speed || 1;
  document.querySelectorAll("#animZone .profile-btn").forEach((b) => b.classList.toggle("active", b.dataset.zone === (a.zone || "all")));
  $("animParams").style.display = a.name === "rainbow" ? "none" : "";
  syncAnimPicker();
}
function pushAnimation() {
  clearTimeout(animDebounceTimer);
  animDebounceTimer = setTimeout(() => {
    api("/animation", currentAnimState()).catch(() => {});
  }, 120);
}
$("animName").addEventListener("change", () => {
  $("animParams").style.display = $("animName").value === "rainbow" ? "none" : "";
  pushAnimation();
});
$("animColor").addEventListener("input", pushAnimation);
$("animSpeed").addEventListener("input", pushAnimation);
document.querySelectorAll("#animZone .profile-btn").forEach((btn) => {
  btn.addEventListener("click", () => {
    document.querySelectorAll("#animZone .profile-btn").forEach((b) => b.classList.remove("active"));
    btn.classList.add("active");
    pushAnimation();
  });
});

// ---- camera framings -------------------------------------------------
// The camera follows the context: picking a key frames the key, a halo LED frames that LED,
// the joystick and the knob frame their object. The arrow (or Escape) returns to the global view.
let currentView = "global", currentFocus = null;
function setView(v, focus) {
  window.Pad3D.setView(v, focus);
  currentView = v; currentFocus = focus || null;
  $("viewBack").hidden = v === "global";
}
function viewFor(kind, id) {
  if (kind === "key") return ["key"];
  if (kind === "joy") return ["joy"];
  if (kind === "enc") return ["knob"];
  if (kind === "led") {
    const pos = (layout && layout.leds[String(id)]) || null;
    if (pos && pos[0] === "h") return ["halo", pos];
    if (pos && layout.keys[pos] != null) { window.Pad3D.select("key", String(layout.keys[pos])); return ["key"]; }
    return ["halo"];
  }
  if (kind === "global") return id === "ring" ? ["halo"] : ["global"];
  if (kind === "state") {
    const [g] = splitStateId(id);
    if (isKeyGroup(g)) { window.Pad3D.select("key", g.slice(3)); return ["key"]; }
    return g === "halo" ? ["halo"] : ["global"];
  }
  return ["global"];
}
function clearSelection() {
  selected = null;
  document.querySelectorAll(".ring-led").forEach((n) => n.classList.remove("selected"));
  document.querySelectorAll(".global-color").forEach((n) => n.classList.remove("selected"));
  window.Pad3D.select(null, null);
  renderDeviceState();                       // an unapplied preview doesn't stay on the dots
  renderStateList();
  renderStageTitle(); renderRail();
  if (!calib.open) { $("panelContent").hidden = true; $("panelEmpty").hidden = false; }
  setView("global");
}
// the arrow goes up one level: halo LED -> halo view -> global view
function goBack() {
  const g = selected && selected.kind === "state" ? splitStateId(selected.id)[0] : null;
  if (g && isKeyGroup(g)) selectElement("key", g.slice(3));
  else if (currentView === "halo" && currentFocus) selectElement("global", "ring");
  else clearSelection();
}
$("viewBack").addEventListener("click", goBack);
document.addEventListener("keydown", (e) => {
  if (e.key !== "Escape" || calib.open || !selected) return;
  if (e.target.closest && e.target.closest("input, textarea, select")) return;
  goBack();
});

// ---- scene title + agent rail ------------------------------------------
function agentForKey(keyId) {
  // explicitly linked session (binding "agent"), otherwise detected session occupying this slot
  const b = (state.bindings.keys || {})[keyId];
  if (b && b.action === "agent") return agentSessions.find((s) => String(s.slot) === String(b.value)) || null;
  const autos = agentSessions.filter((s) => s.auto).sort((a, b2) => (a.pid || 0) - (b2.pid || 0));
  // key assigned by the server (it stays the same as long as the session lives)
  if (autos.some((s) => "agent_key" in s)) return state.profile === "claude_code" ? autos.find((s) => s.agent_key === Number(keyId)) || null : null;
  const idx = AGENT_KEY_ORDER.indexOf(Number(keyId));
  return idx >= 0 && state.profile === "claude_code" ? autos[idx] || null : null;
}
function keyForAgent(s) {
  for (const k of KEYS) { const a = agentForKey(k.keyId); if (a && a.slot === s.slot) return k.keyId; }
  return null;
}
function renderStageTitle() {
  const kicker = $("stageKicker"), name = $("stageName");
  document.querySelector(".stage-title").classList.toggle("empty", !selected);
  if (!selected) { kicker.textContent = "Click an element of the pad to set it up"; name.textContent = ""; return; }
  const { kind, id } = selected;
  if (kind === "key") {
    const a = agentForKey(id);
    if (a) {
      kicker.innerHTML = `Key ${escapeHtml(id)} · <b>${escapeHtml(STATUS_LABEL[a.status] || a.status)}</b>`;
      name.textContent = a.name || "slot " + a.slot;
    } else { kicker.textContent = `Key ${id} · LED ${KEY_TO_LED[Number(id)]}`; name.textContent = keyName(id); }
  } else if (kind === "enc") { kicker.textContent = "Rotation + click"; name.textContent = "Encoder"; }
  else if (kind === "joy") { kicker.textContent = "Joystick button"; name.textContent = "Joystick"; }
  else if (kind === "led") { kicker.textContent = "Halo"; name.textContent = `LED ${id}`; }
  else if (kind === "global") { kicker.textContent = "Global color"; name.textContent = id === "default" ? "Keys" : "Halo"; }
  else if (kind === "state") { const [g, n] = splitStateId(id); kicker.textContent = isKeyGroup(g) ? `Key ${g.slice(3)}` : STATE_GROUP_LABEL[g] || "State"; name.textContent = stateMeta(n)[1]; }
}
function renderRail() {
  const box = $("agentRail");
  if (!box) return;                       // sessions live in "Now"
  box.innerHTML = "";
  const list = detectedSessions().slice(0, 8);
  if (!list.length) {
    const p = document.createElement("div");
    p.className = "rail-empty";
    p.textContent = "No Claude Code session detected";
    box.appendChild(p);
    return;
  }
  for (const s of list) {
    const keyId = keyForAgent(s);
    const b = document.createElement("button");
    b.type = "button";
    b.className = "rail-btn" + (selected && selected.kind === "key" && keyId != null && Number(selected.id) === keyId ? " active" : "");
    b.style.color = STATUS_COLOR[s.status] || STATUS_COLOR.offline;
    b.setAttribute("aria-label", `${s.name || "slot " + s.slot}, ${STATUS_LABEL[s.status] || s.status}`);
    b.innerHTML = `<span class="rail-dot"></span><span class="rail-label">${escapeHtml(s.name || "slot " + s.slot)}</span>`;
    b.addEventListener("click", () => {
      if (keyId != null) selectElement("key", String(keyId));
      else toast("This session isn't linked to any key");
    });
    box.appendChild(b);
  }
}

$("applyBtn").addEventListener("click", async () => {
  if (!selected) return;
  const { kind, id } = selected;
  try {
    if (kind === "key" || kind === "joy") {
      const action = $("actionType").value || null;
      let value = $("actionValue").value;
      if (action === "macro") value = macroSteps;
      else if (action === "agent") value = $("agentActionSlot").value;
      const keyId = kind === "joy" ? "110" : id;
      if (!$("roleSection").hidden) {
        const rb = roleBinding();
        if (rb) await api("/binding/key", { id: keyId, action: rb.action, value: rb.value });
      } else if (!$("bindingSection").hidden) await api("/binding/key", { id: keyId, action, value });
      if (!$("colorSection").hidden) await applyColor();
      toast("Applied");
    } else if (kind === "enc" && !$("knobSection").hidden) {
      await api("/binding/encoder", knobBody());
      toast("Applied");
      await loadState();
      renderKnobSection();
      return;
    } else if (kind === "enc") {
      const mk = (v) => (v ? { action: "combo", value: v } : null);
      await api("/binding/encoder", {
        id: "0",
        cw: mk($("encCw").value),
        ccw: mk($("encCcw").value),
        press: mk($("encPress").value),
      });
      toast("Applied");
    } else if (kind === "led" || kind === "global") {
      await applyColor();
      toast("Applied");
    } else if (kind === "state") {
      await applyStateDraft(id);
      toast("Applied");
      await loadState();
      selectElement("state", id);
      return;
    }
    await loadState();
    renderColorDefault();
    if (!$("roleSection").hidden) renderRoleSection(selected.id);
  } catch (err) {
    toast("Error: " + err.message);
  }
});

async function applyColor() {
  const c = currentColorFromInputs();
  if (selected.kind === "global") {
    await api("/color_global", { which: selected.id, color: c });
  } else if (selected.kind === "key") {
    // the "colors" config is indexed by LED id, not key id
    await api("/color", { id: String(KEY_TO_LED[Number(selected.id)]), color: c });
  } else {
    await api("/color", { id: String(selected.id), color: c });
  }
}

$("previewBtn").addEventListener("click", async () => {
  if (!selected) return;
  if (selected.kind === "state") { previewStateDraft(4); return; }
  const c = currentColorFromInputs();
  try {
    if (selected.kind === "global") {
      await api("/preview_led_all", { color: c, zone: selected.id === "ring" ? "ring" : "keys" });
    } else if (selected.kind === "key") {
      await api("/preview_led", { id: KEY_TO_LED[Number(selected.id)], color: c });
    } else {
      await api("/preview_led", { id: Number(selected.id), color: c });
    }
  } catch (err) {
    toast("Not connected?");
  }
});

$("connectBtn").addEventListener("click", async () => {
  try {
    if (state.connected) {
      await api("/disconnect");
    } else {
      const port = $("portSelect").value || null;
      await api("/connect", { port });
      connectWs();
    }
    await loadState();
  } catch (err) {
    toast("Error: " + err.message);
  }
});

// ---- profiles: Agents + macropad profiles (each its own config), automatic switching ----
async function switchProfile(id) {
  await api("/profile", { profile: id });
  await loadState();
  if (selected) selectElement(selected.kind, selected.id);
}
function renderProfiles() {
  const box = $("profileSwitch"), profs = state.profiles || [];
  box.innerHTML = "";
  for (const p of profs) {
    const b = document.createElement("button");
    b.type = "button"; b.className = "profile-chip" + (p.id === state.profile ? " active" : "");
    b.textContent = p.name;
    b.addEventListener("click", () => switchProfile(p.id));
    box.appendChild(b);
  }
  const add = document.createElement("button");
  add.type = "button"; add.className = "profile-chip add"; add.textContent = "+"; add.title = "New macro profile";
  add.addEventListener("click", async () => {
    const r = await api("/profiles/create", { name: "New profile" });
    await loadState(); renderProfiles();
    $("profileName").focus(); $("profileName").select();
    toast("Profile created: give it a name and its applications");
  });
  box.appendChild(add);
  const cur = profs.find((p) => p.id === state.profile);
  const macro = cur && cur.kind === "macropad";
  $("profileEdit").hidden = !macro;
  $("autoProfile").checked = !!state.auto_profile;
  renderDefaultProject(cur && cur.kind === "agents");
  if (!macro) return;
  if (document.activeElement !== $("profileName")) $("profileName").value = cur.name;
  const apps = $("profileApps"); apps.innerHTML = "";
  if (!cur.apps.length) apps.innerHTML = '<span class="app-none">no application: manual selection only</span>';
  for (const a of cur.apps) {
    const c = document.createElement("span"); c.className = "app-chip";
    c.innerHTML = `${escapeHtml(a)}<button type="button" aria-label="Remove">×</button>`;
    c.querySelector("button").addEventListener("click", async () => {
      await api("/profiles/update", { id: cur.id, apps: cur.apps.filter((x) => x !== a) }); await loadState();
    });
    apps.appendChild(c);
  }
  $("profileDelete").hidden = profs.filter((p) => p.kind === "macropad").length <= 1;
}
$("profileName").addEventListener("change", async () => {
  await api("/profiles/update", { id: state.profile, name: $("profileName").value }); await loadState();
});
$("profileAddApp").addEventListener("focus", async () => {
  const list = await api("/apps").catch(() => []), sel = $("profileAddApp");
  sel.innerHTML = '<option value="">+ Add an open application…</option>' +
    list.map((a) => `<option value="${escapeHtml(a.exe)}">${escapeHtml(a.exe)} — ${escapeHtml(a.title)}</option>`).join("");
});
$("profileAddApp").addEventListener("change", async () => {
  const exe = $("profileAddApp").value; if (!exe) return;
  const cur = (state.profiles || []).find((p) => p.id === state.profile);
  await api("/profiles/update", { id: state.profile, apps: [...(cur ? cur.apps : []), exe] });
  $("profileAddApp").value = ""; await loadState();
});
$("profileDelete").addEventListener("click", async () => {
  await api("/profiles/delete", { id: state.profile }); await loadState(); toast("Profile deleted (Undo to get it back)");
});
// ---- default project: an agent key with no session opens Claude there ----
async function renderDefaultProject(show) {
  $("defaultProject").hidden = !show;
  if (!show) return;
  const inp = $("defaultProjectInput");
  if (document.activeElement !== inp) { inp.value = state.default_project || ""; inp.classList.remove("bad"); }
  // shortcuts: the folders of the open sessions (except the one already chosen)
  let dirs = [];
  try { dirs = (await (await fetch("/api/agents")).json()).filter((a) => a.alive && a.cwd).map((a) => a.cwd); } catch {}
  const box = $("defaultProjectChips"); box.innerHTML = "";
  for (const d of [...new Set(dirs)].filter((d) => d !== state.default_project)) {
    const b = document.createElement("button");
    b.type = "button"; b.className = "dp-chip"; b.title = d;
    b.textContent = d.replace(/[\\/]+$/, "").split(/[\\/]/).pop();
    b.addEventListener("click", () => setDefaultProject(d));
    box.appendChild(b);
  }
}
async function setDefaultProject(path) {
  const r = await api("/default_project", { path });
  if (!r.ok) { $("defaultProjectInput").classList.add("bad"); toast(r.error || "Folder not found"); return; }
  await loadState(); renderProfiles();
  toast(r.default_project ? "Default project: " + r.default_project : "No default project");
}
$("defaultProjectInput").addEventListener("change", (e) => setDefaultProject(e.target.value.trim()));
$("defaultProjectInput").addEventListener("keydown", (e) => { if (e.key === "Enter") e.target.blur(); });

// ---- Claude Code hooks: session state without reading the screen (essential on Mac) ----
async function renderHooks() {
  let h;
  try { h = await (await fetch("/api/hooks")).json(); } catch { return; }
  $("hooksHint").textContent = h.installed
    ? "Installed: each session tells the pad what it is doing."
    : h.needed ? "Needs installing: without them, the pad can't see session states on this system."
               : "Optional here (the pad reads the session screens).";
  $("hooksBtn").textContent = h.installed ? "Remove hooks" : "Install hooks";
  $("hooksBtn").dataset.on = h.installed ? "1" : "";
  $("hooksHint").classList.toggle("warn", !h.installed && h.needed);
}
$("hooksBtn").addEventListener("click", async () => {
  const r = await api("/hooks", { install: !$("hooksBtn").dataset.on });
  toast(r.installed ? "Hooks installed (your Claude Code settings were backed up)" : "Hooks removed");
  renderHooks();
});
renderHooks();

$("autoProfile").addEventListener("change", () => api("/auto_profile", { on: $("autoProfile").checked }).catch(() => {}));

// automatic save (server): here we just display "Saved" and handle Undo
let savedTimer = null;
function showSaved() {
  $("saveHint").textContent = "Saved"; $("saveHint").style.opacity = 1;
  clearTimeout(savedTimer); savedTimer = setTimeout(() => ($("saveHint").style.opacity = 0), 1800);
}
function setUndo(depth) { $("undoBtn").disabled = !depth; }
async function undoLast() {
  try {
    const r = await api("/undo", {});
    setUndo(r.undo_depth);
    if (!r.ok) return;
    await loadState();
    if (selected) selectElement(selected.kind, selected.id);
    toast("Change undone");
  } catch (err) { toast("Error: " + err.message); }
}
$("undoBtn").addEventListener("click", undoLast);
document.addEventListener("keydown", (e) => {
  if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === "z" && !(e.target.closest && e.target.closest("input, textarea, select"))) {
    e.preventDefault(); undoLast();
  }
});

// ---- agent states (Claude Code profile) -------------------------------------------------
// Two separately adjustable groups: the agent keys (each = its own session) and the halo
// (summary of all sessions). For each state: color + animation, and a factory
// setting always restorable. Only the states the software actually detects are offered.
// A selected state has id "<group>:<state>", e.g. "halo:thinking".
const STATE_UI = [
  ["thinking", "Working", "the session is computing or running a tool"],
  ["needs_attention", "Needs you", "a choice or permission is waiting for your answer"],
  ["waiting_input", "Idle", "the session is doing nothing and you have already seen it"],
  ["done", "Done", "finished its task while you were looking elsewhere; turns off when you show it"],
  ["error", "Error", "the process stopped with an error"],
  ["idle", "No session", "no session detected"],
];
const STATE_GROUP_LABEL = { keys: "Agent keys", halo: "Halo" };
const STATE_ANIM_LABEL = { solid: "Solid", breathe: "Breathe", blink: "Blink" };
const sameSpec = (a, b) => a && b && a.anim === b.anim && Number(a.speed) === Number(b.speed) && a.color.every((v, i) => v === (b.color[i] || 0));
const PER_KEY_STATES = ["waiting_input", "idle"];
const splitStateId = (id) => { const [g, n] = String(id).split(":"); return n ? [g, n] : ["keys", g]; };
const isKeyGroup = (g) => /^key\d+$/.test(g);
const isAgentKey = (id) => state.profile === "claude_code" && AGENT_KEY_ORDER.includes(Number(id));
// a key: the global setting, except for the states it has customized; its "default" = the global
const statesOf = (g) => isKeyGroup(g) ? { ...state.states, ...((state.key_states || {})[g.slice(3)] || {}) } : (g === "halo" ? state.halo_states : state.states) || {};
const defaultsOf = (g) => (isKeyGroup(g) ? state.states : g === "halo" ? state.halo_state_defaults : state.state_defaults) || {};
const stateMeta = (n) => STATE_UI.find((s) => s[0] === n) || [n, n, ""];

function swatchStyle(el, spec) {
  // the white channel (W) counts: a soft white [0,0,0,30] must be visible, not come out black
  const [r, g, b, w = 0] = spec.color, lift = (v) => Math.min(255, v + w);
  el.style.setProperty("--sw", spec.color.slice(0, 4).some((v) => v) ? `rgb(${lift(r)},${lift(g)},${lift(b)})` : "#1b1d20");
  el.classList.toggle("st-breathe", spec.anim === "breathe");
  el.classList.toggle("st-blink", spec.anim === "blink");
  el.style.setProperty("--dur", (spec.anim === "blink" ? 0.5 : 2.4) / (Number(spec.speed) || 1) + "s");
}

function renderLightMode() {
  const cc = state.profile === "claude_code";
  $("lightStates").hidden = !cc;
  $("lightGeneric").hidden = cc;
  const mode = state.halo_mode || "agents";
  document.querySelectorAll("#haloMode .profile-btn").forEach((b) => b.classList.toggle("active", b.dataset.halo === mode));
  $("haloFixedColor").hidden = mode === "agents";
  $("baseColors").hidden = cc;
  const ring = (state.bindings.colors || {}).ring || [20, 20, 20, 0];
  $("haloFixedColor").style.setProperty("--sw", ring.slice(0, 4).some((v) => v) ? rgbCss(ring) : "#1b1d20");
  const br = state.brightness ?? 100;
  $("brightness").value = br; $("brightnessVal").textContent = br + " %";
  $("zoneBright").hidden = !cc;
  const zb = state.zone_brightness || {};
  for (const z of ["agents", "halo"]) {
    document.querySelector(`#zoneBright [data-zone="${z}"]`).value = zb[z] ?? 100;
    document.querySelector(`#zoneBright [data-val="${z}"]`).textContent = (zb[z] ?? 100) + " %";
  }
  if (cc) { renderStateList(); renderAmbiance(); }
}

// ---- ambiances: all states at once (keys + halo) ----
function renderAmbiance() {
  const cur = state.ambiance || "perso";
  document.querySelectorAll("#ambianceChips [data-amb]").forEach((b) => b.classList.toggle("active", b.dataset.amb === cur));
  $("ambPerso").hidden = cur !== "perso";
  $("ambPerso").classList.toggle("active", cur === "perso");
}
document.querySelectorAll("#ambianceChips [data-amb]").forEach((b) => b.addEventListener("click", async () => {
  await api("/ambiance", { name: b.dataset.amb });
  await loadState();
  if (selected && selected.kind === "state") selectElement("state", selected.id);
  toast("Ambiance: " + b.textContent);
}));

function renderStateList() {
  const box = $("stateList");
  if (!box || !state.states) return;
  box.innerHTML = "";
  const haloOn = (state.halo_mode || "agents") === "agents";
  for (const [name, label] of STATE_UI) {
    const ks = statesOf("keys")[name], hs = statesOf("halo")[name];
    if (!ks || !hs) continue;
    const sel = selected && selected.kind === "state" && splitStateId(selected.id)[1] === name && !isKeyGroup(splitStateId(selected.id)[0]);
    const custom = !sameSpec(ks, defaultsOf("keys")[name]) || !sameSpec(hs, defaultsOf("halo")[name]);
    const row = document.createElement("button");
    row.type = "button"; row.setAttribute("role", "listitem");
    row.className = "state-row duo" + (sel ? " selected" : "");
    row.innerHTML = `<span><span class="state-name">${escapeHtml(label)}</span><br><span class="state-meta">${STATE_ANIM_LABEL[ks.anim] || ks.anim}${custom ? ' · <span class="state-custom">custom</span>' : ""}</span></span><span class="swatches"><span class="state-swatch"></span><span class="state-swatch${haloOn ? "" : " off"}"></span></span><span class="state-meta">›</span>`;
    const [sk, sh] = row.querySelectorAll(".swatches .state-swatch");
    swatchStyle(sk, ks); swatchStyle(sh, hs);
    row.addEventListener("click", () => selectElement("state", "keys:" + name));
    box.appendChild(row);
  }
}

function renderKeyStateList(keyId) {
  const box = $("keyStateList");
  box.innerHTML = "";
  const g = "key" + keyId, specs = statesOf(g), defs = defaultsOf(g);
  for (const name of PER_KEY_STATES) {
    const spec = specs[name], id = g + ":" + name;
    const row = document.createElement("button");
    row.type = "button"; row.setAttribute("role", "listitem"); row.className = "state-row";
    const own = !sameSpec(spec, defs[name]);
    row.innerHTML = `<span class="state-swatch"></span><span><span class="state-name">${escapeHtml(stateMeta(name)[1])}</span><br><span class="state-meta">${STATE_ANIM_LABEL[spec.anim] || spec.anim} · ${own ? '<span class="state-custom">key-specific</span>' : "global"}</span></span><span class="state-meta">›</span>`;
    swatchStyle(row.querySelector(".state-swatch"), spec);
    row.addEventListener("click", () => selectElement("state", id));
    box.appendChild(row);
  }
}

let stateAnim = "solid";
function setStateAnim(a) {
  stateAnim = a;
  document.querySelectorAll("#stateAnim .anim-opt").forEach((b) => b.classList.toggle("active", b.dataset.anim === a));
  $("stateSpeed").disabled = a === "solid";
}
function loadStateEditor(id) {
  const [g, name] = splitStateId(id);
  const spec = statesOf(g)[name], def = defaultsOf(g)[name];
  setColorInputs(spec.color);
  setStateAnim(spec.anim);
  $("stateSpeed").value = spec.speed || 1;
  swatchStyle($("stateDefaultSwatch"), def);
  $("stateDefaultLabel").textContent = isKeyGroup(g) ? "Global" : "Default";
  $("stateGroup").hidden = isKeyGroup(g);
  document.querySelectorAll("#stateGroup .profile-btn").forEach((b) => b.classList.toggle("active", b.dataset.group === g));
  const hint = g === "keys" && PER_KEY_STATES.includes(name) ? "Also adjustable key by key: click an agent key."
    : g === "halo" ? "The halo takes the most important state across all sessions." : "";
  $("stateGroupHint").textContent = hint; $("stateGroupHint").hidden = !hint;
  $("stateDefaultText").textContent = STATE_ANIM_LABEL[def.anim] || def.anim;
  $("stateReset").disabled = sameSpec(spec, def);
}
function stateDraft() {
  return { color: currentColorFromInputs(), anim: stateAnim, speed: Number($("stateSpeed").value) };
}
let statePreviewTimer = null;
function previewStateDraft(ttl = 3) {
  // preview on the state's zone (8 agent keys, or the halo); the 3D pad follows the server frame
  if (!selected || selected.kind !== "state") return;
  const [g] = splitStateId(selected.id);
  const group = isKeyGroup(g) ? "key:" + g.slice(3) : g;
  clearTimeout(statePreviewTimer);
  statePreviewTimer = setTimeout(() => {
    api("/states/preview", { group, ...stateDraft(), ttl }).catch(() => {});
  }, 70);
}
async function applyStateDraft(id) {
  const [group, name] = splitStateId(id);
  if (isKeyGroup(group)) await api("/key_state", { key: group.slice(3), state: name, ...stateDraft() });
  else await api("/states", { group, state: name, ...stateDraft() });
}
document.querySelectorAll("#stateAnim .anim-opt").forEach((b) => b.addEventListener("click", () => { setStateAnim(b.dataset.anim); previewStateDraft(); }));
$("stateSpeed").addEventListener("input", () => previewStateDraft());
$("stateReset").addEventListener("click", async () => {
  if (!selected || selected.kind !== "state") return;
  const [group, name] = splitStateId(selected.id);
  if (isKeyGroup(group)) await api("/key_state/reset", { key: group.slice(3), state: name });
  else await api("/states/reset", { group, state: name });
  await loadState();
  selectElement("state", selected.id);
  previewStateDraft();
  toast(isKeyGroup(group) ? "The key is back to the global setting" : "Factory setting restored");
});
$("statesResetAll").addEventListener("click", async () => {
  await api("/states/reset", { group: "keys" });
  await api("/states/reset", { group: "halo" });
  await loadState();
  if (selected && selected.kind === "state") selectElement("state", selected.id);
  toast("Keys and halo are back to factory settings");
});
// editor: same state, keys or halo part
document.querySelectorAll("#stateGroup .profile-btn").forEach((b) => b.addEventListener("click", () => {
  if (!selected || selected.kind !== "state") return;
  selectElement("state", b.dataset.group + ":" + splitStateId(selected.id)[1]);
}));
$("haloFixedColor").addEventListener("click", () => selectElement("global", "ring"));
document.querySelectorAll("#haloMode .profile-btn").forEach((btn) => btn.addEventListener("click", async () => {
  await api("/halo_mode", { mode: btn.dataset.halo });
  await loadState();
}));

// per-zone brightness (agent mode): agent keys, halo, and each bottom key (its own card)
let zoneTimer = null;
function pushZone(body) { clearTimeout(zoneTimer); zoneTimer = setTimeout(() => api("/zone_brightness", body).catch(() => {}), 120); }
document.querySelectorAll("#zoneBright input").forEach((el) => el.addEventListener("input", () => {
  document.querySelector(`#zoneBright [data-val="${el.dataset.zone}"]`).textContent = el.value + " %";
  (state.zone_brightness ||= {})[el.dataset.zone] = Number(el.value);   // keeps the local state up to date
  pushZone({ zone: el.dataset.zone, value: Number(el.value) });
}));
$("keyBright").addEventListener("input", () => {
  if (!selected || selected.kind !== "key") return;
  $("keyBrightVal").textContent = $("keyBright").value + " %";
  ((state.zone_brightness ||= {}).keys ||= {})[selected.id] = Number($("keyBright").value);
  pushZone({ zone: "key", key: selected.id, value: Number($("keyBright").value) });
});

// global pad brightness: applied by the server to the whole frame
let brightnessTimer = null;
$("brightness").addEventListener("input", () => {
  const v = Number($("brightness").value);
  $("brightnessVal").textContent = v + " %";
  clearTimeout(brightnessTimer);
  brightnessTimer = setTimeout(() => api("/brightness", { value: v }).catch(() => {}), 120);
});

async function loadState() {
  state = await api("/state");
  applyLayout(state.layout);
  renderConnState();
  renderDeviceState();
  renderAnimControls();
  renderLightMode();
  setUndo(state.undo_depth);
  renderProfiles();
}

let bootId = null;
function connectWs() {
  if (ws) ws.close();
  ws = new WebSocket(`ws://${location.host}/ws?c=page`);
  // server restarted (new boot id): we reload this tab to get the
  // latest version, instead of the server opening a new one
  ws.onopen = () => api("/state").then((s) => {
    if (bootId && s.boot && s.boot !== bootId) location.reload();
    bootId = s.boot || bootId;
  }).catch(() => {});
  ws.onmessage = (ev) => {
    const msg = JSON.parse(ev.data);
    if (msg.t === "key") { if (msg.ev === "down") showLegend({ kind: msg.id === 100 ? "enc" : msg.id === 110 ? "joy" : "key", id: String(msg.id) }, true); flashHotspot(msg.id, msg.ev === "down"); if (calib.onKey && msg.ev === "down") calib.onKey(msg.id); }
    else if (msg.t === "enc") { flashHotspot("enc0", true); spinEncoder(msg.d || 0); }
    else if (msg.t === "joy") { updateJoystick(msg.x, msg.y); calib.onJoy(msg.x, msg.y); }
    else if (msg.t === "agents") { agentSessions = msg.sessions; renderAgentsList(); populateAgentActionSlot(); renderRail(); renderStageTitle(); }
    else if (msg.t === "frame") applyLiveFrame(msg.frame);
    else if (msg.t === "profile" && msg.profile !== state.profile) {
      loadState().then(() => { if (selected) selectElement(selected.kind, selected.id); });
      toast("Profile: " + (((state.profiles || []).find((p) => p.id === msg.profile) || {}).name || msg.profile));
    }
    else if (msg.t === "conn") {
      // automatic server-side connect/disconnect (startup, or
      // cable unplugged/replugged) -- no user action here,
      // just reflecting the real state live
      state.connected = msg.connected;
      state.port = msg.port;
      renderConnState();
      if (msg.connected) { loadState(); toast("Pad connected (" + msg.port + ")"); }
      else toast("Pad disconnected — reconnecting automatically…");
    }
  };
  ws.onclose = () => setTimeout(connectWs, 1500);
}

// ---- live feedback: joystick (real deflection) + encoder (rotation) --
const JOY_CENTER = 2048, JOY_RANGE = 2048, JOY_VISUAL_DEADZONE = 80;
let encAngle = 0;

function updateJoystick(rawX, rawY) {
  const norm = (v) => {
    const d = v - JOY_CENTER;
    if (Math.abs(d) < JOY_VISUAL_DEADZONE) return 0;
    return Math.max(-1, Math.min(1, d / JOY_RANGE));
  };
  // module's Y axis: an increasing value corresponds to "toward the user"
  window.Pad3D.setJoystick(norm(rawX), norm(rawY));
}

function spinEncoder(delta) {
  if (delta) window.Pad3D.spinEncoder(delta);
}

function flashHotspot(id, on) {
  if (id === "enc0") return;                          // knob rotation: already animated by spinEncoder
  if (Number(id) >= 100 && Number(id) !== 100 && Number(id) !== 110) return;   // joystick directions (200+)
  window.Pad3D.flashKey(Number(id), on);              // keys, knob click (100), joystick click (110)
}

// ---- live LED preview -------------------------------------------------
// The server continuously broadcasts (~10/s, whether the pad is plugged in or not) the same frame
// that is sent to the hardware (colors + animation + agent status + previews already merged):
// we apply it as-is to the 3D model, the halo dots, and the ambient color.
let lastFrame = null;
const localOverride = {};   // ledId -> { c, until }: immediate preview while adjusting a color
function applyLiveFrame(frame) {
  if (!Array.isArray(frame)) return;
  lastFrame = frame;
  const now = performance.now();
  const f = frame.slice();
  for (const id in localOverride) {
    if (localOverride[id].until < now) delete localOverride[id];
    else f[Number(id)] = localOverride[id].c;
  }
  window.Pad3D.setFrame(f);
  document.querySelectorAll(".ring-led").forEach((dot) => {
    const c = f[Number(dot.dataset.id)];
    if (c) dot.style.background = rgbCss(c);
  });
  const amb = window.Pad3D.ringColorCss();
  const lit = amb && Math.max(...amb.match(/\d+/g).map(Number)) > 70;
  document.documentElement.style.setProperty("--ambient", lit ? amb : "#12b9ff");
}

(async function init() {
  await refreshPorts();
  await loadState();
  await refreshAgents();
  connectWs(); // the ws is also used for the "agents" stream, useful even without a pad connected
})();


// =====================  panels: UX components  =====================
// The old controls (select #actionType, #animName, #agentActionSlot) stay in the page,
// hidden: the new components drive them, so all existing logic stays valid.

function markDirty(on) {
  if (on) { showSaved(); setUndo(1); }      // the server saves on its own; Undo becomes possible
}



// ---- animation: choice grid -> hidden select ----
function syncAnimPicker() {
  const v = $("animName").value;
  document.querySelectorAll("#animPicker .anim-opt").forEach((b) => {
    b.classList.toggle("active", b.dataset.anim === v);
    b.setAttribute("aria-checked", b.dataset.anim === v ? "true" : "false");
  });
}
document.querySelectorAll("#animPicker .anim-opt").forEach((b) => b.addEventListener("click", () => {
  $("animName").value = b.dataset.anim;
  $("animName").dispatchEvent(new Event("change"));
  syncAnimPicker();
}));

// ---- right panel header ----
const KIND_LABEL = { key: "Key", enc: "Encoder", joy: "Joystick", led: "Halo LED", global: "Global color", state: "Agent state" };
function renderPanelHead() {
  if (!selected) return;
  const { kind, id } = selected;
  $("panelKicker").textContent = KIND_LABEL[kind] || "Element";
  const chip = $("panelChip");
  const a = kind === "key" ? agentForKey(id) : null;
  if (a) {
    chip.hidden = false;
    chip.style.color = STATUS_COLOR[a.status] || STATUS_COLOR.offline;
    chip.textContent = (a.name || "slot " + a.slot) + " · " + (STATUS_LABEL[a.status] || a.status);
  } else chip.hidden = true;
}

// ---- action type: segmented buttons -> hidden select ----
function syncActionUi() {
  const v = $("actionType").value;
  document.querySelectorAll(".seg-btn").forEach((b) => {
    b.classList.toggle("active", b.dataset.action === v);
    b.setAttribute("aria-checked", b.dataset.action === v ? "true" : "false");
  });
  renderKbdPreview();
  renderAgentPick();
}
document.querySelectorAll(".seg-btn").forEach((b) => b.addEventListener("click", () => {
  $("actionType").value = b.dataset.action;
  $("actionType").dispatchEvent(new Event("change"));
}));

// ---- shortcut: preview as keyboard keys ----
function renderKbdPreview() {
  const box = $("kbdPreview");
  box.innerHTML = "";
  if ($("actionType").value !== "combo") return;
  const parts = $("actionValue").value.split("+").map((p) => p.trim()).filter(Boolean);
  parts.forEach((p, i) => {
    if (i) { const plus = document.createElement("span"); plus.className = "kbd-plus"; plus.textContent = "+"; box.appendChild(plus); }
    const k = document.createElement("span"); k.className = "kbd"; k.textContent = p; box.appendChild(k);
  });
}
$("actionValue").addEventListener("input", renderKbdPreview);

// ---- agent: session cards -> hidden select ----
function renderAgentPick() {
  const box = $("agentPick");
  if (!box) return;   // no more session <-> key link in the UI (radial menu)
  box.innerHTML = "";
  if (!agentSessions.length) {
    const p = document.createElement("div"); p.className = "hint"; p.textContent = "No session — add one in the Sessions tab.";
    box.appendChild(p); return;
  }
  const cur = $("agentActionSlot").value;
  for (const s of agentSessions) {
    const b = document.createElement("button");
    b.type = "button";
    b.className = "agent-card" + (String(s.slot) === String(cur) ? " active" : "");
    b.innerHTML = `<span class="agent-dot ${escapeHtml(s.status)}"></span><span class="agent-info"><span class="agent-name" style="display:block">${escapeHtml(s.name || "slot " + s.slot)}</span><span class="agent-sub" style="display:block">${escapeHtml(s.cwd || s.command || "")}</span></span>`;
    b.addEventListener("click", () => { $("agentActionSlot").value = s.slot; renderAgentPick(); });
    box.appendChild(b);
  }
}

// ---- quick colors ----
document.querySelectorAll(".preset").forEach((b) => b.addEventListener("click", () => {
  setColorInputs(b.dataset.c.split(",").map(Number));
  livePreviewColor();
}));


// =====================  calibration: joystick, keys, lights  =====================
// During calibration, the server triggers no action (mode "calibrating"); it only relays
// the raw events. Each step sends its result to /api/calibration, saved
// on its own (and undoable) like everything else.
const calib = { open: false, step: "joy", onKey: null, onPick: null, onJoy: () => {} };

function calibSetOpen(on) {
  calib.open = on;
  document.body.classList.toggle("calibrating", on);
  $("calibOpen").classList.toggle("active", on);
  $("calibPanel").hidden = !on;
  if (on) { $("panelEmpty").hidden = true; $("panelContent").hidden = true; }
  else { calibStopSequences(); if (selected) $("panelContent").hidden = false; else $("panelEmpty").hidden = false; }
  api("/calibration/mode", { on }).catch(() => {});
  clearInterval(calib.beat);
  if (on) calib.beat = setInterval(() => api("/calibration/mode", { on: true }).catch(() => {}), 8000);
  if (on) { joyLoad(); calibShowStep(calib.step); }
}
$("calibOpen").addEventListener("click", () => calibSetOpen(!calib.open));
$("calibClose").addEventListener("click", () => { if (guideRunning()) guideEnd(false); calib.joyAbort = true; calibSetOpen(false); });

function calibShowStep(step) {
  calibStopSequences();
  calib.step = step;
  document.querySelectorAll("[data-step]").forEach((b) => b.classList.toggle("active", b.dataset.step === step));
  document.querySelectorAll("[data-calib]").forEach((sct) => { sct.hidden = sct.dataset.calib !== step; });
  $("calibSave").textContent = step === "joy" ? "Apply joystick" : step === "keys" ? "Apply keys" : "Apply LEDs";
  if (step === "keys") renderKeysGrid();
  if (step === "leds") renderLedsGrid();
}
document.querySelectorAll("[data-step]").forEach((b) => b.addEventListener("click", () => calibShowStep(b.dataset.step)));

function calibStopSequences() {
  calib.onKey = null; calib.onPick = null;
  window.Pad3D.showHaloMarkers(false);
  clearInterval(calib.soloTimer); calib.soloTimer = null;
  window.Pad3D.highlight(null);
  $("keysSkip").disabled = true; $("ledsSkip").disabled = true;
}

// ---- joystick ----
const joy = { raw: [2048, 2048], hist: [], center: null, range: null, measuring: false, dead: 650, invX: false, invY: false };
function joyLoad() {
  const j = (layout && layout.joystick) || {};
  const prof = ((state.bindings || {}).joystick) || {};
  joy.center = j.center || null;
  joy.range = j.range || null;
  joy.dead = j.deadzone ?? prof.deadzone ?? 650;
  joy.invX = j.invert_x ?? prof.invert_x ?? false;
  joy.invY = j.invert_y ?? prof.invert_y ?? false;
  $("joyDead").value = joy.dead; $("joyDeadVal").textContent = joy.dead;
  $("joyInvX").checked = joy.invX; $("joyInvY").checked = joy.invY;
  joyRender();
}
const COMPASS = ["East", "Northeast", "North", "Northwest", "West", "Southwest", "South", "Southeast"];
function joyRender() {
  const [x, y] = joy.raw;
  const c = joy.center || [2048, 2048];
  $("jpDot").setAttribute("cx", x); $("jpDot").setAttribute("cy", y);
  $("jpCx").setAttribute("x1", c[0]); $("jpCx").setAttribute("x2", c[0]);
  $("jpCy").setAttribute("y1", c[1]); $("jpCy").setAttribute("y2", c[1]);
  $("jpDead").setAttribute("cx", c[0]); $("jpDead").setAttribute("cy", c[1]); $("jpDead").setAttribute("r", joy.dead);
  const r = joy.range;
  if (r) { $("jpRange").setAttribute("x", r[0]); $("jpRange").setAttribute("y", r[2]); $("jpRange").setAttribute("width", r[1] - r[0]); $("jpRange").setAttribute("height", r[3] - r[2]); }
  $("joyRaw").textContent = `${x} · ${y}`;
  $("joyCenter").textContent = joy.center ? `${joy.center[0]} · ${joy.center[1]}` : "auto";
  $("joyAmp").textContent = r ? `${r[1] - r[0]} × ${r[3] - r[2]}` : "—";
  // exactly the server's decoding (agentpad/joystick.py): what is shown here is what
  // the pad will trigger. If "up" gives "South", it's the Y axis that needs inverting.
  let dx = x - c[0], dy = y - c[1];
  if (joy.invX) dx = -dx;
  if (joy.invY) dy = -dy;
  if (Math.hypot(dx, dy) < joy.dead) { $("joyDir").textContent = "Center"; return; }
  const deg = ((Math.atan2(dy, dx) * 180) / Math.PI + 360) % 360;
  $("joyDir").textContent = COMPASS[Math.round(deg / 45) % 8];
}
calib.onJoy = (x, y) => {
  joy.raw = [x, y];
  joy.hist.push([x, y]); if (joy.hist.length > 12) joy.hist.shift();
  if (joy.measuring) {
    const r = joy.range;
    joy.range = [Math.min(r[0], x), Math.max(r[1], x), Math.min(r[2], y), Math.max(r[3], y)];
  }
  if (calib.open) joyRender();
};
$("joySetCenter").addEventListener("click", () => {
  const h = joy.hist.length ? joy.hist : [joy.raw];
  joy.center = [0, 1].map((i) => Math.round(h.reduce((a, p) => a + p[i], 0) / h.length));
  joyRender(); toast("Center set");
});
$("joyMeasure").addEventListener("click", () => {
  joy.range = [joy.raw[0], joy.raw[0], joy.raw[1], joy.raw[1]];
  joy.measuring = true;
  $("joyMeasure").disabled = true; $("joyMeasure").textContent = "Measuring… make circles";
  setTimeout(() => {
    joy.measuring = false;
    $("joyMeasure").disabled = false; $("joyMeasure").textContent = "Measure range — make circles for 5 s";
    const half = Math.min(joy.range[1] - joy.range[0], joy.range[3] - joy.range[2]) / 2;
    if (half > 400) { joy.dead = Math.round(half * 0.32 / 10) * 10; $("joyDead").value = joy.dead; $("joyDeadVal").textContent = joy.dead; }
    joyRender(); toast("Range measured");
  }, 5000);
});
$("joyDead").addEventListener("input", () => { joy.dead = Number($("joyDead").value); $("joyDeadVal").textContent = joy.dead; joyRender(); });
$("joyInvX").addEventListener("change", () => { joy.invX = $("joyInvX").checked; joyRender(); });
$("joyInvY").addEventListener("change", () => { joy.invY = $("joyInvY").checked; joyRender(); });

// ---- keys: each position glows, press the matching real key ----
let keysDraft = {};
function renderKeysGrid() {
  const g = $("keysGrid"); g.innerHTML = "";
  const src = Object.keys(keysDraft).length ? keysDraft : (layout ? layout.keys : {});
  KEY_POSITIONS.forEach((pos) => {
    const d = document.createElement("div");
    d.className = "mini-cell" + (calib.keyPos === pos ? " current" : "") + (pos in keysDraft ? " done" : "");
    d.innerHTML = `<b>${src[pos] ?? "?"}</b><span>${pos}</span>`;
    g.appendChild(d);
  });
}
function keysNext(i) {
  if (i >= KEY_POSITIONS.length) {
    calibStopSequences(); calib.keyPos = null; renderKeysGrid();
    $("keysInstr").innerHTML = "<b>Done.</b> Check the grid, then apply.";
    guideNext("keys");
    return;
  }
  const pos = KEY_POSITIONS[i];
  calib.keyPos = pos;
  window.Pad3D.highlight(pos);
  $("keysInstr").innerHTML = `Press the key that <b>glows white</b> on the 3D pad (${i + 1} / ${KEY_POSITIONS.length}).`;
  renderKeysGrid();
  calib.onKey = (id) => {
    if (id >= 100) return;                                  // encoder / joystick: ignored here
    const dup = Object.keys(keysDraft).find((p) => keysDraft[p] === id);
    if (dup) { toast(`Key ${id} already assigned to ${dup}`); return; }
    keysDraft[pos] = id; keysNext(i + 1);
  };
  $("keysSkip").onclick = () => keysNext(i + 1);
}
$("keysStart").addEventListener("click", () => { keysDraft = {}; $("keysSkip").disabled = false; keysNext(0); });

// ---- lights: each LED lights up alone, click where it appears ----
let ledsDraft = {};
function renderLedsGrid() {
  const src = Object.keys(ledsDraft).length ? ledsDraft : (layout ? layout.leds : {});
  const at = {}; for (const led in src) at[src[led]] = led;
  const g = $("ledsGrid"); g.innerHTML = "";
  KEY_POSITIONS.forEach((pos) => {
    const d = document.createElement("div");
    d.className = "mini-cell" + (Object.values(ledsDraft).includes(pos) ? " done" : "");
    d.innerHTML = `<b>${at[pos] ?? "–"}</b><span>LED</span>`;
    g.appendChild(d);
  });
  const hr = $("ledsHalo"); hr.innerHTML = "";
  HALO_POSITIONS.forEach((pos) => {
    const d = document.createElement("div");
    d.className = "halo-cell" + (Object.values(ledsDraft).includes(pos) ? " done" : "");
    d.textContent = at[pos] ?? "–"; d.title = pos;
    hr.appendChild(d);
  });
}
function ledsNext(id) {
  const n = state.num_leds || 24;
  clearInterval(calib.soloTimer);
  if (id >= n) {
    calibStopSequences(); renderLedsGrid();
    api("/calibration/solo", { id: -1, color: [0, 0, 0, 0], ttl: 0.1 }).catch(() => {});
    $("ledsInstr").innerHTML = "<b>Done.</b> Check the positions, then apply.";
    guideNext("leds");
    return;
  }
  const solo = () => api("/calibration/solo", { id, color: [0, 0, 0, 255], ttl: 1.2 }).catch(() => {});
  solo(); calib.soloTimer = setInterval(solo, 800);
  $("ledsInstr").innerHTML = `<b>LED ${id}</b> is lit on the real pad: click its position on the 3D model — a key or a halo point (${id + 1} / ${n}). <span class="calib-tip">Drag to rotate the pad, scroll to zoom, double-click to reframe.</span>`;
  renderLedsGrid();
  calib.onPick = (kind, _id, pos) => {
    if (kind !== "key" && kind !== "halo") return true;    // knob/joystick: ignored, but we keep control
    const taken = Object.keys(ledsDraft).find((l) => ledsDraft[l] === pos);
    if (taken) { toast(`Position already taken by LED ${taken}`); return true; }
    ledsDraft[id] = pos; window.Pad3D.highlight(pos); setTimeout(() => window.Pad3D.highlight(null), 400);
    ledsNext(id + 1);
    return true;
  };
  $("ledsSkip").onclick = () => ledsNext(id + 1);
}
$("ledsStart").addEventListener("click", () => { ledsDraft = {}; $("ledsSkip").disabled = false; setView("global"); window.Pad3D.showHaloMarkers(true); ledsNext(0); });

// ---- apply the current step ----
async function applyCalibStep(step, quiet) {
  try {
    let body;
    calib.step = step;
    if (calib.step === "joy") {
      body = { joystick: { deadzone: joy.dead, invert_x: joy.invX, invert_y: joy.invY, ...(joy.center ? { center: joy.center } : {}), ...(joy.range ? { range: joy.range } : {}) } };
    } else if (calib.step === "keys") {
      if (Object.keys(keysDraft).length < KEY_POSITIONS.length) { toast("Go through all 12 keys before applying"); return; }
      body = { keys: keysDraft };
    } else {
      if (!Object.keys(ledsDraft).length) { toast("Run the LED calibration first"); return; }
      // interrupted calibration: LEDs not yet clicked keep their current position,
      // unless that position was just assigned to another LED
      const taken = new Set(Object.values(ledsDraft)), leds = {};
      for (const [led, pos] of Object.entries((layout && layout.leds) || {})) if (!taken.has(pos)) leds[led] = pos;
      body = { leds: Object.assign(leds, ledsDraft) };
    }
    const lay = await api("/calibration", body);
    applyLayout(lay);
    state.layout = lay;
    if (!quiet) toast("Setting applied");
    return true;
  } catch (err) {
    toast("Error: " + err.message);
    return false;
  }
}
$("calibSave").addEventListener("click", () => applyCalibStep(calib.step));

// =====================  guided calibration  =====================
// One single button: keys -> lights -> joystick. Each step starts on its own, applies itself at
// the end (saving is automatic) and moves to the next one.
const GUIDE = [["keys", "Keys"], ["leds", "LEDs"], ["joy", "Joystick"]];
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
function guideRunning() { return calib.guide != null; }
function guideShow() {
  const g = calib.guide;
  $("guideProgress").hidden = g == null;
  $("guideHint").hidden = g != null;
  $("guideStart").textContent = g == null ? "Guided calibration" : "Stop guided calibration";
  if (g != null && GUIDE[g]) $("guideProgress").textContent = `Step ${g + 1} / ${GUIDE.length} · ${GUIDE[g][1]}`;
}
function guideStep() {
  const cur = GUIDE[calib.guide];
  if (!cur) return guideEnd(true);
  guideShow();
  calibShowStep(cur[0]);
  if (cur[0] === "keys") $("keysStart").click();
  else if (cur[0] === "leds") $("ledsStart").click();
  else joyAuto();
}
async function guideNext(step) {
  if (!guideRunning() || GUIDE[calib.guide][0] !== step) return;
  await sleep(500);
  await applyCalibStep(step, true);
  calib.guide += 1;
  guideStep();
}
function guideEnd(done) {
  calib.guide = null; calib.joyAbort = true;
  guideShow();
  if (done) { toast("Calibration finished and saved"); $("guideProgress").hidden = false; $("guideProgress").textContent = "Done"; }
}
$("guideStart").addEventListener("click", () => {
  if (guideRunning()) { calibStopSequences(); guideEnd(false); return; }
  calib.guide = 0; guideStep();
});

// ---- automatic joystick: center, axis direction, range ----
function joyDelta() { const c = joy.center || [2048, 2048]; return [joy.raw[0] - c[0], joy.raw[1] - c[1]]; }
async function waitJoy(pred, timeoutMs) {
  const t0 = Date.now();
  while (Date.now() - t0 < timeoutMs) { if (calib.joyAbort) return null; const d = joyDelta(); if (pred(d)) return d; await sleep(60); }
  return null;
}
async function joyAuto() {
  calib.joyAbort = false;
  const say = (html) => ($("joyInstr").innerHTML = html);
  $("joyAutoBtn").disabled = true;
  try {
    say("<b>Let go of the joystick</b> and don't touch it…");
    await sleep(1600); joy.hist = []; await sleep(900);
    const h = joy.hist.length ? joy.hist : [joy.raw];
    joy.center = [0, 1].map((i) => Math.round(h.reduce((a, p) => a + p[i], 0) / h.length));
    joyRender();
    say("Push the joystick <b>up</b> and hold.");
    const up = await waitJoy((d) => Math.hypot(d[0], d[1]) > 900, 20000);
    if (!up) { say("No movement detected: joystick left as is."); return; }
    if (Math.abs(up[0]) > Math.abs(up[1])) toast("Pushing the joystick up moves the horizontal axis: check its orientation");
    joy.invY = up[1] < 0;                                   // the server expects "up" = positive Y
    say("Good. <b>Release</b> the joystick.");
    await waitJoy((d) => Math.hypot(d[0], d[1]) < 300, 10000);
    say("Push the joystick <b>right</b> and hold.");
    const right = await waitJoy((d) => Math.hypot(d[0], d[1]) > 900, 20000);
    if (right) joy.invX = right[0] < 0;
    say("Good. <b>Release</b> it, then make <b>big circles</b> for 4 seconds.");
    await waitJoy((d) => Math.hypot(d[0], d[1]) < 300, 10000);
    joy.range = [joy.raw[0], joy.raw[0], joy.raw[1], joy.raw[1]];
    joy.measuring = true; await sleep(4000); joy.measuring = false;
    const half = Math.min(joy.range[1] - joy.range[0], joy.range[3] - joy.range[2]) / 2;
    if (half > 400) joy.dead = Math.round(half * 0.32 / 10) * 10;
    $("joyDead").value = joy.dead; $("joyDeadVal").textContent = joy.dead;
    $("joyInvX").checked = joy.invX; $("joyInvY").checked = joy.invY;
    joyRender();
    say("<b>Joystick set.</b> Push it: the displayed direction should follow your movement.");
    if (guideRunning()) await guideNext("joy");
    else await applyCalibStep("joy");
  } finally {
    $("joyAutoBtn").disabled = false;
  }
}
$("joyAutoBtn").addEventListener("click", () => joyAuto());



// =====================  spaces: "Now" and "Pad"  =====================
// Now = live sessions (terminal text, menus to answer). Pad = settings.
const NOW_STATUS = {
  attention: ["Needs you", "#ffc21a"], finished: ["Done", "#2fd36b"], running: ["Working", "#12b9ff"],
  waiting: ["Idle", "#9aa0a6"], error: ["Error", "#ff3b30"], done: ["Finished", "#2fd36b"], offline: ["Offline", "#5b5f66"],
};
let space = "now", nowTimer = null;
function setSpace(which) {
  space = which;
  try { localStorage.setItem("agentpad.space", which); } catch (_) {}
  document.querySelectorAll("#spaceNav .profile-btn").forEach((b) => b.classList.toggle("active", b.dataset.space === which));
  $("nowView").hidden = which !== "now";
  $("padView").hidden = which !== "pad";
  clearInterval(nowTimer);
  if (which === "now") { refreshNow(); nowTimer = setInterval(refreshNow, 1500); }
  else window.dispatchEvent(new Event("resize"));      // the 3D canvas regains its size
}
document.querySelectorAll("#spaceNav .profile-btn").forEach((b) => b.addEventListener("click", () => setSpace(b.dataset.space)));

function nowHeadline(list) {
  if (!list.length) return "No Claude Code sessions";
  const n = (st) => list.filter((s) => s.status === st).length;
  const parts = [`${list.length} session${list.length > 1 ? "s" : ""}`];
  if (n("attention")) parts.push(`<b>${n("attention")} need${n("attention") > 1 ? "" : "s"} you</b>`);
  if (n("finished")) parts.push(`${n("finished")} done`);
  if (n("running")) parts.push(`${n("running")} working`);
  return parts.join(" · ");
}

let nowBusy = false;
async function refreshNow() {
  if (nowBusy || space !== "now") return;
  nowBusy = true;
  try {
    const list = await api("/live");
    $("nowTitle").innerHTML = nowHeadline(list);
    renderNow(list);
  } catch (_) { /* server restarting: we retry next round */ }
  nowBusy = false;
}

function renderNow(list) {
  const grid = $("nowGrid");
  if (!list.length) { grid.innerHTML = '<div class="now-empty">Open a Claude Code session: it will show up here on its own.</div>'; return; }
  const keep = new Set();
  for (const s of list) {
    const id = "sess-" + s.pid; keep.add(id);
    let card = document.getElementById(id);
    if (!card) {
      card = document.createElement("article"); card.id = id; card.className = "sess";
      card.innerHTML = `<header class="sess-head"><span class="sess-dot"></span><span class="sess-name"></span><span class="sess-key"></span><span class="sess-status"></span></header>
        <pre class="sess-text"></pre><div class="sess-menu" hidden></div>
        <footer class="sess-foot"><button type="button" class="btn btn-ghost btn-sm" data-act="focus">Show window</button></footer>`;
      card.querySelector('[data-act="focus"]').addEventListener("click", () => api("/live/focus", { pid: s.pid }).catch(() => {}));
      grid.appendChild(card);
    }
    const [label, color] = NOW_STATUS[s.status] || [s.status, "#9aa0a6"];
    card.className = "sess st-" + s.status + (s.focused ? " focused" : "");
    card.querySelector(".sess-dot").style.color = color;
    card.querySelector(".sess-name").textContent = s.name || "session";
    card.querySelector(".sess-status").textContent = label; card.querySelector(".sess-status").style.color = color;
    card.querySelector(".sess-key").textContent = s.agent_key != null ? keyName(s.agent_key) : "";
    card.querySelector(".sess-key").hidden = s.agent_key == null;
    const pre = card.querySelector(".sess-text"), atBottom = pre.scrollHeight - pre.scrollTop - pre.clientHeight < 8;
    const txt = (s.text || []).join("\n");
    if (pre.textContent !== txt) { pre.textContent = txt; if (atBottom) pre.scrollTop = pre.scrollHeight; }
    const box = card.querySelector(".sess-menu"), sig = s.menu ? JSON.stringify(s.menu) : "";
    box.hidden = !s.menu;
    if (box.dataset.sig !== sig) {
      box.dataset.sig = sig; box.innerHTML = "";
      if (s.menu) {
        const q = document.createElement("div"); q.className = "sess-q"; q.textContent = s.menu.question || "Choose an option"; box.appendChild(q);
        const opts = document.createElement("div"); opts.className = "sess-opts";
        for (const it of s.menu.items) {
          const b = document.createElement("button"); b.type = "button"; b.className = "sess-opt" + (it.selected ? " sel" : "");
          b.innerHTML = `<span class="n">${it.n}</span><span>${escapeHtml(it.label)}${it.desc ? `<span class="d">${escapeHtml(it.desc)}</span>` : ""}</span>`;
          b.addEventListener("click", async () => {
            const r = await api("/live/answer", { pid: s.pid, n: it.n }).catch((e) => ({ ok: false, error: e.message }));
            toast(r.ok ? "Answer sent" : "Not sent: " + (r.error || "error"));
            setTimeout(refreshNow, 600);
          });
          opts.appendChild(b);
        }
        box.appendChild(opts);
      }
    }
  }
  for (const c of [...grid.children]) if (c.id && !keep.has(c.id)) c.remove();
  const empty = grid.querySelector(".now-empty"); if (empty && list.length) empty.remove();
}

try { setSpace(localStorage.getItem("agentpad.space") === "pad" ? "pad" : "now"); } catch (_) { setSpace("now"); }


// =====================  live legend  =====================
// What each pad element does RIGHT NOW, in the active profile: on hover over the 3D model,
// and briefly when a real key is pressed.
function describeMacroBinding(b) {
  if (!b || !b.action || b.action === "none") return "does nothing";
  if (b.action === "combo") return "sends " + String(b.value || "").replace(/\+/g, " + ");
  if (b.action === "text") return "types “" + String(b.value || "").trim() + "”";
  if (b.action === "macro") { const k = Array.isArray(b.value) ? b.value.length : 0; return "runs a macro with " + k + " step" + (k === 1 ? "" : "s"); }
  return b.action;
}
function legendFor(kind, id) {
  const cc = state.profile === "claude_code";
  const binds = (state.bindings && state.bindings.keys) || {};
  if (kind === "key") {
    if (isAgentKey(id)) {
      const a = agentForKey(id);
      const sess = a ? `${a.name || "session"} · ${(NOW_STATUS[a.status] || [a.status])[0].toLowerCase()}` : "no session";
      return [keyName(id), `${sess}. Press: shows or hides its window.`];
    }
    const b = binds[id] || (state.key_action_defaults || {})[id];
    if (cc && b && b.action === "mic") return [keyName(id), "Hold: Claude Code dictation in the foreground session."];
    if (cc) {
      const extra = b && b.action === "combo" && b.value === "esc" ? " Right after a dictation or in a menu: clears it or picks “Chat about this”." : "";
      return [keyName(id), (b ? describeBinding(b) : "does nothing") + " in the foreground session." + extra];
    }
    return [keyName(id), describeMacroBinding(b) + "."];
  }
  if (kind === "enc") {
    const enc = ((state.bindings.encoders || {})["0"]) || {}, def = state.encoder_defaults || {};
    const cw = enc.cw || def.cw, press = enc.press || def.press, dbl = enc.double || def.double;
    const rot = cw && cw.action === "auto" ? "in a session: menu choice; elsewhere: volume" : describeMacroBinding(cw);
    const btn = (x) => { const r = KNOB_BTN.find((k) => sameB(k.b, x)); return r ? r.name.toLowerCase() + (r.id === "sessions" ? " (grid)" : "") : describeMacroBinding(x); };
    return ["Knob", `Rotation: ${rot}. Click: ${cc ? btn(press) : "hides the sessions"}.${cc ? " Double-click: " + btn(dbl) + "." : ""}`];
  }
  if (kind === "joy") return ["Joystick", cc ? "Up/down: scrolls the foreground session. Click: opens the radial menu; point with the joystick."
                                            : "Click: opens the radial menu; point with the joystick."];
  if (kind === "halo") return ["Halo", cc && (state.halo_mode || "agents") === "agents" ? "Shows the most important state across all sessions." : "The profile's solid color."];
  return null;
}
let legendTimer = null;
function showLegend(h, pressed) {
  const tip = $("legendTip");
  if (!h || space !== "pad") { if (!pressed) tip.hidden = true; return; }
  const L = legendFor(h.kind, h.id);
  if (!L) { tip.hidden = true; return; }
  tip.innerHTML = `<b>${escapeHtml(L[0])}</b>${escapeHtml(L[1])}`;
  tip.classList.toggle("pressed", !!pressed);
  if (!pressed) { tip.style.left = Math.min(innerWidth - 280, h.x + 16) + "px"; tip.style.top = (h.y + 18) + "px"; }
  tip.hidden = false;
  clearTimeout(legendTimer);
  if (pressed) legendTimer = setTimeout(() => (tip.hidden = true), 2200);
}
