"""Rastreabilidade e auditoria das vendas do Mercado Livre (módulo separado da DRE principal).

Junta, pedido a pedido: a venda (pedido e itens), a nota fiscal (emissor do ML ou informada no envio), as cobranças
do faturamento do ML/MP ligadas ao pedido (com os estornos), as linhas do extrato do Mercado Pago e a nota escriturada
na Domínio (pela chave de acesso). A auditoria aponta o que não fecha.
"""
import json
import statistics
import datetime as dt
from collections import defaultdict

from sqlalchemy import select, func

from db import Empresa, MLVenda, MLItem, MLNota, MLCobranca, MLExtrato, DominioNota
import mlanalise

ROTULO_EXTRATO = {
    "payment": "Venda (pagamento recebido)", "refund": "Reembolso ao comprador", "chargeback": "Contestação (chargeback)",
    "mediation": "Reclamação / mediação", "shipping": "Frete", "payout": "Saque / transferência", "withdrawal": "Saque",
    "reserve_for_dispute": "Retenção por disputa", "reserve_for_refund": "Retenção para reembolso",
    "reserve_for_bpp_shipping_return": "Retenção para frete de devolução", "fee-release_in_advance": "Tarifa de antecipação",
    "cancellation": "Cancelamento", "dispute": "Disputa", "credit_payment": "Pagamento de crédito", "tax": "Imposto",
    "release_in_advance": "Antecipação", "chargeback_cancel": "Estorno de contestação", "refund_cancel": "Estorno de reembolso",
    "mediation_cancel": "Estorno de mediação", "shipping_cancel": "Estorno de frete", "bonus": "Bonificação",
    "money_transfer": "Transferência", "payment_addition": "Pagamento adicional",
}
GRUPOS_COBRANCA = [("COMISSAO", "Tarifa de venda (comissão)"), ("FRETE", "Frete / Mercado Envios"),
                   ("PARCELAMENTO", "Parcelamento"), ("DEVOLUCAO", "Devolução"), ("FULL", "Full / armazenagem"),
                   ("PUBLICIDADE", "Publicidade"), ("OUTRAS", "Outras cobranças"), ("BONUS", "Bonificações")]
REGRAS_PADRAO = {"classico_pct": None, "premium_pct": None, "fixa_valor": None, "fixa_limite": 79.0, "tolerancia": 0.10,
                 "desvio_pp": 1.0}
TIPO_ANUNCIO = {"gold_special": "Clássico", "gold_pro": "Premium", "free": "Grátis", "gold": "Ouro"}


def rotulo_extrato(d):
    return ROTULO_EXTRATO.get(str(d or "").strip().lower(), str(d or ""))


def grupo_cobranca(c: MLCobranca):
    t, d = (c.tipo or "").upper(), (c.descricao or "").lower()
    if c.natureza == "BONUS":
        return "BONUS"
    if "devolu" in d or "return" in d or "retorno" in d:
        return "DEVOLUCAO"
    # tarifa de venda: CV, ou desmembrada em custo por vender (CVVML), por cobrar (CVVPRC) e taxa de recebimento (CVVFNU)
    if t == "CV" or t.startswith("CVV") or ("tarifa" in d and "venda" in d) or "custo por vender" in d \
            or "custo por cobrar" in d or "taxa de recebimento" in d or "cargo por venta" in d:
        return "COMISSAO"
    if t.startswith("CX") or t in ("CFLX", "CFFE") or "envio" in d or "frete" in d or "envío" in d:
        return "FRETE"
    if t in ("CFONPN",) or "parcel" in d or "financ" in d:
        return "PARCELAMENTO"
    if "full" in d or "armazen" in d or "estoque" in d or t.startswith("CF"):
        return "FULL"
    if t == "PADS" or "publicidade" in d or "ads" in d:
        return "PUBLICIDADE"
    return "OUTRAS"


# composição do resultado da venda (valores com sinal)
COMPONENTES = [("venda", "Venda (produtos)"), ("frete_comprador", "Frete pago pelo comprador"), ("reembolso", "Devolvido ao comprador"),
               ("tarifa", "Tarifa de venda"), ("parcelamento", "Parcelamento"), ("frete", "Frete (envio)"),
               ("frete_devolucao", "Frete da devolução"), ("outras", "Outras cobranças"), ("imposto", "Impostos"),
               ("cmv", "Custo dos produtos")]
DEVOLUCAO_MP = {"refund", "mediation", "chargeback", "dispute"}


