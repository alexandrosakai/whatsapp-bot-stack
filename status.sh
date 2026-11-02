#!/bin/sh
cd /opt/whatsapp-stack; set -a; . ./.env; set +a
docker compose ps --format 'table {{.Service}}\t{{.Status}}'
printf 'WhatsApp: '; curl -s http://127.0.0.1:8085/instance/connectionState/bot -H "apikey: $EVOLUTION_API_KEY"; echo
docker compose exec -T postgres psql -U postgres -d wa -c "select (select count(*) from contacts where ativo) contatos, (select count(*) from send_queue where status='pendente') fila_pendente, (select count(*) from send_queue where status='enviado' and enviado_em::date=current_date) enviadas_hoje, (select count(*) from send_queue where status='erro') erros, (select count(*) from sessions where ativa) conversas_ativas, (select count(*) from audio_messages) audios"
