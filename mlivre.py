"""Mercado Livre — OAuth 2.0 por empresa, leitura de pedidos (com itens e frete) e do faturamento (tarifas e bonificações).

Módulo separado: os dados do Mercado Livre têm DRE e análises próprias (mlanalise.py) e não entram na DRE principal
nem na associação de contas. Somente leitura. Um aplicativo (Configurações) serve a todas as empresas; cada empresa
autoriza com a própria conta de vendedor."""
import re
import io
import csv
import time
import json
import unicodedata
import datetime as dt
import threading

import httpx
from sqlalchemy import select, delete

from db import SessionLocal, Empresa, MLVenda, MLItem, MLTarifa, MLNota, MLCobranca, MLExtrato, Job, agora, FUSO
from seguranca import cifrar, decifrar, get_config

AUTORIZAR = "https://auth.mercadolivre.com.br/authorization"
API = "https://api.mercadolibre.com"
TOKEN = API + "/oauth/token"
INTERVALO = 0.12
_trava = threading.Lock()
_ultima = [0.0]
dormir = time.sleep  # espera entre novas tentativas (trocada nos testes)

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

    def req(self, metodo, url, params=None, corpo=None, opcional=False, texto=False):
        """Chamada autenticada. url pode ser um caminho da API do ML ou um endereço completo (Mercado Pago)."""
        alvo = url if url.startswith("http") else API + url
        renovou, ultimo = False, ""
        for tentativa in range(6):
            with _trava:
                espera = INTERVALO - (time.monotonic() - _ultima[0])
                if espera > 0:
                    time.sleep(espera)
                _ultima[0] = time.monotonic()
            r = self.http.request(metodo, alvo, params=params, json=corpo,
                                  headers={"Authorization": f"Bearer {self._access()}", "Accept": "application/json"})
            if r.status_code == 401 and not renovou:
                self._access(forcar=True)
                renovou = True
                continue
            if r.status_code == 429 or r.status_code >= 500:
                ultimo = f"{r.status_code} {r.text[:200]}"
                dormir(min(3 * 2 ** tentativa, 60) if r.status_code == 429 else min(2 ** tentativa, 20))
                continue
            if r.status_code in (400, 403, 404) and opcional:
                return None
            if r.status_code >= 400:
                try:
                    msg = r.json().get("message") or r.text
                except ValueError:
                    msg = r.text
                raise MLErro(f"Mercado Livre respondeu {r.status_code} em {url.split('?')[0]}: {str(msg)[:300]}")
            if texto:
                return r.text
            try:
                return r.json() if r.content else {}
            except ValueError:
                return {}
        raise MLErro(f"O Mercado Livre não respondeu em {url.split('?')[0]} (última resposta: {ultimo}). "
                     "Tente de novo em alguns minutos.")

    def get(self, caminho, params=None, opcional=False):
        return self.req("GET", caminho, params, opcional=opcional)


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
                          tarifa=round(_num(it.get("sale_fee")), 2), categoria=str(item.get("category_id") or "")[:30]))
    return MLVenda(empresa_id=empresa_id, order_id=int(o["id"]), pack_id=_int(o.get("pack_id")),
                   data=d, status=str(o.get("status") or "")[:30], total=round(_num(o.get("total_amount")), 2),
                   pago=round(pago, 2), devolvido=round(devolvido, 2),
                   comissao=round(sum(_num(it.get("sale_fee")) * _num(it.get("quantity") or 1) for it in itens), 2),
                   itens=int(sum(_num(it.get("quantity")) for it in itens)),
                   descricao=(" + ".join(t for t in titulos if t))[:300],
                   comprador=str((o.get("buyer") or {}).get("nickname") or "")[:120],
                   pagamentos=",".join(str(p_["id"]) for p_ in o.get("payments") or [] if p_.get("id"))[:300],
                   shipping_id=_int((o.get("shipping") or {}).get("id"))), lin


def link_venda(order_id, pack_id=None):
    """Página da venda no painel do vendedor do Mercado Livre (vendas de carrinho abrem pelo pacote)."""
    return f"https://www.mercadolivre.com.br/vendas/{pack_id or order_id}/detalhe"


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
        if not _int(x.get("user_id")) or _int(x.get("user_id")) == int(seller):
            total += _num(x.get("cost"))
    return round(total, 2)