def classificar_extrato(linhas):
    """Separa cada linha do extrato de um pedido em componentes (a soma bate com o líquido da linha).
    Retenções por disputa (bloqueia e libera) vão para 'retido' — líquido zero quando a disputa termina."""
    comp = defaultdict(float)
    reservas_frete = [round(abs(_f(x.bruto) or (_f(x.debito) - _f(x.credito))), 2) for x in linhas
                      if "shipping_return" in str(x.descricao).lower() and _f(x.debito)]
    for x in linhas:
        d = str(x.descricao or "").lower()
        liquido = _f(x.credito) - _f(x.debito)
        if d.startswith("reserve"):
            comp["retido"] += liquido
            continue
        bruto = _f(x.bruto) if x.bruto is not None and (_f(x.bruto) or d not in ("payment",)) else 0.0
        if d == "payment":
            comp["recebido"] += bruto
        elif d in DEVOLUCAO_MP or d.endswith("_cancel"):
            v = round(abs(bruto), 2)
            if bruto < 0 and v in reservas_frete:
                reservas_frete.remove(v)
                comp["frete_devolucao"] += bruto
            else:
                comp["reembolso"] += bruto
        elif d == "shipping":
            comp["frete"] += bruto
        else:
            comp["outras"] += bruto
        comp["tarifa"] += _f(x.tarifa_mp)
        comp["parcelamento"] += _f(x.financiamento)
        comp["frete"] += _f(x.frete)
        comp["outras"] += _f(x.impostos)
        resto = liquido - (bruto + _f(x.tarifa_mp) + _f(x.financiamento) + _f(x.frete) + _f(x.impostos))
        if abs(resto) > 0.004:
            comp["outras"] += resto
    return comp


def regras(e: Empresa):
    try:
        r = json.loads(e.ml_regras or "{}")
    except ValueError:
        r = {}
    return {**REGRAS_PADRAO, **{k: v for k, v in r.items() if k in REGRAS_PADRAO}}


def _f(v):
    return float(v or 0)


def _periodo(ini, fim):
    return mlanalise.periodo_datas(ini, fim)


def dominio_lido(s, eid, d1, d2):
    """Meses (AAAA-MM) cobertos pela última leitura da Domínio que trouxe a lista de notas (Agente 0.15.0+)."""
    if not s.scalar(select(DominioNota.id).where(DominioNota.empresa_id == eid).limit(1)):
        return set()
    per = (s.get(Empresa, eid).dominio_sync_periodo or "").split(" a ")
    if len(per) != 2:
        return set()
    return set(mlanalise.meses_entre(per[0], per[1]))


