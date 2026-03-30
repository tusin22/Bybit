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
- Primeira camada de **escrita restrita à Bybit testnet** para enviar **apenas ordem de entrada** (`Market`).
- Proteções obrigatórias de execução:
  - bloqueio quando `DRY_RUN=true`;
  - bloqueio quando `ENABLE_ORDER_EXECUTION=false`;
  - bloqueio quando `ENABLE_ORDER_EXECUTION=true` e `BYBIT_TESTNET=false`;
  - bloqueio quando `ExecutionPlan` não for elegível.
- Log estruturado do resultado da tentativa de execução (`ExecutionResult`).
- Em falha de parsing, log de erro claro e continuidade do loop.
- **Sem TP/SL automáticos nesta fase**.
- **Sem reduceOnly nesta fase**.
- **Sem monitor e sem websocket nesta fase**.
- A confirmação final de execução/preenchimento da ordem ainda não está implementada nesta fase.

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

## Executar listener

```bash
python -m src.main
```

Na primeira execução, o Telethon pode solicitar autenticação da conta para criar a sessão local.
No startup, o listener valida/resolve `TELEGRAM_SOURCE_CHAT`; se o valor for inválido, o processo encerra com erro de configuração claro (sem traceback como fluxo principal).

## Comportamento em runtime

- Nova mensagem chega no chat/canal configurado.
- O texto bruto (`raw_text`) é enviado para o parser.
- Se o parsing for válido, o sinal é enriquecido com validação read-only da Bybit:
  - consulta de instrumento (`linear`);
  - consulta de preço atual (`linear`);
  - captura de metadados básicos do instrumento (`status`, `tickSize`, `qtyStep`);
  - validação da janela de entrada.
- O sinal enriquecido é convertido em `ExecutionPlan`:
  - normalização de preços por `tickSize` com regras explícitas por contexto (entrada, stop e take profit);
  - normalização de quantity por `qtyStep`;
  - cálculo de quantity por sizing fixo configurado.
- O executor avalia proteções e elegibilidade:
  - se bloqueado por proteção, registra o motivo;
  - se elegível e desbloqueado, envia ordem de entrada `Market` em `category=linear` com one-way (`positionIdx=0`).
- O resultado estruturado (`ExecutionResult`) separa:
  - tentativa de ordem (`order_attempted`);
  - submissão aceita pela API (`order_sent` / ACK inicial);
  - confirmação final (`order_confirmed`), que permanece pendente nesta fase (`confirmation_status=pending_confirmation`).

## Rodar testes

```bash
pytest -q
```

## Política de integração com APIs

- Integrações com Telethon, Telegram, Bybit e pybit usam somente documentação/repositórios oficiais.
- Se houver dúvida não confirmada oficialmente, a implementação deve parar e a incerteza deve ser reportada no resumo da entrega.
- Referências oficiais do projeto: `docs/API_REFERENCES.md`.
