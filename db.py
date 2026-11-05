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
ORIGENS = ("BLING", "DOMINIO")
TIPOS_LINHA = ("CONTA", "TOTAL", "TITULO")


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
    """Linha do modelo. CONTA recebe valores das contas de origem; TOTAL soma/subtrai linhas anteriores
    (fórmula com os códigos, ex.: "RB + DED"); TITULO só organiza a leitura.
    sinal da CONTA: +1 soma, -1 subtrai, 0 automático (receita soma, despesa subtrai)."""
    __tablename__ = "linhas_dre"
    __table_args__ = (UniqueConstraint("modelo_id", "codigo"),)
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    modelo_id: Mapped[int] = mapped_column(ForeignKey("modelos_dre.id", ondelete="CASCADE"), index=True)
    ordem: Mapped[int] = mapped_column(Integer, default=0)
    codigo: Mapped[str] = mapped_column(String(30))
    nome: Mapped[str] = mapped_column(String(200))
    tipo: Mapped[str] = mapped_column(String(10), default="CONTA")
    sinal: Mapped[int] = mapped_column(Integer, default=1)
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
    atualizado_em: Mapped[dt.datetime] = mapped_column(DateTime, default=agora)

    @property
    def assinado(self) -> float:
        """Entrada positiva, saída negativa."""
        return float(self.valor or 0) * (1 if self.deb_cred == "C" else -1)


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