# ------------------------------------------------------------------ notas fiscais das vendas
CHAVE = re.compile(r"(?<!\d)(\d{44})(?!\d)")


def _procura(d, nomes):
    """Primeiro valor de uma das chaves (em qualquer nível do JSON)."""
    if isinstance(d, dict):
        for k, v in d.items():
            if k in nomes and v not in (None, "", []):
                return v
        for v in d.values():
            r = _procura(v, nomes)
            if r not in (None, "", []):
                return r
    elif isinstance(d, list):
        for v in d:
            r = _procura(v, nomes)
            if r not in (None, "", []):
                return r
    return None


def _nota(d, origem, empresa_id, order_id):
    if not d or not isinstance(d, (dict, list)):
        return None
    if isinstance(d, list):
        d = d[0] if d else None
        if not d:
            return None
    chave = str(_procura(d, {"invoice_key", "fiscal_key", "key", "access_key", "chave"}) or "")
    if not CHAVE.fullmatch(chave):
        m = CHAVE.search(json.dumps(d))
        chave = m.group(1) if m else ""
    numero = str(_procura(d, {"invoice_number", "number", "numero"}) or "")[:20]
    if not chave and not numero:
        return None
    tipo = str(_procura(d, {"invoice_type", "transaction_type"}) or "venda").lower()
    return MLNota(empresa_id=empresa_id, order_id=order_id, origem=origem,
                  tipo="devolucao" if "devol" in tipo or "return" in tipo else "venda", numero=numero,
                  serie=str(_procura(d, {"invoice_series", "series", "serie"}) or "")[:10], chave=chave,
                  status=str(_procura(d, {"status"}) or "")[:30],
                  data=_data_br(_procura(d, {"issued_date", "authorized_date", "invoice_date", "date_created", "emission_date"})),
                  valor=_num(_procura(d, {"amount", "total_amount", "invoice_amount", "total"})) or None,
                  invoice_id=_int(d.get("id")) if origem == "EMISSOR_ML" and isinstance(d, dict) else None)


def _lista(d):
    if isinstance(d, list):
        return d
    if isinstance(d, dict):
        for k in ("results", "invoices", "data"):
            if isinstance(d.get(k), list):
                return d[k]
        return [d]
    return []


def notas_da_venda(cli: Cliente, seller: int, v: MLVenda):
    """Notas da venda: emitidas pelo emissor do ML (venda e, se houver, devolução); se não houver, a nota informada no envio
    (ERP como o Bling). Para vendas com reembolso, procura também a nota do envio de devolução (reclamação → devolução)."""
    out, chaves = [], set()

    def junta(dados, origem, tipo=None):
        for d in _lista(dados):
            n = _nota(d, origem, v.empresa_id, v.order_id)
            if n and (n.chave or n.numero) not in chaves:
                if tipo:
                    n.tipo = tipo
                chaves.add(n.chave or n.numero)
                out.append(n)

    junta(cli.get(f"/users/{seller}/invoices/orders/{v.order_id}", opcional=True), "EMISSOR_ML")
    if not out and v.shipping_id:
        junta(cli.get(f"/shipments/{v.shipping_id}/invoice_data", {"siteId": "MLB"}, opcional=True), "ENVIO")
    if float(v.devolvido or 0) > 0:
        try:
            for envio in envios_de_devolucao(cli, v.order_id):
                junta(cli.get(f"/users/{seller}/invoices/shipments/{envio}", opcional=True), "EMISSOR_ML", "devolucao")
        except MLErro:
            pass  # sem acesso às reclamações: a conferência da devolução fica pela Domínio
    return out


