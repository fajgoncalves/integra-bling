"""Mercado Livre — OAuth 2.0 por empresa, leitura de pedidos (vendas) e do faturamento (tarifas e bonificações).

Somente leitura. Um único aplicativo (Client ID / Secret Key nas Configurações) serve a todas as empresas;
cada empresa autoriza com a própria conta de vendedor.

O que vai para a DRE (contas de origem "MLIVRE"):
  - VENDAS   pedidos pagos no mês (valor dos produtos, sem o frete pago pelo comprador)
  - DEVOL    valores devolvidos ao comprador (reembolsos) dos pedidos do mês
  - ML:CH:<tipo> / MP:CH:<tipo>  encargos do faturamento do mês (comissão CV, Mercado Envios CXD, Product Ads PADS...)
  - ML:BO:<tipo> / MP:BO:<tipo>  bonificações do faturamento
"""
import time
import json
import datetime as dt
import threading

import httpx
from sqlalchemy import select, delete

from db import SessionLocal, Empresa, ContaOrigem, LinhaDRE, MLVenda, MLTarifa, Job, agora, FUSO
from seguranca import cifrar, decifrar, get_config

AUTORIZAR = "https://auth.mercadolivre.com.br/authorization"
API = "https://api.mercadolibre.com"
TOKEN = API + "/oauth/token"
INTERVALO = 0.12
_trava = threading.Lock()
_ultima = [0.0]

FIXAS = [("VENDAS", "Mercado Livre · Vendas (pedidos pagos)", "R"),
         ("DEVOL", "Mercado Livre · Reembolsos ao comprador", "D")]
# sugestão de linha do modelo "DRE Mercado Livre" por tipo de encargo
SUGESTAO_TIPO = {"CV": "ML_COMISSAO", "CXD": "ML_FRETE", "PADS": "ML_ADS"}
PAGO = {"approved", "refunded", "charged_back", "partially_refunded"}


class MLErro(Exception):
    pass


def credenciais(s):
    return get_config(s, "ml_client_id"), decifrar(get_config(s, "ml_client_secret"))


def url_autorizacao(client_id, redirect_uri, state):
    return str(httpx.URL(AUTORIZAR, params={"response_type": "code", "client_id": client_id,
                                            "redirect_uri": redirect_uri, "state": state}))


def _token(dados, http=None):
    http = http or httpx.Client(timeout=30)
    r = http.post(TOKEN, data=dados, headers={"Accept": "application/json",
                                              "Content-Type": "application/x-www-form-urlencoded"})
    if r.status_code >= 400:
        try:
            j = r.json()
            msg = j.get("message") or j.get("error_description") or j.get("error") or r.text
        except ValueError:
            msg = r.text
        raise MLErro(f"Mercado Livre recusou a autorização ({r.status_code}): {str(msg)[:300]}")
    return r.json()


def guardar_tokens(emp: Empresa, t: dict):
    emp.ml_access = cifrar(t["access_token"])
    if t.get("refresh_token"):  # o refresh token do ML é de uso único: guarda sempre o novo
        emp.ml_refresh = cifrar(t["refresh_token"])
    emp.ml_expira = agora() + dt.timedelta(seconds=int(t.get("expires_in") or 10800) - 120)
    if t.get("user_id"):
        emp.ml_user_id = int(t["user_id"])


def trocar_codigo(s, emp: Empresa, code: str, redirect_uri: str, http=None):
    cid, sec = credenciais(s)
    if not cid or not sec:
        raise MLErro("Cadastre o Client ID e a Secret Key do aplicativo do Mercado Livre em Configurações.")
    guardar_tokens(emp, _token({"grant_type": "authorization_code", "client_id": cid, "client_secret": sec,
                                "code": code, "redirect_uri": redirect_uri}, http))
    emp.ml_conectado_em = agora()
    s.flush()
    try:
        me = Cliente(emp.id, http, sessao=s).get("/users/me")
        emp.ml_apelido = str(me.get("nickname") or "")[:120]
        emp.ml_user_id = int(me.get("id") or emp.ml_user_id or 0) or None
    except MLErro:
        pass


