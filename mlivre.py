"""Mercado Livre — OAuth 2.0 por empresa, leitura de pedidos (com itens e frete) e do faturamento (tarifas e bonificações).

Módulo separado: os dados do Mercado Livre têm DRE e análises próprias (mlanalise.py) e não entram na DRE principal
nem na associação de contas. Somente leitura. Um aplicativo (Configurações) serve a todas as empresas; cada empresa
autoriza com a própria conta de vendedor."""
import time
import json
import datetime as dt
import threading

import httpx
from sqlalchemy import select, delete

from db import SessionLocal, Empresa, MLVenda, MLItem, MLTarifa, Job, agora, FUSO
from seguranca import cifrar, decifrar, get_config

AUTORIZAR = "https://auth.mercadolivre.com.br/authorization"
API = "https://api.mercadolibre.com"
TOKEN = API + "/oauth/token"
INTERVALO = 0.12
_trava = threading.Lock()
_ultima = [0.0]

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


def venda(o: dict, empresa_id: int):
    """(MLVenda, [MLItem]) ou (None, [])."""
    d = _data_br(o.get("date_closed") or o.get("date_created"))
    if not d or not o.get("id"):
        return None, []
    itens = o.get("order_items") or []
    pagos = [p for p in o.get("payments") or [] if p.get("status") in PAGO]
    pago = sum(_num(p.get("transaction_amount")) for p in pagos)
    devolvido = sum(_num(p.get("transaction_amount_refunded")) for p in pagos)
    titulos = [str((it.get("item") or {}).get("title") or "") for it in itens]
    lin = []
    for it in itens:
        item = it.get("item") or {}
        lin.append(MLItem(empresa_id=empresa_id, order_id=int(o["id"]), anuncio=str(item.get("id") or "")[:30],
                          sku=str(item.get("seller_sku") or item.get("seller_custom_field") or "").strip()[:80],
                          titulo=str(item.get("title") or "")[:300], tipo_anuncio=str(it.get("listing_type_id") or "")[:30],
                          qtd=_num(it.get("quantity") or 1), preco=round(_num(it.get("unit_price")), 2),
                          tarifa=round(_num(it.get("sale_fee")), 2)))
    return MLVenda(empresa_id=empresa_id, order_id=int(o["id"]), pack_id=int(o["pack_id"]) if o.get("pack_id") else None,
                   data=d, status=str(o.get("status") or "")[:30], total=round(_num(o.get("total_amount")), 2),
                   pago=round(pago, 2), devolvido=round(devolvido, 2),
                   comissao=round(sum(_num(it.get("sale_fee")) * _num(it.get("quantity") or 1) for it in itens), 2),
                   itens=int(sum(_num(it.get("quantity")) for it in itens)),
                   descricao=(" + ".join(t for t in titulos if t))[:300],
                   comprador=str((o.get("buyer") or {}).get("nickname") or "")[:120],
                   shipping_id=int((o.get("shipping") or {}).get("id")) if (o.get("shipping") or {}).get("id") else None), lin


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


def frete_vendedor(cli: Cliente, shipping_id: int, seller: int):
    """Quanto o vendedor pagou pelo envio (custo do Mercado Envios para ele); None se não der para ler."""
    r = cli.get(f"/shipments/{shipping_id}/costs", opcional=True)
    if not r:
        return None
    total = 0.0
    for x in r.get("senders") or []:
        if not x.get("user_id") or int(x["user_id"]) == int(seller):
            total += _num(x.get("cost"))
    return round(total, 2)


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
        vendas, itens = {}, {}
        for n, mes in enumerate(meses, 1):
            andamento(f"Lendo pedidos de {mes[5:]}/{mes[:4]} ({len(vendas)} até agora)...", n, len(meses))
            for o in pedidos(cli, seller, dt.date.fromisoformat(mes + "-01"), _fim_mes(mes)):
                v, lin = venda(o, emp_id)
                if v and d1 <= v.data <= d2:
                    vendas[v.order_id] = v
                    itens[v.order_id] = lin
        if p.get("frete", True):
            por_envio = {}
            for v in vendas.values():
                if v.shipping_id and v.pago:
                    por_envio.setdefault(v.shipping_id, []).append(v)
            for n, (sid, vs) in enumerate(por_envio.items(), 1):
                if n % 25 == 1:
                    andamento(f"Lendo o frete de cada envio ({n} de {len(por_envio)})...", n, len(por_envio))
                f = frete_vendedor(cli, sid, seller)
                if f is not None:  # envio de um pacote com vários pedidos: rateia pelo valor
                    base = sum(float(x.pago or 0) for x in vs) or len(vs)
                    for x in vs:
                        x.frete = round(f * (float(x.pago or 0) or 1) / base, 2)
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
            antigos = select(MLVenda.order_id).where(MLVenda.empresa_id == emp_id, MLVenda.data >= d1, MLVenda.data <= d2)
            s.execute(delete(MLItem).where(MLItem.empresa_id == emp_id, MLItem.order_id.in_(antigos)))
            ids = list(vendas)
            for k in range(0, len(ids), 5000):  # pedidos que mudaram de mês (data de fechamento) também são substituídos
                lote = ids[k:k + 5000]
                s.execute(delete(MLItem).where(MLItem.empresa_id == emp_id, MLItem.order_id.in_(lote)))
                s.execute(delete(MLVenda).where(MLVenda.empresa_id == emp_id, MLVenda.order_id.in_(lote)))
            s.execute(delete(MLVenda).where(MLVenda.empresa_id == emp_id, MLVenda.data >= d1, MLVenda.data <= d2))
            s.execute(delete(MLTarifa).where(MLTarifa.empresa_id == emp_id, MLTarifa.periodo >= ini, MLTarifa.periodo <= fim))
            s.flush()
            s.add_all(vendas.values())
            for lin in itens.values():
                s.add_all(lin)
            s.add_all(tarifas)
            s.flush()
            emp = s.get(Empresa, emp_id)
            emp.ml_user_id = seller
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
