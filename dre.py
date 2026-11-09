"""Modelo de DRE e cálculo mês a mês."""
import re
import datetime as dt
from collections import defaultdict

from sqlalchemy import select

from db import ModeloDRE, LinhaDRE, ContaOrigem, Movimento, DominioValor
import dominio

# Modelo padrão: (código, nome, tipo, fórmula, base_av, contas [(código, nome)])
MODELO_PADRAO = [
    ("RB", "Receita bruta", "GRUPO", "", False, [
        ("RB_VENDAS", "Venda de mercadorias e produtos"), ("RB_SERV", "Prestação de serviços"),
        ("RB_OUTRAS", "Outras receitas operacionais")]),
    ("DED", "(-) Deduções da receita", "GRUPO", "", False, [
        ("DED_DEV", "Devoluções de vendas"), ("PIS_COFINS", "PIS/COFINS"), ("ICMS_TTD", "ICMS TTD"),
        ("DED_IMP", "Impostos sobre vendas")]),
    ("RL", "(=) Receita líquida", "TOTAL", "RB + DED", True, []),
    ("CUSTOS", "(-) Custos", "GRUPO", "", False, [
        ("CMV", "Custo das mercadorias e produtos vendidos"), ("CMV_DEVC", "Devoluções de compra")]),
    ("LB", "(=) Lucro bruto", "TOTAL", "RL + CUSTOS", False, []),
    ("DOP", "(-) Despesas operacionais", "GRUPO", "", False, [
        ("DV_COM", "Despesas com comissões"), ("DV_VEND", "Despesas com vendas e marketing"),
        ("DV_FRETE", "Fretes e logística"), ("DA_PES", "Despesas com pessoal"),
        ("DA_OCUP", "Ocupação (aluguel, energia, água)"), ("DA_ADM", "Despesas administrativas"),
        ("DA_TRIB", "Despesas tributárias")]),
    ("RO", "(=) Resultado operacional (EBITDA)", "TOTAL", "LB + DOP", False, []),
    ("RFIN", "(+/-) Resultado financeiro", "GRUPO", "", False, [
        ("RF", "Receitas financeiras"), ("DF", "Despesas financeiras e tarifas bancárias")]),
    ("OUTRAS", "(+/-) Outras receitas e despesas", "GRUPO", "", False, [
        ("ONO", "Outras receitas e despesas não operacionais")]),
    ("LAIR", "(=) Resultado antes do IR e da CSLL", "TOTAL", "RO + RFIN + OUTRAS", False, []),
    ("IRG", "(-) IRPJ e CSLL", "GRUPO", "", False, [("IR", "IRPJ e CSLL")]),
    ("LL", "(=) Lucro líquido", "TOTAL", "LAIR + IRG", False, []),
]

TOKEN = re.compile(r"\s*([+-]?)\s*([A-Za-z0-9_]+)\s*")


def _plano_padrao():
    """Lista achatada de dicts no formato do editor (grupo seguido das contas dele)."""
    out = []
    for cod, nome, tipo, form, base, contas in MODELO_PADRAO:
        out.append({"codigo": cod, "nome": nome, "tipo": tipo, "formula": form, "base_av": base,
                    "destaque": tipo == "TOTAL", "sinal": 0, "pai_codigo": None})
        out += [{"codigo": c, "nome": n, "tipo": "CONTA", "formula": "", "base_av": False, "destaque": False,
                 "sinal": 0, "pai_codigo": cod} for c, n in contas]
    return out


def criar_modelo_padrao(s, nome="DRE Padrão"):
    m = ModeloDRE(nome=nome, descricao="Modelo inicial. Crie grupos, contas e resultados à vontade.")
    s.add(m)
    s.flush()
    gravar_linhas(s, m.id, _plano_padrao(), {})
    return m


def ordenar(linhas):
    """Põe cada conta logo depois do seu grupo, na ordem em que vieram; grupos e resultados na ordem em que vieram."""
    topo = [x for x in linhas if x["tipo"] != "CONTA"]
    filhos = {}
    for x in linhas:
        if x["tipo"] == "CONTA":
            filhos.setdefault(x.get("pai_codigo"), []).append(x)
    out = []
    for t in topo:
        out.append(t)
        if t["tipo"] == "GRUPO":
            out += filhos.pop(t["codigo"], [])
    for resto in filhos.values():  # contas sem grupo válido: a validação acusa
        out += resto
    return out


