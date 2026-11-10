"""Integra Bling — DRE multiempresa com dados do Bling (contas a pagar e a receber) e da Domínio
(faturamento e devoluções, via Central de Inteligência 360). Alta Visão Contabilidade."""
import io
import re
import json
import secrets
import threading
import datetime as dt
from pathlib import Path

from fastapi import FastAPI, Request, Depends, HTTPException
from fastapi.responses import JSONResponse, FileResponse, RedirectResponse, StreamingResponse, Response
from fastapi.staticfiles import StaticFiles
from sqlalchemy import select, func

import db
from db import (SessionLocal, Usuario, Empresa, ModeloDRE, LinhaDRE, ContaOrigem, Movimento, DominioValor, DominioItem, Produto, ProdutoCusto, Job,
                MLVenda, MLItem, MLTarifa, MLCusto, MLNota, Auditoria, agora, PERFIS, TIPOS_LINHA)
import bling
import dominio
import dre
import fluxo
import mlivre
import mlanalise
import mlauditoria
from seguranca import (hash_senha, confere_senha, assinar, verificar, cifrar, decifrar, get_config, set_config)
import os

VERSAO = "1.13.1"
COOKIE = "ib_sess"
RAIZ = Path(__file__).parent
app = FastAPI(title="Integra Bling", version=VERSAO)
app.mount("/static", StaticFiles(directory=RAIZ / "static"), name="static")


# ------------------------------------------------------------------ infraestrutura
def get_db():
    s = SessionLocal()
    try:
        yield s
    finally:
        s.close()


@app.on_event("startup")
def iniciar():
    db.criar_tabelas()
    with SessionLocal() as s:
        if not s.scalar(select(func.count(ModeloDRE.id))):
            dre.criar_modelo_padrao(s)
        if get_config(s, "migracao_caixa") != "1":  # 1.1.0: DRE pelo caixa — contas passam a usar o sinal automático
            for ln in s.scalars(select(LinhaDRE).where(LinhaDRE.tipo == "CONTA")).all():
                ln.sinal = 0
            set_config(s, "migracao_caixa", "1")
        if get_config(s, "migracao_grupos") != "1":  # 1.4.0: modelo em grupos (soma automática)
            for m in s.scalars(select(ModeloDRE)).all():
                dre.converter_modelo(s, m)
            set_config(s, "migracao_grupos", "1")
        if get_config(s, "migracao_pis_cofins") != "1":  # 1.6.0: linha PIS/COFINS automática
            for m in s.scalars(select(ModeloDRE)).all():
                lns = s.scalars(select(LinhaDRE).where(LinhaDRE.modelo_id == m.id).order_by(LinhaDRE.ordem)).all()
                ded = next((x for x in lns if x.tipo == "GRUPO" and x.codigo == "DED"), None)
                if ded and not any(x.codigo == "PIS_COFINS" for x in lns):
                    dev = next((x for x in lns if x.codigo == "DED_DEV" and x.pai_id == ded.id), None)
                    pos = (dev or ded).ordem
                    for x in lns:
                        if x.ordem > pos:
                            x.ordem += 10
                    s.add(LinhaDRE(modelo_id=m.id, pai_id=ded.id, ordem=pos + 5, codigo="PIS_COFINS", nome="PIS/COFINS",
                                   tipo="CONTA", sinal=0))
            s.flush()
            for e in s.scalars(select(Empresa)).all():
                dominio.aplicar_sugestoes(s, e)
            set_config(s, "migracao_pis_cofins", "1")
        if get_config(s, "migracao_icms_ttd") != "1":  # 1.7.0: linha ICMS TTD (apuração do regime especial)
            for m in s.scalars(select(ModeloDRE)).all():
                lns = s.scalars(select(LinhaDRE).where(LinhaDRE.modelo_id == m.id).order_by(LinhaDRE.ordem)).all()
                ded = next((x for x in lns if x.tipo == "GRUPO" and x.codigo == "DED"), None)
                if ded and not any(x.codigo == "ICMS_TTD" for x in lns):
                    ant = next((x for x in lns if x.codigo == "PIS_COFINS" and x.pai_id == ded.id), None) or \
                        next((x for x in lns if x.codigo == "DED_DEV" and x.pai_id == ded.id), None) or ded
                    for x in lns:
                        if x.ordem > ant.ordem:
                            x.ordem += 10
                    s.add(LinhaDRE(modelo_id=m.id, pai_id=ded.id, ordem=ant.ordem + 5, codigo="ICMS_TTD", nome="ICMS TTD",
                                   tipo="CONTA", sinal=0))
            s.flush()
            for e in s.scalars(select(Empresa)).all():
                dominio.aplicar_sugestoes(s, e)
            set_config(s, "migracao_icms_ttd", "1")
        if get_config(s, "migracao_custo_vigencia") != "1":  # 1.3.0: custo por vigência mensal
            for p in s.scalars(select(Produto).where(Produto.custo.is_not(None))).all():
                s.add(ProdutoCusto(produto_id=p.id, mes_inicio="2000-01", custo=p.custo, criado_por=p.custo_por or "migração"))
            set_config(s, "migracao_custo_vigencia", "1")
        if get_config(s, "migracao_ml_separado") != "1":  # 1.10.0: Mercado Livre vira módulo separado da DRE principal
            for c in s.scalars(select(ContaOrigem).where(ContaOrigem.origem == "MLIVRE")).all():
                s.delete(c)
            m = s.scalar(select(ModeloDRE).where(ModeloDRE.nome == "DRE Mercado Livre"))
            if m and not s.scalar(select(func.count(Empresa.id)).where(Empresa.modelo_id == m.id)):
                s.delete(m)
            set_config(s, "migracao_ml_separado", "1")
        for j in s.scalars(select(Job).where(Job.status.in_(("PENDENTE", "EXECUTANDO")))).all():
            j.status, j.fim, j.mensagem = "ERRO", agora(), "Interrompido: o sistema foi reiniciado. Peça de novo."
        s.commit()


@app.exception_handler(HTTPException)
async def erro_http(_req, exc: HTTPException):
    return JSONResponse({"erro": exc.detail}, status_code=exc.status_code)


@app.middleware("http")
async def cabecalhos(request: Request, call_next):
    resp = await call_next(request)
    resp.headers["X-Content-Type-Options"] = "nosniff"
    resp.headers["X-Frame-Options"] = "DENY"
    resp.headers["Referrer-Policy"] = "same-origin"
    return resp


def audit(s, quem, acao, detalhe=""):
    s.add(Auditoria(usuario=quem, acao=acao, detalhe=str(detalhe)[:2000]))


def _404(msg="Não encontrado."):
    raise HTTPException(404, msg)


def usuario(request: Request, s=Depends(get_db)):
    d = verificar(request.cookies.get(COOKIE, ""))
    u = s.get(Usuario, d.get("uid")) if d else None
    if not u or not u.ativo:
        raise HTTPException(401, "Faça login.")
    return u


def admin(u=Depends(usuario)):
    if u.perfil != "ADMIN":
        raise HTTPException(403, "Somente administradores.")
    return u


def dt2s(v):
    return v.isoformat(timespec="seconds") if v else None


def so_digitos(v):
    return re.sub(r"\D", "", str(v or ""))


def _mes_ok(v, campo):
    if not re.fullmatch(r"\d{4}-(0[1-9]|1[0-2])", str(v or "")):
        raise HTTPException(400, f"{campo}: use o formato AAAA-MM.")
    return v


def _data_ok(v, campo):
    try:
        return dt.date.fromisoformat(str(v)).isoformat()
    except ValueError:
        raise HTTPException(400, f"{campo}: data inválida.") from None


def base_url(request: Request):
    url = os.environ.get("PUBLIC_URL", "").rstrip("/")
    if url:
        return url
    proto = request.headers.get("x-forwarded-proto", request.url.scheme)
    host = request.headers.get("x-forwarded-host", request.headers.get("host", request.url.netloc))
    return f"{proto}://{host}"


@app.get("/health")
def health():
    return {"ok": True, "versao": VERSAO}


@app.get("/")
def index():
    return FileResponse(RAIZ / "static" / "index.html", headers={"Cache-Control": "no-cache"})


# ------------------------------------------------------------------ login e usuários
def _u_json(u: Usuario):
    return {"id": u.id, "nome": u.nome, "email": u.email, "perfil": u.perfil, "ativo": u.ativo}


def _sessao(resp, u, request):
    resp.set_cookie(COOKIE, assinar({"uid": u.id}, 12 * 3600), httponly=True, samesite="lax", max_age=12 * 3600,
                    secure=base_url(request).startswith("https"))


def _senha_ok(senha):
    if len(senha or "") < 8:
        raise HTTPException(400, "A senha precisa ter pelo menos 8 caracteres.")
    return senha


@app.get("/api/estado")
def estado(request: Request, s=Depends(get_db)):
    d = verificar(request.cookies.get(COOKIE, ""))
    u = s.get(Usuario, d.get("uid")) if d else None
    return {"versao": VERSAO, "precisa_setup": not s.scalar(select(func.count(Usuario.id))),
            "usuario": _u_json(u) if u and u.ativo else None}


@app.post("/api/setup")
async def setup(request: Request, s=Depends(get_db)):
    if s.scalar(select(func.count(Usuario.id))):
        raise HTTPException(400, "O administrador já foi criado.")
    b = await request.json()
    email = str(b.get("email") or "").strip().lower()
    if "@" not in email:
        raise HTTPException(400, "E-mail inválido.")
    u = Usuario(nome=str(b.get("nome") or "Administrador").strip()[:120], email=email,
                senha_hash=hash_senha(_senha_ok(b.get("senha"))), perfil="ADMIN")
    s.add(u)
    audit(s, email, "SETUP")
    s.commit()
    resp = JSONResponse(_u_json(u))
    _sessao(resp, u, request)
    return resp


@app.post("/api/login")
async def login(request: Request, s=Depends(get_db)):
    b = await request.json()
    u = s.scalar(select(Usuario).where(Usuario.email == str(b.get("email") or "").strip().lower()))
    if not u or not u.ativo or not confere_senha(str(b.get("senha") or ""), u.senha_hash):
        raise HTTPException(401, "E-mail ou senha incorretos.")
    audit(s, u.email, "LOGIN")
    s.commit()
    resp = JSONResponse(_u_json(u))
    _sessao(resp, u, request)
    return resp


@app.post("/api/logout")
def logout():
    resp = JSONResponse({"ok": True})
    resp.delete_cookie(COOKIE)
    return resp


@app.post("/api/senha")
async def trocar_senha(request: Request, u=Depends(usuario), s=Depends(get_db)):
    b = await request.json()
    if not confere_senha(str(b.get("atual") or ""), u.senha_hash):
        raise HTTPException(400, "A senha atual não confere.")
    u.senha_hash = hash_senha(_senha_ok(b.get("nova")))
    audit(s, u.email, "SENHA")
    s.commit()
    return {"ok": True}


@app.get("/api/usuarios")
def usuarios(_u=Depends(admin), s=Depends(get_db)):
    return [_u_json(x) for x in s.scalars(select(Usuario).order_by(Usuario.nome)).all()]


@app.post("/api/usuarios")
async def usuario_novo(request: Request, u=Depends(admin), s=Depends(get_db)):
    b = await request.json()
    email = str(b.get("email") or "").strip().lower()
    if "@" not in email:
        raise HTTPException(400, "E-mail inválido.")
    if s.scalar(select(Usuario).where(Usuario.email == email)):
        raise HTTPException(400, "Já existe um usuário com esse e-mail.")
    perfil = str(b.get("perfil") or "OPERADOR").upper()
    if perfil not in PERFIS:
        raise HTTPException(400, "Perfil inválido.")
    x = Usuario(nome=str(b.get("nome") or email).strip()[:120], email=email, perfil=perfil,
                senha_hash=hash_senha(_senha_ok(b.get("senha"))))
    s.add(x)
    audit(s, u.email, "USUARIO_NOVO", email)
    s.commit()
    return _u_json(x)


