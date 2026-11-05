"""Fluxo completo com Bling e Central simulados. Rodar: python -m pytest -q tests/test_integra.py"""
import io
import os
import sys
import json
import tempfile
from pathlib import Path
from urllib.parse import parse_qs, urlparse

RAIZ = Path(__file__).resolve().parents[1]
os.environ["DATABASE_URL"] = f"sqlite:///{Path(tempfile.mkdtemp()) / 'ib.db'}"
os.environ["INTEGRA_SEM_THREADS"] = "1"
os.environ["SECRET_KEY"] = "teste-" + "x" * 40
sys.path.insert(0, str(RAIZ))

import httpx  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402
import main  # noqa: E402
import bling  # noqa: E402
import dominio  # noqa: E402
from db import SessionLocal, Job  # noqa: E402

bling.INTERVALO = 0

CATEGORIAS = [{"id": 10, "idCategoriaPai": 0, "descricao": "Vendas", "tipo": 2},
              {"id": 20, "idCategoriaPai": 0, "descricao": "Despesas comerciais", "tipo": 1},
              {"id": 21, "idCategoriaPai": 20, "descricao": "Comissão de vendedores", "tipo": 1},
              {"id": 22, "idCategoriaPai": 20, "descricao": "Tarifa de comissão marketplace", "tipo": 1},
              {"id": 30, "idCategoriaPai": 0, "descricao": "Tarifas bancárias", "tipo": 1}]
# id: (categoria, data, debCred, valor)
CAIXA = {1: (10, "2026-01-15", "C", 5000.0), 2: (21, "2026-01-10", "D", 300.0), 3: (22, "2026-01-20", "D", 120.5),
         4: (30, "2026-02-05", "D", 45.0), 5: (21, "2026-02-12", "C", 20.0),  # 5 = estorno de comissão
         6: (None, "2026-03-01", "D", 1000.0)}  # transferência sem categoria
PAIS = {c["id"]: c["idCategoriaPai"] for c in CATEGORIAS}
chamadas = {"token": 0, "caixas": 0}


def bling_falso(req: httpx.Request):
    url = str(req.url)
    if url.endswith("/oauth/token"):
        chamadas["token"] += 1
        corpo = parse_qs(req.content.decode())
        assert req.headers["authorization"].startswith("Basic ")
        assert corpo["grant_type"][0] in ("authorization_code", "refresh_token")
        return httpx.Response(200, json={"access_token": f"acc{chamadas['token']}", "refresh_token": "ref",
                                         "expires_in": 21600})
    assert req.headers["authorization"].startswith("Bearer acc")
    p = urlparse(url)
    q = parse_qs(p.query)
    pag = int(q.get("pagina", ["1"])[0])
    if p.path.endswith("/categorias/receitas-despesas"):
        return httpx.Response(200, json={"data": CATEGORIAS if pag == 1 else []})
    if p.path.endswith("/caixas"):
        chamadas["caixas"] += 1
        assert q["situacao"] == ["R"] and q["dataInicial"] == ["2026-01-01"]
        filtro = {int(x) for x in q.get("idsCategorias[]", [])}

        def casa(cat):  # o Bling devolve também os lançamentos das categorias filhas
            while cat:
                if cat in filtro:
                    return True
                cat = PAIS.get(cat)
            return False
        lista = [{"id": i, "debCred": d[2], "valor": d[3], "data": d[1], "descricao": f"Lanç. {i}",
                  "contato": {"nome": "Fulano"}, "contaFinanceira": {"descricao": "Banco X"}, "origem": {"tipo": "bordero"}}
                 for i, d in CAIXA.items() if not filtro or casa(d[0])]
        return httpx.Response(200, json={"data": lista if pag == 1 else []})
    return httpx.Response(404)


def test_classificar_ambiguo():
    todos = {1: {}, 2: {}, 3: {}}
    final, amb = bling.classificar(todos, {"A": {1, 2}, "B": {2}, "C": {3}, "D": {3}}, {"B": "A"})
    assert final == {1: "A", 2: "B"} and amb == [3]


