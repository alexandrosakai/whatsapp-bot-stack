-- Somente estrutura (sem dados). Valores de configuração: veja a tabela 'config' no README.
\connect wa
CREATE OR REPLACE FUNCTION rodape_sorteado(p_numero text) RETURNS text AS $$
DECLARE opcoes text[]; ultimo text; escolha text;
BEGIN
  SELECT string_to_array(valor, '|||') INTO opcoes FROM config WHERE chave = 'rodape_pool';
  IF opcoes IS NULL THEN RETURN NULL; END IF;
  SELECT rodape INTO ultimo FROM send_queue WHERE numero = p_numero AND rodape IS NOT NULL ORDER BY id DESC LIMIT 1;
  SELECT o INTO escolha FROM unnest(opcoes) o WHERE o IS DISTINCT FROM ultimo ORDER BY random() LIMIT 1;
  RETURN COALESCE(escolha, opcoes[1]);
END $$ LANGUAGE plpgsql;
CREATE OR REPLACE FUNCTION fila_rodape_aleatorio() RETURNS trigger AS $$
BEGIN
  IF NEW.rodape IS NULL OR NEW.rodape = (SELECT valor FROM config WHERE chave = 'rodape') THEN
    NEW.rodape := COALESCE(rodape_sorteado(NEW.numero), NEW.rodape);
  END IF;
  RETURN NEW;
END $$ LANGUAGE plpgsql;
DROP TRIGGER IF EXISTS trg_fila_rodape ON send_queue;
CREATE TRIGGER trg_fila_rodape BEFORE INSERT ON send_queue FOR EACH ROW EXECUTE FUNCTION fila_rodape_aleatorio();
