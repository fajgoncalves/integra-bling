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
const ORIGEM = { BLING: '<span class="badge b-bling">Bling</span>', DOMINIO: '<span class="badge b-dom">Domínio</span>' };
const TIPO = { R: "Receita", D: "Despesa", RD: "Receita e despesa" };
let EU = null;

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
        <span class="badge">${b.associadas}/${b.total} categorias associadas</span>
      </div>
      ${e.ultimo_job ? `<div class="suave peq" style="margin-top:8px">${statusJob(e.ultimo_job)} ${esc(e.ultimo_job.mensagem).slice(0, 120)}</div>` : ""}
    </div>`;
  }).join("") : `<div class="card vazio">Nenhuma empresa ainda. Clique em <b>Nova empresa</b>.</div>`;
  $$(".emp").forEach((c) => c.onclick = () => { location.hash = `#/empresa/${c.dataset.id}`; });
}

function statusJob(j) {
  const cls = { OK: "b-ok", ERRO: "b-erro", EXECUTANDO: "b-aviso", PENDENTE: "b-aviso" }[j.status] || "";
  const nome = { bling_sync: "Bling", dominio_importar: "Domínio" }[j.tipo] || j.tipo;
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
      modelo_id: Number($("#e-mod", m).value), bling_client_id: $("#e-cid", m).value };
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
  const abas = [["conexoes", "Conexões e dados"], ["associacao", "Associação de contas"], ["custos", "Custo dos produtos"], ["dre", "DRE"]];
  casca("empresas", `<div class="topo"><div><a href="#/empresas" class="peq">← Empresas</a><h1>${esc(e.nome)}</h1>
      <div class="suave">${cnpjFmt(e.cnpj) || "sem CNPJ"} · Domínio ${esc(e.codigo_dominio || "—")} · Modelo: ${esc(e.modelo_nome || "—")}</div></div>
      <button class="btn" id="editar">Editar empresa</button></div>
    <div class="abas">${abas.map(([k, t]) => `<a href="#/empresa/${id}/${k}" class="${aba === k ? "ativo" : ""}">${t}</a>`).join("")}</div>
    <div id="aba"></div>`);
  $("#editar").onclick = () => formEmpresa(e);
  if (params.get("bling") === "ok") toast("Bling conectado. Agora atualize os dados do período.");
  if (params.get("bling") === "erro") toast(`Bling: ${params.get("msg") || "não foi possível conectar"}`, true);
  if (aba === "associacao") return abaAssociacao(e);
  if (aba === "dre") return abaDre(e);
  if (aba === "custos") return abaCustos(e);
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
      <div class="suave peq" style="margin-top:10px">Lê as categorias financeiras e os lançamentos de <b>Caixa e Bancos</b> do período — só o que foi efetivamente pago ou recebido (somente leitura). Contas a pagar e a receber em aberto não entram. Pode levar alguns minutos.</div>
    </div>
    <div class="card"><h2>Domínio (Central 360)</h2>
      <div class="suave peq" style="margin-bottom:10px">Faturamento (notas de saída e serviços), devoluções por CFOP e os <b>produtos de cada nota</b> (base do CMV), lidos pelo Agente 360 no banco da Domínio.</div>
      <div class="suave peq" style="margin-bottom:10px">Última leitura: ${e.dominio_sync_em ? `${dataHora(e.dominio_sync_em)} (${esc(e.dominio_sync_periodo)})` : "nunca"}</div>
      <div class="linha"><label>De <input type="month" id="d-ini" value="${p.inicio}"></label><label>Até <input type="month" id="d-fim" value="${p.fim}"></label></div>
      <div class="linha" style="margin-top:12px"><button class="btn pri" id="d-imp">Ler da Domínio</button></div>
      ${!e.cnpj && !e.codigo_dominio ? '<div class="aviso peq" style="margin-top:10px">Informe o CNPJ ou o código da Domínio no cadastro.</div>' : ""}
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
  carregarJobs(e.id);
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
      <select id="f-o"><option value="">Bling e Domínio</option><option value="BLING">Só Bling</option><option value="DOMINIO">Só Domínio</option></select>
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
    let html = '<option value="">— não entra na DRE —</option>', grupo = null;
    for (const ln of modelo.linhas) {
      if (ln.tipo === "TITULO") { if (grupo) html += "</optgroup>"; html += `<optgroup label="${esc(ln.nome)}">`; grupo = 1; }
      if (ln.tipo === "CONTA") html += `<option value="${ln.id}">${esc(ln.nome)}</option>`;
    }
    return html + (grupo ? "</optgroup>" : "");
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
  const m = modal(`<div class="linha entre"><div><h2 style="margin:0">${esc(c.nome)}</h2><div class="suave peq">${ORIGEM[c.origem]} ${mesBR(p.inicio)} a ${mesBR(p.fim)} · ${c.origem === "BLING" ? "caixa e bancos (pagos e recebidos)" : "notas fiscais"}</div></div>
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
  const abertas = new Set();
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
      + `<div class="suave peq">Bling pelo caixa (pagos e recebidos em ${esc(r.empresa.bling_sync_periodo || "—")}) · Domínio pelas notas fiscais (${esc(r.empresa.dominio_sync_periodo || "—")}) · Base da análise vertical: ${esc(r.base_av || "—")}. Clique numa linha para ver de onde vêm os valores.</div>`;
    const cols = r.meses.length + 2 + (av ? 1 : 0);
    const cel = (v) => `<td class="num ${v < 0 ? "neg" : ""}">${v ? brl(v) : '<span class="suave">–</span>'}</td>`;
    let html = `<div class="rolagem"><table class="dre"><tr><th>Linha</th>${r.meses.map((m) => `<th class="num">${mesBR(m)}</th>`).join("")}<th class="num">Total</th>${av ? '<th class="num">AV</th>' : ""}</tr>`;
    for (const ln of r.linhas) {
      if (ln.tipo === "TITULO") { html += `<tr class="titulo"><td colspan="${cols}">${esc(ln.nome)}</td></tr>`; continue; }
      const temComp = ln.composicao.length > 0;
      html += `<tr class="${ln.destaque ? "destaque" : ""}" data-l="${ln.id}"><td class="${temComp ? "clicavel" : ""}">${temComp ? (abertas.has(ln.id) ? "▾ " : "▸ ") : ""}${esc(ln.nome)}</td>
        ${ln.valores.map(cel).join("")}${cel(ln.total)}${av ? `<td class="num suave">${pct(ln.av_total)}</td>` : ""}</tr>`;
      if (abertas.has(ln.id)) for (const c of ln.composicao) {
        html += `<tr class="comp"><td>${c.origem === "BLING" ? "Bling" : "Domínio"} · ${esc(c.descricao)}</td>${c.valores.map(cel).join("")}${cel(c.total)}${av ? "<td></td>" : ""}</tr>`;
      }
    }
    $("#dre").innerHTML = html + "</table></div>";
    $("#dre").classList.remove("suave");
    $$("#dre tr[data-l] td.clicavel").forEach((td) => td.onclick = () => {
      const id = Number(td.parentElement.dataset.l);
      if (abertas.has(id)) abertas.delete(id); else abertas.add(id);
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
    <div class="rolagem"><table id="cx-tab"></table></div></div>`;
  let r = null;
  const carregar = async () => { const p = periodo(); r = await api(`/api/empresas/${e.id}/produtos?inicio=${p.inicio}&fim=${p.fim}&vigencia=${vig}`); desenhar(); };
  const desenhar = () => {
    $("#cx-kpi").innerHTML = `<div>Produtos<b>${r.itens.length}</b></div><div>Faturamento dos itens<b>R$ ${brl(r.totais.valor_vendido)}</b></div>
      <div>CMV no período<b>R$ ${brl(r.totais.cmv)}</b></div>
      <div style="${r.sem_custo.produtos ? "border-color:#F1D9A6;background:var(--amarelo-cl)" : ""}">Vendidos sem custo<b>${r.sem_custo.produtos}</b><span class="peq suave">R$ ${brl(r.sem_custo.faturamento)}</span></div>`;
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
    <button class="btn pri" id="novo">+ Novo modelo</button></div><div id="lista"></div>`);
  const ms = await api("/api/modelos");
  $("#lista").innerHTML = `<div class="card"><table><tr><th>Modelo</th><th>Linhas</th><th>Empresas usando</th><th></th></tr>${ms.map((m) => `<tr>
    <td><a href="#/modelo/${m.id}"><b>${esc(m.nome)}</b></a><div class="suave peq">${esc(m.descricao)}</div></td><td>${m.linhas.length}</td><td>${m.empresas}</td>
    <td style="text-align:right"><a class="btn peq" href="#/modelo/${m.id}">Editar</a> <button class="btn peq dup" data-id="${m.id}" data-nome="${esc(m.nome)}">Duplicar</button></td></tr>`).join("")}</table></div>`;
  const novo = (copiar, nome) => tentar(async () => {
    const n = prompt("Nome do novo modelo:", copiar ? `${nome} (cópia)` : "Meu modelo de DRE");
    if (!n) return;
    const m = await api("/api/modelos", { method: "POST", body: { nome: n, copiar_de: copiar || null } });
    location.hash = `#/modelo/${m.id}`;
  });
  $("#novo").onclick = () => novo(null);
  $$(".dup").forEach((b) => b.onclick = () => novo(Number(b.dataset.id), b.dataset.nome));
}