class Cliente:
    def __init__(self, empresa_id: int, http=None, sessao=None):
        self.empresa_id = empresa_id
        self.http = http or httpx.Client(timeout=60)
        self.sessao = sessao  # usada só na conexão (antes do commit)

    def _access(self, forcar=False):
        s = self.sessao or SessionLocal()
        try:
            emp = s.get(Empresa, self.empresa_id)
            if not emp or not emp.ml_refresh:
                raise MLErro("Empresa não conectada ao Mercado Livre. Clique em Conectar Mercado Livre.")
            if forcar or not emp.ml_access or not emp.ml_expira or emp.ml_expira <= agora():
                cid, sec = credenciais(s)
                try:
                    t = _token({"grant_type": "refresh_token", "client_id": cid, "client_secret": sec,
                                "refresh_token": decifrar(emp.ml_refresh)}, self.http)
                except MLErro as e:
                    raise MLErro(f"A conexão com o Mercado Livre expirou. Conecte de novo a empresa. ({e})") from None
                guardar_tokens(emp, t)
                if self.sessao is None:
                    s.commit()
                else:
                    s.flush()
            return decifrar(emp.ml_access)
        finally:
            if self.sessao is None:
                s.close()

    def get(self, caminho, params=None, opcional=False):
        renovou = False
        for tentativa in range(6):
            with _trava:
                espera = INTERVALO - (time.monotonic() - _ultima[0])
                if espera > 0:
                    time.sleep(espera)
                _ultima[0] = time.monotonic()
            r = self.http.get(API + caminho, params=params, headers={"Authorization": f"Bearer {self._access()}",
                                                                      "Accept": "application/json"})
            if r.status_code == 401 and not renovou:
                self._access(forcar=True)
                renovou = True
                continue
            if r.status_code == 429 or r.status_code >= 500:
                time.sleep(min(2 ** tentativa, 20))
                continue
            if r.status_code in (403, 404) and opcional:
                return None
            if r.status_code >= 400:
                try:
                    msg = r.json().get("message") or r.text
                except ValueError:
                    msg = r.text
                raise MLErro(f"Mercado Livre respondeu {r.status_code} em {caminho}: {str(msg)[:300]}")
            return r.json()
        raise MLErro(f"O Mercado Livre não respondeu em {caminho}. Tente de novo em alguns minutos.")


# ------------------------------------------------------------------ conversões
def _data_br(v):
    """'2026-09-01T23:30:00.000-04:00' -> data no fuso de Brasília."""
    if not v:
        return None
    try:
        d = dt.datetime.fromisoformat(str(v).replace("Z", "+00:00"))
    except ValueError:
        return None
    return (d.astimezone(FUSO) if d.tzinfo else d).date()


def _num(v):
    try:
        return float(v or 0)
    except (TypeError, ValueError):
        return 0.0


def venda(o: dict, empresa_id: int) -> MLVenda | None:
    d = _data_br(o.get("date_closed") or o.get("date_created"))
    if not d or not o.get("id"):
        return None
    itens = o.get("order_items") or []
    pagos = [p for p in o.get("payments") or [] if p.get("status") in PAGO]
    pago = sum(_num(p.get("transaction_amount")) for p in pagos)
    devolvido = sum(_num(p.get("transaction_amount_refunded")) for p in pagos)
    titulos = [str((it.get("item") or {}).get("title") or "") for it in itens]
    return MLVenda(empresa_id=empresa_id, order_id=int(o["id"]), pack_id=int(o["pack_id"]) if o.get("pack_id") else None,
                   data=d, status=str(o.get("status") or "")[:30], total=round(_num(o.get("total_amount")), 2),
                   pago=round(pago, 2), devolvido=round(devolvido, 2),
                   comissao=round(sum(_num(it.get("sale_fee")) * _num(it.get("quantity") or 1) for it in itens), 2),
                   itens=int(sum(_num(it.get("quantity")) for it in itens)),
                   descricao=(" + ".join(t for t in titulos if t))[:300],
                   comprador=str((o.get("buyer") or {}).get("nickname") or "")[:120],
                   shipping_id=int((o.get("shipping") or {}).get("id")) if (o.get("shipping") or {}).get("id") else None)


def meses_entre(ini, fim):
    out, (a, m) = [], map(int, ini.split("-"))
    while f"{a:04d}-{m:02d}" <= fim:
        out.append(f"{a:04d}-{m:02d}")
        a, m = (a + 1, 1) if m == 12 else (a, m + 1)
    return out


def _fim_mes(mes):
    a, m = map(int, mes.split("-"))
    return (dt.date(a + (m == 12), 1 if m == 12 else m + 1, 1) - dt.timedelta(days=1))


