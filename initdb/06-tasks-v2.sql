-- Somente estrutura (sem dados). Valores de configuração: veja a tabela 'config' no README.
\connect wa
ALTER TABLE tasks ADD COLUMN IF NOT EXISTS msg_inicio TEXT, ADD COLUMN IF NOT EXISTS msg_lembrete TEXT, ADD COLUMN IF NOT EXISTS msg_parabens TEXT;
ALTER TABLE tasks ALTER COLUMN lembrar_cada_min SET DEFAULT 0;
     -- 0 = sem lembretes durante o dia (só o aviso diário na hora do envio)
ALTER TABLE tasks ALTER COLUMN hora_envio SET DEFAULT '08:00';
ALTER TABLE send_queue ADD COLUMN IF NOT EXISTS rodape TEXT;
DROP FUNCTION IF EXISTS tarefa_enfileirar(INT, BOOLEAN);
DROP FUNCTION IF EXISTS tarefa_lembrar(INT);
-- texto da tarefa: troca {nome} {titulo} {data} {link} {sequencia_txt} {atraso}
CREATE OR REPLACE FUNCTION tarefa_texto(p_modelo TEXT, p_nome TEXT, p_titulo TEXT, p_data DATE, p_link TEXT, p_seq INT) RETURNS TEXT LANGUAGE sql AS $f$
  SELECT replace(replace(replace(replace(replace(replace(p_modelo,
    '{nome}', p_nome), '{titulo}', p_titulo),
    '{data}', (ARRAY['domingo','segunda-feira','terça-feira','quarta-feira','quinta-feira','sexta-feira','sábado'])[extract(dow FROM p_data)::int + 1] || ', ' || to_char(p_data,'DD/MM')),
    '{link}', p_link),
    '{sequencia_txt}', CASE WHEN p_seq >= 2 THEN E'\n🔥 ' || p_seq || ' checklists seguidos!' ELSE '' END),
    '{atraso}', to_char(p_data,'DD/MM'))
$f$;
-- sequência de execuções concluídas seguidas (a mais recente primeiro)
CREATE OR REPLACE FUNCTION tarefa_sequencia(p_tarefa INT) RETURNS INT LANGUAGE sql AS $f$
  SELECT count(*)::int FROM (
    SELECT status, row_number() OVER (ORDER BY data DESC) rn FROM task_runs WHERE tarefa_id = p_tarefa) s
  WHERE rn < COALESCE((SELECT min(rn) FROM (SELECT status, row_number() OVER (ORDER BY data DESC) rn FROM task_runs WHERE tarefa_id = p_tarefa) x WHERE status <> 'concluida'), 100000)
