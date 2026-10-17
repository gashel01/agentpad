// Pad 3D -- the real model of the v2 case (exported from Blender: hardware/case/agentpad_case.blend,
// V2_NewCase collection) rendered with three.js r147 (vendor/, classic script, no module).
// Exposes window.Pad3D: init, setLayout, setFrame, flashKey, setJoystick, spinEncoder, select, setView, highlight.
// No business logic here: app.js keeps the config, the bindings and the websocket.
(function () {
  const T = window.THREE;

  // node name in the model -> physical position "r{row}c{col}" (row 0 = back).
  // Which key (matrix id) and which LED are at each position: that's the calibration
  // (setLayout), not the model. The agent keys carry their slot number.
  const NODE_TO_POS = {
    key_1: 'r0c0', key_2: 'r0c1', key_3: 'r0c2', key_4: 'r0c3',
    key_5: 'r1c0', key_6: 'r1c1', key_7: 'r1c2', key_8: 'r1c3',
    key_eclair: 'r2c0', key_check: 'r2c1', key_croix: 'r2c2', key_micro: 'r2c3', key_code: 'r2c3',
  };
  // halo: "h0".."h11" clockwise seen from above (back l->r, right back->front,
  // front r->l, left front->back), 3 LEDs per wall at 16.67 mm pitch
  const HALO_POS = Array.from({ length: 12 }, (_, i) => 'h' + i);

  const P = {
    keys: {},        // position -> { cap, sw, capMat, swMat, baseY, press, pressT, agent }
    keyPos: {},      // position -> Vector3 (top of the key)
    halo: {},        // "hN" -> { mesh, mat }
    posToKey: {}, keyToPos: {}, posToLed: {},
    highlightPos: null,
    orbit: { yaw: 0, pitch: 0, zoom: 1 },          // user's free rotation, on top of the view
    view: 'global', focusKey: null,
    joy: { x: 0, y: 0 }, encAngle: 0,
    // joystick click (110) and knob click (100): press depth in mm, target t / value v
    btn: { 110: { t: 0, v: 0, mm: 1.6 }, 100: { t: 0, v: 0, mm: 1.8 } },
    selected: null,
  };

  function init(opts) {
    const cv = opts.canvas;
    T.ColorManagement.legacyMode = false;                      // hex colors read in sRGB
    const renderer = new T.WebGLRenderer({ canvas: cv, antialias: true, alpha: true });
    renderer.setPixelRatio(Math.min(window.devicePixelRatio || 1, 2));
    renderer.outputEncoding = T.sRGBEncoding;
    renderer.toneMapping = T.ACESFilmicToneMapping;
    renderer.toneMappingExposure = 0.85;
    const scene = new T.Scene();
    scene.environment = new T.PMREMGenerator(renderer).fromScene(new T.RoomEnvironment(), 0.04).texture;
    const cam = new T.PerspectiveCamera(30, 1, 0.01, 50);
    Object.assign(P, { renderer, scene, cam, onPick: opts.onPick || (() => {}), onHover: opts.onHover || (() => {}) });

    const key = new T.DirectionalLight(0xfff1e0, 1.8); key.position.set(-1.2, 2.2, 1.6); scene.add(key);
    const rim = new T.DirectionalLight(0xbcd4ff, 1.4); rim.position.set(1.6, 0.8, -1.8); scene.add(rim);
    scene.add(new T.HemisphereLight(0xffffff, 0x080808, 0.25));

    // light grey satin PETG (no varnish: no hot spot on the front), matte white PLA
    const petg = new T.MeshPhysicalMaterial({ color: 0x7d8084, roughness: 0.62, envMapIntensity: 0.3 });
    const petgKey = new T.MeshPhysicalMaterial({ color: 0x74777b, roughness: 0.6, envMapIntensity: 0.3 });
    const pla = new T.MeshPhysicalMaterial({ color: 0xe6e6e1, roughness: 0.62, envMapIntensity: 0.3 });
    const below = new T.MeshStandardMaterial({ color: 0x111214, roughness: 0.5 });
    const pcb = new T.MeshStandardMaterial({ color: 0x0b2a16, roughness: 0.6 });
    P.haloMat = new T.MeshBasicMaterial({ color: 0x222428, side: T.DoubleSide, toneMapped: false });
    P.grooveLights = [];
    P.pickables = [];

    fetch(opts.modelUrl).then((r) => r.arrayBuffer()).then((buf) => {
      new T.GLTFLoader().parse(buf, '', (gltf) => onModel(gltf, { petg, petgKey, pla, below, pcb }, opts), (e) => opts.onError && opts.onError(e));
    }).catch((e) => opts.onError && opts.onError(e));

    const resize = () => {
      const w = cv.clientWidth, h = cv.clientHeight;
      if (!w || !h) return;
      renderer.setSize(w, h, false); cam.aspect = w / h; cam.updateProjectionMatrix();
    };
    new ResizeObserver(resize).observe(cv); resize();

    const ray = new T.Raycaster(), ptr = new T.Vector2();
    const hit = (e) => {
      const r = cv.getBoundingClientRect();
      ptr.set(((e.clientX - r.left) / r.width) * 2 - 1, -((e.clientY - r.top) / r.height) * 2 + 1);
      ray.setFromCamera(ptr, cam);
      return ray.intersectObjects(P.pickables, false)[0];
    };
    // drag = rotate around the pad, wheel = zoom, double-click = reframe. A press that
    // moves less than 5 px stays a click (selecting a key / a halo point).
    let down = null, last = null;
    cv.style.touchAction = 'none';
    cv.addEventListener('pointerdown', (e) => { down = last = [e.clientX, e.clientY]; cv.setPointerCapture(e.pointerId); });
    cv.addEventListener('pointerup', (e) => {
      const moved = down && Math.hypot(e.clientX - down[0], e.clientY - down[1]) > 5;
      const d0 = down; down = null;
      if (!d0 || moved) return;
      const h = hit(e);
      if (!h) return;
      const ud = h.object.userData;
      P.onPick(ud.kind, ud.kind === 'key' ? String(P.posToKey[ud.pos]) : ud.pos, ud.pos);
    });
    cv.addEventListener('pointermove', (e) => {
      if (down) {
        P.orbit.yaw -= (e.clientX - last[0]) * 0.008;
        P.orbit.pitch = Math.max(-0.6, Math.min(1.1, P.orbit.pitch + (e.clientY - last[1]) * 0.006));
        last = [e.clientX, e.clientY];
        if (Math.hypot(e.clientX - down[0], e.clientY - down[1]) > 5) { cv.style.cursor = 'grabbing'; return; }
      }
      const h = hit(e);
      cv.style.cursor = h ? 'pointer' : 'grab';
      // hover: we tell the app which element is under the pointer (live legend)
      const ud = h && h.object.userData;
      P.onHover(ud ? { kind: ud.kind, id: ud.kind === 'key' ? String(P.posToKey[ud.pos]) : ud.kind === 'halo' ? ud.pos : ud.id, x: e.clientX, y: e.clientY } : null);
    });
    cv.addEventListener('pointerleave', () => P.onHover(null));
    cv.addEventListener('wheel', (e) => {
      e.preventDefault();
      P.orbit.zoom = Math.max(0.45, Math.min(1.8, P.orbit.zoom * Math.exp(e.deltaY * 0.001)));
    }, { passive: false });
    cv.addEventListener('dblclick', () => { P.orbit.yaw = 0; P.orbit.pitch = 0; P.orbit.zoom = 1; });

    loop();
  }

  // pivot group around a point (bottom of the joystick cap, knob axis), without moving the object
  function pivotAt(obj, point) {
    const g = new T.Group();
    obj.parent.add(g);
    g.position.copy(obj.parent.worldToLocal(point.clone()));
    g.updateMatrixWorld(true);
    g.attach(obj);
    return g;
  }

  function onModel(gltf, m, opts) {
    const root = gltf.scene;
    root.scale.setScalar(0.01);                                 // mm -> m
    root.rotation.x = 6 * Math.PI / 180;                        // resting on its base tilted by 6 deg
    const meshes = [];
    root.traverse((o) => { if (o.isMesh) meshes.push(o); });
    for (const o of meshes) {
      const n = o.name;
      if (n in NODE_TO_POS) {
        const id = NODE_TO_POS[n];
        const agent = id[1] !== '2';                                 // back and middle rows
        const mat = agent
          ? new T.MeshPhysicalMaterial({ color: 0xd9d9d4, roughness: 0.55, emissive: 0x000000, envMapIntensity: 0.25 })
          : m.petgKey.clone();
        o.material = mat;
        o.userData = { kind: 'key', pos: id };
        P.pickables.push(o);
        (P.keys[id] = P.keys[id] || {}).cap = o; P.keys[id].capMat = mat; P.keys[id].agent = agent;
      } else if (/^sw_/.test(n)) {
        const id = NODE_TO_POS['key_' + n.slice(3)];
        const mat = new T.MeshPhysicalMaterial({ color: 0xdfe2e5, roughness: 0.35, emissive: 0x000000, envMapIntensity: 0.3 });
        o.material = mat;
        (P.keys[id] = P.keys[id] || {}).sw = o; P.keys[id].swMat = mat;
      } else if (n === 'knob') { o.material = m.pla; o.userData = { kind: 'enc', id: '0' }; P.pickables.push(o); P.knob = o; }
      else if (n === 'joycap') { o.material = m.pla; o.userData = { kind: 'joy', id: '110' }; P.pickables.push(o); P.joycap = o; }
      else if (n === 'switches') o.material = m.below;
      else if (n === 'joystick_pcb') o.material = m.pcb;
      else if (n === 'halo') { o.material = P.haloMat; P.haloBand = o; o.userData = { kind: 'halo', pos: null }; P.pickables.push(o); }
      else o.material = m.petg;
    }
    const box = new T.Box3().setFromObject(root);
    const c = box.getCenter(new T.Vector3());
    root.position.set(-c.x, -box.min.y, -c.z);
    const pivot = new T.Group(); pivot.add(root); P.scene.add(pivot); P.pivot = pivot;
    pivot.updateMatrixWorld(true);

    for (const id in P.keys) {
      const k = P.keys[id];
      const kb = new T.Box3().setFromObject(k.cap);
      const kc = kb.getCenter(new T.Vector3()); kc.y = kb.max.y;
      P.keyPos[id] = kc;
      k.baseY = k.cap.position.y; k.press = 0; k.pressT = 0;
    }
    // pivots: joystick cap (tilts around its base), knob (rotates on its axis)
    if (P.joycap) {
      const jb = new T.Box3().setFromObject(P.joycap); const jc = jb.getCenter(new T.Vector3()); jc.y = jb.min.y;
      P.joyPivot = pivotAt(P.joycap, jc);
    }
    if (P.knob) {
      const kb = new T.Box3().setFromObject(P.knob);
      P.knobPivot = pivotAt(P.knob, kb.getCenter(new T.Vector3()));
    }
    // camera targets for the joystick and the knob: top of the object
    for (const [o, k] of [[P.joycap, 'joyPos'], [P.knob, 'knobPos']]) {
      if (!o) continue;
      const b = new T.Box3().setFromObject(o), c = b.getCenter(new T.Vector3()); c.y = b.max.y; P[k] = c;
    }
    // halo: the 12 LEDs at the case's real coordinates (RingLED_v2 blocks of agentpad_case.blend,
    // assembled case, in mm). Blender -> GLB frame: (x, y, z) -> (x, z, -(y - 259)).
    // Clockwise seen from above: back l->r (h0-h2), right back->front (h3-h5), front r->l (h6-h8),
    // left front->back (h9-h11). 16.67 mm pitch, blocks at 47.65 mm (front/back) and 46.65 mm (sides).
    const LED_MM = [[-16.67, 306.65], [0, 306.65], [16.67, 306.65],
      [46.65, 275.67], [46.65, 259], [46.65, 242.33],
      [16.67, 211.35], [0, 211.35], [-16.67, 211.35],
      [-46.65, 242.33], [-46.65, 259], [-46.65, 275.67]];
    root.updateMatrixWorld(true);
    // point of the case in the scene, at `rad` mm from the center along its wall's normal, height z mm.
    // Groove profile (measured on the model): outer wall at 58 mm, opening between z 6.5 and
    // 13 mm, 45 deg sloped bottom rising from 56 mm (z 6.7) to 49 mm (z 12) toward the strip.
    const ledWorld = ([x, y], rad, z) => {
      const side = Math.abs(x) > 40;
      const X = side ? Math.sign(x) * rad : x, Y = side ? y : 259 + Math.sign(y - 259) * rad;
      return root.localToWorld(new T.Vector3(X, z, -(Y - 259)));
    };
    const geo = new T.SphereGeometry(0.011, 16, 12);
    const glowTex = (() => {
      const c = document.createElement('canvas'); c.width = c.height = 64;
      const g = c.getContext('2d'), gr = g.createRadialGradient(32, 32, 0, 32, 32, 32);
      gr.addColorStop(0, 'rgba(255,255,255,1)'); gr.addColorStop(0.35, 'rgba(255,255,255,0.45)'); gr.addColorStop(1, 'rgba(255,255,255,0)');
      g.fillStyle = gr; g.fillRect(0, 0, 64, 64);
      return new T.CanvasTexture(c);
    })();
    const hitMat = new T.MeshBasicMaterial({ side: T.DoubleSide });
    LED_MM.forEach((mm, i) => {
      const hp = HALO_POS[i];
      // one lamp per LED, at the bottom of the groove: it lights the sloped plane and the wall around it
      const l = new T.PointLight(0x000000, 0, 0.22, 2);
      l.position.copy(ledWorld(mm, 53, 10.5));
      P.scene.add(l);
      // glow at the groove's exit (additive, doesn't mask anything)
      const glow = new T.Sprite(new T.SpriteMaterial({ map: glowTex, color: 0x000000, blending: T.AdditiveBlending, depthWrite: false, transparent: true, toneMapped: false }));
      glow.position.copy(ledWorld(mm, 57, 9.5)); glow.scale.setScalar(0.17);
      P.scene.add(glow);
      // clickable point in the middle of the opening, facing the LED
      const mat = new T.MeshBasicMaterial({ color: 0x222222, toneMapped: false });
      const m = new T.Mesh(geo, mat);
      m.position.copy(ledWorld(mm, 57.5, 9.5));
      m.userData.home = m.position.clone();
      m.userData.out = ledWorld(mm, 58.5, 9.5).sub(m.position).normalize();
      Object.assign(m.userData, { kind: 'halo', pos: hp });
      P.scene.add(m); P.pickables.push(m);
      P.halo[hp] = { mesh: m, mat, light: l, glow, world: ledWorld(mm, 52, 12) };
      // invisible click zone: the full slot width in front of the LED (16.7 x 8 mm), in model mm
      const side = Math.abs(mm[0]) > 40;
      const hit = new T.Mesh(new T.BoxGeometry(side ? 5 : 16.6, 8, side ? 16.6 : 5), hitMat);
      hit.visible = false;
      hit.position.set(side ? Math.sign(mm[0]) * 57 : mm[0], 9.5, side ? -(mm[1] - 259) : -(Math.sign(mm[1] - 259) * 57));
      hit.userData = { kind: 'halo', pos: hp };
      root.add(hit); P.pickables.push(hit);
    });
    // halo band: each vertex takes the color of the nearby LEDs (gaussian falloff
    // ~11 mm), like light diffused by a strip behind a diffuser
    if (P.haloBand) {
      const g = P.haloBand.geometry, pa = g.attributes.position;
      const col = new Float32Array(pa.count * 3); g.setAttribute('color', new T.BufferAttribute(col, 3));
      P.haloMat.vertexColors = true; P.haloMat.color.set(0xffffff); P.haloMat.needsUpdate = true;
      const v = new T.Vector3(), sig2 = 2 * 0.11 * 0.11;
      P.bandW = [];
      for (let q = 0; q < pa.count; q++) {
        v.fromBufferAttribute(pa, q).applyMatrix4(P.haloBand.matrixWorld);
        P.bandW.push(HALO_POS.map((hp) => Math.exp(-v.distanceToSquared(P.halo[hp].world) / sig2)));
      }
      const hb = new T.Box3().setFromObject(P.haloBand);
      P.haloFront = new T.Vector3(0, (hb.min.y + hb.max.y) / 2, hb.max.z);
    }
    P.ready = true;
    if (P.pendingFrame) setFrame(P.pendingFrame);
    opts.onReady && opts.onReady();
  }

  // ---- LEDs: same frame as the one sent to the hardware (indexed by LED id) -----------------
  const tmp = new T.Color();
  function ledColor(c, out) {
    // RGBW 0-255 -> light. An LED's PWM value is already linear light output (87/255 = 34 % of
    // its light, clearly visible on the pad): decoding it as sRGB would crush it to ~9 %. The white
    // channel adds white. Then a perceptual curve: the eye sees an LED at 30 % as clearly lit, the
    // screen has to show it that way too (hue kept, only the level is lifted).
    const w = (c[3] || 0) / 255;
    out.setRGB(Math.min(1, c[0] / 255 + w), Math.min(1, c[1] / 255 + w), Math.min(1, c[2] / 255 + w), T.LinearSRGBColorSpace);
    const m = Math.max(out.r, out.g, out.b);
    return m > 0 ? out.multiplyScalar(Math.pow(m, 0.45) / m) : out;
  }
  // calibrated layout: { keys: {pos: keyId}, leds: {ledId: pos} } (see /api/state "layout")
  function setLayout(layout) {
    P.posToKey = {}; P.keyToPos = {}; P.posToLed = {};
    for (const pos in layout.keys) { P.posToKey[pos] = Number(layout.keys[pos]); P.keyToPos[layout.keys[pos]] = pos; }
    for (const led in layout.leds) P.posToLed[layout.leds[led]] = Number(led);
  }
  // A lit LED illuminates its enclosure from the inside: the material around it (switch housing,
  // translucent cap) takes on the color instead of staying white with a colored veil.
  // So we darken the base color proportionally and keep a moderate emission --
  // too strong, the ACES tone mapping washes it out and everything turns pastel.
  const SW_BASE = new T.Color(0xdfe2e5), CAP_BASE = new T.Color(0xd9d9d4), LIT_BASE = new T.Color(0x18191b);
  function setFrame(frame) {
    if (!P.ready) { P.pendingFrame = frame; return; }
    for (const pos in P.keys) {
      const k = P.keys[pos], c = frame[P.posToLed[pos]];
      if (!c) continue;
      ledColor(c, tmp);
      const lum = Math.max(tmp.r, tmp.g, tmp.b);
      const lit = Math.min(1, lum * 1.8);
      if (k.agent) {
        k.capMat.emissive.copy(tmp); k.capMat.emissiveIntensity = 1.7;
        k.capMat.color.copy(CAP_BASE).lerp(LIT_BASE, lit * 0.97);
      }
      if (k.swMat) {
        k.swMat.emissive.copy(tmp); k.swMat.emissiveIntensity = 1.25;
        k.swMat.color.copy(SW_BASE).lerp(LIT_BASE, lit);
      }
      k.lum = lum;
    }
    // halo: each LED colors its point, its lamp and its portion of the band; the average is used for the ambiance
    let r = 0, g = 0, b = 0, n = 0;
    const cols = [];
    for (const hp of HALO_POS) {
      const c = frame[P.posToLed[hp]], h = P.halo[hp];
      const lc = new T.Color(0, 0, 0);
      if (c) { ledColor(c, lc); r += lc.r; g += lc.g; b += lc.b; n++; }
      cols.push(lc);
      if (!h) continue;
      const lum = Math.max(lc.r, lc.g, lc.b);
      h.mat.color.copy(lc).multiplyScalar(1.6).add(new T.Color(0x0c0c0c));
      h.on = lum > 0.02;                    // LED off: no black dot on the shell (except during calibration)
      h.light.color.copy(lc); h.light.intensity = lum > 0.02 ? (P.view === 'halo' ? 3.2 : 2.4) / Math.max(lum, 0.35) : 0;
      h.glow.material.color.copy(lc).multiplyScalar(1.5);
    }
    if (P.bandW) {
      const col = P.haloBand.geometry.attributes.color;
      for (let q = 0; q < P.bandW.length; q++) {
        const w = P.bandW[q]; let cr = 0.035, cg = 0.037, cb = 0.04;          // diffuser off: dark grey
        for (let i = 0; i < 12; i++) { const c = cols[i], k = w[i] * 1.5; cr += c.r * k; cg += c.g * k; cb += c.b * k; }
        col.setXYZ(q, Math.min(cr, 1.2), Math.min(cg, 1.2), Math.min(cb, 1.2));
      }
      col.needsUpdate = true;
    }
    if (n) P.ringColor = new T.Color(r / n, g / n, b / n);
  }
  function ringColorCss() {
    const c = P.ringColor; if (!c) return null;
    const s = c.clone().convertLinearToSRGB();
    return `rgb(${Math.round(s.r * 255)},${Math.round(s.g * 255)},${Math.round(s.b * 255)})`;
  }

  // ---- live feedback: press, joystick, encoder ------------------------------------------
  function flashKey(keyId, down) {
    // the key stays pressed as long as it's held (e.g. mic): we wait for the real release.
    // A brief click stays visible for at least 150 ms; 30 s safety net if a release got lost.
    const k = P.keys[P.keyToPos[keyId]] || P.btn[keyId]; if (!k) return;
    clearTimeout(k._t);
    if (down) {
      k.pressT = 1; k.t = 1; k._downAt = performance.now();
      k._t = setTimeout(() => { k.pressT = 0; k.t = 0; }, 30000);
    } else {
      const left = 150 - (performance.now() - (k._downAt || 0));
      if (left > 0) k._t = setTimeout(() => { k.pressT = 0; k.t = 0; }, left);
      else { k.pressT = 0; k.t = 0; }
    }
  }
  function setJoystick(nx, ny) { P.joy.x = nx; P.joy.y = ny; }
  function spinEncoder(delta) { P.encAngle += delta > 0 ? -0.42 : 0.42; }

  // ---- selection and framings -------------------------------------------------------------------
  function select(kind, id) {
    P.selected = kind === 'key' ? P.keyToPos[id] : null;
    if (P.selected != null) P.focusKey = P.selected;
  }
  // calibration: makes a position glow (key "r1c2" or halo LED "h4"), null to stop
  // LED calibration: the halo points grow and come out of the groove to be clickable
  function showHaloMarkers(on) { P.markersBig = !!on; }
  function highlight(pos) {
    P.highlightPos = pos;
    if (pos && pos[0] === 'r') P.focusKey = pos;
  }
  // framing based on what the user just chose: 'global', 'key' (selected key),
  // 'halo' (focus = position "hN" of an LED, otherwise the whole front halo), 'joy', 'knob'
  function setView(v, focus) {
    if (v !== P.view || focus !== P.focusHalo) { P.orbit.yaw = 0; P.orbit.pitch = 0; P.orbit.zoom = 1; }   // one view = one clean framing
    P.view = v;
    P.focusHalo = v === 'halo' ? focus || null : null;
  }

  const camPos = new T.Vector3(0, 1.4, 2.2), camTgt = new T.Vector3(0, 0.22, 0);
  const wantPos = new T.Vector3(), wantTgt = new T.Vector3();
  const orb = { s: new T.Spherical(), off: new T.Vector3(), yaw: 0, pitch: 0, zoom: 1 };
  const t0 = performance.now(); let tPrev = t0;
  function loop() {
    requestAnimationFrame(loop);
    if (!P.renderer) return;
    const now = performance.now(), t = (now - t0) / 1000, dt = Math.min(0.1, (now - tPrev) / 1000); tPrev = now;
    const ease = 1 - Math.exp(-dt * 3.2);        // ~1.2 s to arrive, regardless of the frame rate
    const kp = P.focusKey != null ? P.keyPos[P.focusKey] : null, hf = P.haloFront;
    if (P.view === 'key' && kp) { wantTgt.copy(kp); wantPos.set(kp.x + 0.2, kp.y + 0.85, kp.z + 0.78); }
    else if (P.view === 'halo' && P.focusHalo && P.halo[P.focusHalo]) {
      const m = P.halo[P.focusHalo].mesh;                                   // facing the LED, a bit above
      wantTgt.copy(m.userData.home); wantPos.copy(m.userData.home).addScaledVector(m.userData.out, 1.25); wantPos.y += 0.6;
    }
    else if ((P.view === 'joy' && P.joyPos) || (P.view === 'knob' && P.knobPos)) {
      const c = P.view === 'joy' ? P.joyPos : P.knobPos;
      wantTgt.copy(c); wantPos.set(c.x + 0.22, c.y + 0.85, c.z + 0.85);
    }
    else if (P.view === 'halo' && hf) {                                    // overview: front + right side, slight low angle
      wantTgt.set(0, hf.y, 0); wantPos.set(0.95, hf.y + 0.5, 1.45);
    }
    else { wantTgt.set(0, 0.22, 0.02); wantPos.set(Math.sin(t * 0.15) * 0.2, 1.38, 1.9); }
    camPos.lerp(wantPos, ease); camTgt.lerp(wantTgt, ease);
    // user's orbit applied after the camera move: dragging responds immediately
    const oe = 1 - Math.exp(-dt * 18);
    orb.yaw += (P.orbit.yaw - orb.yaw) * oe; orb.pitch += (P.orbit.pitch - orb.pitch) * oe; orb.zoom += (P.orbit.zoom - orb.zoom) * oe;
    orb.s.setFromVector3(orb.off.copy(camPos).sub(camTgt));
    orb.s.theta += orb.yaw;
    orb.s.phi = Math.max(0.08, Math.min(1.75, orb.s.phi - orb.pitch));
    orb.s.radius *= orb.zoom;
    // screen narrower than the desktop scene (phone in portrait): we pull back to see everything
    orb.s.radius *= Math.max(1, Math.min(2.3, 1.45 / P.cam.aspect));
    P.cam.position.copy(camTgt).add(orb.off.setFromSpherical(orb.s)); P.cam.lookAt(camTgt);
    if (P.pivot) P.pivot.position.y = P.view === 'global' ? Math.sin(t * 0.9) * 0.006 : 0;

    const pe = 1 - Math.exp(-dt * 30);
    for (const id in P.keys) {
      const k = P.keys[id]; if (!k.cap) continue;
      k.press += (k.pressT - k.press) * pe;
      k.cap.position.y = k.baseY - k.press * 3.2;              // 3.2 mm of visible travel (model units = mm)
      const sel = P.selected === id ? 0.35 : 0;
      if (P.highlightPos === id) {                                 // calibration: the key to press pulses white
        const pulse = 0.55 + 0.45 * Math.sin(t * 7);
        k.capMat.emissive.setRGB(pulse, pulse, pulse); k.capMat.emissiveIntensity = k.agent ? 1.6 : 1;
      } else if (!k.agent) k.capMat.emissive.setRGB(sel * 0.4, sel * 0.4, sel * 0.4);
      else if (sel && (k.lum || 0) < 0.05) k.capMat.emissive.setRGB(0.08, 0.08, 0.08), k.capMat.emissiveIntensity = 1;
    }
    for (const hp in P.halo) {
      const h = P.halo[hp], m = h.mesh, big = P.markersBig ? 2.4 : P.view === 'halo' ? 1.7 : 1;
      m.scale.setScalar(big * (P.highlightPos === hp || P.focusHalo === hp ? 1.5 + 0.4 * Math.sin(t * 7) : 1));
      m.position.copy(m.userData.home).addScaledVector(m.userData.out, P.markersBig ? 0.05 : 0);
      // calibration: an off LED stays visible in grey so it can be clicked
      const shown = P.markersBig || P.view === 'halo';            // calibration, or halo view: all points selectable
      if (shown && h.mat.color.r + h.mat.color.g + h.mat.color.b < 0.15) h.mat.color.set(0x5a5f66);
      m.visible = shown || !!h.on;
    }
    if (P.joyPivot) {
      P.joyPivot.rotation.x += (P.joy.y * 0.35 - P.joyPivot.rotation.x) * pe;
      P.joyPivot.rotation.z += (-P.joy.x * 0.35 - P.joyPivot.rotation.z) * pe;
    }
    if (P.knobPivot) P.knobPivot.rotation.y += (P.encAngle - P.knobPivot.rotation.y) * (1 - Math.exp(-dt * 14));
    // clicks: the joystick cap and the knob sink in as long as they're held
    for (const [id, piv] of [[110, P.joyPivot], [100, P.knobPivot]]) {
      if (!piv) continue;
      const b = P.btn[id];
      if (piv.userData.baseY === undefined) piv.userData.baseY = piv.position.y;
      b.v += (b.t - b.v) * pe;
      piv.position.y = piv.userData.baseY - b.v * b.mm;
    }
    P.renderer.render(P.scene, P.cam);
  }

  window.Pad3D = { init, setLayout, setFrame, flashKey, setJoystick, spinEncoder, select, setView, highlight, showHaloMarkers, ringColorCss };
})();
