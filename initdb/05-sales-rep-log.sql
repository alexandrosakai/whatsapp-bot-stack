-- Somente estrutura (sem dados). Valores de configuração: veja a tabela 'config' no README.
\connect wa
-- Log de tudo o que o envio diário agendou para os vendedores (o status real de envio fica em send_queue).
CREATE TABLE IF NOT EXISTS sales_rep_messages (
  id bigserial PRIMARY KEY,
  data date NOT NULL,
  cod text NOT NULL,
  nome text NOT NULL,
  numero text NOT NULL,
  tipo text NOT NULL,            -- metas | orcamentos | cobranca | aviso | resumo | entrega (prefixo teste- nos testes)
  fila_id bigint REFERENCES send_queue(id) ON DELETE SET NULL,
  agendado_para timestamptz NOT NULL,
  criado timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS ix_sales_rep_messages_data ON sales_rep_messages(data, cod);
CREATE OR REPLACE VIEW sales_rep_message_log AS
  SELECT e.data, e.cod, e.nome, e.tipo, e.numero, e.agendado_para, f.status, f.tentativas, f.enviado_em, f.erro
  FROM sales_rep_messages e LEFT JOIN send_queue f ON f.id = e.fila_id ORDER BY e.id;