$f$;
-- monta a mensagem (inicio | lembrete | parabens) e coloca na fila
CREATE OR REPLACE FUNCTION tarefa_enfileirar(p_exec INT, p_tipo TEXT) RETURNS INT LANGUAGE plpgsql AS $f$
DECLARE r RECORD; link TEXT; modelo TEXT; msg TEXT; ontem BOOLEAN; rod TEXT; quando TIMESTAMPTZ;
BEGIN
  SELECT e.id, e.token, e.data, e.envios, t.titulo, t.checklist, t.nome_checklist, t.id AS tid, t.msg_inicio, t.msg_lembrete, t.msg_parabens, c.numero, split_part(c.nome,' ',1) AS pnome
    INTO r FROM task_runs e JOIN tasks t ON t.id=e.tarefa_id JOIN contacts c ON c.id=t.contato_id WHERE e.id=p_exec;
  IF NOT FOUND THEN RETURN NULL; END IF;
  link := (SELECT valor FROM config WHERE chave='checklist_url') || '?t=' || r.token || '&n=' || encode(convert_to(r.nome_checklist,'UTF8'),'hex') || '#' || r.checklist;
  IF p_tipo = 'lembrete' THEN
    modelo := COALESCE(r.msg_lembrete, E'⏰ *Pendente:* o *{titulo}* de hoje ainda não foi preenchido, {nome}. Reserve um minutinho e vamos manter o ritmo! 💪\n\n👉 {link}');
  ELSIF p_tipo = 'parabens' THEN
    modelo := COALESCE(r.msg_parabens, E'✅ Checklist recebido, {nome}! 🎉 Obrigado por manter o ritmo.{sequencia_txt}\nContinue assim!');
  ELSE
    modelo := COALESCE(r.msg_inicio, E'📋 *{titulo}*\nOlá, {nome}! Hoje ({data}) você precisa preencher este checklist. Leva só alguns minutinhos e mantém tudo em dia. 💪\n\n👉 {link}');
  END IF;
  msg := tarefa_texto(modelo, r.pnome, r.titulo, r.data, link, CASE WHEN p_tipo='parabens' THEN tarefa_sequencia(r.tid) ELSE 0 END);
  IF p_tipo = 'inicio' THEN
    SELECT EXISTS (SELECT 1 FROM task_runs x WHERE x.tarefa_id=r.tid AND x.data < r.data AND x.status='expirada' AND x.data = (SELECT max(y.data) FROM task_runs y WHERE y.tarefa_id=r.tid AND y.data < r.data)) INTO ontem;
    IF ontem THEN msg := msg || E'\n\n⚠️ O checklist anterior ficou pendente.'; END IF;
  END IF;
  rod := (SELECT valor FROM config WHERE chave='rodape_tarefa');
  quando := CASE WHEN p_tipo = 'parabens' THEN now() ELSE wa_proximo_slot() END;     -- parabéns sai logo; o resto respeita o espaçamento
  INSERT INTO send_queue(numero,texto,liberar_em,rodape) VALUES (r.numero, msg, quando, COALESCE(rod,''));
  IF p_tipo <> 'parabens' THEN UPDATE task_runs SET envios = envios + 1, ultimo_envio = now() WHERE id = p_exec; END IF;
  RETURN p_exec;
END $f$;
CREATE OR REPLACE FUNCTION tarefa_enviar_hoje(p_tarefa INT) RETURNS INT LANGUAGE plpgsql AS $f$
DECLARE hoje DATE := (now() AT TIME ZONE 'America/Sao_Paulo')::date; eid INT;
BEGIN
  INSERT INTO task_runs(tarefa_id,data,token) VALUES (p_tarefa, hoje, replace(gen_random_uuid()::text,'-','')) ON CONFLICT (tarefa_id,data) DO NOTHING RETURNING id INTO eid;
  IF eid IS NULL THEN RETURN NULL; END IF;
  UPDATE task_runs SET status='expirada' WHERE tarefa_id=p_tarefa AND status='pendente' AND id <> eid;    -- a nova ocorrência substitui a anterior não feita
  RETURN tarefa_enfileirar(eid, 'inicio');
END $f$;
CREATE OR REPLACE FUNCTION tarefa_lembrar(p_exec INT) RETURNS INT LANGUAGE sql AS $f$ SELECT tarefa_enfileirar(p_exec, 'lembrete') $f$;
-- marca como concluída (uma vez só) e manda a mensagem de incentivo
CREATE OR REPLACE FUNCTION tarefa_concluir(p_exec INT) RETURNS INT LANGUAGE plpgsql AS $f$
DECLARE n INT;
BEGIN
  UPDATE task_runs SET status='concluida', concluida_em=now() WHERE id=p_exec AND status='pendente';
  GET DIAGNOSTICS n = ROW_COUNT;
  IF n = 0 THEN RETURN NULL; END IF;
  RETURN tarefa_enfileirar(p_exec, 'parabens');
