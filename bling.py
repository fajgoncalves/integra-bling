"""Bling API v3 — OAuth 2.0 por empresa, leitura de categorias e contas a pagar/receber.

Somente leitura: o Integra Bling nunca grava nada no Bling.
Limite do Bling: poucas requisições por segundo; o cliente espera entre chamadas e repete em 429.
"""
import time
import datetime as dt
import threading

import httpx
from sqlalchemy import select

from db import SessionLocal, Empresa, ContaOrigem, Lancamento, Job, agora
from seguranca import cifrar, decifrar, get_config

AUTORIZAR = "https://www.bling.com.br/Api/v3/oauth/authorize"
TOKEN = "https://www.bling.com.br/Api/v3/oauth/token"
API = "https://api.bling.com.br/Api/v3"
INTERVALO = 0.4  # segundos entre chamadas (≈ 2,5 por segundo)
SITUACAO_CANCELADA = 5
_trava = threading.Lock()
_ultima = [0.0]


class BlingErro(Exception):
    pass


def credenciais(s, emp: Empresa):
    """(client_id, client_secret): o aplicativo próprio da empresa ou o padrão das Configurações."""
    if emp.bling_client_id:
        return emp.bling_client_id, decifrar(emp.bling_client_secret)
    return get_config(s, "bling_client_id"), decifrar(get_config(s, "bling_client_secret"))


def url_autorizacao(client_id: str, state: str) -> str:
    return str(httpx.URL(AUTORIZAR, params={"response_type": "code", "client_id": client_id, "state": state}))


def _token(client_id, client_secret, dados, http=None):
    http = http or httpx.Client(timeout=30)
    r = http.post(TOKEN, data=dados, auth=(client_id, client_secret),
                  headers={"Accept": "1.0", "enable-jwt": "1", "Content-Type": "application/x-www-form-urlencoded"})
    if r.status_code >= 400:
        try:
            msg = r.json().get("error", {})
            msg = msg.get("description") or msg.get("message") or r.text if isinstance(msg, dict) else str(msg)
        except ValueError:
            msg = r.text
        raise BlingErro(f"Bling recusou a autorização ({r.status_code}): {str(msg)[:300]}")
    return r.json()


def guardar_tokens(emp: Empresa, t: dict):
    emp.bling_access = cifrar(t["access_token"])
    if t.get("refresh_token"):
        emp.bling_refresh = cifrar(t["refresh_token"])
    emp.bling_expira = agora() + dt.timedelta(seconds=int(t.get("expires_in") or 21600) - 120)


def trocar_codigo(s, emp: Empresa, code: str, http=None):
    cid, sec = credenciais(s, emp)
    if not cid or not sec:
        raise BlingErro("Cadastre o Client ID e o Client Secret do aplicativo do Bling.")
    guardar_tokens(emp, _token(cid, sec, {"grant_type": "authorization_code", "code": code}, http))
    emp.bling_conectado_em = agora()


