"""Análise do Mercado Livre — DRE própria, margem por produto e por pedido.

Usa só os dados do Mercado Livre (pedidos, itens, frete de cada envio e faturamento mensal) e a tabela de custos
própria do módulo (por SKU, com vigência mensal). Quando o SKU não tem custo no módulo, usa o custo da aba Custo dos
produtos da empresa (casando o SKU com o código do produto) — só leitura: não lê nem altera a DRE principal.
"""
import re
import datetime as dt
from collections import defaultdict

from sqlalchemy import select

from db import Empresa, MLVenda, MLItem, MLTarifa, MLCusto, MLAds, Produto, ProdutoCusto

# encargos do faturamento → linha da DRE do ML
LINHA_TIPO = {"CV": "COM", "CXD": "FRETE", "PADS": "ADS"}
LINHAS = [  # (código, nome, tipo, fórmula)
    ("RB", "Vendas brutas (pedidos pagos)", "CONTA", ""),
    ("DEV", "(-) Reembolsos e cancelamentos", "CONTA", ""),
    ("VL", "(=) Vendas líquidas", "TOTAL", "RB + DEV"),
    ("IMP", "(-) Impostos sobre vendas", "CONTA", ""),
    ("RL", "(=) Receita líquida", "TOTAL", "VL + IMP"),
    ("CMV", "(-) Custo dos produtos vendidos", "CONTA", ""),
    ("LB", "(=) Lucro bruto", "TOTAL", "RL + CMV"),
    ("CML", "(-) Custos do Mercado Livre", "GRUPO", ""),
    ("COM", "Tarifas de venda (comissão)", "CONTA", "CML"),
    ("FRETE", "Mercado Envios (frete)", "CONTA", "CML"),
    ("ADS", "Publicidade (Product Ads)", "CONTA", "CML"),
    ("OUTRAS", "Outras tarifas do Mercado Livre (Full, armazenagem...)", "CONTA", "CML"),
    ("TMP", "Tarifas do Mercado Pago", "CONTA", "CML"),
    ("BONUS", "(+) Bonificações do Mercado Livre", "CONTA", "CML"),
    ("MC", "(=) Margem de contribuição", "TOTAL", "LB + CML"),
]


def meses_entre(ini, fim):
    out, (a, m) = [], map(int, ini.split("-"))
    while f"{a:04d}-{m:02d}" <= fim:
        out.append(f"{a:04d}-{m:02d}")
        a, m = (a + 1, 1) if m == 12 else (a, m + 1)
    return out


def periodo_datas(ini, fim):
    a, m = map(int, fim.split("-"))
    return dt.date.fromisoformat(ini + "-01"), dt.date(a + (m == 12), 1 if m == 12 else m + 1, 1) - dt.timedelta(days=1)


def chave(it: MLItem):
    return it.sku or it.anuncio or it.titulo[:80]


def norm(k):
    """SKU/código comparável: sem espaços, sem caixa e sem zeros à esquerda."""
    k = re.sub(r"\s+", "", str(k or "")).upper()
    return k.lstrip("0") or k


class Custos:
    """Custo unitário de um produto do ML num mês.

    1º o custo do próprio módulo (aba Custos do Mercado Livre), pela vigência;
    2º o custo da aba Custo dos produtos da empresa, quando o SKU é igual ao código (ou código interno) do produto.
    Em vendas de meses anteriores à primeira vigência, usa o primeiro custo informado (marcado como estimado)."""

    def __init__(self, s, eid):
        self.ml = defaultdict(list)
        for c in s.scalars(select(MLCusto).where(MLCusto.empresa_id == eid).order_by(MLCusto.mes_inicio)).all():
            self.ml[c.chave].append((c.mes_inicio, float(c.custo)))
        cad = defaultdict(list)
        self.nome_cad = {}
        for p, mes, v in s.execute(select(Produto, ProdutoCusto.mes_inicio, ProdutoCusto.custo)
                                   .join(ProdutoCusto, ProdutoCusto.produto_id == Produto.id)
                                   .where(Produto.empresa_id == eid).order_by(ProdutoCusto.mes_inicio)).all():
            for k in {norm(p.codigo), norm(p.produto)} - {""}:
                cad[k].append((mes, float(v)))
                self.nome_cad[k] = p.descricao
        self.cad = cad

    @staticmethod
    def _no_mes(lista, mes):
        if not lista:
            return None, False
        c = None
        for ini, v in lista:
            if ini <= mes:
                c = v
        return (c, False) if c is not None else (lista[0][1], True)

    def custo(self, k, mes):
        """(custo, origem 'ML'|'CADASTRO'|None, estimado)"""
        c, est = self._no_mes(self.ml.get(k), mes)
        if c is not None:
            return c, "ML", est
        c, est = self._no_mes(self.cad.get(norm(k)), mes)
        if c is not None:
            return c, "CADASTRO", est
        return None, None, False

    def vigencias_ml(self, k):
        return len(self.ml.get(k, []))


