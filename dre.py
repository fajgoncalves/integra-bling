"""Modelo de DRE e cálculo mês a mês."""
import re
import datetime as dt
from collections import defaultdict

from sqlalchemy import select

from db import ModeloDRE, LinhaDRE, ContaOrigem, Lancamento, DominioValor
import dominio

# código, nome, tipo, sinal, fórmula, destaque, base_av
MODELO_PADRAO = [
    ("T_REC", "RECEITA", "TITULO", 0, "", False, False),
    ("RB_VENDAS", "Venda de mercadorias e produtos", "CONTA", 1, "", False, False),
    ("RB_SERV", "Prestação de serviços", "CONTA", 1, "", False, False),
    ("RB_OUTRAS", "Outras receitas operacionais", "CONTA", 1, "", False, False),
    ("RB", "(=) Receita bruta", "TOTAL", 0, "RB_VENDAS + RB_SERV + RB_OUTRAS", True, False),
    ("DED_DEV", "(-) Devoluções de vendas", "CONTA", -1, "", False, False),
    ("DED_IMP", "(-) Impostos sobre vendas", "CONTA", -1, "", False, False),
    ("RL", "(=) Receita líquida", "TOTAL", 0, "RB + DED_DEV + DED_IMP", True, True),
    ("CMV", "(-) Custo das mercadorias e produtos vendidos", "CONTA", -1, "", False, False),
    ("CMV_DEVC", "(+) Devoluções de compra", "CONTA", 1, "", False, False),
    ("LB", "(=) Lucro bruto", "TOTAL", 0, "RL + CMV + CMV_DEVC", True, False),
    ("T_DESP", "DESPESAS OPERACIONAIS", "TITULO", 0, "", False, False),
    ("DV_COM", "(-) Despesas com comissões", "CONTA", -1, "", False, False),
    ("DV_VEND", "(-) Despesas com vendas e marketing", "CONTA", -1, "", False, False),
    ("DV_FRETE", "(-) Fretes e logística", "CONTA", -1, "", False, False),
    ("DA_PES", "(-) Despesas com pessoal", "CONTA", -1, "", False, False),
    ("DA_OCUP", "(-) Ocupação (aluguel, energia, água)", "CONTA", -1, "", False, False),
    ("DA_ADM", "(-) Despesas administrativas", "CONTA", -1, "", False, False),
    ("DA_TRIB", "(-) Despesas tributárias", "CONTA", -1, "", False, False),
    ("DOP", "(=) Total das despesas operacionais", "TOTAL", 0,
     "DV_COM + DV_VEND + DV_FRETE + DA_PES + DA_OCUP + DA_ADM + DA_TRIB", False, False),
    ("RO", "(=) Resultado operacional (EBITDA)", "TOTAL", 0, "LB + DOP", True, False),
    ("RF", "(+) Receitas financeiras", "CONTA", 1, "", False, False),
    ("DF", "(-) Despesas financeiras e tarifas bancárias", "CONTA", -1, "", False, False),
    ("ONO", "(+/-) Outras receitas e despesas não operacionais", "CONTA", 0, "", False, False),
    ("LAIR", "(=) Resultado antes do IR e da CSLL", "TOTAL", 0, "RO + RF + DF + ONO", True, False),
    ("IR", "(-) IRPJ e CSLL", "CONTA", -1, "", False, False),
    ("LL", "(=) Lucro líquido", "TOTAL", 0, "LAIR + IR", True, False),
]

# sugestão de associação das contas da Domínio no modelo padrão
SUGESTAO_DOMINIO = {"VENDAS": "RB_VENDAS", "SERVICOS": "RB_SERV", "DEV_VENDA": "DED_DEV", "DEV_COMPRA": "CMV_DEVC"}

TOKEN = re.compile(r"\s*([+-]?)\s*([A-Za-z0-9_]+)\s*")


