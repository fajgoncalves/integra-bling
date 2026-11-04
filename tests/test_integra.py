"""Fluxo completo com Bling e Central simulados. Rodar: python -m pytest -q tests/test_integra.py"""
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
PAGAR = {101: (21, "2026-01-10", 300.0, 2), 102: (22, "2026-01-20", 120.5, 1), 103: (30, "2026-02-05", 45.0, 2),
         104: (21, "2026-02-12", 999.0, 5)}  # 104 cancelado
RECEBER = {201: (10, "2026-01-15", 5000.0, 2)}
chamadas = {"token": 0, "detalhe": 0}


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
    for base, dados, tipo in (("/contas/pagar", PAGAR, "P"), ("/contas/receber", RECEBER, "R")):
        if p.path.endswith(base):
            lista = [{"id": i, "situacao": d[3], "vencimento": d[1], "valor": d[2], "contato": {"id": 1}} for i, d in dados.items()]
            return httpx.Response(200, json={"data": lista if pag == 1 else []})
        if base + "/" in p.path:
            i = int(p.path.rsplit("/", 1)[1])
            chamadas["detalhe"] += 1
            d = dados[i]
            return httpx.Response(200, json={"data": {"id": i, "situacao": d[3], "valor": d[2], "vencimento": d[1],
                                                      "dataEmissao": d[1], "competencia": d[1], "categoria": {"id": d[0]},
                                                      "contato": {"id": 1, "nome": "Fulano"}, "historico": f"Título {i}"}})
    return httpx.Response(404)


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
        "servicos": [{"mes": "2026-02", "valor": 1000, "qtd": 1}]}})


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
        assert e["modelo_id"] == modelos[0]["id"] and e["contas"]["DOMINIO"]["associadas"] == 4  # sugestões da Domínio
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
            assert chamadas["detalhe"] == 5
            # segunda sincronização não detalha de novo o que não mudou
            with SessionLocal() as s:
                j2 = Job(empresa_id=e["id"], tipo="bling_sync", parametros=json.dumps({"inicio": "2026-01-01", "fim": "2026-03-31"}))
                s.add(j2)
                s.commit()
                jid2 = j2.id
            bling.sincronizar(jid2, bling.Cliente(e["id"], bling_http))
            assert chamadas["detalhe"] == 5
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

        # associação
        ct = c.get(f"/api/empresas/{e['id']}/contas?inicio=2026-01&fim=2026-03").json()["contas"]
        por = {x["descricao"]: x for x in ct}
        assert por["Comissão de vendedores"]["nome"] == "Despesas comerciais › Comissão de vendedores"
        assert por["Comissão de vendedores"]["total"] == 300  # o cancelado não entra
        linhas = {ln["codigo"]: ln["id"] for ln in modelos[0]["linhas"]}
        itens = [{"conta_id": por["Comissão de vendedores"]["id"], "linha_id": linhas["DV_COM"]},
                 {"conta_id": por["Tarifa de comissão marketplace"]["id"], "linha_id": linhas["DV_COM"]},
                 {"conta_id": por["Tarifas bancárias"]["id"], "linha_id": linhas["DF"]}]
        assert c.post(f"/api/empresas/{e['id']}/contas/lote", json={"itens": itens}).json()["alteradas"] == 3
        assert c.put(f"/api/contas/{por['Vendas']['id']}", json={"linha_id": linhas["RB"]}).status_code == 400  # total
        det = c.get(f"/api/contas/{por['Comissão de vendedores']['id']}/detalhe?inicio=2026-01&fim=2026-03").json()
        assert len(det["itens"]) == 2 and det["total"] == 300
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
        assert L["DV_COM"]["valores"] == [-420.5, 0, 0]
        assert len(L["DV_COM"]["composicao"]) == 2
        assert L["DF"]["total"] == -45
        assert L["LL"]["total"] == round(8700 + 200 - 420.5 - 45, 2)
        assert any(x["descricao"] == "Vendas" for x in d["nao_associadas"])  # vendas do Bling ficam de fora (Domínio já traz)
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
        assert c.get(f"/api/empresas/{e['id']}/dre?inicio=2026-01&fim=2026-03").json()["linhas"][12]["total"] == -420.5
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