def linha_tarifa(t: MLTarifa):
    if t.natureza != "CHARGE":
        return "BONUS"
    if t.grupo == "MP":
        return "TMP"
    return LINHA_TIPO.get(t.tipo, "OUTRAS")


def _fator(v: MLVenda):
    """Parte do pedido que ficou vendida (1 = nada devolvido)."""
    pago = float(v.pago or 0)
    return max(0.0, min(1.0, 1 - float(v.devolvido or 0) / pago)) if pago else 0.0


def pedidos(s, emp: Empresa, ini, fim, cus=None):
    """[(MLVenda, [MLItem], resultado dict)] dos pedidos pagos do período."""
    d1, d2 = periodo_datas(ini, fim)
    cus = cus or Custos(s, emp.id)
    aliq = float(emp.ml_imposto_aliq or 0) / 100
    vs = s.scalars(select(MLVenda).where(MLVenda.empresa_id == emp.id, MLVenda.data >= d1, MLVenda.data <= d2,
                                         MLVenda.pago > 0).order_by(MLVenda.data, MLVenda.order_id)).all()
    itens = defaultdict(list)
    ids = [v.order_id for v in vs]
    for k in range(0, len(ids), 5000):
        for it in s.scalars(select(MLItem).where(MLItem.empresa_id == emp.id, MLItem.order_id.in_(ids[k:k + 5000]))).all():
            itens[it.order_id].append(it)
    out = []
    for v in vs:
        mes, f = v.data.strftime("%Y-%m"), _fator(v)
        pago, dev = float(v.pago or 0), min(float(v.devolvido or 0), float(v.pago or 0))  # reembolso de frete fica fora
        cmv, sem, estimado = 0.0, [], False
        for it in itens[v.order_id]:
            c, _origem, est = cus.custo(chave(it), mes)
            estimado = estimado or est
            if c is None:
                sem.append(chave(it))
            else:
                cmv += c * float(it.qtd or 0) * f
        tarifa = float(v.comissao or 0) * f
        imposto = aliq * (pago - dev)
        frete = float(v.frete or 0)
        margem = pago - dev - imposto - tarifa - frete - cmv
        out.append((v, itens[v.order_id], {"mes": mes, "fator": f, "vendas": pago, "devolvido": dev, "imposto": imposto,
                                           "tarifa": tarifa, "frete": frete, "frete_lido": v.frete is not None,
                                           "cmv": cmv, "sem_custo": sem, "estimado": estimado, "margem": margem}))
    return out


def ads_rateio(s, eid, ini, fim):
    """Rateio do custo do Product Ads (por anúncio e mês, da API de publicidade) entre as vendas pagas daquele anúncio no mês,
    pelas unidades vendidas (líquidas de devolução). Devolve um dict com:
      pedido {order_id: valor}, item {(order_id, anúncio): valor}, nao_alocado [{mes, anuncio, titulo, custo}],
      meses {mes: {"api": custo, "alocado": x, "nao_alocado": y}}."""
    d1, d2 = periodo_datas(ini, fim)
    ads = s.scalars(select(MLAds).where(MLAds.empresa_id == eid, MLAds.mes >= ini, MLAds.mes <= fim)).all()
    out = {"pedido": defaultdict(float), "item": defaultdict(float), "nao_alocado": [],
           "meses": defaultdict(lambda: {"api": 0.0, "alocado": 0.0, "nao_alocado": 0.0})}
    if not ads:
        return out
    unidades = defaultdict(list)  # (mes, anúncio) → [(order_id, unidades líquidas)]
    vs = {v.order_id: v for v in s.scalars(select(MLVenda).where(MLVenda.empresa_id == eid, MLVenda.data >= d1, MLVenda.data <= d2,
                                                                  MLVenda.pago > 0)).all()}
    ids = list(vs)
    for k in range(0, len(ids), 5000):
        for it in s.scalars(select(MLItem).where(MLItem.empresa_id == eid, MLItem.order_id.in_(ids[k:k + 5000]))).all():
            v = vs[it.order_id]
            u = float(it.qtd or 0) * _fator(v)
            if u > 0 and it.anuncio:
                unidades[(v.data.strftime("%Y-%m"), it.anuncio)].append((it.order_id, u))
    for a in ads:
        custo = float(a.custo or 0)
        m = out["meses"][a.mes]
        m["api"] += custo
        lst = unidades.get((a.mes, a.anuncio))
        tot = sum(u for _o, u in lst) if lst else 0
        if not tot:
            m["nao_alocado"] += custo
            out["nao_alocado"].append({"mes": a.mes, "anuncio": a.anuncio, "titulo": a.titulo, "custo": round(custo, 2),
                                       "cliques": a.cliques, "impressoes": a.impressoes})
            continue
        m["alocado"] += custo
        for oid, u in lst:
            x = custo * u / tot
            out["pedido"][oid] += x
            out["item"][(oid, a.anuncio)] += x
    return out