@app.put("/api/usuarios/{uid}")
async def usuario_editar(uid: int, request: Request, u=Depends(admin), s=Depends(get_db)):
    x = s.get(Usuario, uid) or _404()
    b = await request.json()
    if "nome" in b:
        x.nome = str(b["nome"]).strip()[:120] or x.nome
    if "perfil" in b:
        if b["perfil"] not in PERFIS:
            raise HTTPException(400, "Perfil inválido.")
        if x.id == u.id and b["perfil"] != "ADMIN":
            raise HTTPException(400, "Você não pode tirar o seu próprio perfil de administrador.")
        x.perfil = b["perfil"]
    if "ativo" in b:
        if x.id == u.id and not b["ativo"]:
            raise HTTPException(400, "Você não pode desativar a si mesmo.")
        x.ativo = bool(b["ativo"])
    if b.get("senha"):
        x.senha_hash = hash_senha(_senha_ok(b["senha"]))
    audit(s, u.email, "USUARIO_EDITA", x.email)
    s.commit()
    return _u_json(x)


# ------------------------------------------------------------------ configurações
@app.get("/api/config")
def config_ver(request: Request, _u=Depends(admin), s=Depends(get_db)):
    return {"bling_client_id": get_config(s, "bling_client_id"),
            "bling_secret_definido": bool(get_config(s, "bling_client_secret")),
            "redirect_uri": base_url(request) + "/bling/callback",
            "ml_client_id": get_config(s, "ml_client_id"),
            "ml_secret_definido": bool(get_config(s, "ml_client_secret")),
            "ml_redirect_uri": base_url(request) + "/mlivre/callback",
            "central_url": get_config(s, "central_url"),
            "central_chave_definida": bool(get_config(s, "central_chave")),
            "secret_key_no_ambiente": bool(os.environ.get("SECRET_KEY"))}


@app.put("/api/config")
async def config_salvar(request: Request, u=Depends(admin), s=Depends(get_db)):
    b = await request.json()
    if "bling_client_id" in b:
        set_config(s, "bling_client_id", str(b["bling_client_id"] or "").strip()[:200])
    if b.get("bling_client_secret"):
        set_config(s, "bling_client_secret", cifrar(str(b["bling_client_secret"]).strip()))
    if "ml_client_id" in b:
        set_config(s, "ml_client_id", str(b["ml_client_id"] or "").strip()[:200])
    if b.get("ml_client_secret"):
        set_config(s, "ml_client_secret", cifrar(str(b["ml_client_secret"]).strip()))
    if "central_url" in b:
        url = str(b["central_url"] or "").strip().rstrip("/")
        if url and not url.startswith("https://") and not url.startswith("http://localhost"):
            raise HTTPException(400, "O endereço da Central precisa começar com https://")
        set_config(s, "central_url", url)
    if b.get("central_chave"):
        set_config(s, "central_chave", cifrar(str(b["central_chave"]).strip()))
    audit(s, u.email, "CONFIG", ",".join(k for k in b if b[k]))
    s.commit()
    return config_ver(request, u, s)


@app.post("/api/config/testar-central")
def config_testar_central(_u=Depends(admin), s=Depends(get_db)):
    try:
        itens = dominio.clientes_central(s)
    except Exception as e:  # noqa: BLE001
        raise HTTPException(400, str(e)) from None
    return {"ok": True, "clientes": len(itens)}


@app.get("/api/central/clientes")
def central_clientes(q: str = "", _u=Depends(usuario), s=Depends(get_db)):
    try:
        itens = dominio.clientes_central(s)
    except Exception as e:  # noqa: BLE001
        raise HTTPException(400, str(e)) from None
    q = q.strip().lower()
    if q:
        dq = so_digitos(q)
        itens = [c for c in itens if q in (c["razao_social"] or "").lower() or q in (c["nome_fantasia"] or "").lower()
                 or (dq and dq in (c["cnpj"] or "")) or q == str(c["codigo_dominio"])]
    return itens[:50]


# ------------------------------------------------------------------ empresas
def _emp_json(s, e: Empresa):
    contas = s.execute(select(ContaOrigem.origem, func.count(ContaOrigem.id), func.count(ContaOrigem.linha_id))
                       .where(ContaOrigem.empresa_id == e.id, ContaOrigem.ativa == True)  # noqa: E712
                       .group_by(ContaOrigem.origem)).all()
    job = s.scalar(select(Job).where(Job.empresa_id == e.id).order_by(Job.id.desc()).limit(1))
    m = s.get(ModeloDRE, e.modelo_id) if e.modelo_id else None
    return {"id": e.id, "nome": e.nome, "cnpj": e.cnpj, "codigo_dominio": e.codigo_dominio, "ativo": e.ativo,
            "modelo_id": e.modelo_id, "modelo_nome": m.nome if m else None,
            "pis_cofins_aliq": float(e.pis_cofins_aliq if e.pis_cofins_aliq is not None else 9.25),
            "pis_cofins_fonte": e.pis_cofins_fonte or "APURACAO",
            "bling_conectado": bool(e.bling_refresh), "bling_conectado_em": dt2s(e.bling_conectado_em),
            "bling_app_proprio": bool(e.bling_client_id), "bling_client_id": e.bling_client_id,
            "bling_sync_em": dt2s(e.bling_sync_em), "bling_sync_periodo": e.bling_sync_periodo,
            "dominio_sync_em": dt2s(e.dominio_sync_em), "dominio_sync_periodo": e.dominio_sync_periodo,
            "ml_conectado": bool(e.ml_refresh), "ml_conectado_em": dt2s(e.ml_conectado_em), "ml_apelido": e.ml_apelido,
            "ml_sync_em": dt2s(e.ml_sync_em), "ml_sync_periodo": e.ml_sync_periodo,
            "ml_imposto_aliq": float(e.ml_imposto_aliq or 0), "ml_regras": mlauditoria.regras(e),
            "contas": {o: {"total": t, "associadas": a} for o, t, a in contas},
            "ultimo_job": _job_json(job) if job else None}


def _aplicar_sugestoes(s, e: Empresa):
    dominio.aplicar_sugestoes(s, e)


def _ler_empresa(b, e: Empresa, s):
    if "nome" in b:
        e.nome = str(b["nome"] or "").strip()[:200]
    if "cnpj" in b:
        c = so_digitos(b["cnpj"])
        if c and len(c) not in (11, 14):
            raise HTTPException(400, "CNPJ inválido.")
        e.cnpj = c
    if "codigo_dominio" in b:
        e.codigo_dominio = str(b["codigo_dominio"] or "").strip()[:20]
    if "ativo" in b:
        e.ativo = bool(b["ativo"])
    if "pis_cofins_fonte" in b:
        if b["pis_cofins_fonte"] not in ("APURACAO", "ALIQUOTA"):
            raise HTTPException(400, "Origem do PIS/COFINS inválida.")
        e.pis_cofins_fonte = b["pis_cofins_fonte"]
    if "pis_cofins_aliq" in b:
        a = _num_br(b["pis_cofins_aliq"])
        if a is None or a < 0 or a > 100:
            raise HTTPException(400, "Alíquota de PIS/COFINS inválida.")
        e.pis_cofins_aliq = a
    if "bling_client_id" in b:
        e.bling_client_id = str(b["bling_client_id"] or "").strip()[:200]
        if not e.bling_client_id:
            e.bling_client_secret = ""
    if b.get("bling_client_secret"):
        e.bling_client_secret = cifrar(str(b["bling_client_secret"]).strip())
    if "modelo_id" in b:
        novo = int(b["modelo_id"]) if b["modelo_id"] else None
        if novo and not s.get(ModeloDRE, novo):
            raise HTTPException(400, "Modelo de DRE não encontrado.")
        if novo != e.modelo_id and e.id:
            # associações do modelo antigo não valem no novo
            for c in s.scalars(select(ContaOrigem).where(ContaOrigem.empresa_id == e.id, ContaOrigem.linha_id.is_not(None))):
                c.linha_id = None
        e.modelo_id = novo
    if not e.nome:
        raise HTTPException(400, "Informe o nome da empresa.")


@app.get("/api/empresas")
def empresas(_u=Depends(usuario), s=Depends(get_db)):
    return [_emp_json(s, e) for e in s.scalars(select(Empresa).order_by(Empresa.ativo.desc(), Empresa.nome)).all()]


@app.post("/api/empresas")
async def empresa_nova(request: Request, u=Depends(usuario), s=Depends(get_db)):
    b = await request.json()
    e = Empresa(nome="")
    if not b.get("modelo_id"):
        m = s.scalar(select(ModeloDRE).order_by(ModeloDRE.id).limit(1))
        b["modelo_id"] = m.id if m else None
    _ler_empresa(b, e, s)
    if e.cnpj and s.scalar(select(Empresa).where(Empresa.cnpj == e.cnpj)):
        raise HTTPException(400, "Já existe uma empresa com esse CNPJ.")
    s.add(e)
    s.flush()
    _aplicar_sugestoes(s, e)
    audit(s, u.email, "EMPRESA_NOVA", f"{e.cnpj} {e.nome}")
    s.commit()
    return _emp_json(s, e)


@app.get("/api/empresas/{eid}")
def empresa_ver(eid: int, _u=Depends(usuario), s=Depends(get_db)):
    return _emp_json(s, s.get(Empresa, eid) or _404())


@app.put("/api/empresas/{eid}")
async def empresa_editar(eid: int, request: Request, u=Depends(usuario), s=Depends(get_db)):
    e = s.get(Empresa, eid) or _404()
    b = await request.json()
    _ler_empresa(b, e, s)
    _aplicar_sugestoes(s, e)
    audit(s, u.email, "EMPRESA_EDITA", f"{e.id} {e.nome}")
    s.commit()
    return _emp_json(s, e)


@app.delete("/api/empresas/{eid}")
def empresa_excluir(eid: int, u=Depends(admin), s=Depends(get_db)):
    e = s.get(Empresa, eid) or _404()
    audit(s, u.email, "EMPRESA_EXCLUI", f"{e.cnpj} {e.nome}")
    s.delete(e)
    s.commit()
    return {"ok": True}


# ------------------------------------------------------------------ Bling: conexão e sincronização
@app.post("/api/empresas/{eid}/bling/conectar")
def bling_conectar(eid: int, u=Depends(usuario), s=Depends(get_db)):
    e = s.get(Empresa, eid) or _404()
    cid, sec = bling.credenciais(s, e)
    if not cid or not sec:
        raise HTTPException(400, "Cadastre o aplicativo do Bling (Client ID e Client Secret) em Configurações "
                                 "ou no cadastro da empresa.")
    state = assinar({"eid": e.id, "uid": u.id, "n": secrets.token_hex(4)}, 1800)
    return {"url": bling.url_autorizacao(cid, state)}


@app.get("/bling/callback")
def bling_callback(code: str = "", state: str = "", error: str = "", error_description: str = "", s=Depends(get_db)):
    d = verificar(state)
    if not d:
        return RedirectResponse("/#/empresas?bling=erro&msg=" + "Autorização expirada. Tente de novo.")
    e = s.get(Empresa, d["eid"])
    if not e:
        return RedirectResponse("/#/empresas?bling=erro&msg=Empresa não encontrada")
    if error or not code:
        return RedirectResponse(f"/#/empresa/{e.id}?bling=erro&msg=" + (error_description or error or "Autorização negada"))
    try:
        bling.trocar_codigo(s, e, code)
        u = s.get(Usuario, d.get("uid"))
        audit(s, u.email if u else "", "BLING_CONECTA", f"{e.id} {e.nome}")
        s.commit()
    except Exception as ex:  # noqa: BLE001
        return RedirectResponse(f"/#/empresa/{e.id}?bling=erro&msg=" + str(ex)[:200])
    return RedirectResponse(f"/#/empresa/{e.id}?bling=ok")


