# Assistente do Residencial Aurora

API conversacional em Python 3.12+ com FastAPI e Google ADK 2.2.0. O agente principal encaminha solicitações a especialistas em reservas, visitantes e regulamento. Regras de identidade, confirmação e exclusividade são aplicadas pelo serviço e pelo SQLite, independentemente do texto do morador.

## Arquitetura

- `src/aurora/api.py` implementa as rotas HTTP e mantém a sessão ligada ao apartamento informado na criação.
- `src/aurora/agents.py` cria o `Runner` ADK e o agente principal com três especialistas. `src/aurora/tools.py` fornece as ferramentas compartilhadas pelos especialistas e pelo adaptador fake.
- `src/aurora/service.py` contém as operações de negócio e o controle de confirmações.
- `src/aurora/repository.py` persiste sessões da API, eventos, confirmações, reservas e visitantes em SQLite. O `DatabaseSessionService` do ADK usa o mesmo arquivo para persistir estado e eventos do Runner.
- `dados/` é a fonte inicial e permanece sem alterações. O regulamento só é lido pela ferramenta de busca quando há uma pergunta.

O agente principal delega por assunto para manter a conversa e as ferramentas focadas. O especialista de reservas consulta disponibilidade e trata reservas/cancelamentos; o de visitantes consulta e pede autorizações; o especialista do regulamento procura trechos relevantes sob demanda. Para ações críticas, `api.py` verifica se o Runner realmente chamou a tool correspondente; se o modelo apenas responder baseado no histórico, a API executa a mesma tool confiável como fallback, sem aceitar a conversa como confirmação. No desenvolvimento sem credenciais, `AURORA_FAKE_MODEL=1` habilita um adaptador determinístico para smoke tests da API. Em execução normal, as mensagens são processadas pelo Runner ADK com Gemini.

## Garantias

- **Cobrança e acesso exigem confirmação do endpoint.** `AuroraService.request_reservation` e `request_visitor` criam pendências; `confirm` só executa uma confirmação ainda pendente e rejeita repetição ou ID desconhecido ([`service.py:54-71,89-134`](src/aurora/service.py)). A rota chama a ferramenta confiável `confirm_action`, traduz conflito a HTTP 409 e lista pendências ([`tools.py:40-48`](src/aurora/tools.py), [`api.py:102-150`](src/aurora/api.py)). A quadra, cuja taxa é zero, é gravada diretamente; cancelamentos também não criam pendência ([`service.py:54-87`](src/aurora/service.py)).
- **O apartamento vem da sessão.** A API grava o apartamento no registro e no estado do ADK ao criar a sessão ([`api.py:106-116`](src/aurora/api.py)). As ferramentas recebem apenas o ID da sessão e resolvem a unidade persistida antes de ler ou cancelar dados ([`tools.py:19-42`](src/aurora/tools.py), [`service.py:25-35,73-87`](src/aurora/service.py)). A consulta de disponibilidade retorna apenas `occupied` ([`service.py:46-52`](src/aurora/service.py)).
- **Reinícios preservam estado e eventos.** O repositório cria e consulta tabelas duráveis para sessões/eventos ([`repository.py:25-55,94-111`](src/aurora/repository.py)); o `DatabaseSessionService` do ADK usa o mesmo arquivo SQLite ([`agents.py:19-22`](src/aurora/agents.py), [`api.py:19-25`](src/aurora/api.py)). Use o mesmo `AURORA_DATABASE` após reiniciar. O comando de restauração apaga esse arquivo e repõe os dados seed ([`restore.py:10-20`](src/aurora/restore.py)).
- **Regulamento sob demanda.** O agente principal não contém o regulamento em suas instruções ([`agents.py:83-95`](src/aurora/agents.py)); o especialista chama a ferramenta de busca ([`agents.py:76-81`](src/aurora/agents.py), [`tools.py:56-57`](src/aurora/tools.py)). Ela lê `dados/regulamento.md` durante a consulta e retorna apenas o artigo mais compatível com os termos da pergunta ([`service.py:145-161`](src/aurora/service.py)).
- **Reserva única inclusive sob concorrência.** O índice SQLite parcial `one_active_reservation` impõe unicidade de `(area, date)` para registros ativos ([`repository.py:37-42`](src/aurora/repository.py)). A gravação usa transação imediata; colisões viram rejeição normal `occupied` ([`service.py:100-134,163-186`](src/aurora/service.py)). Os códigos têm tabela de unicidade própria e reservas canceladas preservam seu código ([`repository.py:37-42,51-53`](src/aurora/repository.py), [`service.py:171-176`](src/aurora/service.py)).

## Como rodar

Requer Python 3.12+ e [uv](https://docs.astral.sh/uv/).

```bash
cp .env.example .env
# Preencha GOOGLE_API_KEY com uma chave do Google AI Studio.
uv sync
uv run python -m aurora.restore
uv run uvicorn aurora.api:app --app-dir src --host 127.0.0.1 --port 8000
```

O projeto fixa `google-adk[db]==2.2.0` em `pyproject.toml` e `uv.lock`. O modelo padrão é configurado por `GEMINI_MODEL` (padrão `gemini-2.5-flash`); `AURORA_DATABASE` escolhe o arquivo SQLite. A API sobe em `http://localhost:8000`. Para desenvolvimento sem chamada externa, defina `AURORA_FAKE_MODEL=1` no `.env` antes de iniciar.

`uv run python -m aurora.restore` reinicializa reservas e visitantes com os arquivos `dados/` e também remove sessões/eventos anteriores. Não rode esse comando ao reiniciar a API se quiser manter o estado.

## API

- `POST /sessoes` — `{"apartamento":"101"}`; retorna 201 e `session_id`.
- `POST /sessoes/{session_id}/mensagens` — `{"texto":"..."}`; retorna `resposta` e `confirmacoes_pendentes`.
- `POST /sessoes/{session_id}/confirmacoes` — `{"id":"...","confirmado":true}`; responde 409 se a confirmação não estiver pendente naquela sessão.
- `GET /sessoes/{session_id}/eventos` — eventos em ordem; sessão inexistente retorna 404.
- `GET /apartamentos/{apartamento}/reservas` e `GET /apartamentos/{apartamento}/visitantes` — leitura direta para conferência.

## Testes e verificações locais

```bash
uv sync --extra dev
uv run pytest
uv run ruff check src tests
uv run python -m compileall -q src tests
```

O teste de API usa o caminho `AURORA_FAKE_MODEL=1`, e o teste de domínio dispara duas aprovações concorrentes para a mesma área/data.