def painel(s, emp: Empresa, ini, fim):
    """Uma linha por pedido do período (pagos ou com reembolso), com tudo o que se liga a ele."""
    d1, d2 = _periodo(ini, fim)
    eid = emp.id
    vendas = s.scalars(select(MLVenda).where(MLVenda.empresa_id == eid, MLVenda.data >= d1, MLVenda.data <= d2,
                                             (MLVenda.pago > 0) | (MLVenda.devolvido > 0))
                       .order_by(MLVenda.data, MLVenda.order_id)).all()
    ids = [v.order_id for v in vendas]
    porp = lambda: defaultdict(list)  # noqa: E731
    itens, notas, cobs, ext = porp(), porp(), porp(), porp()
    for k in range(0, len(ids), 5000):
        lote = ids[k:k + 5000]
        for x in s.scalars(select(MLItem).where(MLItem.empresa_id == eid, MLItem.order_id.in_(lote))).all():
            itens[x.order_id].append(x)
        for x in s.scalars(select(MLNota).where(MLNota.empresa_id == eid, MLNota.order_id.in_(lote))).all():
            notas[x.order_id].append(x)
        for x in s.scalars(select(MLCobranca).where(MLCobranca.empresa_id == eid, MLCobranca.order_id.in_(lote))).all():
            cobs[x.order_id].append(x)
        for x in s.scalars(select(MLExtrato).where(MLExtrato.empresa_id == eid, MLExtrato.order_id.in_(lote))
                           .order_by(MLExtrato.data)).all():
            ext[x.order_id].append(x)
    chaves = [n.chave for ns in notas.values() for n in ns if n.chave]
    dom = {}
    for k in range(0, len(chaves), 5000):
        for n in s.scalars(select(DominioNota).where(DominioNota.empresa_id == eid, DominioNota.chave.in_(chaves[k:k + 5000]))).all():
            dom[n.chave] = n
    meses_dom = dominio_lido(s, eid, d1, d2)
    ext_min = s.scalar(select(func.min(MLExtrato.data)).where(MLExtrato.empresa_id == eid))
    ext_max = s.scalar(select(func.max(MLExtrato.data)).where(MLExtrato.empresa_id == eid))
    res_an = {v.order_id: r for v, _its, r in mlanalise.pedidos(s, emp, ini, fim)}
    aliq = _f(emp.ml_imposto_aliq) / 100
    out = []
    for v in vendas:
        pago, dev_total = _f(v.pago), _f(v.devolvido)
        dev_prod = min(dev_total, pago)
        r = res_an.get(v.order_id) or {"imposto": aliq * (pago - dev_prod), "cmv": 0.0, "sem_custo": [], "estimado": False}
        g = defaultdict(float)
        for c in cobs[v.order_id]:
            g[grupo_cobranca(c)] += _f(c.valor)
        g = {k: round(g.get(k, 0.0), 2) for k, _n in GRUPOS_COBRANCA}
        linhas_ext = ext[v.order_id]
        tem_pagamento = any(str(x.descricao).lower() == "payment" for x in linhas_ext)
        c = {"venda": pago, "imposto": -r["imposto"], "cmv": -r["cmv"]}
        if tem_pagamento:  # o extrato é a fonte do que realmente entrou e saiu
            e = classificar_extrato(linhas_ext)
            c["frete_comprador"] = round(e["recebido"] - pago, 2)
            c["reembolso"] = e["reembolso"]
            c["tarifa"] = e["tarifa"]
            c["parcelamento"] = e["parcelamento"]
            c["frete"] = e["frete"]
            c["frete_devolucao"] = e["frete_devolucao"]
            c["outras"] = e["outras"] + g["FULL"] + g["PUBLICIDADE"]  # cobradas na fatura do ML, fora do Mercado Pago
            retido = round(e["retido"], 2)
            fonte = "extrato"
        elif cobs[v.order_id]:
            c["frete_comprador"] = 0.0
            c["reembolso"] = -dev_total
            c["tarifa"] = g["COMISSAO"]
            c["parcelamento"] = g["PARCELAMENTO"]
            c["frete"] = g["FRETE"]
            c["frete_devolucao"] = g["DEVOLUCAO"]
            c["outras"] = g["FULL"] + g["PUBLICIDADE"] + g["OUTRAS"] + g["BONUS"]
            retido, fonte = 0.0, "faturamento"
        else:
            integral = dev_total >= pago - 0.01 and dev_total > 0
            c.update({"frete_comprador": 0.0, "reembolso": -dev_total, "tarifa": 0.0 if integral else -_f(v.comissao),
                      "parcelamento": 0.0, "frete": -_f(v.frete), "frete_devolucao": 0.0, "outras": 0.0})
            retido, fonte = 0.0, "pedido"
        c = {k: round(c.get(k, 0.0), 2) for k, _n in COMPONENTES}
        resultado = round(sum(c.values()), 2)
        integral = dev_prod >= pago - 0.01 and dev_total > 0
        ns = notas[v.order_id]
        nv = next((n for n in ns if n.tipo == "venda"), ns[0] if ns else None)
        if not nv:
            st_dom = "sem nota"
        elif not nv.chave:
            st_dom = "nota sem chave"
        elif nv.chave in dom:
            st_dom = "cancelada na Domínio" if dom[nv.chave].cancelada else "escriturada"
        elif v.data.strftime("%Y-%m") not in meses_dom:
            st_dom = "Domínio não lida"
        else:
            st_dom = "não escriturada"
        its = itens[v.order_id]
        e_cred = round(sum(_f(x.credito) for x in linhas_ext), 2)
        e_deb = round(sum(_f(x.debito) for x in linhas_ext), 2)
        out.append({
            "order_id": v.order_id, "pack_id": v.pack_id, "data": v.data.isoformat(), "status": v.status,
            "descricao": v.descricao, "comprador": v.comprador, "itens": v.itens,
            "sku": ", ".join(sorted({i.sku for i in its if i.sku})),
            "tipo_anuncio": ", ".join(sorted({TIPO_ANUNCIO.get(i.tipo_anuncio, i.tipo_anuncio) for i in its if i.tipo_anuncio})),
            "nota": {"numero": nv.numero, "serie": nv.serie, "chave": nv.chave, "origem": nv.origem,
                     "valor": _f(nv.valor) if nv.valor is not None else None} if nv else None,
            "nota_devolucao": next(({"numero": n.numero, "chave": n.chave} for n in ns if n.tipo == "devolucao"), None),
            "dominio": st_dom, "dominio_valor": _f(dom[nv.chave].valor) if nv and nv.chave in dom else None,
            "componentes": c, "resultado": resultado, "fonte": fonte, "retido": retido,
            "vendas": round(pago, 2), "devolvido": round(dev_total, 2), "devolucao_integral": integral,
            "imposto": round(r["imposto"], 2), "cmv": round(r["cmv"], 2), "sem_custo": bool(r["sem_custo"]),
            "cobrancas": g, "cobrancas_liq": round(sum(g.values()), 2),
            "custo_devolucao": round(-resultado, 2) if integral else None,
            "extrato_credito": e_cred, "extrato_debito": e_deb, "extrato_liquido": round(e_cred - e_deb, 2),
            "extrato_linhas": len(linhas_ext), "no_periodo_extrato": bool(ext_min and ext_max and ext_min.date() <= v.data <= ext_max.date()),
        })
    return out


