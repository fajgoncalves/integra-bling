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
         6: (None, "2026-03-01", "D", 1000.0),  # transferência do banco para o caixa (sem categoria)...
         8: (None, "2026-03-01", "C", 1000.0),  # ...e a entrada no caixa
         90: (10, "2025-12-20", "C", 10000.0), 91: (30, "2024-06-10", "D", 500.0)}  # histórico (saldo inicial 9.500)
CONTA_DO = {8: (2, "Caixa da loja")}
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
        assert q["situacao"] == ["R"]
        di, df = q["dataInicial"][0], q["dataFinal"][0]
        filtro = {int(x) for x in q.get("idsCategorias[]", [])}

        def casa(cat):  # o Bling devolve também os lançamentos das categorias filhas
            while cat:
                if cat in filtro:
                    return True
                cat = PAIS.get(cat)
            return False
        lista = [{"id": i, "debCred": d[2], "valor": d[3], "data": d[1], "descricao": f"Lanç. {i}",
                  "contato": {"nome": "Fulano"}, "origem": {"tipo": "bordero"},
                  "contaFinanceira": {"id": CONTA_DO.get(i, (1, ""))[0], "descricao": CONTA_DO.get(i, (1, "Banco X"))[1]}}
                 for i, d in CAIXA.items() if (not filtro or casa(d[0])) and di <= d[1] <= df]
        return httpx.Response(200, json={"data": lista if pag == 1 else []})
    if p.path.endswith("/contas-contabeis"):
        return httpx.Response(200, json={"data": [{"id": 1, "descricao": "Banco X", "tipo": "banco", "situacao": 1},
                                                  {"id": 2, "descricao": "Caixa da loja", "tipo": "caixa", "situacao": 1}]
                                         if pag == 1 else []})
    return httpx.Response(404)


PEDIDOS = [
    {"id": 2000001, "date_created": "2026-01-10T10:00:00.000-04:00", "date_closed": "2026-01-10T10:01:00.000-04:00",
     "status": "paid", "total_amount": 100.0, "buyer": {"nickname": "COMPRADOR1"}, "shipping": {"id": 41},
     "order_items": [{"item": {"id": "MLB1", "title": "Câmera", "seller_sku": "CAM-001"}, "quantity": 1, "unit_price": 100.0,
                      "sale_fee": 12.0, "listing_type_id": "gold_pro"}],
     "payments": [{"id": 9001, "status": "approved", "transaction_amount": 100.0, "transaction_amount_refunded": 0}]},
    {"id": 2000002, "date_created": "2026-01-31T23:30:00.000-04:00", "date_closed": "2026-01-31T23:30:00.000-04:00",  # 01/02 em Brasília
     "status": "cancelled", "total_amount": 50.0, "buyer": {"nickname": "COMPRADOR2"},
     "shipping": {"id": 42},
     "order_items": [{"item": {"id": "MLB2", "title": "Cabo"}, "quantity": 2, "unit_price": 25.0, "sale_fee": 3.0,
                      "listing_type_id": "gold_special"}],
     "payments": [{"id": 9002, "status": "refunded", "transaction_amount": 50.0, "transaction_amount_refunded": 50.0}]},
    {"id": 2000003, "date_closed": "2026-02-03T09:00:00.000-03:00", "status": "payment_required", "total_amount": 70.0,
     "order_items": [{"item": {"title": "Tripé"}, "quantity": 1, "sale_fee": 8.0}], "payments": []},
]
FATURAMENTO = {
    ("2026-01", "ML"): {"period": {"date_from": "2026-01-01T00:00:00", "date_to": "2026-01-31T23:59:59"},
                        "bill_includes": {"total_amount": 60.0,
                                          "charges": [{"label": "Tarifa de venda", "amount": 15.0, "type": "CV"},
                                                      {"label": "Mercado Envios", "amount": 40.0, "type": "CXD"},
                                                      {"label": "Product Ads", "amount": 10.0, "type": "PADS"}],
                                          "bonuses": [{"label": "Bônus por envio", "amount": 5.0, "type": "BXD"}]}},
    ("2026-02", "MP"): {"bill_includes": {"charges": [{"label": "Tarifa de parcelamento", "amount": 2.5, "type": "FIN"}]}},
}
ml_chamadas = {"token": 0, "refresh": set()}
K1, K2 = "31260112345678000190550010000010011000000011", "31260212345678000190550010000010021000000022"


def _cob(did, tipo, desc, valor, oid, deb="YES", nat="CHARGE"):
    return {"charge_info": {"detail_id": did, "detail_amount": valor, "detail_type": nat, "detail_sub_type": tipo,
                            "transaction_detail": desc, "creation_date_time": "2026-01-15T10:00:00",
                            "debited_from_operation": deb},
            "sales_info": [{"order_id": oid}] if oid else [], "shipping_info": {"shipping_id": "null"}}