def central_falsa(req: httpx.Request):
    assert req.headers["x-api-key"] == "f360_chave_de_teste_integra"
    if req.url.path == "/api/integra/clientes":
        return httpx.Response(200, json={"itens": [{"id": 1, "codigo_dominio": "77", "cnpj": "12345678000190",
                                                    "razao_social": "ALFA LTDA", "nome_fantasia": "Alfa"}]})
    if req.method == "POST":
        return httpx.Response(200, json={"tarefa_id": 9, "status": "PENDENTE", "agente": {"online": True}})
    return httpx.Response(200, json={"status": "CONCLUIDA", "erro": "", "resultado": {
        "mensagem": "ok", "avisos": [],
        "saidas": [{"mes": "2026-01", "cfop": 5102, "valor": 8000, "qtd": 4}, {"mes": "2026-01", "cfop": 5202, "valor": 200, "qtd": 1},
                   {"mes": "2026-02", "cfop": 5949, "valor": 50, "qtd": 1}],
        "entradas": [{"mes": "2026-02", "cfop": 1202, "valor": 300, "qtd": 1}, {"mes": "2026-02", "cfop": 1102, "valor": 4000, "qtd": 2}],
        "servicos": [{"mes": "2026-02", "valor": 1000, "qtd": 1}],
        "itens_saida": [{"mes": "2026-01", "cfop": 5102, "produto": "100", "qtd": 10, "valor": 6000},
                        {"mes": "2026-01", "cfop": 5102, "produto": "200", "qtd": 4, "valor": 2000},
                        {"mes": "2026-01", "cfop": 5202, "produto": "300", "qtd": 1, "valor": 200}],  # devolução de compra: não é CMV
        "itens_entrada": [{"mes": "2026-02", "cfop": 1202, "produto": "100", "qtd": 1, "valor": 300}],
        "produtos": [{"produto": "100", "codigo": "CAM-001", "descricao": "Camiseta", "unidade": "UN"},
                     {"produto": "200", "codigo": "BON-9", "descricao": "Boné", "unidade": "UN"}]}})