END $f$;
-- chamada pelo app de checklists assim que alguém envia: acha a tarefa pelo link (token) ou pelo nome do responsável
CREATE OR REPLACE FUNCTION tarefa_concluir_por_envio(p_token TEXT, p_cid TEXT, p_nome TEXT) RETURNS INT LANGUAGE plpgsql AS $f$
DECLARE eid INT;
BEGIN
  SELECT e.id INTO eid FROM task_runs e WHERE e.token = p_token AND e.status='pendente';
  IF eid IS NULL THEN
    SELECT e.id INTO eid FROM task_runs e JOIN tasks t ON t.id=e.tarefa_id
     WHERE e.status='pendente' AND t.checklist=p_cid AND lower(t.nome_checklist)=lower(p_nome) ORDER BY e.data DESC LIMIT 1;
  END IF;
  IF eid IS NULL THEN RETURN NULL; END IF;
  RETURN tarefa_concluir(eid);
END $f$;
CREATE OR REPLACE FUNCTION tarefa_enfileirar(p_exec INT, p_tipo TEXT) RETURNS INT LANGUAGE plpgsql AS $f$
DECLARE r RECORD; link TEXT; modelo TEXT; msg TEXT; ontem BOOLEAN; rod TEXT; quando TIMESTAMPTZ; hoje DATE := (now() AT TIME ZONE 'America/Sao_Paulo')::date;
BEGIN
  SELECT e.id, e.token, e.data, e.envios, t.titulo, t.checklist, t.nome_checklist, t.id AS tid, t.msg_inicio, t.msg_lembrete, t.msg_parabens, c.numero, split_part(c.nome,' ',1) AS pnome
    INTO r FROM task_runs e JOIN tasks t ON t.id=e.tarefa_id JOIN contacts c ON c.id=t.contato_id WHERE e.id=p_exec;
  IF NOT FOUND THEN RETURN NULL; END IF;
  link := (SELECT valor FROM config WHERE chave='checklist_url') || '?t=' || r.token || '&n=' || encode(convert_to(r.nome_checklist,'UTF8'),'hex') || '#' || r.checklist;
  IF p_tipo = 'lembrete' THEN
    modelo := COALESCE(r.msg_lembrete, E'⏰ *Pendente:* o *{titulo}* de hoje ainda não foi preenchido, {nome}. Reserve um minutinho e vamos manter o ritmo! 💪\n\n👉 {link}');
    IF r.data < hoje THEN modelo := replace(modelo, 'de hoje', 'de {atraso}'); END IF;
  ELSIF p_tipo = 'parabens' THEN
    modelo := COALESCE(r.msg_parabens, E'✅ Checklist recebido, {nome}! 🎉 Obrigado por manter o ritmo.{sequencia_txt}\nContinue assim!');
  ELSE
    modelo := COALESCE(r.msg_inicio, E'📋 *{titulo}*\nOlá, {nome}! Hoje ({data}) você precisa preencher este checklist. Leva só alguns minutinhos e mantém tudo em dia. 💪\n\n👉 {link}');
  END IF;
  msg := tarefa_texto(modelo, r.pnome, r.titulo, r.data, link, CASE WHEN p_tipo='parabens' THEN tarefa_sequencia(r.tid) ELSE 0 END);
  IF p_tipo = 'inicio' THEN
    SELECT EXISTS (SELECT 1 FROM task_runs x WHERE x.tarefa_id=r.tid AND x.data < r.data AND x.status='expirada' AND x.data = (SELECT max(y.data) FROM task_runs y WHERE y.tarefa_id=r.tid AND y.data < r.data)) INTO ontem;
    IF ontem THEN msg := msg || E'\n\n⚠️ O checklist anterior ficou pendente.'; END IF;
  END IF;
  rod := (SELECT valor FROM config WHERE chave='rodape_tarefa');
  quando := CASE WHEN p_tipo = 'parabens' THEN now() ELSE wa_proximo_slot() END;
  INSERT INTO send_queue(numero,texto,liberar_em,rodape) VALUES (r.numero, msg, quando, COALESCE(rod,''));
  IF p_tipo <> 'parabens' THEN UPDATE task_runs SET envios = envios + 1, ultimo_envio = now() WHERE id = p_exec; END IF;
  RETURN p_exec;
END $f$;
