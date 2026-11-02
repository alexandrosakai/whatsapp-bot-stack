#!/bin/sh
# Rode DEPOIS de criar no DNS os registros A: n8n.example.com e evolution.example.com -> 203.0.113.10
set -e
export PATH=$PATH:/usr/sbin:/sbin
cd /opt/whatsapp-stack
for h in n8n evolution; do
  host $h.example.com 1.1.1.1 | grep -q "has address 203.0.113.10" || { echo "DNS de $h.example.com ainda não existe"; exit 1; }
  cp nginx/$h.example.com.conf /etc/nginx/sites-available/$h.example.com
  ln -sf /etc/nginx/sites-available/$h.example.com /etc/nginx/sites-enabled/$h.example.com
done
nginx -t && systemctl reload nginx
certbot --nginx --non-interactive --agree-tos --redirect -m user@example.com -d n8n.example.com -d evolution.example.com
sed -i '/^N8N_HOST=/d;/^N8N_PROTOCOL=/d;/^N8N_PUBLIC_URL=/d;/^N8N_SECURE_COOKIE=/d;/^EVOLUTION_PUBLIC_URL=/d' .env
printf 'N8N_HOST=n8n.example.com\nN8N_PROTOCOL=https\nN8N_PUBLIC_URL=https://n8n.example.com/\nN8N_SECURE_COOKIE=true\nEVOLUTION_PUBLIC_URL=https://evolution.example.com\n' >> .env
docker compose up -d n8n evolution
echo "Pronto: https://n8n.example.com  e  https://evolution.example.com/manager"
