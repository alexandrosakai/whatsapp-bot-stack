-- Somente estrutura (sem dados). Valores de configuração: veja a tabela 'config' no README.
\connect wa
-- Banco "wa": dados do sistema de mensagens
CREATE TABLE IF NOT EXISTS contacts (
  id            SERIAL PRIMARY KEY,
  nome          TEXT NOT NULL,
  numero        TEXT NOT NULL UNIQUE,          -- só dígitos, com DDI (ex.: 5511900000000)
  grupo         TEXT NOT NULL DEFAULT 'geral',
  ativo         BOOLEAN NOT NULL DEFAULT TRUE,
  criado        TIMESTAMPTZ NOT NULL DEFAULT now()
);
-- modo "conversa com Claude" (liga com a frase gatilho, desliga com a frase de saída ou por inatividade)
CREATE TABLE IF NOT EXISTS sessions (
  numero        TEXT PRIMARY KEY REFERENCES contacts(numero) ON DELETE CASCADE,
  ativa         BOOLEAN NOT NULL DEFAULT FALSE,
  iniciada      TIMESTAMPTZ,
  ultima_msg    TIMESTAMPTZ
);
CREATE TABLE IF NOT EXISTS history (
  id            BIGSERIAL PRIMARY KEY,
  numero        TEXT NOT NULL,
  papel         TEXT NOT NULL CHECK (papel IN ('user','assistant')),
  conteudo      TEXT NOT NULL,
  criado        TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS ix_hist_num ON history(numero, id DESC);
CREATE TABLE IF NOT EXISTS schedules (
  id            SERIAL PRIMARY KEY,
  titulo        TEXT NOT NULL,
  texto         TEXT NOT NULL,
  destino       TEXT NOT NULL DEFAULT 'todos', -- 'todos' | nome do grupo | números separados por vírgula
  quando        TIMESTAMPTZ NOT NULL,
  recorrencia   TEXT CHECK (recorrencia IN ('diaria','semanal','mensal')),
  status        TEXT NOT NULL DEFAULT 'pendente' CHECK (status IN ('pendente','enfileirado','cancelado')),
  criado        TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS ix_agend ON schedules(status, quando);
-- uma linha por mensagem a enviar; liberar_em já traz o espaçamento aleatório entre mensagens
CREATE TABLE IF NOT EXISTS send_queue (
  id            BIGSERIAL PRIMARY KEY,
  agendamento_id INT REFERENCES schedules(id) ON DELETE SET NULL,
  numero        TEXT NOT NULL,
  texto         TEXT NOT NULL,
  status        TEXT NOT NULL DEFAULT 'pendente' CHECK (status IN ('pendente','enviado','erro')),
  tentativas    INT NOT NULL DEFAULT 0,
  liberar_em    TIMESTAMPTZ NOT NULL,
  enviado_em    TIMESTAMPTZ,
  erro          TEXT
);
CREATE INDEX IF NOT EXISTS ix_fila ON send_queue(status, liberar_em);
CREATE TABLE IF NOT EXISTS audio_messages (
  id            BIGSERIAL PRIMARY KEY,
  numero        TEXT NOT NULL,
  criado        TIMESTAMPTZ NOT NULL DEFAULT now(),
  duracao_s     NUMERIC,
  transcricao   TEXT,
  resumo        TEXT,
  arquivo       TEXT
);
CREATE TABLE IF NOT EXISTS config (
  chave TEXT PRIMARY KEY, valor TEXT NOT NULL, descricao TEXT
);
-- informações da empresa que o assistente usa para responder (cadastradas pela página /whatsapp/empresa/)
CREATE TABLE IF NOT EXISTS knowledge (
  id SERIAL PRIMARY KEY, titulo TEXT NOT NULL, texto TEXT NOT NULL, ativo BOOLEAN NOT NULL DEFAULT TRUE,
  ordem INT NOT NULL DEFAULT 0, atualizado TIMESTAMPTZ NOT NULL DEFAULT now()
);
