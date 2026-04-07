# Bybit Trade Bot (Fundação)

Projeto em Python para processar sinais de trade recebidos via Telegram, com evolução incremental por fases.

## Escopo desta fase

- Listener real de mensagens com **Telethon**.
- Escuta de **um único chat/canal** configurado em `.env`.
- Recebimento de texto bruto e envio ao parser existente (`VectraSignalParser`).
- Integração **read-only** com Bybit API V5 via **pybit** para validar entrada tardia.
- Consulta de preço atual do símbolo do sinal (escopo atual: `category=linear`).
- Consulta de informações do instrumento/símbolo (escopo atual: `category=linear`).
- Camada de **planejamento de execução** (`ExecutionPlan`) para normalizar preços e qty.
- Validação pré-envio no plano para mínimos do instrumento (`minOrderQty` e `minNotionalValue`) antes de tentar `place_order`.
- Primeira camada de **escrita restrita à Bybit testnet** para enviar **apenas ordem de entrada** (`Market`).
- Proteções obrigatórias de execução:
  - bloqueio quando `DRY_RUN=true`;
  - bloqueio quando `ENABLE_ORDER_EXECUTION=false`;
  - bloqueio quando `ENABLE_ORDER_EXECUTION=true` e `BYBIT_TESTNET=false`;
  - bloqueio quando `ExecutionPlan` não for elegível.
- Log estruturado do resultado da tentativa de execução (`ExecutionResult`).
- Journal local estruturado por execução/trade em arquivo JSON (auditoria e diagnóstico), sem banco/painel/analytics avançada nesta fase.
- Journal local estruturado por execução/trade em arquivo JSON com schema padronizado por blocos (`signal`, `plan`, `execution`, `monitor`, `cleanup`, `errors`, `summary`) e `tradeStatus` final normalizado para auditoria/debug.
- Em falha de parsing, log de erro claro e continuidade do loop.
- Proteção pós-confirmação: configuração automática de **stop loss** na posição via `Set Trading Stop`.
- Take profits parciais pós-confirmação com **4 ordens Limit** separadas em `category=linear`, `positionIdx=0`, `reduceOnly=true` (distribuição configurável por `.env`).
- Limpeza incremental de ordens penduradas com foco nos IDs de TP da execução atual (REST curto e controlado, sem monitor contínuo).
- Monitor curto da execução atual após entrada+proteções para acompanhar fechamento da posição e concluir cleanup com janela limitada.
- Monitor curto preferencial via **websocket privado Bybit V5** (`position`, `order` e assinatura opcional de `execution`) restrito à execução atual, com fallback REST seguro.
- No monitor websocket-first desta fase, `position` é a fonte de verdade para fechamento final; `order` e `execution` são complementares para telemetria/rastreio de ordens e fills da execução atual.
- **Sem trailing stop nesta fase**.
- **Sem monitor contínuo global de posição nesta fase**.
- Confirmação pós-ACK implementada com polling REST curto e controlado (sem websocket).

## Requisitos

- Python 3.11+
- `pytest`
- `python-dotenv`
- `telethon`
- `pybit`

## Instalação

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

## Configuração

1. Copie o arquivo de exemplo:

```bash
cp .env.example .env
```

2. Preencha os campos de Telegram no `.env`:

- `TELEGRAM_API_ID`: ID da aplicação Telegram (obtido no portal oficial).
- `TELEGRAM_API_HASH`: hash da aplicação Telegram.
- `TELEGRAM_SESSION_NAME`: nome local da sessão Telethon.
- `TELEGRAM_SOURCE_CHAT`: chat/canal alvo no formato **`@username` público** ou **inteiro numérico válido** (incluindo sinal, como `-100...`, quando aplicável).

3. Configure os campos da Bybit:

- `BYBIT_TESTNET=true`
- `BYBIT_API_KEY`
- `BYBIT_API_SECRET`

> A validação read-only funciona sem autenticação, mas a escrita de ordem exige credenciais válidas.

4. Configure proteções de execução:

- `DRY_RUN=true` mantém bloqueio total de envio.
- `ENABLE_ORDER_EXECUTION=false` mantém bloqueio total de envio.
- Para liberar envio em testnet, as três condições devem ser atendidas ao mesmo tempo:
  - `DRY_RUN=false`
  - `ENABLE_ORDER_EXECUTION=true`
  - `BYBIT_TESTNET=true`

