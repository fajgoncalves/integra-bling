"""Integra Bling — modelo de dados (SQLAlchemy 2).

DRE multiempresa: cada empresa tem a própria conexão com o Bling (OAuth 2.0) e o próprio
de-para entre as contas de origem (categorias do Bling e grupos da Domínio) e as linhas do
modelo de DRE escolhido para ela.
"""
import os
import datetime as dt
from zoneinfo import ZoneInfo

from sqlalchemy import (create_engine, String, Integer, BigInteger, Boolean, Date, DateTime, Numeric, Text,
                        ForeignKey, UniqueConstraint, Index, event)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, sessionmaker


def _db_url() -> str:
    url = os.environ.get("DATABASE_URL", "sqlite:///./integra_bling.db")
    if url.startswith("postgres://"):
        url = "postgresql://" + url[len("postgres://"):]
    if url.startswith("postgresql://"):
        url = "postgresql+psycopg://" + url[len("postgresql://"):]
    return url


DB_URL = _db_url()
engine = create_engine(DB_URL, pool_pre_ping=True, future=True,
                       connect_args={"check_same_thread": False} if DB_URL.startswith("sqlite") else {})
if DB_URL.startswith("sqlite"):
    @event.listens_for(engine, "connect")
    def _fk(dbapi_con, _):
        dbapi_con.execute("PRAGMA foreign_keys=ON")

SessionLocal = sessionmaker(bind=engine, expire_on_commit=False, autoflush=False, future=True)
FUSO = ZoneInfo("America/Sao_Paulo")


def agora():
    """Horário de Brasília, sem fuso no valor."""
    return dt.datetime.now(FUSO).replace(tzinfo=None)


class Base(DeclarativeBase):
    pass


PERFIS = ("ADMIN", "OPERADOR")
ORIGENS = ("BLING", "DOMINIO", "MLIVRE")
TIPOS_LINHA = ("GRUPO", "CONTA", "TOTAL")


class Usuario(Base):
    __tablename__ = "usuarios"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    nome: Mapped[str] = mapped_column(String(120))
    email: Mapped[str] = mapped_column(String(200), unique=True)
    senha_hash: Mapped[str] = mapped_column(String(200))
    perfil: Mapped[str] = mapped_column(String(20), default="OPERADOR")
    ativo: Mapped[bool] = mapped_column(Boolean, default=True)
    criado_em: Mapped[dt.datetime] = mapped_column(DateTime, default=agora)


class Config(Base):
    __tablename__ = "config"
    chave: Mapped[str] = mapped_column(String(60), primary_key=True)
    valor: Mapped[str] = mapped_column(Text, default="")


class ModeloDRE(Base):
    __tablename__ = "modelos_dre"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    nome: Mapped[str] = mapped_column(String(120))
    descricao: Mapped[str] = mapped_column(Text, default="")
    criado_em: Mapped[dt.datetime] = mapped_column(DateTime, default=agora)


class LinhaDRE(Base):
    """Linha do modelo.
    GRUPO: soma automática das contas dele. CONTA: fica dentro de um grupo (pai_id) e recebe as associações.
    TOTAL (resultado): fórmula com códigos de linhas anteriores, ex.: "RB + DED".
    sinal da CONTA: 0 automático (entrada +, saída −), +1 sempre soma, -1 sempre subtrai.
    A ordem (ordem) é a da leitura: grupo, as contas dele, próximo grupo ou resultado..."""
    __tablename__ = "linhas_dre"
    __table_args__ = (UniqueConstraint("modelo_id", "codigo"),)
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    modelo_id: Mapped[int] = mapped_column(ForeignKey("modelos_dre.id", ondelete="CASCADE"), index=True)
    pai_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    ordem: Mapped[int] = mapped_column(Integer, default=0)
    codigo: Mapped[str] = mapped_column(String(30))
    nome: Mapped[str] = mapped_column(String(200))
    tipo: Mapped[str] = mapped_column(String(10), default="CONTA")
    sinal: Mapped[int] = mapped_column(Integer, default=0)
    formula: Mapped[str] = mapped_column(Text, default="")
    destaque: Mapped[bool] = mapped_column(Boolean, default=False)
    base_av: Mapped[bool] = mapped_column(Boolean, default=False)  # base da análise vertical (100%)


