# 💬 whatsapp-bot-stack

**Assistente de WhatsApp com IA, pronto para subir com Docker: Evolution API + n8n + Claude + transcrição de áudio local.**

![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)
![Docker](https://img.shields.io/badge/Docker-Compose-2496ED?logo=docker&logoColor=white)
![n8n](https://img.shields.io/badge/n8n-EA4B71?logo=n8n&logoColor=white)
![PostgreSQL](https://img.shields.io/badge/PostgreSQL-17-4169E1?logo=postgresql&logoColor=white)
![Python](https://img.shields.io/badge/Python-3.12-3776AB?logo=python&logoColor=white)
![Claude](https://img.shields.io/badge/Claude-API-D97757?logo=anthropic&logoColor=white)

Conecta um número de WhatsApp a fluxos do n8n que respondem contatos cadastrados com a API do Claude, resumem áudios e enviam mensagens programadas com regras de segurança contra excesso de envio.

## ✨ Funcionalidades

- **Conversa com IA** apenas para contatos cadastrados, com memória das últimas mensagens e fim de sessão por inatividade
- **Áudios**: transcrição local com Whisper (faster-whisper) e resumo em tópicos
- **Envio programado** (única vez, diário, semanal ou mensal) com fila, intervalo aleatório entre mensagens, janela de horário e limite diário
- **Rodapés variados** sorteados sem repetir o último usado
- **Tarefas e lembretes** recorrentes por contato, com confirmação de conclusão
- **Respostas curtas ignoradas** (configurável), para não conversar à toa
- **Painel web** para cadastrar contatos, pausar envios e testar mensagens
- **Tudo preso a `127.0.0.1`**: nada exposto à internet sem você decidir

## 🧰 Tecnologias

![Docker](https://img.shields.io/badge/Docker-2496ED?logo=docker&logoColor=white)
![n8n](https://img.shields.io/badge/n8n-EA4B71?logo=n8n&logoColor=white)
![PostgreSQL](https://img.shields.io/badge/PostgreSQL-4169E1?logo=postgresql&logoColor=white)
![Flask](https://img.shields.io/badge/Flask-000000?logo=flask&logoColor=white)
![nginx](https://img.shields.io/badge/nginx-009639?logo=nginx&logoColor=white)

Evolution API · n8n · PostgreSQL 17 · Whisper (faster-whisper) · Flask (painel) · API da Anthropic (Claude)

## 📋 Requisitos

- Docker 24+ e Docker Compose v2
- Cerca de 2 GB de RAM livres (o Whisper `small` é o maior consumidor)
- Uma chave de API da Anthropic
- Um número de WhatsApp para parear (use com responsabilidade e conforme os termos do serviço)
- Opcional: um domínio com HTTPS

## 🚀 Instalação

O projeto é todo em Docker. Não há instalação "sem Docker" suportada, pois reúne vários serviços (Postgres, Evolution API, n8n, Whisper, painel).

```bash
git clone https://github.com/alexandrosakai/whatsapp-bot-stack.git
cd whatsapp-bot-stack
cp .env.example .env && chmod 600 .env
```

Preencha o `.env`. Gere valores aleatórios longos para `POSTGRES_PASSWORD`, `EVOLUTION_API_KEY`, `N8N_ENCRYPTION_KEY`, `N8N_OWNER_PASSWORD`, `WEBHOOK_SECRET` e `PAINEL_SEGREDO`:

```bash
openssl rand -hex 32
```

Informe também `N8N_OWNER_EMAIL` e `ANTHROPIC_API_KEY`. Depois:

```bash
docker compose up -d --build
docker compose ps
```

As tabelas são criadas automaticamente pelos scripts de `initdb/` na primeira subida do Postgres.

### Pareando o WhatsApp e importando os fluxos

```bash
./qr.sh        # mostra o QR para escanear em WhatsApp > Aparelhos conectados
./status.sh    # confere conexão e filas

# importa e ativa os fluxos do n8n
docker compose exec n8n n8n import:workflow --separate --input=/import
docker compose exec n8n n8n update:workflow --all --active=true
docker compose restart n8n
```

Os fluxos são gerados por `build_workflows.py`; edite-o e rode `python3 build_workflows.py` para regenerá-los em `workflows/`.

## ⚙️ Configuração

### Variáveis de ambiente

Todas listadas em `.env.example`. **Nunca** faça commit do `.env`.

### Tabela `config` (banco `wa`)

O comportamento do assistente fica em uma tabela de pares chave/valor. Cadastre os valores que quiser; sem eles os fluxos usam o que estiver definido:

| Chave | Para que serve |
|---|---|
| `persona` | instruções que definem o papel e o tom do assistente |
| `modelo` | modelo do Claude usado nas respostas |
| `timeout_min` | minutos sem mensagens até a conversa expirar |
| `gap_min_s`, `gap_max_s` | intervalo mínimo e máximo entre mensagens automáticas (segundos) |
| `janela_ini`, `janela_fim` | horário permitido para envios automáticos |
| `max_dia` | máximo de mensagens automáticas por dia |
| `saida` | palavra que encerra a conversa |
| `ignorar_curtas` | lista de respostas curtas que não geram resposta |
| `rodape`, `rodape_pool` | rodapé padrão e conjunto de rodapés sorteados (separados por `\|\|\|`) |
| `ack_audio` | confirmar recebimento de áudio fora de conversa (`sim`/`nao`) |

Exemplo:

```bash
docker compose exec postgres psql -U postgres -d wa -c \
  "INSERT INTO config(chave, valor, descricao) VALUES
     ('persona', 'Você é um assistente virtual educado e objetivo.', 'papel do assistente'),
     ('modelo', 'claude-sonnet-5-5', 'modelo usado nas respostas'),
     ('timeout_min', '360', 'minutos para a conversa expirar'),
     ('max_dia', '80', 'limite diário de mensagens automáticas')
   ON CONFLICT (chave) DO UPDATE SET valor = EXCLUDED.valor;"
```

### HTTPS opcional

`nginx/` traz modelos (`n8n.example.com.conf`, `evolution.example.com.conf`). Troque o domínio, crie os registros DNS e adapte `ativar-dominios.sh`.

## 🧭 Como usar

1. Acesse o painel em `http://127.0.0.1:8110` e cadastre os contatos autorizados.
2. Acesse o n8n em `http://127.0.0.1:5678` para ver e ajustar os fluxos e formulários.
3. Contatos cadastrados que enviarem mensagem ou áudio abrem uma conversa com o assistente.
4. Use o formulário de agendamento para programar mensagens; elas entram na fila e saem respeitando janela e limite.

Detalhes de operação (regras, limites, tabelas): [`docs/OPERACAO.md`](docs/OPERACAO.md).

## 📁 Estrutura de pastas

```
whatsapp-bot-stack/
├── docker-compose.yml     # postgres, evolution, n8n, whisper, painel
├── .env.example
├── initdb/                # esquema do banco (somente estrutura)
├── workflows/             # fluxos do n8n (gerados por build_workflows.py)
├── build_workflows.py     # gerador dos fluxos
├── painel/                # painel web (Flask)
├── whisper/               # serviço de transcrição
├── nginx/                 # modelos de proxy reverso
├── qr.sh  status.sh  ativar-dominios.sh  limpeza.py
└── docs/OPERACAO.md
```

## 🗺️ Roadmap

- [ ] Instalador interativo para preencher o `.env`
- [ ] Testes automatizados dos fluxos
- [ ] Suporte a outros provedores de modelo
- [ ] Painel de métricas (envios, respostas, erros)
- [ ] Documentação em inglês

## 🤝 Como contribuir

1. Faça um fork e crie uma branch: `git checkout -b minha-melhoria`
2. Para mudar fluxos, edite `build_workflows.py` e regenere os JSON
3. Abra um Pull Request descrevendo como testar

## 📄 Licença

Distribuído sob a licença MIT. Veja o arquivo [LICENSE](LICENSE).

> ⚠️ Automação de mensagens pode violar os termos do WhatsApp e causar banimento do número. Envie apenas para quem consentiu, mantenha limites baixos e respeite a legislação de proteção de dados.