DETALHES = {("2026-01", "ML", "BILL"): [_cob(1, "CV", "Tarifa de venda", 13.0, 2000001), _cob(2, "CXD", "Mercado Envios", 20.0, 2000001),
                                        _cob(3, "CV", "Tarifa de venda", 3.0, 2000002), _cob(4, "CV", "Tarifa de venda", 99.0, 7777)],
            ("2026-01", "MP", "BILL"): [_cob("null", "CCMP", "Sem id", 1.0, "null"), _cob(6, "CCMP", "Cargo de Mercado Pago", 1.0, "null")]}
EXTRATO_CSV = ("DATE;SOURCE_ID;EXTERNAL_REFERENCE;RECORD_TYPE;DESCRIPTION;NET_CREDIT_AMOUNT;NET_DEBIT_AMOUNT;GROSS_AMOUNT;"
               "MP_FEE_AMOUNT;FINANCING_FEE_AMOUNT;SHIPPING_FEE_AMOUNT;TAXES_AMOUNT;COUPON_AMOUNT;INSTALLMENTS;PAYMENT_METHOD;"
               "BALANCE_AMOUNT;ORDER_ID;SHIPPING_ID;PACK_ID\n"
               "2026-01-25T10:00:00.000-03:00;9001;;release;payment;67.00;0.00;100.00;-13.00;0;-20.00;0;0;1;pix;67.00;;;\n"
               "2026-02-02T10:00:00.000-03:00;9002;;release;payment;37.00;0.00;60.00;-3.00;0;-20.00;0;0;1;pix;104.00;;;\n"
               "2026-02-02T10:00:00.000-03:00;9002;;release;reserve_for_dispute;0.00;37.00;-37.00;0;0;0;0;0;1;pix;67.00;;;\n"
               "2026-02-03T10:00:00.000-03:00;9002;;release;reserve_for_dispute;37.00;0.00;37.00;0;0;0;0;0;1;pix;104.00;;;\n"
               "2026-02-03T10:00:00.000-03:00;9002;;release;mediation;0.00;60.00;-60.00;0;0;0;0;0;1;pix;44.00;;;\n"
               "2026-02-03T10:00:00.000-03:00;9002;;release;refund;3.00;0.00;0.00;3.00;0;0;0;0;1;pix;47.00;;;\n"
               "2026-02-03T11:00:00.000-03:00;9002;;release;reserve_for_bpp_shipping_return;0.00;15.00;-15.00;0;0;0;0;0;1;pix;32.00;;;\n"
               "2026-02-03T11:00:00.000-03:00;9002;;release;mediation;0.00;15.00;-15.00;0;0;0;0;0;1;pix;17.00;;;\n"
               "2026-02-03T11:00:00.000-03:00;9002;;release;reserve_for_bpp_shipping_return;15.00;0.00;15.00;0;0;0;0;0;1;pix;32.00;;;\n"
               "2026-02-04T10:00:00.000-03:00;8888;XYZ;release;mediation;0.00;10.00;-10.00;0;0;0;0;0;1;;22.00;;;\n"
               "2026-02-05T10:00:00.000-03:00;7777777;;release;payout;0.00;22.00;-22.00;0;0;0;0;0;1;;0.00;;;\n")