def dre(s, emp: Empresa, ini, fim):
    meses = meses_entre(ini, fim)
    idx = {m: i for i, m in enumerate(meses)}
    val = {cod: [0.0] * len(meses) for cod, *_ in LINHAS}
    comp = defaultdict(lambda: defaultdict(lambda: [0.0] * len(meses)))
    estimados = 0
    sem_custo, fretes_nao_lidos, conf = {}, 0, {"comissao_pedidos": 0.0, "frete_pedidos": 0.0}
    for v, its, r in pedidos(s, emp, ini, fim):
        i = idx[r["mes"]]
        val["RB"][i] += r["vendas"]
        val["DEV"][i] -= r["devolvido"]
        val["IMP"][i] -= r["imposto"]
        val["CMV"][i] -= r["cmv"]
        conf["comissao_pedidos"] += r["tarifa"]
        conf["frete_pedidos"] += r["frete"]
        fretes_nao_lidos += bool(v.shipping_id) and not r["frete_lido"]
        estimados += r["estimado"]
        for it in its:
            if chave(it) in r["sem_custo"]:
                x = sem_custo.setdefault(chave(it), {"chave": chave(it), "titulo": it.titulo, "vendas": 0.0})
                x["vendas"] += float(it.preco or 0) * float(it.qtd or 0)
    meses_tarifa = set()
    for t in s.scalars(select(MLTarifa).where(MLTarifa.empresa_id == emp.id, MLTarifa.periodo >= ini,
                                              MLTarifa.periodo <= fim)).all():
        ln = linha_tarifa(t)
        i = idx[t.periodo]
        x = abs(float(t.valor or 0)) * (1 if ln == "BONUS" else -1)
        val[ln][i] += x
        comp[ln][f"{t.rotulo} ({t.tipo})"][i] += x
        meses_tarifa.add(t.periodo)
    for cod, _n, tipo, form in LINHAS:  # grupos e totais, na ordem
        if tipo == "GRUPO":
            filhos = [c for c, _, tp, f in LINHAS if tp == "CONTA" and f == cod]
            val[cod] = [sum(val[f][i] for f in filhos) for i in range(len(meses))]
        elif tipo == "TOTAL":
            a, b = [x.strip() for x in form.split("+")]
            val[cod] = [val[a][i] + val[b][i] for i in range(len(meses))]
    base = val["RB"]
    linhas = []
    for cod, nome, tipo, form in LINHAS:
        vs = [round(x, 2) for x in val[cod]]
        tot = round(sum(vs), 2)
        btot = sum(base)
        linhas.append({"codigo": cod, "nome": nome if cod != "IMP" else f"{nome} ({_br(float(emp.ml_imposto_aliq or 0))}%)",
                       "tipo": tipo, "grupo": form if tipo == "CONTA" and form else None, "valores": vs, "total": tot,
                       "av": [round(x / b * 100, 1) if b else None for x, b in zip(vs, base)],
                       "av_total": round(tot / btot * 100, 1) if btot else None,
                       "composicao": [{"descricao": k, "valores": [round(y, 2) for y in v], "total": round(sum(v), 2)}
                                      for k, v in sorted(comp[cod].items(), key=lambda kv: -abs(sum(kv[1])))]})
    avisos = []
    if not float(emp.ml_imposto_aliq or 0):
        avisos.append("Informe a alíquota de impostos sobre vendas (botão Configurar): sem ela os impostos ficam zerados.")
    if sem_custo:
        tot = sum(x["vendas"] for x in sem_custo.values())
        avisos.append(f"{len(sem_custo)} produto(s) vendido(s) sem custo (R$ {_br(tot)} em vendas): o CMV está incompleto. "
                      "Informe na aba Custos.")
    sem_fat = [m for m in meses if base[idx[m]] and m not in meses_tarifa]
    if sem_fat:
        avisos.append("Sem faturamento do Mercado Livre lido em " + ", ".join(f"{m[5:]}/{m[:4]}" for m in sem_fat)
                      + " (o período pode não ter fechado): as tarifas desses meses estão zeradas.")
    if estimados:
        avisos.append(f"{estimados} pedido(s) de meses anteriores à primeira vigência de custo do produto: foi usado o primeiro "
                      "custo informado. Para precisão, informe o custo a partir do mês das vendas.")
    if fretes_nao_lidos:
        avisos.append(f"{fretes_nao_lidos} pedido(s) sem o frete do envio lido: a margem por pedido e por produto fica sem esse frete.")
    return {"meses": meses, "linhas": linhas, "avisos": avisos, "sem_custo": sorted(sem_custo.values(), key=lambda x: -x["vendas"]),
            "conferencia": {"comissao_pedidos": round(conf["comissao_pedidos"], 2),
                            "comissao_faturamento": round(-sum(val["COM"]), 2),
                            "frete_pedidos": round(conf["frete_pedidos"], 2), "frete_faturamento": round(-sum(val["FRETE"]), 2)},
            "aliquota": float(emp.ml_imposto_aliq or 0)}


