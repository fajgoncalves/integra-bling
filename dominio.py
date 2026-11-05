"""Domínio (via Central de Inteligência 360 → Agente 360): faturamento e devoluções por mês e CFOP.

A Central enfileira a tarefa `dre_dominio`; o Agente 360, no servidor da Domínio, lê o banco
(somente leitura) e devolve os totais por CFOP. Aqui os CFOPs viram as contas de origem
"DOMINIO", que o usuário associa às linhas da DRE como qualquer categoria do Bling.
"""
import json
import time

import httpx
from sqlalchemy import select, delete

from db import SessionLocal, Empresa, ContaOrigem, DominioValor, DominioItem, Produto, ProdutoCusto, LinhaDRE, Job, agora
from seguranca import get_config, decifrar

# contas de origem da Domínio (código, descrição, tipo)
GRUPOS = [
    ("VENDAS", "Vendas de mercadorias e produtos (NF de saída)", "R"),
    ("SERVICOS", "Serviços prestados (NFS-e e CFOP x.933)", "R"),
    ("DEV_VENDA", "Devoluções de venda (NF de entrada)", "D"),
    ("DEV_COMPRA", "Devoluções de compra (NF de saída)", "R"),
    ("OUTRAS_SAIDAS", "Outras saídas (remessas, transferências, ativo etc.)", "R"),
    ("CMV", "CMV calculado (vendas − devoluções de venda, × custo do produto)", "D"),
]
# sugestão de associação das contas da Domínio no modelo padrão
SUGESTAO = {"VENDAS": "RB_VENDAS", "SERVICOS": "RB_SERV", "DEV_VENDA": "DED_DEV", "DEV_COMPRA": "CMV_DEVC", "CMV": "CMV"}
NOMES = {g[0]: g[1] for g in GRUPOS}

DEVOLUCAO_VENDA = {1201, 1202, 1203, 1204, 1410, 1411, 2201, 2202, 2203, 2204, 2410, 2411, 3201, 3202}
DEV_COMPRA_FINAL = set(range(201, 213)) | {410, 411, 412, 413, 503, 553, 555, 556, 660, 661, 662}


def grupo_saida(cfop: int) -> str:
    if not cfop:
        return "VENDAS"
    if cfop // 1000 not in (5, 6, 7):
        return "OUTRAS_SAIDAS"
    r = cfop % 1000
    if r in DEV_COMPRA_FINAL:
        return "DEV_COMPRA"
    if r in (932, 933):
        return "SERVICOS"
    if 100 <= r < 200 or 400 <= r < 500 or 650 <= r < 660:
        return "VENDAS"
    return "OUTRAS_SAIDAS"


def grupo(dv_grupo: str, cfop: int) -> str | None:
    """Conta de origem da Domínio para um total (None = não entra na DRE)."""
    if dv_grupo == "SERVICO":
        return "SERVICOS"
    if dv_grupo == "SAIDA":
        return grupo_saida(cfop)
    if dv_grupo == "ENTRADA" and cfop in DEVOLUCAO_VENDA:
        return "DEV_VENDA"
    return None


def garantir_contas(s, empresa_id: int):
    tem = {c.codigo for c in s.scalars(select(ContaOrigem).where(
        ContaOrigem.empresa_id == empresa_id, ContaOrigem.origem == "DOMINIO")).all()}
    for cod, desc, tipo in GRUPOS:
        if cod not in tem:
            s.add(ContaOrigem(empresa_id=empresa_id, origem="DOMINIO", codigo=cod, descricao=desc, tipo=tipo))


def aplicar_sugestoes(s, e: Empresa):
    """Cria as contas da Domínio e associa às linhas sugeridas do modelo, quando ainda não associadas."""
    garantir_contas(s, e.id)
    s.flush()
    if not e.modelo_id:
        return
    linhas = {ln.codigo: ln.id for ln in s.scalars(select(LinhaDRE).where(LinhaDRE.modelo_id == e.modelo_id,
                                                                          LinhaDRE.tipo == "CONTA")).all()}
    for c in s.scalars(select(ContaOrigem).where(ContaOrigem.empresa_id == e.id, ContaOrigem.origem == "DOMINIO")).all():
        sug = SUGESTAO.get(c.codigo)
        if not c.linha_id and sug in linhas:
            c.linha_id = linhas[sug]


def eh_venda(cfop: int) -> bool:
    return grupo_saida(cfop) == "VENDAS"


def vigencias(s, empresa_id: int):
    """{produto(codi_pdi): [(mes_inicio, custo), ...] em ordem}"""
    out = {}
    for pr, mes, c in s.execute(select(Produto.produto, ProdutoCusto.mes_inicio, ProdutoCusto.custo)
                                .join(ProdutoCusto, ProdutoCusto.produto_id == Produto.id)
                                .where(Produto.empresa_id == empresa_id).order_by(ProdutoCusto.mes_inicio)).all():
        out.setdefault(pr, []).append((mes, float(c)))
    return out


