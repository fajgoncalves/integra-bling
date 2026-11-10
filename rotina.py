"""Rotina automática diária (a partir de 00:00, horário de Brasília): Mercado Livre e Domínio.

Para cada empresa ativa na rotina:
  1. Mercado Livre — pedidos, itens, frete, notas fiscais e cobranças detalhadas, mês a mês. Primeiro todos os meses desde o
     início configurado (padrão 2026-01) que ainda não foram importados; depois, sempre, os 3 últimos meses (devoluções,
     estornos e faturamento chegam depois da venda).
  2. Extrato do Mercado Pago — desde o início (primeira vez, em blocos de até 120 dias); depois, dos últimos 60 dias até hoje.
  3. Domínio — todos os meses desde o início na primeira vez; depois, o mês anterior e o atual.
  4. Conferência — compara a quantidade de pedidos de cada mês no ML com a gravada (reimporta o mês que não bater) e procura
     vendas pagas há mais de 45 dias sem movimento no extrato (busca o extrato de novo em volta delas).

Cada etapa tenta até 3 vezes (com espera entre as tentativas). O que não der certo vira uma falha aberta (tabela rotina_falhas,
tela Configurações → Rotina automática) com a mensagem e o rastreio técnico, e é tentado de novo na noite seguinte — o que
ficou para trás nunca é esquecido, porque o estado só avança quando a etapa termina bem. Os botões manuais continuam valendo.
"""
import json
import time
import threading
import traceback
import datetime as dt

from sqlalchemy import select, func

from db import SessionLocal, Empresa, Job, MLVenda, MLExtrato, RotinaExec, RotinaFalha, agora
from seguranca import get_config, set_config
import mlivre
import dominio

HORA = 0  # começa à 00:00
JANELA_FIM = 7  # se o serviço reiniciar no meio da madrugada, retoma até as 07:00
ESPERAS = [120, 600]  # segundos entre a 1ª e a 2ª, e entre a 2ª e a 3ª tentativa
ULTIMOS_MESES = 3
EXTRATO_RETROATIVO = 60
BLOCO_EXTRATO = 85  # o Mercado Pago só gera o relatório de liberações para menos de 90 dias
_trava = threading.Lock()
dormir = time.sleep


def log(msg):
    print(f"[ROTINA] {agora():%Y-%m-%d %H:%M:%S} {msg}", flush=True)


def inicio_padrao(s):
    return get_config(s, "rotina_inicio") or "2026-01"


def estado(e: Empresa):
    try:
        x = json.loads(e.rotina_estado or "{}")
    except ValueError:
        x = {}
    x.setdefault("ml_meses", {})
    x.setdefault("dominio_meses", {})
    return x


def _salvar_estado(eid, muda):
    with SessionLocal() as s:
        e = s.get(Empresa, eid)
        x = estado(e)
        muda(x)
        e.rotina_estado = json.dumps(x)
        s.commit()


def _etapa(rid, texto):
    with SessionLocal() as s:
        r = s.get(RotinaExec, rid)
        r.etapa = texto[:300]
        s.commit()


def _job(eid, tipo, params):
    with SessionLocal() as s:
        j = Job(empresa_id=eid, tipo=tipo, parametros=json.dumps(params), criado_por="rotina automática")
        s.add(j)
        s.commit()
        return j.id


def _falha(eid, etapa, msg, detalhe="", tipo="ERRO", tentativas=3):
    with SessionLocal() as s:
        f = s.scalar(select(RotinaFalha).where(RotinaFalha.empresa_id == eid, RotinaFalha.etapa == etapa))
        if not f:
            f = RotinaFalha(empresa_id=eid, etapa=etapa)
            s.add(f)
        nova_noite = f.resolvida_em is not None or not f.noites or (f.ultima and f.ultima.date() != agora().date())
        f.tipo, f.mensagem, f.detalhe = tipo, msg[:4000], detalhe[:8000]
        f.tentativas = (f.tentativas or 0) + tentativas
        f.noites = (f.noites or 0) + (1 if nova_noite else 0)
        f.ultima, f.resolvida_em = agora(), None
        s.commit()
    log(f"FALHA {tipo} empresa={eid} etapa={etapa}: {msg[:500]}")