def xml_da_nota(cli: Cliente, seller: int, n: MLNota):
    """XML autorizado da nota emitida pelo emissor do ML (pela id da nota; se faltar, pelo pedido)."""
    alvos = []
    if n.invoice_id:
        alvos.append(f"/users/{seller}/invoices/documents/xml/{n.invoice_id}/authorized")
    alvos.append(f"/users/{seller}/invoices/orders/{n.order_id}")
    for url in alvos:
        txt = cli.req("GET", url, opcional=True, texto=True)
        if not txt:
            continue
        t = txt.lstrip("\ufeff").strip()
        if t.startswith("<"):
            return t
        try:
            d = json.loads(t)
        except ValueError:
            continue
        loc = _procura(d, {"xml_location"})
        if loc:
            x = cli.req("GET", loc if str(loc).startswith("http") else str(loc), opcional=True, texto=True)
            if x and x.lstrip().startswith("<"):
                return x.strip()
        if not n.invoice_id and _int(d.get("id") if isinstance(d, dict) else None):
            n.invoice_id = _int(d.get("id"))
            x = cli.req("GET", f"/users/{seller}/invoices/documents/xml/{n.invoice_id}/authorized", opcional=True, texto=True)
            if x and x.lstrip().startswith("<"):
                return x.strip()
    return ""


def envios_de_devolucao(cli: Cliente, order_id: int):
    """Envios de devolução das reclamações do pedido (pós-venda)."""
    envios = []
    busca = cli.get("/post-purchase/v1/claims/search", {"resource_id": order_id, "resource": "order"}, opcional=True) or {}
    for cl in (busca.get("data") or busca.get("results") or []):
        cid = _int(cl.get("id"))
        if not cid:
            continue
        dev = cli.get(f"/post-purchase/v2/claims/{cid}/returns", opcional=True) or {}
        for sh in _lista(dev.get("shipments") if isinstance(dev, dict) and dev.get("shipments") is not None else dev):
            sid = _int((sh or {}).get("shipment_id") or (sh or {}).get("id"))
            if sid:
                envios.append(sid)
    return envios


# ------------------------------------------------------------------ faturamento detalhado (cobrança por pedido)
def cobrancas(cli: Cliente, mes: str, empresa_id: int, andamento=None, avisos=None):
    """Detalhe do faturamento (cada cobrança/bonificação com o pedido). Paginação por from_id/last_id, como pede o ML.
    Uma falha num grupo/documento vira aviso e não derruba a atualização."""
    out, vistos = [], set()
    for grupo in ("ML", "MP"):
        for doc in ("BILL", "CREDIT_NOTE"):
            from_id, lidos = 0, 0
            try:
                while True:
                    r = cli.get(f"/billing/integration/periods/key/{mes}-01/group/{grupo}/details",
                                {"document_type": doc, "limit": 1000, "from_id": from_id}, opcional=True)
                    if not r:
                        break
                    res = r.get("results") or []
                    for x in res:
                        ci = x.get("charge_info") or {}
                        did = _int(ci.get("detail_id"))
                        if not did or (grupo, did) in vistos:
                            continue
                        vistos.add((grupo, did))
                        nat = "BONUS" if "BONUS" in str(ci.get("detail_type") or "").upper() else "CHARGE"
                        v = abs(_num(ci.get("detail_amount")))
                        if doc == "CREDIT_NOTE":
                            v = v if nat == "CHARGE" else -v  # estorno de cobrança (+) / anulação de bonificação (−)
                        else:
                            v = -v if nat == "CHARGE" else v
                        vendas_ = x.get("sales_info") or []
                        oid = (vendas_[0] or {}).get("order_id") if vendas_ else None
                        sid = (x.get("shipping_info") or {}).get("shipping_id")
                        out.append(MLCobranca(empresa_id=empresa_id, periodo=mes, grupo=grupo, documento=doc, detail_id=did,
                                              natureza=nat, tipo=str(ci.get("detail_sub_type") or "")[:20],
                                              descricao=str(ci.get("transaction_detail") or "")[:200], valor=round(v, 2),
                                              data=_data_br(ci.get("creation_date_time")),
                                              order_id=_int(oid), shipping_id=_int(sid),
                                              debitada=(str(ci.get("debited_from_operation") or "").upper() in ("YES", "SI", "SIM", "TRUE"))
                                              if ci.get("debited_from_operation") is not None else None))
                    lidos += len(res)
                    prox = _int(r.get("last_id")) or (max(_int((x.get("charge_info") or {}).get("detail_id")) or 0 for x in res) if res else None)
                    if not res or len(res) < 1000 or not prox or prox == from_id:
                        break
                    from_id = prox
                    if andamento:
                        andamento(f"Lendo as cobranças de {mes[5:]}/{mes[:4]} ({grupo}, {lidos} até agora)...")
            except MLErro as e:
                if avisos is None:
                    raise
                avisos.append(f"{mes[5:]}/{mes[:4]} {grupo} {'estornos' if doc == 'CREDIT_NOTE' else 'cobranças'}: {e}")
    return out


