# AGENTS.md

## Objetivo
Garantir implementação simples, modular e segura para fases incrementais do bot.

## Diretrizes desta base

1. Não implementar envio de ordens na Bybit antes da fase específica.
2. Não usar chaves reais no código ou fixtures.
3. Preferir legibilidade e tipagem forte.
4. Manter parser com regex por blocos, evitando regex único gigante.
5. Em erro de parsing, lançar exceções explícitas com mensagens claras.
6. Evitar funcionalidades fora do escopo solicitado.

## Política obrigatória de documentação oficial (integrações)

1. Toda integração com API ou biblioteca externa deve ser baseada em documentação oficial e/ou repositório oficial.
2. Nunca inventar métodos, parâmetros, endpoints, campos de resposta ou comportamentos não confirmados.
3. Em caso de dúvida não confirmada na documentação oficial, parar a implementação e sinalizar explicitamente no resumo da entrega.
4. Em mudanças envolvendo Telethon, Telegram, Bybit ou pybit, informar no resumo final quais referências oficiais foram consultadas.
5. Não usar blogs, fóruns ou exemplos não oficiais como fonte primária quando existir documentação oficial.

## Testes

- Toda mudança no parser deve incluir testes de sucesso e falha quando aplicável.
- Fixtures devem representar sinais realistas.

## Logging

- Logs objetivos com contexto suficiente para troubleshooting.
- Não logar dados sensíveis.