def _resolvida(eid, etapa):
    with SessionLocal() as s:
        f = s.scalar(select(RotinaFalha).where(RotinaFalha.empresa_id == eid, RotinaFalha.etapa == etapa,
                                               RotinaFalha.resolvida_em.is_(None)))
        if f:
            f.resolvida_em = agora()
            s.commit()


def tentar(eid, etapa, fazer, rid=None, vezes=None):
    """Executa fazer() até 3 vezes (ou `vezes`). fazer() devolve None (ok) ou levanta exceção. Registra/resolve a falha."""
    erro, rastro = "", ""
    esperas = ESPERAS[:(vezes - 1)] if vezes else ESPERAS
    for n in range(1 + len(esperas)):
        if rid:
            _etapa(rid, f"empresa {eid} · {etapa} · tentativa {n + 1}")
        try:
            fazer()
            _resolvida(eid, etapa)
            return True
        except Exception as ex:  # noqa: BLE001
            erro, rastro = str(ex) or type(ex).__name__, traceback.format_exc()
            log(f"erro empresa={eid} etapa={etapa} tentativa={n + 1}: {erro[:300]}")
            if n < len(esperas):
                dormir(esperas[n])
    _falha(eid, etapa, erro, rastro, tentativas=1 + len(esperas))
    return False


def _rodar_job(alvo, jid, **kw):
    alvo(jid, **kw)
    with SessionLocal() as s:
        j = s.get(Job, jid)
        if j.status != "OK":
            raise RuntimeError(j.mensagem or f"Atividade {jid} terminou com {j.status}")
        return j.mensagem or ""


# ------------------------------------------------------------------ etapas
def ml_mes(eid, mes):
    def fazer():
        jid = _job(eid, "ml_sync", {"inicio": mes, "fim": mes, "frete": True, "notas": True, "cobrancas": True, "ads": True})
        msg = _rodar_job(mlivre.sincronizar, jid)
        if "não devolveu parte das cobranças" in msg:
            raise RuntimeError(msg)
        if "publicidade não lida" in msg:  # não impede o mês; fica como aviso para verificar (ex.: permissão de publicidade)
            _falha(eid, f"ADS:{mes}", msg[msg.index("Atenção — publicidade"):][:1000], "", "AVISO", 0)
        else:
            _resolvida(eid, f"ADS:{mes}")
        _salvar_estado(eid, lambda x: x["ml_meses"].__setitem__(mes, agora().isoformat(timespec="seconds")))
    return fazer


def ml_extrato(eid, d1: dt.date, d2: dt.date):
    def fazer():
        jid = _job(eid, "ml_extrato", {"de": d1.isoformat(), "ate": d2.isoformat()})
        _rodar_job(mlivre.sincronizar_extrato, jid, limite_s=3600)

        def marca(x):
            if not x.get("extrato_ate") or x["extrato_ate"] < d2.isoformat():
                x["extrato_ate"] = d2.isoformat()
            x.setdefault("extrato_de", d1.isoformat())
            x["extrato_de"] = min(x["extrato_de"], d1.isoformat())
        _salvar_estado(eid, marca)
    return fazer


def dominio_periodo(eid, ini, fim):
    def fazer():
        jid = _job(eid, "dominio_importar", {"inicio": ini, "fim": fim})
        _rodar_job(dominio.importar, jid, limite_s=2400)
        meses = mlivre.meses_entre(ini, fim)
        _salvar_estado(eid, lambda x: x["dominio_meses"].update({m: agora().isoformat(timespec="seconds") for m in meses}))
    return fazer


def conferir_pedidos(eid, mes, cli=None):
    """Quantidade de pedidos do mês no ML (API) × gravada. Devolve (api, gravados)."""
    with SessionLocal() as s:
        e = s.get(Empresa, eid)
        seller = e.ml_user_id
        d1 = dt.date.fromisoformat(mes + "-01")
        d2 = mlivre._fim_mes(mes)
        gravados = s.scalar(select(func.count(MLVenda.id)).where(MLVenda.empresa_id == eid, MLVenda.data >= d1,
                                                                 MLVenda.data <= d2)) or 0
    cli = cli or mlivre.Cliente(eid)
    de, ate = mlivre._janela(d1, d2)
    r = cli.get("/orders/search", {"seller": seller, "order.date_closed.from": de, "order.date_closed.to": ate,
                                   "offset": 0, "limit": 1})
    return int((r.get("paging") or {}).get("total") or 0), gravados