# ------------------------------------------------------------------ extrato do Mercado Pago (relatório de liberações)
MP = "https://api.mercadopago.com"
COLUNAS_EXTRATO = ["DATE", "SOURCE_ID", "EXTERNAL_REFERENCE", "RECORD_TYPE", "DESCRIPTION", "NET_CREDIT_AMOUNT",
                   "NET_DEBIT_AMOUNT", "GROSS_AMOUNT", "MP_FEE_AMOUNT", "FINANCING_FEE_AMOUNT", "SHIPPING_FEE_AMOUNT",
                   "TAXES_AMOUNT", "COUPON_AMOUNT", "INSTALLMENTS", "PAYMENT_METHOD", "BALANCE_AMOUNT", "ORDER_ID",
                   "SHIPPING_ID", "PACK_ID"]
# nomes aceitos nos arquivos (chave técnica ou tradução em português)
APELIDOS = {
    "DATE": ("DATE", "DATA", "DATA_DE_LIBERACAO", "DATA_DA_LIBERACAO", "FECHA"),
    "SOURCE_ID": ("SOURCE_ID", "ID_DA_OPERACAO_NO_MERCADO_PAGO", "ID_DE_ORIGEM", "ID_DA_OPERACAO", "ID_DA_TRANSACAO", "ID_DE_OPERACAO"),
    "EXTERNAL_REFERENCE": ("EXTERNAL_REFERENCE", "REFERENCIA_EXTERNA", "CODIGO_DE_REFERENCIA", "CODIGO_DE_REFERENCIA_EXTERNA"),
    "RECORD_TYPE": ("RECORD_TYPE", "TIPO_DE_REGISTRO"),
    "DESCRIPTION": ("DESCRIPTION", "DESCRICAO", "DESCRICAO_DA_OPERACAO", "TIPO_DE_OPERACAO"),
    "NET_CREDIT_AMOUNT": ("NET_CREDIT_AMOUNT", "VALOR_LIQUIDO_CREDITADO", "CREDITO_LIQUIDO", "VALOR_DE_CREDITO_LIQUIDO", "VALOR_CREDITADO", "CREDITO"),
    "NET_DEBIT_AMOUNT": ("NET_DEBIT_AMOUNT", "VALOR_LIQUIDO_DEBITADO", "DEBITO_LIQUIDO", "VALOR_DE_DEBITO_LIQUIDO", "VALOR_DEBITADO", "DEBITO"),
    "GROSS_AMOUNT": ("GROSS_AMOUNT", "VALOR_BRUTO", "VALOR_BRUTO_DA_OPERACAO"),
    "MP_FEE_AMOUNT": ("MP_FEE_AMOUNT", "TARIFA_DO_MERCADO_PAGO", "TARIFA_MERCADO_PAGO", "TARIFAS_DO_MERCADO_PAGO", "TARIFA"),
    "FINANCING_FEE_AMOUNT": ("FINANCING_FEE_AMOUNT", "TARIFA_DE_PARCELAMENTO", "CUSTO_DE_PARCELAMENTO", "TARIFA_DE_FINANCIAMENTO"),
    "SHIPPING_FEE_AMOUNT": ("SHIPPING_FEE_AMOUNT", "CUSTO_DE_ENVIO", "TARIFA_DE_ENVIO", "FRETE", "CUSTO_DO_FRETE"),
    "TAXES_AMOUNT": ("TAXES_AMOUNT", "IMPOSTOS", "IMPOSTOS_COBRADOS", "IMPOSTOS_RETIDOS"),
    "COUPON_AMOUNT": ("COUPON_AMOUNT", "CUPOM", "DESCONTO_DE_CUPOM", "VALOR_DO_CUPOM"),
    "INSTALLMENTS": ("INSTALLMENTS", "PARCELAS", "NUMERO_DE_PARCELAS"),
    "PAYMENT_METHOD": ("PAYMENT_METHOD", "MEIO_DE_PAGAMENTO", "FORMA_DE_PAGAMENTO"),
    "BALANCE_AMOUNT": ("BALANCE_AMOUNT", "SALDO", "SALDO_DISPONIVEL"),
    "ORDER_ID": ("ORDER_ID", "ID_DO_PEDIDO", "NUMERO_DO_PEDIDO", "NUMERO_DA_VENDA", "ID_DA_VENDA", "PEDIDO"),
    "SHIPPING_ID": ("SHIPPING_ID", "ID_DO_ENVIO", "NUMERO_DO_ENVIO"),
    "PACK_ID": ("PACK_ID", "ID_DO_PACOTE", "NUMERO_DO_PACOTE", "ID_DO_CARRINHO", "NUMERO_DO_CARRINHO"),
}