def custo_no_mes(lista, mes):
    """Custo vigente no mês: a última vigência com início até o mês (None se não houver)."""
    atual = None
    for ini, c in lista or []:
        if ini <= mes:
            atual = c
        else:
            break
    return atual


def cmv(s, empresa_id: int, ini: str, fim: str, mes_ref: str | None = None):
    """CMV por mês e por produto: (quantidade vendida − quantidade devolvida) × custo vigente no mês.
    Devolve {mes: cmv}, {produto: {...}} e os produtos vendidos sem custo em algum mês."""
    cad = {p.produto: p for p in s.scalars(select(Produto).where(Produto.empresa_id == empresa_id)).all()}
    vig = vigencias(s, empresa_id)
    mes_ref = mes_ref or fim
    por_mes, por_prod = {}, {}
    for it in s.scalars(select(DominioItem).where(DominioItem.empresa_id == empresa_id, DominioItem.mes >= ini,
                                                  DominioItem.mes <= fim)).all():
        if it.lado == "S" and eh_venda(it.cfop):
            sinal = 1
        elif it.lado == "E" and it.cfop in DEVOLUCAO_VENDA:
            sinal = -1
        else:
            continue
        p = cad.get(it.produto)
        x = por_prod.setdefault(it.produto, {"produto": it.produto, "codigo": p.codigo if p else it.produto,
                                             "descricao": p.descricao if p else "", "unidade": p.unidade if p else "",
                                             "custo": custo_no_mes(vig.get(it.produto), mes_ref),
                                             "qtd_vendida": 0.0, "valor_vendido": 0.0, "qtd_devolvida": 0.0,
                                             "valor_devolvido": 0.0, "cmv": 0.0, "meses_sem_custo": set(),
                                             "faturamento_sem_custo": 0.0, "id": p.id if p else None})
        q, v = float(it.qtd or 0), float(it.valor or 0)
        if sinal > 0:
            x["qtd_vendida"] += q
            x["valor_vendido"] += v
        else:
            x["qtd_devolvida"] += q
            x["valor_devolvido"] += v
        cu = custo_no_mes(vig.get(it.produto), it.mes)
        if cu is None:
            if sinal > 0:
                x["meses_sem_custo"].add(it.mes)
                x["faturamento_sem_custo"] += v
            continue
        c = sinal * q * cu
        x["cmv"] += c
        por_mes[it.mes] = por_mes.get(it.mes, 0.0) + c
    sem_custo = {"produtos": 0, "faturamento": 0.0}
    for x in por_prod.values():
        x["cmv"] = round(x["cmv"], 2)
        x["meses_sem_custo"] = sorted(x["meses_sem_custo"])
        if x["meses_sem_custo"]:
            sem_custo["produtos"] += 1
            sem_custo["faturamento"] += x["faturamento_sem_custo"]
    sem_custo["faturamento"] = round(sem_custo["faturamento"], 2)
    return {m: round(v, 2) for m, v in por_mes.items()}, por_prod, sem_custo


def central(s):
    url = get_config(s, "central_url").rstrip("/")
    chave = decifrar(get_config(s, "central_chave"))
    if not url or not chave:
        raise RuntimeError("Configure o endereço e a chave da Central 360 em Configurações.")
    return url, {"X-API-Key": chave, "User-Agent": "IntegraBling/1.0"}


def gravar_resultado(s, empresa_id: int, res: dict, ini_mes: str, fim_mes: str):
    s.execute(delete(DominioValor).where(DominioValor.empresa_id == empresa_id, DominioValor.mes >= ini_mes,
                                         DominioValor.mes <= fim_mes))
    soma = {}
    for nome, lista in (("SAIDA", res.get("saidas") or []), ("ENTRADA", res.get("entradas") or [])):
        for x in lista:
            k = (x["mes"], nome, int(x.get("cfop") or 0))
            v = soma.setdefault(k, [0.0, 0])
            v[0] += float(x.get("valor") or 0)
            v[1] += int(x.get("qtd") or 0)
    for x in res.get("servicos") or []:
        v = soma.setdefault((x["mes"], "SERVICO", 0), [0.0, 0])
        v[0] += float(x.get("valor") or 0)
        v[1] += int(x.get("qtd") or 0)
    for (mes, g, cfop), (v, q) in soma.items():
        if mes and ini_mes <= mes <= fim_mes:
            s.add(DominioValor(empresa_id=empresa_id, mes=mes, grupo=g, cfop=cfop, valor=round(v, 2), qtd=q))
    # itens das notas (produtos) — base do CMV
    s.execute(delete(DominioItem).where(DominioItem.empresa_id == empresa_id, DominioItem.mes >= ini_mes,
                                        DominioItem.mes <= fim_mes))
    itens = {}
    for lado, lista in (("S", res.get("itens_saida") or []), ("E", res.get("itens_entrada") or [])):
        for x in lista:
            k = (x["mes"], lado, int(x.get("cfop") or 0), str(x.get("produto") or "").strip()[:40])
            if not k[0] or not k[3] or not ini_mes <= k[0] <= fim_mes:
                continue
            v = itens.setdefault(k, [0.0, 0.0])
            v[0] += float(x.get("qtd") or 0)
            v[1] += float(x.get("valor") or 0)
    for (mes, lado, cfop, prod), (q, v) in itens.items():
        s.add(DominioItem(empresa_id=empresa_id, mes=mes, lado=lado, cfop=cfop, produto=prod, qtd=round(q, 4), valor=round(v, 2)))
    cad = {p.produto: p for p in s.scalars(select(Produto).where(Produto.empresa_id == empresa_id)).all()}
    info = {str(p.get("produto")): p for p in res.get("produtos") or []}
    for prod in {k[3] for k in itens}:
        p = cad.get(prod)
        if not p:
            p = Produto(empresa_id=empresa_id, produto=prod)
            s.add(p)
            cad[prod] = p
        i = info.get(prod) or {}
        p.codigo = (i.get("codigo") or p.codigo or prod)[:60]
        p.descricao = (i.get("descricao") or p.descricao or "")[:200]
        p.unidade = (i.get("unidade") or p.unidade or "")[:10]
    e = s.get(Empresa, empresa_id)
    aplicar_sugestoes(s, e)


