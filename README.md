# Bybit Trade Bot (Fundação)

Projeto em Python para processar sinais de trade recebidos via Telegram, com evolução incremental por fases.

## Escopo desta fase

- Listener real de mensagens com **Telethon**.
- Escuta de **um único chat/canal** configurado em `.env`.
- Recebimento de texto bruto e envio ao parser existente (`VectraSignalParser`).
- Integração **read-only** com Bybit API V5 via **pybit** para validar entrada tardia.
- Consulta de preço atual do símbolo do sinal (escopo atual: `category=linear`).
- Consulta de informações do instrumento/símbolo (escopo atual: `category=linear`).
- Camada nova de **planejamento de execução** (`ExecutionPlan`) sem interação transacional com corretora.
- Validação de elegibilidade final do plano com base em:
  - status do instrumento;
  - presença de `tickSize` e `qtyStep`;
  - janela de entrada do sinal;
  - quantity positiva após normalização.
- Sizing configurável por `.env` (modo padrão: `fixed_notional_usdt`).
- Log estruturado do plano gerado com sucesso.
- Em falha de parsing, log de erro claro e continuidade do loop.
- **Sem envio de ordens nesta fase**.
- **Sem abertura de posições nesta fase**.
- **Sem websocket e sem monitor nesta fase**.

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

3. Configure os campos mínimos da Bybit para integração read-only:

- `BYBIT_TESTNET` (obrigatório para escolher ambiente)
- `BYBIT_API_KEY` (**opcional** nesta fase)
- `BYBIT_API_SECRET` (**opcional** nesta fase)

> Nesta fase usamos apenas endpoints públicos de market (`get_tickers` e `get_instruments_info`), então autenticação é opcional.

4. Configure sizing para planejamento:

- `EXECUTION_SIZING_MODE=fixed_notional_usdt` (padrão e recomendado nesta fase).
- `EXECUTION_FIXED_NOTIONAL_USDT` (ex.: `25`), usado com `fixed_notional_usdt`.
- `EXECUTION_FIXED_QTY` (ex.: `0.01`), usado apenas com `fixed_qty`.

5. Garanta `DRY_RUN=true`.

## Executar listener em dry-run

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
- Se qualquer validação crítica falhar, o plano é marcado como inelegível com motivo explícito.
- Não há envio de ordens nem abertura de posição nesta fase (inclusive na preparação para a próxima fase de escrita na testnet).

## Rodar testes

```bash
pytest -q
```

## Política de integração com APIs

- Integrações com Telethon, Telegram, Bybit e pybit usam somente documentação/repositórios oficiais.
- Se houver dúvida não confirmada oficialmente, a implementação deve parar e a incerteza deve ser reportada no resumo da entrega.
- Referências oficiais do projeto: `docs/API_REFERENCES.md`.