def _norm_col(t):
    t = unicodedata.normalize("NFKD", str(t or "")).encode("ascii", "ignore").decode().upper().strip()
    return re.sub(r"[^A-Z0-9]+", "_", t).strip("_")


def _num_txt(v):
    if v is None or v == "":
        return 0.0
    if isinstance(v, (int, float)):
        return float(v)
    t = str(v).strip().replace("R$", "").replace(" ", "")
    if "," in t and "." in t:
        t = t.replace(".", "").replace(",", ".") if t.rfind(",") > t.rfind(".") else t.replace(",", "")
    elif "," in t:
        t = t.replace(",", ".")
    try:
        return float(t)
    except ValueError:
        return 0.0


def _dh(v):
    if isinstance(v, dt.datetime):
        return v.replace(tzinfo=None)
    t = str(v or "").strip()
    if not t:
        return None
    try:
        d = dt.datetime.fromisoformat(t.replace("Z", "+00:00"))
        return (d.astimezone(FUSO) if d.tzinfo else d).replace(tzinfo=None)
    except ValueError:
        pass
    for f in ("%d/%m/%Y %H:%M:%S", "%d/%m/%Y %H:%M", "%d/%m/%Y", "%Y-%m-%d %H:%M:%S", "%d-%m-%Y %H:%M:%S"):
        try:
            return dt.datetime.strptime(t[:19], f)
        except ValueError:
            continue
    return None


def _int(v):
    t = re.sub(r"\D", "", str(v or "").split(".")[0])
    return int(t) if t and len(t) < 19 else None