def importar(job_id: int, http=None, espera=5, limite_s=1800):
    """Job: pede à Central e espera o Agente 360 devolver."""
    http = http or httpx.Client(timeout=60)
    with SessionLocal() as s:
        job = s.get(Job, job_id)
        p = json.loads(job.parametros or "{}")
        emp = s.get(Empresa, job.empresa_id)
        job.status, job.mensagem = "EXECUTANDO", "Pedindo os dados à Central 360..."
        s.commit()
        emp_id, cnpj, cod = emp.id, emp.cnpj, emp.codigo_dominio
    try:
        with SessionLocal() as s:
            url, h = central(s)
        r = http.post(f"{url}/api/integra/dominio/dre", headers=h,
                      json={"cnpj": cnpj, "codigo_dominio": cod, "inicio": p["inicio"], "fim": p["fim"]})
        if r.status_code >= 400:
            raise RuntimeError(f"Central respondeu {r.status_code}: {_msg(r)}")
        tid = r.json()["tarefa_id"]
        online = (r.json().get("agente") or {}).get("online")
        _andamento(job_id, f"Tarefa nº {tid} na fila da Central. "
                   + ("Aguardando o Agente 360 ler a Domínio..." if online else
                      "Atenção: o Agente 360 está offline; a leitura acontece quando ele voltar."))
        inicio = time.monotonic()
        while True:
            v = http.get(f"{url}/api/integra/dominio/dre/{tid}", headers=h)
            if v.status_code >= 400:
                raise RuntimeError(f"Central respondeu {v.status_code}: {_msg(v)}")
            v = v.json()
            if v["status"] == "CONCLUIDA":
                res = v["resultado"] or {}
                break
            if v["status"] in ("ERRO", "CANCELADA"):
                raise RuntimeError(f"Agente 360: {v.get('erro') or v['status']}")
            if time.monotonic() - inicio > limite_s:
                raise RuntimeError(f"O Agente 360 não respondeu em {limite_s // 60} minutos (tarefa nº {tid} continua na fila "
                                   "da Central). Confira se ele está ligado e peça de novo.")
            time.sleep(espera)
        with SessionLocal() as s:
            gravar_resultado(s, emp_id, res, p["inicio"], p["fim"])
            emp = s.get(Empresa, emp_id)
            emp.dominio_sync_em = agora()
            emp.dominio_sync_periodo = f"{p['inicio']} a {p['fim']}"
            j = s.get(Job, job_id)
            avisos = res.get("avisos") or []
            j.status, j.fim = "OK", agora()
            j.mensagem = (res.get("mensagem") or "Domínio lida.") + (" Avisos: " + " ".join(avisos) if avisos else "")
            s.commit()
    except Exception as e:  # noqa: BLE001
        with SessionLocal() as s:
            j = s.get(Job, job_id)
            j.status, j.fim, j.mensagem = "ERRO", agora(), str(e)[:1000]
            s.commit()


def _andamento(job_id, msg):
    with SessionLocal() as s:
        s.get(Job, job_id).mensagem = msg
        s.commit()


def _msg(r):
    try:
        return r.json().get("detail") or r.json().get("erro") or r.text[:300]
    except ValueError:
        return r.text[:300]


def clientes_central(s, http=None):
    http = http or httpx.Client(timeout=30)
    url, h = central(s)
    r = http.get(f"{url}/api/integra/clientes", headers=h)
    if r.status_code >= 400:
        raise RuntimeError(f"Central respondeu {r.status_code}: {_msg(r)}")
    return r.json()["itens"]