@app.post("/api/empresas/{eid}/bling/desconectar")
def bling_desconectar(eid: int, u=Depends(usuario), s=Depends(get_db)):
    e = s.get(Empresa, eid) or _404()
    e.bling_access = e.bling_refresh = ""
    e.bling_expira = e.bling_conectado_em = None
    audit(s, u.email, "BLING_DESCONECTA", f"{e.id} {e.nome}")
    s.commit()
    return _emp_json(s, e)


def _job_json(j: Job):
    return {"id": j.id, "empresa_id": j.empresa_id, "tipo": j.tipo, "status": j.status, "progresso": j.progresso,
            "total": j.total, "mensagem": j.mensagem, "criado_por": j.criado_por, "criado_em": dt2s(j.criado_em),
            "fim": dt2s(j.fim), "parametros": json.loads(j.parametros or "{}")}


def _iniciar_job(s, e, tipo, params, u, alvo):
    ativo = s.scalar(select(Job).where(Job.empresa_id == e.id, Job.tipo == tipo, Job.status.in_(("PENDENTE", "EXECUTANDO"))))
    if ativo:
        return _job_json(ativo)
    j = Job(empresa_id=e.id, tipo=tipo, parametros=json.dumps(params), criado_por=u.email)
    s.add(j)
    audit(s, u.email, tipo.upper(), f"{e.id} {e.nome} {params}")
    s.commit()
    if os.environ.get("INTEGRA_SEM_THREADS") != "1":
        threading.Thread(target=alvo, args=(j.id,), daemon=True).start()
    return _job_json(j)


@app.post("/api/empresas/{eid}/bling/sincronizar")
async def bling_sincronizar(eid: int, request: Request, u=Depends(usuario), s=Depends(get_db)):
    e = s.get(Empresa, eid) or _404()
    if not e.bling_refresh:
        raise HTTPException(400, "Conecte a empresa ao Bling primeiro.")
    b = await request.json()
    ini, fim = _data_ok(b.get("inicio"), "Início"), _data_ok(b.get("fim"), "Fim")
    if ini > fim:
        raise HTTPException(400, "O início é depois do fim.")
    if (dt.date.fromisoformat(fim) - dt.date.fromisoformat(ini)).days > 800:
        raise HTTPException(400, "Período máximo de 2 anos por vez.")
    return _iniciar_job(s, e, "bling_sync", {"inicio": ini, "fim": fim}, u, bling.sincronizar)


# ------------------------------------------------------------------ Mercado Livre
@app.post("/api/empresas/{eid}/mlivre/conectar")
def ml_conectar(eid: int, request: Request, u=Depends(usuario), s=Depends(get_db)):
    e = s.get(Empresa, eid) or _404()
    cid, sec = mlivre.credenciais(s)
    if not cid or not sec:
        raise HTTPException(400, "Cadastre o aplicativo do Mercado Livre (Client ID e Secret Key) em Configurações.")
    state = assinar({"eid": e.id, "uid": u.id, "n": secrets.token_hex(4)}, 1800)
    return {"url": mlivre.url_autorizacao(cid, base_url(request) + "/mlivre/callback", state)}


@app.get("/mlivre/callback")
def ml_callback(request: Request, code: str = "", state: str = "", error: str = "", error_description: str = "",
                s=Depends(get_db)):
    d = verificar(state)
    if not d:
        return RedirectResponse("/#/ml?ml=erro&msg=Autorização expirada. Tente de novo.")
    e = s.get(Empresa, d["eid"])
    if not e:
        return RedirectResponse("/#/ml?ml=erro&msg=Empresa não encontrada")
    if error or not code:
        return RedirectResponse(f"/#/ml/{e.id}/conexao?ml=erro&msg=" + (error_description or error or "Autorização negada"))
    try:
        mlivre.trocar_codigo(s, e, code, base_url(request) + "/mlivre/callback")
        u = s.get(Usuario, d.get("uid"))
        audit(s, u.email if u else "", "ML_CONECTA", f"{e.id} {e.nome} {e.ml_apelido}")
        s.commit()
    except Exception as ex:  # noqa: BLE001
        return RedirectResponse(f"/#/ml/{e.id}/conexao?ml=erro&msg=" + str(ex)[:200])
    return RedirectResponse(f"/#/ml/{e.id}/conexao?ml=ok")


@app.post("/mlivre/notificacoes")
async def ml_notificacoes():
    """O Mercado Livre exige uma URL de notificações que responda 200 rápido. Por ora não usamos os avisos:
    os dados são lidos quando o usuário clica em Atualizar do Mercado Livre."""
    return {"ok": True}


@app.post("/api/empresas/{eid}/mlivre/desconectar")
def ml_desconectar(eid: int, u=Depends(usuario), s=Depends(get_db)):
    e = s.get(Empresa, eid) or _404()
    e.ml_access = e.ml_refresh = ""
    e.ml_expira = e.ml_conectado_em = None
    audit(s, u.email, "ML_DESCONECTA", f"{e.id} {e.nome}")
    s.commit()
    return _emp_json(s, e)


@app.post("/api/empresas/{eid}/mlivre/sincronizar")
async def ml_sincronizar(eid: int, request: Request, u=Depends(usuario), s=Depends(get_db)):
    e = s.get(Empresa, eid) or _404()
    if not e.ml_refresh:
        raise HTTPException(400, "Conecte a empresa ao Mercado Livre primeiro.")
    b = await request.json()
    ini, fim = _mes_ok(b.get("inicio"), "Início"), _mes_ok(b.get("fim"), "Fim")
    if ini > fim:
        raise HTTPException(400, "O início é depois do fim.")
    if len(dre.meses_entre(ini, fim)) > 24:
        raise HTTPException(400, "Período máximo de 24 meses por vez.")
    return _iniciar_job(s, e, "ml_sync", {"inicio": ini, "fim": fim, "frete": b.get("frete", True) is not False,
                                          "notas": b.get("notas", True) is not False,
                                          "cobrancas": b.get("cobrancas", True) is not False}, u, mlivre.sincronizar)


def _ml_emp(s, eid):
    return s.get(Empresa, eid) or _404()


def _periodo_ml(inicio, fim):
    _mes_ok(inicio, "Início")
    _mes_ok(fim, "Fim")
    if inicio > fim:
        raise HTTPException(400, "O início é depois do fim.")
    if len(dre.meses_entre(inicio, fim)) > 36:
        raise HTTPException(400, "Período máximo de 36 meses.")


@app.get("/api/ml/empresas")
def ml_empresas(_u=Depends(usuario), s=Depends(get_db)):
    out = []
    for e in s.scalars(select(Empresa).where(Empresa.ativo == True).order_by(Empresa.nome)).all():  # noqa: E712
        out.append({"id": e.id, "nome": e.nome, "cnpj": e.cnpj, "ml_conectado": bool(e.ml_refresh), "ml_apelido": e.ml_apelido,
                    "ml_sync_em": dt2s(e.ml_sync_em), "ml_sync_periodo": e.ml_sync_periodo,
                    "ml_imposto_aliq": float(e.ml_imposto_aliq or 0),
                    "pedidos": s.scalar(select(func.count(MLVenda.id)).where(MLVenda.empresa_id == e.id)) or 0})
    return out


@app.put("/api/empresas/{eid}/mlivre/config")
async def ml_config(eid: int, request: Request, u=Depends(usuario), s=Depends(get_db)):
    e = _ml_emp(s, eid)
    b = await request.json()
    if isinstance(b.get("regras"), dict):
        r = {}
        for k in mlauditoria.REGRAS_PADRAO:
            v = b["regras"].get(k)
            if v in (None, ""):
                continue
            n = _num_br(v)
            if n is None or n < 0:
                raise HTTPException(400, f"Valor inválido em {k}.")
            r[k] = n
        e.ml_regras = json.dumps(r)
    if "imposto" in b:
        v = _num_br(b["imposto"])
        if v is None or v < 0 or v > 60:
            raise HTTPException(400, "Alíquota de impostos inválida (0 a 60%).")
        e.ml_imposto_aliq = v
    audit(s, u.email, "ML_CONFIG", f"{e.id} imposto={e.ml_imposto_aliq}")
    s.commit()
    return _emp_json(s, e)


@app.get("/api/empresas/{eid}/mlivre/dre")
def ml_dre(eid: int, inicio: str, fim: str, _u=Depends(usuario), s=Depends(get_db)):
    e = _ml_emp(s, eid)
    _periodo_ml(inicio, fim)
    return {"empresa": _emp_json(s, e), **mlanalise.dre(s, e, inicio, fim)}


@app.get("/api/empresas/{eid}/mlivre/dre.xlsx")
def ml_dre_xlsx(eid: int, inicio: str, fim: str, _u=Depends(usuario), s=Depends(get_db)):
    from openpyxl import Workbook
    from openpyxl.styles import Font, PatternFill
    e = _ml_emp(s, eid)
    _periodo_ml(inicio, fim)
    d = mlanalise.dre(s, e, inicio, fim)
    wb = Workbook()
    ws = wb.active
    ws.title = "DRE Mercado Livre"
    ws.append([f"DRE Mercado Livre — {e.nome}"])
    ws["A1"].font = Font(bold=True, size=13)
    ws.append([f"{inicio[5:]}/{inicio[:4]} a {fim[5:]}/{fim[:4]}"])
    ws.append([])
    ws.append(["Linha"] + [f"{m[5:]}/{m[:4]}" for m in d["meses"]] + ["Total", "% vendas"])
    for c in ws[4]:
        c.font = Font(bold=True, color="FFFFFF")
        c.fill = PatternFill("solid", fgColor="0F3D5E")
    for ln in d["linhas"]:
        ws.append([("    " if ln["grupo"] else "") + ln["nome"]] + ln["valores"] + [ln["total"], (ln["av_total"] or 0) / 100])
        r = ws.max_row
        for c in ws[r][1:]:
            c.number_format = "#,##0.00;[Red]-#,##0.00"
        ws.cell(r, ws.max_column).number_format = "0.0%"
        if ln["tipo"] != "CONTA":
            for c in ws[r]:
                c.font = Font(bold=True)
    ws.column_dimensions["A"].width = 52
    for a in d["avisos"]:
        ws.append([])
        ws.append([a])
    buf = io.BytesIO()
    wb.save(buf)
    buf.seek(0)
    nome = re.sub(r"[^A-Za-z0-9_-]+", "_", e.nome)[:40]
    return StreamingResponse(buf, media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                             headers={"Content-Disposition": f'attachment; filename="DRE_ML_{nome}_{inicio}_{fim}.xlsx"'})


@app.get("/api/empresas/{eid}/mlivre/produtos")
def ml_produtos(eid: int, inicio: str, fim: str, _u=Depends(usuario), s=Depends(get_db)):
    e = _ml_emp(s, eid)
    _periodo_ml(inicio, fim)
    return {"empresa": _emp_json(s, e), "itens": mlanalise.produtos(s, e, inicio, fim)}