def gravar_linhas(s, modelo_id, linhas, atuais):
    """Grava a lista (já validada e ordenada). `atuais` = {id: LinhaDRE} existentes; as não citadas são excluídas."""
    for ln in atuais.values():
        ln.codigo = f"__{ln.id}"
    s.flush()
    ids, mantidos = {}, set()
    for i, x in enumerate(linhas, 1):
        ln = atuais.get(int(x["id"])) if x.get("id") else None
        if not ln:
            ln = LinhaDRE(modelo_id=modelo_id)
            s.add(ln)
        ln.ordem, ln.codigo, ln.nome, ln.tipo = i * 10, x["codigo"], str(x["nome"]).strip()[:200], x["tipo"]
        ln.sinal = max(-1, min(1, int(x.get("sinal") or 0))) if x["tipo"] == "CONTA" else 0
        ln.formula = str(x.get("formula") or "").strip().upper()[:2000] if x["tipo"] == "TOTAL" else ""
        ln.destaque = bool(x.get("destaque")) or x["tipo"] == "TOTAL"
        ln.base_av = bool(x.get("base_av")) and x["tipo"] != "CONTA"
        ln.pai_id = ids.get(x.get("pai_codigo")) if x["tipo"] == "CONTA" else None
        s.flush()
        ids[ln.codigo] = ln.id
        mantidos.add(ln.id)
    for lid, ln in atuais.items():
        if lid not in mantidos:
            s.delete(ln)
    s.flush()


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
    """linhas: lista de dicts já ordenada (ordenar()). Devolve lista de erros."""
    erros, vistos = [], set()
    grupos = {x["codigo"] for x in linhas if x["tipo"] == "GRUPO"}
    for x in linhas:
        cod = x["codigo"]
        if not re.fullmatch(r"[A-Z0-9_]{1,30}", cod):
            erros.append(f"Código inválido: {cod or '(vazio)'} — use letras, números e _.")
        if cod in vistos:
            erros.append(f"Código repetido: {cod}.")
        if x["tipo"] == "CONTA" and x.get("pai_codigo") not in grupos:
            erros.append(f"A conta {cod} precisa estar dentro de um grupo.")
        if x["tipo"] == "TOTAL":
            try:
                termos = parse_formula(x.get("formula"))
                if not termos:
                    erros.append(f"{cod}: informe a fórmula do resultado.")
                for _sinal, ref in termos:
                    if ref not in vistos:
                        erros.append(f"{cod}: a fórmula usa {ref}, que não existe numa linha acima.")
            except ValueError as e:
                erros.append(f"{cod}: {e}")
        vistos.add(cod)
    return erros


# ------------------------------------------------------------------ conversão dos modelos antigos (com TITULO)
CODIGOS_ANTIGOS = {"T_REC", "RB_VENDAS", "RB_SERV", "RB_OUTRAS", "RB", "DED_DEV", "DED_IMP", "RL", "CMV", "CMV_DEVC",
                   "LB", "T_DESP", "DV_COM", "DV_VEND", "DV_FRETE", "DA_PES", "DA_OCUP", "DA_ADM", "DA_TRIB", "DOP",
                   "RO", "RF", "DF", "ONO", "LAIR", "IR", "LL"}