class Cliente:
    """Cliente HTTP de uma empresa, com renovação automática do token."""

    def __init__(self, empresa_id: int, http=None):
        self.empresa_id = empresa_id
        self.http = http or httpx.Client(timeout=60)

    def _access(self, forcar=False):
        with SessionLocal() as s:
            emp = s.get(Empresa, self.empresa_id)
            if not emp or not emp.bling_refresh:
                raise BlingErro("Empresa não conectada ao Bling. Clique em Conectar ao Bling.")
            if forcar or not emp.bling_access or not emp.bling_expira or emp.bling_expira <= agora():
                cid, sec = credenciais(s, emp)
                try:
                    t = _token(cid, sec, {"grant_type": "refresh_token", "refresh_token": decifrar(emp.bling_refresh)}, self.http)
                except BlingErro as e:
                    raise BlingErro(f"A conexão com o Bling expirou. Conecte de novo a empresa. ({e})") from None
                guardar_tokens(emp, t)
                s.commit()
            return decifrar(emp.bling_access)

    def get(self, caminho: str, params=None):
        renovou = False
        for tentativa in range(6):
            with _trava:
                espera = INTERVALO - (time.monotonic() - _ultima[0])
                if espera > 0:
                    time.sleep(espera)
                _ultima[0] = time.monotonic()
            r = self.http.get(API + caminho, params=params,
                              headers={"Authorization": f"Bearer {self._access()}", "enable-jwt": "1", "Accept": "application/json"})
            if r.status_code == 401 and not renovou:
                self._access(forcar=True)
                renovou = True
                continue
            if r.status_code == 429 or r.status_code >= 500:
                time.sleep(min(2 ** tentativa, 20))
                continue
            if r.status_code == 404:
                return {"data": []}
            if r.status_code >= 400:
                try:
                    e = r.json().get("error", {})
                    msg = e.get("description") or e.get("message") or r.text
                except (ValueError, AttributeError):
                    msg = r.text
                raise BlingErro(f"Bling respondeu {r.status_code} em {caminho}: {str(msg)[:300]}")
            return r.json()
        raise BlingErro(f"O Bling não respondeu em {caminho} (limite de requisições). Tente de novo em alguns minutos.")

    def paginas(self, caminho, params=None, limite=100):
        pagina = 1
        while True:
            dados = self.get(caminho, {**(params or {}), "pagina": pagina, "limite": limite}).get("data") or []
            yield from dados
            if len(dados) < limite:
                return
            pagina += 1


# ------------------------------------------------------------------ conversões
def _data(v):
    if not v or str(v).startswith("0000"):
        return None
    try:
        return dt.date.fromisoformat(str(v)[:10])
    except ValueError:
        return None


TIPO_CATEGORIA = {1: "D", 2: "R", 3: "RD"}


def sincronizar_categorias(cli: Cliente, s, empresa_id: int) -> int:
    existentes = {c.codigo: c for c in s.scalars(select(ContaOrigem).where(
        ContaOrigem.empresa_id == empresa_id, ContaOrigem.origem == "BLING")).all()}
    vistos = set()
    for c in cli.paginas("/categorias/receitas-despesas", {"tipo": 0, "situacao": 0}):
        cod = str(c.get("id"))
        vistos.add(cod)
        x = existentes.get(cod)
        if not x:
            x = ContaOrigem(empresa_id=empresa_id, origem="BLING", codigo=cod)
            s.add(x)
            existentes[cod] = x
        x.descricao = (c.get("descricao") or "")[:300] or f"Categoria {cod}"
        x.tipo = TIPO_CATEGORIA.get(int(c.get("tipo") or 1), "D")
        pai = c.get("idCategoriaPai") or 0
        x.pai_codigo = str(pai) if pai else ""
        x.ativa = int(c.get("situacao", 1) or 1) == 1
        x.atualizado_em = agora()
    for cod, x in existentes.items():
        if cod not in vistos and not cod.startswith("_"):
            x.ativa = False
    s.commit()
    return len(vistos)


def _upsert_lista(s, empresa_id, tipo, item, cache):
    ide = int(item["id"])
    x = cache.get(ide)
    valor = round(float(item.get("valor") or 0), 2)
    sit = int(item.get("situacao") or 1)
    novo = x is None
    if novo:
        x = Lancamento(empresa_id=empresa_id, tipo=tipo, id_externo=ide)
        s.add(x)
        cache[ide] = x
    mudou = novo or float(x.valor or 0) != valor or x.situacao != sit
    x.valor, x.situacao = valor, sit
    x.vencimento = _data(item.get("vencimento")) or x.vencimento
    if item.get("dataEmissao"):
        x.data_emissao = _data(item["dataEmissao"])
    nome = (item.get("contato") or {}).get("nome")
    if nome:
        x.contato = nome[:200]
    if mudou:
        x.detalhado = False
    x.atualizado_em = agora()
    return x