def vendas_sem_extrato(eid, ate_extrato: dt.date):
    """Vendas pagas (não devolvidas) há mais de 45 dias, dentro do período do extrato lido, sem nenhum movimento."""
    with SessionLocal() as s:
        ini = s.scalar(select(func.min(MLExtrato.data)).where(MLExtrato.empresa_id == eid))
        if not ini:
            return []
        limite = min(ate_extrato, agora().date()) - dt.timedelta(days=45)
        com = select(MLExtrato.order_id).where(MLExtrato.empresa_id == eid, MLExtrato.order_id.is_not(None))
        q = select(MLVenda.order_id, MLVenda.data).where(MLVenda.empresa_id == eid, MLVenda.pago > MLVenda.devolvido,
                                                        MLVenda.data >= ini.date(), MLVenda.data <= limite,
                                                        MLVenda.order_id.not_in(com))
        return list(s.execute(q).all())


# ------------------------------------------------------------------ rotina
def executar(origem="agendada", hoje=None):
    if not _trava.acquire(blocking=False):
        log("já em execução; ignorado")
        return None
    try:
        hoje = hoje or agora().date()
        with SessionLocal() as s:
            r = RotinaExec(origem=origem)
            s.add(r)
            set_config(s, "rotina_ultima_data", hoje.isoformat())
            s.commit()
            rid = r.id
            ini_cfg = inicio_padrao(s)
            emps = [(e.id, e.nome) for e in s.scalars(select(Empresa).where(Empresa.ativo == True,  # noqa: E712
                                                                             Empresa.rotina_ativa == True)).all()]  # noqa: E712
        log(f"início ({origem}) — {len(emps)} empresa(s), desde {ini_cfg}")
        resumo, falhas = [], 0
        mes_atual = hoje.strftime("%Y-%m")
        meses = mlivre.meses_entre(ini_cfg, mes_atual)
        for eid, nome in emps:
            with SessionLocal() as s:
                e = s.get(Empresa, eid)
                tem_ml, tem_dom = bool(e.ml_refresh), bool(e.cnpj or e.codigo_dominio)
                st = estado(e)
                try:
                    dominio.central(s)
                except Exception:  # noqa: BLE001
                    tem_dom = False
            linha = [nome]
            if tem_ml:
                ultimos = set(meses[-ULTIMOS_MESES:])
                pend = [m for m in meses if m not in st["ml_meses"] or m in ultimos]
                ok = sum(tentar(eid, f"ML_MES:{m}", ml_mes(eid, m), rid) for m in pend)
                falhas += len(pend) - ok
                linha.append(f"ML {ok}/{len(pend)} mês(es)")
                # extrato: primeira vez desde o início em blocos; depois, janela retroativa
                with SessionLocal() as s:
                    st = estado(s.get(Empresa, eid))
                inicio_ext = dt.date.fromisoformat(ini_cfg + "-01")
                if st.get("extrato_ate") and st.get("extrato_de", "9999") <= inicio_ext.isoformat():
                    inicio_ext = max(inicio_ext, dt.date.fromisoformat(st["extrato_ate"]) - dt.timedelta(days=EXTRATO_RETROATIVO))
                d = inicio_ext
                n_ext = n_ok = 0
                while d <= hoje:
                    d2 = min(hoje, d + dt.timedelta(days=BLOCO_EXTRATO - 1))
                    n_ext += 1
                    n_ok += tentar(eid, f"ML_EXTRATO:{d.isoformat()}:{d2.isoformat()}", ml_extrato(eid, d, d2), rid)
                    d = d2 + dt.timedelta(days=1)
                falhas += n_ext - n_ok
                linha.append(f"extrato {n_ok}/{n_ext} bloco(s)")
                # conferência: quantidade de pedidos por mês
                reimp = []
                for m in meses:
                    def conferir(m=m):
                        api, grav = conferir_pedidos(eid, m)
                        if api != grav:
                            log(f"empresa={eid} {m}: ML {api} pedidos × gravados {grav} — reimportando")
                            ml_mes(eid, m)()
                            reimp.append(m)
                            api, grav = conferir_pedidos(eid, m)
                            if api != grav:
                                raise RuntimeError(f"Depois de reimportar, o ML informa {api} pedidos em {m} e há {grav} gravados.")
                    if not tentar(eid, f"CONFERE_PEDIDOS:{m}", conferir, rid, vezes=1):  # a conferência já reimporta 1 vez
                        falhas += 1
                if reimp:
                    linha.append(f"{len(set(reimp))} mês(es) reimportado(s) na conferência")
                # conferência: vendas sem movimento no extrato
                with SessionLocal() as s:
                    st = estado(s.get(Empresa, eid))
                if st.get("extrato_ate"):
                    faltam = vendas_sem_extrato(eid, dt.date.fromisoformat(st["extrato_ate"]))
                    if faltam:
                        de_ = min(d for _o, d in faltam) - dt.timedelta(days=5)
                        ate_ = min(hoje, max(d for _o, d in faltam) + dt.timedelta(days=60))
                        tentar(eid, f"ML_EXTRATO:{de_.isoformat()}:{ate_.isoformat()}", ml_extrato(eid, de_, ate_), rid)
                        faltam = vendas_sem_extrato(eid, dt.date.fromisoformat(st["extrato_ate"]))
                    etapa = "CONFERE_EXTRATO"
                    if faltam:
                        _falha(eid, etapa, f"{len(faltam)} venda(s) paga(s) há mais de 45 dias sem nenhum movimento no extrato do "
                               "Mercado Pago (ex.: " + ", ".join(str(o) for o, _d in faltam[:10]) + "). Pode ser dinheiro retido ou "
                               "venda não identificada no extrato.", json.dumps([o for o, _d in faltam][:500]), "AVISO", 0)
                        linha.append(f"{len(faltam)} venda(s) sem extrato")
                    else:
                        _resolvida(eid, etapa)
            if tem_dom:
                pend = [m for m in meses if m not in st["dominio_meses"]]
                ini_d = pend[0] if pend else meses[max(0, len(meses) - 2)]
                ini_d = min(ini_d, meses[max(0, len(meses) - 2)])
                ok = tentar(eid, f"DOMINIO:{ini_d}:{mes_atual}", dominio_periodo(eid, ini_d, mes_atual), rid)
                falhas += 0 if ok else 1
                linha.append(f"Domínio {ini_d} a {mes_atual}: {'ok' if ok else 'falhou'}")
            resumo.append(" · ".join(linha))
        with SessionLocal() as s:
            r = s.get(RotinaExec, rid)
            r.fim, r.status = agora(), ("OK" if not falhas else "COM_FALHAS")
            r.resumo = "\n".join(resumo) or "Nenhuma empresa ativa na rotina."
            r.etapa = ""
            s.commit()
        log(f"fim — {'ok' if not falhas else f'{falhas} etapa(s) com falha'}")
        return rid
    finally:
        _trava.release()