class Empresa(Base):
    __tablename__ = "empresas"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    nome: Mapped[str] = mapped_column(String(200))
    cnpj: Mapped[str] = mapped_column(String(14), default="")
    codigo_dominio: Mapped[str] = mapped_column(String(20), default="")
    modelo_id: Mapped[int | None] = mapped_column(ForeignKey("modelos_dre.id", ondelete="SET NULL"), nullable=True)
    ativo: Mapped[bool] = mapped_column(Boolean, default=True)
    # Bling: aplicativo próprio da empresa (opcional; sem ele vale o aplicativo padrão das Configurações)
    bling_client_id: Mapped[str] = mapped_column(String(200), default="")
    bling_client_secret: Mapped[str] = mapped_column(Text, default="")  # cifrado
    bling_access: Mapped[str] = mapped_column(Text, default="")  # cifrado
    bling_refresh: Mapped[str] = mapped_column(Text, default="")  # cifrado
    bling_expira: Mapped[dt.datetime | None] = mapped_column(DateTime, nullable=True)
    bling_conectado_em: Mapped[dt.datetime | None] = mapped_column(DateTime, nullable=True)
    bling_conta: Mapped[str] = mapped_column(String(200), default="")
    bling_sync_em: Mapped[dt.datetime | None] = mapped_column(DateTime, nullable=True)
    bling_sync_periodo: Mapped[str] = mapped_column(String(30), default="")
    pis_cofins_aliq: Mapped[float] = mapped_column(Numeric(7, 4), default=9.25)  # % sobre faturamento − devoluções
    pis_cofins_fonte: Mapped[str] = mapped_column(String(10), default="APURACAO")  # APURACAO (Domínio) ou ALIQUOTA
    # Mercado Livre (OAuth por empresa; o aplicativo fica nas Configurações)
    ml_access: Mapped[str] = mapped_column(Text, default="")  # cifrado
    ml_refresh: Mapped[str] = mapped_column(Text, default="")  # cifrado
    ml_expira: Mapped[dt.datetime | None] = mapped_column(DateTime, nullable=True)
    ml_user_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    ml_apelido: Mapped[str] = mapped_column(String(120), default="")
    ml_conectado_em: Mapped[dt.datetime | None] = mapped_column(DateTime, nullable=True)
    ml_sync_em: Mapped[dt.datetime | None] = mapped_column(DateTime, nullable=True)
    ml_sync_periodo: Mapped[str] = mapped_column(String(30), default="")
    ml_imposto_aliq: Mapped[float] = mapped_column(Numeric(7, 4), default=0)  # % de impostos sobre vendas (DRE do ML)
    ml_regras: Mapped[str] = mapped_column(Text, default="")  # JSON: comissão acordada por tipo de anúncio etc. (auditoria)
    dominio_sync_em: Mapped[dt.datetime | None] = mapped_column(DateTime, nullable=True)
    dominio_sync_periodo: Mapped[str] = mapped_column(String(30), default="")
    criado_em: Mapped[dt.datetime] = mapped_column(DateTime, default=agora)


class ContaOrigem(Base):
    """Categoria do Bling ou grupo da Domínio de uma empresa, com a linha da DRE associada."""
    __tablename__ = "contas_origem"
    __table_args__ = (UniqueConstraint("empresa_id", "origem", "codigo"),)
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    empresa_id: Mapped[int] = mapped_column(ForeignKey("empresas.id", ondelete="CASCADE"), index=True)
    origem: Mapped[str] = mapped_column(String(10))
    codigo: Mapped[str] = mapped_column(String(40))
    descricao: Mapped[str] = mapped_column(String(300))
    tipo: Mapped[str] = mapped_column(String(2), default="D")  # R receita, D despesa, RD as duas
    pai_codigo: Mapped[str] = mapped_column(String(40), default="")
    ativa: Mapped[bool] = mapped_column(Boolean, default=True)
    linha_id: Mapped[int | None] = mapped_column(ForeignKey("linhas_dre.id", ondelete="SET NULL"), nullable=True)
    fluxo_secao: Mapped[str | None] = mapped_column(String(10), nullable=True)  # ENTRADAS/SAIDAS no fluxo de caixa
    fluxo_ordem: Mapped[int] = mapped_column(Integer, default=0)
    atualizado_em: Mapped[dt.datetime] = mapped_column(DateTime, default=agora)