async function telaModelo(id) {
  const m = await api(`/api/modelos/${id}`);
  casca("modelos", `<div class="topo"><div><a href="#/modelos" class="peq">← Modelos</a><h1>Editar modelo</h1></div>
    <div class="linha">${EU.perfil === "ADMIN" ? '<button class="btn perigo" id="m-del">Excluir</button>' : ""}<button class="btn pri" id="m-salvar">Salvar modelo</button></div></div>
    <div class="card"><div class="form" style="grid-template-columns:1fr 2fr"><label>Nome <input id="m-nome" value="${esc(m.nome)}"></label><label>Descrição <input id="m-desc" value="${esc(m.descricao)}"></label></div></div>
    <div class="info peq"><b>Como funciona.</b> <b>Conta</b>: recebe as categorias do Bling e os grupos da Domínio que você associar. Sinal <b>automático</b> (recomendado): entradas de caixa somam e saídas subtraem, e um estorno abate a despesa. "Soma" e "Subtrai" forçam o sinal.
      <b>Total</b>: fórmula com os códigos das linhas de cima, ex.: <span class="mono">RB + DED_DEV + DED_IMP</span> (as despesas já vêm negativas, por isso normalmente é tudo "+").
      <b>Título</b>: só organiza a leitura. Marque <b>AV</b> na linha que vale 100% na análise vertical (normalmente a Receita líquida).
      ${m.empresas ? `<br>Este modelo é usado por ${m.empresas} empresa(s): excluir uma linha apaga as associações dela.` : ""}</div>
    <div class="card"><div class="rolagem" style="max-height:none"><table class="editor"><tr><th></th><th style="width:120px">Código</th><th>Nome</th><th style="width:110px">Tipo</th><th style="width:300px">Sinal / fórmula</th><th style="width:60px">Destaque</th><th style="width:40px">AV</th><th style="width:110px"></th></tr>
    <tbody id="linhas"></tbody></table></div>
    <div class="linha" style="margin-top:12px"><button class="btn" id="add-c">+ Conta</button><button class="btn" id="add-t">+ Total</button><button class="btn" id="add-ti">+ Título</button></div></div>`);
  const linhas = m.linhas.map((x) => ({ ...x }));
  const desenhar = () => {
    $("#linhas").innerHTML = linhas.map((x, i) => `<tr class="${x.tipo}" data-i="${i}">
      <td class="suave peq">${i + 1}</td>
      <td><input class="mono f-cod" value="${esc(x.codigo)}"></td>
      <td><input class="f-nome" value="${esc(x.nome)}"></td>
      <td><select class="f-tipo"><option value="CONTA">Conta</option><option value="TOTAL">Total</option><option value="TITULO">Título</option></select></td>
      <td>${x.tipo === "CONTA" ? `<select class="f-sinal"><option value="0">Automático</option><option value="1">Sempre soma (+)</option><option value="-1">Sempre subtrai (−)</option></select>`
        : x.tipo === "TOTAL" ? `<input class="mono f-form" value="${esc(x.formula)}" placeholder="RB + DED">` : ""}</td>
      <td style="text-align:center"><input type="checkbox" class="f-dest" ${x.destaque ? "checked" : ""}></td>
      <td style="text-align:center">${x.tipo !== "TITULO" ? `<input type="radio" name="av" class="f-av" ${x.base_av ? "checked" : ""}>` : ""}</td>
      <td style="white-space:nowrap"><button class="btn peq sobe" title="Subir">↑</button><button class="btn peq desce" title="Descer">↓</button><button class="btn peq perigo del" title="Excluir">✕</button></td></tr>`).join("");
    $$("#linhas tr").forEach((tr) => {
      const i = Number(tr.dataset.i), x = linhas[i];
      $(".f-tipo", tr).value = x.tipo;
      if ($(".f-sinal", tr)) $(".f-sinal", tr).value = String(x.sinal ?? 1);
      $(".f-cod", tr).oninput = (ev) => { x.codigo = ev.target.value.toUpperCase().replace(/[^A-Z0-9_]/g, ""); ev.target.value = x.codigo; };
      $(".f-nome", tr).oninput = (ev) => { x.nome = ev.target.value; };
      $(".f-tipo", tr).onchange = (ev) => { x.tipo = ev.target.value; if (x.tipo === "CONTA" && x.sinal == null) x.sinal = 0; desenhar(); };
      if ($(".f-sinal", tr)) $(".f-sinal", tr).onchange = (ev) => { x.sinal = Number(ev.target.value); };
      if ($(".f-form", tr)) $(".f-form", tr).oninput = (ev) => { x.formula = ev.target.value.toUpperCase(); };
      $(".f-dest", tr).onchange = (ev) => { x.destaque = ev.target.checked; };
      if ($(".f-av", tr)) $(".f-av", tr).onchange = () => { linhas.forEach((y) => { y.base_av = false; }); x.base_av = true; };
      $(".sobe", tr).onclick = () => { if (i > 0) { [linhas[i - 1], linhas[i]] = [linhas[i], linhas[i - 1]]; desenhar(); } };
      $(".desce", tr).onclick = () => { if (i < linhas.length - 1) { [linhas[i + 1], linhas[i]] = [linhas[i], linhas[i + 1]]; desenhar(); } };
      $(".del", tr).onclick = () => { if (confirm(`Excluir a linha ${x.nome}?`)) { linhas.splice(i, 1); desenhar(); } };
    });
  };
  const add = (tipo) => {
    let n = linhas.length + 1;
    while (linhas.some((x) => x.codigo === `L${n}`)) n += 1;
    linhas.push({ codigo: `L${n}`, nome: tipo === "TITULO" ? "NOVO GRUPO" : tipo === "TOTAL" ? "(=) Novo total" : "(-) Nova conta", tipo, sinal: 0, formula: "", destaque: tipo === "TOTAL", base_av: false });
    desenhar();
    const ult = $$("#linhas tr").pop(); ult.scrollIntoView({ block: "center" }); $(".f-nome", ult).select();
  };
  $("#add-c").onclick = () => add("CONTA");
  $("#add-t").onclick = () => add("TOTAL");
  $("#add-ti").onclick = () => add("TITULO");
  $("#m-salvar").onclick = (ev) => tentar(async () => {
    const r = await api(`/api/modelos/${id}`, { method: "PUT", body: { nome: $("#m-nome").value, descricao: $("#m-desc").value, linhas } });
    linhas.splice(0, linhas.length, ...r.linhas.map((x) => ({ ...x })));
    desenhar(); toast("Modelo salvo.");
  }, ev.target);
  if ($("#m-del")) $("#m-del").onclick = (ev) => tentar(async () => {
    if (!confirm("Excluir este modelo?")) return;
    await api(`/api/modelos/${id}`, { method: "DELETE" }); location.hash = "#/modelos";
  }, ev.target);
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
  $$(".modal-fundo").forEach((m) => m.remove());
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
