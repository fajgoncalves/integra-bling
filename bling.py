"""Bling API v3 — OAuth 2.0 por empresa, leitura de categorias e dos lançamentos de Caixa e Bancos.

Somente leitura: o Integra Bling nunca grava nada no Bling.
Limite do Bling: poucas requisições por segundo; o cliente espera entre chamadas e repete em 429.
"""
import time
import datetime as dt
import threading

import httpx
from sqlalchemy import select

from db import SessionLocal, Empresa, ContaOrigem, Movimento, ContaFinanceira, Job, agora
from sqlalchemy import delete
from seguranca import cifrar, decifrar, get_config

AUTORIZAR = "https://www.bling.com.br/Api/v3/oauth/authorize"
TOKEN = "https://www.bling.com.br/Api/v3/oauth/token"
API = "https://api.bling.com.br/Api/v3"
INTERVALO = 0.4  # segundos entre chamadas (≈ 2,5 por segundo)
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


def _movimento(item, empresa_id, categoria=""):
    d = _data(item.get("data"))
    if not d:
        return None
    dc = str(item.get("debCred") or "").upper()[:1]
    if dc not in ("C", "D"):
        dc = "C" if str(item.get("tipoLancamento")) == "2" else "D"
    return Movimento(
        empresa_id=empresa_id, id_externo=int(item["id"]), data=d, deb_cred=dc,
        valor=round(abs(float(item.get("valor") or 0)), 2), categoria_codigo=categoria,
        conta_financeira=((item.get("contaFinanceira") or {}).get("descricao") or "")[:200],
        contato=((item.get("contato") or {}).get("nome") or "")[:200],
        descricao=(item.get("descricao") or item.get("observacoes") or "")[:2000],
        origem_tipo=str((item.get("origem") or {}).get("tipo") or "")[:20],
        conta_id=int((item.get("contaFinanceira") or {}).get("id") or 0) or None, categorizado=True)


HISTORICO_DESDE = dt.date(2010, 1, 1)  # saldos: soma de todos os lançamentos de cada conta desde o início


def sincronizar_contas_financeiras(cli, s, emp_id, vistas: dict):
    """Lista de contas de Caixa e Bancos (/contas-contabeis); sem o escopo, usa as contas vistas nos lançamentos."""
    contas = {c.id_externo: c for c in s.scalars(select(ContaFinanceira).where(ContaFinanceira.empresa_id == emp_id)).all()}
    try:
        lista = list(cli.paginas("/contas-contabeis"))
    except BlingErro:
        lista = []
    for it in lista:
        vistas[int(it["id"])] = (it.get("descricao") or "", (it.get("tipo") or ""), int(it.get("situacao") or 1) == 1)
    for i, (desc, tipo, ativa) in vistas.items():
        c = contas.get(i)
        if not c:
            c = ContaFinanceira(empresa_id=emp_id, id_externo=i, ordem=len(contas) + 1)
            s.add(c)
            contas[i] = c
        c.descricao = (desc or c.descricao or f"Conta {i}")[:200]
        c.tipo = str(tipo or c.tipo or "")[:30]
        c.ativa = ativa