def ml_falso(req: httpx.Request):
    url = urlparse(str(req.url))
    q = parse_qs(url.query)
    if url.path == "/oauth/token":
        f = parse_qs(req.content.decode())
        assert f["client_id"] == ["mlcid"] and f["client_secret"] == ["mlsec"]
        if f["grant_type"] == ["authorization_code"]:
            assert f["redirect_uri"][0].endswith("/mlivre/callback")
        else:
            assert f["refresh_token"][0] not in ml_chamadas["refresh"]  # refresh token de uso único
            ml_chamadas["refresh"].add(f["refresh_token"][0])
        ml_chamadas["token"] += 1
        return httpx.Response(200, json={"access_token": f"mlacc{ml_chamadas['token']}", "expires_in": 10800,
                                         "refresh_token": f"mlref{ml_chamadas['token']}", "user_id": 555})
    assert req.headers["authorization"].startswith("Bearer mlacc")
    if url.path == "/users/me":
        return httpx.Response(200, json={"id": 555, "nickname": "LOJA_ALFA"})
    if url.path == "/orders/search":
        assert q["seller"] == ["555"]
        de, ate = q["order.date_closed.from"][0][:10], q["order.date_closed.to"][0][:10]
        rs = [o for o in PEDIDOS if de <= (o.get("date_closed") or o["date_created"])[:10] <= ate]
        off, lim = int(q["offset"][0]), int(q["limit"][0])
        return httpx.Response(200, json={"results": rs[off:off + lim], "paging": {"total": len(rs), "offset": off, "limit": lim}})
    if url.path == "/users/555/invoices/documents/xml/1377978/authorized":
        ml_chamadas["xml"] = ml_chamadas.get("xml", 0) + 1
        return httpx.Response(200, text=(Path(__file__).parent / "nfe_exemplo.xml").read_text(encoding="utf-8"))
    if url.path == "/users/555/invoices/orders/2000001":
        return httpx.Response(200, json={"id": 1377978, "status": "authorized", "invoice_number": 1001, "invoice_series": "1",
                                         "amount": 100.0, "issued_date": "2026-01-10T11:00:00.000-03:00",
                                         "attributes": {"invoice_key": K1, "xml_location": "x"}})
    if url.path.startswith("/users/555/invoices/orders/"):
        return httpx.Response(404, json={"message": "invoice not found"})
    if url.path == "/post-purchase/v1/claims/search":
        return httpx.Response(200, json={"data": [{"id": 55}]} if q["resource_id"] == ["2000002"] else {"data": []})
    if url.path == "/post-purchase/v2/claims/55/returns":
        return httpx.Response(200, json={"shipments": [{"shipment_id": 4242, "destination": {"name": "seller_address"}}]})
    if url.path == "/users/555/invoices/shipments/4242":
        return httpx.Response(200, json={"invoice_number": 2003, "invoice_series": "1", "status": "authorized", "amount": 50.0,
                                         "attributes": {"invoice_key": "31260212345678000190550010000020031000000033", "invoice_type": "devolution"}})
    if url.path == "/shipments/42/invoice_data":
        assert q["siteId"] == ["MLB"]
        return httpx.Response(200, json={"fiscal_key": K2, "invoice_number": "1002", "invoice_series": "1"})
    if url.path.endswith("/invoice_data"):
        return httpx.Response(404)
    if "/group/" in url.path and "/billing/integration/periods/key/" in url.path:
        mes, grupo = url.path.split("/key/")[1][:7], url.path.split("/group/")[1].split("/")[0]
        rs = DETALHES.get((mes, grupo, q["document_type"][0]), [])
        assert "offset" not in q and q["limit"] == ["1000"]
        fid = int(q["from_id"][0])
        rs = [x for x in rs if str(x["charge_info"]["detail_id"]).isdigit() and x["charge_info"]["detail_id"] > fid or (fid == 0 and x["charge_info"]["detail_id"] == "null")]
        if mes == "2026-02" and grupo == "MP":
            return httpx.Response(500, json={"message": "internal"})
        return httpx.Response(200, json={"limit": 1000, "last_id": rs[-1]["charge_info"]["detail_id"] if rs else "null", "results": rs})
    if url.netloc == "api.mercadopago.com":
        if url.path == "/v1/account/release_report/config":
            if req.method == "GET":
                return httpx.Response(404, json={"message": "config not found"})
            cfg = json.loads(req.content)
            assert {"key": "ORDER_ID"} in cfg["columns"]
            if not cfg.get("frequency"):
                return httpx.Response(400, json={"message": "Invalid frequency"})
            return httpx.Response(201, json=cfg)
        if url.path == "/v1/account/release_report" and req.method == "POST":
            ml_chamadas["relatorio"] = json.loads(req.content)
            return httpx.Response(202, json={})
        if url.path == "/v1/account/release_report/list":
            b = ml_chamadas.get("relatorio") or {}
            return httpx.Response(200, json=[{"id": 1, "file_name": "rel-1.csv", "begin_date": b.get("begin_date"),
                                              "end_date": b.get("end_date"), "status": "processed"}])
        if url.path == "/v1/account/release_report/rel-1.csv":
            return httpx.Response(200, text=EXTRATO_CSV)
        return httpx.Response(404)
    if url.path.startswith("/shipments/"):
        sid = url.path.split("/")[2]
        return httpx.Response(200, json={"senders": [{"user_id": 555, "cost": {"41": 20.0, "42": 9.0}[sid]}]})
    if "/billing/integration/periods/key/" in url.path and "/summary/" in url.path:
        mes = url.path.split("/key/")[1][:7]
        r = FATURAMENTO.get((mes, q["group"][0]))
        return httpx.Response(200, json=r) if r else httpx.Response(404, json={"message": "not found"})
    return httpx.Response(404)


