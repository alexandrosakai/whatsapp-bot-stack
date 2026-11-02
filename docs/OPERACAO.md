# WhatsApp: Evolution API + n8n + Claude (VPS Empresa Exemplo)

Tudo em Docker (`/opt/whatsapp-stack`), preso a 127.0.0.1. Segredos em `.env` (modo 600).

| Serviço | Para quê | Acesso local |
|---|---|---|
| evolution | ponte com o WhatsApp (instância `bot`) | http://127.0.0.1:8085 (`/manager`) |
| n8n | fluxos, formulários, Claude | http://127.0.0.1:5678 |
| whisper | transcrição de áudio (faster-whisper "small", local) | interno |
| postgres 17 | bancos `wa` (dados), `n8n`, `evolution` | interno |

## Regras implementadas
- **Não cadastrado**: o fluxo ignora tudo. Nenhuma resposta automática.
- **Cadastrado**: qualquer mensagem (texto ou áudio) abre/continua a conversa com o Claude, sem frase de ativação.
  A sessão (memória das últimas 20 mensagens) acaba sozinha após `timeout_min` = 360 min (6 h) sem mensagens; a próxima mensagem começa do zero.
  *encerrar* é opcional (encerra e apaga a memória). **Não existe mais o comando "parar"**: descadastrar é feito na página
  https://example.com/whatsapp/contatos/ (cancela os avisos da fila, encerra a conversa e bloqueia qualquer envio, inclusive o manual;
  o histórico é mantido; dá para reativar). Quem está descadastrado não recebe resposta nem aviso.
- **Respostas curtas não são respondidas** (config `ignorar_curtas`: "ok", "obrigado", "valeu", só emoji, 1 letra...). Mensagem com "?" ou com
  qualquer palavra fora da lista sempre é respondida. Edite a lista no banco sem mexer em código.
- **Toda mensagem automática** termina com o rodapé: pode responder para falar comigo; quem não quiser mais avisos pede por aqui e o admin descadastra pela página de Contatos.
- **Áudio de cadastrado**: Whisper transcreve, Claude resume em tópicos e grava `data/resumos/AAAA-MM-DD_HHMMSS_numero_nome.md`
  (resumo, transcrição e, se estiver em conversa, a resposta). Como toda mensagem de cadastrado vira conversa, o áudio também é respondido pelo assistente.
- **Envio programado**: formulário *Agendar mensagem* -> tabela `agendamentos` -> a cada minuto vira linhas em `fila_envio` com
  intervalo aleatório (45–150 s, tabela `config`) -> o Enviador (a cada 20 s) manda 1 por vez, com "digitando…" de 2,5–7 s,
  só entre 08:00 e 19:00 (Brasília), máx. 80/dia, 3 tentativas. Recorrência diária/semanal/mensal.

## Formulários (login: usuário `admin`, senha = `N8N_OWNER_PASSWORD` do .env)
- Cadastrar contato: `/form/cadastrar`    - Agendar mensagem: `/form/agendar`   (no endereço do n8n)

## Ajustes sem mexer em código (tabela `config` do banco `wa`)
`gatilho, saida, timeout_min, gap_min_s, gap_max_s, janela_ini, janela_fim, max_dia, rodape, persona, ack_audio, modelo`
Ex.: `docker compose exec postgres psql -U postgres -d wa -c "UPDATE config SET valor='60' WHERE chave='gap_min_s'"`

## Comandos
- `./status.sh` situação | `./qr.sh` gera `qr.png` para conectar o WhatsApp | `./backup.sh` (também diário 03:17, 14 dias)
- Mudou `build_workflows.py`: `python3 build_workflows.py && rm -rf workflows/_cred && docker compose exec n8n n8n import:workflow --separate --input=/import/`
  depois `n8n publish:workflow --id=<id>` de cada um e `docker compose restart n8n`.
- Chave do Claude: preencher `ANTHROPIC_API_KEY=` no `.env` e `docker compose up -d n8n`.
- Domínios/HTTPS: criar DNS A `n8n.example.com` e `evolution.example.com` -> 203.0.113.10 e rodar `./ativar-dominios.sh`.

## Página de Mensagens (https://app.example.com/whatsapp/mensagens/)
Atendimento só para **contatos cadastrados** (grupos e números não cadastrados não aparecem e são bloqueados no servidor: nenhuma rota
aceita jid avulso; mídia de outras conversas retorna 403). Lista contatos, histórico, abre imagem/vídeo/áudio/documento, mostra resumos
de áudio e envia texto/arquivo. Conversas @lid são ligadas ao telefone pelo par remoteJid/remoteJidAlt guardado pela Evolution.
- Login: tela própria em /whatsapp/entrar/ (senha = `N8N_OWNER_PASSWORD`); sessão por cookie assinado de 30 dias (nginx `auth_request` -> painel `/auth`; segredo `PAINEL_SEGREDO` no .env). Botão "Sair" na página.
- Serviço: contêiner `painel` (`painel/server.py`, 127.0.0.1:8110). Página: `painel/index.html`
  (publicar: `cp painel/index.html /var/www/portal/whatsapp/mensagens/index.html`).
- Áudio ogg/opus é convertido para MP3 ao abrir; mídia em cache no volume `painel_midia`.
- Envio manual NÃO passa pela fila/espaçamento (20/min). A Evolution grava mensagens/contatos/chats (`DATABASE_SAVE_*`=true); o painel
  nativo da Evolution (/manager) ainda mostra tudo que ela guarda, inclusive grupos.

## Endereço alternativo (por causa do antivírus)
O Kaspersky Free (anti-phishing) bloqueou as chamadas de app.example.com (subdomínio novo, com telas de login). As mesmas páginas
estão também em **https://example.com/whatsapp/** (vhost do portal, locais isolados em /etc/nginx/sites-available/exemplo;
backup /opt/app/portal-vhost.bak). Cada domínio tem a sua sessão (cookie por host). O card do painel de sistemas aponta para este.

## Privacidade: só cadastrados ficam no sistema
- `limpeza.py` (cron a cada 15 min, `/etc/cron.d/wa-stack-limpeza`, log em `logs/limpeza.log`) apaga do banco da Evolution tudo que não for de contato
  cadastrado (telefone ou @lid ligado a ele): grupos, números desconhecidos, contatos, "IsOnWhatsapp". Quem saiu da lista (PARAR, `ativo=false`)
  continua em `contatos`, então o histórico dele é MANTIDO. `python3 limpeza.py --simular` só mostra o que seria apagado.
- Travas: aborta se não houver contatos cadastrados ou se as contagens de cadastrados divergirem.
- n8n: o fluxo "WA - Recebimento" não guarda execuções com sucesso (as mensagens de não cadastrados não ficam registradas).
- Backups (`backup.sh`, 03:17) passam a conter só cadastrados. Os backups antigos com o histórico completo foram apagados em 31/10/2026.
- O histórico de grupos e de não cadastrados existe somente no celular do número conectado.

## Informações da empresa (base de conhecimento do assistente)
Página https://example.com/whatsapp/empresa/ (login): cadastro de "assuntos" (horário, telefones, endereço, pagamento, entrega...). Ficam na tabela
`conhecimento` (banco wa; limite 12.000 caracteres ativos). A cada conversa o fluxo "WA - Recebimento" inclui os assuntos ativos nas instruções do Claude
("única fonte; se não constar, diga que vai verificar com a equipe e não invente"). Sem cadastro, o assistente nunca inventa dados da empresa.
