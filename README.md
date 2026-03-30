# Bybit Trade Bot (Fundação)

Projeto em Python para processar sinais de trade recebidos via Telegram, com evolução incremental por fases.

## Escopo desta fase

- Listener real de mensagens com **Telethon**.
- Escuta de **um único chat/canal** configurado em `.env`.
- Recebimento de texto bruto e envio ao parser existente (`VectraSignalParser`).
- Log estruturado do sinal parseado com sucesso.
- Em falha de parsing, log de erro claro e continuidade do loop.
- Execução somente em **dry-run/read-only**.
- **Sem integração com Bybit nesta fase**.

## Requisitos

- Python 3.11+
- `pytest`
- `python-dotenv`
- `telethon`

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

3. Garanta `DRY_RUN=true`.

## Executar listener em dry-run

```bash
python -m src.main
```

Na primeira execução, o Telethon pode solicitar autenticação da conta para criar a sessão local.
No startup, o listener valida/resolve `TELEGRAM_SOURCE_CHAT`; se o valor for inválido, o processo encerra com erro de configuração claro (sem traceback como fluxo principal).

## Comportamento em runtime

- Nova mensagem chega no chat/canal configurado.
- O texto bruto (`raw_text`) é enviado para o parser.
- Se o parsing for válido, o sinal estruturado é logado em JSON.
- Se o parsing falhar, a mensagem é ignorada com log explícito.
- O processo continua em execução aguardando novas mensagens.

## Rodar testes

```bash
pytest -q
```

## Política de integração com APIs

- Integrações com Telethon, Telegram, Bybit e pybit usam somente documentação/repositórios oficiais.
- Se houver dúvida não confirmada oficialmente, a implementação deve parar e a incerteza deve ser reportada no resumo da entrega.
- Referências oficiais do projeto: `docs/API_REFERENCES.md`.