def detalhe(s, emp: Empresa, order_id: int):
    eid = emp.id
    v = s.scalar(select(MLVenda).where(MLVenda.empresa_id == eid, MLVenda.order_id == order_id))
    if not v:
        return None
    mes = v.data.strftime("%Y-%m")
    linha = next((x for x in painel(s, emp, mes, mes) if x["order_id"] == order_id), None)
    its = s.scalars(select(MLItem).where(MLItem.empresa_id == eid, MLItem.order_id == order_id)).all()
    cus = mlanalise.Custos(s, eid)
    notas = s.scalars(select(MLNota).where(MLNota.empresa_id == eid, MLNota.order_id == order_id)).all()
    dom = {n.chave: n for n in s.scalars(select(DominioNota).where(DominioNota.empresa_id == eid,
                                                                   DominioNota.chave.in_([n.chave for n in notas if n.chave] or ["-"]))).all()}
    cobs = s.scalars(select(MLCobranca).where(MLCobranca.empresa_id == eid, MLCobranca.order_id == order_id)
                     .order_by(MLCobranca.data, MLCobranca.detail_id)).all()
    ext = s.scalars(select(MLExtrato).where(MLExtrato.empresa_id == eid, MLExtrato.order_id == order_id).order_by(MLExtrato.data)).all()
    return {
        "pedido": linha,
        "itens": [{"anuncio": i.anuncio, "sku": i.sku, "titulo": i.titulo, "tipo": TIPO_ANUNCIO.get(i.tipo_anuncio, i.tipo_anuncio),
                   "categoria": i.categoria, "qtd": _f(i.qtd), "preco": _f(i.preco), "tarifa_unit": _f(i.tarifa),
                   "tarifa_pct": round(_f(i.tarifa) / _f(i.preco) * 100, 2) if _f(i.preco) else None,
                   "custo_unit": cus.custo(mlanalise.chave(i), mes)[0]} for i in its],
        "notas": [{"tipo": n.tipo, "origem": "Emissor do Mercado Livre" if n.origem == "EMISSOR_ML" else "Informada no envio (ERP)",
                   "numero": n.numero, "serie": n.serie, "chave": n.chave, "status": n.status,
                   "data": n.data.isoformat() if n.data else None, "valor": _f(n.valor) if n.valor is not None else None,
                   "dominio": ({"numero": dom[n.chave].numero, "data": dom[n.chave].data.isoformat(), "cfop": dom[n.chave].cfop,
                                "valor": _f(dom[n.chave].valor), "cancelada": dom[n.chave].cancelada} if n.chave in dom else None)}
                  for n in notas],
        "cobrancas": [{"data": c.data.isoformat() if c.data else None, "periodo": c.periodo, "grupo": c.grupo,
                       "documento": "Estorno (nota de crédito)" if c.documento == "CREDIT_NOTE" else "Fatura",
                       "tipo": c.tipo, "descricao": c.descricao, "classe": dict(GRUPOS_COBRANCA)[grupo_cobranca(c)],
                       "valor": _f(c.valor), "debitada": c.debitada} for c in cobs],
        "extrato": [{"data": x.data.isoformat(sep=" ")[:16], "descricao": rotulo_extrato(x.descricao), "tipo": x.descricao,
                     "origem_id": x.origem_id, "credito": _f(x.credito), "debito": _f(x.debito), "bruto": _f(x.bruto),
                     "tarifa_mp": _f(x.tarifa_mp), "financiamento": _f(x.financiamento), "frete": _f(x.frete),
                     "cupom": _f(x.cupom), "como": x.identificacao,
                     "retencao": str(x.descricao or "").lower().startswith("reserve")} for x in ext],
        "componentes": [{"chave": k, "nome": n, "valor": (linha or {}).get("componentes", {}).get(k, 0.0)} for k, n in COMPONENTES],
    }