def test_fluxo_completo():
    with TestClient(main.app) as c:
        assert c.get("/api/estado").json()["precisa_setup"] is True
        assert c.post("/api/setup", json={"nome": "Adm", "email": "adm@x.com", "senha": "curta"}).status_code == 400
        assert c.post("/api/setup", json={"nome": "Adm", "email": "adm@x.com", "senha": "senha-forte-1"}).status_code == 200
        assert c.post("/api/setup", json={"email": "b@x.com", "senha": "senha-forte-1"}).status_code == 400
        cfg = c.put("/api/config", json={"bling_client_id": "cid", "bling_client_secret": "sec",
                                         "central_url": "https://central.exemplo", "central_chave": "f360_chave_de_teste_integra"}).json()
        assert cfg["bling_secret_definido"] and cfg["redirect_uri"].endswith("/bling/callback")
        modelos = c.get("/api/modelos").json()
        assert len(modelos) == 1 and any(ln["codigo"] == "LL" for ln in modelos[0]["linhas"])
        e = c.post("/api/empresas", json={"nome": "Alfa", "cnpj": "12.345.678/0001-90", "codigo_dominio": "77"}).json()
        assert e["modelo_id"] == modelos[0]["id"] and e["contas"]["DOMINIO"]["associadas"] == 5  # sugestões da Domínio (com o CMV)
        assert c.post("/api/empresas", json={"nome": "Dup", "cnpj": "12345678000190"}).status_code == 400

        # OAuth
        u = c.post(f"/api/empresas/{e['id']}/bling/conectar").json()["url"]
        q = parse_qs(urlparse(u).query)
        assert q["client_id"] == ["cid"] and q["response_type"] == ["code"]
        orig = httpx.Client
        bling_http = orig(transport=httpx.MockTransport(bling_falso))
        bling.httpx.Client = lambda **kw: bling_http
        try:
            r = c.get(f"/bling/callback?code=abc&state={q['state'][0]}", follow_redirects=False)
            assert r.status_code == 307 and "bling=ok" in r.headers["location"]
            assert c.get(f"/bling/callback?code=abc&state=falso", follow_redirects=False).headers["location"].find("erro") > 0
            # sincronização
            assert c.post(f"/api/empresas/{e['id']}/bling/sincronizar", json={"inicio": "2026-01-01", "fim": "2026-03-31"}).status_code == 200
            jid = c.get("/api/jobs").json()[0]["id"]
            bling.sincronizar(jid, bling.Cliente(e["id"], bling_http))
            j = c.get(f"/api/jobs/{jid}").json()
            assert j["status"] == "OK", j["mensagem"]
            assert "6 lançamentos" in j["mensagem"] and "1 sem categoria" in j["mensagem"]
            # sincronizar de novo o mesmo período substitui (não duplica)
            with SessionLocal() as s:
                j2 = Job(empresa_id=e["id"], tipo="bling_sync", parametros=json.dumps({"inicio": "2026-01-01", "fim": "2026-03-31"}))
                s.add(j2)
                s.commit()
                jid2 = j2.id
            bling.sincronizar(jid2, bling.Cliente(e["id"], bling_http))
            with SessionLocal() as s:
                from db import Movimento
                assert s.query(Movimento).count() == 6
        finally:
            bling.httpx.Client = orig

        # Domínio pela Central
        central_http = httpx.Client(transport=httpx.MockTransport(central_falsa))
        orig_d = dominio.httpx.Client
        dominio.httpx.Client = lambda **kw: central_http
        try:
            assert c.get("/api/central/clientes?q=alfa").json()[0]["codigo_dominio"] == "77"
            r = c.post(f"/api/empresas/{e['id']}/dominio/importar", json={"inicio": "2026-01", "fim": "2026-03"}).json()
            dominio.importar(r["id"], central_http, espera=0)
            assert c.get(f"/api/jobs/{r['id']}").json()["status"] == "OK"
        finally:
            dominio.httpx.Client = orig_d

        # custo dos produtos e CMV
        pr = c.get(f"/api/empresas/{e['id']}/produtos?inicio=2026-01&fim=2026-03").json()
        P = {x["codigo"]: x for x in pr["itens"]}
        assert P["CAM-001"]["qtd_vendida"] == 10 and P["CAM-001"]["qtd_devolvida"] == 1 and P["CAM-001"]["custo"] is None
        assert pr["sem_custo"]["produtos"] == 2
        cam = P["CAM-001"]["id"]
        assert c.put(f"/api/produtos/{cam}", json={"custo": "25,00", "vigencia": "2026-01"}).json()["custo"] == 25
        assert c.put(f"/api/produtos/{cam}", json={"custo": "-1", "vigencia": "2026-01"}).status_code == 400
        assert c.put(f"/api/produtos/{cam}", json={"custo": "25"}).status_code == 400  # vigência obrigatória
        d0 = c.get(f"/api/empresas/{e['id']}/dre?inicio=2026-01&fim=2026-03").json()
        assert any("CMV incompleto: 1 produto" in a for a in d0["avisos"])
        # importar custo pela planilha baixada (vigência janeiro)
        import base64
        from openpyxl import load_workbook
        x = c.get(f"/api/empresas/{e['id']}/produtos.xlsx?inicio=2026-01&fim=2026-03&vigencia=2026-01")
        wb = load_workbook(io.BytesIO(x.content))
        ws = wb["Custos"]
        assert ws.cell(2, 5).value == "2026-01"
        for row in ws.iter_rows(min_row=2):
            if row[1].value == "BON-9":
                row[5].value = 50
        buf = io.BytesIO()
        wb.save(buf)
        r = c.post(f"/api/empresas/{e['id']}/produtos/importar",
                   json={"arquivo": base64.b64encode(buf.getvalue()).decode(), "vigencia": "2026-03"}).json()
        assert r["atualizados"] == 2 and r["meses"] == ["2026-01"] and r["nao_encontrados"] == []
        cm = c.get(f"/api/empresas/{e['id']}/produtos?inicio=2026-01&fim=2026-03").json()
        assert cm["totais"]["cmv"] == 10 * 25 + 4 * 50 - 25 and cm["sem_custo"]["produtos"] == 0
        # custo novo a partir de fevereiro: a devolução de fevereiro usa 30, janeiro continua 25
        assert c.put(f"/api/produtos/{cam}", json={"custo": "30", "vigencia": "2026-02"}).json()["custo"] == 30
        cm = c.get(f"/api/empresas/{e['id']}/produtos?inicio=2026-01&fim=2026-03&vigencia=2026-01").json()
        CM = {x["codigo"]: x for x in cm["itens"]}
        assert cm["totais"]["cmv"] == 10 * 25 + 4 * 50 - 30 and CM["CAM-001"]["custo"] == 25 and CM["CAM-001"]["custo_desde"] == "2026-01"
        h = c.get(f"/api/produtos/{cam}/custos").json()["custos"]
        assert [x["mes_inicio"] for x in h] == ["2026-02", "2026-01"]
        # custo só a partir de março para um produto vendido em janeiro = sem custo em janeiro
        bon = P["BON-9"]["id"]
        hb = c.get(f"/api/produtos/{bon}/custos").json()["custos"]
        assert c.delete(f"/api/produtos/{bon}/custos/{hb[0]['id']}").json()["ok"]
        c.put(f"/api/produtos/{bon}", json={"custo": "50", "vigencia": "2026-03"})
        cm = c.get(f"/api/empresas/{e['id']}/produtos?inicio=2026-01&fim=2026-03").json()
        assert cm["sem_custo"]["produtos"] == 1 and {x["codigo"]: x for x in cm["itens"]}["BON-9"]["meses_sem_custo"] == ["2026-01"]
        c.put(f"/api/produtos/{bon}", json={"custo": "50", "vigencia": "2026-01"})

        # associação
        ct = c.get(f"/api/empresas/{e['id']}/contas?inicio=2026-01&fim=2026-03").json()["contas"]
        por = {x["descricao"]: x for x in ct}
        assert por["Comissão de vendedores"]["nome"] == "Despesas comerciais › Comissão de vendedores"
        assert por["Comissão de vendedores"]["total"] == -280  # saída 300, estorno +20
        linhas = {ln["codigo"]: ln["id"] for ln in modelos[0]["linhas"]}
        itens = [{"conta_id": por["Comissão de vendedores"]["id"], "linha_id": linhas["DV_COM"]},
                 {"conta_id": por["Tarifa de comissão marketplace"]["id"], "linha_id": linhas["DV_COM"]},
                 {"conta_id": por["Tarifas bancárias"]["id"], "linha_id": linhas["DF"]}]
        assert c.post(f"/api/empresas/{e['id']}/contas/lote", json={"itens": itens}).json()["alteradas"] == 3
        assert c.put(f"/api/contas/{por['Vendas']['id']}", json={"linha_id": linhas["RB"]}).status_code == 400  # total
        det = c.get(f"/api/contas/{por['Comissão de vendedores']['id']}/detalhe?inicio=2026-01&fim=2026-03").json()
        assert len(det["itens"]) == 2 and det["total"] == -280 and det["itens"][0]["conta_financeira"] == "Banco X"
        detd = c.get(f"/api/contas/{por['Vendas de mercadorias e produtos (NF de saída)']['id']}/detalhe?inicio=2026-01&fim=2026-03").json()
        assert detd["itens"][0]["cfop"] == 5102 and detd["total"] == 8000

        # DRE
        d = c.get(f"/api/empresas/{e['id']}/dre?inicio=2026-01&fim=2026-03").json()
        L = {x["codigo"]: x for x in d["linhas"]}
        assert L["RB_VENDAS"]["valores"] == [8000, 0, 0]
        assert L["RB_SERV"]["total"] == 1000
        assert L["RB"]["total"] == 9000
        assert L["DED_DEV"]["total"] == -300
        assert L["RL"]["total"] == 8700 and L["RL"]["av_total"] == 100
        assert L["CMV_DEVC"]["total"] == 200
        assert L["DV_COM"]["valores"] == [-420.5, 20, 0]
        assert len(L["DV_COM"]["composicao"]) == 2
        assert L["DF"]["total"] == -45
        assert L["CMV"]["valores"] == [-450, 30, 0]
        assert L["LL"]["total"] == round(8700 + 200 - 420 - 400.5 - 45, 2)
        ct2 = {x["descricao"]: x for x in c.get(f"/api/empresas/{e['id']}/contas").json()["contas"]}
        dv = c.get(f"/api/contas/{ct2['Vendas de mercadorias e produtos (NF de saída)']['id']}/detalhe?inicio=2026-01&fim=2026-03").json()
        assert [p["codigo"] for p in dv["produtos"]] == ["CAM-001", "BON-9"]
        dc = c.get(f"/api/contas/{ct2['CMV calculado (vendas − devoluções de venda, × custo do produto)']['id']}/detalhe?inicio=2026-01&fim=2026-03").json()
        assert dc["tipo"] == "cmv" and dc["total"] == -420
        assert any(x["descricao"] == "Vendas" for x in d["nao_associadas"])  # recebimentos de vendas ficam de fora (Domínio já traz)
        assert any("sem categoria" in a and "1.000,00" in a for a in d["avisos"])
        x = c.get(f"/api/empresas/{e['id']}/dre.xlsx?inicio=2026-01&fim=2026-03")
        assert x.status_code == 200 and x.content[:2] == b"PK"

        # modelo: editar, validar fórmula, duplicar, copiar associações
        m = c.get(f"/api/modelos/{modelos[0]['id']}").json()
        m["linhas"].append({"codigo": "X", "nome": "Teste", "tipo": "TOTAL", "formula": "LL + NAOEXISTE"})
        assert c.put(f"/api/modelos/{m['id']}", json=m).status_code == 400
        m["linhas"][-1]["formula"] = "LL - IR"
        m["linhas"][-1]["codigo"] = "LL2"
        assert c.put(f"/api/modelos/{m['id']}", json=m).status_code == 200
        assert c.get(f"/api/empresas/{e['id']}/dre?inicio=2026-01&fim=2026-03").json()["linhas"][-1]["codigo"] == "LL2"
        # as associações continuam depois de salvar o modelo
        assert c.get(f"/api/empresas/{e['id']}/dre?inicio=2026-01&fim=2026-03").json()["linhas"][12]["total"] == -400.5
        e2 = c.post("/api/empresas", json={"nome": "Beta"}).json()
        assert c.post("/api/modelos", json={"nome": "Cópia", "copiar_de": m["id"]}).json()["linhas"][-1]["codigo"] == "LL2"
        assert c.post(f"/api/empresas/{e2['id']}/contas/copiar", json={"de_empresa_id": e["id"]}).json()["ok"]
        assert c.delete(f"/api/modelos/{m['id']}").status_code == 400  # em uso

        # usuários
        assert c.post("/api/usuarios", json={"nome": "Op", "email": "op@x.com", "senha": "senha-forte-2"}).status_code == 200
        c.post("/api/logout")
        assert c.get("/api/empresas").status_code == 401
        assert c.post("/api/login", json={"email": "op@x.com", "senha": "senha-forte-2"}).status_code == 200
        assert c.get("/api/config").status_code == 403
        assert c.get("/api/empresas").status_code == 200
