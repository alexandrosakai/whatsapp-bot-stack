#!/bin/sh
# Backup diário dos bancos (wa, n8n, evolution) + resumos de áudio. Mantém 14 dias.
set -e
D=/opt/whatsapp-stack; cd $D; mkdir -p backups; T=$(date +%F)
for db in wa n8n evolution; do docker compose exec -T postgres pg_dump -U postgres -Fc $db > backups/$db-$T.dump; done
tar czf backups/resumos-$T.tgz -C data resumos
find backups -type f -mtime +14 -delete
