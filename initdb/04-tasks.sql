-- Somente estrutura (sem dados). Valores de configuração: veja a tabela 'config' no README.
\connect wa
-- Tarefas: enviar um checklist a um responsável (com link personalizado) e cobrar até concluir.
CREATE TABLE IF NOT EXISTS tasks (
  id              SERIAL PRIMARY KEY,
  titulo          TEXT NOT NULL,                       -- ex.: Checklist Diário - Veículos
  checklist       TEXT NOT NULL,                       -- id no app de checklists (veiculos, motos, empilhadeira...)
  contato_id      INT NOT NULL REFERENCES contacts(id) ON DELETE CASCADE,
  nome_checklist  TEXT NOT NULL,                       -- nome do responsável como aparece no formulário (ex.: Mateus)
  dias            INT[] NOT NULL DEFAULT '{1,2,3,4,5,6}',   -- 1=segunda ... 7=domingo
  hora_envio      TIME NOT NULL DEFAULT '07:30',
  lembrar_cada_min INT NOT NULL DEFAULT 60,
  lembrar_ate     TIME NOT NULL DEFAULT '18:00',       -- depois disso não cobra mais no dia
  inicio          DATE NOT NULL DEFAULT current_date,
  fim             DATE,
  ativo           BOOLEAN NOT NULL DEFAULT TRUE,
  criado          TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE TABLE IF NOT EXISTS task_runs (
  id            SERIAL PRIMARY KEY,
  tarefa_id     INT NOT NULL REFERENCES tasks(id) ON DELETE CASCADE,
  data          DATE NOT NULL,
  token         TEXT NOT NULL UNIQUE,
  status        TEXT NOT NULL DEFAULT 'pendente' CHECK (status IN ('pendente','concluida','expirada')),
  envios        INT NOT NULL DEFAULT 0,
  ultimo_envio  TIMESTAMPTZ,
  concluida_em  TIMESTAMPTZ,
  criado        TIMESTAMPTZ NOT NULL DEFAULT now(),
  UNIQUE (tarefa_id, data)
);
-- próximo horário livre na fila, com espaçamento aleatório (mesma regra dos avisos)
CREATE OR REPLACE FUNCTION wa_proximo_slot() RETURNS timestamptz LANGUAGE sql AS $f$
  SELECT GREATEST(now(), COALESCE((SELECT max(liberar_em) FROM send_queue WHERE status='pendente'), now()))
         + make_interval(secs => g.gmin + random()*(g.gmax - g.gmin))
  FROM (SELECT (SELECT valor::int FROM config WHERE chave='gap_min_s') gmin, (SELECT valor::int FROM config WHERE chave='gap_max_s') gmax) g
$f$;
-- monta a mensagem (primeira ou lembrete) e coloca na fila
CREATE OR REPLACE FUNCTION tarefa_enfileirar(p_exec INT, p_lembrete BOOLEAN) RETURNS INT LANGUAGE plpgsql AS $f$
DECLARE r RECORD; link TEXT; msg TEXT; dias TEXT[] := ARRAY['domingo','segunda-feira','terça-feira','quarta-feira','quinta-feira','sexta-feira','sábado']; ontem BOOLEAN;
BEGIN
  SELECT e.id, e.token, e.data, e.envios, t.titulo, t.checklist, t.nome_checklist, t.id AS tid, c.numero, split_part(c.nome,' ',1) AS pnome
    INTO r FROM task_runs e JOIN tasks t ON t.id=e.tarefa_id JOIN contacts c ON c.id=t.contato_id WHERE e.id=p_exec;
  IF NOT FOUND THEN RETURN NULL; END IF;
  link := (SELECT valor FROM config WHERE chave='checklist_url') || '?t=' || r.token || '&n=' || encode(convert_to(r.nome_checklist,'UTF8'),'hex') || '#' || r.checklist;
  IF p_lembrete THEN
    msg := E'⏰ *Pendente' || CASE WHEN r.envios > 1 THEN ' (lembrete ' || r.envios || ')' ELSE '' END || E':* o *' || r.titulo || E'* de hoje ainda não foi preenchido.\nPor favor, preencha agora para concluir a tarefa:\n\n👉 ' || link;
  ELSE
    SELECT EXISTS (SELECT 1 FROM task_runs x WHERE x.tarefa_id=r.tid AND x.data=r.data-1 AND x.status='expirada') INTO ontem;
    msg := E'📋 *' || r.titulo || E'*\nOlá, ' || r.pnome || '! Hoje (' || dias[extract(dow FROM r.data)::int + 1] || ', ' || to_char(r.data,'DD/MM') || E') você precisa preencher este checklist.\n\n👉 ' || link
           || CASE WHEN ontem THEN E'\n\n⚠️ O checklist de ontem ficou pendente.' ELSE '' END;
  END IF;
  INSERT INTO send_queue(numero,texto,liberar_em) VALUES (r.numero, msg, wa_proximo_slot());
  UPDATE task_runs SET envios = envios + 1, ultimo_envio = now() WHERE id = p_exec;
  RETURN p_exec;
END $f$;
-- cria a execução de hoje (se ainda não existe) e envia o checklist
CREATE OR REPLACE FUNCTION tarefa_enviar_hoje(p_tarefa INT) RETURNS INT LANGUAGE plpgsql AS $f$
DECLARE hoje DATE := (now() AT TIME ZONE 'America/Sao_Paulo')::date; eid INT;
BEGIN
  INSERT INTO task_runs(tarefa_id,data,token) VALUES (p_tarefa, hoje, replace(gen_random_uuid()::text,'-','')) ON CONFLICT (tarefa_id,data) DO NOTHING RETURNING id INTO eid;
  IF eid IS NULL THEN RETURN NULL; END IF;       -- já existia para hoje
  RETURN tarefa_enfileirar(eid, false);
END $f$;
CREATE OR REPLACE FUNCTION tarefa_lembrar(p_exec INT) RETURNS INT LANGUAGE sql AS $f$ SELECT tarefa_enfileirar(p_exec, true) $f$;