def linhas_extrato(tabela, empresa_id, fonte="API"):
    """tabela: lista de listas (1ª = cabeçalho) → [MLExtrato]. Ignora linhas de total/saldo inicial sem data."""
    if not tabela:
        return [], []
    cab = [_norm_col(c) for c in tabela[0]]
    idx = {}
    for k, nomes in APELIDOS.items():
        for n in nomes:
            if n in cab:
                idx[k] = cab.index(n)
                break
    faltam = [k for k in ("DATE", "DESCRIPTION") if k not in idx] + \
             (["NET_CREDIT_AMOUNT/NET_DEBIT_AMOUNT"] if "NET_CREDIT_AMOUNT" not in idx and "NET_DEBIT_AMOUNT" not in idx else [])
    if faltam:
        raise MLErro("O arquivo não parece ser o relatório de liberações do Mercado Pago (faltam as colunas "
                     + ", ".join(faltam) + "). Colunas encontradas: " + ", ".join(cab[:30]))
    g = lambda ln, k: ln[idx[k]] if k in idx and idx[k] < len(ln) else None  # noqa: E731
    out = []
    for ln in tabela[1:]:
        d = _dh(g(ln, "DATE"))
        desc = str(g(ln, "DESCRIPTION") or "").strip()
        if not d or not desc:
            continue
        out.append(MLExtrato(empresa_id=empresa_id, data=d, origem_id=str(g(ln, "SOURCE_ID") or "").split(".")[0][:40],
                             referencia=str(g(ln, "EXTERNAL_REFERENCE") or "")[:80],
                             tipo_registro=str(g(ln, "RECORD_TYPE") or "")[:30], descricao=desc[:60],
                             credito=round(abs(_num_txt(g(ln, "NET_CREDIT_AMOUNT"))), 2),
                             debito=round(abs(_num_txt(g(ln, "NET_DEBIT_AMOUNT"))), 2),
                             bruto=round(_num_txt(g(ln, "GROSS_AMOUNT")), 2), tarifa_mp=round(_num_txt(g(ln, "MP_FEE_AMOUNT")), 2),
                             financiamento=round(_num_txt(g(ln, "FINANCING_FEE_AMOUNT")), 2),
                             frete=round(_num_txt(g(ln, "SHIPPING_FEE_AMOUNT")), 2), impostos=round(_num_txt(g(ln, "TAXES_AMOUNT")), 2),
                             cupom=round(_num_txt(g(ln, "COUPON_AMOUNT")), 2),
                             saldo=round(_num_txt(g(ln, "BALANCE_AMOUNT")), 2) if "BALANCE_AMOUNT" in idx and g(ln, "BALANCE_AMOUNT") not in (None, "") else None,
                             meio=str(g(ln, "PAYMENT_METHOD") or "")[:40], order_id=_int(g(ln, "ORDER_ID")),
                             shipping_id=_int(g(ln, "SHIPPING_ID")), pack_id=_int(g(ln, "PACK_ID")), fonte=fonte))
    return out, [k for k in ("ORDER_ID", "SOURCE_ID", "BALANCE_AMOUNT") if k not in idx]


def ler_csv(texto):
    texto = texto.lstrip("\ufeff")
    amostra = texto[:5000]
    try:
        sep = csv.Sniffer().sniff(amostra, delimiters=";,\t").delimiter
    except csv.Error:
        sep = ";" if amostra.count(";") > amostra.count(",") else ","
    return list(csv.reader(io.StringIO(texto), delimiter=sep))


def extrato_api(cli: Cliente, d1: dt.date, d2: dt.date, andamento=None, espera=15, limite_s=1500):
    """Gera o relatório de liberações no Mercado Pago (pela API) e devolve as linhas do arquivo (tabela)."""
    cfg = cli.req("GET", MP + "/v1/account/release_report/config", opcional=True)
    if not cfg:  # sem configuração: cria uma, com as colunas que a auditoria usa (não altera configuração existente)
        cli.req("POST", MP + "/v1/account/release_report/config",
                corpo={"file_name_prefix": "integra-bling", "include_withdrawal_at_end": False, "execute_after_withdrawal": False,
                       "display_timezone": "GMT-03", "report_translation": "en", "separator": ";", "scheduled": False,
                       "frequency": {"hour": 0, "value": 1, "type": "monthly"}, "notification_email_list": [],
                       "columns": [{"key": k} for k in COLUNAS_EXTRATO]})
    ini = f"{d1.isoformat()}T03:00:00Z"
    fim = f"{(d2 + dt.timedelta(days=1)).isoformat()}T02:59:59Z"
    url_lista = MP + "/v1/account/release_report/list"

    def lista():
        r = cli.req("GET", url_lista, opcional=True) or []
        return r if isinstance(r, list) else (r.get("results") or r.get("reports") or [])

    def mesmo_periodo(x):
        return str(x.get("begin_date", ""))[:10] == ini[:10] and str(x.get("end_date", ""))[:10] == fim[:10]

    def pronto(x):
        return bool(x.get("file_name")) and str(x.get("status") or "processed").lower() not in (
            "pending", "processing", "in_progress", "waiting", "error", "failed")

    def baixar(x):
        return ler_csv(cli.req("GET", MP + f"/v1/account/release_report/{x['file_name']}", texto=True))

    antes = lista()
    # relatório do mesmo período já gerado (ex.: pedido anterior que demorou): usa ele
    feitos = [x for x in antes if mesmo_periodo(x) and pronto(x)]
    if feitos:
        return baixar(sorted(feitos, key=lambda x: str(x.get("date_created") or x.get("id") or ""))[-1])
    ids_antes = {str(x.get("id") or x.get("file_name")) for x in antes}
    criado = cli.req("POST", MP + "/v1/account/release_report", corpo={"begin_date": ini, "end_date": fim}) or {}
    t0, ultimo = time.monotonic(), []
    while time.monotonic() - t0 < limite_s:
        ultimo = lista()
        novos = [x for x in ultimo if str(x.get("id") or x.get("file_name")) not in ids_antes
                 or (criado.get("id") and x.get("id") == criado.get("id")) or mesmo_periodo(x)]
        prontos = [x for x in novos if pronto(x)]
        if prontos:
            return baixar(sorted(prontos, key=lambda x: str(x.get("date_created") or x.get("id") or ""))[-1])
        falhou = [x for x in novos if str(x.get("status") or "").lower() in ("error", "failed")]
        if falhou:
            raise MLErro(f"O Mercado Pago informou erro ao gerar o relatório: {json.dumps(falhou[-1])[:300]}")
        if andamento:
            andamento(f"Aguardando o Mercado Pago gerar o relatório de liberações ({int(time.monotonic() - t0)}s; "
                      f"{len(novos)} relatório(s) novo(s) na lista)...")
        time.sleep(espera)
    resumo = json.dumps([{k: x.get(k) for k in ("id", "begin_date", "end_date", "status", "file_name", "date_created")}
                         for x in ultimo[-3:]], ensure_ascii=False)[:500]
    raise MLErro("O Mercado Pago ainda não terminou de gerar o relatório de liberações. O pedido ficou feito lá: tente "
                 "Atualizar de novo daqui a alguns minutos (o sistema reaproveita o relatório quando ficar pronto). "
                 f"Últimos relatórios na lista: {resumo}")