def extrato(s, emp: Empresa, de: dt.date, ate: dt.date):
    ls = s.scalars(select(MLExtrato).where(MLExtrato.empresa_id == emp.id, MLExtrato.data >= dt.datetime.combine(de, dt.time()),
                                           MLExtrato.data < dt.datetime.combine(ate + dt.timedelta(days=1), dt.time()))
                   .order_by(MLExtrato.data, MLExtrato.id)).all()
    vendas = {}
    ids = list({x.order_id for x in ls if x.order_id})
    for k in range(0, len(ids), 5000):
        for v in s.scalars(select(MLVenda).where(MLVenda.empresa_id == emp.id, MLVenda.order_id.in_(ids[k:k + 5000]))).all():
            vendas[v.order_id] = v
    saldo, linhas, resumo = None, [], defaultdict(lambda: [0.0, 0.0, 0])
    for x in ls:
        if x.saldo is not None:
            saldo = _f(x.saldo)
        v = vendas.get(x.order_id)
        r = resumo[rotulo_extrato(x.descricao)]
        r[0] += _f(x.credito)
        r[1] += _f(x.debito)
        r[2] += 1
        linhas.append({"id": x.id, "data": x.data.isoformat(sep=" ")[:16], "descricao": rotulo_extrato(x.descricao),
                       "tipo": x.descricao, "origem_id": x.origem_id, "referencia": x.referencia, "credito": _f(x.credito),
                       "debito": _f(x.debito), "saldo": _f(x.saldo) if x.saldo is not None else None, "bruto": _f(x.bruto),
                       "tarifa_mp": _f(x.tarifa_mp), "financiamento": _f(x.financiamento), "frete": _f(x.frete),
                       "cupom": _f(x.cupom), "meio": x.meio, "order_id": x.order_id, "como": x.identificacao,
                       "venda": ({"data": v.data.isoformat(), "descricao": v.descricao} if v else None)})
    tc, td = sum(_f(x.credito) for x in ls), sum(_f(x.debito) for x in ls)
    return {"linhas": linhas, "creditos": round(tc, 2), "debitos": round(td, 2), "liquido": round(tc - td, 2),
            "saldo_final": saldo, "identificados": sum(1 for x in ls if x.order_id),
            "nao_identificados": sum(1 for x in ls if not x.order_id and str(x.descricao).lower() not in ("payout", "withdrawal")),
            "resumo": [{"descricao": k, "credito": round(a, 2), "debito": round(b, 2), "qtd": n} for k, (a, b, n) in
                       sorted(resumo.items(), key=lambda kv: -(kv[1][0] + kv[1][1]))],
            "inicio_lido": s.scalar(select(func.min(MLExtrato.data)).where(MLExtrato.empresa_id == emp.id)),
            "fim_lido": s.scalar(select(func.max(MLExtrato.data)).where(MLExtrato.empresa_id == emp.id))}


