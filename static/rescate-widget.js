
(function () {
  const API_URL = "https://rescate.ventatalk.com";
  let currentStep = 0, formData = {}, STEPS = [], tipoReporte = "";

  function buildSteps() {
    const tR = formData.tipo_reportante || "familiar";
    const isDesap = tipoReporte === "desaparecido";
    const ubicacionQ = !isDesap
      ? "¿Dónde está ahora mismo esta persona?"
      : tR === "familiar" ? "¿Cuál fue su última ubicación conocida?"
      : (tR === "rescatista" || tR === "personal_salud") ? "¿En qué punto de rescate o zona fue visto/a?"
      : "¿Dónde exactamente lo/la viste?";
    const estadoQ = (tR === "personal_salud" || tR === "rescatista")
      ? "¿Cuál es su estado clínico actual?"
      : "¿Cuál era su estado cuando lo/la viste por última vez?";
    const descripQ = { familiar: "¿Alguna seña particular adicional? (cicatrices, tatuajes, condiciones médicas)", personal_salud: "¿Algún detalle clínico relevante?", rescatista: "¿Algún detalle operacional relevante?", testigo: "¿Qué más recuerdas de cuando lo/la viste?" }[tR] || "¿Alguna seña particular adicional?";
    return [
      { field: "nombres_apellidos", question: "¿Cuál es el nombre completo de la persona?", subtext: "Si no lo sabes escribe 'Desconocido'", type: "text", placeholder: "Ej: Maria Gonzalez Perez", required: true },
      { field: "_doc_choice", type: "doc-choice", question: "¿Tienes información sobre su cédula de identidad?" },
      { field: "sexo", type: "buttons", question: "Descripción física — <small style='color:#9ca3af'>Sexo</small>", buttons: [{ label: "Masculino", value: "masculino" }, { label: "Femenino", value: "femenino" }, { label: "No det.", value: "no_determinado" }] },
      { field: "edad_aproximada", type: "buttons", question: "<small style='color:#9ca3af'>Edad aproximada</small>", buttons: [{ label: "Niño 0-12", value: "nino" }, { label: "Joven 13-25", value: "joven" }, { label: "Adulto 26-60", value: "adulto" }, { label: "Mayor 60+", value: "adulto_mayor" }, { label: "No sé", value: "no_sabe" }] },
      { field: "contextura", type: "buttons", question: "<small style='color:#9ca3af'>Contextura</small>", buttons: [{ label: "Delgada", value: "delgada" }, { label: "Media", value: "media" }, { label: "Robusta", value: "robusta" }, { label: "No sé", value: "no_sabe" }] },
      { field: "cabello", type: "buttons", question: "<small style='color:#9ca3af'>Cabello</small>", buttons: [{ label: "Corto", value: "corto" }, { label: "Largo", value: "largo" }, { label: "Calvo/a", value: "calvo" }, { label: "Canoso/a", value: "canoso" }, { label: "No sé", value: "no_sabe" }] },
      { field: "ropa_aproximada", type: "text", question: "¿Qué ropa llevaba? (opcional)", placeholder: "Ej: Camisa azul, pantalón negro", required: false },
      { field: "estado_clinico", type: "clinico", question: estadoQ },
      { field: "ultima_ubicacion", type: "text", question: ubicacionQ, placeholder: "Ej: Av. Principal, Urb. El Valle", required: true },
      { field: "foto", type: "file", question: "📷 Foto reciente (opcional):", required: false },
      { field: "descripcion", type: "textarea", question: descripQ, placeholder: "Cualquier detalle ayuda", required: false },
      { field: "numero_contacto", type: "tel", question: "¿A qué número podemos contactarte?", placeholder: "+58 412 1234567", maxlength: 15, required: true },
      { field: "quien_ayudo", type: "text", question: "¿Hay alguien más con información? ¿Nombre? (opcional)", placeholder: "Ej: Pedro Ramírez", required: false },
      { field: "contacto_quien_ayudo", type: "tel", question: "¿Contacto de esa persona? (opcional)", placeholder: "+58 414 7654321", maxlength: 15, required: false },
    ];
  }

  const css = `
    #rw-root * { box-sizing:border-box; font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif; margin:0; padding:0; }
    #rw-fab { position:fixed; bottom:24px; right:24px; z-index:99998; width:64px; height:64px; border-radius:50%; background:#dc2626; border:none; cursor:pointer; box-shadow:0 4px 24px rgba(220,38,38,.5); display:flex; align-items:center; justify-content:center; font-size:28px; transition:transform .2s; }
    #rw-fab:hover { transform:scale(1.1); }
    #rw-badge { position:absolute; top:-4px; right:-4px; width:18px; height:18px; border-radius:50%; background:#fbbf24; border:2px solid white; animation:rw-pulse 2s ease-in-out infinite; }
    @keyframes rw-pulse { 0%,100%{transform:scale(1)} 50%{transform:scale(1.35)} }
    #rw-panel { position:fixed; bottom:100px; right:24px; width:370px; max-height:580px; background:#fff; border-radius:18px; box-shadow:0 10px 50px rgba(0,0,0,.22); display:none; flex-direction:column; z-index:99997; overflow:hidden; }
    #rw-panel.rw-open { display:flex; }
    #rw-header { background:linear-gradient(135deg,#dc2626,#b91c1c); color:white; padding:14px 18px; display:flex; align-items:center; gap:10px; }
    #rw-header h3 { font-size:14px; font-weight:700; }
    #rw-header p { font-size:11px; opacity:.85; margin-top:2px; }
    #rw-progress { height:3px; background:#fecaca; }
    #rw-bar { height:100%; background:#dc2626; transition:width .4s; width:0%; }
    #rw-messages { flex:1; overflow-y:auto; padding:16px 14px; display:flex; flex-direction:column; gap:10px; }
    .rw-bot,.rw-user { max-width:88%; padding:10px 14px; border-radius:14px; font-size:13.5px; line-height:1.55; }
    .rw-bot { background:#f3f4f6; color:#111; border-bottom-left-radius:4px; align-self:flex-start; }
    .rw-user { background:#dc2626; color:white; border-bottom-right-radius:4px; align-self:flex-end; }
    .rw-typing { display:flex; gap:4px; align-items:center; padding:12px 14px; background:#f3f4f6; border-radius:14px; border-bottom-left-radius:4px; align-self:flex-start; max-width:88%; }
    .rw-dot { width:7px; height:7px; border-radius:50%; background:#9ca3af; animation:rw-bounce 1.2s ease-in-out infinite; }
    .rw-dot:nth-child(2){animation-delay:.2s}.rw-dot:nth-child(3){animation-delay:.4s}
    @keyframes rw-bounce { 0%,80%,100%{transform:translateY(0)} 40%{transform:translateY(-8px)} }
    #rw-input-area { padding:10px 14px 14px; border-top:1px solid #f0f0f0; display:flex; flex-direction:column; gap:6px; }
    #rw-input-area input,#rw-input-area textarea { width:100%; border:1.5px solid #d1d5db; border-radius:10px; padding:10px 12px; font-size:14px; outline:none; resize:none; color:#111; background:#fafafa; }
    #rw-input-area input:focus,#rw-input-area textarea:focus { border-color:#dc2626; background:#fff; }
    #rw-input-area textarea { height:75px; }
    .rw-btn { width:100%; padding:10px 14px; background:#dc2626; color:white; border:none; border-radius:10px; font-size:13.5px; font-weight:600; cursor:pointer; }
    .rw-btn:hover { background:#b91c1c; }
    .rw-btn-sec { background:transparent; color:#6b7280; border:1.5px solid #e5e7eb; }
    .rw-btn-sec:hover { background:#f9fafb; }
    .rw-btn-tipo-des { width:100%; padding:12px; background:#dc2626; color:white; border:none; border-radius:10px; font-size:13px; font-weight:700; cursor:pointer; text-align:left; line-height:1.4; transition:background .15s; }
    .rw-btn-tipo-des:hover { background:#b91c1c; }
    .rw-btn-tipo-enc { width:100%; padding:12px; background:#16a34a; color:white; border:none; border-radius:10px; font-size:13px; font-weight:700; cursor:pointer; text-align:left; line-height:1.4; transition:background .15s; }
    .rw-btn-tipo-enc:hover { background:#15803d; }
    .rw-btn-grid { display:flex; gap:6px; flex-wrap:wrap; }
    .rw-btn-opt { display:block; width:100%; padding:10px 14px; background:white; color:#dc2626; border:2px solid #dc2626; border-radius:8px; font-size:13px; font-weight:500; cursor:pointer; text-align:left; margin-bottom:6px; transition:background .15s, color .15s; }
    .rw-btn-opt:hover { background:#dc2626; color:white; }
    .rw-btn-fallecido { width:100%; padding:8px 10px; margin-top:4px; background:transparent; color:#9ca3af; border:1.5px solid #e5e7eb; border-radius:8px; font-size:12px; font-weight:600; cursor:pointer; transition:all .15s; }
    .rw-btn-fallecido:hover { border-color:#9ca3af; color:#374151; }
    .rw-success { text-align:center; padding:28px 20px; display:flex; flex-direction:column; align-items:center; gap:10px; }
    .rw-success-icon { font-size:54px; }
    .rw-success h4 { font-size:16px; color:#16a34a; font-weight:700; }
    .rw-success p { font-size:13px; color:#6b7280; line-height:1.6; max-width:280px; }
    @media(max-width:420px){#rw-panel{width:calc(100vw - 24px);right:12px;bottom:86px;}#rw-fab{bottom:16px;right:16px;}}
  `;

  const styleEl = document.createElement("style");
  styleEl.textContent = css;
  document.head.appendChild(styleEl);

  const root = document.createElement("div");
  root.id = "rw-root";
  root.innerHTML = `
    <button id="rw-fab" title="Reportar persona desaparecida">🆘<div id="rw-badge"></div></button>
    <div id="rw-panel">
      <div id="rw-header">
        <div style="font-size:26px">🔴</div>
        <div><h3>Búsqueda de Desaparecidos</h3><p>Venezuela Rescate — Reporte urgente</p></div>
      </div>
      <div id="rw-progress"><div id="rw-bar"></div></div>
      <div id="rw-messages"></div>
      <div id="rw-input-area"></div>
    </div>
  `;
  document.body.appendChild(root);

  const fab = document.getElementById("rw-fab");
  const panel = document.getElementById("rw-panel");
  const msgs = document.getElementById("rw-messages");
  const inputArea = document.getElementById("rw-input-area");
  const bar = document.getElementById("rw-bar");

  fab.addEventListener("click", () => {
    const open = panel.classList.toggle("rw-open");
    if (open && msgs.children.length === 0) startChat();
  });

  function typing(ms = 600) {
    return new Promise(resolve => {
      const el = document.createElement("div");
      el.className = "rw-typing";
      el.innerHTML = "<div class='rw-dot'></div><div class='rw-dot'></div><div class='rw-dot'></div>";
      msgs.appendChild(el); scroll();
      setTimeout(() => { msgs.removeChild(el); resolve(); }, ms);
    });
  }

  async function botMsg(html, delay = 400) {
    await typing(delay);
    const el = document.createElement("div"); el.className = "rw-bot"; el.innerHTML = html;
    msgs.appendChild(el); scroll();
  }

  function userMsg(text) {
    const el = document.createElement("div"); el.className = "rw-user"; el.textContent = text;
    msgs.appendChild(el); scroll();
  }

  function scroll() { setTimeout(() => msgs.scrollTop = msgs.scrollHeight, 50); }
  function setProgress(s) { bar.style.width = ((s / (STEPS.length || 1)) * 100).toFixed(0) + "%"; }

  async function startChat() {
    await botMsg("¿Cuál es tu rol en esta situación?", 700);
    renderTipoReportanteButtons();
  }

  function renderTipoReportanteButtons() {
    inputArea.innerHTML = "";
    [
      { label: "👤 Familiar o conocido", value: "familiar" },
      { label: "🏥 Personal de salud",   value: "personal_salud" },
      { label: "🦺 Rescatista",           value: "rescatista" },
      { label: "👁️ Testigo",        value: "testigo" },
    ].forEach(op => {
      const b = document.createElement("button");
      b.className = "rw-btn"; b.style.textAlign = "left"; b.textContent = op.label;
      b.addEventListener("click", () => seleccionarTipoReportante(op.value, op.label));
      inputArea.appendChild(b);
    });
    scroll();
  }

  async function seleccionarTipoReportante(valor, label) {
    formData.tipo_reportante = valor;
    userMsg(label);
    inputArea.innerHTML = "";
    await botMsg("Entendido. ¿Qué deseas reportar?", 500);
    renderTipoButtons();
  }

  function renderTipoButtons() {
    inputArea.innerHTML = "";
    const btnDes = document.createElement("button");
    btnDes.className = "rw-btn-tipo-des";
    btnDes.textContent = "🔴 Reportar desaparecido";
    btnDes.addEventListener("click", () => seleccionarTipo("desaparecido"));
    const btnEnc = document.createElement("button");
    btnEnc.className = "rw-btn-tipo-enc";
    btnEnc.textContent = "🟢 Reportar persona encontrada";
    btnEnc.addEventListener("click", () => seleccionarTipo("encontrado_vivo"));
    inputArea.appendChild(btnDes);
    inputArea.appendChild(btnEnc);
    scroll();
  }

  async function seleccionarTipo(tipo) {
    tipoReporte = tipo;
    inputArea.innerHTML = "";
    userMsg(tipo === "desaparecido" ? "🔴 Desaparecido" : "🟢 Persona encontrada");
    await botMsg(tipo === "desaparecido"
      ? "Entendido. Te haré unas preguntas rápidas. 🙏"
      : "Gracias por reportar. Te haré unas preguntas. 🙏", 500);
    STEPS = buildSteps();
    currentStep = 0;
    setTimeout(askStep, 300);
  }

  function askStep() {
    if (currentStep >= STEPS.length) { submitForm(); return; }
    const step = STEPS[currentStep];
    setProgress(currentStep);
    botMsg(step.question, 350).then(() => setTimeout(() => renderInput(step), 200));
  }

  function renderInput(step) {
    inputArea.innerHTML = "";
    if (step.type === "file")       { renderFotoPanel(); return; }
    if (step.type === "buttons")    { renderButtonStep(step); return; }
    if (step.type === "doc-choice") { renderDocChoice(); return; }
    if (step.type === "clinico")    { renderClinico(); return; }
    renderTextInput(step);
  }

  function renderButtonStep(step) {
    const grid = document.createElement("div");
    grid.className = "rw-btn-grid";
    step.buttons.forEach(opt => {
      const b = document.createElement("button");
      b.className = "rw-btn-opt"; b.textContent = opt.label;
      b.addEventListener("click", () => {
        formData[step.field] = opt.value;
        userMsg(opt.label); currentStep++; askStep();
      });
      grid.appendChild(b);
    });
    inputArea.appendChild(grid);
  }

  function renderDocChoice() {
    const btnSi = document.createElement("button");
    btnSi.className = "rw-btn"; btnSi.textContent = "Sí, tengo la cédula";
    btnSi.addEventListener("click", handleDocSi);
    const btnNo = document.createElement("button");
    btnNo.className = "rw-btn rw-btn-sec"; btnNo.textContent = "No tiene documentos / No la sé";
    btnNo.addEventListener("click", () => {
      userMsg("No tiene documentos / No la sé");
      formData.sin_documentos = true;
      currentStep++; askStep();
    });
    inputArea.appendChild(btnSi);
    inputArea.appendChild(btnNo);
  }

  async function handleDocSi() {
    inputArea.innerHTML = "";
    userMsg("Sí, tengo la cédula");
    await botMsg("¿Cuál es el número de cédula?", 350);
    inputArea.innerHTML = `
      <input type="text" id="rw-input" placeholder="Ej: V-12345678" maxlength="15">
      <button class="rw-btn" id="rw-next">Continuar</button>
      <button class="rw-btn rw-btn-sec" id="rw-skip">No recuerdo el número</button>`;
    const inp = document.getElementById("rw-input");
    const dispatch = () => cedulaCheck({ field: "cedula" }, inp);
    document.getElementById("rw-next").addEventListener("click", dispatch);
    document.getElementById("rw-skip").addEventListener("click", () => { currentStep++; askStep(); });
    inp.addEventListener("keydown", e => { if (e.key === "Enter") dispatch(); });
    setTimeout(() => inp && inp.focus(), 100);
  }

  function renderClinico() {
    const grid = document.createElement("div");
    grid.className = "rw-btn-grid";
    [
      { label: "Consciente · Estable",  value: "consciente_estable" },
      { label: "Consciente · Herido/a", value: "consciente_herido" },
      { label: "Inconsciente",               value: "inconsciente" },
      { label: "Desconocido",                value: "desconocido" },
    ].forEach(opt => {
      const b = document.createElement("button");
      b.className = "rw-btn-opt"; b.textContent = opt.label;
      b.addEventListener("click", () => {
        formData.estado_clinico = opt.value;
        userMsg(opt.label); currentStep++; askStep();
      });
      grid.appendChild(b);
    });
    inputArea.appendChild(grid);
    const fallBtn = document.createElement("button");
    fallBtn.className = "rw-btn-fallecido"; fallBtn.textContent = "Fallecido/a"; fallBtn.style.display = "none";
    fallBtn.addEventListener("click", () => {
      if (confirm("¿Confirmas que la persona ha fallecido? Esta información es sensible.")) {
        formData.estado_clinico = "fallecido";
        userMsg("Fallecido/a"); currentStep++; askStep();
      }
    });
    inputArea.appendChild(fallBtn);
  }

  function renderTextInput(step) {
    const tag = step.type === "textarea" ? "textarea" : "input";
    const attrs = tag === "input" ? `type="${step.type}"${step.maxlength ? ` maxlength="${step.maxlength}"` : ""}` : "";
    const subtextHtml = step.subtext ? `<div style="font-size:11px;color:#9ca3af;padding:0 2px 2px;">${step.subtext}</div>` : "";
    inputArea.innerHTML = `${subtextHtml}<${tag} ${attrs} id="rw-input" placeholder="${step.placeholder || ""}">${tag === "textarea" ? "</textarea>" : ""}<button class="rw-btn" id="rw-next">Continuar</button>${!step.required ? `<button class="rw-btn rw-btn-sec" id="rw-skip">Omitir</button>` : ""}`;
    const inp = document.getElementById("rw-input");
    document.getElementById("rw-next").addEventListener("click", () => processInput(step, inp));
    const sk = document.getElementById("rw-skip"); if (sk) sk.addEventListener("click", skipStep);
    if (step.type !== "textarea") inp.addEventListener("keydown", e => { if (e.key === "Enter") processInput(step, inp); });
    setTimeout(() => inp && inp.focus(), 100);
  }

  function renderFotoPanel() {
    const fotosArr = [];
    const MAX_FOTOS = 3;
    function renderFotoUI() {
      inputArea.innerHTML = "";
      if (fotosArr.length > 0) {
        const wrap = document.createElement("div");
        wrap.style.cssText = "display:flex;gap:6px;flex-wrap:wrap;";
        fotosArr.forEach(f => {
          const img = document.createElement("img");
          img.src = URL.createObjectURL(f);
          img.style.cssText = "width:50px;height:50px;object-fit:cover;border-radius:6px;border:2px solid #dc2626;";
          wrap.appendChild(img);
        });
        inputArea.appendChild(wrap);
        const cnt = document.createElement("div");
        cnt.style.cssText = "font-size:12px;color:#16a34a;";
        cnt.textContent = `✅ ${fotosArr.length}/${MAX_FOTOS} foto(s)`;
        inputArea.appendChild(cnt);
      }
      if (fotosArr.length < MAX_FOTOS) {
        const camId = "rw-fc-" + fotosArr.length, galId = "rw-fg-" + fotosArr.length;
        const small = fotosArr.length > 0;
        const camIn = document.createElement("input");
        camIn.type = "file"; camIn.accept = "image/*"; camIn.capture = "environment";
        camIn.id = camId; camIn.style.display = "none";
        inputArea.appendChild(camIn);
        const galIn = document.createElement("input");
        galIn.type = "file"; galIn.accept = "image/*";
        galIn.id = galId; galIn.style.display = "none";
        inputArea.appendChild(galIn);
        const camLbl = document.createElement("label");
        camLbl.htmlFor = camId; camLbl.className = "rw-btn";
        camLbl.style.cssText = small ? "flex:1;display:block;text-align:center;font-size:12px;padding:8px 6px;" : "display:block;text-align:center;";
        camLbl.textContent = small ? "+ 📷" : "📷 Tomar foto";
        const galLbl = document.createElement("label");
        galLbl.htmlFor = galId; galLbl.className = "rw-btn rw-btn-sec";
        galLbl.style.cssText = small ? "flex:1;display:block;text-align:center;font-size:12px;padding:8px 6px;" : "display:block;text-align:center;";
        galLbl.textContent = small ? "+ 🖼️" : "🖼️ Adjuntar foto";
        if (small) {
          const row = document.createElement("div");
          row.style.cssText = "display:flex;gap:6px;";
          row.appendChild(camLbl); row.appendChild(galLbl);
          inputArea.appendChild(row);
        } else {
          inputArea.appendChild(camLbl); inputArea.appendChild(galLbl);
        }
        [camIn, galIn].forEach(inp => {
          inp.addEventListener("change", e => { const f = e.target.files[0]; if (!f) return; fotosArr.push(f); renderFotoUI(); });
        });
      }
      if (fotosArr.length > 0) {
        const listoBtn = document.createElement("button");
        listoBtn.className = "rw-btn"; listoBtn.textContent = "Listo con las fotos";
        listoBtn.addEventListener("click", () => {
          formData.fotos = fotosArr.slice();
          userMsg("📷 " + fotosArr.length + " foto(s)");
          currentStep++; askStep();
        });
        inputArea.appendChild(listoBtn);
      } else {
        const skipBtn = document.createElement("button");
        skipBtn.className = "rw-btn rw-btn-sec"; skipBtn.textContent = "Continuar sin foto";
        skipBtn.addEventListener("click", skipStep);
        inputArea.appendChild(skipBtn);
      }
    }
    renderFotoUI();
  }

  async function cedulaCheck(step, inp) {
    const value = inp ? inp.value.trim() : "";
    if (!value) { currentStep++; askStep(); return; }
    try {
      const res = await fetch(API_URL + `/api/check-cedula?cedula=${encodeURIComponent(value)}`);
      const data = await res.json();
      if (data.existe) {
        userMsg(value);
        formData[step.field] = value;
        inputArea.innerHTML = "";
        await botMsg("⚠️ Ya hay un registro con esa cédula. Puedes continuar de todas formas.", 400);
        inputArea.innerHTML = `<button class="rw-btn" id="rw-ced-ok">Continuar</button><button class="rw-btn rw-btn-sec" id="rw-ced-no">Cancelar</button>`;
        document.getElementById("rw-ced-ok").addEventListener("click", () => { currentStep++; askStep(); });
        document.getElementById("rw-ced-no").addEventListener("click", () => { delete formData[step.field]; handleDocSi(); });
        return;
      }
    } catch (e) { /* continúa si falla */ }
    processInput(step, inp);
  }

  function processInput(step, inp) {
    const value = inp ? inp.value.trim() : "";
    if (step.required && !value) { inp.style.borderColor = "#ef4444"; inp.placeholder = "⚠️ Obligatorio"; return; }
    if (value) { formData[step.field] = value; userMsg(value); }
    currentStep++; askStep();
  }

  function skipStep() { currentStep++; askStep(); }

  async function submitForm() {
    setProgress(STEPS.length); inputArea.innerHTML = "";
    await botMsg("Enviando reporte... ⏳", 500);
    try {
      const fd = new FormData();
      fd.append("nombres_apellidos", formData.nombres_apellidos || "");
      if (formData.cedula) fd.append("cedula", formData.cedula);
      fd.append("ultima_ubicacion", formData.ultima_ubicacion || "");
      if (formData.descripcion) fd.append("descripcion", formData.descripcion);
      fd.append("numero_contacto", formData.numero_contacto || "");
      if (formData.quien_ayudo) fd.append("quien_ayudo", formData.quien_ayudo);
      if (formData.contacto_quien_ayudo) fd.append("contacto_quien_ayudo", formData.contacto_quien_ayudo);
      fd.append("tipo_reporte", tipoReporte);
      if (formData.tipo_reportante) fd.append("tipo_reportante", formData.tipo_reportante);
      if (formData.sexo) fd.append("sexo", formData.sexo);
      if (formData.edad_aproximada) fd.append("edad_aproximada", formData.edad_aproximada);
      if (formData.contextura) fd.append("contextura", formData.contextura);
      if (formData.cabello) fd.append("cabello", formData.cabello);
      if (formData.ropa_aproximada) fd.append("ropa_aproximada", formData.ropa_aproximada);
      if (formData.estado_clinico) fd.append("estado_clinico", formData.estado_clinico);
      fd.append("sin_documentos", formData.sin_documentos ? "true" : "false");
      if (formData.fotos) {
        if (formData.fotos[0]) fd.append("foto", formData.fotos[0]);
        if (formData.fotos[1]) fd.append("foto_2", formData.fotos[1]);
        if (formData.fotos[2]) fd.append("foto_3", formData.fotos[2]);
      }
      const res = await fetch(API_URL + "/api/reportes/", { method: "POST", body: fd });
      if (!res.ok) throw new Error("Error " + res.status);
      msgs.innerHTML = ""; inputArea.innerHTML = ""; bar.style.width = "100%";
      const ok = document.createElement("div"); ok.className = "rw-success";
      ok.innerHTML = `<div class="rw-success-icon">✅</div><h4>¡Reporte enviado!</h4><p>${tipoReporte === "encontrado_vivo" ? "Los equipos ubicarán a esta persona con su familia." : "Disponible para los equipos de rescate."} Que Dios los guíe. 🙏🇺🇪</p>`;
      msgs.appendChild(ok);
      inputArea.innerHTML = `<button class="rw-btn rw-btn-sec" id="rw-new">Hacer otro reporte</button>`;
      document.getElementById("rw-new").addEventListener("click", () => {
        currentStep = 0; formData = {}; tipoReporte = ""; STEPS = [];
        msgs.innerHTML = ""; bar.style.width = "0%"; startChat();
      });
    } catch (err) {
      await botMsg("❌ Error al enviar. Intenta de nuevo.");
      inputArea.innerHTML = `<button class="rw-btn" id="rw-retry">Reintentar</button>`;
      document.getElementById("rw-retry").addEventListener("click", submitForm);
    }
  }
})();
