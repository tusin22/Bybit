# Bybit Trade Bot (Fundação)

Projeto inicial em Python para processar sinais de trade recebidos via Telegram e preparar uma base modular para futuras etapas.

## Escopo desta fase

- Estrutura inicial do projeto.
- Modelo tipado do sinal.
- Parser robusto para o formato de sinais definido.
- Testes com `pytest` (SHORT e LONG).
- Execução local em modo **dry-run** (sem envio de ordens).

## Requisitos

- Python 3.11+
- `pytest`
- `python-dotenv`
- `telethon` e `pybit` incluídos em dependências, mas **não utilizados nesta fase**.

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

2. Ajuste os valores conforme necessário.

> Nesta fase, as credenciais são apenas placeholders e não são usadas para execução.

## Política de integração com APIs

- Integrações com Telethon, Telegram, Bybit e pybit devem usar somente documentação/repositórios oficiais.
- Se houver dúvida não confirmada oficialmente, a implementação deve parar e a incerteza deve ser reportada no resumo da entrega.
- Referências oficiais do projeto: `docs/API_REFERENCES.md`.

## Executar dry-run

O `main.py` lê uma fixture local e imprime o JSON parseado no console.

```bash
python -m src.main
```

## Rodar testes

```bash
pytest -q
```