5. Configure sizing para planejamento:

- `EXECUTION_SIZING_MODE=fixed_notional_usdt` (padrão e recomendado nesta fase).
- `EXECUTION_FIXED_NOTIONAL_USDT` (ex.: `25`), usado com `fixed_notional_usdt`.
- `EXECUTION_FIXED_QTY` (ex.: `0.01`), usado apenas com `fixed_qty`.
- Distribuição dos 4 TPs parciais:
  - `TP1_PERCENT` (padrão `50`)
  - `TP2_PERCENT` (padrão `20`)
  - `TP3_PERCENT` (padrão `20`)
  - `TP4_PERCENT` (padrão `10`)
  - regra obrigatória: soma = `100`.

## Executar listener

```bash
python -m src.main
```

Na primeira execução, o Telethon pode solicitar autenticação da conta para criar a sessão local.
No startup, o listener valida/resolve `TELEGRAM_SOURCE_CHAT`; se o valor for inválido, o processo encerra com erro de configuração claro (sem traceback como fluxo principal).

Nota: o journal local por execução é salvo automaticamente em `runtime/journal/` para auditoria e diagnóstico; nesta fase ainda não há banco de dados, dashboard ou analytics avançada.


## Modos de sinal

- **telegram** (padrão): fluxo atual com Telethon em `python -m src.main`.
- **auto_analysis**: geração interna de sinal para `BTCUSDT`/`linear` em candle fechado da Bybit (`bootstrap` REST + atualização por websocket público de kline) em `python -m src.main_auto`.

Variáveis novas relevantes:
- `SIGNAL_SOURCE=telegram|auto_analysis`
- `AUTO_ANALYSIS_ENABLED`
- `AUTO_ANALYSIS_SYMBOL` (fixo nesta fase: `BTCUSDT`)
- `AUTO_ANALYSIS_INTERVAL` (padrão `60`)
- parâmetros técnicos `AUTO_ANALYSIS_*` (EMA/MACD/RSI/volume/ADX/cooldown)

Observação: o modo `auto_analysis` continua **safe-by-default** e respeita as proteções já existentes (`DRY_RUN`, `ENABLE_ORDER_EXECUTION`, `BYBIT_TESTNET`).
Nesta fase, os níveis de `stop_loss` e `take_profits` gerados no `auto_analysis` são **provisórios** para integração operacional com o executor atual e auditoria do fluxo; ainda não representam paridade 1:1 com a lógica dinâmica de break-even/step stop do Pine.

## Comportamento em runtime

- Nova mensagem chega no chat/canal configurado.
- O texto bruto (`raw_text`) é enviado para o parser.
- Se o parsing for válido, o sinal é enriquecido com validação read-only da Bybit:
  - consulta de instrumento (`linear`);
  - consulta de preço atual (`linear`);
  - captura de metadados básicos do instrumento (`status`, `tickSize`, `qtyStep`);
  - captura de metadados de mínimos do instrumento (`minOrderQty`, `minNotionalValue`);
  - validação da janela de entrada.
- O sinal enriquecido é convertido em `ExecutionPlan`:
  - normalização de preços por `tickSize` com regras explícitas por contexto (entrada, stop e take profit);
  - normalização de quantity por `qtyStep`;
  - cálculo de quantity por sizing fixo configurado.
  - validação de elegibilidade por mínimos do instrumento:
    - bloqueia quando `planned_quantity < minOrderQty`;
    - bloqueia quando `reference_price * planned_quantity < minNotionalValue`.
- O executor avalia proteções e elegibilidade:
  - se bloqueado por proteção, registra o motivo;
  - se elegível e desbloqueado, envia ordem de entrada `Market` em `category=linear` com one-way (`positionIdx=0`).
- O resultado estruturado (`ExecutionResult`) separa:
  - tentativa de ordem (`order_attempted`);
  - submissão aceita pela API (`order_sent` / ACK inicial);
  - confirmação pós-ACK (`order_confirmed`) via REST com status explícito: `pending_confirmation`, `confirmed`, `rejected`, `cancelled`, `not_found` ou `timeout`.