@app.get("/api/empresas/{eid}/mlivre/vendas")
def ml_vendas(eid: int, inicio: str, fim: str, q: str = "", _u=Depends(usuario), s=Depends(get_db)):
    """Pedidos pagos do período com o resultado de cada um (impostos, tarifa, frete, custo e margem)."""
    e = _ml_emp(s, eid)
    _periodo_ml(inicio, fim)
    d1, d2 = dre.periodo_datas(inicio, fim)
    nao_pagos = s.scalar(select(func.count(MLVenda.id)).where(MLVenda.empresa_id == e.id, MLVenda.data >= d1,
                                                              MLVenda.data <= d2, MLVenda.pago <= 0)) or 0
    out = []
    t = q.strip().lower()
    for v, its, r in mlanalise.pedidos(s, e, inicio, fim):
        if t and not (t in str(v.order_id) or t in (v.descricao or "").lower() or t in (v.comprador or "").lower()
                      or any(t in (i.sku or "").lower() for i in its)):
            continue
        out.append({"order_id": v.order_id, "pack_id": v.pack_id, "data": v.data.isoformat(), "status": v.status,
                    "descricao": v.descricao, "comprador": v.comprador, "itens": v.itens,
                    "sku": ", ".join(sorted({i.sku for i in its if i.sku})),
                    **{k: round(r[k], 2) for k in ("vendas", "devolvido", "imposto", "tarifa", "frete", "cmv", "margem")},
                    "frete_lido": r["frete_lido"], "sem_custo": bool(r["sem_custo"]), "estimado": r["estimado"]})
    return {"total_pedidos": len(out), "nao_pagos": nao_pagos, "vendas": out[-3000:]}


# ---- rastreabilidade e auditoria
def _tabela_xlsx(nome_arq, titulo, cab, linhas, formatos=None):
    from openpyxl import Workbook
    from openpyxl.styles import Font, PatternFill
    wb = Workbook()
    ws = wb.active
    ws.title = titulo[:31]
    ws.append(cab)
    for c in ws[1]:
        c.font = Font(bold=True, color="FFFFFF")
        c.fill = PatternFill("solid", fgColor="0F3D5E")
    for ln in linhas:
        ws.append(ln)
    for i, f in (formatos or {}).items():
        for c in ws.iter_cols(min_col=i + 1, max_col=i + 1, min_row=2):
            for x in c:
                x.number_format = f
    for i, _ in enumerate(cab):
        ws.column_dimensions[ws.cell(1, i + 1).column_letter].width = 16
    ws.freeze_panes = "A2"
    buf = io.BytesIO()
    wb.save(buf)
    buf.seek(0)
    return StreamingResponse(buf, media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                             headers={"Content-Disposition": f'attachment; filename="{nome_arq}"'})


MOEDA = "#,##0.00;[Red]-#,##0.00"


@app.get("/api/empresas/{eid}/mlivre/painel")
def ml_painel(eid: int, inicio: str, fim: str, _u=Depends(usuario), s=Depends(get_db)):
    e = _ml_emp(s, eid)
    _periodo_ml(inicio, fim)
    ps = mlauditoria.painel(s, e, inicio, fim)
    return {"pedidos": ps[-5000:], "total": len(ps), "grupos": mlauditoria.GRUPOS_COBRANCA}


@app.get("/api/empresas/{eid}/mlivre/painel.xlsx")
def ml_painel_xlsx(eid: int, inicio: str, fim: str, _u=Depends(usuario), s=Depends(get_db)):
    e = _ml_emp(s, eid)
    _periodo_ml(inicio, fim)
    ps = mlauditoria.painel(s, e, inicio, fim)
    cs = mlauditoria.COMPONENTES
    cab = (["Data", "Pedido", "Link no Mercado Livre", "Pacote", "Situação", "Produto", "SKU", "Anúncio", "Comprador", "NF número", "NF série", "NF chave",
            "NF origem", "Domínio"] + [n for _k, n in cs]
           + ["Resultado", "Fonte", "Retido em disputa", "Custo da devolução", "Extrato entradas", "Extrato saídas", "Extrato líquido"])
    linhas = [[p["data"], str(p["order_id"]), mlivre.link_venda(p["order_id"], p["pack_id"]), str(p["pack_id"] or ""), p["status"], p["descricao"], p["sku"], p["tipo_anuncio"],
               p["comprador"], (p["nota"] or {}).get("numero", ""), (p["nota"] or {}).get("serie", ""),
               (p["nota"] or {}).get("chave", ""), (p["nota"] or {}).get("origem", ""), p["dominio"]]
              + [p["componentes"][k] for k, _n in cs]
              + [p["resultado"], p["fonte"], p["retido"], p["custo_devolucao"], p["extrato_credito"], -p["extrato_debito"],
                 p["extrato_liquido"]] for p in ps]
    fm = {i: MOEDA for i in range(14, len(cab)) if cab[i] != "Fonte"}
    return _tabela_xlsx(f"Vendas_ML_{inicio}_{fim}.xlsx", "Vendas por pedido", cab, linhas, fm)


@app.get("/api/empresas/{eid}/mlivre/pedido/{oid}")
def ml_pedido(eid: int, oid: int, _u=Depends(usuario), s=Depends(get_db)):
    e = _ml_emp(s, eid)
    d = mlauditoria.detalhe(s, e, oid)
    if not d:
        _404()
    return d


def _datas_extrato(de, ate):
    try:
        d1 = dt.date.fromisoformat(de or "2026-09-01")
        d2 = dt.date.fromisoformat(ate) if ate else agora().date()
    except ValueError:
        raise HTTPException(400, "Datas inválidas (use AAAA-MM-DD).") from None
    if d1 > d2:
        raise HTTPException(400, "A data inicial é depois da final.")
    if (d2 - d1).days > 400:
        raise HTTPException(400, "Período máximo de 400 dias.")
    return d1, d2


@app.get("/api/empresas/{eid}/mlivre/extrato")
def ml_extrato(eid: int, de: str = "", ate: str = "", _u=Depends(usuario), s=Depends(get_db)):
    e = _ml_emp(s, eid)
    d1, d2 = _datas_extrato(de, ate)
    r = mlauditoria.extrato(s, e, d1, d2)
    r["inicio_lido"], r["fim_lido"] = dt2s(r["inicio_lido"]), dt2s(r["fim_lido"])
    return r


@app.get("/api/empresas/{eid}/mlivre/extrato.xlsx")
def ml_extrato_xlsx(eid: int, de: str = "", ate: str = "", _u=Depends(usuario), s=Depends(get_db)):
    e = _ml_emp(s, eid)
    d1, d2 = _datas_extrato(de, ate)
    r = mlauditoria.extrato(s, e, d1, d2)
    cab = ["Data", "Movimento", "Tipo (MP)", "Operação MP", "Referência", "Pedido", "Identificado por", "Produto", "Crédito",
           "Débito", "Saldo", "Bruto", "Tarifa MP", "Parcelamento", "Frete", "Cupom", "Meio de pagamento"]
    linhas = [[x["data"], x["descricao"], x["tipo"], x["origem_id"], x["referencia"], str(x["order_id"] or ""), x["como"],
               (x["venda"] or {}).get("descricao", ""), x["credito"], -x["debito"], x["saldo"], x["bruto"], x["tarifa_mp"],
               x["financiamento"], x["frete"], x["cupom"], x["meio"]] for x in r["linhas"]]
    return _tabela_xlsx(f"Extrato_MP_{d1}_{d2}.xlsx", "Extrato Mercado Pago", cab, linhas, {i: MOEDA for i in range(8, 16)})


@app.post("/api/empresas/{eid}/mlivre/extrato/atualizar")
async def ml_extrato_atualizar(eid: int, request: Request, u=Depends(usuario), s=Depends(get_db)):
    e = _ml_emp(s, eid)
    if not e.ml_refresh:
        raise HTTPException(400, "Conecte a empresa ao Mercado Livre primeiro.")
    b = await request.json()
    d1, d2 = _datas_extrato(b.get("de"), b.get("ate"))
    return _iniciar_job(s, e, "ml_extrato", {"de": d1.isoformat(), "ate": d2.isoformat()}, u, mlivre.sincronizar_extrato)


@app.post("/api/empresas/{eid}/mlivre/extrato/arquivo")
async def ml_extrato_arquivo(eid: int, request: Request, u=Depends(usuario), s=Depends(get_db)):
    """Relatório de liberações baixado do Mercado Pago (CSV ou XLSX, em base64)."""
    import base64
    e = _ml_emp(s, eid)
    b = await request.json()
    try:
        bruto = base64.b64decode(b.get("arquivo") or "")
    except ValueError:
        raise HTTPException(400, "Arquivo inválido.") from None
    nome = str(b.get("nome") or "").lower()
    try:
        if nome.endswith((".xlsx", ".xlsm")) or bruto[:2] == b"PK":
            from openpyxl import load_workbook
            wb = load_workbook(io.BytesIO(bruto), data_only=True, read_only=True)
            tabela = [list(r) for r in wb.active.iter_rows(values_only=True)]
            while tabela and not any(tabela[0]):
                tabela.pop(0)
        else:
            try:
                texto = bruto.decode("utf-8")
            except UnicodeDecodeError:
                texto = bruto.decode("latin-1")
            tabela = mlivre.ler_csv(texto)
        linhas, faltam = mlivre.linhas_extrato(tabela, e.id, "ARQUIVO")
    except mlivre.MLErro as ex:
        raise HTTPException(400, str(ex)) from None
    except Exception:  # noqa: BLE001
        raise HTTPException(400, "Não consegui ler o arquivo. Envie o Relatório de liberações do Mercado Pago em CSV ou Excel.") from None
    if not linhas:
        raise HTTPException(400, "Nenhum movimento encontrado no arquivo.")
    d1, d2 = min(x.data for x in linhas).date(), max(x.data for x in linhas).date()
    n = mlivre.gravar_extrato(s, e.id, linhas, d1, d2)
    ident = sum(1 for x in linhas if x.order_id)
    audit(s, u.email, "ML_EXTRATO_ARQUIVO", f"{e.id} {d1} a {d2}: {n}")
    s.commit()
    return {"ok": True, "movimentos": n, "identificados": ident, "de": d1.isoformat(), "ate": d2.isoformat(), "faltam": faltam}


@app.get("/api/empresas/{eid}/mlivre/auditoria")
def ml_auditoria(eid: int, inicio: str, fim: str, _u=Depends(usuario), s=Depends(get_db)):
    e = _ml_emp(s, eid)
    _periodo_ml(inicio, fim)
    return mlauditoria.auditoria(s, e, inicio, fim)


@app.get("/api/empresas/{eid}/mlivre/auditoria.xlsx")
def ml_auditoria_xlsx(eid: int, inicio: str, fim: str, _u=Depends(usuario), s=Depends(get_db)):
    e = _ml_emp(s, eid)
    _periodo_ml(inicio, fim)
    a = mlauditoria.auditoria(s, e, inicio, fim)
    cab = ["Gravidade", "Achado", "Data", "Pedido", "Link no Mercado Livre", "Produto / movimento", "Detalhe", "Esperado", "Cobrado / lançado", "Diferença"]
    linhas = [[x["gravidade"], x["codigo"], x["data"], str(x["order_id"] or ""),
               mlivre.link_venda(x["order_id"]) if x["order_id"] else "", x["descricao"], x["texto"], x["esperado"],
               x["cobrado"], x["diferenca"]] for x in a["achados"]]
    return _tabela_xlsx(f"Auditoria_ML_{inicio}_{fim}.xlsx", "Auditoria", cab, linhas, {7: MOEDA, 8: MOEDA, 9: MOEDA})


def _xml_nota_ml(s, e, nid):
    n = s.get(MLNota, nid)
    if not n or n.empresa_id != e.id:
        _404()
    if n.xml:
        return n, n.xml, ""
    xml, link, erros = "", "", []
    if e.ml_refresh and e.ml_user_id:
        try:
            xml = mlivre.xml_da_nota(mlivre.Cliente(e.id), e.ml_user_id, n)
        except mlivre.MLErro as ex:
            erros.append(str(ex))
    if not xml and e.bling_refresh:
        try:
            xml, link = bling.nota_por_chave(e.id, n.chave, n.numero, n.data)
        except Exception as ex:  # noqa: BLE001
            erros.append(f"Bling: {ex}")
    if xml:
        n.xml = xml
        s.commit()
    if not xml and not link and erros:
        raise HTTPException(404, "Não encontrei o XML desta nota. " + " | ".join(erros)[:400])
    return n, xml, link