def identificar_extrato(s, empresa_id, linhas):
    """Liga cada linha do extrato ao pedido: ORDER_ID, pagamento (SOURCE_ID), referência externa, pacote ou envio."""
    por_pag, por_pack, por_envio, pedidos_ = {}, {}, {}, set()
    for oid, pags, pack, sid in s.execute(select(MLVenda.order_id, MLVenda.pagamentos, MLVenda.pack_id, MLVenda.shipping_id)
                                          .where(MLVenda.empresa_id == empresa_id)).all():
        pedidos_.add(oid)
        for p_ in (pags or "").split(","):
            if p_:
                por_pag[p_] = oid
        if pack:
            por_pack.setdefault(pack, []).append(oid)
        if sid:
            por_envio.setdefault(sid, []).append(oid)
    for x in linhas:
        oid, como = None, ""
        if x.order_id and x.order_id in pedidos_:
            oid, como = x.order_id, "pedido"
        elif x.origem_id and x.origem_id in por_pag:
            oid, como = por_pag[x.origem_id], "pagamento"
        elif _int(x.referencia) in pedidos_:
            oid, como = _int(x.referencia), "referência"
        elif x.pack_id and len(por_pack.get(x.pack_id, [])) >= 1:
            oid, como = por_pack[x.pack_id][0], "pacote"
        elif x.shipping_id and por_envio.get(x.shipping_id):
            oid, como = por_envio[x.shipping_id][0], "envio"
        elif x.order_id:
            oid, como = x.order_id, "pedido fora do período"
        x.order_id, x.identificacao = oid, como
    return linhas


def gravar_extrato(s, empresa_id, linhas, d1: dt.date, d2: dt.date):
    s.execute(delete(MLExtrato).where(MLExtrato.empresa_id == empresa_id, MLExtrato.data >= dt.datetime.combine(d1, dt.time()),
                                      MLExtrato.data < dt.datetime.combine(d2 + dt.timedelta(days=1), dt.time())))
    identificar_extrato(s, empresa_id, linhas)
    n = 0
    for x in linhas:
        if d1 <= x.data.date() <= d2:
            s.add(x)
            n += 1
    return n