def converter_modelo(s, m):
    """Modelo do formato antigo (títulos e totais manuais) para grupos. Mantém os ids das contas (associações)."""
    linhas = s.scalars(select(LinhaDRE).where(LinhaDRE.modelo_id == m.id).order_by(LinhaDRE.ordem, LinhaDRE.id)).all()
    if not any(ln.tipo == "TITULO" for ln in linhas) and all(ln.tipo != "CONTA" or ln.pai_id for ln in linhas):
        return False
    atuais = {ln.id: ln for ln in linhas}
    por_cod = {ln.codigo: ln for ln in linhas}
    if {ln.codigo for ln in linhas} == CODIGOS_ANTIGOS:  # modelo padrão sem alterações de estrutura
        plano = _plano_padrao()
        for x in plano:
            velho = por_cod.get(x["codigo"])
            if velho and velho.tipo == "CONTA" and x["tipo"] == "CONTA":
                x["id"], x["nome"], x["sinal"] = velho.id, velho.nome.lstrip("(-+/=) ").strip() or x["nome"], velho.sinal
        gravar_linhas(s, m.id, plano, atuais)
        return True
    plano, run, titulo, n = [], [], None, 0

    def fechar(com_titulo):
        nonlocal run, n
        if not run:
            return
        n += 1
        g = {"codigo": com_titulo.codigo if com_titulo else f"G{n}", "nome": com_titulo.nome if com_titulo else "Grupo de contas",
             "tipo": "GRUPO", "id": com_titulo.id if com_titulo else None}
        plano.append(g)
        plano.extend({**c, "pai_codigo": g["codigo"]} for c in run)
        run = []

    def item(ln, **k):
        return {"id": ln.id, "codigo": ln.codigo, "nome": ln.nome, "tipo": ln.tipo, "sinal": ln.sinal, "formula": ln.formula,
                "destaque": ln.destaque, "base_av": ln.base_av, **k}

    for ln in linhas:
        if ln.tipo == "TITULO":
            fechar(titulo)
            titulo = ln
        elif ln.tipo == "CONTA":
            run.append(item(ln))
        else:
            try:
                termos = parse_formula(ln.formula)
            except ValueError:
                termos = []
            if run and termos and all(sg == 1 for sg, _c in termos) and {c for _s, c in termos} == {c["codigo"] for c in run}:
                plano.append(item(ln, tipo="GRUPO", formula=""))
                plano.extend({**c, "pai_codigo": ln.codigo} for c in run)
                run, titulo = [], None
            else:
                fechar(titulo)
                titulo = None
                plano.append(item(ln))
    fechar(titulo)
    usados = {x["codigo"] for x in plano}
    for x in plano:  # código de grupo gerado não pode colidir
        if x.get("id") is None and x["codigo"] in {ln.codigo for ln in linhas if ln.codigo not in usados}:
            x["codigo"] += "_G"
    gravar_linhas(s, m.id, plano, atuais)
    return True


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


def periodo_datas(ini: str, fim: str):
    d_ini = dt.date(int(ini[:4]), int(ini[5:7]), 1)
    fa, fm = int(fim[:4]), int(fim[5:7])
    return d_ini, dt.date(fa + (fm == 12), 1 if fm == 12 else fm + 1, 1) - dt.timedelta(days=1)


# sinal natural das contas da Domínio (receitas +, devolução de venda −)
SINAL_DOMINIO = {"DEV_VENDA": -1, "ICMS_TTD": -1}


def valores_origem(s, empresa_id: int, ini: str, fim: str):
    """{conta_origem_id: {mes: valor com sinal}} — Bling pelo caixa (entrada +, saída −, pela data do lançamento em
    Caixa e Bancos) e Domínio pelas notas do mês. Devolve também os totais sem categoria."""
    contas = s.scalars(select(ContaOrigem).where(ContaOrigem.empresa_id == empresa_id)).all()
    por_cod = {(c.origem, c.codigo): c for c in contas}
    vals = defaultdict(lambda: defaultdict(float))
    d_ini, d_fim = periodo_datas(ini, fim)
    sem_categoria = {"C": 0.0, "D": 0.0}
    sem_categoria["nao_lidos"] = 0
    for x in s.scalars(select(Movimento).where(Movimento.empresa_id == empresa_id, Movimento.data >= d_ini,
                                               Movimento.data <= d_fim)).all():
        if not x.categorizado:
            sem_categoria["nao_lidos"] += 1
            continue
        c = por_cod.get(("BLING", x.categoria_codigo)) if x.categoria_codigo else None
        if not c:
            sem_categoria[x.deb_cred] += float(x.valor or 0)
            continue
        vals[c.id][_mes(x.data)] += x.assinado
    for v in s.scalars(select(DominioValor).where(DominioValor.empresa_id == empresa_id, DominioValor.mes >= ini,
                                                  DominioValor.mes <= fim)).all():
        g = dominio.grupo(v.grupo, v.cfop)
        c = por_cod.get(("DOMINIO", g)) if g else None
        if c:
            vals[c.id][v.mes] += SINAL_DOMINIO.get(g, 1) * float(v.valor or 0)
    c = por_cod.get(("DOMINIO", "PIS_COFINS"))
    if c and c.linha_id:
        from db import Empresa
        e = s.get(Empresa, empresa_id)
        for x in dominio.pis_cofins(s, empresa_id, ini, fim, float(e.pis_cofins_aliq or 0)):
            vals[c.id][x["mes"]] -= x["valor"]
    c = por_cod.get(("DOMINIO", "CMV"))
    sem_custo = {"produtos": 0, "faturamento": 0.0}
    if c:
        por_mes, _p, sem_custo = dominio.cmv(s, empresa_id, ini, fim)
        for m, v in por_mes.items():
            vals[c.id][m] -= v  # custo: saída
    sem_categoria["sem_custo"] = sem_custo
    return contas, vals, sem_categoria