class Movimento(Base):
    """Lançamento de Caixa e Bancos do Bling (pagamento ou recebimento efetivado).
    valor é sempre positivo; deb_cred C = entrada (crédito), D = saída (débito)."""
    __tablename__ = "movimentos_caixa"
    __table_args__ = (UniqueConstraint("empresa_id", "id_externo"),
                      Index("ix_mov_emp_cat", "empresa_id", "categoria_codigo"),
                      Index("ix_mov_emp_data", "empresa_id", "data"))
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    empresa_id: Mapped[int] = mapped_column(ForeignKey("empresas.id", ondelete="CASCADE"), index=True)
    id_externo: Mapped[int] = mapped_column(BigInteger)
    data: Mapped[dt.date] = mapped_column(Date)
    deb_cred: Mapped[str] = mapped_column(String(1))
    valor: Mapped[float] = mapped_column(Numeric(15, 2), default=0)
    categoria_codigo: Mapped[str] = mapped_column(String(40), default="")
    conta_financeira: Mapped[str] = mapped_column(String(200), default="")
    contato: Mapped[str] = mapped_column(String(200), default="")
    descricao: Mapped[str] = mapped_column(Text, default="")
    origem_tipo: Mapped[str] = mapped_column(String(20), default="")
    conta_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)  # conta financeira no Bling
    categorizado: Mapped[bool] = mapped_column(Boolean, default=True)  # False: só histórico de saldo (categoria não lida)
    atualizado_em: Mapped[dt.datetime] = mapped_column(DateTime, default=agora)

    @property
    def assinado(self) -> float:
        """Entrada positiva, saída negativa."""
        return float(self.valor or 0) * (1 if self.deb_cred == "C" else -1)


class ContaFinanceira(Base):
    """Conta de Caixa e Bancos do Bling."""
    __tablename__ = "contas_financeiras"
    __table_args__ = (UniqueConstraint("empresa_id", "id_externo"),)
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    empresa_id: Mapped[int] = mapped_column(ForeignKey("empresas.id", ondelete="CASCADE"), index=True)
    id_externo: Mapped[int] = mapped_column(BigInteger)
    descricao: Mapped[str] = mapped_column(String(200), default="")
    tipo: Mapped[str] = mapped_column(String(30), default="")
    ativa: Mapped[bool] = mapped_column(Boolean, default=True)
    ordem: Mapped[int] = mapped_column(Integer, default=0)


class DominioValor(Base):
    """Totais da Domínio por mês e CFOP (grupo SAIDA, ENTRADA ou SERVICO)."""
    __tablename__ = "dominio_valores"
    __table_args__ = (UniqueConstraint("empresa_id", "mes", "grupo", "cfop"),)
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    empresa_id: Mapped[int] = mapped_column(ForeignKey("empresas.id", ondelete="CASCADE"), index=True)
    mes: Mapped[str] = mapped_column(String(7))
    grupo: Mapped[str] = mapped_column(String(10))
    cfop: Mapped[int] = mapped_column(Integer, default=0)
    valor: Mapped[float] = mapped_column(Numeric(15, 2), default=0)
    qtd: Mapped[int] = mapped_column(Integer, default=0)