def sincronizar_extrato(job_id: int, cli: Cliente | None = None, espera=15):
    with SessionLocal() as s:
        job = s.get(Job, job_id)
        p = json.loads(job.parametros or "{}")
        emp_id = job.empresa_id
        job.status, job.mensagem = "EXECUTANDO", "Pedindo o relatório de liberações ao Mercado Pago..."
        s.commit()
    cli = cli or Cliente(emp_id)

    def andamento(msg):
        with SessionLocal() as s2:
            s2.get(Job, job_id).mensagem = msg
            s2.commit()

    try:
        d1, d2 = dt.date.fromisoformat(p["de"]), dt.date.fromisoformat(p["ate"])
        tabela = extrato_api(cli, d1, d2, andamento, espera=espera)
        linhas, faltam = linhas_extrato(tabela, emp_id, "API")
        with SessionLocal() as s:
            n = gravar_extrato(s, emp_id, linhas, d1, d2)
            ident = sum(1 for x in linhas if x.order_id and d1 <= x.data.date() <= d2)
            j = s.get(Job, job_id)
            j.status, j.fim = "OK", agora()
            j.mensagem = (f"Extrato do Mercado Pago de {d1:%d/%m/%Y} a {d2:%d/%m/%Y}: {n} movimentos, {ident} ligados a pedidos."
                          + (f" O relatório veio sem as colunas {', '.join(faltam)}." if faltam else ""))
            s.commit()
    except Exception as e:  # noqa: BLE001
        with SessionLocal() as s:
            j = s.get(Job, job_id)
            j.status, j.fim = "ERRO", agora()
            j.mensagem = (f"{str(e)[:1200]} — Se o Mercado Pago não liberar o relatório pela API, baixe o "
                          "Relatório de liberações no Mercado Pago (Relatórios → Liberações) e envie o arquivo na aba Extrato.")
            s.commit()


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
        notas = []
        if p.get("notas", True):
            pagos_ = [v for v in vendas.values() if v.pago]
            for n, v in enumerate(pagos_, 1):
                if n % 25 == 1:
                    andamento(f"Lendo a nota fiscal de cada venda ({n} de {len(pagos_)})...", n, len(pagos_))
                notas += notas_da_venda(cli, seller, v)
        cobs, avisos_cob = [], []
        if p.get("cobrancas", True):
            for n, mes in enumerate(meses, 1):
                andamento(f"Lendo as cobranças pedido a pedido de {mes[5:]}/{mes[:4]}...", n, len(meses))
                cobs += cobrancas(cli, mes, emp_id, andamento, avisos_cob)
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
            if p.get("cobrancas", True):
                s.execute(delete(MLCobranca).where(MLCobranca.empresa_id == emp_id, MLCobranca.periodo >= ini,
                                                   MLCobranca.periodo <= fim))
            if p.get("notas", True):
                for k in range(0, len(ids), 5000):
                    s.execute(delete(MLNota).where(MLNota.empresa_id == emp_id, MLNota.order_id.in_(ids[k:k + 5000])))
            s.flush()
            s.add_all(vendas.values())
            for lin in itens.values():
                s.add_all(lin)
            s.add_all(tarifas)
            s.add_all(notas)
            s.add_all(cobs)
            s.flush()
            emp = s.get(Empresa, emp_id)
            emp.ml_user_id = seller
            emp.ml_sync_em = agora()
            emp.ml_sync_periodo = f"{ini} a {fim}"
            j = s.get(Job, job_id)
            pagos = sum(1 for v in vendas.values() if v.pago)
            j.status, j.fim = "OK", agora()
            j.mensagem = (f"Mercado Livre atualizado: {len(vendas)} pedidos ({pagos} pagos) e {len(tarifas)} "
                          f"tarifas/bonificações do faturamento, de {ini[5:]}/{ini[:4]} a {fim[5:]}/{fim[:4]}."
                          + (f" {len(notas)} notas fiscais." if p.get("notas", True) else "")
                          + (f" {len(cobs)} cobranças detalhadas." if p.get("cobrancas", True) else "")
                          + (" Atenção — o Mercado Livre não devolveu parte das cobranças detalhadas: " + "; ".join(avisos_cob)[:600]
                             if avisos_cob else ""))
            s.commit()
    except Exception as e:  # noqa: BLE001
        with SessionLocal() as s:
            j = s.get(Job, job_id)
            j.status, j.fim, j.mensagem = "ERRO", agora(), str(e)[:1000]
            s.commit()
