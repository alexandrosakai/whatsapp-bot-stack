#!/bin/sh
# Gera o QR Code para conectar o WhatsApp: salva em /opt/whatsapp-stack/qr.png (expira em ~1 min; rode de novo se precisar)
cd /opt/whatsapp-stack; set -a; . ./.env; set +a
curl -s http://127.0.0.1:8085/instance/connect/bot -H "apikey: $EVOLUTION_API_KEY" | python3 -c "
import sys,json,base64
d=json.load(sys.stdin); b=d.get('base64')
if not b: print('Sem QR (já conectado?):', json.dumps(d)[:200]); sys.exit(1)
open('/opt/whatsapp-stack/qr.png','wb').write(base64.b64decode(b.split(',',1)[1])); print('QR salvo em /opt/whatsapp-stack/qr.png')"
