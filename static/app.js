"use strict";
/* Integra Bling — front-end (JavaScript puro). */
const $ = (s, el = document) => el.querySelector(s);
const $$ = (s, el = document) => [...el.querySelectorAll(s)];
const esc = (v) => String(v ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
const brl = (v) => v == null ? "" : Number(v).toLocaleString("pt-BR", { minimumFractionDigits: 2, maximumFractionDigits: 2 });
const brl4 = (v) => v == null ? "" : Number(v).toLocaleString("pt-BR", { minimumFractionDigits: 2, maximumFractionDigits: 4 });
const qtdBR = (v) => v == null ? "" : Number(v).toLocaleString("pt-BR", { maximumFractionDigits: 3 });
const pct = (v) => v == null ? "" : `${Number(v).toLocaleString("pt-BR", { minimumFractionDigits: 1, maximumFractionDigits: 1 })}%`;
const mesBR = (m) => m ? `${m.slice(5, 7)}/${m.slice(0, 4)}` : "";
const dataBR = (d) => d ? `${d.slice(8, 10)}/${d.slice(5, 7)}/${d.slice(0, 4)}` : "";
const dataHora = (d) => d ? `${dataBR(d)} ${d.slice(11, 16)}` : "—";
const cnpjFmt = (c) => !c ? "" : c.length === 14 ? c.replace(/^(\d{2})(\d{3})(\d{3})(\d{4})(\d{2})$/, "$1.$2.$3/$4-$5") : c.replace(/^(\d{3})(\d{3})(\d{3})(\d{2})$/, "$1.$2.$3-$4");
const ORIGEM = { BLING: '<span class="badge b-bling">Bling</span>', DOMINIO: '<span class="badge b-dom">Domínio</span>', MLIVRE: '<span class="badge b-ml">Mercado Livre</span>' };
const TIPO = { R: "Receita", D: "Despesa", RD: "Receita e despesa" };
let EU = null;
let pendenteModelo = null;  // grava o modelo em edição antes de trocar de tela

async function api(caminho, opt = {}) {
  const o = { method: opt.method || "GET", headers: {} };
  if (opt.body !== undefined) { o.body = JSON.stringify(opt.body); o.headers["Content-Type"] = "application/json"; }
  const r = await fetch(caminho, o);
  let d = null;
  try { d = await r.json(); } catch (_) { d = null; }
  if (r.status === 401 && !caminho.startsWith("/api/login")) { EU = null; location.hash = "#/login"; throw new Error("Faça login."); }
  if (!r.ok) throw new Error((d && (d.erro || d.detail)) || `Erro ${r.status}`);
  return d;
}

function toast(msg, erro = false) {
  const d = document.createElement("div");
  d.textContent = msg;
  if (erro) d.className = "erro";
  $("#toast").appendChild(d);
  setTimeout(() => d.remove(), erro ? 7000 : 3500);
}

function modal(html, estreito = false) {
  const f = document.createElement("div");
  f.className = "modal-fundo";
  f.innerHTML = `<div class="modal ${estreito ? "estreito" : ""}">${html}</div>`;
  f.addEventListener("click", (e) => { if (e.target === f || e.target.closest("[data-fechar]")) f.remove(); });
  document.body.appendChild(f);
  return f;
}

async function tentar(fn, el) {
  if (el) el.disabled = true;
  try { return await fn(); } catch (e) { toast(e.message, true); } finally { if (el) el.disabled = false; }
}

// ------------------------------------------------------------------ período (guardado no navegador)
function periodoPadrao() {
  const h = new Date();
  const ult = new Date(h.getFullYear(), h.getMonth(), 0);
  const fim = `${ult.getFullYear()}-${String(ult.getMonth() + 1).padStart(2, "0")}`;
  return { inicio: `${ult.getFullYear()}-01`, fim };
}
function periodo() {
  try { return { ...periodoPadrao(), ...JSON.parse(localStorage.getItem("ib_periodo") || "{}") }; } catch (_) { return periodoPadrao(); }
}
function salvarPeriodo(p) { try { localStorage.setItem("ib_periodo", JSON.stringify(p)); } catch (_) { /* sem storage */ } }
function ultimoDia(mes) { const [a, m] = mes.split("-").map(Number); return `${mes}-${String(new Date(a, m, 0).getDate()).padStart(2, "0")}`; }

function seletorPeriodo(aoMudar) {
  const p = periodo();
  const html = `<div class="linha">
    <label>De <input type="month" id="p-ini" value="${p.inicio}"></label>
    <label>Até <input type="month" id="p-fim" value="${p.fim}"></label>
  </div>`;
  setTimeout(() => {
    ["#p-ini", "#p-fim"].forEach((id) => {
      const el = $(id);
      if (el) el.addEventListener("change", () => {
        const n = { inicio: $("#p-ini").value, fim: $("#p-fim").value };
        if (!n.inicio || !n.fim) return;
        salvarPeriodo(n);
        aoMudar(n);
      });
    });
  });
  return html;
}

// ------------------------------------------------------------------ estrutura
function casca(ativo, corpo) {
  const it = [["empresas", "Empresas"], ["modelos", "Modelos de DRE"]];
  if (EU && EU.perfil === "ADMIN") it.push(["config", "Configurações"]);
  $("#app").innerHTML = `<div class="layout">
    <nav class="lateral">
      <div class="marca"><svg width="30" height="30" viewBox="0 0 32 32"><rect width="32" height="32" rx="7" fill="#fff" fill-opacity=".14"/><text x="16" y="21" text-anchor="middle" font-size="13" font-weight="700" fill="#fff" font-family="Segoe UI,Arial">IB</text><path d="M7 26h18" stroke="#34C3A0" stroke-width="2.2"/></svg>
        <div>Integra Bling<small>Alta Visão Contabilidade</small></div></div>
      ${it.map(([h, t]) => `<a href="#/${h}" class="${ativo === h ? "ativo" : ""}">${t}</a>`).join("")}
      <div class="rodape">${esc(EU ? EU.nome : "")}<br><a href="#" id="trocar-senha">Trocar senha</a> · <a href="#" id="sair">Sair</a></div>
    </nav>
    <main class="conteudo" id="conteudo">${corpo}</main></div>`;
  $("#sair").onclick = async (e) => { e.preventDefault(); await api("/api/logout", { method: "POST" }); EU = null; location.hash = "#/login"; };
  $("#trocar-senha").onclick = (e) => { e.preventDefault(); telaSenha(); };
}

function telaSenha() {
  const m = modal(`<h2>Trocar senha</h2><div class="form" style="grid-template-columns:1fr">
    <label>Senha atual <input type="password" id="s-atual"></label>
    <label>Nova senha (mínimo 8 caracteres) <input type="password" id="s-nova"></label></div>
    <div class="linha" style="margin-top:14px"><button class="btn pri" id="s-ok">Salvar</button><button class="btn" data-fechar>Cancelar</button></div>`, true);
  $("#s-ok", m).onclick = (e) => tentar(async () => {
    await api("/api/senha", { method: "POST", body: { atual: $("#s-atual", m).value, nova: $("#s-nova", m).value } });
    m.remove(); toast("Senha alterada.");
  }, e.target);
}

// ------------------------------------------------------------------ login e primeiro acesso
function telaLogin(setup) {
  $("#app").innerHTML = `<div class="login"><div class="card">
    <div class="marca" style="color:var(--azul);padding:0 0 14px"><div><b style="font-size:20px">Integra Bling</b><br><span class="suave peq">DRE multiempresa · Bling + Domínio</span></div></div>
    ${setup ? `<div class="info">Primeiro acesso: crie o usuário administrador.</div><label>Nome <input id="l-nome"></label><br>` : ""}
    <label>E-mail <input id="l-email" type="email" autocomplete="username"></label><br>
    <label>Senha <input id="l-senha" type="password" autocomplete="${setup ? "new-password" : "current-password"}"></label><br>
    <button class="btn pri" id="l-ok" style="width:100%;justify-content:center">${setup ? "Criar administrador" : "Entrar"}</button>
  </div></div>`;
  const ir = (e) => tentar(async () => {
    const body = { email: $("#l-email").value, senha: $("#l-senha").value };
    if (setup) body.nome = $("#l-nome").value;
    EU = await api(setup ? "/api/setup" : "/api/login", { method: "POST", body });
    location.hash = "#/empresas";
    rota();
  }, e && e.target);
  $("#l-ok").onclick = ir;
  $("#l-senha").addEventListener("keydown", (e) => { if (e.key === "Enter") ir(); });
}

// ------------------------------------------------------------------ empresas
async function telaEmpresas() {
  casca("empresas", `<div class="topo"><div><h1>Empresas</h1><div class="suave">Cada empresa tem a própria conexão com o Bling e a própria associação de contas.</div></div>
    <button class="btn pri" id="nova">+ Nova empresa</button></div><div id="lista" class="grade"><div class="vazio">Carregando...</div></div>`);
  $("#nova").onclick = () => formEmpresa();
  const lista = await api("/api/empresas");
  $("#lista").innerHTML = lista.length ? lista.map((e) => {
    const b = e.contas.BLING || { total: 0, associadas: 0 };
    return `<div class="card emp" data-id="${e.id}">
      <div class="linha entre"><h3 style="margin:0">${esc(e.nome)}</h3>${e.ativo ? "" : '<span class="badge">inativa</span>'}</div>
      <div class="suave peq">${cnpjFmt(e.cnpj) || "sem CNPJ"} · Domínio ${esc(e.codigo_dominio || "—")} · ${esc(e.modelo_nome || "sem modelo")}</div>
      <div class="linha" style="margin-top:10px">
        ${e.bling_conectado ? '<span class="badge b-ok">Bling conectado</span>' : '<span class="badge b-aviso">Bling não conectado</span>'}
        ${e.dominio_sync_em ? '<span class="badge b-dom">Domínio lida</span>' : ""}
        ${e.ml_conectado ? '<span class="badge b-ml">Mercado Livre</span>' : ""}
        <span class="badge">${b.associadas}/${b.total} categorias associadas</span>
      </div>
      ${e.ultimo_job ? `<div class="suave peq" style="margin-top:8px">${statusJob(e.ultimo_job)} ${esc(e.ultimo_job.mensagem).slice(0, 120)}</div>` : ""}
    </div>`;
  }).join("") : `<div class="card vazio">Nenhuma empresa ainda. Clique em <b>Nova empresa</b>.</div>`;
  $$(".emp").forEach((c) => c.onclick = () => { location.hash = `#/empresa/${c.dataset.id}`; });
}

function statusJob(j) {
  const cls = { OK: "b-ok", ERRO: "b-erro", EXECUTANDO: "b-aviso", PENDENTE: "b-aviso" }[j.status] || "";
  const nome = { bling_sync: "Bling", dominio_importar: "Domínio", ml_sync: "Mercado Livre" }[j.tipo] || j.tipo;
  const st = { OK: "concluído", ERRO: "erro", EXECUTANDO: "em andamento", PENDENTE: "na fila" }[j.status] || j.status;
  return `<span class="badge ${cls}">${nome}: ${st}</span>`;
}

async function formEmpresa(e) {
  const modelos = await api("/api/modelos");
  const m = modal(`<h2>${e ? "Editar empresa" : "Nova empresa"}</h2>
    ${e ? "" : `<div class="info"><b>Buscar na Central 360</b> (traz nome, CNPJ e código da Domínio)
      <div class="linha" style="margin-top:6px"><input id="c-q" placeholder="Nome, CNPJ ou código" style="flex:1"><button class="btn" id="c-buscar">Buscar</button></div>
      <div id="c-res" class="peq" style="margin-top:6px"></div></div>`}
    <div class="form">
      <label>Nome <input id="e-nome" value="${esc(e?.nome)}"></label>
      <label>CNPJ <input id="e-cnpj" value="${esc(cnpjFmt(e?.cnpj))}"></label>
      <label>Código na Domínio <input id="e-cod" value="${esc(e?.codigo_dominio)}"></label>
      <label>PIS/COFINS na DRE <select id="e-pisf"><option value="APURACAO">Débito apurado na Domínio</option><option value="ALIQUOTA">Alíquota × (faturamento − devoluções)</option></select></label>
      <label>Alíquota PIS/COFINS (%) — se usar alíquota <input id="e-pis" inputmode="decimal" value="${e ? brl4(e.pis_cofins_aliq).replace(/,?0+$/, "") : "9,25"}"></label>
      <label>Modelo de DRE <select id="e-mod">${modelos.map((x) => `<option value="${x.id}" ${e?.modelo_id === x.id ? "selected" : ""}>${esc(x.nome)}</option>`).join("")}</select></label>
    </div>
    <details style="margin-top:14px" ${e?.bling_app_proprio ? "open" : ""}><summary><b>Aplicativo do Bling próprio desta empresa</b> <span class="suave">(opcional)</span></summary>
      <div class="suave peq" style="margin:6px 0">Use quando a empresa tiver um aplicativo privado criado na conta Bling dela. Sem isso, vale o aplicativo padrão das Configurações.</div>
      <div class="form"><label>Client ID <input id="e-cid" value="${esc(e?.bling_client_id)}"></label>
      <label>Client Secret <input id="e-sec" type="password" placeholder="${e?.bling_app_proprio ? "(guardado — preencha para trocar)" : ""}"></label></div></details>
    ${e ? `<label class="chk" style="margin-top:12px"><input type="checkbox" id="e-ativo" ${e.ativo ? "checked" : ""}> Empresa ativa</label>` : ""}
    ${e && e.modelo_id ? `<div class="aviso peq" style="margin-top:12px">Trocar o modelo de DRE apaga as associações desta empresa.</div>` : ""}
    <div class="linha" style="margin-top:16px"><button class="btn pri" id="e-ok">Salvar</button><button class="btn" data-fechar>Cancelar</button>
    ${e && EU.perfil === "ADMIN" ? '<button class="btn perigo" id="e-del" style="margin-left:auto">Excluir empresa</button>' : ""}</div>`);
  $("#e-pisf", m).value = (e && e.pis_cofins_fonte) || "APURACAO";
  if (!e) {
    const buscar = (ev) => tentar(async () => {
      const r = await api(`/api/central/clientes?q=${encodeURIComponent($("#c-q", m).value)}`);
      $("#c-res", m).innerHTML = r.length ? r.slice(0, 12).map((c, i) => `<div><a href="#" data-i="${i}">${esc(c.razao_social)}</a> <span class="suave">${cnpjFmt(c.cnpj)} · cód. ${esc(c.codigo_dominio)}</span></div>`).join("") : "Nada encontrado.";
      $$("#c-res a", m).forEach((a) => a.onclick = (x) => {
        x.preventDefault();
        const c = r[a.dataset.i];
        $("#e-nome", m).value = c.nome_fantasia || c.razao_social; $("#e-cnpj", m).value = cnpjFmt(c.cnpj); $("#e-cod", m).value = c.codigo_dominio;
      });
    }, ev && ev.target);
    $("#c-buscar", m).onclick = buscar;
    $("#c-q", m).addEventListener("keydown", (x) => { if (x.key === "Enter") buscar(); });
  }
  $("#e-ok", m).onclick = (ev) => tentar(async () => {
    const body = { nome: $("#e-nome", m).value, cnpj: $("#e-cnpj", m).value, codigo_dominio: $("#e-cod", m).value,
      modelo_id: Number($("#e-mod", m).value), bling_client_id: $("#e-cid", m).value, pis_cofins_aliq: $("#e-pis", m).value, pis_cofins_fonte: $("#e-pisf", m).value };
    if ($("#e-sec", m).value) body.bling_client_secret = $("#e-sec", m).value;
    if (e) body.ativo = $("#e-ativo", m).checked;
    if (e && e.modelo_id && body.modelo_id !== e.modelo_id && !confirm("Trocar o modelo apaga as associações desta empresa. Continuar?")) return;
    const r = await api(e ? `/api/empresas/${e.id}` : "/api/empresas", { method: e ? "PUT" : "POST", body });
    m.remove(); toast("Empresa salva.");
    location.hash = `#/empresa/${r.id}`; rota();
  }, ev.target);
  if ($("#e-del", m)) $("#e-del", m).onclick = (ev) => tentar(async () => {
    if (!confirm(`Excluir ${e.nome} e todos os dados copiados do Bling e da Domínio?`)) return;
    await api(`/api/empresas/${e.id}`, { method: "DELETE" }); m.remove(); location.hash = "#/empresas";
  }, ev.target);
}

// ------------------------------------------------------------------ empresa (abas)
let pollJobs = null;
async function telaEmpresa(id, aba, params) {
  clearInterval(pollJobs);
  const e = await api(`/api/empresas/${id}`);
  const abas = [["conexoes", "Conexões e dados"], ["associacao", "Associação de contas"], ["custos", "Custo dos produtos"], ["mlivre", "Mercado Livre"], ["dre", "DRE"], ["fluxo", "Fluxo de caixa"]];
  casca("empresas", `<div class="topo"><div><a href="#/empresas" class="peq">← Empresas</a><h1>${esc(e.nome)}</h1>
      <div class="suave">${cnpjFmt(e.cnpj) || "sem CNPJ"} · Domínio ${esc(e.codigo_dominio || "—")} · Modelo: ${esc(e.modelo_nome || "—")}</div></div>
      <button class="btn" id="editar">Editar empresa</button></div>
    <div class="abas">${abas.map(([k, t]) => `<a href="#/empresa/${id}/${k}" class="${aba === k ? "ativo" : ""}">${t}</a>`).join("")}</div>
    <div id="aba"></div>`);
  $("#editar").onclick = () => formEmpresa(e);
  if (params.get("bling") === "ok") toast("Bling conectado. Agora atualize os dados do período.");
  if (params.get("bling") === "erro") toast(`Bling: ${params.get("msg") || "não foi possível conectar"}`, true);
  if (params.get("ml") === "ok") toast("Mercado Livre conectado. Agora atualize os dados do período.");
  if (params.get("ml") === "erro") toast(`Mercado Livre: ${params.get("msg") || "não foi possível conectar"}`, true);
  if (aba === "mlivre") return abaMLivre(e);
  if (aba === "associacao") return abaAssociacao(e);
  if (aba === "dre") return abaDre(e);
  if (aba === "custos") return abaCustos(e);
  if (aba === "fluxo") return abaFluxo(e);
  return abaConexoes(e);
}

function abaConexoes(e) {
  const p = periodo();
  $("#aba").innerHTML = `<div class="grade" style="grid-template-columns:repeat(auto-fill,minmax(360px,1fr))">
    <div class="card"><h2>Bling</h2>
      ${e.bling_conectado ? `<div class="linha"><span class="badge b-ok">Conectado</span><span class="suave peq">desde ${dataHora(e.bling_conectado_em)}${e.bling_app_proprio ? " · aplicativo próprio" : ""}</span></div>`
        : `<div class="aviso">Ainda não conectada. Clique em <b>Conectar ao Bling</b> e entre com o usuário do Bling desta empresa para autorizar a leitura.</div>`}
      <div class="suave peq" style="margin:10px 0">Última atualização: ${e.bling_sync_em ? `${dataHora(e.bling_sync_em)} (caixa de ${esc(e.bling_sync_periodo)})` : "nunca"}</div>
      <div class="linha"><label>De <input type="month" id="b-ini" value="${p.inicio}"></label><label>Até <input type="month" id="b-fim" value="${p.fim}"></label></div>
      <div class="linha" style="margin-top:12px">
        <button class="btn ${e.bling_conectado ? "" : "pri"}" id="b-con">${e.bling_conectado ? "Reconectar" : "Conectar ao Bling"}</button>
        <button class="btn pri" id="b-sync" ${e.bling_conectado ? "" : "disabled"}>Atualizar do Bling</button>
        ${e.bling_conectado ? '<button class="btn perigo peq" id="b-des">Desconectar</button>' : ""}</div>
      <div class="suave peq" style="margin-top:10px">Lê as categorias financeiras e os lançamentos de <b>Caixa e Bancos</b> do período — só o que foi efetivamente pago ou recebido (somente leitura) — e o histórico anterior de cada conta para calcular os saldos do fluxo de caixa. Contas a pagar e a receber em aberto não entram. Pode levar alguns minutos.</div>
    </div>
    <div class="card"><h2>Domínio (Central 360)</h2>
      <div class="suave peq" style="margin-bottom:10px">Faturamento (notas de saída e serviços), devoluções por CFOP e os <b>produtos de cada nota</b> (base do CMV), lidos pelo Agente 360 no banco da Domínio.</div>
      <div class="suave peq" style="margin-bottom:10px">Última leitura: ${e.dominio_sync_em ? `${dataHora(e.dominio_sync_em)} (${esc(e.dominio_sync_periodo)})` : "nunca"}</div>
      <div class="linha"><label>De <input type="month" id="d-ini" value="${p.inicio}"></label><label>Até <input type="month" id="d-fim" value="${p.fim}"></label></div>
      <div class="linha" style="margin-top:12px"><button class="btn pri" id="d-imp">Ler da Domínio</button></div>
      ${!e.cnpj && !e.codigo_dominio ? '<div class="aviso peq" style="margin-top:10px">Informe o CNPJ ou o código da Domínio no cadastro.</div>' : ""}
    </div>
    <div class="card"><h2>Mercado Livre</h2>
      ${e.ml_conectado ? `<div class="linha"><span class="badge b-ok">Conectado</span><span class="suave peq">${esc(e.ml_apelido || "")} · desde ${dataHora(e.ml_conectado_em)}</span></div>`
        : `<div class="aviso">Ainda não conectada. Clique em <b>Conectar Mercado Livre</b> e entre com a conta de vendedor desta empresa para autorizar a leitura.</div>`}
      <div class="suave peq" style="margin:10px 0">Última atualização: ${e.ml_sync_em ? `${dataHora(e.ml_sync_em)} (${esc(e.ml_sync_periodo)})` : "nunca"}</div>
      <div class="linha"><label>De <input type="month" id="m-ini" value="${p.inicio}"></label><label>Até <input type="month" id="m-fim" value="${p.fim}"></label></div>
      <div class="linha" style="margin-top:12px">
        <button class="btn ${e.ml_conectado ? "" : "pri"}" id="m-con">${e.ml_conectado ? "Reconectar" : "Conectar Mercado Livre"}</button>
        <button class="btn pri" id="m-sync" ${e.ml_conectado ? "" : "disabled"}>Atualizar do Mercado Livre</button>
        ${e.ml_conectado ? '<button class="btn perigo peq" id="m-des">Desconectar</button>' : ""}</div>
      <div class="suave peq" style="margin-top:10px">Lê os <b>pedidos</b> (valor pago, reembolsos e a tarifa de venda de cada um) e o <b>faturamento</b> do Mercado Livre e do Mercado Pago de cada mês (comissões, Mercado Envios, Product Ads, bonificações). Somente leitura.</div>
    </div></div>
    <div class="card"><h2>Atividades</h2><div id="jobs" class="suave">Carregando...</div></div>`;
  $("#b-con").onclick = (ev) => tentar(async () => { const r = await api(`/api/empresas/${e.id}/bling/conectar`, { method: "POST" }); location.href = r.url; }, ev.target);
  if ($("#b-des")) $("#b-des").onclick = (ev) => tentar(async () => {
    if (!confirm("Desconectar esta empresa do Bling? Os dados já copiados continuam.")) return;
    await api(`/api/empresas/${e.id}/bling/desconectar`, { method: "POST" }); rota();
  }, ev.target);
  $("#b-sync").onclick = (ev) => tentar(async () => {
    const ini = $("#b-ini").value, fim = $("#b-fim").value;
    await api(`/api/empresas/${e.id}/bling/sincronizar`, { method: "POST", body: { inicio: `${ini}-01`, fim: ultimoDia(fim) } });
    toast("Atualização do Bling iniciada."); carregarJobs(e.id);
  }, ev.target);
  $("#d-imp").onclick = (ev) => tentar(async () => {
    await api(`/api/empresas/${e.id}/dominio/importar`, { method: "POST", body: { inicio: $("#d-ini").value, fim: $("#d-fim").value } });
    toast("Pedido enviado à Central 360."); carregarJobs(e.id);
  }, ev.target);
  $("#m-con").onclick = (ev) => tentar(async () => { const r = await api(`/api/empresas/${e.id}/mlivre/conectar`, { method: "POST" }); location.href = r.url; }, ev.target);
  if ($("#m-des")) $("#m-des").onclick = (ev) => tentar(async () => {
    if (!confirm("Desconectar esta empresa do Mercado Livre? Os dados já copiados continuam.")) return;
    await api(`/api/empresas/${e.id}/mlivre/desconectar`, { method: "POST" }); rota();
  }, ev.target);
  $("#m-sync").onclick = (ev) => tentar(async () => {
    await api(`/api/empresas/${e.id}/mlivre/sincronizar`, { method: "POST", body: { inicio: $("#m-ini").value, fim: $("#m-fim").value } });
    toast("Atualização do Mercado Livre iniciada."); carregarJobs(e.id);
  }, ev.target);
  carregarJobs(e.id);
}

// ------------------------------------------------------------------ Mercado Livre (pedidos e tarifas)
async function abaMLivre(e) {
  $("#aba").innerHTML = `<div class="card"><div class="linha entre" style="align-items:flex-end">${seletorPeriodo(() => carregar())}
    <input id="ml-q" placeholder="Pedido, produto ou comprador..." style="min-width:240px"></div>
    <div id="ml-corpo" style="margin-top:12px" class="suave">Carregando...</div></div>`;
  let r = null;
  const ST = { paid: "pago", cancelled: "cancelado", confirmed: "confirmado", payment_required: "aguardando pagamento", payment_in_process: "pagamento em análise", partially_refunded: "reembolso parcial" };
  const carregar = async () => {
    const p = periodo();
    r = await api(`/api/empresas/${e.id}/mlivre/vendas?inicio=${p.inicio}&fim=${p.fim}&q=${encodeURIComponent($("#ml-q").value)}`);
    desenhar();
  };
  const desenhar = () => {
    const c = $("#ml-corpo");
    if (!r.meses.length) {
      c.innerHTML = `<div class="vazio">${e.ml_conectado ? "Nada lido no período. Atualize na aba Conexões e dados." : "Conecte a empresa ao Mercado Livre na aba Conexões e dados."}</div>`;
      return;
    }
    const tot = r.meses.reduce((a, m) => { for (const k of ["pedidos", "pagos", "vendas", "devolvido", "comissao_pedidos", "encargos", "bonus", "liquido"]) a[k] = (a[k] || 0) + m[k]; return a; }, {});
    c.innerHTML = `<div class="kpi"><div>Vendas pagas<b>R$ ${brl(tot.vendas)}</b></div><div>Reembolsos<b class="neg">R$ ${brl(-tot.devolvido)}</b></div>
      <div>Encargos do faturamento<b class="neg">R$ ${brl(-tot.encargos)}</b></div><div>Bonificações<b>R$ ${brl(tot.bonus)}</b></div>
      <div>Líquido<b>R$ ${brl(tot.liquido)}</b></div><div>Custo ML / vendas<b>${tot.vendas ? pct((tot.encargos - tot.bonus) / tot.vendas * 100) : "—"}</b></div></div>
    <h3>Por mês</h3><div class="rolagem"><table><tr><th>Mês</th><th class="num">Pedidos</th><th class="num">Pagos</th><th class="num">Vendas pagas</th><th class="num">Reembolsos</th>
      <th class="num">Tarifa nos pedidos</th><th class="num">Encargos (faturamento)</th><th class="num">Bonificações</th><th class="num">Líquido</th></tr>
      ${r.meses.map((m) => `<tr><td>${mesBR(m.mes)}</td><td class="num">${m.pedidos}</td><td class="num">${m.pagos}</td><td class="num">${brl(m.vendas)}</td>
        <td class="num neg">${brl(-m.devolvido)}</td><td class="num suave">${brl(-m.comissao_pedidos)}</td><td class="num neg">${brl(-m.encargos)}</td>
        <td class="num">${brl(m.bonus)}</td><td class="num"><b>${brl(m.liquido)}</b></td></tr>`).join("")}</table></div>
    <div class="suave peq" style="margin:6px 0 14px">“Tarifa nos pedidos” é a comissão informada em cada pedido (conferência); a DRE usa os encargos do faturamento oficial do mês. O período de faturamento do Mercado Livre pode não fechar exatamente no último dia do mês.</div>
    <details><summary><b>Encargos e bonificações do faturamento</b> (${r.tarifas.length})</summary>
      <div class="rolagem" style="max-height:40vh"><table><tr><th>Mês</th><th>Origem</th><th>Tipo</th><th>Descrição</th><th class="num">Valor</th></tr>
      ${r.tarifas.map((t) => `<tr><td>${mesBR(t.periodo)}</td><td>${t.grupo === "ML" ? "Mercado Livre" : "Mercado Pago"}</td><td class="mono peq">${esc(t.tipo)}</td>
        <td>${esc(t.rotulo)}</td><td class="num ${t.natureza === "CHARGE" ? "neg" : ""}">${brl(t.natureza === "CHARGE" ? -Math.abs(t.valor) : Math.abs(t.valor))}</td></tr>`).join("")}</table></div></details>
    <h3 style="margin-top:16px">Pedidos (${r.total_pedidos})</h3>${tabelaPedidos(r.vendas, ST)}`;
  };
  let t = null;
  $("#ml-q").oninput = () => { clearTimeout(t); t = setTimeout(() => tentar(carregar), 400); };
  await tentar(carregar);
}

function tabelaPedidos(vs, ST = {}) {
  return `<div class="rolagem" style="max-height:55vh"><table><tr><th>Data</th><th>Pedido</th><th>Produto</th><th>Comprador</th><th>Situação</th>
    <th class="num">Pago</th><th class="num">Reembolso</th><th class="num">Tarifa de venda</th><th class="num">Líquido</th></tr>
    ${vs.map((v) => `<tr><td>${dataBR(v.data)}</td><td class="mono peq">${v.order_id}${v.pack_id ? `<div class="suave">pacote ${v.pack_id}</div>` : ""}</td>
      <td class="peq">${esc(v.descricao)}${v.itens > 1 ? ` <span class="suave">(${v.itens} un)</span>` : ""}</td><td class="peq">${esc(v.comprador)}</td>
      <td class="peq">${esc(ST[v.status] || v.status)}</td><td class="num">${brl(v.pago)}</td><td class="num ${v.devolvido ? "neg" : "suave"}">${v.devolvido ? brl(-v.devolvido) : "—"}</td>
      <td class="num neg">${brl(-v.comissao)}</td><td class="num"><b>${brl(v.liquido)}</b></td></tr>`).join("") || '<tr><td colspan="9" class="vazio">Nenhum pedido.</td></tr>'}</table></div>`;
}

async function carregarJobs(eid) {
  clearInterval(pollJobs);
  const desenha = async () => {
    const el = $("#jobs");
    if (!el) { clearInterval(pollJobs); return; }
    const js = await api(`/api/jobs?empresa_id=${eid}`);
    el.innerHTML = js.length ? `<table><tr><th>Quando</th><th>Atividade</th><th>Situação</th><th>Por</th></tr>${js.slice(0, 15).map((j) => `<tr>
      <td class="peq">${dataHora(j.criado_em)}</td><td>${statusJob(j)}</td>
      <td>${esc(j.mensagem)}${j.status === "EXECUTANDO" && j.total ? `<div class="barra"><i style="width:${Math.round(j.progresso / j.total * 100)}%"></i></div>` : ""}</td>
      <td class="peq suave">${esc(j.criado_por)}</td></tr>`).join("")}</table>` : "Nenhuma atividade ainda.";
    if (!js.some((j) => ["PENDENTE", "EXECUTANDO"].includes(j.status))) clearInterval(pollJobs);
  };
  await desenha();
  pollJobs = setInterval(() => desenha().catch(() => clearInterval(pollJobs)), 3000);
}

// ------------------------------------------------------------------ associação
async function abaAssociacao(e) {
  if (!e.modelo_id) { $("#aba").innerHTML = `<div class="aviso">Escolha um modelo de DRE no cadastro da empresa.</div>`; return; }
  const filtros = { q: "", origem: "", pend: false };
  $("#aba").innerHTML = `<div class="card">
    <div class="linha entre" style="align-items:flex-end">${seletorPeriodo(() => carregar())}
      <div class="linha"><button class="btn" id="copiar">Copiar associações de outra empresa</button></div></div>
    <div class="linha" style="margin-top:12px">
      <input id="f-q" placeholder="Buscar conta..." style="flex:1;min-width:180px">
      <select id="f-o"><option value="">Todas as origens</option><option value="BLING">Só Bling</option><option value="DOMINIO">Só Domínio</option><option value="MLIVRE">Só Mercado Livre</option></select>
      <label class="chk"><input type="checkbox" id="f-p"> Só sem associação</label></div>
    <div id="resumo" class="suave peq" style="margin:10px 0"></div>
    <div class="rolagem"><table id="tab"></table></div></div>`;
  let dados = null, modelo = null;
  const carregar = async () => {
    const p = periodo();
    [dados, modelo] = await Promise.all([api(`/api/empresas/${e.id}/contas?inicio=${p.inicio}&fim=${p.fim}`), api(`/api/modelos/${e.modelo_id}`)]);
    desenhar();
  };
  const opcoes = () => {
    let html = '<option value="">— não entra na DRE —</option>';
    for (const g of modelo.linhas.filter((x) => x.tipo === "GRUPO")) {
      const fs = modelo.linhas.filter((x) => x.tipo === "CONTA" && x.pai_codigo === g.codigo);
      if (fs.length) html += `<optgroup label="${esc(g.nome)}">${fs.map((ln) => `<option value="${ln.id}">${esc(ln.nome)}</option>`).join("")}</optgroup>`;
    }
    return html;
  };
  const desenhar = () => {
    const ops = opcoes();
    const q = filtros.q.toLowerCase();
    const lin = dados.contas.filter((c) => (!filtros.origem || c.origem === filtros.origem) && (!filtros.pend || !c.linha_id)
      && (!q || c.nome.toLowerCase().includes(q) || (c.linha_nome || "").toLowerCase().includes(q)));
    const bl = dados.contas.filter((c) => c.origem === "BLING" && c.ativa);
    const comValor = dados.contas.filter((c) => c.total && !c.linha_id);
    $("#resumo").innerHTML = `${bl.filter((c) => c.linha_id).length} de ${bl.length} categorias ativas do Bling associadas.`
      + (comValor.length ? ` <b style="color:var(--amarelo)">${comValor.length} conta(s) com valor no período ainda sem associação.</b>` : "")
      + (bl.length ? "" : " <b>Ainda não há categorias do Bling: conecte e atualize na aba Conexões.</b>");
    $("#tab").innerHTML = `<tr><th>Origem</th><th>Conta</th><th>Tipo</th><th class="num">Pago/recebido no período</th><th style="min-width:260px">Linha da DRE</th><th></th></tr>`
      + (lin.length ? lin.map((c) => `<tr data-id="${c.id}" style="${c.ativa ? "" : "opacity:.55"}">
        <td>${ORIGEM[c.origem]}</td>
        <td>${esc(c.nome)}${c.ativa ? "" : ' <span class="badge">inativa</span>'}${c.origem === "BLING" ? ` <span class="suave peq">#${esc(c.codigo)}</span>` : ""}</td>
        <td class="peq">${TIPO[c.tipo] || c.tipo}</td>
        <td class="num ${c.total < 0 ? "neg" : ""}">${c.total ? brl(c.total) : '<span class="suave">—</span>'}</td>
        <td><select class="sel-linha" style="width:100%">${ops}</select></td>
        <td><button class="btn peq consultar">Consultar</button></td></tr>`).join("") : `<tr><td colspan="6" class="vazio">Nada para mostrar.</td></tr>`);
    $$("#tab tr[data-id]").forEach((tr) => {
      const c = dados.contas.find((x) => x.id === Number(tr.dataset.id));
      const sel = $(".sel-linha", tr);
      sel.value = c.linha_id || "";
      sel.onchange = () => tentar(async () => {
        await api(`/api/contas/${c.id}`, { method: "PUT", body: { linha_id: sel.value ? Number(sel.value) : null } });
        c.linha_id = sel.value ? Number(sel.value) : null;
        tr.style.background = "#E3F5EF"; setTimeout(() => { tr.style.background = ""; }, 700);
        if (filtros.pend) desenhar();
      });
      $(".consultar", tr).onclick = () => consultarConta(c, e);
    });
  };
  $("#f-q").oninput = (x) => { filtros.q = x.target.value; desenhar(); };
  $("#f-o").onchange = (x) => { filtros.origem = x.target.value; desenhar(); };
  $("#f-p").onchange = (x) => { filtros.pend = x.target.checked; desenhar(); };
  $("#copiar").onclick = async () => {
    const outras = (await api("/api/empresas")).filter((x) => x.id !== e.id && x.modelo_id === e.modelo_id);
    if (!outras.length) return toast("Nenhuma outra empresa usa o mesmo modelo de DRE.", true);
    const m = modal(`<h2>Copiar associações</h2><div class="suave" style="margin-bottom:10px">As categorias são casadas pelo nome. Só preenche as que estão sem associação, a não ser que você marque a opção abaixo.</div>
      <label>De qual empresa <select id="cp-e">${outras.map((x) => `<option value="${x.id}">${esc(x.nome)}</option>`).join("")}</select></label>
      <label class="chk" style="margin-top:10px"><input type="checkbox" id="cp-s"> Substituir também as já associadas</label>
      <div class="linha" style="margin-top:14px"><button class="btn pri" id="cp-ok">Copiar</button><button class="btn" data-fechar>Cancelar</button></div>`, true);
    $("#cp-ok", m).onclick = (ev) => tentar(async () => {
      const r = await api(`/api/empresas/${e.id}/contas/copiar`, { method: "POST", body: { de_empresa_id: Number($("#cp-e", m).value), sobrescrever: $("#cp-s", m).checked } });
      m.remove(); toast(`${r.alteradas} associação(ões) copiada(s).`); carregar();
    }, ev.target);
  };
  await carregar();
}

async function consultarConta(c, e) {
  const p = periodo();
  const m = modal(`<div class="linha entre"><div><h2 style="margin:0">${esc(c.nome)}</h2><div class="suave peq">${ORIGEM[c.origem]} ${mesBR(p.inicio)} a ${mesBR(p.fim)} · ${{ BLING: "caixa e bancos (pagos e recebidos)", MLIVRE: "pedidos e faturamento do Mercado Livre" }[c.origem] || "notas fiscais"}</div></div>
    <div class="linha">${c.origem === "BLING" && e ? '<button class="btn" id="cs-sync">Atualizar do Bling</button>' : ""}<button class="btn" data-fechar>Fechar</button></div></div>
    <div id="cs-corpo" style="margin-top:12px" class="suave">Carregando...</div>`);
  const carregar = async () => {
    const r = await api(`/api/contas/${c.id}/detalhe?inicio=${p.inicio}&fim=${p.fim}`);
    const corpo = $("#cs-corpo", m);
    if (!r.itens.length) { corpo.innerHTML = '<div class="vazio">Nenhum lançamento no período.</div>'; return; }
    if (r.tipo === "caixa") {
      corpo.innerHTML = `<div class="kpi"><div>Total<b class="${r.total < 0 ? "neg" : ""}">R$ ${brl(r.total)}</b></div><div>Lançamentos<b>${r.itens.length}</b></div></div>
      <div class="rolagem"><table><tr><th>Data</th><th>Conta</th><th>Contato</th><th>Descrição</th><th>Origem</th><th class="num">Valor</th></tr>
      ${r.itens.map((i) => `<tr><td>${dataBR(i.data)}</td><td class="peq">${esc(i.conta_financeira)}</td><td>${esc(i.contato)}</td>
        <td class="peq">${esc(i.descricao)}</td><td class="peq suave">${esc(i.origem)}</td><td class="num ${i.valor < 0 ? "neg" : ""}">${brl(i.valor)}</td></tr>`).join("")}</table></div>`;
    } else if (r.tipo === "ml_pedidos") {
      corpo.innerHTML = `<div class="kpi"><div>Total<b class="${r.total < 0 ? "neg" : ""}">R$ ${brl(r.total)}</b></div><div>Pedidos<b>${r.qtd}</b></div></div>${tabelaPedidos(r.itens)}`;
    } else if (r.tipo === "ml_tarifa") {
      corpo.innerHTML = `<div class="kpi"><div>Total<b class="${r.total < 0 ? "neg" : ""}">R$ ${brl(r.total)}</b></div></div>
      <div class="rolagem" style="max-height:30vh"><table><tr><th>Mês</th><th>Descrição</th><th>Período de faturamento</th><th class="num">Valor</th></tr>
      ${r.itens.map((i) => `<tr><td>${mesBR(i.periodo)}</td><td>${esc(i.rotulo)} <span class="mono suave peq">${esc(i.tipo)}</span></td><td class="peq">${i.de ? `${dataBR(i.de)} a ${dataBR(i.ate)}` : "—"}</td><td class="num ${i.valor < 0 ? "neg" : ""}">${brl(i.valor)}</td></tr>`).join("")}</table></div>
      ${r.pedidos.length ? `<h3 style="margin-top:16px">Tarifa de venda em cada pedido (${r.pedidos.length})</h3>${tabelaPedidos(r.pedidos)}` : ""}`;
    } else if (r.tipo === "icms") {
      corpo.innerHTML = `<div class="kpi"><div>ICMS TTD a recolher<b class="neg">R$ ${brl(r.total)}</b></div></div>
      <div class="suave peq" style="margin-bottom:8px">Apuração do ICMS no regime especial, lida da Domínio (soma das sub-apurações): débito das saídas − crédito presumido − demais créditos = saldo a recolher (é o que vai para a DRE).</div>
      <div class="rolagem"><table><tr><th>Mês</th><th class="num">Débito das saídas</th><th class="num">Crédito presumido</th><th class="num">Saldo a recolher</th></tr>
      ${r.itens.map((i) => `<tr><td>${mesBR(i.mes)}</td><td class="num">${brl(i.debito)}</td><td class="num">${brl(i.credito_presumido)}</td><td class="num"><b>${brl(i.saldo_recolher)}</b></td></tr>`).join("") || '<tr><td colspan="4" class="vazio">Sem apuração no período (leia a Domínio com o Agente 360 0.14.3 ou superior).</td></tr>'}</table></div>`;
    } else if (r.tipo === "pis") {
      const ap = r.fonte === "APURACAO";
      corpo.innerHTML = `<div class="kpi"><div>PIS/COFINS<b class="neg">R$ ${brl(r.total)}</b></div><div>Origem<b style="font-size:14px">${ap ? "Débito apurado na Domínio" : `Alíquota de ${brl4(r.aliquota)}%`}</b></div></div>
      <div class="suave peq" style="margin-bottom:8px">${ap ? "Total de débitos de PIS e de COFINS da apuração do mês na Domínio. Faturamento e devoluções aparecem só para comparação."
        : "Base = faturamento (vendas + serviços das notas da Domínio, sem canceladas) − devoluções de venda."} A origem fica no cadastro da empresa.</div>
      <div class="rolagem"><table><tr><th>Mês</th><th class="num">Faturamento</th><th class="num">Devoluções</th>${ap ? '<th class="num">PIS apurado</th><th class="num">COFINS apurada</th>' : '<th class="num">Base</th>'}<th class="num">PIS/COFINS</th><th class="num">% do faturamento</th></tr>
      ${r.itens.map((i) => `<tr><td>${mesBR(i.mes)}</td><td class="num">${brl(i.faturamento)}</td><td class="num">${brl(i.devolucoes)}</td>
        ${ap ? `<td class="num">${i.apurado ? brl(i.pis) : '<span class="badge b-aviso">sem apuração</span>'}</td><td class="num">${i.apurado ? brl(i.cofins) : ""}</td>` : `<td class="num">${brl(i.base)}</td>`}
        <td class="num"><b>${brl(i.valor)}</b></td><td class="num suave">${i.faturamento ? pct(i.valor / i.faturamento * 100) : ""}</td></tr>`).join("")}</table></div>`;
    } else if (r.tipo === "cmv") {
      corpo.innerHTML = `<div class="kpi"><div>CMV<b class="neg">R$ ${brl(r.total)}</b></div><div>Produtos<b>${r.itens.length}</b></div>
        ${r.sem_custo.produtos ? `<div style="border-color:#F1D9A6;background:var(--amarelo-cl)">Sem custo<b>${r.sem_custo.produtos}</b></div>` : ""}</div>
      <div class="suave peq" style="margin-bottom:8px">(Quantidade vendida − devolvida) × custo vigente em cada mês. Informe os custos na aba <a href="#/empresa/${e.id}/custos" data-fechar>Custo dos produtos</a>.</div>
      <div class="rolagem"><table><tr><th>Produto</th><th class="num">Vendida</th><th class="num">Devolvida</th><th class="num">Custo (fim do período)</th><th class="num">CMV</th></tr>
      ${r.itens.map((i) => `<tr><td>${esc(i.codigo)} · ${esc(i.descricao)}</td><td class="num">${qtdBR(i.qtd_vendida)}</td><td class="num">${qtdBR(i.qtd_devolvida)}</td>
        <td class="num">${i.custo == null ? '<span class="badge b-aviso">sem custo</span>' : brl4(i.custo)}${i.meses_sem_custo.length ? `<div class="peq" style="color:var(--amarelo)">sem custo em ${i.meses_sem_custo.map(mesBR).join(", ")}</div>` : ""}</td><td class="num">${brl(i.cmv)}</td></tr>`).join("")}</table></div>`;
    } else {
      const prods = r.produtos && r.produtos.length ? `<h3 style="margin-top:16px">Por produto</h3><div class="rolagem" style="max-height:40vh"><table><tr><th>Produto</th><th>Un</th><th class="num">Quantidade</th><th class="num">Valor dos itens</th></tr>
        ${r.produtos.map((p) => `<tr><td>${esc(p.codigo)} · ${esc(p.descricao)}</td><td>${esc(p.unidade)}</td><td class="num">${qtdBR(p.qtd)}</td><td class="num">${brl(p.valor)}</td></tr>`).join("")}</table></div>` : "";
      corpo.innerHTML = `<div class="kpi"><div>Total<b>R$ ${brl(r.total)}</b></div></div>
      <div class="rolagem" style="max-height:40vh"><table><tr><th>Mês</th><th>Origem</th><th>CFOP</th><th class="num">Notas</th><th class="num">Valor</th></tr>
      ${r.itens.map((i) => `<tr><td>${mesBR(i.mes)}</td><td>${i.origem}</td><td class="mono">${i.cfop ? String(i.cfop).replace(/^(\d)/, "$1.") : "—"}</td><td class="num">${i.qtd}</td><td class="num">${brl(i.valor)}</td></tr>`).join("")}</table></div>${prods}`;
    }
  };
  if ($("#cs-sync", m)) $("#cs-sync", m).onclick = (ev) => tentar(async () => {
    await api(`/api/empresas/${e.id}/bling/sincronizar`, { method: "POST", body: { inicio: `${p.inicio}-01`, fim: ultimoDia(p.fim) } });
    toast("Atualização iniciada; acompanhe na aba Conexões e consulte de novo ao terminar.");
  }, ev.target);
  tentar(carregar);
}

// ------------------------------------------------------------------ DRE
async function abaDre(e) {
  $("#aba").innerHTML = `<div class="card"><div class="linha entre" style="align-items:flex-end">${seletorPeriodo(() => carregar())}
    <div class="linha"><label class="chk"><input type="checkbox" id="av" checked> Análise vertical</label><button class="btn" id="xls">Baixar Excel</button></div></div>
    <div id="dre-avisos" style="margin-top:12px"></div><div id="dre" style="margin-top:8px" class="suave">Carregando...</div></div>
    <div class="card" id="nao-assoc" style="display:none"></div>`;
  const abertas = new Set(), fechados = new Set();
  let r = null;
  const carregar = async () => {
    const p = periodo();
    r = await api(`/api/empresas/${e.id}/dre?inicio=${p.inicio}&fim=${p.fim}`);
    desenhar();
  };
  const desenhar = () => {
    const av = $("#av").checked;
    const fontes = [];
    if (!r.empresa.bling_sync_periodo) fontes.push("o Bling ainda não foi atualizado");
    if (!r.empresa.dominio_sync_periodo) fontes.push("a Domínio ainda não foi lida");
    $("#dre-avisos").innerHTML = (fontes.length ? `<div class="aviso">Atenção: ${fontes.join(" e ")} para esta empresa (aba Conexões).</div>` : "")
      + r.avisos.map((a) => `<div class="aviso">${esc(a)}</div>`).join("")
      + `<div class="suave peq">Bling pelo caixa (pagos e recebidos em ${esc(r.empresa.bling_sync_periodo || "—")}) · Domínio pelas notas fiscais (${esc(r.empresa.dominio_sync_periodo || "—")}) · Base da análise vertical: ${esc(r.base_av || "—")}. Clique num grupo para recolher ou abrir; numa conta, para ver de onde vêm os valores.</div>`;
    const cols = r.meses.length + 2 + (av ? 1 : 0);
    const cel = (v) => `<td class="num ${v < 0 ? "neg" : ""}">${v ? brl(v) : '<span class="suave">–</span>'}</td>`;
    let html = `<div class="rolagem"><table class="dre"><tr><th>Linha</th>${r.meses.map((m) => `<th class="num">${mesBR(m)}</th>`).join("")}<th class="num">Total</th>${av ? '<th class="num">AV</th>' : ""}</tr>`;
    for (const ln of r.linhas) {
      if (ln.tipo === "CONTA" && fechados.has(ln.pai_id)) continue;
      const temComp = ln.composicao.length > 0;
      const seta = ln.tipo === "GRUPO" ? (fechados.has(ln.id) ? "▸ " : "▾ ") : temComp ? (abertas.has(ln.id) ? "▾ " : "▸ ") : "";
      const cls = ln.tipo === "GRUPO" ? "grupo" : ln.tipo === "TOTAL" ? "destaque" : "conta";
      html += `<tr class="${cls}" data-l="${ln.id}" data-t="${ln.tipo}"><td class="${ln.tipo === "GRUPO" || temComp ? "clicavel" : ""}">${seta}${esc(ln.nome)}</td>
        ${ln.valores.map(cel).join("")}${cel(ln.total)}${av ? `<td class="num suave">${pct(ln.av_total)}</td>` : ""}</tr>`;
      if (ln.tipo === "CONTA" && abertas.has(ln.id)) for (const c of ln.composicao) {
        html += `<tr class="comp"><td>${c.origem === "BLING" ? "Bling" : "Domínio"} · ${esc(c.descricao)}</td>${c.valores.map(cel).join("")}${cel(c.total)}${av ? "<td></td>" : ""}</tr>`;
      }
    }
    $("#dre").innerHTML = html + "</table></div>";
    $("#dre").classList.remove("suave");
    $$("#dre tr[data-l] td.clicavel").forEach((td) => td.onclick = () => {
      const id = Number(td.parentElement.dataset.l);
      const conj = td.parentElement.dataset.t === "GRUPO" ? fechados : abertas;
      if (conj.has(id)) conj.delete(id); else conj.add(id);
      desenhar();
    });
    const na = $("#nao-assoc");
    if (r.nao_associadas.length) {
      na.style.display = "";
      na.innerHTML = `<h2>Contas com valor no período que não entram na DRE</h2>
        <div class="suave peq" style="margin-bottom:8px">Associe na aba <a href="#/empresa/${e.id}/associacao">Associação de contas</a> ou deixe de fora de propósito (ex.: recebimentos de vendas no Bling, quando o faturamento vem da Domínio).</div>
        <table><tr><th>Origem</th><th>Conta</th><th>Natureza</th><th class="num">Total</th></tr>${r.nao_associadas.map((c) => `<tr><td>${ORIGEM[c.origem]}</td><td>${esc(c.descricao)}</td><td class="peq">${TIPO[c.natureza] || ""}</td><td class="num">${brl(c.total)}</td></tr>`).join("")}</table>`;
    } else na.style.display = "none";
  };
  $("#av").onchange = () => r && desenhar();
  $("#xls").onclick = () => { const p = periodo(); location.href = `/api/empresas/${e.id}/dre.xlsx?inicio=${p.inicio}&fim=${p.fim}`; };
  await tentar(carregar);
}


// ------------------------------------------------------------------ fluxo de caixa realizado
async function abaFluxo(e) {
  const st = { zeros: false, organizar: false, fechados: new Set(), contasAbertas: false };
  $("#aba").innerHTML = `<div class="card"><div class="linha entre" style="align-items:flex-end">${seletorPeriodo(() => carregar())}
    <div class="linha"><label class="chk"><input type="checkbox" id="fx-zeros"> Categorias sem movimento</label>
      <button class="btn" id="fx-org">Organizar ordem</button><button class="btn" id="fx-xls">Baixar Excel</button></div></div>
    <div id="fx-info" style="margin-top:12px"></div><div id="fx" class="suave">Carregando...</div></div>`;
  let r = null;
  const carregar = async () => { const p = periodo(); r = await api(`/api/empresas/${e.id}/fluxo?inicio=${p.inicio}&fim=${p.fim}`); desenhar(); };
  const salvarOrdem = (itens) => tentar(async () => { await api(`/api/empresas/${e.id}/fluxo/ordem`, { method: "PUT", body: { itens } }); await carregar(); });
  const irmaos = (ln) => r.linhas.filter((x) => x.tipo === "CATEGORIA" && x.secao === ln.secao && (x.pai || null) === (ln.pai || null) && x.nivel === ln.nivel);
  const desenhar = () => {
    $("#fx-info").innerHTML = (r.empresa.bling_sync_periodo ? "" : `<div class="aviso">O Bling ainda não foi atualizado para esta empresa (aba Conexões).</div>`)
      + r.avisos.map((a) => `<div class="aviso">${esc(a)}</div>`).join("")
      + `<div class="suave peq">Fiel ao Caixa e Bancos do Bling: categorias e hierarquia do Bling, saldos pela soma de todos os lançamentos de cada conta.
        Clique numa categoria para ver os lançamentos; nas categorias-pai, use ▾ para recolher. ${st.organizar ? "<b>Modo organizar:</b> setas mudam a ordem entre categorias do mesmo nível; a caixa muda a seção (Entradas/Saídas)." : ""}</div>`;
    const n = r.meses.length;
    const cel = (v) => `<td class="num ${v < 0 ? "neg" : ""}">${Math.abs(v) > 0.004 ? brl(v) : '<span class="suave">–</span>'}</td>`;
    const escondido = (ln) => {  // filho de categoria recolhida
      let p = ln.pai;
      while (p) { if (st.fechados.has(p)) return true; const pai = r.linhas.find((x) => x.tipo === "CATEGORIA" && x.codigo === p); p = pai && pai.pai; }
      return false;
    };
    let h = `<div class="rolagem"><table class="dre fx"><tr><th>Fluxo de caixa realizado</th>${r.meses.map((m) => `<th class="num">${mesBR(m)}</th>`).join("")}<th class="num">Total</th></tr>`;
    r.linhas.forEach((ln, i) => {
      if (ln.tipo === "CATEGORIA" && !st.zeros && !st.organizar && !ln.tem_mov) return;
      if (ln.tipo === "CATEGORIA" && escondido(ln)) return;
      if ((ln.tipo === "SALDO_CONTA_INI" || ln.tipo === "SALDO_CONTA_FIM") && !st.contasAbertas) return;
      const pad = 12 + ln.nivel * 18;
      let nome = esc(ln.nome), cls = "conta", extra = "";
      if (ln.tipo === "SALDO_INI" || ln.tipo === "SALDO_FIM") { cls = "destaque"; nome = `<span class="fx-contas clicavel">${st.contasAbertas ? "▾" : "▸"} ${nome}</span>`; }
      else if (ln.tipo === "SALDO_CONTA_INI" || ln.tipo === "SALDO_CONTA_FIM") cls = "comp";
      else if (ln.tipo === "SECAO") cls = "grupo";
      else if (ln.tipo === "VARIACAO") cls = "destaque";
      else if (ln.tipo === "CATEGORIA") {
        const seta = ln.filhos ? `<span class="fx-tog clicavel" data-c="${esc(ln.codigo)}">${st.fechados.has(ln.codigo) ? "▸" : "▾"}</span> ` : "";
        nome = `${seta}<span class="fx-cat clicavel" data-i="${i}">${nome}</span>${ln.ativa ? "" : ' <span class="badge">inativa</span>'}`;
        if (ln.nivel === 1) cls = "fx-raiz";
        if (st.organizar) {
          const irs = irmaos(ln), k = irs.indexOf(ln);
          extra = `<span style="white-space:nowrap;margin-right:6px"><button class="btn peq fx-sobe" data-i="${i}" ${k === 0 ? "disabled" : ""}>↑</button><button class="btn peq fx-desce" data-i="${i}" ${k === irs.length - 1 ? "disabled" : ""}>↓</button>
            ${ln.nivel === 1 ? `<select class="fx-sec" data-i="${i}"><option value="ENTRADAS" ${ln.secao === "ENTRADAS" ? "selected" : ""}>Entradas</option><option value="SAIDAS" ${ln.secao === "SAIDAS" ? "selected" : ""}>Saídas</option></select>` : ""}</span>`;
        }
      }
      const tot = ln.tipo.startsWith("SALDO") ? `<td class="num ${ln.total < 0 ? "neg" : ""}" title="${ln.tipo.includes("INI") ? "Saldo no início do período" : "Saldo no fim do período"}">${brl(ln.total)}</td>` : cel(ln.total);
      h += `<tr class="${cls}"><td style="padding-left:${pad}px;${st.organizar ? "min-width:420px" : ""}">${extra}${nome}</td>${ln.valores.map(cel).join("")}${tot}</tr>`;
    });
    $("#fx").innerHTML = h + "</table></div>";
    $("#fx").classList.remove("suave");
    $$("#fx .fx-contas").forEach((x) => x.onclick = () => { st.contasAbertas = !st.contasAbertas; desenhar(); });
    $$("#fx .fx-tog").forEach((x) => x.onclick = () => { const c = x.dataset.c; if (st.fechados.has(c)) st.fechados.delete(c); else st.fechados.add(c); desenhar(); });
    $$("#fx .fx-cat").forEach((x) => x.onclick = () => { const ln = r.linhas[Number(x.dataset.i)]; consultarConta({ id: ln.conta_id, nome: ln.nome, origem: "BLING" }, e); });
    const mover = (i, d) => {
      const ln = r.linhas[i], irs = irmaos(ln), k = irs.indexOf(ln), j = k + d;
      if (j < 0 || j >= irs.length) return;
      [irs[k], irs[j]] = [irs[j], irs[k]];
      salvarOrdem(irs.map((x, o) => ({ conta_id: x.conta_id, ordem: (o + 1) * 10 })));
    };
    $$("#fx .fx-sobe").forEach((b) => b.onclick = () => mover(Number(b.dataset.i), -1));
    $$("#fx .fx-desce").forEach((b) => b.onclick = () => mover(Number(b.dataset.i), 1));
    $$("#fx .fx-sec").forEach((sel) => sel.onchange = () => { const ln = r.linhas[Number(sel.dataset.i)]; salvarOrdem([{ conta_id: ln.conta_id, secao: sel.value, ordem: 9999 }]); });
  };
  $("#fx-zeros").onchange = (x) => { st.zeros = x.target.checked; desenhar(); };
  $("#fx-org").onclick = () => { st.organizar = !st.organizar; $("#fx-org").textContent = st.organizar ? "Concluir organização" : "Organizar ordem"; $("#fx-org").classList.toggle("pri", st.organizar); desenhar(); };
  $("#fx-xls").onclick = () => { const p = periodo(); location.href = `/api/empresas/${e.id}/fluxo.xlsx?inicio=${p.inicio}&fim=${p.fim}`; };
  await tentar(carregar);
}

// ------------------------------------------------------------------ custo dos produtos (CMV)
async function abaCustos(e) {
  const filtros = { q: "", sem: false };
  let vig = sessionStorage.getItem(`ib_vig_${e.id}`) || periodo().inicio;
  $("#aba").innerHTML = `<div class="card">
    <div class="linha entre" style="align-items:flex-end">${seletorPeriodo(() => carregar())}
      <div class="linha" style="align-items:flex-end"><label>Custos a partir de <input type="month" id="cx-vig" value="${vig}"></label>
      <button class="btn" id="cx-baixar">Baixar planilha</button><label class="btn" style="flex-direction:row">Importar planilha<input type="file" id="cx-imp" accept=".xlsx" hidden></label></div></div>
    <div class="info peq" style="margin-top:12px">CMV de cada mês = (quantidade vendida − devolvida) × <b>custo vigente naquele mês</b>. Cada custo vale a partir do mês informado até o próximo custo do produto.
      Escolha o mês em <b>Custos a partir de</b> e digite os custos; o que você digitar vale desse mês em diante. Para vários produtos, use a planilha (coluna Vigência). O botão <b>Histórico</b> mostra e apaga vigências.</div>
    <div id="cx-kpi" class="kpi"></div>
    <div class="linha" style="margin-bottom:10px"><input id="cx-q" placeholder="Buscar produto..." style="flex:1;min-width:180px">
      <label class="chk"><input type="checkbox" id="cx-sem"> Só com mês sem custo</label></div>
    <div class="rolagem"><table id="cx-tab"></table></div></div>
    <div class="card" id="cx-conf"></div>`;
  let r = null;
  const carregar = async () => { const p = periodo(); r = await api(`/api/empresas/${e.id}/produtos?inicio=${p.inicio}&fim=${p.fim}&vigencia=${vig}`); desenhar(); };
  const desenhar = () => {
    $("#cx-kpi").innerHTML = `<div>Produtos<b>${r.itens.length}</b></div><div>Faturamento dos itens<b>R$ ${brl(r.totais.valor_vendido)}</b></div>
      <div>CMV no período<b>R$ ${brl(r.totais.cmv)}</b></div>
      <div style="${r.sem_custo.produtos ? "border-color:#F1D9A6;background:var(--amarelo-cl)" : ""}">Vendidos sem custo<b>${r.sem_custo.produtos}</b><span class="peq suave">R$ ${brl(r.sem_custo.faturamento)}</span></div>`;
    const cf = r.conferencia || [];
    const tn = cf.reduce((a, x) => a + x.notas, 0), ti = cf.reduce((a, x) => a + x.itens, 0);
    $("#cx-conf").innerHTML = cf.length ? `<h2>Conferência: notas × itens (vendas)</h2>
      <div class="suave peq" style="margin-bottom:8px"><b>Valor das notas</b> = valor contábil das notas de venda (é o que vai para a DRE). <b>Valor dos itens</b> = soma do valor dos produtos nos itens (base desta aba).
        Diferença positiva costuma ser desconto na nota ou nota cancelada com itens; negativa, frete/IPI/ST somados na nota.</div>
      <div class="rolagem" style="max-height:none"><table><tr><th>Mês</th><th class="num">Notas</th><th class="num">Valor das notas</th><th class="num">Valor dos itens</th><th class="num">Diferença</th><th class="num">%</th></tr>
      ${cf.map((x) => `<tr><td>${mesBR(x.mes)}</td><td class="num">${x.qtd_notas}</td><td class="num">${brl(x.notas)}</td><td class="num">${brl(x.itens)}</td>
        <td class="num ${Math.abs(x.diferenca) > 0.01 ? (x.diferenca > 0 ? "" : "neg") : "suave"}">${brl(x.diferenca)}</td><td class="num suave">${x.notas ? pct(x.diferenca / x.notas * 100) : ""}</td></tr>`).join("")}
      <tr class="destaque" style="font-weight:700"><td>Total</td><td></td><td class="num">${brl(tn)}</td><td class="num">${brl(ti)}</td><td class="num">${brl(ti - tn)}</td><td class="num">${tn ? pct((ti - tn) / tn * 100) : ""}</td></tr></table></div>` : "";
    const q = filtros.q.toLowerCase();
    const lin = r.itens.filter((i) => (!filtros.sem || i.meses_sem_custo.length) && (!q || `${i.codigo} ${i.descricao}`.toLowerCase().includes(q)));
    if (!r.itens.length) { $("#cx-tab").innerHTML = `<tr><td class="vazio">Nenhum produto ainda. Leia a Domínio na aba Conexões (precisa do Agente 360 na versão 0.14.0 ou superior).</td></tr>`; return; }
    $("#cx-tab").innerHTML = `<tr><th>Código</th><th style="min-width:240px">Descrição</th><th>Un</th><th class="num">Qtd vendida</th><th class="num">Qtd devolvida</th><th class="num">Faturamento</th><th class="num">Preço médio</th>
      <th class="num" style="width:170px">Custo a partir de ${mesBR(vig)}</th><th class="num">CMV</th><th class="num">Margem</th><th></th></tr>`
      + lin.slice(0, 1500).map((i) => {
        const pm = i.qtd_vendida ? i.valor_vendido / i.qtd_vendida : null;
        const herdado = i.custo != null && !i.custo_proprio_mes;
        return `<tr data-id="${i.id}"><td class="mono">${esc(i.codigo)}</td><td>${esc(i.descricao)}${i.meses_sem_custo.length ? `<br><span class="badge b-aviso" title="${i.meses_sem_custo.map(mesBR).join(", ")}">sem custo em ${i.meses_sem_custo.length} mês(es)</span>` : ""}</td><td>${esc(i.unidade)}</td>
          <td class="num">${qtdBR(i.qtd_vendida)}</td><td class="num">${i.qtd_devolvida ? qtdBR(i.qtd_devolvida) : ""}</td><td class="num">${brl(i.valor_vendido)}</td>
          <td class="num">${pm == null ? "" : brl(pm)}</td>
          <td class="num"><input class="cx-custo" inputmode="decimal" style="width:120px;text-align:right;${herdado ? "color:var(--suave)" : ""}" value="${i.custo == null ? "" : brl4(i.custo)}" placeholder="informar">
            ${herdado ? `<div class="peq suave">vigente desde ${mesBR(i.custo_desde)}</div>` : ""}</td>
          <td class="num">${i.cmv ? brl(i.cmv) : ""}</td><td class="num ${i.margem != null && i.margem < 0 ? "neg" : ""}">${i.margem == null ? "" : pct(i.margem)}</td>
          <td><button class="btn peq cx-hist">Histórico${i.vigencias > 1 ? ` (${i.vigencias})` : ""}</button></td></tr>`;
      }).join("") + (lin.length > 1500 ? `<tr><td colspan="11" class="suave">Mostrando 1.500 de ${lin.length}. Use a busca ou a planilha.</td></tr>` : "");
    $$("#cx-tab tr[data-id]").forEach((tr) => {
      const i = r.itens.find((x) => x.id === Number(tr.dataset.id));
      const inp = $(".cx-custo", tr);
      const original = inp.value;
      const salvar = () => tentar(async () => {
        const novo = inp.value.trim();
        if (novo === original) return;
        if (!novo && !i.custo_proprio_mes) { inp.value = original; return; }
        await api(`/api/produtos/${i.id}`, { method: "PUT", body: { custo: novo || null, vigencia: vig } });
        tr.style.background = "#E3F5EF";
        await carregar();
      });
      inp.addEventListener("change", salvar);
      inp.addEventListener("keydown", (ev) => {
        if (ev.key === "Enter") { ev.preventDefault(); const prox = tr.nextElementSibling && $(".cx-custo", tr.nextElementSibling); inp.blur(); if (prox) prox.focus(); }
      });
      $(".cx-hist", tr).onclick = () => historicoCusto(i, () => carregar());
    });
  };
  $("#cx-vig").onchange = (x) => { if (!x.target.value) return; vig = x.target.value; sessionStorage.setItem(`ib_vig_${e.id}`, vig); tentar(carregar); };
  $("#cx-q").oninput = (x) => { filtros.q = x.target.value; desenhar(); };
  $("#cx-sem").onchange = (x) => { filtros.sem = x.target.checked; desenhar(); };
  $("#cx-baixar").onclick = () => { const p = periodo(); location.href = `/api/empresas/${e.id}/produtos.xlsx?inicio=${p.inicio}&fim=${p.fim}&vigencia=${vig}`; };
  $("#cx-imp").onchange = (ev) => {
    const f = ev.target.files[0];
    if (!f) return;
    const rd = new FileReader();
    rd.onload = () => tentar(async () => {
      const b64 = rd.result.split(",")[1];
      const res = await api(`/api/empresas/${e.id}/produtos/importar`, { method: "POST", body: { arquivo: b64, vigencia: vig } });
      toast(`${res.atualizados} custo(s) gravado(s)` + (res.meses.length ? ` (a partir de ${res.meses.map(mesBR).join(", ")})` : "") + "."
        + (res.nao_encontrados.length ? ` Não encontrados: ${res.nao_encontrados.slice(0, 5).join(", ")}...` : ""));
      ev.target.value = ""; await carregar();
    });
    rd.readAsDataURL(f);
  };
  await tentar(carregar);
}

async function historicoCusto(i, aoMudar) {
  const m = modal(`<div class="linha entre"><div><h2 style="margin:0">${esc(i.codigo)} · ${esc(i.descricao)}</h2><div class="suave peq">Custos por vigência — cada um vale do mês indicado até o próximo.</div></div><button class="btn" data-fechar>Fechar</button></div>
    <div class="linha" style="margin:14px 0;align-items:flex-end"><label>A partir de <input type="month" id="h-mes" value="${periodo().inicio}"></label>
      <label>Custo unitário <input id="h-custo" inputmode="decimal" style="width:130px"></label><button class="btn pri" id="h-add">Gravar</button></div>
    <div id="h-lista" class="suave">Carregando...</div>`, true);
  const carregar = async () => {
    const h = await api(`/api/produtos/${i.id}/custos`);
    $("#h-lista", m).innerHTML = h.custos.length ? `<table><tr><th>A partir de</th><th class="num">Custo</th><th>Lançado por</th><th></th></tr>${h.custos.map((c) => `<tr>
      <td>${mesBR(c.mes_inicio === "2000-01" ? "" : c.mes_inicio) || "início"}</td><td class="num">${brl4(c.custo)}</td><td class="peq suave">${esc(c.criado_por)} · ${dataHora(c.criado_em)}</td>
      <td style="text-align:right"><button class="btn peq perigo" data-id="${c.id}">Apagar</button></td></tr>`).join("")}</table>` : "Nenhum custo informado.";
    $("#h-lista", m).classList.remove("suave");
    $$("#h-lista button[data-id]", m).forEach((b) => b.onclick = () => tentar(async () => {
      await api(`/api/produtos/${i.id}/custos/${b.dataset.id}`, { method: "DELETE" }); await carregar(); aoMudar();
    }, b));
  };
  $("#h-add", m).onclick = (ev) => tentar(async () => {
    if (!$("#h-custo", m).value.trim()) return toast("Informe o custo.", true);
    await api(`/api/produtos/${i.id}`, { method: "PUT", body: { custo: $("#h-custo", m).value, vigencia: $("#h-mes", m).value } });
    $("#h-custo", m).value = ""; await carregar(); aoMudar();
  }, ev.target);
  await tentar(carregar);
}

// ------------------------------------------------------------------ modelos de DRE
async function telaModelos() {
  casca("modelos", `<div class="topo"><div><h1>Modelos de DRE</h1><div class="suave">A estrutura da DRE: linhas que recebem contas, totais e títulos. Cada empresa usa um modelo.</div></div>
    <div class="linha"><button class="btn" id="novo-ml">+ Modelo Mercado Livre</button><button class="btn pri" id="novo">+ Novo modelo</button></div></div><div id="lista"></div>`);
  const ms = await api("/api/modelos");
  $("#lista").innerHTML = `<div class="card"><table><tr><th>Modelo</th><th>Linhas</th><th>Empresas usando</th><th></th></tr>${ms.map((m) => `<tr>
    <td><a href="#/modelo/${m.id}"><b>${esc(m.nome)}</b></a><div class="suave peq">${esc(m.descricao)}</div></td><td>${m.linhas.length}</td><td>${m.empresas}</td>
    <td style="text-align:right"><a class="btn peq" href="#/modelo/${m.id}">Editar</a> <button class="btn peq dup" data-id="${m.id}" data-nome="${esc(m.nome)}">Duplicar</button></td></tr>`).join("")}</table></div>`;
  const novo = (copiar, nome, mlivre = false) => tentar(async () => {
    const n = prompt("Nome do novo modelo:", copiar ? `${nome} (cópia)` : mlivre ? "DRE Mercado Livre" : "Meu modelo de DRE");
    if (!n) return;
    const m = await api("/api/modelos", { method: "POST", body: { nome: n, copiar_de: copiar || null, mlivre } });
    location.hash = `#/modelo/${m.id}`;
  });
  $("#novo").onclick = () => novo(null);
  $("#novo-ml").onclick = () => novo(null, "", true);
  $$(".dup").forEach((b) => b.onclick = () => novo(Number(b.dataset.id), b.dataset.nome));
}

function codigoDe(nome, existentes) {
  let base = (nome || "").normalize("NFD").replace(/[̀-ͯ]/g, "").toUpperCase().replace(/\([^)]*\)/g, " ")
    .replace(/[^A-Z0-9]+/g, " ").trim().split(" ").filter((w) => w.length > 3 || /\d/.test(w)).slice(0, 3)
    .map((w, i, a) => (a.length === 1 ? w.slice(0, 12) : w.slice(0, 5))).join("_") || "LINHA";
  let c = base, n = 2;
  while (existentes.has(c)) c = `${base}_${n++}`;
  return c;
}

async function telaModelo(id) {
  const m = await api(`/api/modelos/${id}`);
  casca("modelos", `<div class="topo"><div><a href="#/modelos" class="peq">← Modelos</a><h1>Editar modelo</h1></div>
    <div class="linha">${EU.perfil === "ADMIN" ? '<button class="btn perigo" id="m-del">Excluir</button>' : ""}<button class="btn" id="m-novo">+ Novo</button><button class="btn" id="m-salvar" title="Normalmente não é preciso: tudo grava sozinho">Salvar agora</button></div></div>
    <div class="card"><div class="form" style="grid-template-columns:1fr 2fr"><label>Nome <input id="m-nome" value="${esc(m.nome)}"></label><label>Descrição <input id="m-desc" value="${esc(m.descricao)}"></label></div></div>
    <div class="info peq">A DRE é feita de <b>grupos</b> (o total do grupo é a soma automática das contas dele), <b>contas</b> (sempre dentro de um grupo; recebem as categorias do Bling e da Domínio) e <b>resultados</b> (fórmula com os grupos e resultados de cima, ex.: <span class="mono">RB + DED</span>).
      Clique em <b>+ Novo</b> para criar; a conta nova entra no fim do grupo escolhido. As setas mudam a posição dentro do grupo. Marque <b>AV</b> na linha que vale 100% na análise vertical.
      <b>Tudo é gravado automaticamente</b> (veja o aviso "✓ Salvo" no fim da página).${m.empresas ? ` Este modelo é usado por ${m.empresas} empresa(s): excluir uma conta apaga as associações dela.` : ""}</div>
    <div class="card"><div class="rolagem" style="max-height:none"><table class="editor" id="ed"></table></div>
    <div class="linha" style="margin-top:12px"><button class="btn" id="m-novo2">+ Novo</button><span class="peq" id="m-sujo" style="font-weight:600"></span></div></div>`);
  const L = m.linhas.map((x) => ({ ...x }));
  let sujo = false, timer = null, salvando = null;
  const status = (t, cor) => { const el = $("#m-sujo"); if (el) { el.textContent = t; el.style.color = cor || ""; } };
  const salvar = async () => {
    clearTimeout(timer);
    if (salvando) await salvando.catch(() => {});
    salvando = (async () => {
      status("Salvando...");
      const r = await api(`/api/modelos/${id}`, { method: "PUT", body: { nome: $("#m-nome").value, descricao: $("#m-desc").value, linhas: L } });
      const porCod = new Map(r.linhas.map((x) => [x.codigo, x.id]));
      L.forEach((x) => { if (porCod.has(x.codigo)) x.id = porCod.get(x.codigo); });
      sujo = false; status("✓ Salvo automaticamente", "var(--verde)");
    })();
    try { await salvando; } catch (e) { status(`Não salvo: ${e.message}`, "var(--vermelho)"); throw e; } finally { salvando = null; }
  };
  const marcar = (espera = 600) => {  // toda alteração grava sozinha
    sujo = true; status("Alterações não salvas...");
    clearTimeout(timer);
    timer = setTimeout(() => salvar().catch(() => {}), espera);
  };
  const editando = () => { sujo = true; clearTimeout(timer); status("Alterações não salvas..."); };
  const topo = () => L.filter((x) => x.tipo !== "CONTA");
  const filhos = (g) => L.filter((x) => x.tipo === "CONTA" && x.pai_codigo === g);
  const reordenar = (tops) => {  // reconstrói a lista: cada grupo seguido das suas contas
    const nova = [];
    for (const t of tops) { nova.push(t); if (t.tipo === "GRUPO") nova.push(...filhos(t.codigo)); }
    L.splice(0, L.length, ...nova);
  };
  const tirarDaFormula = (cod) => {
    for (const t of L) if (t.tipo === "TOTAL" && t.formula) {
      t.formula = t.formula.replace(new RegExp(`\\s*[+-]?\\s*\\b${cod}\\b`, "g"), "").replace(/^\s*\+\s*/, "").trim();
    }
  };
  const renomear = (de, para) => {
    for (const x of L) {
      if (x.pai_codigo === de) x.pai_codigo = para;
      if (x.tipo === "TOTAL" && x.formula) x.formula = x.formula.replace(new RegExp(`\\b${de}\\b`, "g"), para);
    }
  };
  const sinalSel = (x) => `<select class="f-sinal"><option value="0" ${!x.sinal ? "selected" : ""}>Automático</option><option value="1" ${x.sinal === 1 ? "selected" : ""}>Sempre soma (+)</option><option value="-1" ${x.sinal === -1 ? "selected" : ""}>Sempre subtrai (−)</option></select>`;
  const desenhar = () => {
    const grupos = topo().filter((x) => x.tipo === "GRUPO");
    let h = `<tr><th style="width:84px"></th><th style="width:130px">Código</th><th>Nome</th><th style="width:240px">Cálculo</th><th style="width:40px">AV</th><th style="width:220px">Grupo</th></tr>`;
    for (const t of topo()) {
      const i = L.indexOf(t);
      if (t.tipo === "GRUPO") {
        const fs = filhos(t.codigo);
        h += `<tr class="g-linha" data-i="${i}" style="background:#EEF3F9">
          <td style="white-space:nowrap"><button class="btn peq t-sobe" title="Subir o grupo">↑</button><button class="btn peq t-desce" title="Descer o grupo">↓</button></td>
          <td><input class="mono f-cod" value="${esc(t.codigo)}"></td><td><input class="f-nome" value="${esc(t.nome)}" style="font-weight:700;color:var(--azul)"></td>
          <td class="peq suave">Grupo · soma automática de ${fs.length} conta(s)</td>
          <td style="text-align:center"><input type="radio" name="av" class="f-av" ${t.base_av ? "checked" : ""}></td>
          <td style="white-space:nowrap"><button class="btn peq g-add">+ Conta</button><button class="btn peq perigo del" title="Excluir grupo">✕</button></td></tr>`;
        fs.forEach((c, k) => {
          h += `<tr data-i="${L.indexOf(c)}">
            <td style="white-space:nowrap;padding-left:18px"><button class="btn peq c-sobe" ${k === 0 ? "disabled" : ""}>↑</button><button class="btn peq c-desce" ${k === fs.length - 1 ? "disabled" : ""}>↓</button></td>
            <td><input class="mono f-cod" value="${esc(c.codigo)}"></td><td style="padding-left:22px"><input class="f-nome" value="${esc(c.nome)}"></td>
            <td>${sinalSel(c)}</td><td></td>
            <td style="white-space:nowrap"><select class="c-grupo" title="Mover para outro grupo" style="width:170px">${grupos.map((g) => `<option value="${esc(g.codigo)}" ${g.codigo === c.pai_codigo ? "selected" : ""}>${esc(g.nome)}</option>`).join("")}</select><button class="btn peq perigo del" title="Excluir conta">✕</button></td></tr>`;
        });
        if (!fs.length) h += `<tr><td></td><td colspan="5" class="suave peq" style="padding-left:30px">Grupo sem contas — clique em + Conta.</td></tr>`;
      } else {
        h += `<tr class="TOTAL" data-i="${i}">
          <td style="white-space:nowrap"><button class="btn peq t-sobe">↑</button><button class="btn peq t-desce">↓</button></td>
          <td><input class="mono f-cod" value="${esc(t.codigo)}"></td><td><input class="f-nome" value="${esc(t.nome)}" style="font-weight:700"></td>
          <td><input class="mono f-form" value="${esc(t.formula)}" placeholder="RB + DED"></td>
          <td style="text-align:center"><input type="radio" name="av" class="f-av" ${t.base_av ? "checked" : ""}></td>
          <td><button class="btn peq perigo del" title="Excluir resultado">✕</button></td></tr>`;
      }
    }
    $("#ed").innerHTML = h;
    $$("#ed tr[data-i]").forEach((tr) => {
      const x = L[Number(tr.dataset.i)];
      const cod = $(".f-cod", tr);
      cod.onchange = () => {
        const novo = cod.value.toUpperCase().replace(/[^A-Z0-9_]/g, "");
        if (!novo || (novo !== x.codigo && L.some((y) => y.codigo === novo))) { toast("Código vazio ou repetido.", true); cod.value = x.codigo; return; }
        renomear(x.codigo, novo); x.codigo = novo; marcar(); desenhar();
      };
      $(".f-nome", tr).oninput = (ev) => { x.nome = ev.target.value; editando(); };
      $(".f-nome", tr).onchange = () => marcar(0);
      if ($(".f-sinal", tr)) $(".f-sinal", tr).onchange = (ev) => { x.sinal = Number(ev.target.value); marcar(); };
      if ($(".f-form", tr)) { $(".f-form", tr).oninput = (ev) => { x.formula = ev.target.value.toUpperCase(); editando(); }; $(".f-form", tr).onchange = () => marcar(0); }
      if ($(".f-av", tr)) $(".f-av", tr).onchange = () => { L.forEach((y) => { y.base_av = false; }); x.base_av = true; marcar(); };
      const moverTopo = (d) => { const ts = topo(); const k = ts.indexOf(x); const j = k + d; if (j < 0 || j >= ts.length) return; [ts[k], ts[j]] = [ts[j], ts[k]]; reordenar(ts); marcar(); desenhar(); };
      if ($(".t-sobe", tr)) { $(".t-sobe", tr).onclick = () => moverTopo(-1); $(".t-desce", tr).onclick = () => moverTopo(1); }
      const moverConta = (d) => {
        const fs = filhos(x.pai_codigo); const k = fs.indexOf(x); const y = fs[k + d]; if (!y) return;
        const a = L.indexOf(x), b = L.indexOf(y); [L[a], L[b]] = [L[b], L[a]]; marcar(); desenhar();
      };
      if ($(".c-sobe", tr)) { $(".c-sobe", tr).onclick = () => moverConta(-1); $(".c-desce", tr).onclick = () => moverConta(1); }
      if ($(".c-grupo", tr)) $(".c-grupo", tr).onchange = (ev) => { L.splice(L.indexOf(x), 1); x.pai_codigo = ev.target.value; L.push(x); reordenar(topo()); marcar(); desenhar(); };
      if ($(".g-add", tr)) $(".g-add", tr).onclick = () => novo("CONTA", x.codigo);
      $(".del", tr).onclick = () => {
        if (x.tipo === "GRUPO") {
          const fs = filhos(x.codigo);
          if (!confirm(`Excluir o grupo ${x.nome}${fs.length ? ` e as ${fs.length} conta(s) dele` : ""}? As associações dessas contas serão perdidas ao salvar.`)) return;
          fs.forEach((c) => L.splice(L.indexOf(c), 1));
        } else if (!confirm(`Excluir ${x.nome}?`)) return;
        L.splice(L.indexOf(x), 1); tirarDaFormula(x.codigo); marcar(); desenhar();
      };
    });
  };
  const formulaSugerida = (pos) => {  // resultado anterior + grupos entre ele e a posição
    const ts = topo().slice(0, pos + 1), termos = [];
    for (let k = ts.length - 1; k >= 0; k--) { termos.unshift(ts[k].codigo); if (ts[k].tipo === "TOTAL") break; }
    return termos.join(" + ");
  };
  const novo = (tipoIni, grupoIni) => {
    const ts = topo(), grupos = ts.filter((x) => x.tipo === "GRUPO");
    const md = modal(`<h2>Criar</h2>
      <div class="linha" style="margin-bottom:12px"><label class="chk"><input type="radio" name="n-tipo" value="CONTA"> Conta</label>
        <label class="chk"><input type="radio" name="n-tipo" value="GRUPO"> Grupo</label><label class="chk"><input type="radio" name="n-tipo" value="TOTAL"> Resultado (fórmula)</label></div>
      <div class="form" style="grid-template-columns:1fr">
        <label id="n-l-grupo">Em qual grupo? <select id="n-grupo">${grupos.map((g) => `<option value="${esc(g.codigo)}">${esc(g.nome)}</option>`).join("")}</select></label>
        <label>Nome <input id="n-nome" placeholder="Ex.: Marketing digital"></label>
        <label>Código <input id="n-cod" class="mono" placeholder="gerado pelo nome"></label>
        <label id="n-l-pos">Posição: depois de <select id="n-pos">${ts.map((t, k) => `<option value="${k}" ${k === ts.length - 1 ? "selected" : ""}>${esc(t.nome)}</option>`).join("")}</select></label>
        <label id="n-l-res" class="chk"><input type="checkbox" id="n-res" checked> <span id="n-res-txt"></span></label>
        <label id="n-l-form">Fórmula <input id="n-form" class="mono"></label>
      </div>
      <div class="linha" style="margin-top:14px"><button class="btn pri" id="n-ok">Criar</button><button class="btn" data-fechar>Cancelar</button></div>`, true);
    const tipo = () => ($("input[name=n-tipo]:checked", md) || {}).value;
    const proxResultado = (pos) => ts.slice(pos + 1).find((t) => t.tipo === "TOTAL");
    const atualizar = () => {
      const t = tipo();
      $("#n-l-grupo", md).style.display = t === "CONTA" ? "" : "none";
      $("#n-l-pos", md).style.display = t && t !== "CONTA" ? "" : "none";
      const pr = proxResultado(Number($("#n-pos", md).value));
      $("#n-l-res", md).style.display = t === "GRUPO" && pr ? "" : "none";
      if (pr) $("#n-res-txt", md).textContent = `Somar este grupo em "${pr.nome}" (fórmula ${pr.formula} + novo)`;
      $("#n-l-form", md).style.display = t === "TOTAL" ? "" : "none";
      if (t === "TOTAL" && !$("#n-form", md).dataset.mexeu) $("#n-form", md).value = formulaSugerida(Number($("#n-pos", md).value));
    };
    $$("input[name=n-tipo]", md).forEach((r) => { r.onchange = atualizar; if (r.value === tipoIni) r.checked = true; });
    if (grupoIni) $("#n-grupo", md).value = grupoIni;
    $("#n-pos", md).onchange = atualizar;
    $("#n-form", md).oninput = (ev) => { ev.target.dataset.mexeu = "1"; };
    $("#n-nome", md).oninput = () => { if (!$("#n-cod", md).dataset.mexeu) $("#n-cod", md).value = codigoDe($("#n-nome", md).value, new Set(L.map((x) => x.codigo))); };
    $("#n-cod", md).oninput = (ev) => { ev.target.dataset.mexeu = "1"; ev.target.value = ev.target.value.toUpperCase().replace(/[^A-Z0-9_]/g, ""); };
    atualizar();
    $("#n-nome", md).focus();
    $("#n-ok", md).onclick = () => {
      const t = tipo(), nome = $("#n-nome", md).value.trim();
      const cod = $("#n-cod", md).value || codigoDe(nome, new Set(L.map((x) => x.codigo)));
      if (!t) return toast("Escolha: conta, grupo ou resultado.", true);
      if (!nome) return toast("Informe o nome.", true);
      if (L.some((x) => x.codigo === cod)) return toast(`O código ${cod} já existe.`, true);
      if (t === "CONTA") {
        if (!grupos.length) return toast("Crie um grupo primeiro.", true);
        L.push({ codigo: cod, nome, tipo: "CONTA", sinal: 0, formula: "", destaque: false, base_av: false, pai_codigo: $("#n-grupo", md).value });
        reordenar(topo());
      } else {
        const pos = Number($("#n-pos", md).value);
        const item = { codigo: cod, nome, tipo: t, sinal: 0, formula: t === "TOTAL" ? $("#n-form", md).value.toUpperCase() : "", destaque: t === "TOTAL", base_av: false, pai_codigo: null };
        const nt = topo(); nt.splice(pos + 1, 0, item); L.push(item); reordenar(nt);
        const pr = proxResultado(pos);
        if (t === "GRUPO" && pr && $("#n-res", md).checked) pr.formula = `${pr.formula} + ${cod}`;
      }
      md.remove(); marcar(); desenhar();
      const linha = $$("#ed tr[data-i]").find((tr) => L[Number(tr.dataset.i)].codigo === cod);
      if (linha) { linha.style.background = "#E3F5EF"; linha.scrollIntoView({ block: "center" }); }
    };
  };
  $("#m-novo").onclick = () => novo();
  $("#m-novo2").onclick = () => novo();
  $("#m-salvar").onclick = (ev) => tentar(async () => { await salvar(); toast("Modelo salvo."); }, ev.target);
  $("#m-nome").onchange = () => marcar(0);
  $("#m-desc").onchange = () => marcar(0);
  if ($("#m-del")) $("#m-del").onclick = (ev) => tentar(async () => {
    if (!confirm("Excluir este modelo?")) return;
    await api(`/api/modelos/${id}`, { method: "DELETE" }); location.hash = "#/modelos";
  }, ev.target);
  window.onbeforeunload = () => (sujo ? true : null);
  pendenteModelo = () => (sujo ? salvar().catch(() => {}) : null);
  desenhar();
}

// ------------------------------------------------------------------ configurações (admin)
async function telaConfig() {
  const [c, us] = await Promise.all([api("/api/config"), api("/api/usuarios")]);
  casca("config", `<div class="topo"><div><h1>Configurações</h1></div></div>
    ${c.secret_key_no_ambiente ? "" : '<div class="aviso">A variável SECRET_KEY não está definida no servidor; os segredos estão cifrados com uma chave guardada no próprio banco.</div>'}
    <div class="card"><h2>Aplicativo do Bling (padrão)</h2>
      <div class="info peq">No Bling: <b>Central de Extensões → Área do Integrador → Criar aplicativo</b> (tipo API, uso privado). Em <b>Link de redirecionamento</b> coloque o endereço abaixo. Escopos (só leitura): <b>Categorias financeiras</b> e <b>Caixas e bancos</b>. Depois copie o Client ID e o Client Secret para cá.
      Um aplicativo privado só pode ser autorizado pela conta Bling que o criou; empresas com conta Bling própria podem ter o próprio aplicativo (no cadastro da empresa).</div>
      <label>Link de redirecionamento (cole no Bling)<div class="linha"><input readonly value="${esc(c.redirect_uri)}" style="flex:1" id="cf-redir"><button class="btn" id="cf-copiar">Copiar</button></div></label>
      <div class="form" style="margin-top:12px"><label>Client ID <input id="cf-cid" value="${esc(c.bling_client_id)}"></label>
      <label>Client Secret <input id="cf-sec" type="password" placeholder="${c.bling_secret_definido ? "(guardado — preencha para trocar)" : ""}"></label></div>
      <div class="linha" style="margin-top:12px"><button class="btn pri" id="cf-bling">Salvar</button></div></div>
    <div class="card"><h2>Aplicativo do Mercado Livre</h2>
      <div class="info peq">Em <b>developers.mercadolivre.com.br → Minhas aplicações → Criar nova aplicação</b> (logado na conta de vendedor). Em <b>URI de redirect</b> coloque o endereço abaixo; permissões só de <b>leitura</b> (vendas, faturamento, pagamentos) e acesso offline. Depois copie o Client ID e a Secret Key para cá. Um aplicativo serve para todas as empresas: cada uma autoriza com a própria conta.</div>
      <label>URI de redirect (cole no Mercado Livre)<div class="linha"><input readonly value="${esc(c.ml_redirect_uri)}" style="flex:1"><button class="btn" id="cf-ml-copiar">Copiar</button></div></label>
      <div class="form" style="margin-top:12px"><label>Client ID <input id="cf-ml-cid" value="${esc(c.ml_client_id)}"></label>
      <label>Secret Key <input id="cf-ml-sec" type="password" placeholder="${c.ml_secret_definido ? "(guardada — preencha para trocar)" : ""}"></label></div>
      <div class="linha" style="margin-top:12px"><button class="btn pri" id="cf-ml">Salvar</button></div></div>
    <div class="card"><h2>Central de Inteligência 360 (Domínio)</h2>
      <div class="info peq">Na Central: <b>Administração → Gerar chave</b>, tipo <b>Integra Bling</b>. A chave só lê: lista de clientes e faturamento/devoluções da Domínio pelo Agente 360.</div>
      <div class="form"><label>Endereço da Central <input id="cf-url" value="${esc(c.central_url)}" placeholder="https://..."></label>
      <label>Chave de API <input id="cf-chave" type="password" placeholder="${c.central_chave_definida ? "(guardada — preencha para trocar)" : "f360_..."}"></label></div>
      <div class="linha" style="margin-top:12px"><button class="btn pri" id="cf-central">Salvar</button><button class="btn" id="cf-testar">Testar conexão</button></div></div>
    <div class="card"><div class="linha entre"><h2>Usuários</h2><button class="btn" id="u-novo">+ Usuário</button></div>
      <table><tr><th>Nome</th><th>E-mail</th><th>Perfil</th><th>Situação</th><th></th></tr>${us.map((u) => `<tr><td>${esc(u.nome)}</td><td>${esc(u.email)}</td>
        <td>${u.perfil === "ADMIN" ? "Administrador" : "Operador"}</td><td>${u.ativo ? '<span class="badge b-ok">ativo</span>' : '<span class="badge">inativo</span>'}</td>
        <td style="text-align:right"><button class="btn peq u-ed" data-id="${u.id}">Editar</button></td></tr>`).join("")}</table>
      <div class="suave peq" style="margin-top:8px">Operador usa tudo, menos Configurações e exclusões. Administrador acessa tudo.</div></div>`);
  $("#cf-copiar").onclick = () => { navigator.clipboard.writeText(c.redirect_uri).then(() => toast("Copiado.")); };
  $("#cf-bling").onclick = (ev) => tentar(async () => {
    const b = { bling_client_id: $("#cf-cid").value };
    if ($("#cf-sec").value) b.bling_client_secret = $("#cf-sec").value;
    await api("/api/config", { method: "PUT", body: b }); toast("Aplicativo do Bling salvo."); telaConfig();
  }, ev.target);
  $("#cf-ml-copiar").onclick = () => { navigator.clipboard.writeText(c.ml_redirect_uri).then(() => toast("Copiado.")); };
  $("#cf-ml").onclick = (ev) => tentar(async () => {
    const b = { ml_client_id: $("#cf-ml-cid").value };
    if ($("#cf-ml-sec").value) b.ml_client_secret = $("#cf-ml-sec").value;
    await api("/api/config", { method: "PUT", body: b }); toast("Aplicativo do Mercado Livre salvo."); telaConfig();
  }, ev.target);
  $("#cf-central").onclick = (ev) => tentar(async () => {
    const b = { central_url: $("#cf-url").value };
    if ($("#cf-chave").value) b.central_chave = $("#cf-chave").value;
    await api("/api/config", { method: "PUT", body: b }); toast("Central salva."); telaConfig();
  }, ev.target);
  $("#cf-testar").onclick = (ev) => tentar(async () => {
    const r = await api("/api/config/testar-central", { method: "POST" }); toast(`Conectado: ${r.clientes} clientes na Central.`);
  }, ev.target);
  const formU = (u) => {
    const m = modal(`<h2>${u ? "Editar usuário" : "Novo usuário"}</h2><div class="form" style="grid-template-columns:1fr">
      <label>Nome <input id="u-nome" value="${esc(u?.nome)}"></label>
      ${u ? "" : '<label>E-mail <input id="u-email" type="email"></label>'}
      <label>${u ? "Nova senha (deixe em branco para manter)" : "Senha (mínimo 8)"} <input id="u-senha" type="password"></label>
      <label>Perfil <select id="u-perfil"><option value="OPERADOR">Operador</option><option value="ADMIN">Administrador</option></select></label>
      ${u ? `<label class="chk"><input type="checkbox" id="u-ativo" ${u.ativo ? "checked" : ""}> Ativo</label>` : ""}</div>
      <div class="linha" style="margin-top:14px"><button class="btn pri" id="u-ok">Salvar</button><button class="btn" data-fechar>Cancelar</button></div>`, true);
    $("#u-perfil", m).value = u?.perfil || "OPERADOR";
    $("#u-ok", m).onclick = (ev) => tentar(async () => {
      const b = { nome: $("#u-nome", m).value, perfil: $("#u-perfil", m).value };
      if ($("#u-senha", m).value) b.senha = $("#u-senha", m).value;
      if (u) b.ativo = $("#u-ativo", m).checked; else b.email = $("#u-email", m).value;
      await api(u ? `/api/usuarios/${u.id}` : "/api/usuarios", { method: u ? "PUT" : "POST", body: b });
      m.remove(); toast("Usuário salvo."); telaConfig();
    }, ev.target);
  };
  $("#u-novo").onclick = () => formU(null);
  $$(".u-ed").forEach((b) => b.onclick = () => formU(us.find((x) => x.id === Number(b.dataset.id))));
}

// ------------------------------------------------------------------ rotas
async function rota() {
  if (pendenteModelo) { const p = pendenteModelo(); pendenteModelo = null; if (p) await p; }
  $$(".modal-fundo").forEach((m) => m.remove());
  window.onbeforeunload = null;
  const [caminho, qs] = (location.hash.slice(1) || "/empresas").split("?");
  const params = new URLSearchParams(qs || "");
  const p = caminho.split("/").filter(Boolean);
  try {
    if (!EU) {
      const est = await api("/api/estado");
      EU = est.usuario;
      if (!EU) return telaLogin(est.precisa_setup);
    }
    if (p[0] === "login") { location.hash = "#/empresas"; return; }
    if (p[0] === "empresa" && p[1]) return await telaEmpresa(Number(p[1]), p[2] || "conexoes", params);
    if (p[0] === "modelos") return await telaModelos();
    if (p[0] === "modelo" && p[1]) return await telaModelo(Number(p[1]));
    if (p[0] === "config") return await telaConfig();
    return await telaEmpresas();
  } catch (e) {
    if (!EU) return;
    toast(e.message, true);
  }
}
window.addEventListener("hashchange", rota);
rota();
