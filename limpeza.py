#!/usr/bin/env python3
"""Mantém no banco da Evolution SÓ o histórico dos contatos cadastrados (ativos ou que saíram da lista).
Grupos e números não cadastrados são apagados do servidor (continuam no celular).

Uso:  limpeza.py --simular   (só mostra o que seria apagado)
      limpeza.py             (apaga)
Travas: não roda se não houver contatos cadastrados; nunca apaga conversa de cadastrado (telefone ou @lid ligado a ele)."""
import re, subprocess, sys, time

SIMULAR = "--simular" in sys.argv
DIR = "/opt/whatsapp-stack"


def psql(db, sql, vars_=()):
    cmd = ["docker", "compose", "exec", "-T", "postgres", "psql", "-U", "postgres", "-d", db, "-v", "ON_ERROR_STOP=1", "-X", "-q"]
    r = subprocess.run(cmd, input=sql, capture_output=True, text=True, cwd=DIR)
    if r.returncode:
        sys.exit("ERRO no psql: " + r.stderr.strip()[:400])
    return r.stdout


# 1) cadastrados (ativos E inativos): nunca apagar o histórico de quem saiu da lista
nums = [x.strip() for x in psql("wa", "COPY (SELECT numero FROM contacts) TO STDOUT;").splitlines() if x.strip()]
nums = [n for n in nums if re.fullmatch(r"55\d{10,11}", n)]
if not nums:
    sys.exit("ABORTADO: nenhum contato cadastrado encontrado (nada foi apagado).")
reg = ",".join(f"('{n[2:4]}','{n[-8:]}')" for n in nums)

SQL = f"""
BEGIN;
SET LOCAL lock_timeout = '10s';
CREATE TEMP TABLE reg(ddd text, l8 text);
INSERT INTO reg VALUES {reg};
CREATE TEMP TABLE ok(jid text PRIMARY KEY);
-- telefones cadastrados (casa com/sem o 9º dígito)
INSERT INTO ok
SELECT DISTINCT j FROM (
  SELECT key->>'remoteJid' j FROM "Message"
  UNION SELECT "remoteJid" FROM "Chat" UNION SELECT "remoteJid" FROM "Contact" UNION SELECT "remoteJid" FROM "IntegrationSession"
) x WHERE j LIKE '%@s.whatsapp.net'
  AND EXISTS (SELECT 1 FROM reg WHERE ddd = substr(split_part(j,'@',1),3,2) AND l8 = right(split_part(j,'@',1),8))
ON CONFLICT DO NOTHING;
-- identificadores @lid ligados a um telefone cadastrado (par remoteJid/remoteJidAlt guardado pela Evolution)
INSERT INTO ok
SELECT DISTINCT lid FROM (
  SELECT CASE WHEN key->>'remoteJid' LIKE '%@lid' THEN key->>'remoteJid' ELSE key->>'remoteJidAlt' END lid,
         CASE WHEN key->>'remoteJid' LIKE '%@lid' THEN key->>'remoteJidAlt' ELSE key->>'remoteJid' END tel
  FROM "Message" WHERE key->>'remoteJidAlt' IS NOT NULL
) m WHERE lid LIKE '%@lid' AND tel LIKE '%@s.whatsapp.net'
  AND EXISTS (SELECT 1 FROM reg WHERE ddd = substr(split_part(tel,'@',1),3,2) AND l8 = right(split_part(tel,'@',1),8))
ON CONFLICT DO NOTHING;
CREATE TEMP TABLE antes AS SELECT
  (SELECT count(*) FROM "Message") msg, (SELECT count(*) FROM "Message" WHERE key->>'remoteJid' IN (SELECT jid FROM ok)) msg_ok,
  (SELECT count(*) FROM "Chat") chat, (SELECT count(*) FROM "Contact") contato, (SELECT count(*) FROM "IsOnWhatsapp") isonwa;
\\echo === a manter: conversas de cadastrados ===
SELECT (SELECT count(*) FROM ok) jids_cadastrados, msg_ok mensagens_mantidas FROM antes;
\\echo === a apagar ===
SELECT msg - msg_ok mensagens, chat - (SELECT count(*) FROM "Chat" WHERE "remoteJid" IN (SELECT jid FROM ok)) conversas,
       contato - (SELECT count(*) FROM "Contact" WHERE "remoteJid" IN (SELECT jid FROM ok)) contatos FROM antes;
"""
if not SIMULAR:
    SQL += """
-- trava de segurança: precisa existir ao menos uma conversa de cadastrado, ou não apaga nada
DO $$ BEGIN IF (SELECT count(*) FROM ok) = 0 THEN RAISE EXCEPTION 'nenhuma conversa de cadastrado encontrada: abortado'; END IF; END $$;
DELETE FROM "Message" WHERE COALESCE(key->>'remoteJid','') NOT IN (SELECT jid FROM ok);
DELETE FROM "MessageUpdate" WHERE COALESCE("remoteJid",'') NOT IN (SELECT jid FROM ok);
DELETE FROM "Chat" WHERE "remoteJid" NOT IN (SELECT jid FROM ok);
DELETE FROM "Contact" WHERE "remoteJid" NOT IN (SELECT jid FROM ok);
DELETE FROM "IntegrationSession" WHERE "remoteJid" NOT IN (SELECT jid FROM ok);
DELETE FROM "IsOnWhatsapp" x WHERE NOT EXISTS (SELECT 1 FROM reg WHERE ddd = substr(regexp_replace(split_part(x."remoteJid",'@',1),'\\D','','g'),3,2) AND l8 = right(regexp_replace(split_part(x."remoteJid",'@',1),'\\D','','g'),8));
\\echo === depois ===
SELECT (SELECT count(*) FROM "Message") mensagens, (SELECT count(*) FROM "Message" WHERE key->>'remoteJid' IN (SELECT jid FROM ok)) mensagens_cadastrados,
       (SELECT msg_ok FROM antes) esperado_cadastrados, (SELECT count(*) FROM "Chat") conversas, (SELECT count(*) FROM "Contact") contatos;
"""
    SQL += "COMMIT;\n"
else:
    SQL += "ROLLBACK;\n"

out = psql("evolution", SQL)
print(out.strip())

if not SIMULAR:
    # confere que nada de cadastrado foi perdido (as duas contagens têm de ser iguais)
    m = re.search(r"\n\s*(\d+)\s*\|\s*(\d+)\s*\|\s*(\d+)\s*\|", out.split("=== depois ===")[1])
    if not m or m.group(2) != m.group(3):
        sys.exit("ALERTA: contagem de cadastrados difere do esperado!")
    psql("evolution", 'VACUUM ANALYZE "Message"; VACUUM ANALYZE "Chat"; VACUUM ANALYZE "Contact"; VACUUM ANALYZE "IsOnWhatsapp";')
    # cache de mídia do painel: o que tem mais de 7 dias sai (é regenerável)
    subprocess.run(["docker", "compose", "exec", "-T", "painel", "sh", "-c", "find /data/midia -type f -mtime +7 -delete"], cwd=DIR, capture_output=True)
    ap = re.search(r"=== a apagar ===\s*\n.*\n[-+]+\n\s*(\d+)\s*\|\s*(\d+)\s*\|\s*(\d+)", out)
    print("OK", time.strftime("%Y-%m-%d %H:%M:%S"), "apagou: mensagens=%s conversas=%s contatos=%s" % (ap.groups() if ap else ("?", "?", "?")))