def agendador(parar: threading.Event):
    """Laço do serviço: à 00:00 (ou ao reiniciar de madrugada sem a rotina do dia), executa."""
    retomar = False
    with SessionLocal() as s:  # execução interrompida por reinício do serviço (ex.: publicação de versão nova)
        for r in s.scalars(select(RotinaExec).where(RotinaExec.status == "EXECUTANDO")).all():
            r.status, r.fim = "INTERROMPIDA", agora()
            r.resumo = (r.resumo or "") + "\nInterrompida (o serviço reiniciou). O que faltou é retomado."
            s.commit()
            if r.inicio.date() == agora().date():
                retomar = True
    if retomar and not parar.wait(20):
        log("retomando a rotina interrompida pelo reinício do serviço")
        threading.Thread(target=executar, args=("retomada",), daemon=True).start()
    while not parar.wait(30):
        try:
            agora_ = agora()
            with SessionLocal() as s:
                ativa = get_config(s, "rotina_ativa") != "0"
                ultima = get_config(s, "rotina_ultima_data")
            if ativa and HORA <= agora_.hour < JANELA_FIM and ultima != agora_.date().isoformat():
                threading.Thread(target=executar, args=("agendada",), daemon=True).start()
                parar.wait(60)
        except Exception as ex:  # noqa: BLE001
            log(f"erro no agendador: {ex}")