def produtos(s, emp: Empresa, ini, fim):
    """Margem por produto (SKU ou anúncio), com frete do pedido rateado pelo valor dos itens."""
    cus = Custos(s, emp.id)
    aliq = float(emp.ml_imposto_aliq or 0) / 100
    agg = {}
    rat = ads_rateio(s, emp.id, ini, fim)
    for v, its, r in pedidos(s, emp, ini, fim, cus):
        tot_itens = sum(float(i.preco or 0) * float(i.qtd or 0) for i in its) or 1
        for it in its:
            k = chave(it)
            bruto = float(it.preco or 0) * float(it.qtd or 0)
            parte = bruto / tot_itens
            x = agg.setdefault(k, {"chave": k, "sku": it.sku, "anuncio": it.anuncio, "titulo": it.titulo, "tipos": set(),
                                   "pedidos": 0, "qtd": 0.0, "vendas": 0.0, "devolvido": 0.0, "imposto": 0.0, "tarifa": 0.0,
                                   "frete": 0.0, "cmv": 0.0, "publicidade": 0.0, "sem_custo": False, "origens": set(),
                                   "estimado": False})
            x["tipos"].add({"gold_special": "Clássico", "gold_pro": "Premium"}.get(it.tipo_anuncio, it.tipo_anuncio or "—"))
            x["pedidos"] += 1
            x["qtd"] += float(it.qtd or 0) * r["fator"]
            x["vendas"] += bruto
            x["devolvido"] += bruto * (1 - r["fator"])
            x["imposto"] += aliq * bruto * r["fator"]
            x["tarifa"] += float(it.tarifa or 0) * float(it.qtd or 0) * r["fator"]
            x["frete"] += r["frete"] * parte
            x["publicidade"] += rat["item"].get((v.order_id, it.anuncio), 0.0) if it.anuncio else 0.0
            c, origem, est = cus.custo(k, r["mes"])
            if origem:
                x["origens"].add(origem)
            x["estimado"] = x["estimado"] or est
            if c is None:
                x["sem_custo"] = True
            else:
                x["cmv"] += c * float(it.qtd or 0) * r["fator"]
    out = []
    for x in agg.values():
        liq = x["vendas"] - x["devolvido"]
        x["margem_antes_ads"] = liq - x["imposto"] - x["tarifa"] - x["frete"] - x["cmv"]
        x["margem"] = x["margem_antes_ads"] - x["publicidade"]
        x["margem_pct"] = round(x["margem"] / liq * 100, 1) if liq else None
        x["tipos"] = ", ".join(sorted(x["tipos"]))
        x["custo_atual"], _o, _e = cus.custo(x["chave"], fim)
        x["origem_custo"] = " + ".join(sorted({"ML": "Mercado Livre", "CADASTRO": "Custo dos produtos"}[o] for o in x.pop("origens")))
        for k in ("qtd", "vendas", "devolvido", "imposto", "tarifa", "frete", "cmv", "publicidade", "margem_antes_ads", "margem"):
            x[k] = round(x[k], 4 if k == "qtd" else 2)
        out.append(x)
    out.sort(key=lambda x: -x["vendas"])
    return out


def _br(v):
    return f"{v:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")
