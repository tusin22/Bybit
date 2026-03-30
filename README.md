# Bybit Trade Bot (Fundação)

Projeto em Python para processar sinais de trade recebidos via Telegram, com evolução incremental por fases.

## Escopo desta fase

- Listener real de mensagens com **Telethon**.
- Escuta de **um único chat/canal** configurado em `.env`.
- Recebimento de texto bruto e envio ao parser existente (`VectraSignalParser`).
- Integração **read-only** com Bybit API V5 via **pybit** para validar entrada tardia.
- Consulta de preço atual do símbolo do sinal.
- Consulta de informações do instrumento/símbolo.
- Marcação do sinal como elegível/não elegível conforme faixa de entrada.
- Formalização de intenção operacional no domínio:
  - `LONG` => `open_long`
  - `SHORT` => `open_short`
- Log estruturado do sinal parseado com sucesso.
- Em falha de parsing, log de erro claro e continuidade do loop.
- **Sem envio de ordens nesta fase**.
- **Sem abertura de posições nesta fase**.

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

- `BYBIT_API_KEY`
- `BYBIT_API_SECRET`
- `BYBIT_TESTNET`

4. Garanta `DRY_RUN=true`.

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
  - consulta de instrumento;
  - consulta de preço atual;
  - validação da janela de entrada.
- Se o preço atual estiver fora da faixa de entrada, o sinal é marcado como inválido para entrada tardia.
- Se o preço atual estiver na faixa, o sinal é marcado como elegível para futura execução.
- Não há envio de ordens nem abertura de posição nesta fase.

## Rodar testes

```bash
pytest -q
```

## Política de integração com APIs

- Integrações com Telethon, Telegram, Bybit e pybit usam somente documentação/repositórios oficiais.
- Se houver dúvida não confirmada oficialmente, a implementação deve parar e a incerteza deve ser reportada no resumo da entrega.
- Referências oficiais do projeto: `docs/API_REFERENCES.md`.