def _detalhar(cli: Cliente, x: Lancamento):
    caminho = "/contas/pagar/" if x.tipo == "P" else "/contas/receber/"
    d = cli.get(caminho + str(x.id_externo)).get("data") or {}
    cat = (d.get("categoria") or {}).get("id")
    x.categoria_codigo = str(cat) if cat else ""
    x.data_emissao = _data(d.get("dataEmissao")) or x.data_emissao
    x.competencia = _data(d.get("competencia"))
    x.vencimento = _data(d.get("vencimento")) or x.vencimento
    x.saldo = round(float(d.get("saldo") or 0), 2)
    x.historico = (d.get("historico") or "")[:2000]
    x.documento = str(d.get("numeroDocumento") or "")[:60]
    contato = d.get("contato") or {}
    if contato.get("nome"):
        x.contato = contato["nome"][:200]
    if d.get("situacao"):
        x.situacao = int(d["situacao"])
    x.detalhado = True


def sincronizar(job_id: int, cli: Cliente | None = None):
    """Job de sincronização: categorias + contas a pagar e a receber emitidas no período."""
    with SessionLocal() as s:
        job = s.get(Job, job_id)
        import json
        p = json.loads(job.parametros or "{}")
        emp_id = job.empresa_id
        job.status, job.mensagem = "EXECUTANDO", "Lendo categorias do Bling..."
        s.commit()
    cli = cli or Cliente(emp_id)

    def andamento(msg, prog=None, total=None):
        with SessionLocal() as s2:
            j = s2.get(Job, job_id)
            j.mensagem = msg
            if prog is not None:
                j.progresso = prog
            if total is not None:
                j.total = total
            s2.commit()

    try:
        with SessionLocal() as s:
            ncat = sincronizar_categorias(cli, s, emp_id)
            ini, fim = p["inicio"], p["fim"]
            cache = {(x.tipo, x.id_externo): x for x in s.scalars(select(Lancamento).where(
                Lancamento.empresa_id == emp_id)).all()}
            cp = {k[1]: v for k, v in cache.items() if k[0] == "P"}
            cr = {k[1]: v for k, v in cache.items() if k[0] == "R"}
            andamento(f"{ncat} categorias. Lendo contas a pagar...")
            n_p = 0
            for it in cli.paginas("/contas/pagar", {"dataEmissaoInicial": ini, "dataEmissaoFinal": fim}):
                _upsert_lista(s, emp_id, "P", it, cp)
                n_p += 1
            s.commit()
            andamento(f"{ncat} categorias, {n_p} contas a pagar. Lendo contas a receber...")
            n_r = 0
            for it in cli.paginas("/contas/receber", {"tipoFiltroData": "E", "dataInicial": ini, "dataFinal": fim}):
                _upsert_lista(s, emp_id, "R", it, cr)
                n_r += 1
            s.commit()
            pend = [x for x in list(cp.values()) + list(cr.values()) if not x.detalhado]
            andamento(f"{n_p} a pagar e {n_r} a receber. Buscando a categoria de cada título...", 0, len(pend))
            for i, x in enumerate(pend, 1):
                _detalhar(cli, x)
                if i % 25 == 0:
                    s.commit()
                    andamento(f"Buscando a categoria de cada título ({i} de {len(pend)})...", i)
            emp = s.get(Empresa, emp_id)
            emp.bling_sync_em = agora()
            emp.bling_sync_periodo = f"{ini} a {fim}"
            j = s.get(Job, job_id)
            j.status, j.progresso, j.total, j.fim = "OK", len(pend), len(pend), agora()
            j.mensagem = (f"Bling atualizado: {ncat} categorias, {n_p} contas a pagar e {n_r} a receber "
                          f"emitidas de {_br_data(ini)} a {_br_data(fim)} ({len(pend)} detalhadas agora).")
            s.commit()
    except Exception as e:  # noqa: BLE001 — o erro vai para a tela
        with SessionLocal() as s:
            j = s.get(Job, job_id)
            j.status, j.fim, j.mensagem = "ERRO", agora(), str(e)[:1000]
            s.commit()


def _br_data(iso):
    return f"{iso[8:10]}/{iso[5:7]}/{iso[:4]}"