def auditoria(s, emp: Empresa, ini, fim):
    """Lista de achados (cada um com o pedido, o que se esperava e o que foi cobrado/lançado)."""
    d1, d2 = _periodo(ini, fim)
    eid = emp.id
    rg = regras(emp)
    tol = float(rg["tolerancia"] or 0.10)
    pn = painel(s, emp, ini, fim)
    por_id = {x["order_id"]: x for x in pn}
    achados = []

    def add(cod, gravidade, oid, texto, esperado=None, cobrado=None):
        p = por_id.get(oid) or {}
        achados.append({"codigo": cod, "gravidade": gravidade, "order_id": oid, "data": p.get("data"),
                        "descricao": p.get("descricao", ""), "texto": texto, "esperado": esperado, "cobrado": cobrado,
                        "diferenca": round((cobrado or 0) - (esperado or 0), 2) if esperado is not None and cobrado is not None else None})

    vendas = {v.order_id: v for v in s.scalars(select(MLVenda).where(MLVenda.empresa_id == eid, MLVenda.data >= d1,
                                                                      MLVenda.data <= d2)).all()}
    itens = defaultdict(list)
    ids = list(vendas)
    for k in range(0, len(ids), 5000):
        for i in s.scalars(select(MLItem).where(MLItem.empresa_id == eid, MLItem.order_id.in_(ids[k:k + 5000]))).all():
            itens[i.order_id].append(i)
    # % efetivo da tarifa por tipo de anúncio e categoria (referência quando não há comissão acordada cadastrada)
    pcts = defaultdict(list)
    for oid, its in itens.items():
        for i in its:
            if _f(i.preco) > 0 and _f(i.tarifa) > 0:
                pcts[(i.tipo_anuncio, i.categoria)].append(_f(i.tarifa) / _f(i.preco) * 100)
    mediana = {k: statistics.median(v) for k, v in pcts.items() if len(v) >= 3}
    # notas de devolução (entrada) na Domínio, para conferir as vendas devolvidas
    entradas = s.scalars(select(DominioNota).where(DominioNota.empresa_id == eid, DominioNota.lado == "E",
                                                   DominioNota.data >= d1, DominioNota.cancelada == False)).all()  # noqa: E712
    chaves_dev = {n.chave for n in s.scalars(select(MLNota).where(MLNota.empresa_id == eid, MLNota.tipo == "devolucao")).all() if n.chave}
    usadas = set()
    meses_dom = dominio_lido(s, eid, d1, d2)

    def tem_nota_devolucao(oid, p):
        nd = p.get("nota_devolucao")
        if nd and nd.get("chave") and (nd["chave"] in chaves_dev) and any(n.chave == nd["chave"] for n in entradas):
            return True
        data = dt.date.fromisoformat(p["data"])
        if data.strftime("%Y-%m") not in meses_dom:
            return True  # Domínio não lida para o período: não aponta
        alvo = _f((p["nota"] or {}).get("valor")) or p["vendas"]
        for n in entradas:
            if n.id not in usadas and data <= n.data <= data + dt.timedelta(days=120) and abs(_f(n.valor) - alvo) <= 0.05:
                usadas.add(n.id)
                return True
        return False
    for oid, p in por_id.items():
        v = vendas.get(oid)
        if not v:
            continue
        tem_fat = any(p["cobrancas"][k] for k in p["cobrancas"])
        cv_fat = -p["cobrancas"]["COMISSAO"] if tem_fat else None
        cv_ped = _f(v.comissao)
        integral = p["devolucao_integral"]
        c = p["componentes"]
        # 1. comissão cobrada no faturamento × informada no pedido
        if cv_fat is not None and not integral and abs(cv_fat - cv_ped) > tol:
            add("COMISSAO_DIVERGENTE", "alta", oid, "Tarifa de venda cobrada no faturamento diferente da informada no pedido.",
                round(cv_ped, 2), round(cv_fat, 2))
        # 1b. faturamento do ML × o que foi descontado no Mercado Pago
        if p["fonte"] == "extrato" and cv_fat is not None and abs(cv_fat - (-c["tarifa"])) > tol:
            add("COMISSAO_FATURA_X_EXTRATO", "média", oid, "Tarifa de venda no faturamento do ML diferente da descontada (líquida de "
                "estornos) no Mercado Pago. Se a venda foi devolvida, o estorno pode ainda não ter entrado na fatura.",
                round(-c["tarifa"], 2), round(cv_fat, 2))
        # 2. comissão × acordada (cadastro) ou × padrão do tipo de anúncio/categoria
        for i in itens.get(oid, []):
            if _f(i.preco) <= 0 or _f(i.tarifa) <= 0:
                continue
            pct_acordado = rg["premium_pct"] if i.tipo_anuncio == "gold_pro" else rg["classico_pct"] if i.tipo_anuncio == "gold_special" else None
            if pct_acordado is not None:
                fixa = _f(rg["fixa_valor"]) if rg["fixa_valor"] and _f(i.preco) < _f(rg["fixa_limite"] or 0) else 0.0
                esperado = round((float(pct_acordado) / 100 * _f(i.preco) + fixa) * _f(i.qtd), 2)
                cobrado = round(_f(i.tarifa) * _f(i.qtd), 2)
                if cobrado - esperado > tol:
                    add("COMISSAO_ACIMA_ACORDADO", "alta", oid, f"Tarifa de venda acima da acordada ({TIPO_ANUNCIO.get(i.tipo_anuncio, i.tipo_anuncio)} "
                        f"{float(pct_acordado):.2f}%{' + tarifa fixa' if fixa else ''}) — {i.titulo[:60]}", esperado, cobrado)
            else:
                m = mediana.get((i.tipo_anuncio, i.categoria))
                pct = _f(i.tarifa) / _f(i.preco) * 100
                if m is not None and pct - m > float(rg["desvio_pp"] or 1.0) and _f(i.preco) >= _f(rg["fixa_limite"] or 0):
                    add("COMISSAO_FORA_PADRAO", "média", oid, f"Tarifa de {pct:.2f}% do preço, acima do padrão de {m:.2f}% para "
                        f"{TIPO_ANUNCIO.get(i.tipo_anuncio, i.tipo_anuncio)} na mesma categoria — {i.titulo[:60]}",
                        round(m / 100 * _f(i.preco) * _f(i.qtd), 2), round(_f(i.tarifa) * _f(i.qtd), 2))
        # 3. frete cobrado × custo do envio
        if v.frete is not None and not integral and p["fonte"] != "pedido":
            fr = -c["frete"] - c["frete_comprador"] if p["fonte"] == "extrato" else -c["frete"]
            fr_bruto = -c["frete"]
            if abs(fr_bruto - _f(v.frete)) > max(tol, 0.5) and abs(fr - _f(v.frete)) > max(tol, 0.5):
                add("FRETE_DIVERGENTE", "média", oid, "Frete descontado diferente do custo do envio informado pelo Mercado Envios.",
                    round(_f(v.frete), 2), round(fr_bruto, 2))
        # 4. devoluções
        if integral and c["tarifa"] < -tol:
            add("TARIFA_NAO_ESTORNADA", "alta", oid, "Venda devolvida/cancelada integralmente, mas a tarifa de venda não foi estornada"
                + (" no Mercado Pago." if p["fonte"] == "extrato" else " no faturamento."), 0.0, round(-c["tarifa"], 2))
        if integral and p["custo_devolucao"] and p["custo_devolucao"] > tol:
            partes = [f"{n.lower()} {abs(c[k]):.2f}".replace(".", ",") for k, n in COMPONENTES
                      if k in ("tarifa", "parcelamento", "frete", "frete_devolucao", "outras") and abs(c[k]) > 0.004]
            add("CUSTO_DEVOLUCAO", "info", oid, "Custo que ficou com o vendedor nesta devolução: " + (", ".join(partes) or "—") + ".",
                0.0, p["custo_devolucao"])
        if abs(p["retido"]) > tol:
            add("VALOR_RETIDO", "info", oid, "Valor ainda retido pelo Mercado Pago (disputa ou reclamação em aberto).", 0.0, round(-p["retido"], 2))
        if p["devolvido"] > 0 and p["dominio"] == "escriturada" and not tem_nota_devolucao(oid, p):
            add("DEVOLUCAO_SEM_NOTA_ENTRADA", "média", oid, f"Venda com devolução ao comprador e nota {p['nota']['numero']} escriturada, mas "
                "nenhuma nota de devolução (entrada) de mesmo valor foi encontrada na Domínio até 120 dias depois. Se a mercadoria voltou, "
                "falta escriturar a devolução.", round(p["nota"]["valor"] or p["vendas"], 2), 0.0)
        # 5. nota fiscal
        st = p["dominio"]
        if _f(v.pago) > _f(v.devolvido):
            if st == "sem nota":
                add("SEM_NOTA", "alta", oid, "Venda paga sem nota fiscal encontrada no Mercado Livre (nem pelo emissor do ML, nem informada no envio).")
            elif st == "não escriturada":
                add("NOTA_NAO_ESCRITURADA", "alta", oid, f"Nota {p['nota']['numero']} não encontrada na Domínio pela chave de acesso.")
            elif st == "cancelada na Domínio":
                add("NOTA_CANCELADA_DOMINIO", "alta", oid, f"Nota {p['nota']['numero']} está cancelada na Domínio, mas a venda foi paga.")
            elif st == "nota sem chave":
                add("NOTA_SEM_CHAVE", "média", oid, f"Nota {p['nota']['numero']} sem chave de acesso no Mercado Livre: não dá para conferir na Domínio.")
            if p["dominio_valor"] is not None and _f(v.pago) and abs(p["dominio_valor"] - _f(v.pago)) > max(1.0, 0.01 * _f(v.pago)):
                add("NOTA_VALOR_DIFERENTE", "média", oid, "Valor da nota na Domínio diferente do valor pago no pedido "
                    "(pode ser frete na nota, desconto ou pacote com vários pedidos).", round(_f(v.pago), 2), round(p["dominio_valor"], 2))
        # 6. extrato
        if p["no_periodo_extrato"] and _f(v.pago) > _f(v.devolvido) and not p["extrato_linhas"]:
            add("VENDA_SEM_EXTRATO", "média", oid, "Venda paga sem nenhum movimento no extrato do Mercado Pago no período lido "
                "(o dinheiro pode ainda não ter sido liberado).")
    # 7. cobranças de pedidos que não estão entre as vendas lidas
    orfas = s.execute(select(MLCobranca.order_id, func.sum(MLCobranca.valor), func.count(MLCobranca.id))
                      .where(MLCobranca.empresa_id == eid, MLCobranca.periodo >= ini, MLCobranca.periodo <= fim,
                             MLCobranca.order_id.is_not(None)).group_by(MLCobranca.order_id)).all()
    conhecidos = {oid for (oid,) in s.execute(select(MLVenda.order_id).where(MLVenda.empresa_id == eid)).all()}
    for oid, soma, n in orfas:
        if oid not in conhecidos:
            achados.append({"codigo": "COBRANCA_SEM_VENDA", "gravidade": "média", "order_id": oid, "data": None, "descricao": "",
                            "texto": f"{n} cobrança(s) no faturamento de um pedido que não está entre as vendas lidas "
                                     "(pode ser de período anterior: atualize um período maior).",
                            "esperado": None, "cobrado": round(-_f(soma), 2), "diferenca": None})
    # 8. movimentos do extrato sem pedido (exceto saques)
    for x in s.scalars(select(MLExtrato).where(MLExtrato.empresa_id == eid, MLExtrato.order_id.is_(None),
                                               MLExtrato.data >= dt.datetime.combine(d1, dt.time()),
                                               MLExtrato.data < dt.datetime.combine(d2 + dt.timedelta(days=1), dt.time()))).all():
        if str(x.descricao).lower() in ("payout", "withdrawal"):
            continue
        achados.append({"codigo": "EXTRATO_SEM_PEDIDO", "gravidade": "média", "order_id": None, "data": x.data.date().isoformat(),
                        "descricao": rotulo_extrato(x.descricao),
                        "texto": f"Movimento do extrato sem pedido identificado (operação {x.origem_id or '—'}, referência {x.referencia or '—'}).",
                        "esperado": None, "cobrado": round(_f(x.credito) - _f(x.debito), 2), "diferenca": None})
    # 9. notas da Domínio (saídas de venda) sem venda no ML — informativo (pode ser outro canal)
    chaves_ml = {c for (c,) in s.execute(select(MLNota.chave).where(MLNota.empresa_id == eid, MLNota.chave != "")).all()}
    sem_ml = [n for n in s.scalars(select(DominioNota).where(DominioNota.empresa_id == eid, DominioNota.lado == "S",
                                                             DominioNota.data >= d1, DominioNota.data <= d2,
                                                             DominioNota.cancelada == False)).all()  # noqa: E712
              if n.chave and n.chave not in chaves_ml and n.cfop % 1000 in (102, 101, 403, 405, 106, 108, 117, 118, 119, 120, 922, 923)]
    ordem = {"alta": 0, "média": 1, "info": 2}
    achados.sort(key=lambda a: (ordem.get(a["gravidade"], 3), a["codigo"], a["data"] or ""))
    resumo = defaultdict(lambda: {"qtd": 0, "valor": 0.0})
    for a in achados:
        resumo[a["codigo"]]["qtd"] += 1
        resumo[a["codigo"]]["valor"] += a["diferenca"] if a["diferenca"] is not None else (a["cobrado"] or 0)
    return {"achados": achados, "resumo": {k: {"qtd": v["qtd"], "valor": round(v["valor"], 2)} for k, v in resumo.items()},
            "regras": rg, "pedidos": len(pn),
            "dominio_sem_ml": [{"data": n.data.isoformat(), "numero": n.numero, "serie": n.serie, "chave": n.chave, "cfop": n.cfop,
                                "valor": _f(n.valor)} for n in sorted(sem_ml, key=lambda n: n.data)][:2000],
            "dominio_lida": bool(dominio_lido(s, eid, d1, d2)),
            "tem_cobrancas": any(any(p["cobrancas"].values()) for p in pn),
            "tem_extrato": any(p["no_periodo_extrato"] for p in pn)}