def _janela(d1: dt.date, d2: dt.date):
    return d1.isoformat() + "T00:00:00.000-03:00", d2.isoformat() + "T23:59:59.999-03:00"


def pedidos(cli: Cliente, seller: int, d1: dt.date, d2: dt.date):
    """Pedidos fechados entre d1 e d2. Se passar do limite de paginação, divide o período ao meio."""
    de, ate = _janela(d1, d2)
    base = {"seller": seller, "order.date_closed.from": de, "order.date_closed.to": ate, "sort": "date_asc"}
    primeira = cli.get("/orders/search", {**base, "offset": 0, "limit": 50})
    total = int((primeira.get("paging") or {}).get("total") or 0)
    if total > 9500 and d1 < d2:
        meio = d1 + (d2 - d1) // 2
        yield from pedidos(cli, seller, d1, meio)
        yield from pedidos(cli, seller, meio + dt.timedelta(days=1), d2)
        return
    yield from primeira.get("results") or []
    off = 50
    while off < total:
        yield from cli.get("/orders/search", {**base, "offset": off, "limit": 50}).get("results") or []
        off += 50


def faturamento(cli: Cliente, mes: str):
    """[(grupo, natureza, tipo, rótulo, valor, de, até)] do resumo do período de faturamento do mês (ML e MP)."""
    out = []
    for grupo in ("ML", "MP"):
        r = cli.get(f"/billing/integration/periods/key/{mes}-01/summary/details",
                    {"group": grupo, "document_type": "BILL"}, opcional=True)
        if not r:
            continue
        inc = r.get("bill_includes") or {}
        per = r.get("period") or {}
        de, ate = _data_br(per.get("date_from")), _data_br(per.get("date_to"))
        for natureza, chave in (("CHARGE", "charges"), ("BONUS", "bonuses")):
            for x in inc.get(chave) or []:
                v = _num(x.get("amount"))
                if v:
                    out.append((grupo, natureza, str(x.get("type") or "OUTRO")[:20],
                                str(x.get("label") or x.get("type") or "")[:200], v, de, ate))
    return out


def codigo_tarifa(grupo, natureza, tipo):
    return f"{grupo}:{'CH' if natureza == 'CHARGE' else 'BO'}:{tipo}"


def garantir_contas(s, empresa_id: int):
    tem = {c.codigo: c for c in s.scalars(select(ContaOrigem).where(ContaOrigem.empresa_id == empresa_id,
                                                                    ContaOrigem.origem == "MLIVRE")).all()}
    for cod, desc, tipo in FIXAS:
        if cod not in tem:
            s.add(ContaOrigem(empresa_id=empresa_id, origem="MLIVRE", codigo=cod, descricao=desc, tipo=tipo))
    for g, nat, tipo, rot in s.execute(select(MLTarifa.grupo, MLTarifa.natureza, MLTarifa.tipo, MLTarifa.rotulo)
                                       .where(MLTarifa.empresa_id == empresa_id).distinct()).all():
        cod = codigo_tarifa(g, nat, tipo)
        if cod in tem:
            continue
        nome = "Mercado Livre" if g == "ML" else "Mercado Pago"
        c = ContaOrigem(empresa_id=empresa_id, origem="MLIVRE", codigo=cod, descricao=f"{nome} · {rot or tipo}",
                        tipo="D" if nat == "CHARGE" else "R")
        s.add(c)
        tem[cod] = c


def sugestao(codigo: str):
    if codigo == "VENDAS":
        return "RB_ML"
    if codigo == "DEVOL":
        return "ML_DEVOL"
    partes = codigo.split(":")
    if len(partes) == 3:
        return "ML_BONUS" if partes[1] == "BO" else SUGESTAO_TIPO.get(partes[2], "ML_TARIFAS")
    return None


def aplicar_sugestoes(s, e: Empresa):
    garantir_contas(s, e.id)
    s.flush()
    if not e.modelo_id:
        return
    linhas = {ln.codigo: ln.id for ln in s.scalars(select(LinhaDRE).where(LinhaDRE.modelo_id == e.modelo_id,
                                                                          LinhaDRE.tipo == "CONTA")).all()}
    for c in s.scalars(select(ContaOrigem).where(ContaOrigem.empresa_id == e.id, ContaOrigem.origem == "MLIVRE")).all():
        sug = sugestao(c.codigo)
        if not c.linha_id and sug in linhas:
            c.linha_id = linhas[sug]