def criar_modelo_padrao(s, nome="DRE Padrão"):
    m = ModeloDRE(nome=nome, descricao="Modelo inicial. Edite as linhas e as fórmulas à vontade.")
    s.add(m)
    s.flush()
    for i, (cod, nome_l, tipo, sinal, form, dest, base) in enumerate(MODELO_PADRAO, 1):
        s.add(LinhaDRE(modelo_id=m.id, ordem=i * 10, codigo=cod, nome=nome_l, tipo=tipo, sinal=sinal,
                       formula=form, destaque=dest, base_av=base))
    s.flush()
    return m


def parse_formula(txt: str):
    """'RB + DED - X' -> [(1,'RB'), (1,'DED'), (-1,'X')]. ValueError se inválida."""
    txt = (txt or "").strip()
    if not txt:
        return []
    termos, pos = [], 0
    while pos < len(txt):
        m = TOKEN.match(txt, pos)
        if not m or m.end() == pos:
            raise ValueError(f"Fórmula inválida perto de: {txt[pos:pos + 15]!r}")
        if termos and not m.group(1):
            raise ValueError("Separe os códigos com + ou -.")
        termos.append((-1 if m.group(1) == "-" else 1, m.group(2).upper()))
        pos = m.end()
    return termos


def validar_modelo(linhas):
    """linhas: lista de dicts já ordenada. Devolve lista de erros."""
    erros, vistos = [], set()
    codigos = [str(x.get("codigo") or "").strip().upper() for x in linhas]
    for x, cod in zip(linhas, codigos):
        if not re.fullmatch(r"[A-Z0-9_]{1,30}", cod):
            erros.append(f"Código inválido: {cod or '(vazio)'} — use letras, números e _.")
        if cod in vistos:
            erros.append(f"Código repetido: {cod}.")
        if x.get("tipo") == "TOTAL":
            try:
                for _sinal, ref in parse_formula(x.get("formula")):
                    if ref not in vistos:
                        erros.append(f"{cod}: a fórmula usa {ref}, que não existe numa linha acima.")
            except ValueError as e:
                erros.append(f"{cod}: {e}")
        vistos.add(cod)
    return erros


def meses_entre(ini: str, fim: str):
    a, m = int(ini[:4]), int(ini[5:7])
    fa, fm = int(fim[:4]), int(fim[5:7])
    out = []
    while (a, m) <= (fa, fm):
        out.append(f"{a:04d}-{m:02d}")
        m += 1
        if m > 12:
            a, m = a + 1, 1
    return out


def _mes(d: dt.date | None):
    return f"{d.year:04d}-{d.month:02d}" if d else None


def data_lancamento(x: Lancamento, regime: str):
    if regime == "vencimento":
        return x.vencimento
    return x.competencia or x.data_emissao or x.vencimento


def valores_origem(s, empresa_id: int, ini: str, fim: str, regime="competencia"):
    """{conta_origem_id: {mes: valor positivo}} e {conta_id: 'R'|'D'} para Bling e Domínio no período."""
    contas = s.scalars(select(ContaOrigem).where(ContaOrigem.empresa_id == empresa_id)).all()
    por_cod = {(c.origem, c.codigo): c for c in contas}
    vals = defaultdict(lambda: defaultdict(float))
    natureza = {}
    d_ini = dt.date(int(ini[:4]), int(ini[5:7]), 1)
    fa, fm = int(fim[:4]), int(fim[5:7])
    d_fim = dt.date(fa + (fm == 12), 1 if fm == 12 else fm + 1, 1) - dt.timedelta(days=1)
    sem_categoria = {"R": 0.0, "P": 0.0}
    for x in s.scalars(select(Lancamento).where(Lancamento.empresa_id == empresa_id, Lancamento.situacao != 5)).all():
        d = data_lancamento(x, regime)
        if not d or d < d_ini or d > d_fim:
            continue
        c = por_cod.get(("BLING", x.categoria_codigo)) if x.categoria_codigo else None
        if not c:
            sem_categoria[x.tipo] += float(x.valor or 0)
            continue
        vals[c.id][_mes(d)] += float(x.valor or 0)
        natureza[c.id] = "R" if x.tipo == "R" else "D"
    for v in s.scalars(select(DominioValor).where(DominioValor.empresa_id == empresa_id, DominioValor.mes >= ini,
                                                  DominioValor.mes <= fim)).all():
        g = dominio.grupo(v.grupo, v.cfop)
        c = por_cod.get(("DOMINIO", g)) if g else None
        if c:
            vals[c.id][v.mes] += float(v.valor or 0)
            natureza[c.id] = c.tipo if c.tipo in ("R", "D") else "R"
    return contas, vals, natureza, sem_categoria