@app.get("/api/empresas/{eid}/mlivre/notas/{nid}/danfe")
def ml_nota_danfe(eid: int, nid: int, _u=Depends(usuario), s=Depends(get_db)):
    e = _ml_emp(s, eid)
    n, xml, link = _xml_nota_ml(s, e, nid)
    if not xml:
        if link:
            return RedirectResponse(link)
        raise HTTPException(404, "Não encontrei o XML desta nota no emissor do Mercado Livre nem no Bling.")
    try:
        from brazilfiscalreport.danfe import Danfe
        pdf = bytes(Danfe(xml=xml).output())
    except Exception as ex:  # noqa: BLE001
        raise HTTPException(422, f"Não consegui montar o DANFE deste XML ({type(ex).__name__}). Baixe o XML.") from None
    return Response(pdf, media_type="application/pdf",
                    headers={"Content-Disposition": f'inline; filename="DANFE-{n.numero or n.chave}.pdf"', "Cache-Control": "private, no-store"})


@app.get("/api/empresas/{eid}/mlivre/notas/{nid}/xml")
def ml_nota_xml(eid: int, nid: int, _u=Depends(usuario), s=Depends(get_db)):
    e = _ml_emp(s, eid)
    n, xml, _link = _xml_nota_ml(s, e, nid)
    if not xml:
        raise HTTPException(404, "Não encontrei o XML desta nota no emissor do Mercado Livre nem no Bling.")
    return Response(xml, media_type="application/xml",
                    headers={"Content-Disposition": f'attachment; filename="NFe-{n.chave or n.numero}.xml"'})


@app.get("/api/empresas/{eid}/mlivre/tarifas")
def ml_tarifas(eid: int, inicio: str, fim: str, _u=Depends(usuario), s=Depends(get_db)):
    e = _ml_emp(s, eid)
    _periodo_ml(inicio, fim)
    ts = s.scalars(select(MLTarifa).where(MLTarifa.empresa_id == e.id, MLTarifa.periodo >= inicio, MLTarifa.periodo <= fim)
                   .order_by(MLTarifa.periodo, MLTarifa.natureza, MLTarifa.valor.desc())).all()
    nomes = {c: n for c, n, *_ in mlanalise.LINHAS}
    return [{"periodo": t.periodo, "grupo": t.grupo, "natureza": t.natureza, "tipo": t.tipo, "rotulo": t.rotulo,
             "linha": nomes[mlanalise.linha_tarifa(t)],
             "valor": abs(float(t.valor or 0)) * (-1 if t.natureza == "CHARGE" else 1),
             "de": t.de.isoformat() if t.de else None, "ate": t.ate.isoformat() if t.ate else None} for t in ts]


@app.get("/api/empresas/{eid}/mlivre/custos")
def ml_custos(eid: int, vigencia: str = "", _u=Depends(usuario), s=Depends(get_db)):
    """Produtos já vendidos no ML (por SKU ou anúncio) com o custo vigente no mês `vigencia` (padrão: mês atual)."""
    e = _ml_emp(s, eid)
    mes = _mes_ok(vigencia, "Vigência") if vigencia else agora().strftime("%Y-%m")
    cus = mlanalise.Custos(s, e.id)
    prods = {}
    pagos = select(MLVenda.order_id).where(MLVenda.empresa_id == e.id, MLVenda.pago > 0)
    for it in s.scalars(select(MLItem).where(MLItem.empresa_id == e.id, MLItem.order_id.in_(pagos))).all():
        k = mlanalise.chave(it)
        x = prods.setdefault(k, {"chave": k, "sku": it.sku, "anuncio": it.anuncio, "titulo": it.titulo, "qtd": 0.0, "vendas": 0.0})
        x["qtd"] += float(it.qtd or 0)
        x["vendas"] += float(it.qtd or 0) * float(it.preco or 0)
    for k in cus.ml:
        prods.setdefault(k, {"chave": k, "sku": k, "anuncio": "", "titulo": "", "qtd": 0.0, "vendas": 0.0})
    out = []
    for x in prods.values():
        x["custo"], x["origem"], x["estimado"] = cus.custo(x["chave"], mes)
        x["vigencias"] = cus.vigencias_ml(x["chave"])
        x["vendas"] = round(x["vendas"], 2)
        x["preco_medio"] = round(x["vendas"] / x["qtd"], 2) if x["qtd"] else None
        out.append(x)
    out.sort(key=lambda x: (x["custo"] is not None, -x["vendas"]))
    return {"vigencia": mes, "itens": out}


@app.put("/api/empresas/{eid}/mlivre/custos")
async def ml_custo_salvar(eid: int, request: Request, u=Depends(usuario), s=Depends(get_db)):
    e = _ml_emp(s, eid)
    b = await request.json()
    k = str(b.get("chave") or "").strip()[:80]
    mes = _mes_ok(b.get("vigencia"), "Vigência")
    v = _num_br(b.get("custo"))
    if not k:
        raise HTTPException(400, "Produto não informado.")
    if v is None or v < 0:
        raise HTTPException(400, "Custo inválido.")
    c = s.scalar(select(MLCusto).where(MLCusto.empresa_id == e.id, MLCusto.chave == k, MLCusto.mes_inicio == mes))
    if not c:
        c = MLCusto(empresa_id=e.id, chave=k, mes_inicio=mes)
        s.add(c)
    c.custo, c.criado_por = v, u.email
    audit(s, u.email, "ML_CUSTO", f"{e.id} {k} {mes} {v}")
    s.commit()
    return {"ok": True, "chave": k, "custo": v, "vigencia": mes}


@app.get("/api/empresas/{eid}/mlivre/custos/historico")
def ml_custo_historico(eid: int, chave: str, _u=Depends(usuario), s=Depends(get_db)):
    return [{"id": c.id, "vigencia": c.mes_inicio, "custo": float(c.custo), "por": c.criado_por, "em": dt2s(c.criado_em)}
            for c in s.scalars(select(MLCusto).where(MLCusto.empresa_id == eid, MLCusto.chave == chave)
                               .order_by(MLCusto.mes_inicio.desc())).all()]


@app.delete("/api/empresas/{eid}/mlivre/custos/{cid}")
def ml_custo_excluir(eid: int, cid: int, u=Depends(usuario), s=Depends(get_db)):
    c = s.get(MLCusto, cid)
    if not c or c.empresa_id != eid:
        _404()
    audit(s, u.email, "ML_CUSTO_EXCLUI", f"{eid} {c.chave} {c.mes_inicio}")
    s.delete(c)
    s.commit()
    return {"ok": True}


@app.post("/api/empresas/{eid}/mlivre/custos/copiar")
async def ml_custo_copiar(eid: int, request: Request, u=Depends(usuario), s=Depends(get_db)):
    """Preenche os produtos sem custo com o custo da aba Custo dos produtos (Domínio), casando o SKU com o código do produto.
    Só copia: depois disso os custos do módulo do ML são independentes."""
    e = _ml_emp(s, eid)
    b = await request.json()
    mes = _mes_ok(b.get("vigencia"), "Vigência")
    cus = mlanalise.Custos(s, e.id)
    n = 0
    for k in {mlanalise.chave(it) for it in s.scalars(select(MLItem).where(MLItem.empresa_id == e.id)).all()}:
        c, origem, _est = cus.custo(k, mes)
        if origem == "CADASTRO":
            s.add(MLCusto(empresa_id=e.id, chave=k, mes_inicio=mes, custo=c, criado_por=u.email))
            n += 1
    audit(s, u.email, "ML_CUSTO_COPIA", f"{e.id} {mes}: {n}")
    s.commit()
    return {"ok": True, "copiados": n}


@app.post("/api/empresas/{eid}/dominio/importar")
async def dominio_importar(eid: int, request: Request, u=Depends(usuario), s=Depends(get_db)):
    e = s.get(Empresa, eid) or _404()
    if not e.cnpj and not e.codigo_dominio:
        raise HTTPException(400, "Informe o CNPJ ou o código da Domínio da empresa.")
    b = await request.json()
    ini, fim = _mes_ok(b.get("inicio"), "Início"), _mes_ok(b.get("fim"), "Fim")
    if ini > fim:
        raise HTTPException(400, "O início é depois do fim.")
    if len(dre.meses_entre(ini, fim)) > 36:
        raise HTTPException(400, "Período máximo de 36 meses.")
    try:
        dominio.central(s)
    except RuntimeError as ex:
        raise HTTPException(400, str(ex)) from None
    return _iniciar_job(s, e, "dominio_importar", {"inicio": ini, "fim": fim}, u, dominio.importar)


@app.get("/api/jobs")
def jobs(empresa_id: int | None = None, _u=Depends(usuario), s=Depends(get_db)):
    st = select(Job).order_by(Job.id.desc()).limit(50)
    if empresa_id:
        st = st.where(Job.empresa_id == empresa_id)
    return [_job_json(j) for j in s.scalars(st).all()]


@app.get("/api/jobs/{jid}")
def job_ver(jid: int, _u=Depends(usuario), s=Depends(get_db)):
    return _job_json(s.get(Job, jid) or _404())


# ------------------------------------------------------------------ contas de origem e associação
def _nome_completo(c, por_cod):
    pai = por_cod.get(c.pai_codigo) if c.pai_codigo else None
    return f"{pai.descricao} › {c.descricao}" if pai else c.descricao


@app.get("/api/empresas/{eid}/contas")
def contas(eid: int, inicio: str = "", fim: str = "", _u=Depends(usuario), s=Depends(get_db)):
    e = s.get(Empresa, eid) or _404()
    totais = {}
    if inicio and fim:
        _mes_ok(inicio, "Início")
        _mes_ok(fim, "Fim")
        _c, vals, _sc = dre.valores_origem(s, e.id, inicio, fim)
        totais = {k: round(sum(v.values()), 2) for k, v in vals.items()}
    rows = s.scalars(select(ContaOrigem).where(ContaOrigem.empresa_id == e.id)).all()
    por_cod = {c.codigo: c for c in rows if c.origem == "BLING"}
    qtd = dict(s.execute(select(Movimento.categoria_codigo, func.count(Movimento.id)).where(
        Movimento.empresa_id == e.id).group_by(Movimento.categoria_codigo)).all())
    linhas = {ln.id: ln for ln in s.scalars(select(LinhaDRE).where(LinhaDRE.modelo_id == e.modelo_id)).all()} if e.modelo_id else {}
    out = []
    for c in rows:
        ln = linhas.get(c.linha_id)
        out.append({"id": c.id, "origem": c.origem, "codigo": c.codigo, "descricao": c.descricao,
                    "nome": _nome_completo(c, por_cod) if c.origem == "BLING" else c.descricao,
                    "tipo": c.tipo, "ativa": c.ativa, "linha_id": c.linha_id if ln else None,
                    "linha_nome": ln.nome if ln else None, "movimentos": qtd.get(c.codigo, 0) if c.origem == "BLING" else None,
                    "total": totais.get(c.id)})
    ordem_origem = {"DOMINIO": 0, "MLIVRE": 1, "BLING": 2}
    out.sort(key=lambda x: (ordem_origem.get(x["origem"], 3), not x["ativa"], x["tipo"] != "R", x["nome"].lower()))
    return {"empresa": _emp_json(s, e), "contas": out}


@app.put("/api/contas/{cid}")
async def conta_associar(cid: int, request: Request, u=Depends(usuario), s=Depends(get_db)):
    c = s.get(ContaOrigem, cid) or _404()
    b = await request.json()
    _associar(s, c, b.get("linha_id"))
    audit(s, u.email, "ASSOCIA", f"{c.empresa_id} {c.origem}:{c.codigo} -> {c.linha_id}")
    s.commit()
    return {"ok": True, "linha_id": c.linha_id}