class DominioItem(Base):
    """Itens das notas da Domínio por mês, lado (S saída, E entrada), CFOP e produto — base do CMV."""
    __tablename__ = "dominio_itens"
    __table_args__ = (UniqueConstraint("empresa_id", "mes", "lado", "cfop", "produto"),
                      Index("ix_ditem_emp_mes", "empresa_id", "mes"))
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    empresa_id: Mapped[int] = mapped_column(ForeignKey("empresas.id", ondelete="CASCADE"), index=True)
    mes: Mapped[str] = mapped_column(String(7))
    lado: Mapped[str] = mapped_column(String(1))
    cfop: Mapped[int] = mapped_column(Integer, default=0)
    produto: Mapped[str] = mapped_column(String(40))
    qtd: Mapped[float] = mapped_column(Numeric(18, 4), default=0)
    valor: Mapped[float] = mapped_column(Numeric(15, 2), default=0)


class Produto(Base):
    """Produto da empresa (vindo das notas da Domínio) com o custo unitário informado pelo usuário."""
    __tablename__ = "produtos"
    __table_args__ = (UniqueConstraint("empresa_id", "produto"),)
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    empresa_id: Mapped[int] = mapped_column(ForeignKey("empresas.id", ondelete="CASCADE"), index=True)
    produto: Mapped[str] = mapped_column(String(40))  # código interno da Domínio (codi_pdi)
    codigo: Mapped[str] = mapped_column(String(60), default="")
    descricao: Mapped[str] = mapped_column(String(200), default="")
    unidade: Mapped[str] = mapped_column(String(10), default="")
    custo: Mapped[float | None] = mapped_column(Numeric(15, 4), nullable=True)  # legado (antes das vigências)
    custo_em: Mapped[dt.datetime | None] = mapped_column(DateTime, nullable=True)
    custo_por: Mapped[str] = mapped_column(String(200), default="")


class ProdutoCusto(Base):
    """Custo unitário do produto a partir de um mês (vale até a próxima vigência)."""
    __tablename__ = "produtos_custos"
    __table_args__ = (UniqueConstraint("produto_id", "mes_inicio"),)
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    produto_id: Mapped[int] = mapped_column(ForeignKey("produtos.id", ondelete="CASCADE"), index=True)
    mes_inicio: Mapped[str] = mapped_column(String(7))
    custo: Mapped[float] = mapped_column(Numeric(15, 4))
    criado_em: Mapped[dt.datetime] = mapped_column(DateTime, default=agora)
    criado_por: Mapped[str] = mapped_column(String(200), default="")


class MLVenda(Base):
    """Pedido do Mercado Livre (venda)."""
    __tablename__ = "ml_vendas"
    __table_args__ = (UniqueConstraint("empresa_id", "order_id"), Index("ix_mlv_emp_data", "empresa_id", "data"))
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    empresa_id: Mapped[int] = mapped_column(ForeignKey("empresas.id", ondelete="CASCADE"), index=True)
    order_id: Mapped[int] = mapped_column(BigInteger)
    pack_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    data: Mapped[dt.date] = mapped_column(Date)
    status: Mapped[str] = mapped_column(String(30), default="")
    total: Mapped[float] = mapped_column(Numeric(15, 2), default=0)
    pago: Mapped[float] = mapped_column(Numeric(15, 2), default=0)
    devolvido: Mapped[float] = mapped_column(Numeric(15, 2), default=0)
    comissao: Mapped[float] = mapped_column(Numeric(15, 2), default=0)  # soma de sale_fee × quantidade
    itens: Mapped[int] = mapped_column(Integer, default=0)
    descricao: Mapped[str] = mapped_column(String(300), default="")
    comprador: Mapped[str] = mapped_column(String(120), default="")
    shipping_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    frete: Mapped[float | None] = mapped_column(Numeric(15, 2), nullable=True)  # custo do envio para o vendedor (None = não lido)
    pagamentos: Mapped[str] = mapped_column(String(300), default="")  # ids dos pagamentos no Mercado Pago, separados por vírgula