def calcular(s, empresa, ini: str, fim: str):
    meses = meses_entre(ini, fim)
    linhas = s.scalars(select(LinhaDRE).where(LinhaDRE.modelo_id == empresa.modelo_id).order_by(LinhaDRE.ordem, LinhaDRE.id)).all()
    contas, vals, sem_cat = valores_origem(s, empresa.id, ini, fim)
    por_linha = defaultdict(list)
    for c in contas:
        if c.linha_id:
            por_linha[c.linha_id].append(c)
    res, por_codigo, avisos = [], {}, []
    conta_val, conta_comp = {}, {}
    for ln in linhas:
        if ln.tipo != "CONTA":
            continue
        v = [0.0] * len(meses)
        comp = []
        for c in por_linha.get(ln.id, []):
            cv = vals.get(c.id, {})
            serie = [cv.get(m, 0.0) for m in meses]
            if ln.sinal:  # +1 sempre soma, -1 sempre subtrai; 0 (automático) mantém entrada + e saída −
                serie = [ln.sinal * abs(x) for x in serie]
            serie = [round(x, 2) for x in serie]
            if any(serie):
                comp.append({"conta_id": c.id, "origem": c.origem, "descricao": c.descricao, "valores": serie,
                             "total": round(sum(serie), 2)})
            v = [a + b for a, b in zip(v, serie)]
        conta_val[ln.id] = [round(x, 2) for x in v]
        conta_comp[ln.id] = sorted(comp, key=lambda x: -abs(x["total"]))
    for ln in linhas:
        v = [0.0] * len(meses)
        if ln.tipo == "CONTA":
            v = conta_val[ln.id]
        elif ln.tipo == "GRUPO":
            for f in linhas:
                if f.tipo == "CONTA" and f.pai_id == ln.id:
                    v = [a + b for a, b in zip(v, conta_val[f.id])]
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
                    "pai_id": ln.pai_id, "base_av": ln.base_av, "valores": v, "total": round(sum(v), 2),
                    "composicao": conta_comp.get(ln.id, [])})
    base = next((r for r in res if r["base_av"]), None) or next((r for r in res if r["tipo"] == "TOTAL"), None)
    for r in res:
        if not base:
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
                        "natureza": "R" if t >= 0 else "D"})
    for cod, txt in (("CMV", "Compra de mercadoria paga não é CMV"),
                     ("PIS_COFINS", "O PIS/COFINS já é calculado sobre o faturamento")):
        calc = next((c for c in contas if c.origem == "DOMINIO" and c.codigo == cod and c.linha_id), None)
        if not calc:
            continue
        juntos = [c.descricao for c in contas if c.origem == "BLING" and c.linha_id == calc.linha_id and vals.get(c.id)]
        if juntos:
            avisos.append(f"A linha que recebe o {cod.replace('_', '/')} calculado também soma pagamentos do Bling ("
                          + ", ".join(juntos[:4]) + f"). {txt}: deixe essas categorias fora da DRE ou leve-as para outra "
                          "linha, senão o valor é contado duas vezes.")
    sc = sem_cat.get("sem_custo") or {}
    if sc.get("produtos") and any(c.origem == "DOMINIO" and c.codigo == "CMV" and c.linha_id for c in contas):
        avisos.append(f"CMV incompleto: {sc['produtos']} produto(s) vendido(s) no período sem custo informado "
                      f"(faturamento de R$ {_br(sc['faturamento'])}). Informe na aba Custo dos produtos.")
    if sem_cat.get("nao_lidos"):
        avisos.append(f"{sem_cat['nao_lidos']} lançamento(s) do Bling no período não têm a categoria lida (o período não foi "
                      "atualizado por inteiro): atualize o Bling cobrindo todo o período da DRE.")
    if sem_cat["C"] or sem_cat["D"]:
        avisos.append(f"Lançamentos de caixa e bancos sem categoria no período: entradas R$ {_br(sem_cat['C'])}, "
                      f"saídas R$ {_br(sem_cat['D'])} (não entram na DRE; podem ser transferências entre contas).")
    return {"meses": meses, "linhas": res, "nao_associadas": sorted(nao, key=lambda x: -abs(x["total"])),
            "avisos": avisos, "base_av": base["codigo"] if base else None, "regime": "caixa"}


def _br(v):
    return f"{v:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")