def _associar(s, c, linha_id):
    if not linha_id:
        c.linha_id = None
        return
    e = s.get(Empresa, c.empresa_id)
    ln = s.get(LinhaDRE, int(linha_id))
    if not ln or ln.modelo_id != e.modelo_id:
        raise HTTPException(400, "Linha não pertence ao modelo de DRE da empresa.")
    if ln.tipo != "CONTA":
        raise HTTPException(400, "Só é possível associar a linhas do tipo Conta (totais e títulos são calculados).")
    c.linha_id = ln.id


@app.post("/api/empresas/{eid}/contas/lote")
async def contas_lote(eid: int, request: Request, u=Depends(usuario), s=Depends(get_db)):
    """[{conta_id, linha_id}] de uma vez."""
    b = await request.json()
    n = 0
    for it in b.get("itens") or []:
        c = s.get(ContaOrigem, int(it.get("conta_id") or 0))
        if c and c.empresa_id == eid:
            _associar(s, c, it.get("linha_id"))
            n += 1
    audit(s, u.email, "ASSOCIA_LOTE", f"{eid}: {n}")
    s.commit()
    return {"ok": True, "alteradas": n}


@app.post("/api/empresas/{eid}/contas/copiar")
async def contas_copiar(eid: int, request: Request, u=Depends(usuario), s=Depends(get_db)):
    """Copia as associações de outra empresa (mesmo modelo), casando categorias pela descrição."""
    e = s.get(Empresa, eid) or _404()
    b = await request.json()
    o = s.get(Empresa, int(b.get("de_empresa_id") or 0)) or _404("Empresa de origem não encontrada.")
    if o.modelo_id != e.modelo_id:
        raise HTTPException(400, "As duas empresas precisam usar o mesmo modelo de DRE.")
    sobrescrever = bool(b.get("sobrescrever"))

    def chave(c):
        return (c.origem, c.codigo if c.origem != "BLING" else re.sub(r"\s+", " ", c.descricao.strip().lower()))

    mapa = {chave(c): c.linha_id for c in s.scalars(select(ContaOrigem).where(
        ContaOrigem.empresa_id == o.id, ContaOrigem.linha_id.is_not(None))).all()}
    n = 0
    for c in s.scalars(select(ContaOrigem).where(ContaOrigem.empresa_id == e.id)).all():
        lid = mapa.get(chave(c))
        if lid and (sobrescrever or not c.linha_id) and c.linha_id != lid:
            c.linha_id = lid
            n += 1
    audit(s, u.email, "ASSOCIA_COPIA", f"{o.id} -> {e.id}: {n}")
    s.commit()
    return {"ok": True, "alteradas": n}


@app.get("/api/contas/{cid}/detalhe")
def conta_detalhe(cid: int, inicio: str, fim: str, _u=Depends(usuario), s=Depends(get_db)):
    """Botão Consultar: lançamentos de Caixa e Bancos da categoria (Bling) ou os CFOPs que formam o grupo (Domínio)."""
    c = s.get(ContaOrigem, cid) or _404()
    _mes_ok(inicio, "Início")
    _mes_ok(fim, "Fim")
    if c.origem == "BLING":
        d_ini, d_fim = dre.periodo_datas(inicio, fim)
        itens = [{"id_bling": x.id_externo, "data": x.data.isoformat(), "tipo": "Entrada" if x.deb_cred == "C" else "Saída",
                  "conta_financeira": x.conta_financeira, "contato": x.contato, "descricao": x.descricao,
                  "origem": ORIGENS_CAIXA.get(x.origem_tipo, x.origem_tipo), "valor": round(x.assinado, 2)}
                 for x in s.scalars(select(Movimento).where(Movimento.empresa_id == c.empresa_id, Movimento.categoria_codigo == c.codigo,
                                                            Movimento.data >= d_ini, Movimento.data <= d_fim)
                                    .order_by(Movimento.data, Movimento.id)).all()]
        return {"conta": {"id": c.id, "origem": c.origem, "descricao": c.descricao}, "tipo": "caixa", "itens": itens,
                "total": round(sum(i["valor"] for i in itens), 2)}
    if c.codigo == "ICMS_TTD":
        por = {}
        for v in s.scalars(select(DominioValor).where(DominioValor.empresa_id == c.empresa_id, DominioValor.mes >= inicio,
                                                      DominioValor.mes <= fim, DominioValor.grupo.in_(("ICMS_REG", "ICMS_DEB", "ICMS_CP")))).all():
            x = por.setdefault(v.mes, {"mes": v.mes, "debito": 0.0, "credito_presumido": 0.0, "saldo_recolher": 0.0})
            x[{"ICMS_REG": "saldo_recolher", "ICMS_DEB": "debito", "ICMS_CP": "credito_presumido"}[v.grupo]] += float(v.valor)
        itens = [por[m] for m in sorted(por)]
        return {"conta": {"id": c.id, "origem": c.origem, "descricao": c.descricao}, "tipo": "icms", "itens": itens,
                "total": round(-sum(x["saldo_recolher"] for x in itens), 2)}
    if c.codigo == "PIS_COFINS":
        e = s.get(Empresa, c.empresa_id)
        fonte = e.pis_cofins_fonte or "APURACAO"
        itens = dominio.pis_cofins(s, c.empresa_id, inicio, fim, float(e.pis_cofins_aliq or 0), fonte)
        return {"conta": {"id": c.id, "origem": c.origem, "descricao": c.descricao}, "tipo": "pis", "itens": itens,
                "total": round(-sum(x["valor"] for x in itens), 2), "aliquota": float(e.pis_cofins_aliq or 0), "fonte": fonte}
    if c.codigo == "CMV":
        _pm, por_prod, sem = dominio.cmv(s, c.empresa_id, inicio, fim)
        itens = sorted(({k: v for k, v in x.items() if k != "id"} for x in por_prod.values()), key=lambda x: -x["valor_vendido"])
        return {"conta": {"id": c.id, "origem": c.origem, "descricao": c.descricao}, "tipo": "cmv", "itens": itens,
                "total": round(-sum(x["cmv"] for x in itens), 2), "sem_custo": sem}
    produtos_ = []
    if c.codigo in ("VENDAS", "DEV_VENDA"):
        cad = {p.produto: p for p in s.scalars(select(Produto).where(Produto.empresa_id == c.empresa_id)).all()}
        agg = {}
        for it in s.scalars(select(DominioItem).where(DominioItem.empresa_id == c.empresa_id, DominioItem.mes >= inicio,
                                                      DominioItem.mes <= fim)).all():
            ok = (it.lado == "S" and dominio.eh_venda(it.cfop)) if c.codigo == "VENDAS" else \
                 (it.lado == "E" and it.cfop in dominio.DEVOLUCAO_VENDA)
            if ok:
                x = agg.setdefault(it.produto, [0.0, 0.0])
                x[0] += float(it.qtd or 0)
                x[1] += float(it.valor or 0)
        for k, (q, v) in sorted(agg.items(), key=lambda kv: -kv[1][1]):
            p = cad.get(k)
            produtos_.append({"codigo": p.codigo if p else k, "descricao": p.descricao if p else "", "unidade": p.unidade if p else "",
                              "qtd": round(q, 4), "valor": round(v, 2)})
    itens = []
    for v in s.scalars(select(DominioValor).where(DominioValor.empresa_id == c.empresa_id, DominioValor.mes >= inicio,
                                                  DominioValor.mes <= fim).order_by(DominioValor.mes, DominioValor.cfop)).all():
        if dominio.grupo(v.grupo, v.cfop) == c.codigo:
            itens.append({"mes": v.mes, "origem": {"SAIDA": "NF de saída", "ENTRADA": "NF de entrada",
                                                    "SERVICO": "Serviços prestados"}[v.grupo],
                          "cfop": v.cfop or None, "qtd": v.qtd, "valor": float(v.valor)})
    return {"conta": {"id": c.id, "origem": c.origem, "descricao": c.descricao}, "tipo": "cfops", "itens": itens,
            "total": round(sum(i["valor"] for i in itens), 2), "produtos": produtos_}


ORIGENS_CAIXA = {"caixa": "Lançamento manual", "duplicata": "Conta a pagar/receber", "bordero": "Baixa (borderô)",
                 "estoque": "Estoque"}


# ------------------------------------------------------------------ custo dos produtos (CMV)
def _num_br(v):
    """Aceita 12,50 / 1.234,56 / 12.5 / número."""
    if v is None or v == "":
        return None
    if isinstance(v, (int, float)):
        return float(v)
    t = str(v).strip().replace("R$", "").replace(" ", "")
    if "," in t:
        t = t.replace(".", "").replace(",", ".")
    try:
        return float(t)
    except ValueError:
        raise HTTPException(400, f"Valor inválido: {v}") from None


@app.get("/api/empresas/{eid}/produtos")
def produtos(eid: int, inicio: str, fim: str, vigencia: str = "", _u=Depends(usuario), s=Depends(get_db)):
    """Produtos da empresa com venda/devolução no período, custo vigente no mês `vigencia` (padrão: o fim) e CMV."""
    e = s.get(Empresa, eid) or _404()
    _mes_ok(inicio, "Início")
    _mes_ok(fim, "Fim")
    vigencia = _mes_ok(vigencia, "Vigência") if vigencia else fim
    _pm, por_prod, sem_custo = dominio.cmv(s, e.id, inicio, fim, vigencia)
    vig = dominio.vigencias(s, e.id)
    itens = []
    for p in s.scalars(select(Produto).where(Produto.empresa_id == e.id)).all():
        x = por_prod.get(p.produto) or {}
        lista = vig.get(p.produto) or []
        vale = [m for m, _c in lista if m <= vigencia]
        liq = x.get("valor_vendido", 0) - x.get("valor_devolvido", 0)
        itens.append({"id": p.id, "produto": p.produto, "codigo": p.codigo, "descricao": p.descricao, "unidade": p.unidade,
                      "custo": dominio.custo_no_mes(lista, vigencia), "custo_desde": vale[-1] if vale else None,
                      "custo_proprio_mes": bool(vale) and vale[-1] == vigencia, "vigencias": len(lista),
                      "qtd_vendida": round(x.get("qtd_vendida", 0), 4), "valor_vendido": round(x.get("valor_vendido", 0), 2),
                      "qtd_devolvida": round(x.get("qtd_devolvida", 0), 4), "valor_devolvido": round(x.get("valor_devolvido", 0), 2),
                      "cmv": x.get("cmv", 0), "meses_sem_custo": x.get("meses_sem_custo", []),
                      "margem": round((1 - x["cmv"] / liq) * 100, 1) if x.get("cmv") and liq and not x.get("meses_sem_custo") else None})
    itens.sort(key=lambda i: (-i["valor_vendido"], i["descricao"]))
    # conferência mês a mês: valor contábil das notas de venda (DRE) x soma dos itens de venda
    conf = {}
    for v in s.scalars(select(DominioValor).where(DominioValor.empresa_id == e.id, DominioValor.mes >= inicio,
                                                  DominioValor.mes <= fim, DominioValor.grupo == "SAIDA")).all():
        if dominio.eh_venda(v.cfop):
            x = conf.setdefault(v.mes, {"mes": v.mes, "notas": 0.0, "itens": 0.0, "qtd_notas": 0})
            x["notas"] += float(v.valor or 0)
            x["qtd_notas"] += v.qtd or 0
    for it in s.scalars(select(DominioItem).where(DominioItem.empresa_id == e.id, DominioItem.mes >= inicio,
                                                  DominioItem.mes <= fim, DominioItem.lado == "S")).all():
        if dominio.eh_venda(it.cfop):
            x = conf.setdefault(it.mes, {"mes": it.mes, "notas": 0.0, "itens": 0.0, "qtd_notas": 0})
            x["itens"] += float(it.valor or 0)
    conferencia = [{**x, "notas": round(x["notas"], 2), "itens": round(x["itens"], 2),
                    "diferenca": round(x["itens"] - x["notas"], 2)} for _m, x in sorted(conf.items())]
    return {"itens": itens, "sem_custo": sem_custo, "vigencia": vigencia, "conferencia": conferencia,
            "totais": {"valor_vendido": round(sum(i["valor_vendido"] for i in itens), 2),
                       "valor_devolvido": round(sum(i["valor_devolvido"] for i in itens), 2),
                       "cmv": round(sum(i["cmv"] for i in itens), 2)},
            "dominio_sync_periodo": e.dominio_sync_periodo}