def calcular(s, empresa, ini: str, fim: str, regime="competencia"):
    meses = meses_entre(ini, fim)
    linhas = s.scalars(select(LinhaDRE).where(LinhaDRE.modelo_id == empresa.modelo_id).order_by(LinhaDRE.ordem, LinhaDRE.id)).all()
    contas, vals, natureza, sem_cat = valores_origem(s, empresa.id, ini, fim, regime)
    por_linha = defaultdict(list)
    for c in contas:
        if c.linha_id:
            por_linha[c.linha_id].append(c)
    res, por_codigo, avisos = [], {}, []
    for ln in linhas:
        v = [0.0] * len(meses)
        comp = []
        if ln.tipo == "CONTA":
            for c in por_linha.get(ln.id, []):
                cv = vals.get(c.id, {})
                sinal = ln.sinal or (1 if natureza.get(c.id, c.tipo[:1]) == "R" else -1)
                serie = [round(sinal * cv.get(m, 0.0), 2) for m in meses]
                if any(serie):
                    comp.append({"conta_id": c.id, "origem": c.origem, "descricao": c.descricao, "valores": serie,
                                 "total": round(sum(serie), 2)})
                v = [a + b for a, b in zip(v, serie)]
        elif ln.tipo == "TOTAL":
            try:
                for sinal, ref in parse_formula(ln.formula):
                    base = por_codigo.get(ref)
                    if base is None:
                        avisos.append(f"{ln.codigo}: {ref} não foi encontrado acima.")
                        continue
                    v = [a + sinal * b for a, b in zip(v, base)]
            except ValueError as e:
                avisos.append(f"{ln.codigo}: {e}")
        v = [round(x, 2) for x in v]
        por_codigo[ln.codigo.upper()] = v
        res.append({"id": ln.id, "codigo": ln.codigo, "nome": ln.nome, "tipo": ln.tipo, "destaque": ln.destaque,
                    "base_av": ln.base_av, "valores": v if ln.tipo != "TITULO" else None,
                    "total": round(sum(v), 2) if ln.tipo != "TITULO" else None,
                    "composicao": sorted(comp, key=lambda x: -abs(x["total"]))})
    base = next((r for r in res if r["base_av"]), None) or next((r for r in res if r["tipo"] == "TOTAL"), None)
    for r in res:
        if r["valores"] is None or not base:
            r["av"], r["av_total"] = None, None
            continue
        r["av"] = [round(a / b * 100, 1) if b else None for a, b in zip(r["valores"], base["valores"])]
        r["av_total"] = round(r["total"] / base["total"] * 100, 1) if base["total"] else None
    nao = []
    for c in contas:
        if c.linha_id:
            continue
        t = sum(vals.get(c.id, {}).values())
        if abs(t) >= 0.005:
            nao.append({"conta_id": c.id, "origem": c.origem, "descricao": c.descricao, "total": round(t, 2),
                        "natureza": natureza.get(c.id, c.tipo)})
    if sem_cat["R"] or sem_cat["P"]:
        avisos.append(f"Títulos do Bling sem categoria no período: a receber R$ {_br(sem_cat['R'])}, "
                      f"a pagar R$ {_br(sem_cat['P'])} (não entram na DRE).")
    return {"meses": meses, "linhas": res, "nao_associadas": sorted(nao, key=lambda x: -abs(x["total"])),
            "avisos": avisos, "base_av": base["codigo"] if base else None, "regime": regime}


def _br(v):
    return f"{v:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")
