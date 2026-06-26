
(function () {
  const API_URL = "https://rescate.ventatalk.com";
  const STEPS = [
    { field: "nombres_apellidos", question: "\u{1F464} Nombre completo de la persona desaparecida:", type: "text", placeholder: "Ej: Maria Gonzalez Perez", required: true },
    { field: "cedula", question: "\u{1F4CB} Numero de cedula (opcional):", type: "text", placeholder: "Ej: V-12345678", required: false },
    { field: "ultima_ubicacion", question: "\u{1F4CD} Ultima ubicacion conocida:", type: "text", placeholder: "Ej: Calle 5, Urb. El Valle, Caracas", required: true },
    { field: "foto", question: "\u{1F4F7} Foto reciente (opcional):", type: "file", required: false },
    { field: "descripcion", question: "\u{1F4DD} Descripcion adicional (ropa, senas, salud...):", type: "textarea", placeholder: "Cualquier detalle ayuda", required: false },
    { field: "numero_contacto", question: "\u{1F4F1} Tu numero de contacto:", type: "tel", placeholder: "Ej: +58 412 1234567", required: true },
    { field: "quien_ayudo", question: "\u{1F91D} Nombre de quien la esta ayudando o vio (opcional):", type: "text", placeholder: "Ej: Pedro Ramirez", required: false },
    { field: "contacto_quien_ayudo", question: "\u{1F4DE} Contacto de esa persona (opcional):", type: "tel", placeholder: "Ej: +58 414 7654321", required: false },
  ];

  let currentStep = 0, formData = {};

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
    #rw-file-label { display:flex; align-items:center; justify-content:center; gap:6px; width:100%; padding:10px; border:1.5px dashed #d1d5db; border-radius:10px; color:#374151; font-size:13.5px; cursor:pointer; }
    #rw-file-label:hover { border-color:#dc2626; background:#fff5f5; }
    #rw-file-preview { font-size:12px; color:#16a34a; text-align:center; }
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
    <button id="rw-fab" title="Reportar persona desaparecida">\u{1F198}<div id="rw-badge"></div></button>
    <div id="rw-panel">
      <div id="rw-header">
        <div style="font-size:26px">\u{1F534}</div>
        <div><h3>Busqueda de Desaparecidos</h3><p>Venezuela Rescate - Reporte urgente</p></div>
      </div>
      <div id="rw-progress"><div id="rw-bar"></div></div>
      <div id="rw-messages"></div>
      <div id="rw-input-area"></div>
    </div>
  `;
  document.body.appendChild(root);

  const fab=document.getElementById("rw-fab"), panel=document.getElementById("rw-panel");
  const msgs=document.getElementById("rw-messages"), inputArea=document.getElementById("rw-input-area");
  const bar=document.getElementById("rw-bar");

  fab.addEventListener("click", () => {
    const open = panel.classList.toggle("rw-open");
    if (open && msgs.children.length === 0) startChat();
  });

  function typing(ms=600) {
    return new Promise(resolve => {
      const el=document.createElement("div");
      el.className="rw-typing";
      el.innerHTML="<div class='rw-dot'></div><div class='rw-dot'></div><div class='rw-dot'></div>";
      msgs.appendChild(el); scroll();
      setTimeout(() => { msgs.removeChild(el); resolve(); }, ms);
    });
  }

  async function botMsg(html, delay=400) {
    await typing(delay);
    const el=document.createElement("div"); el.className="rw-bot"; el.innerHTML=html;
    msgs.appendChild(el); scroll();
  }

  function userMsg(text) {
    const el=document.createElement("div"); el.className="rw-user"; el.textContent=text;
    msgs.appendChild(el); scroll();
  }

  function scroll() { setTimeout(() => msgs.scrollTop=msgs.scrollHeight, 50); }
  function setProgress(s) { bar.style.width=((s/STEPS.length)*100).toFixed(0)+"%"; }

  async function startChat() {
    await botMsg("Hola. Estoy aqui para ayudarte a reportar una persona desaparecida. Te hare unas preguntas rapidas. \u{1F64F}", 700);
    setTimeout(askStep, 400);
  }

  function askStep() {
    if (currentStep >= STEPS.length) { submitForm(); return; }
    const step=STEPS[currentStep]; setProgress(currentStep);
    botMsg(step.question, 350).then(() => setTimeout(() => renderInput(step), 200));
  }

  function renderInput(step) {
    inputArea.innerHTML="";
    if (step.type==="file") {
      inputArea.innerHTML=`<input type="file" id="rw-file" accept="image/*" capture="environment" style="display:none"><label id="rw-file-label" for="rw-file">\u{1F4F7} Seleccionar foto</label><div id="rw-file-preview"></div><button class="rw-btn rw-btn-sec" id="rw-skip">Continuar sin foto</button>`;
      document.getElementById("rw-file").addEventListener("change", e => {
        const file=e.target.files[0]; if(!file) return;
        formData["foto"]=file;
        document.getElementById("rw-file-preview").textContent="\u2705 "+file.name;
        setTimeout(() => { userMsg("\u{1F4F7} "+file.name); currentStep++; askStep(); }, 400);
      });
      document.getElementById("rw-skip").addEventListener("click", skipStep);
      return;
    }
    const tag = step.type==="textarea" ? "textarea" : "input";
    const attrs = tag==="input" ? `type="${step.type}"` : "";
    inputArea.innerHTML=`<${tag} ${attrs} id="rw-input" placeholder="${step.placeholder||""}">${tag==="textarea"?"</textarea>":""}<button class="rw-btn" id="rw-next">Continuar</button>${!step.required?`<button class="rw-btn rw-btn-sec" id="rw-skip">Omitir</button>`:""}`;
    const inp=document.getElementById("rw-input");
    document.getElementById("rw-next").addEventListener("click", () => processInput(step, inp));
    const sk=document.getElementById("rw-skip"); if(sk) sk.addEventListener("click", skipStep);
    if(step.type!=="textarea") inp.addEventListener("keydown", e => { if(e.key==="Enter") processInput(step,inp); });
    setTimeout(() => inp && inp.focus(), 100);
  }

  function processInput(step, inp) {
    const value=inp?inp.value.trim():"";
    if(step.required && !value) { inp.style.borderColor="#ef4444"; inp.placeholder="\u26A0\uFE0F Campo obligatorio"; return; }
    if(value) { formData[step.field]=value; userMsg(value); }
    currentStep++; askStep();
  }

  function skipStep() { currentStep++; askStep(); }

  async function submitForm() {
    setProgress(STEPS.length); inputArea.innerHTML="";
    await botMsg("Enviando reporte... \u23F3", 500);
    try {
      const fd=new FormData();
      fd.append("nombres_apellidos", formData.nombres_apellidos||"");
      if(formData.cedula) fd.append("cedula", formData.cedula);
      fd.append("ultima_ubicacion", formData.ultima_ubicacion||"");
      if(formData.descripcion) fd.append("descripcion", formData.descripcion);
      fd.append("numero_contacto", formData.numero_contacto||"");
      if(formData.quien_ayudo) fd.append("quien_ayudo", formData.quien_ayudo);
      if(formData.contacto_quien_ayudo) fd.append("contacto_quien_ayudo", formData.contacto_quien_ayudo);
      if(formData.foto) fd.append("foto", formData.foto);
      const res=await fetch(API_URL+"/api/reportes/", {method:"POST", body:fd});
      if(!res.ok) throw new Error("Error "+res.status);
      msgs.innerHTML=""; inputArea.innerHTML=""; bar.style.width="100%";
      const ok=document.createElement("div"); ok.className="rw-success";
      ok.innerHTML=`<div class="rw-success-icon">\u2705</div><h4>Reporte enviado!</h4><p>Tu reporte esta registrado y disponible para los equipos de rescate. Que Dios los guie. \u{1F64F}\u{1F1FB}\u{1F1EA}</p>`;
      msgs.appendChild(ok);
      inputArea.innerHTML=`<button class="rw-btn rw-btn-sec" id="rw-new">Hacer otro reporte</button>`;
      document.getElementById("rw-new").addEventListener("click", () => { currentStep=0; formData={}; msgs.innerHTML=""; bar.style.width="0%"; startChat(); });
    } catch(err) {
      await botMsg("\u274C Error al enviar. Intenta de nuevo.");
      inputArea.innerHTML=`<button class="rw-btn" id="rw-retry">Reintentar</button>`;
      document.getElementById("rw-retry").addEventListener("click", submitForm);
    }
  }
})();