class MLItem(Base):
    """Item de um pedido do Mercado Livre (base da análise por produto)."""
    __tablename__ = "ml_itens"
    __table_args__ = (Index("ix_mli_emp_order", "empresa_id", "order_id"),)
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    empresa_id: Mapped[int] = mapped_column(ForeignKey("empresas.id", ondelete="CASCADE"), index=True)
    order_id: Mapped[int] = mapped_column(BigInteger)
    anuncio: Mapped[str] = mapped_column(String(30), default="")  # MLB...
    sku: Mapped[str] = mapped_column(String(80), default="")
    titulo: Mapped[str] = mapped_column(String(300), default="")
    tipo_anuncio: Mapped[str] = mapped_column(String(30), default="")  # gold_special (clássico), gold_pro (premium)
    qtd: Mapped[float] = mapped_column(Numeric(15, 4), default=0)
    preco: Mapped[float] = mapped_column(Numeric(15, 2), default=0)  # unitário
    tarifa: Mapped[float] = mapped_column(Numeric(15, 2), default=0)  # tarifa de venda unitária
    categoria: Mapped[str] = mapped_column(String(30), default="")


class MLCusto(Base):
    """Custo unitário de um produto do Mercado Livre (por SKU, ou pelo anúncio quando não há SKU), com vigência mensal."""
    __tablename__ = "ml_custos"
    __table_args__ = (UniqueConstraint("empresa_id", "chave", "mes_inicio"),)
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    empresa_id: Mapped[int] = mapped_column(ForeignKey("empresas.id", ondelete="CASCADE"), index=True)
    chave: Mapped[str] = mapped_column(String(80))
    mes_inicio: Mapped[str] = mapped_column(String(7))
    custo: Mapped[float] = mapped_column(Numeric(15, 4))
    criado_por: Mapped[str] = mapped_column(String(120), default="")
    criado_em: Mapped[dt.datetime] = mapped_column(DateTime, default=lambda: agora())


class MLNota(Base):
    """Nota fiscal de uma venda do Mercado Livre (emitida pelo ML ou informada no envio por um ERP, ex.: Bling)."""
    __tablename__ = "ml_notas"
    __table_args__ = (UniqueConstraint("empresa_id", "order_id", "chave", "numero"),)
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    empresa_id: Mapped[int] = mapped_column(ForeignKey("empresas.id", ondelete="CASCADE"), index=True)
    order_id: Mapped[int] = mapped_column(BigInteger, index=True)
    origem: Mapped[str] = mapped_column(String(20), default="")  # EMISSOR_ML ou ENVIO (nota informada no envio)
    tipo: Mapped[str] = mapped_column(String(20), default="venda")  # venda / devolucao
    numero: Mapped[str] = mapped_column(String(20), default="")
    serie: Mapped[str] = mapped_column(String(10), default="")
    chave: Mapped[str] = mapped_column(String(44), default="")
    status: Mapped[str] = mapped_column(String(30), default="")
    data: Mapped[dt.date | None] = mapped_column(Date, nullable=True)
    valor: Mapped[float | None] = mapped_column(Numeric(15, 2), nullable=True)
    invoice_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)  # id da nota no emissor do ML
    xml: Mapped[str] = mapped_column(Text, default="")  # XML autorizado (guardado na 1ª consulta do DANFE)


class MLCobranca(Base):
    """Linha do detalhe do faturamento do ML/MP (cada cobrança ou bonificação, ligada ao pedido quando houver)."""
    __tablename__ = "ml_cobrancas"
    __table_args__ = (UniqueConstraint("empresa_id", "grupo", "detail_id"), Index("ix_mlc_emp_order", "empresa_id", "order_id"))
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    empresa_id: Mapped[int] = mapped_column(ForeignKey("empresas.id", ondelete="CASCADE"), index=True)
    periodo: Mapped[str] = mapped_column(String(7))
    grupo: Mapped[str] = mapped_column(String(4))
    documento: Mapped[str] = mapped_column(String(20), default="BILL")  # BILL ou CREDIT_NOTE (estorno)
    detail_id: Mapped[int] = mapped_column(BigInteger)
    natureza: Mapped[str] = mapped_column(String(10), default="CHARGE")  # CHARGE / BONUS
    tipo: Mapped[str] = mapped_column(String(20), default="")  # CV, CXD, CFONPN...
    descricao: Mapped[str] = mapped_column(String(200), default="")
    valor: Mapped[float] = mapped_column(Numeric(15, 2), default=0)  # com sinal: cobrança −, bonificação/estorno +
    data: Mapped[dt.date | None] = mapped_column(Date, nullable=True)
    order_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    shipping_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    debitada: Mapped[bool | None] = mapped_column(Boolean, nullable=True)  # descontada na própria operação (extrato)