def verifica_mercado_livre(c, eid):
    import mlivre
    mlivre.INTERVALO = 0
    mlivre.dormir = lambda _x: None
    assert c.post(f"/api/empresas/{eid}/mlivre/conectar").status_code == 400  # sem aplicativo
    cfg = c.put("/api/config", json={"ml_client_id": "mlcid", "ml_client_secret": "mlsec"}).json()
    assert cfg["ml_secret_definido"] and cfg["ml_redirect_uri"].endswith("/mlivre/callback")
    u = c.post(f"/api/empresas/{eid}/mlivre/conectar").json()["url"]
    q = parse_qs(urlparse(u).query)
    assert u.startswith("https://auth.mercadolivre.com.br/authorization") and q["client_id"] == ["mlcid"]
    orig = httpx.Client
    ml_http = orig(transport=httpx.MockTransport(ml_falso))
    mlivre.httpx.Client = lambda **kw: ml_http
    try:
        r = c.get(f"/mlivre/callback?code=xyz&state={q['state'][0]}", follow_redirects=False)
        assert "#/ml/" in r.headers["location"] and "ml=ok" in r.headers["location"], r.headers["location"]
        e = c.get(f"/api/empresas/{eid}").json()
        assert e["ml_conectado"] and e["ml_apelido"] == "LOJA_ALFA"
        r = c.post(f"/api/empresas/{eid}/mlivre/sincronizar", json={"inicio": "2026-01", "fim": "2026-02"}).json()
        cli = mlivre.Cliente(eid, ml_http)
        with SessionLocal() as s:  # força a renovação do token (refresh de uso único)
            from db import Empresa
            s.get(Empresa, eid).ml_expira = None
            s.commit()
        mlivre.sincronizar(r["id"], cli)
        j = c.get(f"/api/jobs/{r['id']}").json()
        assert j["status"] == "OK", j["mensagem"]
        assert "3 pedidos (2 pagos)" in j["mensagem"] and "5 tarifas" in j["mensagem"], j["mensagem"]
        assert "3 notas fiscais" in j["mensagem"] and "5 cobranças detalhadas" in j["mensagem"]
        assert "não devolveu parte das cobranças" in j["mensagem"] and "02/2026 MP" in j["mensagem"]
        # extrato do Mercado Pago pela API (relatório de liberações)
        rx = c.post(f"/api/empresas/{eid}/mlivre/extrato/atualizar", json={"de": "2026-01-01", "ate": "2026-02-28"}).json()
        mlivre.sincronizar_extrato(rx["id"], cli, espera=0)
        jx = c.get(f"/api/jobs/{rx['id']}").json()
        assert jx["status"] == "OK" and "11 movimentos, 9 ligados" in jx["mensagem"], jx["mensagem"]
        assert ml_chamadas["relatorio"]["begin_date"] == "2026-01-01T03:00:00Z"
        mlivre.sincronizar(r["id"], cli)  # de novo: substitui
    finally:
        mlivre.httpx.Client = orig
    # módulo separado: nada do ML entra na DRE principal nem na associação de contas
    ct = c.get(f"/api/empresas/{eid}/contas?inicio=2026-01&fim=2026-02").json()["contas"]
    assert not any(x["origem"] == "MLIVRE" for x in ct)
    assert c.get("/api/ml/empresas").json()[0]["pedidos"] == 3
    v = c.get(f"/api/empresas/{eid}/mlivre/vendas?inicio=2026-01&fim=2026-02").json()
    assert v["total_pedidos"] == 2 and v["nao_pagos"] == 1
    P = {x["order_id"]: x for x in v["vendas"]}
    assert P[2000001]["frete"] == 20 and not P[2000001]["sem_custo"] and P[2000001]["sku"] == "CAM-001"
    assert P[2000002]["devolvido"] == 50 and P[2000002]["tarifa"] == 0  # devolvido inteiro: tarifa estornada
    # impostos e custo
    assert c.put(f"/api/empresas/{eid}/mlivre/config", json={"imposto": "10"}).json()["ml_imposto_aliq"] == 10
    cs = c.get(f"/api/empresas/{eid}/mlivre/custos?vigencia=2026-01").json()
    assert {x["chave"] for x in cs["itens"]} == {"CAM-001", "MLB2"}
    O = {x["chave"]: x for x in cs["itens"]}
    assert O["CAM-001"]["origem"] == "CADASTRO" and O["CAM-001"]["custo"] == 25 and O["MLB2"]["origem"] is None
    # vendas antes da 1ª vigência usam o primeiro custo (estimado)
    ant = {x["chave"]: x for x in c.get(f"/api/empresas/{eid}/mlivre/custos?vigencia=2025-12").json()["itens"]}
    assert ant["CAM-001"]["estimado"] is True and ant["CAM-001"]["custo"] == 25
    # copia o custo da aba Custo dos produtos (CAM-001 = 25,00 desde 01/2026, gravado antes no teste)
    assert c.post(f"/api/empresas/{eid}/mlivre/custos/copiar", json={"vigencia": "2026-01"}).json()["copiados"] == 1
    assert c.put(f"/api/empresas/{eid}/mlivre/custos", json={"chave": "MLB2", "custo": "4,00", "vigencia": "2026-01"}).json()["custo"] == 4
    assert c.put(f"/api/empresas/{eid}/mlivre/custos", json={"chave": "MLB2", "custo": "x", "vigencia": "2026-01"}).status_code == 400
    v = c.get(f"/api/empresas/{eid}/mlivre/vendas?inicio=2026-01&fim=2026-02").json()
    P = {x["order_id"]: x for x in v["vendas"]}
    assert P[2000001]["margem"] == 100 - 10 - 12 - 20 - 25  # 33
    assert P[2000002]["cmv"] == 0 and P[2000002]["margem"] == -9  # devolvido: só o frete fica
    d = c.get(f"/api/empresas/{eid}/mlivre/dre?inicio=2026-01&fim=2026-02").json()
    L = {x["codigo"]: x for x in d["linhas"]}
    assert L["RB"]["valores"] == [100, 50] and L["DEV"]["valores"] == [0, -50] and L["IMP"]["valores"] == [-10, 0]
    assert L["CMV"]["valores"] == [-25, 0] and L["LB"]["total"] == 65
    assert L["COM"]["valores"] == [-15, 0] and L["FRETE"]["valores"] == [-40, 0] and L["ADS"]["valores"] == [-10, 0]
    assert L["TMP"]["valores"] == [0, -2.5] and L["BONUS"]["valores"] == [5, 0] and L["CML"]["total"] == -62.5
    assert L["MC"]["total"] == 2.5 and L["COM"]["composicao"][0]["descricao"] == "Tarifa de venda (CV)"
    assert d["conferencia"]["comissao_pedidos"] == 12 and d["conferencia"]["frete_pedidos"] == 29
    assert not any("sem custo" in a for a in d["avisos"])
    assert c.get(f"/api/empresas/{eid}/mlivre/dre.xlsx?inicio=2026-01&fim=2026-02").status_code == 200
    pr = {x["chave"]: x for x in c.get(f"/api/empresas/{eid}/mlivre/produtos?inicio=2026-01&fim=2026-02").json()["itens"]}
    assert pr["CAM-001"]["margem"] == 33 and pr["CAM-001"]["tipos"] == "Premium" and pr["MLB2"]["qtd"] == 0
    ts = c.get(f"/api/empresas/{eid}/mlivre/tarifas?inicio=2026-01&fim=2026-02").json()
    assert len(ts) == 5 and {t["linha"] for t in ts if t["tipo"] == "CXD"} == {"Mercado Envios (frete)"}
    # rastreabilidade: painel por pedido
    pn = {x["order_id"]: x for x in c.get(f"/api/empresas/{eid}/mlivre/painel?inicio=2026-01&fim=2026-02").json()["pedidos"]}
    a1, a2 = pn[2000001], pn[2000002]
    assert a1["nota"]["numero"] == "1001" and a1["nota"]["origem"] == "EMISSOR_ML" and a1["dominio"] == "escriturada"
    assert a2["nota"]["chave"] == K2 and a2["nota"]["origem"] == "ENVIO" and a2["dominio"] == "não escriturada"
    assert a1["cobrancas"]["COMISSAO"] == -13 and a1["cobrancas"]["FRETE"] == -20 and a1["fonte"] == "extrato"
    assert a1["resultado"] == 100 - 10 - 33 - 25 and a1["extrato_liquido"] == 67
    # devolução com disputa: retenções se anulam, tarifa estornada no MP, frete de ida e de volta ficam com o vendedor
    c2 = a2["componentes"]
    assert (c2["venda"], c2["frete_comprador"], c2["reembolso"], c2["tarifa"], c2["frete"], c2["frete_devolucao"]) == (50, 10, -60, 0, -20, -15)
    assert c2["imposto"] == 0 and a2["resultado"] == -35 and a2["custo_devolucao"] == 35 and a2["extrato_liquido"] == -35
    assert a2["retido"] == 0 and a2["devolucao_integral"]
    d = c.get(f"/api/empresas/{eid}/mlivre/pedido/2000001").json()
    assert d["notas"][0]["dominio"]["numero"] == "1001" and len(d["cobrancas"]) == 2 and d["extrato"][0]["como"] == "pagamento"
    assert d["itens"][0]["tarifa_pct"] == 12 and d["itens"][0]["custo_unit"] == 25
    x = c.get(f"/api/empresas/{eid}/mlivre/extrato?de=2026-01-01&ate=2026-02-28").json()
    assert x["creditos"] == 159 and x["debitos"] == 159 and x["saldo_final"] == 0 and x["nao_identificados"] == 1
    assert {l["como"] for l in x["linhas"] if l["order_id"]} == {"pagamento"}
    # DANFE e XML: nota do emissor do ML (XML pela API do ML, guardado) e nota do ERP (link do DANFE do Bling)
    n1 = d["notas"][0]
    orig_http = mlivre.httpx.Client
    mlivre.httpx.Client = lambda **kw: orig_http(transport=httpx.MockTransport(ml_falso))
    try:
        r = c.get(f"/api/empresas/{eid}/mlivre/notas/{n1['id']}/danfe")
        assert r.status_code == 200 and r.headers["content-type"] == "application/pdf" and r.content[:4] == b"%PDF"
        assert c.get(f"/api/empresas/{eid}/mlivre/notas/{n1['id']}/xml").text.startswith("<")
        assert ml_chamadas["xml"] == 1  # da segunda vez usa o XML guardado
    finally:
        mlivre.httpx.Client = orig_http
    d2 = c.get(f"/api/empresas/{eid}/mlivre/pedido/2000002").json()
    assert d2["dominio_lida"] and [n["tipo"] for n in d2["notas"]] == ["venda", "devolucao"]
    import bling as _bling
    orig_np = _bling.nota_por_chave
    _bling.nota_por_chave = lambda eid_, chave, numero, data, http=None: ("", "https://www.bling.com.br/danfe/xyz")
    orig_http = mlivre.httpx.Client
    mlivre.httpx.Client = lambda **kw: orig_http(transport=httpx.MockTransport(ml_falso))
    try:
        r = c.get(f"/api/empresas/{eid}/mlivre/notas/{d2['notas'][0]['id']}/danfe", follow_redirects=False)
        assert r.status_code == 307 and "bling.com.br" in r.headers["location"]
    finally:
        _bling.nota_por_chave = orig_np
        mlivre.httpx.Client = orig_http
    assert d2["dominio_devolucoes"][0]["numero"] == "2003" and d2["dominio_devolucoes"][0]["como"].startswith("chave")
    assert sum(1 for l in d2["extrato"] if l["retencao"]) == 4 and {c_["chave"]: c_["valor"] for c_ in d2["componentes"]}["frete_devolucao"] == -15
    a = c.get(f"/api/empresas/{eid}/mlivre/auditoria?inicio=2026-01&fim=2026-02").json()
    A = {}
    for x in a["achados"]:
        A.setdefault(x["codigo"], []).append(x)
    assert A["COMISSAO_DIVERGENTE"][0]["order_id"] == 2000001 and A["COMISSAO_DIVERGENTE"][0]["diferenca"] == 1
    assert "TARIFA_NAO_ESTORNADA" not in A and A["CUSTO_DEVOLUCAO"][0]["cobrado"] == 35
    assert "frete da devolução 15,00" in A["CUSTO_DEVOLUCAO"][0]["texto"]
    assert A["COMISSAO_FATURA_X_EXTRATO"][0]["order_id"] == 2000002 and A["COMISSAO_FATURA_X_EXTRATO"][0]["cobrado"] == 3
    assert A["COBRANCA_SEM_VENDA"][0]["order_id"] == 7777 and A["EXTRATO_SEM_PEDIDO"][0]["cobrado"] == -10
    assert not {"SEM_NOTA", "NOTA_NAO_ESCRITURADA", "VENDA_SEM_EXTRATO", "FRETE_DIVERGENTE", "VALOR_RETIDO",
                "DEVOLUCAO_SEM_NOTA_ENTRADA"} & set(A)
    assert [n["numero"] for n in a["dominio_sem_ml"]] == ["1009"] and a["tem_cobrancas"] and a["tem_extrato"]
    # comissão acordada: Premium 11% → 12,00 cobrado acima do esperado 11,00
    c.put(f"/api/empresas/{eid}/mlivre/config", json={"regras": {"premium_pct": "11"}})
    a = c.get(f"/api/empresas/{eid}/mlivre/auditoria?inicio=2026-01&fim=2026-02").json()
    ac = [x for x in a["achados"] if x["codigo"] == "COMISSAO_ACIMA_ACORDADO"]
    assert ac and ac[0]["esperado"] == 11 and ac[0]["cobrado"] == 12
    for u_ in ("painel", "auditoria"):
        assert c.get(f"/api/empresas/{eid}/mlivre/{u_}.xlsx?inicio=2026-01&fim=2026-02").status_code == 200
    assert c.get(f"/api/empresas/{eid}/mlivre/extrato.xlsx?de=2026-01-01&ate=2026-02-28").status_code == 200
    # extrato por arquivo (relatório baixado do Mercado Pago, cabeçalho em português e valores com vírgula)
    import base64
    arq = ("Data;ID da operação no Mercado Pago;Descrição;Valor líquido creditado;Valor líquido debitado;Saldo;ID do pedido\n"
           "02/03/2026 10:00:00;9999;payment;10,50;0;10,50;2000001\n")
    r = c.post(f"/api/empresas/{eid}/mlivre/extrato/arquivo", json={"arquivo": base64.b64encode(arq.encode()).decode(), "nome": "x.csv"}).json()
    assert r["movimentos"] == 1 and r["identificados"] == 1 and r["de"] == "2026-03-02"
    assert c.post(f"/api/empresas/{eid}/mlivre/extrato/arquivo", json={"arquivo": base64.b64encode(b"a;b\n1;2").decode()}).status_code == 400
    assert c.post(f"/api/empresas/{eid}/mlivre/desconectar").json()["ml_conectado"] is False


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
        "apuracao_pis_cofins": [{"mes": "2026-01", "pis": 100, "cofins": 460, "outros": 0}],
        "apuracao_icms": [{"mes": "2026-02", "debito": 1000, "credito_presumido": 800, "recolhimento_efetivo": 200, "saldo_recolher": 150}],
        "notas_saida": [{"data": "2026-01-10", "numero": "1001", "serie": "1", "chave": "31260112345678000190550010000010011000000011", "cfop": 5102, "valor": 100,
                         "cancelada": False},
                        {"data": "2026-01-20", "numero": "1009", "serie": "1", "chave": "31260112345678000190550010000010091000000099", "cfop": 5102, "valor": 500,
                         "cancelada": False}],
        "notas_entrada": [{"data": "2026-02-10", "numero": "2003", "serie": "1", "chave": "31260212345678000190550010000020031000000033", "cfop": 1202, "valor": 50,
                           "cancelada": False}],
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
        assert e["modelo_id"] == modelos[0]["id"] and e["contas"]["DOMINIO"]["associadas"] == 7  # sugestões da Domínio (com CMV, PIS/COFINS e ICMS TTD)
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
            assert "7 lançamentos" in j["mensagem"] and "2 sem categoria" in j["mensagem"] and "2 lançamentos anteriores" in j["mensagem"]
            # sincronizar de novo o mesmo período substitui (não duplica)
            with SessionLocal() as s:
                j2 = Job(empresa_id=e["id"], tipo="bling_sync", parametros=json.dumps({"inicio": "2026-01-01", "fim": "2026-03-31"}))
                s.add(j2)
                s.commit()
                jid2 = j2.id
            bling.sincronizar(jid2, bling.Cliente(e["id"], bling_http))
            with SessionLocal() as s:
                from db import Movimento
                assert s.query(Movimento).count() == 9
                assert s.query(Movimento).filter_by(categorizado=False).count() == 2
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
        assert L["RL"]["av_total"] == 100
        assert L["CMV_DEVC"]["total"] == 200
        assert L["DV_COM"]["valores"] == [-420.5, 20, 0]
        assert len(L["DV_COM"]["composicao"]) == 2
        assert L["DF"]["total"] == -45
        assert L["CMV"]["valores"] == [-450, 30, 0]
        assert L["PIS_COFINS"]["valores"] == [-560, 0, 0]  # padrão: débito apurado na Domínio (fevereiro sem apuração)
        assert any("PIS/COFINS sem apuração na Domínio em 02/2026" in a for a in d["avisos"])
        assert c.put(f"/api/empresas/{e['id']}", json={"pis_cofins_fonte": "ALIQUOTA"}).json()["pis_cofins_fonte"] == "ALIQUOTA"
        d = c.get(f"/api/empresas/{e['id']}/dre?inicio=2026-01&fim=2026-03").json()
        L = {x["codigo"]: x for x in d["linhas"]}
        assert L["PIS_COFINS"]["valores"] == [-740, -64.75, 0]  # 9,25% de (8.000) e de (1.000 − 300)
        assert L["ICMS_TTD"]["valores"] == [0, -150, 0]
        assert L["DED"]["total"] == -300 - 804.75 - 150 and L["RL"]["total"] == 9000 - 300 - 804.75 - 150
        assert L["LL"]["total"] == round(8700 - 804.75 - 150 + 200 - 420 - 400.5 - 45, 2)
        assert L["DOP"]["tipo"] == "GRUPO" and L["DOP"]["total"] == -400.5  # soma automática das contas do grupo
        assert L["CUSTOS"]["total"] == -420 + 200 and L["RFIN"]["total"] == -45
        ct2 = {x["descricao"]: x for x in c.get(f"/api/empresas/{e['id']}/contas").json()["contas"]}
        ttd = c.get(f"/api/contas/{ct2['ICMS TTD / regime especial (saldo a recolher da apuração na Domínio)']['id']}/detalhe?inicio=2026-01&fim=2026-03").json()
        assert ttd["tipo"] == "icms" and ttd["itens"][0]["credito_presumido"] == 800 and ttd["total"] == -150
        dv = c.get(f"/api/contas/{ct2['Vendas de mercadorias e produtos (NF de saída)']['id']}/detalhe?inicio=2026-01&fim=2026-03").json()
        assert [p["codigo"] for p in dv["produtos"]] == ["CAM-001", "BON-9"]
        dc = c.get(f"/api/contas/{ct2['CMV calculado (vendas − devoluções de venda, × custo do produto)']['id']}/detalhe?inicio=2026-01&fim=2026-03").json()
        assert dc["tipo"] == "cmv" and dc["total"] == -420
        assert any(x["descricao"] == "Vendas" for x in d["nao_associadas"])  # recebimentos de vendas ficam de fora (Domínio já traz)
        assert any("sem categoria" in a and "entradas R$ 1.000,00, saídas R$ 1.000,00" in a for a in d["avisos"])

        # fluxo de caixa realizado
        f = c.get(f"/api/empresas/{e['id']}/fluxo?inicio=2026-01&fim=2026-03").json()
        F = {}
        for ln in f["linhas"]:
            F.setdefault((ln["tipo"], ln["nome"]), ln)
        assert F[("SALDO_INI", "Saldo inicial")]["valores"][0] == 9500
        assert F[("SALDO_CONTA_INI", "Banco X")]["valores"] == [9500, 14079.5, 14054.5]
        assert F[("SECAO", "ENTRADAS")]["total"] == 5000
        assert F[("CATEGORIA", "Despesas comerciais")]["valores"] == [-420.5, 20, 0]  # pai = soma das filhas
        assert F[("CATEGORIA", "Comissão de vendedores")]["nivel"] == 2
        assert F[("SECAO", "SAÍDAS")]["total"] == -445.5
        assert F[("SEMCAT", "Entradas sem categoria")]["total"] == 1000 and F[("SEMCAT", "Saídas sem categoria")]["total"] == -1000
        assert F[("VARIACAO", "(=) Variação do caixa no mês")]["valores"] == [4579.5, -25, 0]
        assert F[("SALDO_FIM", "Saldo final")]["valores"] == [14079.5, 14054.5, 14054.5]
        assert F[("SALDO_CONTA_FIM", "Caixa da loja")]["total"] == 1000 and F[("SALDO_CONTA_FIM", "Banco X")]["total"] == 13054.5
        assert not f["avisos"]
        # reorganizar: tarifas vira a primeira das saídas; vendas passa para saídas (só teste)
        tar = F[("CATEGORIA", "Tarifas bancárias")]["conta_id"]
        assert c.put(f"/api/empresas/{e['id']}/fluxo/ordem", json={"itens": [{"conta_id": tar, "ordem": -1}]}).json()["alteradas"] == 1
        f2 = c.get(f"/api/empresas/{e['id']}/fluxo?inicio=2026-01&fim=2026-03").json()["linhas"]
        sai = [x["nome"] for x in f2 if x["tipo"] == "CATEGORIA" and x["secao"] == "SAIDAS" and x["nivel"] == 1]
        assert sai[0] == "Tarifas bancárias"
        assert c.get(f"/api/empresas/{e['id']}/fluxo.xlsx?inicio=2026-01&fim=2026-03").content[:2] == b"PK"
        x = c.get(f"/api/empresas/{e['id']}/dre.xlsx?inicio=2026-01&fim=2026-03")
        assert x.status_code == 200 and x.content[:2] == b"PK"

        # modelo: editar, validar fórmula, duplicar, copiar associações
        m = c.get(f"/api/modelos/{modelos[0]['id']}").json()
        m["linhas"].append({"codigo": "X", "nome": "Teste", "tipo": "TOTAL", "formula": "LL + NAOEXISTE"})
        assert c.put(f"/api/modelos/{m['id']}", json=m).status_code == 400
        m["linhas"][-1]["formula"] = "LL - IRG"
        m["linhas"][-1]["codigo"] = "LL2"
        # conta nova no fim da lista, apontando para o grupo DOP: vai para o fim do grupo
        m["linhas"].append({"codigo": "DV_MKT", "nome": "Marketing digital", "tipo": "CONTA", "pai_codigo": "DOP"})
        m["linhas"].append({"codigo": "SOLTA", "nome": "Sem grupo", "tipo": "CONTA"})
        assert "precisa estar dentro de um grupo" in c.put(f"/api/modelos/{m['id']}", json=m).json()["erro"]
        m["linhas"].pop()
        r = c.put(f"/api/modelos/{m['id']}", json=m).json()
        cods = [x["codigo"] for x in r["linhas"]]
        assert cods.index("DV_MKT") == cods.index("DA_TRIB") + 1 and cods.index("RO") == cods.index("DV_MKT") + 1
        assert r["linhas"][-1]["codigo"] == "LL2"
        mk = next(x["id"] for x in r["linhas"] if x["codigo"] == "DV_MKT")
        assert c.put(f"/api/contas/{por['Tarifas bancárias']['id']}", json={"linha_id": mk}).status_code == 200
        dd = {x["codigo"]: x for x in c.get(f"/api/empresas/{e['id']}/dre?inicio=2026-01&fim=2026-03").json()["linhas"]}
        assert dd["DV_MKT"]["total"] == -45 and dd["DOP"]["total"] == -445.5 and dd["RFIN"]["total"] == 0
        # as associações continuam depois de salvar o modelo
        assert dd["DV_COM"]["total"] == -400.5
        e2 = c.post("/api/empresas", json={"nome": "Beta"}).json()
        assert c.post("/api/modelos", json={"nome": "Cópia", "copiar_de": m["id"]}).json()["linhas"][-1]["codigo"] == "LL2"
        assert c.post(f"/api/empresas/{e2['id']}/contas/copiar", json={"de_empresa_id": e["id"]}).json()["ok"]
        assert c.delete(f"/api/modelos/{m['id']}").status_code == 400  # em uso

        # conversão de um modelo no formato antigo (títulos e totais manuais)
        from db import ModeloDRE, LinhaDRE
        import dre as D
        with SessionLocal() as s:
            mv = ModeloDRE(nome="Antigo")
            s.add(mv)
            s.flush()
            for i, (cod, nome, tipo, form) in enumerate([("T1", "RECEITAS", "TITULO", ""), ("A", "Venda", "CONTA", ""),
                                                         ("B", "Serviço", "CONTA", ""), ("TA", "(=) Receita", "TOTAL", "A + B"),
                                                         ("C", "Aluguel", "CONTA", ""), ("R", "(=) Resultado", "TOTAL", "TA + C")], 1):
                s.add(LinhaDRE(modelo_id=mv.id, ordem=i, codigo=cod, nome=nome, tipo=tipo, formula=form))
            s.flush()
            id_c = s.query(LinhaDRE).filter_by(modelo_id=mv.id, codigo="C").one().id
            assert D.converter_modelo(s, mv)
            s.commit()
            ls = {x.codigo: x for x in s.query(LinhaDRE).filter_by(modelo_id=mv.id).all()}
            assert ls["TA"].tipo == "GRUPO" and ls["A"].pai_id == ls["TA"].id and "T1" not in ls
            assert ls["C"].id == id_c and ls[next(k for k in ls if ls[k].tipo == "GRUPO" and k != "TA")].id == ls["C"].pai_id
            assert ls["R"].tipo == "TOTAL" and not D.converter_modelo(s, mv)

        # Mercado Livre
        verifica_mercado_livre(c, e["id"])

        # usuários
        assert c.post("/api/usuarios", json={"nome": "Op", "email": "op@x.com", "senha": "senha-forte-2"}).status_code == 200
        c.post("/api/logout")
        assert c.get("/api/empresas").status_code == 401
        assert c.post("/api/login", json={"email": "op@x.com", "senha": "senha-forte-2"}).status_code == 200
        assert c.get("/api/config").status_code == 403
        assert c.get("/api/empresas").status_code == 200