def sincronizar_historico(cli, s, emp_id, antes_de: dt.date, andamento, vistas: dict):
    """Lançamentos (sem categoria) de todas as contas antes do período, para calcular o saldo inicial."""
    existentes = {m.id_externo: m for m in s.scalars(select(Movimento).where(Movimento.empresa_id == emp_id,
                                                                             Movimento.data < antes_de)).all()}
    vistos = set()
    ano = HISTORICO_DESDE.year
    while ano <= antes_de.year:
        ini = max(dt.date(ano, 1, 1), HISTORICO_DESDE)
        fim = min(dt.date(ano, 12, 31), antes_de - dt.timedelta(days=1))
        if ini <= fim:
            andamento(f"Lendo o histórico de caixa e bancos para os saldos ({ano})...")
            for it in cli.paginas("/caixas", {"dataInicial": ini.isoformat(), "dataFinal": fim.isoformat(), "situacao": "R",
                                              "situacaoConciliacao": 3}):
                novo = _movimento(it, emp_id)
                if not novo or novo.data >= antes_de:
                    continue
                cf = it.get("contaFinanceira") or {}
                if cf.get("id"):
                    vistas.setdefault(int(cf["id"]), (cf.get("descricao") or "", "", True))
                vistos.add(novo.id_externo)
                m = existentes.get(novo.id_externo)
                if m:
                    m.data, m.deb_cred, m.valor, m.conta_id = novo.data, novo.deb_cred, novo.valor, novo.conta_id
                    m.conta_financeira, m.contato, m.descricao = novo.conta_financeira, novo.contato, novo.descricao
                else:
                    novo.categorizado = False
                    s.add(novo)
            s.commit()
        ano += 1
    for i, m in existentes.items():
        if i not in vistos:
            s.delete(m)
    return len(vistos)


def classificar(todos: dict, por_categoria: dict, pais: dict):
    """todos: {id: item}; por_categoria: {cod: set(ids)}; pais: {cod: cod_pai}.
    Devolve ({id: cod}, ids_ambiguos). Quando o filtro do Bling devolve o mesmo lançamento em uma categoria-pai e
    numa filha, vale a mais específica (a filha); categorias sem parentesco são ambíguas e vão para a consulta individual."""
    def ancestrais(c):
        vistos = set()
        while c in pais and pais[c] and pais[c] not in vistos:
            c = pais[c]
            vistos.add(c)
        return vistos

    cands = {}
    for cod, ids in por_categoria.items():
        for i in ids:
            if i in todos:
                cands.setdefault(i, set()).add(cod)
    final, ambiguos = {}, []
    for i, cs in cands.items():
        folhas = {c for c in cs if not any(c in ancestrais(o) for o in cs if o != c)}
        if len(folhas) == 1:
            final[i] = folhas.pop()
        else:
            ambiguos.append(i)
    return final, ambiguos