class MLExtrato(Base):
    """Linha do extrato do Mercado Pago (relatório de liberações), ligada ao pedido quando identificada."""
    __tablename__ = "ml_extrato"
    __table_args__ = (Index("ix_mle_emp_data", "empresa_id", "data"),)
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    empresa_id: Mapped[int] = mapped_column(ForeignKey("empresas.id", ondelete="CASCADE"), index=True)
    data: Mapped[dt.datetime] = mapped_column(DateTime)
    origem_id: Mapped[str] = mapped_column(String(40), default="")  # SOURCE_ID (pagamento, reembolso, saque...)
    referencia: Mapped[str] = mapped_column(String(80), default="")  # EXTERNAL_REFERENCE
    tipo_registro: Mapped[str] = mapped_column(String(30), default="")  # RECORD_TYPE
    descricao: Mapped[str] = mapped_column(String(60), default="")  # DESCRIPTION (payment, refund, shipping, payout...)
    credito: Mapped[float] = mapped_column(Numeric(15, 2), default=0)
    debito: Mapped[float] = mapped_column(Numeric(15, 2), default=0)
    bruto: Mapped[float] = mapped_column(Numeric(15, 2), default=0)
    tarifa_mp: Mapped[float] = mapped_column(Numeric(15, 2), default=0)
    financiamento: Mapped[float] = mapped_column(Numeric(15, 2), default=0)
    frete: Mapped[float] = mapped_column(Numeric(15, 2), default=0)
    impostos: Mapped[float] = mapped_column(Numeric(15, 2), default=0)
    cupom: Mapped[float] = mapped_column(Numeric(15, 2), default=0)
    saldo: Mapped[float | None] = mapped_column(Numeric(15, 2), nullable=True)
    meio: Mapped[str] = mapped_column(String(40), default="")
    order_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True, index=True)
    shipping_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    pack_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    identificacao: Mapped[str] = mapped_column(String(30), default="")  # como foi ligado ao pedido
    fonte: Mapped[str] = mapped_column(String(10), default="API")  # API ou ARQUIVO


class DominioNota(Base):
    """Nota escriturada na Domínio (lista com a chave de acesso) — base do cruzamento com as vendas do ML."""
    __tablename__ = "dominio_notas"
    __table_args__ = (Index("ix_dn_emp_chave", "empresa_id", "chave"), Index("ix_dn_emp_data", "empresa_id", "data"))
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    empresa_id: Mapped[int] = mapped_column(ForeignKey("empresas.id", ondelete="CASCADE"), index=True)
    lado: Mapped[str] = mapped_column(String(1))  # S saída, E entrada (devolução)
    data: Mapped[dt.date] = mapped_column(Date)
    numero: Mapped[str] = mapped_column(String(20), default="")
    serie: Mapped[str] = mapped_column(String(10), default="")
    chave: Mapped[str] = mapped_column(String(44), default="")
    cfop: Mapped[int] = mapped_column(Integer, default=0)
    valor: Mapped[float] = mapped_column(Numeric(15, 2), default=0)
    cancelada: Mapped[bool] = mapped_column(Boolean, default=False)


