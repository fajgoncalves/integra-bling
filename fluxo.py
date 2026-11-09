"""Fluxo de caixa realizado — fiel ao Bling (Caixa e Bancos).

Saldo inicial de cada conta = soma de todos os lançamentos registrados antes do mês (entradas − saídas),
movimentos do mês pelas categorias do Bling (mesma hierarquia), lançamentos sem categoria à parte
(normalmente transferências entre contas) e saldo final = saldo inicial + variação.
"""
import datetime as dt
from collections import defaultdict

from sqlalchemy import select, func

from db import ContaOrigem, Movimento, ContaFinanceira
import dre

SECOES = ("ENTRADAS", "SAIDAS")
NOME_SECAO = {"ENTRADAS": "ENTRADAS", "SAIDAS": "SAÍDAS"}


def secao_padrao(c: ContaOrigem, total: float) -> str:
    if c.fluxo_secao in SECOES:
        return c.fluxo_secao
    if c.tipo == "R":
        return "ENTRADAS"
    if c.tipo == "D":
        return "SAIDAS"
    return "ENTRADAS" if total >= 0 else "SAIDAS"


def calcular(s, empresa_id: int, ini: str, fim: str):
    meses = dre.meses_entre(ini, fim)
    d_ini, d_fim = dre.periodo_datas(ini, fim)
    n = len(meses)
    idx = {m: i for i, m in enumerate(meses)}
    avisos = []

    # contas financeiras
    cfs = s.scalars(select(ContaFinanceira).where(ContaFinanceira.empresa_id == empresa_id)
                    .order_by(ContaFinanceira.ordem, ContaFinanceira.descricao)).all()
    nome_conta = {c.id_externo: c.descricao for c in cfs}
    ordem_conta = [c.id_externo for c in cfs]

    # saldo antes do período, por conta
    saldo_ini = defaultdict(float)
    for conta, dc, total in s.execute(select(Movimento.conta_id, Movimento.deb_cred, func.sum(Movimento.valor))
                                      .where(Movimento.empresa_id == empresa_id, Movimento.data < d_ini)
                                      .group_by(Movimento.conta_id, Movimento.deb_cred)).all():
        saldo_ini[conta or 0] += float(total or 0) * (1 if dc == "C" else -1)

    # movimentos do período
    cat_mes = defaultdict(lambda: [0.0] * n)
    conta_mes = defaultdict(lambda: [0.0] * n)
    sem_cat = {"C": [0.0] * n, "D": [0.0] * n}
    nao_lidos = 0
    for x in s.scalars(select(Movimento).where(Movimento.empresa_id == empresa_id, Movimento.data >= d_ini,
                                               Movimento.data <= d_fim)).all():
        i = idx[f"{x.data.year:04d}-{x.data.month:02d}"]
        v = x.assinado
        conta_mes[x.conta_id or 0][i] += v
        if not x.categorizado:
            nao_lidos += 1
            sem_cat[x.deb_cred][i] += v
        elif x.categoria_codigo:
            cat_mes[x.categoria_codigo][i] += v
        else:
            sem_cat[x.deb_cred][i] += v
    if nao_lidos:
        avisos.append(f"{nao_lidos} lançamento(s) do período foram lidos só para o saldo, sem a categoria: "
                      "atualize o Bling cobrindo todo o período para ver as categorias.")
    if any(k == 0 for k in list(saldo_ini) + list(conta_mes)):
        avisos.append("Há lançamentos sem conta bancária identificada (de atualizações anteriores à versão do fluxo): "
                      "atualize o Bling para separar os saldos por conta.")

    # árvore de categorias (como no Bling)
    cats = s.scalars(select(ContaOrigem).where(ContaOrigem.empresa_id == empresa_id, ContaOrigem.origem == "BLING")).all()
    por_cod = {c.codigo: c for c in cats}
    filhos = defaultdict(list)
    raizes = []
    for c in cats:
        if c.pai_codigo and c.pai_codigo in por_cod:
            filhos[c.pai_codigo].append(c)
        else:
            raizes.append(c)
    total_no = {}

    def soma(c):
        v = list(cat_mes.get(c.codigo, [0.0] * n))
        for f in filhos.get(c.codigo, []):
            v = [a + b for a, b in zip(v, soma(f))]
        total_no[c.codigo] = v
        return v

    for r in raizes:
        soma(r)

    def ordenar(lista):
        return sorted(lista, key=lambda c: (c.fluxo_ordem or 0, c.descricao.lower()))

    linhas = []

    def r2(v):
        return [round(x, 2) for x in v]

    def add(tipo, nome, valores, nivel=0, **k):
        linhas.append({"tipo": tipo, "nome": nome, "nivel": nivel, "valores": r2(valores),
                       "total": round(sum(valores), 2), **k})

    # saldo inicial (mês a mês) por conta
    contas_todas = ordem_conta + [k for k in set(saldo_ini) | set(conta_mes) if k not in ordem_conta]
    ini_conta, fim_conta = {}, {}
    for k in contas_todas:
        acc, vi, vf = saldo_ini.get(k, 0.0), [], []
        for i in range(n):
            vi.append(acc)
            acc += conta_mes[k][i]
            vf.append(acc)
        ini_conta[k], fim_conta[k] = vi, vf
    visiveis = [k for k in contas_todas if any(abs(x) > 0.004 for x in ini_conta[k] + fim_conta[k])]

    def nome_cf(k):
        return nome_conta.get(k) or ("Conta não identificada" if not k else f"Conta {k}")

    tot_ini = [sum(ini_conta[k][i] for k in contas_todas) for i in range(n)]
    linhas.append({"tipo": "SALDO_INI", "nome": "Saldo inicial", "nivel": 0, "valores": r2(tot_ini),
                   "total": round(tot_ini[0], 2) if n else 0})
    for k in visiveis:
        linhas.append({"tipo": "SALDO_CONTA_INI", "nome": nome_cf(k), "nivel": 1, "valores": r2(ini_conta[k]),
                       "total": round(ini_conta[k][0], 2) if n else 0, "conta_financeira": k})

    secoes = {sec: [] for sec in SECOES}
    for r in raizes:
        secoes[secao_padrao(r, sum(total_no[r.codigo]))].append(r)

    def arvore(c, nivel, sec):
        v = total_no[c.codigo]
        tem_mov = any(abs(x) > 0.004 for x in v)
        add("CATEGORIA", c.descricao, v, nivel, conta_id=c.id, codigo=c.codigo, secao=sec, pai=c.pai_codigo or None,
            ordem=c.fluxo_ordem or 0, ativa=c.ativa, tem_mov=tem_mov, raiz=nivel == 1,
            filhos=len(filhos.get(c.codigo, [])), proprio=r2(cat_mes.get(c.codigo, [0.0] * n)))
        for f in ordenar(filhos.get(c.codigo, [])):
            arvore(f, nivel + 1, sec)

    tot_sec = {}
    for sec in SECOES:
        v = [0.0] * n
        for r in secoes[sec]:
            v = [a + b for a, b in zip(v, total_no[r.codigo])]
        tot_sec[sec] = v
        add("SECAO", NOME_SECAO[sec], v, 0, secao=sec)
        for r in ordenar(secoes[sec]):
            arvore(r, 1, sec)
    sc_total = [a + b for a, b in zip(sem_cat["C"], sem_cat["D"])]
    add("SECAO", "SEM CATEGORIA (transferências e outros)", sc_total, 0, secao="SEM")
    add("SEMCAT", "Entradas sem categoria", sem_cat["C"], 1, deb_cred="C")
    add("SEMCAT", "Saídas sem categoria", sem_cat["D"], 1, deb_cred="D")
    variacao = [a + b + c for a, b, c in zip(tot_sec["ENTRADAS"], tot_sec["SAIDAS"], sc_total)]
    add("VARIACAO", "(=) Variação do caixa no mês", variacao, 0)
    tot_fim = [sum(fim_conta[k][i] for k in contas_todas) for i in range(n)]
    linhas.append({"tipo": "SALDO_FIM", "nome": "Saldo final", "nivel": 0, "valores": r2(tot_fim),
                   "total": round(tot_fim[-1], 2) if n else 0})
    for k in visiveis:
        linhas.append({"tipo": "SALDO_CONTA_FIM", "nome": nome_cf(k), "nivel": 1, "valores": r2(fim_conta[k]),
                       "total": round(fim_conta[k][-1], 2) if n else 0, "conta_financeira": k})
    # conferência: saldo final = inicial + variação (sempre fecha, por construção)
    dif = [round(f - i - v, 2) for f, i, v in zip(tot_fim, tot_ini, variacao)]
    if any(abs(x) > 0.01 for x in dif):
        avisos.append("Diferença na conferência do saldo: " + ", ".join(f"{m}: {d}" for m, d in zip(meses, dif) if d))
    return {"meses": meses, "linhas": linhas, "avisos": avisos,
            "contas": [{"id": k, "nome": nome_cf(k)} for k in visiveis]}
