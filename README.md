# Integra Bling — Alta Visão Contabilidade

DRE multiempresa montada com os dados do **Bling** (categorias financeiras e lançamentos de **Caixa e Bancos** — só o que foi pago ou recebido) e da **Domínio** (faturamento e devoluções, pela Central de Inteligência 360 e o Agente 360).

## Como funciona
1. **Empresas**: cada empresa tem CNPJ, código da Domínio, um **modelo de DRE** e a própria conexão OAuth com o Bling.
2. **Conexões e dados**: *Conectar ao Bling* (o usuário do Bling da empresa autoriza a leitura) → *Atualizar do Bling* (categorias + lançamentos de Caixa e Bancos do período, separados por categoria com o filtro do próprio Bling; contas a pagar/receber em aberto não entram). *Ler da Domínio* pede à Central a tarefa `dre_dominio`; o Agente 360 devolve os totais por mês e CFOP.
3. **Associação de contas**: de um lado as contas de origem (categorias do Bling e grupos da Domínio), do outro a linha da DRE. Botão **Consultar** mostra os lançamentos de caixa (Bling) ou os CFOPs (Domínio) de cada conta. Dá para copiar as associações de outra empresa (casando pelo nome).
4. **Modelos de DRE**: linhas do tipo *Conta* (recebem as associações; sinal automático = entrada +, saída −), *Total* (fórmula com códigos das linhas de cima, ex. `RB + DED_DEV`) e *Título*. Uma linha marcada como base da análise vertical (AV).
5. **DRE**: mês a mês — Bling pela data do pagamento/recebimento (regime de caixa), Domínio pelo mês da nota — com análise vertical, composição de cada linha, contas sem associação e exportação para Excel.

### Grupos da Domínio
| Conta | Regra |
|---|---|
| Vendas | Saídas CFOP 5/6/7.1xx, .4xx (menos devoluções), .65x |
| Serviços prestados | Tabelas de serviços (NFS-e) + CFOP x.932/x.933 |
| Devoluções de venda | Entradas 1/2.201–204, 1/2.410–411, 3.201–202 |
| Devoluções de compra | Saídas x.201–212, x.410–413, x.503, x.553, x.555, x.556, x.660–662 |
| Outras saídas | Demais saídas (remessas, transferências, ativo...) — por padrão fora da DRE |

## Publicar no Railway
1. Projeto com um serviço apontando para este repositório + banco **Postgres**.
2. Variáveis do serviço: `DATABASE_URL = ${{Postgres.DATABASE_URL}}`, `SECRET_KEY` (texto aleatório longo; cifra tokens e segredos — não troque depois) e, opcional, `PUBLIC_URL` (endereço público, usado no link de retorno do Bling).
3. Gere um domínio. No primeiro acesso a tela pede para criar o administrador.

## Configurar
- **Bling** (Configurações): no Bling, *Central de Extensões → Área do Integrador → Criar aplicativo* (API, privado), link de redirecionamento `https://SEU-DOMINIO/bling/callback`, escopos de leitura de *Categorias financeiras* e *Caixas e bancos* (lançamentos). Copie Client ID e Client Secret. Aplicativo privado só é autorizado pela conta que o criou; empresa com conta Bling própria pode ter o próprio aplicativo (cadastro da empresa).
- **Central 360** (Configurações): endereço da Central + chave gerada em *Administração → Gerar chave*, tipo **Integra Bling**. Exige Agente 360 0.13.0 ou superior.

## Testes
`python -m pytest -q tests/` — fluxo completo com Bling e Central simulados.