class MLTarifa(Base):
    """Encargo (charge) ou bonificação (bonus) do relatório de faturamento do Mercado Livre / Mercado Pago."""
    __tablename__ = "ml_tarifas"
    __table_args__ = (UniqueConstraint("empresa_id", "periodo", "grupo", "natureza", "tipo", "rotulo"),)
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    empresa_id: Mapped[int] = mapped_column(ForeignKey("empresas.id", ondelete="CASCADE"), index=True)
    periodo: Mapped[str] = mapped_column(String(7))  # AAAA-MM (key do período de faturamento)
    grupo: Mapped[str] = mapped_column(String(4))  # ML ou MP
    natureza: Mapped[str] = mapped_column(String(6))  # CHARGE ou BONUS
    tipo: Mapped[str] = mapped_column(String(20))  # CV, CXD, PADS, BXD...
    rotulo: Mapped[str] = mapped_column(String(200), default="")
    valor: Mapped[float] = mapped_column(Numeric(15, 2), default=0)
    de: Mapped[dt.date | None] = mapped_column(Date, nullable=True)
    ate: Mapped[dt.date | None] = mapped_column(Date, nullable=True)


class Job(Base):
    """Atividade em segundo plano (sincronização com o Bling, leitura da Domínio)."""
    __tablename__ = "jobs"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    empresa_id: Mapped[int | None] = mapped_column(ForeignKey("empresas.id", ondelete="CASCADE"), nullable=True, index=True)
    tipo: Mapped[str] = mapped_column(String(30))
    status: Mapped[str] = mapped_column(String(20), default="PENDENTE")  # PENDENTE, EXECUTANDO, OK, ERRO
    parametros: Mapped[str] = mapped_column(Text, default="")
    progresso: Mapped[int] = mapped_column(Integer, default=0)
    total: Mapped[int] = mapped_column(Integer, default=0)
    mensagem: Mapped[str] = mapped_column(Text, default="")
    criado_por: Mapped[str] = mapped_column(String(200), default="")
    criado_em: Mapped[dt.datetime] = mapped_column(DateTime, default=agora)
    fim: Mapped[dt.datetime | None] = mapped_column(DateTime, nullable=True)


class Auditoria(Base):
    __tablename__ = "auditoria"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    quando: Mapped[dt.datetime] = mapped_column(DateTime, default=agora)
    usuario: Mapped[str] = mapped_column(String(200), default="")
    acao: Mapped[str] = mapped_column(String(60))
    detalhe: Mapped[str] = mapped_column(Text, default="")


def criar_tabelas():
    Base.metadata.create_all(engine)
    # colunas novas em tabelas que já existiam
    from sqlalchemy import inspect, text
    novas = {"linhas_dre": [("pai_id", "INTEGER")],
             "movimentos_caixa": [("conta_id", "BIGINT"), ("categorizado", "BOOLEAN DEFAULT TRUE")],
             "contas_origem": [("fluxo_secao", "VARCHAR(10)"), ("fluxo_ordem", "INTEGER DEFAULT 0")],
             "empresas": [("pis_cofins_aliq", "NUMERIC(7,4) DEFAULT 9.25"),
                          ("pis_cofins_fonte", "VARCHAR(10) DEFAULT 'APURACAO'"),
                          ("ml_access", "TEXT DEFAULT ''"), ("ml_refresh", "TEXT DEFAULT ''"), ("ml_expira", "TIMESTAMP"),
                          ("ml_user_id", "BIGINT"), ("ml_apelido", "VARCHAR(120) DEFAULT ''"),
                          ("ml_conectado_em", "TIMESTAMP"), ("ml_sync_em", "TIMESTAMP"),
                          ("ml_sync_periodo", "VARCHAR(30) DEFAULT ''"),
                          ("ml_imposto_aliq", "NUMERIC(7,4) DEFAULT 0"), ("ml_regras", "TEXT DEFAULT ''")],
             "ml_vendas": [("frete", "NUMERIC(15,2)"), ("pagamentos", "VARCHAR(300) DEFAULT ''")],
             "ml_itens": [("categoria", "VARCHAR(30) DEFAULT ''")],
             "ml_notas": [("invoice_id", "BIGINT"), ("xml", "TEXT DEFAULT ''")]}
    insp = inspect(engine)
    for tab, cols in novas.items():
        tem = {c["name"] for c in insp.get_columns(tab)}
        for nome, tipo in cols:
            if nome not in tem:
                with engine.begin() as con:
                    con.execute(text(f"ALTER TABLE {tab} ADD COLUMN {nome} {tipo}"))