def valores(s, empresa_id: int, ini: str, fim: str):
    """{codigo: {mes: valor com sinal}} — vendas e bonificações +, reembolsos e encargos −."""
    from collections import defaultdict
    out = defaultdict(lambda: defaultdict(float))
    d1, d2 = dt.date.fromisoformat(ini + "-01"), _fim_mes(fim)
    for v in s.scalars(select(MLVenda).where(MLVenda.empresa_id == empresa_id, MLVenda.data >= d1, MLVenda.data <= d2)).all():
        m = v.data.strftime("%Y-%m")
        out["VENDAS"][m] += float(v.pago or 0)
        out["DEVOL"][m] -= float(v.devolvido or 0)
    for t in s.scalars(select(MLTarifa).where(MLTarifa.empresa_id == empresa_id, MLTarifa.periodo >= ini,
                                              MLTarifa.periodo <= fim)).all():
        sinal = -1 if t.natureza == "CHARGE" else 1
        out[codigo_tarifa(t.grupo, t.natureza, t.tipo)][t.periodo] += sinal * abs(float(t.valor or 0))
    return out


# ------------------------------------------------------------------ job
def sincronizar(job_id: int, cli: Cliente | None = None):
    with SessionLocal() as s:
        job = s.get(Job, job_id)
        p = json.loads(job.parametros or "{}")
        emp_id = job.empresa_id
        job.status, job.mensagem = "EXECUTANDO", "Lendo os pedidos do Mercado Livre..."
        seller = s.get(Empresa, emp_id).ml_user_id
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
        if not seller:
            seller = int(cli.get("/users/me").get("id"))
        meses = meses_entre(ini, fim)
        d1, d2 = dt.date.fromisoformat(ini + "-01"), _fim_mes(fim)
        vendas = {}
        for n, mes in enumerate(meses, 1):
            andamento(f"Lendo pedidos de {mes[5:]}/{mes[:4]} ({len(vendas)} até agora)...", n, len(meses))
            for o in pedidos(cli, seller, dt.date.fromisoformat(mes + "-01"), _fim_mes(mes)):
                v = venda(o, emp_id)
                if v and d1 <= v.data <= d2:
                    vendas[v.order_id] = v
        tarifas = []
        for n, mes in enumerate(meses, 1):
            andamento(f"Lendo o faturamento (tarifas) de {mes[5:]}/{mes[:4]}...", n, len(meses))
            agg = {}
            for g, nat, tipo, rot, val, de, ate in faturamento(cli, mes):
                k = (g, nat, tipo, rot)
                x = agg.setdefault(k, [0.0, de, ate])
                x[0] += val
            tarifas += [MLTarifa(empresa_id=emp_id, periodo=mes, grupo=g, natureza=nat, tipo=tipo, rotulo=rot,
                                 valor=round(v, 2), de=de, ate=ate) for (g, nat, tipo, rot), (v, de, ate) in agg.items()]
        with SessionLocal() as s:
            s.execute(delete(MLVenda).where(MLVenda.empresa_id == emp_id, MLVenda.data >= d1, MLVenda.data <= d2))
            s.execute(delete(MLTarifa).where(MLTarifa.empresa_id == emp_id, MLTarifa.periodo >= ini, MLTarifa.periodo <= fim))
            s.flush()
            s.add_all(vendas.values())
            s.add_all(tarifas)
            s.flush()
            emp = s.get(Empresa, emp_id)
            emp.ml_user_id = seller
            aplicar_sugestoes(s, emp)
            emp.ml_sync_em = agora()
            emp.ml_sync_periodo = f"{ini} a {fim}"
            j = s.get(Job, job_id)
            pagos = sum(1 for v in vendas.values() if v.pago)
            j.status, j.fim = "OK", agora()
            j.mensagem = (f"Mercado Livre atualizado: {len(vendas)} pedidos ({pagos} pagos) e {len(tarifas)} "
                          f"tarifas/bonificações do faturamento, de {ini[5:]}/{ini[:4]} a {fim[5:]}/{fim[:4]}.")
            s.commit()
    except Exception as e:  # noqa: BLE001
        with SessionLocal() as s:
            j = s.get(Job, job_id)
            j.status, j.fim, j.mensagem = "ERRO", agora(), str(e)[:1000]
            s.commit()