- Após confirmação da entrada (`confirmation_status=confirmed`), o executor só arma `stopLoss` quando o `orderStatus` observado indica posição aberta (`PartiallyFilled` ou `Filled`) em one-way (`positionIdx=0`) via REST em `category=linear`.
- Na mesma condição de confirmação+posição pronta, o executor envia 4 TPs parciais como ordens `Limit` `reduceOnly=true`:
  - LONG: TPs enviados como `Sell`;
  - SHORT: TPs enviados como `Buy`.
- Após normalização por `qtyStep`, o executor reconcilia resíduo de quantidade de forma conservadora:
  - tenta alocar resíduo no último TP sem exceder `planned_quantity`;
  - se não for possível alocar com segurança por `qtyStep`, registra o resíduo explicitamente.
- O resultado estruturado (`ExecutionResult`) separa explicitamente:
  - confirmação da entrada;
  - status de configuração do stop loss;
  - status dos take profits (tentados, aceitos, falhos e razões por TP), incluindo resumo de reconciliação das quantidades;
  - status da limpeza pós-fechamento de posição (tentativa, quantidade encontrada/cancelada/falha e razões).
- Após confirmação e configuração de proteção, há um monitor curto da execução atual:
  - usa os IDs da entrada e dos TPs aceitos desta execução;
  - tenta confirmar fechamento via websocket privado (`position`) com `order` e `execution` como apoio complementar na execução atual;
  - considera fechamento final apenas quando `position` confirmar (fonte de verdade); eventos isolados de `order` ou `execution` não encerram posição;
  - quando `execution` chega para IDs rastreados da execução atual, enriquece telemetria de fills parciais/totais sem alterar a regra principal;
  - se websocket ficar inconclusivo para `position` (mesmo com `order` relevante), aciona fallback REST cedo e explícito (`Get Position Info`, `Get Open & Closed Orders`, `Get Order History`);
  - se detectar posição fechada dentro da janela, aciona cleanup para cancelar apenas TPs remanescentes desta execução;
  - registra tentativas, status final do monitor e ordens remanescentes no `ExecutionResult`;
  - encerra com timeout explícito quando a posição não fecha na janela curta.
- A limpeza incremental continua com foco nos IDs dos TPs da execução atual:
  - registra os `orderId` / `orderLinkId` dos TPs aceitos na execução atual;
  - quando acionada pelo monitor curto, tenta cancelar apenas os TPs registrados desta execução;
- sem loop infinito e sem monitor contínuo global nesta fase.
- O callback também persiste um journal local por execução em `runtime/journal/` (UTF-8 JSON legível), com schema estável por blocos e resumo final (`summary`) para leitura rápida.
- `tradeStatus` normaliza o estado final em valores previsíveis (ex.: `blocked`, `safe_failure`, `entry_sent`, `entry_confirmed`, `protected`, `monitoring_inconclusive`, `closed_clean`, `closed_with_failures`) para facilitar auditoria e consumo posterior sem analytics avançada.

- Interpretação de `success` no resultado final:
  - `True` apenas quando entrada está confirmada, stop loss (quando tentado) foi configurado e TPs (quando tentados) não tiveram falhas;
  - `False` quando entrada não confirma, stop loss falha ou qualquer TP falha (parcial/total).


## Resumo local de journals (CLI)

Para inspeção operacional rápida dos journals JSON já gravados (sem dashboard, sem banco e sem analytics avançada), use:

```bash
python -m src.scripts.journal_summary
```

Opções simples:

- `--path`: diretório dos journals (padrão: `runtime/journal`)
- `--last`: quantidade de registros recentes na listagem curta (padrão: `10`)

Exemplo:

```bash
python -m src.scripts.journal_summary --path runtime/journal --last 15
```

A saída mostra agregados por `tradeStatus`, totais de `success=true/false`, contadores operacionais principais (monitor inconclusivo, fechamento limpo/com falhas, blocked e safe_failure) e uma lista curta dos journals mais recentes.

## Rodar testes

```bash
pytest -q
```

## Política de integração com APIs

- Integrações com Telethon, Telegram, Bybit e pybit usam somente documentação/repositórios oficiais.
- Se houver dúvida não confirmada oficialmente, a implementação deve parar e a incerteza deve ser reportada no resumo da entrega.
- Referências oficiais do projeto: `docs/API_REFERENCES.md`.