def _set_custo(s, p: Produto, custo, mes, u):
    """Grava o custo a partir do mês; custo vazio apaga a vigência daquele mês."""
    if custo is not None and custo < 0:
        raise HTTPException(400, "O custo não pode ser negativo.")
    x = s.scalar(select(ProdutoCusto).where(ProdutoCusto.produto_id == p.id, ProdutoCusto.mes_inicio == mes))
    if custo is None:
        if x:
            s.delete(x)
        return
    if not x:
        x = ProdutoCusto(produto_id=p.id, mes_inicio=mes, custo=0)
        s.add(x)
    x.custo, x.criado_em, x.criado_por = round(custo, 4), agora(), u.email
    p.custo_em, p.custo_por = agora(), u.email


@app.put("/api/produtos/{pid}")
async def produto_custo(pid: int, request: Request, u=Depends(usuario), s=Depends(get_db)):
    p = s.get(Produto, pid) or _404()
    b = await request.json()
    mes = _mes_ok(b.get("vigencia"), "Vigência")
    custo = _num_br(b.get("custo"))
    _set_custo(s, p, custo, mes, u)
    audit(s, u.email, "CUSTO", f"{p.empresa_id} {p.codigo} a partir de {mes} = {custo}")
    s.commit()
    return {"ok": True, "custo": dominio.custo_no_mes(dominio.vigencias(s, p.empresa_id).get(p.produto), mes)}


@app.get("/api/produtos/{pid}/custos")
def produto_historico(pid: int, _u=Depends(usuario), s=Depends(get_db)):
    p = s.get(Produto, pid) or _404()
    return {"produto": {"id": p.id, "codigo": p.codigo, "descricao": p.descricao, "unidade": p.unidade},
            "custos": [{"id": c.id, "mes_inicio": c.mes_inicio, "custo": float(c.custo), "criado_em": dt2s(c.criado_em),
                        "criado_por": c.criado_por}
                       for c in s.scalars(select(ProdutoCusto).where(ProdutoCusto.produto_id == p.id)
                                          .order_by(ProdutoCusto.mes_inicio.desc())).all()]}


@app.delete("/api/produtos/{pid}/custos/{cid}")
def produto_custo_excluir(pid: int, cid: int, u=Depends(usuario), s=Depends(get_db)):
    c = s.get(ProdutoCusto, cid)
    if not c or c.produto_id != pid:
        _404()
    p = s.get(Produto, pid)
    audit(s, u.email, "CUSTO_EXCLUI", f"{p.empresa_id} {p.codigo} vigência {c.mes_inicio} ({c.custo})")
    s.delete(c)
    s.commit()
    return {"ok": True}


@app.get("/api/empresas/{eid}/produtos.xlsx")
def produtos_excel(eid: int, inicio: str, fim: str, vigencia: str = "", u=Depends(usuario), s=Depends(get_db)):
    from openpyxl import Workbook
    from openpyxl.styles import Font, PatternFill
    e = s.get(Empresa, eid) or _404()
    r = produtos(eid, inicio, fim, vigencia, u, s)
    wb = Workbook()
    ws = wb.active
    ws.title = "Custos"
    cab = ["Código interno", "Código", "Descrição", "Unidade", "Vigência (AAAA-MM)", "Custo unitário", "Qtd vendida",
           "Faturamento", "Qtd devolvida", "CMV no período"]
    ws.append(cab)
    for c in ws[1]:
        c.font = Font(bold=True, color="FFFFFF")
        c.fill = PatternFill("solid", fgColor="1F3A5F")
    for i in r["itens"]:
        ws.append([i["produto"], i["codigo"], i["descricao"], i["unidade"], r["vigencia"], i["custo"], i["qtd_vendida"],
                   i["valor_vendido"], i["qtd_devolvida"], i["cmv"]])
    for col, w in zip("ABCDEFGHIJ", (14, 16, 50, 9, 18, 15, 13, 15, 13, 15)):
        ws.column_dimensions[col].width = w
    for row in ws.iter_rows(min_row=2):
        row[5].number_format = "#,##0.0000"
        row[4].fill = row[5].fill = PatternFill("solid", fgColor="FFF7D6")
        for c in (row[7], row[9]):
            c.number_format = "#,##0.00"
    ws2 = wb.create_sheet("Como preencher")
    for t in ("Preencha 'Custo unitário' e, se quiser, troque a 'Vigência (AAAA-MM)': o custo vale a partir desse mês "
              "até a próxima vigência do produto.",
              "Para lançar vários meses do mesmo produto, repita a linha com outra vigência.",
              "Linhas com custo vazio são ignoradas (não apagam nada). Para apagar uma vigência, use o histórico na tela."):
        ws2.append([t])
    ws2.column_dimensions["A"].width = 120
    ws.freeze_panes = "A2"
    buf = io.BytesIO()
    wb.save(buf)
    buf.seek(0)
    nome = re.sub(r"[^A-Za-z0-9]+", "_", e.nome)[:40]
    return StreamingResponse(buf, media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                             headers={"Content-Disposition": f'attachment; filename="Custos_{nome}_{r["vigencia"]}.xlsx"'})


def _mes_planilha(v, padrao):
    if v in (None, ""):
        return padrao
    if isinstance(v, (dt.date, dt.datetime)):
        return f"{v.year:04d}-{v.month:02d}"
    t = str(v).strip()
    m = re.fullmatch(r"(\d{4})[-/](\d{1,2})(?:[-/]\d{1,2})?", t) or None
    if m:
        return f"{int(m.group(1)):04d}-{int(m.group(2)):02d}"
    m = re.fullmatch(r"(\d{1,2})[-/](\d{4})", t)
    if m:
        return f"{int(m.group(2)):04d}-{int(m.group(1)):02d}"
    raise HTTPException(400, f"Vigência inválida na planilha: {t} (use AAAA-MM).")


@app.post("/api/empresas/{eid}/produtos/importar")
async def produtos_importar(eid: int, request: Request, u=Depends(usuario), s=Depends(get_db)):
    """Planilha (base64) com 'Código interno' ou 'Código', 'Custo unitário' e (opcional) 'Vigência (AAAA-MM)'."""
    import base64
    from openpyxl import load_workbook
    e = s.get(Empresa, eid) or _404()
    b = await request.json()
    padrao = _mes_ok(b.get("vigencia"), "Vigência")
    try:
        wb = load_workbook(io.BytesIO(base64.b64decode(b.get("arquivo") or "")), data_only=True, read_only=True)
    except Exception:  # noqa: BLE001
        raise HTTPException(400, "Não consegui abrir a planilha. Use o arquivo .xlsx baixado em Baixar planilha.") from None
    ws = wb["Custos"] if "Custos" in wb.sheetnames else wb.active
    linhas = list(ws.iter_rows(values_only=True))
    if not linhas:
        raise HTTPException(400, "Planilha vazia.")
    cab = [re.sub(r"\s+", " ", str(c or "").strip().lower()) for c in linhas[0]]

    def idx(*nomes):
        return next((cab.index(n) for n in nomes if n in cab), None)
    i_int, i_cod = idx("código interno", "codigo interno"), idx("código", "codigo")
    i_custo = idx("custo unitário", "custo unitario", "custo")
    i_vig = idx("vigência (aaaa-mm)", "vigencia (aaaa-mm)", "vigência", "vigencia", "a partir de")
    if i_custo is None or (i_int is None and i_cod is None):
        raise HTTPException(400, "A planilha precisa das colunas 'Código interno' (ou 'Código') e 'Custo unitário'.")
    prods = s.scalars(select(Produto).where(Produto.empresa_id == e.id)).all()
    por_int = {p.produto: p for p in prods}
    por_cod = {p.codigo.strip().upper(): p for p in prods if p.codigo}
    ok, nao, meses = 0, [], set()
    for ln in linhas[1:]:
        if not ln or i_custo >= len(ln) or ln[i_custo] in (None, ""):
            continue
        chave_int = str(ln[i_int]).strip() if i_int is not None and ln[i_int] not in (None, "") else ""
        chave_cod = str(ln[i_cod]).strip().upper() if i_cod is not None and ln[i_cod] not in (None, "") else ""
        if chave_int.endswith(".0"):
            chave_int = chave_int[:-2]
        p = por_int.get(chave_int) or por_cod.get(chave_cod)
        if not p:
            nao.append(chave_cod or chave_int)
            continue
        mes = _mes_planilha(ln[i_vig] if i_vig is not None and i_vig < len(ln) else None, padrao)
        _set_custo(s, p, _num_br(ln[i_custo]), mes, u)
        s.flush()
        meses.add(mes)
        ok += 1
    audit(s, u.email, "CUSTO_IMPORTA", f"{e.id}: {ok} custos ({', '.join(sorted(meses))}), {len(nao)} não encontrados")
    s.commit()
    return {"ok": True, "atualizados": ok, "meses": sorted(meses), "nao_encontrados": nao[:50]}


# ------------------------------------------------------------------ fluxo de caixa realizado (Bling)
@app.get("/api/empresas/{eid}/fluxo")
def fluxo_ver(eid: int, inicio: str, fim: str, _u=Depends(usuario), s=Depends(get_db)):
    e = s.get(Empresa, eid) or _404()
    _mes_ok(inicio, "Início")
    _mes_ok(fim, "Fim")
    if inicio > fim:
        raise HTTPException(400, "O início é depois do fim.")
    if len(dre.meses_entre(inicio, fim)) > 36:
        raise HTTPException(400, "Período máximo de 36 meses.")
    r = fluxo.calcular(s, e.id, inicio, fim)
    return {"empresa": {"id": e.id, "nome": e.nome, "bling_sync_periodo": e.bling_sync_periodo,
                        "bling_sync_em": dt2s(e.bling_sync_em)}, **r}


@app.put("/api/empresas/{eid}/fluxo/ordem")
async def fluxo_ordem(eid: int, request: Request, u=Depends(usuario), s=Depends(get_db)):
    """[{conta_id, ordem, secao}] — reorganiza o fluxo (seção só para categorias de primeiro nível)."""
    s.get(Empresa, eid) or _404()
    b = await request.json()
    n = 0
    for it in b.get("itens") or []:
        c = s.get(ContaOrigem, int(it.get("conta_id") or 0))
        if not c or c.empresa_id != eid or c.origem != "BLING":
            continue
        if "ordem" in it:
            c.fluxo_ordem = int(it["ordem"] or 0)
        if "secao" in it:
            c.fluxo_secao = it["secao"] if it["secao"] in fluxo.SECOES else None
        n += 1
    audit(s, u.email, "FLUXO_ORDEM", f"{eid}: {n}")
    s.commit()
    return {"ok": True, "alteradas": n}