def sincronizar(job_id: int, cli: Cliente | None = None):
    """Job: categorias + lançamentos de Caixa e Bancos (pagos/recebidos) do período, com a categoria de cada um."""
    import json
    with SessionLocal() as s:
        job = s.get(Job, job_id)
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
        ini, fim = p["inicio"], p["fim"]
        filtro = {"dataInicial": ini, "dataFinal": fim, "situacao": "R", "situacaoConciliacao": 3}
        with SessionLocal() as s:
            ncat = sincronizar_categorias(cli, s, emp_id)
            cats = s.scalars(select(ContaOrigem).where(ContaOrigem.empresa_id == emp_id, ContaOrigem.origem == "BLING")).all()
            pais = {c.codigo: c.pai_codigo for c in cats}
            codigos = [c.codigo for c in cats if not c.codigo.startswith("_")]
        andamento(f"{ncat} categorias. Lendo os lançamentos de Caixa e Bancos...")
        todos = {int(it["id"]): it for it in cli.paginas("/caixas", filtro)}
        por_cat = {}
        for n, cod in enumerate(codigos, 1):
            if n % 5 == 0:
                andamento(f"{len(todos)} lançamentos de caixa e bancos. Separando por categoria ({n} de {len(codigos)})...", n, len(codigos))
            ids = {int(it["id"]) for it in cli.paginas("/caixas", {**filtro, "idsCategorias[]": int(cod)})}
            if ids:
                por_cat[cod] = ids
        cat_de, ambiguos = classificar(todos, por_cat, pais)
        if ambiguos:
            andamento(f"Conferindo {len(ambiguos)} lançamento(s) um a um...", 0, len(ambiguos))
            for n, i in enumerate(ambiguos, 1):
                d = cli.get(f"/caixas/{i}").get("data") or {}
                cat = (d.get("categoria") or {}).get("id")
                cat_de[i] = str(cat) if cat else ""
                if n % 20 == 0:
                    andamento(f"Conferindo lançamentos um a um ({n} de {len(ambiguos)})...", n)
        with SessionLocal() as s:
            d_ini, d_fim = dt.date.fromisoformat(ini), dt.date.fromisoformat(fim)
            s.execute(delete(Movimento).where(Movimento.empresa_id == emp_id, Movimento.data >= d_ini, Movimento.data <= d_fim))
            s.flush()
            sem_cat = 0
            vistas = {}
            for i, it in todos.items():
                m = _movimento(it, emp_id, cat_de.get(i, ""))
                if not m or m.data < d_ini or m.data > d_fim:
                    continue
                cf = it.get("contaFinanceira") or {}
                if cf.get("id"):
                    vistas.setdefault(int(cf["id"]), (cf.get("descricao") or "", "", True))
                sem_cat += not m.categoria_codigo
                s.add(m)
            s.commit()
            n_hist = sincronizar_historico(cli, s, emp_id, d_ini, andamento, vistas)
            sincronizar_contas_financeiras(cli, s, emp_id, vistas)
            emp = s.get(Empresa, emp_id)
            emp.bling_sync_em = agora()
            emp.bling_sync_periodo = f"{ini} a {fim}"
            j = s.get(Job, job_id)
            j.status, j.fim = "OK", agora()
            j.mensagem = (f"Bling atualizado: {ncat} categorias e {len(todos)} lançamentos de caixa e bancos "
                          f"de {_br_data(ini)} a {_br_data(fim)}" + (f" ({sem_cat} sem categoria)" if sem_cat else "")
                          + f"; {n_hist} lançamentos anteriores lidos para os saldos.")
            s.commit()
    except Exception as e:  # noqa: BLE001 — o erro vai para a tela
        with SessionLocal() as s:
            j = s.get(Job, job_id)
            j.status, j.fim, j.mensagem = "ERRO", agora(), str(e)[:1000]
            s.commit()


def _br_data(iso):
    return f"{iso[8:10]}/{iso[5:7]}/{iso[:4]}"


def nota_por_chave(empresa_id: int, chave: str, numero: str, data, http=None):
    """Procura no Bling a NF-e de saída pela chave (ou número) em volta da data e devolve (xml, link do DANFE).
    Usado pelo módulo do Mercado Livre para mostrar o DANFE das notas emitidas pelo Bling."""
    import datetime as _dt
    cli = Cliente(empresa_id, http)
    d = data or _dt.date.today()
    d1, d2 = d - _dt.timedelta(days=5), d + _dt.timedelta(days=5)
    achada = None
    for pagina in range(1, 11):
        r = cli.get("/nfe", {"pagina": pagina, "limite": 100, "tipo": 1,
                             "dataEmissaoInicial": f"{d1.isoformat()} 00:00:00", "dataEmissaoFinal": f"{d2.isoformat()} 23:59:59"})
        lista = r.get("data") or []
        for x in lista:
            ch = "".join(c for c in str(x.get("chaveAcesso") or "") if c.isdigit())
            if (chave and ch == chave) or (not chave and numero and str(x.get("numero") or "").lstrip("0") == str(numero).lstrip("0")):
                achada = x
                break
        if achada or len(lista) < 100:
            break
    if not achada:
        return "", ""
    det = cli.get(f"/nfe/{achada['id']}").get("data") or {}
    link_xml, danfe = det.get("xml") or "", det.get("linkDanfe") or det.get("linkPDF") or ""
    xml = ""
    if link_xml:
        try:
            rx = cli.http.get(link_xml, follow_redirects=True, timeout=60)
            if rx.status_code < 400 and rx.text.lstrip().startswith("<"):
                xml = rx.text.strip()
        except httpx.HTTPError:
            pass
    return xml, danfe