@app.get("/api/empresas/{eid}/fluxo.xlsx")
def fluxo_excel(eid: int, inicio: str, fim: str, u=Depends(usuario), s=Depends(get_db)):
    from openpyxl import Workbook
    from openpyxl.styles import Font, PatternFill
    from openpyxl.utils import get_column_letter
    r = fluxo_ver(eid, inicio, fim, u, s)
    e = s.get(Empresa, eid)
    wb = Workbook()
    ws = wb.active
    ws.title = "Fluxo de caixa"
    ws.append([f"Fluxo de caixa realizado — {e.nome}"])
    ws.append([f"{inicio[5:]}/{inicio[:4]} a {fim[5:]}/{fim[:4]} · lançamentos de Caixa e Bancos do Bling"])
    ws.append([])
    cab = ["Linha"] + [f"{m[5:]}/{m[:4]}" for m in r["meses"]] + ["Total"]
    ws.append(cab)
    for c in ws[4]:
        c.font = Font(bold=True, color="FFFFFF")
        c.fill = PatternFill("solid", fgColor="1F3A5F")
    ws["A1"].font = Font(bold=True, size=14)
    for ln in r["linhas"]:
        if ln["tipo"] == "CATEGORIA" and not ln["tem_mov"]:
            continue
        ws.append(["    " * ln["nivel"] + ln["nome"]] + ln["valores"] + [ln["total"]])
        row = ws.max_row
        for col in range(2, len(cab) + 1):
            ws.cell(row, col).number_format = '#,##0.00;[Red]-#,##0.00'
        if ln["tipo"] in ("SECAO", "VARIACAO", "SALDO_INI", "SALDO_FIM"):
            for col in range(1, len(cab) + 1):
                ws.cell(row, col).font = Font(bold=True)
                ws.cell(row, col).fill = PatternFill("solid", fgColor="E8EEF6")
    ws.column_dimensions["A"].width = 55
    for i in range(2, len(cab) + 1):
        ws.column_dimensions[get_column_letter(i)].width = 14
    ws.freeze_panes = "B5"
    buf = io.BytesIO()
    wb.save(buf)
    buf.seek(0)
    nome = re.sub(r"[^A-Za-z0-9]+", "_", e.nome)[:40]
    return StreamingResponse(buf, media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                             headers={"Content-Disposition": f'attachment; filename="Fluxo_{nome}_{inicio}_{fim}.xlsx"'})


# ------------------------------------------------------------------ modelos de DRE
def _modelo_json(s, m: ModeloDRE, linhas=True):
    d = {"id": m.id, "nome": m.nome, "descricao": m.descricao,
         "empresas": s.scalar(select(func.count(Empresa.id)).where(Empresa.modelo_id == m.id)) or 0}
    if linhas:
        lns = s.scalars(select(LinhaDRE).where(LinhaDRE.modelo_id == m.id).order_by(LinhaDRE.ordem, LinhaDRE.id)).all()
        cod = {ln.id: ln.codigo for ln in lns}
        d["linhas"] = [{"id": ln.id, "ordem": ln.ordem, "codigo": ln.codigo, "nome": ln.nome, "tipo": ln.tipo,
                        "sinal": ln.sinal, "formula": ln.formula, "destaque": ln.destaque, "base_av": ln.base_av,
                        "pai_codigo": cod.get(ln.pai_id)} for ln in lns]
    return d


@app.get("/api/modelos")
def modelos(_u=Depends(usuario), s=Depends(get_db)):
    return [_modelo_json(s, m) for m in s.scalars(select(ModeloDRE).order_by(ModeloDRE.nome)).all()]


@app.post("/api/modelos")
async def modelo_novo(request: Request, u=Depends(usuario), s=Depends(get_db)):
    b = await request.json()
    nome = str(b.get("nome") or "").strip()[:120] or "Novo modelo"
    base = s.get(ModeloDRE, int(b["copiar_de"])) if b.get("copiar_de") else None
    if base:
        m = ModeloDRE(nome=nome, descricao=base.descricao)
        s.add(m)
        s.flush()
        dre.gravar_linhas(s, m.id, [{k: v for k, v in x.items() if k != "id"} for x in _modelo_json(s, base)["linhas"]], {})
    elif b.get("vazio"):
        m = ModeloDRE(nome=nome)
        s.add(m)
        s.flush()
        dre.gravar_linhas(s, m.id, [{"codigo": "RECEITAS", "nome": "Receitas", "tipo": "GRUPO"},
                                    {"codigo": "DESPESAS", "nome": "(-) Despesas", "tipo": "GRUPO"},
                                    {"codigo": "RESULTADO", "nome": "(=) Resultado", "tipo": "TOTAL",
                                     "formula": "RECEITAS + DESPESAS", "base_av": False}], {})
    else:
        m = dre.criar_modelo_padrao(s, nome)
    audit(s, u.email, "MODELO_NOVO", nome)
    s.commit()
    return _modelo_json(s, m)


@app.get("/api/modelos/{mid}")
def modelo_ver(mid: int, _u=Depends(usuario), s=Depends(get_db)):
    return _modelo_json(s, s.get(ModeloDRE, mid) or _404())


@app.put("/api/modelos/{mid}")
async def modelo_salvar(mid: int, request: Request, u=Depends(usuario), s=Depends(get_db)):
    """Salva nome e todas as linhas (as linhas que não vierem são excluídas e perdem as associações)."""
    m = s.get(ModeloDRE, mid) or _404()
    b = await request.json()
    m.nome = str(b.get("nome") or m.nome).strip()[:120]
    m.descricao = str(b.get("descricao") or "")[:2000]
    linhas = b.get("linhas")
    if linhas is not None:
        for x in linhas:
            x["codigo"] = str(x.get("codigo") or "").strip().upper()
            if x.get("tipo") not in TIPOS_LINHA:
                raise HTTPException(400, f"{x['codigo']}: tipo inválido.")
            if not str(x.get("nome") or "").strip():
                raise HTTPException(400, f"{x['codigo']}: informe o nome da linha.")
            if x.get("pai_codigo"):
                x["pai_codigo"] = str(x["pai_codigo"]).strip().upper()
        linhas = dre.ordenar(linhas)
        erros = dre.validar_modelo(linhas)
        if erros:
            raise HTTPException(400, " ".join(erros[:5]))
        atuais = {ln.id: ln for ln in s.scalars(select(LinhaDRE).where(LinhaDRE.modelo_id == m.id)).all()}
        dre.gravar_linhas(s, m.id, linhas, atuais)
        for ln in s.scalars(select(LinhaDRE).where(LinhaDRE.modelo_id == m.id, LinhaDRE.tipo != "CONTA")).all():
            for c in s.scalars(select(ContaOrigem).where(ContaOrigem.linha_id == ln.id)).all():
                c.linha_id = None
    audit(s, u.email, "MODELO_SALVA", f"{m.id} {m.nome}")
    s.commit()
    return _modelo_json(s, m)


@app.delete("/api/modelos/{mid}")
def modelo_excluir(mid: int, u=Depends(admin), s=Depends(get_db)):
    m = s.get(ModeloDRE, mid) or _404()
    if s.scalar(select(func.count(Empresa.id)).where(Empresa.modelo_id == m.id)):
        raise HTTPException(400, "Há empresas usando este modelo. Troque o modelo delas antes de excluir.")
    audit(s, u.email, "MODELO_EXCLUI", m.nome)
    s.delete(m)
    s.commit()
    return {"ok": True}


# ------------------------------------------------------------------ DRE
def _calc(s, eid, inicio, fim):
    e = s.get(Empresa, eid) or _404()
    _mes_ok(inicio, "Início")
    _mes_ok(fim, "Fim")
    if inicio > fim:
        raise HTTPException(400, "O início é depois do fim.")
    if len(dre.meses_entre(inicio, fim)) > 36:
        raise HTTPException(400, "Período máximo de 36 meses.")
    if not e.modelo_id:
        raise HTTPException(400, "Escolha um modelo de DRE para a empresa.")
    return e, dre.calcular(s, e, inicio, fim)


@app.get("/api/empresas/{eid}/dre")
def dre_ver(eid: int, inicio: str, fim: str, _u=Depends(usuario), s=Depends(get_db)):
    e, r = _calc(s, eid, inicio, fim)
    return {"empresa": {"id": e.id, "nome": e.nome, "cnpj": e.cnpj,
                        "bling_sync_periodo": e.bling_sync_periodo, "dominio_sync_periodo": e.dominio_sync_periodo}, **r}


@app.get("/api/empresas/{eid}/dre.xlsx")
def dre_excel(eid: int, inicio: str, fim: str, _u=Depends(usuario), s=Depends(get_db)):
    from openpyxl import Workbook
    from openpyxl.styles import Font, PatternFill, Alignment
    from openpyxl.utils import get_column_letter
    e, r = _calc(s, eid, inicio, fim)
    wb = Workbook()
    ws = wb.active
    ws.title = "DRE"
    ws.append([f"DRE — {e.nome}"])
    ws.append([f"CNPJ {e.cnpj} · {inicio[5:]}/{inicio[:4]} a {fim[5:]}/{fim[:4]} · Bling pelo caixa (pagos e recebidos); "
               "Domínio pelas notas fiscais do mês"])
    ws.append([])
    cab = ["Linha"] + [f"{m[5:]}/{m[:4]}" for m in r["meses"]] + ["Total", "AV %"]
    ws.append(cab)
    for c in ws[4]:
        c.font = Font(bold=True, color="FFFFFF")
        c.fill = PatternFill("solid", fgColor="1F3A5F")
    ws["A1"].font = Font(bold=True, size=14)
    for ln in r["linhas"]:
        nome = ("      " + ln["nome"]) if ln["tipo"] == "CONTA" else ln["nome"]
        ws.append([nome] + ln["valores"] + [ln["total"], (ln["av_total"] or 0) / 100 if ln["av_total"] is not None else None])
        row = ws.max_row
        for col in range(2, len(r["meses"]) + 3):
            ws.cell(row, col).number_format = '#,##0.00;[Red]-#,##0.00'
        ws.cell(row, len(r["meses"]) + 3).number_format = "0.0%"
        if ln["tipo"] == "GRUPO":
            for col in range(1, len(cab) + 1):
                ws.cell(row, col).font = Font(bold=True, color="1F3A5F")
        if ln["tipo"] == "TOTAL" or ln["destaque"]:
            for col in range(1, len(cab) + 1):
                ws.cell(row, col).font = Font(bold=True)
                ws.cell(row, col).fill = PatternFill("solid", fgColor="E8EEF6")
    ws.column_dimensions["A"].width = 52
    for i in range(2, len(cab) + 1):
        ws.column_dimensions[get_column_letter(i)].width = 14
    ws.freeze_panes = "B5"
    ws2 = wb.create_sheet("Composição")
    ws2.append(["Linha da DRE", "Origem", "Conta de origem"] + [f"{m[5:]}/{m[:4]}" for m in r["meses"]] + ["Total"])
    for c in ws2[1]:
        c.font = Font(bold=True)
    for ln in r["linhas"]:
        for c in ln["composicao"]:
            ws2.append([ln["nome"], c["origem"], c["descricao"]] + c["valores"] + [c["total"]])
    for c in r["nao_associadas"]:
        ws2.append(["(sem associação)", c["origem"], c["descricao"]] + [None] * len(r["meses"]) + [c["total"]])
    ws2.column_dimensions["A"].width = 40
    ws2.column_dimensions["C"].width = 45
    buf = io.BytesIO()
    wb.save(buf)
    buf.seek(0)
    nome = re.sub(r"[^A-Za-z0-9]+", "_", e.nome)[:40]
    return StreamingResponse(buf, media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                             headers={"Content-Disposition": f'attachment; filename="DRE_{nome}_{inicio}_{fim}.xlsx"'})


@app.get("/api/auditoria")
def auditoria(_u=Depends(admin), s=Depends(get_db)):
    return [{"quando": dt2s(a.quando), "usuario": a.usuario, "acao": a.acao, "detalhe": a.detalhe}
            for a in s.scalars(select(Auditoria).order_by(Auditoria.id.desc()).limit(200)).all()]
