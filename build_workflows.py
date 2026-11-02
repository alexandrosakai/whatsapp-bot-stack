#!/usr/bin/env python3
"""Gera os workflows do n8n (JSON) em ./workflows. Uso: python3 build_workflows.py"""
import json, os, uuid

OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "workflows")
os.makedirs(OUT, exist_ok=True)
PG = {"postgres": {"id": "cred-wa-db", "name": "wa-db"}}
FORM_AUTH = {"httpBasicAuth": {"id": "cred-form-auth", "name": "form-auth"}}


def node(name, typ, ver, params, pos, **kw):
    n = {"id": str(uuid.uuid4()), "name": name, "type": f"n8n-nodes-base.{typ}", "typeVersion": ver,
         "position": list(pos), "parameters": params}
    n.update(kw)
    return n


def pg(name, query, repl, pos, **kw):
    opts = {"queryReplacement": repl} if repl else {}
    return node(name, "postgres", 2.8, {"operation": "executeQuery", "query": query, "options": opts},
                pos, credentials=PG, **kw)


def code(name, js, pos, **kw):
    return node(name, "code", 2, {"jsCode": js}, pos, **kw)


def http(name, url, body_expr, pos, headers, timeout=60000, **kw):
    return node(name, "httpRequest", 4.5, {
        "method": "POST", "url": url, "sendHeaders": True,
        "headerParameters": {"parameters": [{"name": k, "value": v} for k, v in headers.items()]},
        "sendBody": True, "specifyBody": "json", "jsonBody": body_expr,
        "options": {"timeout": timeout}}, pos, **kw)


def wf(wid, name, nodes, conns, extra_settings=None):
    c = {}
    for src, outs in conns.items():           # outs: lista (por saída) de listas de destinos
        c[src] = {"main": [[{"node": d, "type": "main", "index": 0} for d in o] for o in outs]}
    data = {"id": wid, "name": name, "nodes": nodes, "connections": c, "active": False,
            "settings": {"executionOrder": "v1", "timezone": "America/Sao_Paulo", **(extra_settings or {})}}
    json.dump(data, open(os.path.join(OUT, f"{wid}.json"), "w", encoding="utf8"), ensure_ascii=False, indent=1)


EVO_HDR = {"apikey": "={{ $env.EVOLUTION_API_KEY }}"}
EVO_URL = "={{ $env.EVOLUTION_URL }}/message/sendText/{{ $env.EVOLUTION_INSTANCE }}"
CLAUDE_HDR = {"x-api-key": "={{ $env.ANTHROPIC_API_KEY }}", "anthropic-version": "2023-06-01"}
CLAUDE_URL = "={{ $env.ANTHROPIC_URL }}/v1/messages"
BODY = "={{ JSON.stringify($json.body) }}"

# ------------------------------------------------------------------ 1. RECEBIMENTO
NORMALIZA = r"""
const it = $input.first().json;
const h = it.headers || {};
if ((h['x-secret'] || '') !== $env.WEBHOOK_SECRET) return [];
const b = it.body || {};
if (String(b.event || '').toLowerCase().replace('_', '.') !== 'messages.upsert') return [];
const d = b.data || {}, k = d.key || {};
if (k.fromMe) return [];
const jid = k.remoteJid || '';
if (!jid || jid.endsWith('@g.us') || jid.startsWith('status@') || jid.endsWith('@broadcast')) return [];
const base = jid.endsWith('@lid') ? (k.senderPn || k.remoteJidAlt || '') : jid;
const numero = String(base).split('@')[0].replace(/\D/g, '');
if (!numero) return [];
const m = d.message || {};
const texto = (m.conversation || (m.extendedTextMessage && m.extendedTextMessage.text) || '').trim();
const a = m.audioMessage;
if (!texto && !a) return [];
return [{json: {numero, idmsg: k.id || '', pushName: d.pushName || '', texto, audio: !!a,
  b64: m.base64 || d.base64 || '', mimetype: a ? (a.mimetype || 'audio/ogg') : '', segundos: a ? (a.seconds || 0) : 0}}];
"""

CONTATO_SQL = """SELECT c.numero AS numero_contato, c.nome, c.grupo,
  COALESCE(s.ativa AND s.ultima_msg > now() - make_interval(mins => (SELECT valor::int FROM config WHERE chave='timeout_min')), false) AS em_sessao,
  (SELECT json_object_agg(chave, valor) FROM config) AS cfg
FROM contacts c LEFT JOIN sessions s ON s.numero = c.numero
WHERE c.ativo AND substr(c.numero,3,2) = substr($1,3,2) AND right(c.numero,8) = right($1,8)
LIMIT 1"""

ROTEIA = r"""
const c = $input.first().json, n = $('Normaliza').first().json, cfg = c.cfg || {};
// grupos sem resposta automática (config 'sem_resposta_grupos', separados por vírgula): quem responde é uma pessoa
const mudos = String(cfg.sem_resposta_grupos || '').toLowerCase().split(',').map(x => x.trim()).filter(Boolean);
if (mudos.includes(String(c.grupo || '').toLowerCase())) return [];
const norm = s => (s || '').toLowerCase().normalize('NFD').replace(/[\u0300-\u036f]/g, '').replace(/[^a-z0-9 ?]/g, ' ').replace(/\s+/g, ' ').trim();
let acao;
if (n.audio) acao = 'audio';
else {
  const t = norm(n.texto);
  if (t === norm(cfg.saida || 'encerrar')) acao = 'encerrar';   // opcional: a sessão também acaba sozinha após o tempo configurado
  else {
    // resposta curta ("ok", "obrigado", só emoji...) não gera resposta, para não ficar chato
    const ack = new Set(String(cfg.ignorar_curtas || '').split(',').map(x => norm(x).replace(/\?/g, '')).filter(Boolean));
    const letras = t.replace(/[^a-z0-9]/g, '');
    const palavras = t.replace(/\?/g, ' ').split(' ').filter(Boolean);
    const curta = letras.length <= 1 || (!t.includes('?') && palavras.length > 0 && palavras.every(w => ack.has(w)));
    if (curta) return [];
    acao = 'conversar';
  }
}
return [{json: {acao, numero: n.numero, numero_contato: c.numero_contato, nome: c.nome, em_sessao: true, cfg,
  texto: n.texto, idmsg: n.idmsg, audio: n.audio, b64: n.b64, mimetype: n.mimetype, segundos: n.segundos}}];
"""

SWITCH_RULES = {"values": [
    {"conditions": {"options": {"caseSensitive": True, "leftValue": "", "typeValidation": "strict", "version": 2},
                    "conditions": [{"id": str(uuid.uuid4()), "leftValue": "={{ $json.acao }}", "rightValue": a,
                                    "operator": {"type": "string", "operation": "equals"}}], "combinator": "and"},
     "renameOutput": True, "outputKey": a}
    for a in ("encerrar", "conversar", "audio")]}


def msg(texto_js, delay=2500):
    return f"""const r = $('Roteia').first().json;
const texto = {texto_js};
return [{{json: {{body: {{number: r.numero, text: texto, delay: {delay}}}}}}}];"""


def build_recebimento():
    N = []
    P = lambda x, y: (x * 260, y * 150)
    N.append(node("Webhook", "webhook", 2.2, {"httpMethod": "POST", "path": "wa-in", "responseMode": "onReceived", "options": {}},
                  P(0, 3), webhookId=str(uuid.uuid4())))
    N.append(code("Normaliza", NORMALIZA, P(1, 3)))
    N.append(pg("Contato", CONTATO_SQL, "={{ [$json.numero] }}", P(2, 3)))
    N.append(code("Roteia", ROTEIA, P(3, 3)))
    N.append(node("Switch", "switch", 3.2, {"rules": SWITCH_RULES, "options": {}}, P(4, 3)))
    # encerrar
    N.append(pg("Encerra sessão", "WITH d AS (DELETE FROM history WHERE numero=$1) INSERT INTO sessions(numero,ativa,iniciada,ultima_msg) VALUES($1,false,now(),now()) ON CONFLICT (numero) DO UPDATE SET ativa=false RETURNING numero",
                "={{ [$json.numero_contato] }}", P(5, 2)))
    N.append(code("Msg encerrar", msg("`Conversa encerrada. Quando quiser, é só mandar uma nova mensagem. 😊`"), P(6, 2)))
    # conversar
    N.append(pg("Histórico",
                "SELECT $2::text AS texto, $3::text AS nome, COALESCE((SELECT json_agg(json_build_object('role',papel,'content',conteudo) ORDER BY id) FROM (SELECT id,papel,conteudo FROM history WHERE numero=$1 AND criado > now() - make_interval(mins => (SELECT valor::int FROM config WHERE chave='timeout_min')) ORDER BY id DESC LIMIT 20) t),'[]'::json) AS hist, (SELECT valor FROM config WHERE chave='persona') AS persona, (SELECT valor FROM config WHERE chave='modelo') AS modelo, (SELECT string_agg('### ' || titulo || E'\\n' || texto, E'\\n\\n' ORDER BY ordem, id) FROM knowledge WHERE ativo) AS conhecimento",
                "={{ [$json.numero_contato, $json.texto, $json.nome] }}", P(5, 4)))
    N.append(code("Pedido Claude", r"""
const j = $input.first().json;
let system = (j.persona || '').replace('{nome}', (j.nome || '').split(' ')[0]);
const info = (j.conhecimento || '').trim();
system += info
  ? '\n\nINFORMAÇÕES OFICIAIS DA EMPRESA (única fonte para dados da Empresa Exemplo: horários, telefones, endereço, preços, prazos, políticas). Use somente o que está escrito abaixo; se a pergunta pedir algo que não consta aqui, diga com simpatia que vai verificar com a equipe e NÃO invente:\n\n' + info
  : '\n\nVocê ainda não recebeu informações oficiais da empresa. Para qualquer dado da Empresa Exemplo (horário, telefone, endereço, preços, prazos), diga que vai verificar com a equipe e não invente.';
const messages = (j.hist || []).concat([{role: 'user', content: j.texto}]);
return [{json: {body: {model: j.modelo || 'claude-sonnet-5-5', max_tokens: 600, system, messages}}}];
""", P(6, 4)))
    N.append(http("Claude conversa", CLAUDE_URL, BODY, P(7, 4), CLAUDE_HDR, timeout=90000, onError="continueErrorOutput"))
    N.append(code("Extrai resposta", r"""
const j = $input.first().json, r = $('Roteia').first().json;
const txt = ((j.content || []).filter(b => b.type === 'text').map(b => b.text).join('\n')).trim();
const user = $('Histórico').first().json.texto;
return [{json: {texto: txt || 'Desculpe, não consegui responder agora.', usuario: user}}];
""", P(8, 4)))
    N.append(pg("Salva histórico",
                "WITH a AS (INSERT INTO history(numero,papel,conteudo) VALUES ($1,'user',$2),($1,'assistant',$3)), b AS (INSERT INTO sessions(numero,ativa,iniciada,ultima_msg) VALUES ($1,true,now(),now()) ON CONFLICT (numero) DO UPDATE SET iniciada = CASE WHEN sessoes.ativa AND sessoes.ultima_msg > now() - make_interval(mins => (SELECT valor::int FROM config WHERE chave='timeout_min')) THEN sessoes.iniciada ELSE now() END, ativa=true, ultima_msg=now()) SELECT 1 AS ok",
                "={{ [$('Roteia').first().json.numero_contato, $json.usuario, $json.texto] }}", P(9, 4)))
    N.append(code("Msg resposta", msg("$('Extrai resposta').first().json.texto", 4000), P(10, 4)))
    N.append(code("Msg erro Claude", msg("'Desculpe, não consegui responder agora. Tente de novo em instantes. 🙏'"), P(8, 5)))
    # áudio
    N.append(code("Prepara áudio", r"""
const j = $input.first().json;
if (!j.b64) return [];
const bin = await this.helpers.prepareBinaryData(Buffer.from(j.b64, 'base64'), 'audio.ogg', j.mimetype || 'audio/ogg');
return [{json: {}, binary: {data: bin}}];
""", P(5, 7)))
    N.append(node("Whisper", "httpRequest", 4.5, {
        "method": "POST", "url": "={{ $env.WHISPER_URL }}/transcribe", "sendBody": True,
        "contentType": "multipart-form-data",
        "bodyParameters": {"parameters": [{"parameterType": "formBinaryData", "name": "file", "inputDataFieldName": "data"}]},
        "options": {"timeout": 300000}}, P(6, 7), onError="continueErrorOutput"))
    N.append(code("Pedido resumo", r"""
const w = $input.first().json, r = $('Roteia').first().json;
const texto = (w.text || '').trim();
if (!texto) return [];
const system = 'Você resume áudios de WhatsApp já transcritos. Responda em português do Brasil, em Markdown, usando exatamente estas seções: "## Resumo" (1 a 2 frases), "## Pontos principais" (3 a 7 tópicos com "-"), "## Pedidos e pendências" (tópicos; escreva "- Nenhum" se não houver) e "## Tom e urgência" (uma linha). Seja fiel ao que foi dito e não invente nada.';
return [{json: {transcricao: texto, duracao: w.duration || r.segundos || 0,
  body: {model: r.cfg.modelo || 'claude-sonnet-5-5', max_tokens: 800, system, messages: [{role: 'user', content: 'Transcrição do áudio de ' + r.nome + ':\n\n' + texto}]}}}];
""", P(7, 7)))
    N.append(http("Claude resumo", CLAUDE_URL, BODY, P(8, 7), CLAUDE_HDR, timeout=90000, onError="continueErrorOutput"))
    N.append(code("Resumo", r"""
const j = $input.first().json, p = $('Pedido resumo').first().json;
const resumo = ((j.content || []).filter(b => b.type === 'text').map(b => b.text).join('\n')).trim();
return [{json: {transcricao: p.transcricao, duracao: p.duracao, resumo: resumo || '## Resumo\n- (não foi possível gerar o resumo)'}}];
""", P(9, 7)))
    N.append(code("Resumo sem Claude", r"""
const p = $('Pedido resumo').first().json;
return [{json: {transcricao: p.transcricao, duracao: p.duracao, resumo: '## Resumo\n- (resumo indisponível: falha ao falar com o Claude)'}}];
""", P(9, 8)))
    N.append(node("Em conversa?", "if", 2.2, {"conditions": {
        "options": {"caseSensitive": True, "leftValue": "", "typeValidation": "strict", "version": 2},
        "conditions": [{"id": str(uuid.uuid4()), "leftValue": "={{ $('Roteia').first().json.em_sessao }}", "rightValue": "",
                        "operator": {"type": "boolean", "operation": "true", "singleValue": True}}], "combinator": "and"}, "options": {}},
        P(10, 7)))
    N.append(code("Áudio vira texto", r"""
const r = $('Roteia').first().json, s = $('Resumo').first().json;
return [{json: {numero_contato: r.numero_contato, nome: r.nome, texto: s.transcricao}}];
""", P(11, 6)))
    N.append(code("Monta arquivo", r"""
const r = $('Roteia').first().json;
if (!r.audio) return [];
let s; try { s = $('Resumo').first().json; } catch (e) { s = $('Resumo sem Claude').first().json; }
let resposta = ''; try { resposta = $('Extrai resposta').first().json.texto; } catch (e) {}
const agora = new Date().toLocaleString('sv-SE', {timeZone: 'America/Sao_Paulo'});   // 2026-10-31 09:20:11
const [dia, hora] = agora.split(' ');
const slug = (r.nome || 'contato').normalize('NFD').replace(/[̀-ͯ]/g, '').replace(/[^A-Za-z0-9]+/g, '_').replace(/^_|_$/g, '');
const arquivo = `/files/resumos/${dia}_${hora.replace(/:/g, '')}_${r.numero_contato}_${slug}.md`;
const md = [`# Áudio de ${r.nome}`, '', `- **Contato:** ${r.nome} (${r.numero_contato})`, `- **Data/hora:** ${agora}`,
  `- **Duração:** ${Math.round(s.duracao || 0)} s`, `- **ID da mensagem:** ${r.idmsg}`, `- **Conversa com o assistente:** ${r.em_sessao ? 'sim' : 'não'}`, '',
  s.resumo, '', '## Transcrição', '', ...s.transcricao.split('\n').map(l => '> ' + l)];
if (resposta) md.push('', '## Resposta enviada pelo assistente', '', ...resposta.split('\n').map(l => '> ' + l));
const bin = await this.helpers.prepareBinaryData(Buffer.from(md.join('\n') + '\n', 'utf8'), 'resumo.md', 'text/markdown');
return [{json: {arquivo, numero: r.numero_contato, duracao: s.duracao || 0, transcricao: s.transcricao, resumo: s.resumo}, binary: {data: bin}}];
""", P(11, 8)))
    N.append(node("Grava arquivo", "readWriteFile", 1.1, {"operation": "write", "fileName": "={{ $json.arquivo }}",
                  "dataPropertyName": "data", "options": {}}, P(12, 8)))
    N.append(pg("Registra áudio",
                "INSERT INTO audio_messages(numero,duracao_s,transcricao,resumo,arquivo) VALUES ($1,$2,$3,$4,$5) RETURNING id",
                "={{ [$('Monta arquivo').first().json.numero, $('Monta arquivo').first().json.duracao, $('Monta arquivo').first().json.transcricao, $('Monta arquivo').first().json.resumo, $('Monta arquivo').first().json.arquivo] }}",
                P(13, 8)))
    N.append(code("Ack áudio", r"""
const r = $('Roteia').first().json;
if (r.em_sessao || (r.cfg.ack_audio || 'sim') !== 'sim') return [];
return [{json: {body: {number: r.numero, text: 'Recebi seu áudio ✅ Já guardei o resumo.', delay: 2500}}}];
""", P(14, 8)))
    N.append(code("Msg áudio falhou", msg("'Não consegui entender esse áudio. Pode tentar de novo ou escrever? 🙏'"), P(7, 9)))
    N.append(http("Envia WhatsApp", EVO_URL, BODY, P(15, 3), EVO_HDR, timeout=30000))

    C = {
        "Webhook": [["Normaliza"]], "Normaliza": [["Contato"]], "Contato": [["Roteia"]], "Roteia": [["Switch"]],
        "Switch": [["Encerra sessão"], ["Histórico"], ["Prepara áudio"]],
        "Encerra sessão": [["Msg encerrar"]], "Msg encerrar": [["Envia WhatsApp"]],
        "Histórico": [["Pedido Claude"]], "Pedido Claude": [["Claude conversa"]],
        "Claude conversa": [["Extrai resposta"], ["Msg erro Claude"]],
        "Extrai resposta": [["Salva histórico"]], "Salva histórico": [["Msg resposta"]],
        "Msg resposta": [["Envia WhatsApp", "Monta arquivo"]], "Msg erro Claude": [["Envia WhatsApp"]],
        "Prepara áudio": [["Whisper"]], "Whisper": [["Pedido resumo"], ["Msg áudio falhou"]],
        "Pedido resumo": [["Claude resumo"]], "Claude resumo": [["Resumo"], ["Resumo sem Claude"]],
        "Resumo": [["Em conversa?"]], "Resumo sem Claude": [["Em conversa?"]],
        "Em conversa?": [["Áudio vira texto"], ["Monta arquivo"]],
        "Áudio vira texto": [["Histórico"]],
        "Monta arquivo": [["Grava arquivo"]], "Grava arquivo": [["Registra áudio"]], "Registra áudio": [["Ack áudio"]],
        "Ack áudio": [["Envia WhatsApp"]], "Msg áudio falhou": [["Envia WhatsApp"]],
    }
    # não guarda cópia das mensagens recebidas (quem não é cadastrado nunca fica registrado no n8n)
    wf("wa-recebimento", "WA - Recebimento", N, C, {"saveDataSuccessExecution": "none", "saveManualExecutions": False})


# ------------------------------------------------------------------ 2. AGENDADOR (expande agendamentos em fila espaçada)
EXPANDE_SQL = """WITH g AS (SELECT (SELECT valor::int FROM config WHERE chave='gap_min_s') AS gmin,
                  (SELECT valor::int FROM config WHERE chave='gap_max_s') AS gmax),
due AS (SELECT * FROM schedules WHERE status='pendente' AND quando <= now() ORDER BY quando, id FOR UPDATE SKIP LOCKED),
dest AS (
  SELECT d.id AS ag_id, d.texto, c.numero, c.nome, row_number() OVER (ORDER BY d.id, c.id) AS rn
  FROM due d JOIN contacts c ON c.ativo AND (
       lower(d.destino) = 'todos' OR lower(c.grupo) = lower(d.destino)
    OR c.numero = ANY(string_to_array(regexp_replace(d.destino, '[^0-9,]', '', 'g'), ',')))
),
base AS (SELECT GREATEST(now(), COALESCE((SELECT max(liberar_em) FROM send_queue WHERE status='pendente'), now())) AS t0),
ins AS (
  INSERT INTO send_queue(agendamento_id, numero, texto, liberar_em)
  SELECT ag_id, numero, replace(texto, '{nome}', split_part(nome, ' ', 1)),
         (SELECT t0 FROM base) + make_interval(secs => sum(g.gmin + random() * (g.gmax - g.gmin)) OVER (ORDER BY rn))
  FROM dest, g RETURNING id
),
upd AS (UPDATE schedules SET status='enfileirado' WHERE id IN (SELECT id FROM due) RETURNING *),
prox AS (
  INSERT INTO schedules(titulo, texto, destino, quando, recorrencia)
  SELECT titulo, texto, destino, quando + CASE recorrencia WHEN 'diaria' THEN interval '1 day' WHEN 'semanal' THEN interval '7 days' ELSE interval '1 month' END, recorrencia
  FROM upd WHERE recorrencia IS NOT NULL RETURNING id
)
SELECT (SELECT count(*) FROM upd) AS agendamentos, (SELECT count(*) FROM ins) AS mensagens"""


def build_agendador():
    N = [node("A cada minuto", "scheduleTrigger", 1.2, {"rule": {"interval": [{"field": "minutes", "minutesInterval": 1}]}}, (0, 0)),
         pg("Expande agendamentos na fila", EXPANDE_SQL, None, (260, 0))]
    wf("wa-agendador", "WA - Agendador", N, {"A cada minuto": [["Expande agendamentos na fila"]]}, {"saveDataSuccessExecution": "none"})


# ------------------------------------------------------------------ 3. ENVIADOR (1 mensagem por vez, espaçada)
PEGA_SQL = """WITH cfg AS (SELECT (SELECT valor::int FROM config WHERE chave='gap_min_s') AS gmin,
                    (SELECT valor::int FROM config WHERE chave='max_dia') AS maxdia,
                    (SELECT valor::time FROM config WHERE chave='janela_ini') AS ini,
                    (SELECT valor::time FROM config WHERE chave='janela_fim') AS fim,
                    (SELECT valor FROM config WHERE chave='rodape') AS rodape),
falhas AS (UPDATE send_queue SET status='erro' WHERE status='pendente' AND tentativas >= 3 AND liberar_em <= now() RETURNING id),
pode AS (
  SELECT 1 FROM cfg WHERE (now() AT TIME ZONE 'America/Sao_Paulo')::time BETWEEN ini AND fim
   AND (SELECT count(*) FROM send_queue WHERE status='enviado' AND enviado_em >= (date_trunc('day', now() AT TIME ZONE 'America/Sao_Paulo') AT TIME ZONE 'America/Sao_Paulo')) < maxdia
   AND COALESCE((SELECT max(enviado_em) FROM send_queue WHERE status='enviado') < now() - make_interval(secs => gmin), true)
),
prox AS (SELECT id FROM send_queue WHERE status='pendente' AND tentativas < 3 AND liberar_em <= now() AND EXISTS (SELECT 1 FROM pode)
         ORDER BY liberar_em, id LIMIT 1 FOR UPDATE SKIP LOCKED)
UPDATE send_queue f SET tentativas = tentativas + 1, liberar_em = now() + interval '5 minutes'
FROM prox, cfg WHERE f.id = prox.id
RETURNING f.id, f.numero, f.texto, COALESCE(f.rodape, cfg.rodape) AS rodape"""


def build_enviador():
    N = [node("A cada 20 s", "scheduleTrigger", 1.2, {"rule": {"interval": [{"field": "seconds", "secondsInterval": 20}]}}, (0, 0)),
         pg("Pega próxima da fila", PEGA_SQL, None, (260, 0)),
         code("Monta mensagem", r"""
const j = $input.first().json;
if (!j.id || !j.numero || !j.texto) return [];            // fila vazia: o n8n devolve um item vazio, nunca enviar
const delay = 2500 + Math.floor(Math.random() * 4500);     // simula "digitando…" (2,5 a 7 s)
return [{json: {id: j.id, body: {number: j.numero, text: j.rodape ? j.texto + '\n\n' + j.rodape : j.texto, delay}}}];
""", (520, 0)),
         http("Envia WhatsApp", EVO_URL, BODY, (780, 0), EVO_HDR, timeout=45000, onError="continueErrorOutput"),
         pg("Marca enviado", "UPDATE send_queue SET status='enviado', enviado_em=now(), erro=NULL WHERE id=$1",
            "={{ [$('Monta mensagem').first().json.id] }}", (1040, -60)),
         pg("Registra erro", "UPDATE send_queue SET erro=$2 WHERE id=$1",
            "={{ [$('Monta mensagem').first().json.id, String(($json.error && ($json.error.message || $json.error)) || 'falha no envio').slice(0, 300)] }}", (1040, 80))]
    wf("wa-enviador", "WA - Enviador", N, {
        "A cada 20 s": [["Pega próxima da fila"]], "Pega próxima da fila": [["Monta mensagem"]],
        "Monta mensagem": [["Envia WhatsApp"]], "Envia WhatsApp": [["Marca enviado"], ["Registra erro"]]}, {"saveDataSuccessExecution": "none"})


# ------------------------------------------------------------------ 4/5. FORMULÁRIOS
def campo(label, tipo=None, req=True, ph=None, opts=None):
    f = {"fieldLabel": label, "requiredField": req}
    if tipo: f["fieldType"] = tipo
    if ph: f["placeholder"] = ph
    if opts: f["fieldOptions"] = {"values": [{"option": o} for o in opts]}
    return f


def form(titulo, desc, path, campos, botao):
    return node(titulo, "formTrigger", 2.6, {
        "authentication": "basicAuth", "formTitle": titulo, "formDescription": desc,
        "formFields": {"values": campos}, "responseMode": "onReceived",
        "options": {"path": path, "buttonLabel": botao, "appendAttribution": False}},
        (0, 0), webhookId=str(uuid.uuid4()), credentials=FORM_AUTH)


def fim(nome, titulo, msg, pos):
    """Tela final do formulário (Form Ending) com mensagem dinâmica."""
    return node(nome, "form", 2.5, {"operation": "completion", "completionTitle": titulo, "completionMessage": msg, "options": {}},
                pos, webhookId=str(uuid.uuid4()))


def build_cadastro():
    IF_BOOL = lambda nome, expr, pos: node(nome, "if", 2.2, {"conditions": {
        "options": {"caseSensitive": True, "leftValue": "", "typeValidation": "strict", "version": 2},
        "conditions": [{"id": str(uuid.uuid4()), "leftValue": expr, "rightValue": "",
                        "operator": {"type": "boolean", "operation": "true", "singleValue": True}}], "combinator": "and"}, "options": {}}, pos)
    N = [form("Cadastrar contato", "Cadastre quem vai receber os avisos automáticos e poderá conversar com o assistente.", "cadastrar",
              [campo("Nome"), campo("WhatsApp (com DDD)", ph="11999998888"), campo("Grupo", req=False, ph="geral")], "Cadastrar"),
         code("Valida número", r"""
const j = $input.first().json;
let n = String(j['WhatsApp (com DDD)'] || '').replace(/\D/g, '');
if (n.length === 10 || n.length === 11) n = '55' + n;
const nome = String(j['Nome'] || '').trim().replace(/\s+/g, ' ');
const base = {nome, numero: n, grupo: String(j['Grupo'] || 'geral').trim().toLowerCase() || 'geral'};
if (!nome) return [{json: {...base, valido: false, erro: 'Informe o nome do contato.'}}];
if (!/^55\d{10,11}$/.test(n)) return [{json: {...base, valido: false, erro: 'Número inválido. Informe DDD + número, por exemplo 11999998888.'}}];
return [{json: {...base, valido: true, texto: `Olá, ${nome.split(' ')[0]}! Você foi cadastrado para receber avisos automáticos da Empresa Exemplo neste número. 😊`}}];
""", (260, 0)),
         IF_BOOL("Dados válidos?", "={{ $json.valido }}", (520, 0)),
         fim("Fim - inválido", "Não foi possível cadastrar", "={{ $('Valida número').first().json.erro }}", (780, 120)),
         pg("Procura existente",
            "SELECT c.id, c.nome, c.ativo FROM (SELECT 1) x LEFT JOIN contacts c ON substr(c.numero,3,2) = substr($1,3,2) AND right(c.numero,8) = right($1,8) LIMIT 1",
            "={{ [$json.numero] }}", (780, 0)),
         code("Decide", r"""
const v = $('Valida número').first().json, e = $input.first().json;
const tel = '+' + v.numero.slice(0, 2) + ' ' + v.numero.slice(2, 4) + ' ' + v.numero.slice(4, -4) + '-' + v.numero.slice(-4);
if (e.id == null) return [{json: {...v, novo: true, titulo: 'Contato cadastrado ✅',
  mensagem: `${v.nome} (${tel}) foi cadastrado no grupo “${v.grupo}”. A mensagem de boas-vindas será enviada em alguns minutos, respeitando o espaçamento entre envios.`}}];
if (e.ativo === false) return [{json: {...v, novo: false, titulo: 'Contato já existe (descadastrado)',
  mensagem: `O número ${tel} já está cadastrado como “${e.nome}”, mas foi DESCADASTRADO. Ele NÃO foi reativado e nenhuma mensagem foi enviada. Para reativar, use a página Contatos e descadastro.`}}];
return [{json: {...v, novo: false, titulo: 'Contato já cadastrado',
  mensagem: `O número ${tel} já está cadastrado como “${e.nome}”. Nada foi alterado e nenhuma mensagem foi enviada.`}}];
""", (1040, 0)),
         IF_BOOL("É novo?", "={{ $json.novo }}", (1300, 0)),
         pg("Salva contato", "INSERT INTO contacts(nome,numero,grupo) VALUES ($1,$2,$3) RETURNING numero",
            "={{ [$json.nome, $json.numero, $json.grupo] }}", (1560, -60)),
         pg("Enfileira boas-vindas",
            "INSERT INTO send_queue(numero,texto,liberar_em) VALUES ($1,$2, GREATEST(now(), COALESCE((SELECT max(liberar_em) FROM send_queue WHERE status='pendente'), now())) + make_interval(secs => 45 + random()*105))",
            "={{ [$('Decide').first().json.numero, $('Decide').first().json.texto] }}", (1820, -60)),
         fim("Fim - cadastrado", "={{ $('Decide').first().json.titulo }}", "={{ $('Decide').first().json.mensagem }}", (2080, -60)),
         fim("Fim - já existe", "={{ $('Decide').first().json.titulo }}", "={{ $('Decide').first().json.mensagem }}", (1560, 120))]
    wf("wa-cadastro", "WA - Cadastrar contato (formulário)", N, {
        "Cadastrar contato": [["Valida número"]], "Valida número": [["Dados válidos?"]],
        "Dados válidos?": [["Procura existente"], ["Fim - inválido"]],
        "Procura existente": [["Decide"]], "Decide": [["É novo?"]],
        "É novo?": [["Salva contato"], ["Fim - já existe"]],
        "Salva contato": [["Enfileira boas-vindas"]], "Enfileira boas-vindas": [["Fim - cadastrado"]]})


def build_agendar():
    IF_BOOL = lambda nome, expr, pos: node(nome, "if", 2.2, {"conditions": {
        "options": {"caseSensitive": True, "leftValue": "", "typeValidation": "strict", "version": 2},
        "conditions": [{"id": str(uuid.uuid4()), "leftValue": expr, "rightValue": "",
                        "operator": {"type": "boolean", "operation": "true", "singleValue": True}}], "combinator": "and"}, "options": {}}, pos)
    N = [form("Agendar mensagem", "Programe um aviso. Use {nome} no texto para colocar o primeiro nome de cada pessoa.", "agendar",
              [campo("Título"), campo("Mensagem", "textarea"),
               campo("Destinatários", ph="todos  |  nome de um grupo  |  números separados por vírgula"),
               campo("Data e hora do envio", "dateTime"),
               campo("Repetir", "dropdown", opts=["Não repetir", "Todo dia", "Toda semana", "Todo mês"])], "Agendar"),
         code("Prepara", r"""
const j = $input.first().json;
const rec = {'Todo dia': 'diaria', 'Toda semana': 'semanal', 'Todo mês': 'mensal'}[j['Repetir']] || null;
const quando = String(j['Data e hora do envio'] || '').replace(' ', 'T').slice(0, 16);
const base = {titulo: String(j['Título'] || '').trim(), texto: String(j['Mensagem'] || '').trim(), destino: String(j['Destinatários'] || 'todos').trim() || 'todos', quando, rec, repete: j['Repetir'] || 'Não repetir'};
if (!/^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}$/.test(quando)) return [{json: {...base, ok: false, erro: 'Data e hora inválidas.'}}];
return [{json: {...base, ok: true}}];
""", (260, 0)),
         IF_BOOL("Dados válidos?", "={{ $json.ok }}", (520, 0)),
         fim("Fim - inválido", "Não foi possível agendar", "={{ $('Prepara').first().json.erro }}", (780, 120)),
         pg("Conta destinatários",
            "SELECT count(*)::int AS qtd FROM contacts c WHERE c.ativo AND (lower($1)='todos' OR lower(c.grupo)=lower($1) OR c.numero = ANY(string_to_array(regexp_replace($1,'[^0-9,]','','g'),',')))",
            "={{ [$json.destino] }}", (780, 0)),
         code("Decide", r"""
const p = $('Prepara').first().json, q = $input.first().json.qtd;
const [d, h] = p.quando.split('T'), [a, m, dia] = d.split('-');
const quando = `${dia}/${m}/${a} às ${h}`;
if (!q) return [{json: {...p, enviar: false, tituloFim: 'Nenhum destinatário encontrado',
  mensagem: `Nenhum contato ativo corresponde a “${p.destino}”. Use “todos”, o nome de um grupo existente, ou os números completos (com 55 e DDD) separados por vírgula. Nada foi agendado.`}}];
return [{json: {...p, enviar: true, tituloFim: 'Mensagem agendada ✅',
  mensagem: `“${p.titulo}” será enviada em ${quando} para ${q} contato(s) (${p.destino}). Repetição: ${p.repete}. Os envios saem um de cada vez, com intervalo entre eles, dentro da janela configurada.`}}];
""", (1040, 0)),
         IF_BOOL("Tem destinatários?", "={{ $json.enviar }}", (1300, 0)),
         pg("Salva agendamento",
            "INSERT INTO schedules(titulo,texto,destino,quando,recorrencia) VALUES ($1,$2,$3,($4::timestamp AT TIME ZONE 'America/Sao_Paulo'),$5) RETURNING id",
            "={{ [$json.titulo, $json.texto, $json.destino, $json.quando, $json.rec] }}", (1560, -60)),
         fim("Fim - agendado", "={{ $('Decide').first().json.tituloFim }}", "={{ $('Decide').first().json.mensagem }}", (1820, -60)),
         fim("Fim - sem destino", "={{ $('Decide').first().json.tituloFim }}", "={{ $('Decide').first().json.mensagem }}", (1560, 120))]
    wf("wa-agendar", "WA - Agendar mensagem (formulário)", N, {
        "Agendar mensagem": [["Prepara"]], "Prepara": [["Dados válidos?"]],
        "Dados válidos?": [["Conta destinatários"], ["Fim - inválido"]],
        "Conta destinatários": [["Decide"]], "Decide": [["Tem destinatários?"]],
        "Tem destinatários?": [["Salva agendamento"], ["Fim - sem destino"]],
        "Salva agendamento": [["Fim - agendado"]]})


def build_tarefas():
    IF_BOOL = lambda nome, expr, pos: node(nome, "if", 2.2, {"conditions": {
        "options": {"caseSensitive": True, "leftValue": "", "typeValidation": "strict", "version": 2},
        "conditions": [{"id": str(uuid.uuid4()), "leftValue": expr, "rightValue": "",
                        "operator": {"type": "boolean", "operation": "true", "singleValue": True}}], "combinator": "and"}, "options": {}}, pos)
    LOC = "(now() AT TIME ZONE 'America/Sao_Paulo')"
    N = [node("A cada minuto", "scheduleTrigger", 1.2, {"rule": {"interval": [{"field": "minutes", "minutesInterval": 1}]}}, (0, 60)),
         pg("Envia checklists do dia", f"""SELECT tarefa_enviar_hoje(t.id) AS exec
FROM tasks t JOIN contacts c ON c.id=t.contato_id AND c.ativo
WHERE t.ativo AND {LOC}::date >= t.inicio AND (t.fim IS NULL OR {LOC}::date <= t.fim)
  AND extract(isodow FROM {LOC})::int = ANY(t.dias)
  AND {LOC}::time >= t.hora_envio AND {LOC}::time < t.lembrar_ate
  AND NOT EXISTS (SELECT 1 FROM task_runs e WHERE e.tarefa_id=t.id AND e.data={LOC}::date)""", None, (300, 0)),
         pg("Lembretes a verificar", f"""SELECT e.id AS exec_id, t.checklist, t.nome_checklist, e.data::text AS desde, e.token
FROM task_runs e JOIN tasks t ON t.id=e.tarefa_id JOIN contacts c ON c.id=t.contato_id AND c.ativo
WHERE e.status='pendente' AND t.ativo AND e.envios >= 1
  AND NOT EXISTS (SELECT 1 FROM send_queue f WHERE f.numero=c.numero AND f.status='pendente' AND f.texto LIKE '%' || e.token || '%')
  AND (
    -- lembretes durante o dia (só se a tarefa tiver intervalo definido; 0 = só o aviso diário)
    (t.lembrar_cada_min > 0 AND e.data = {LOC}::date AND now() - e.ultimo_envio >= make_interval(mins => t.lembrar_cada_min) AND {LOC}::time < t.lembrar_ate)
    OR
    -- ocorrência antiga ainda pendente e hoje não é dia de nova ocorrência: cobra uma vez por dia na hora do envio
    (e.data < {LOC}::date AND NOT (extract(isodow FROM {LOC})::int = ANY(t.dias))
       AND (e.ultimo_envio AT TIME ZONE 'America/Sao_Paulo')::date < {LOC}::date AND {LOC}::time >= t.hora_envio AND {LOC}::time < t.lembrar_ate)
  )""", None, (300, 140)),
         node("Checklist foi preenchido?", "httpRequest", 4.5, {
             "method": "GET",
             "url": "={{ $env.CHECKLIST_URL + 'api/interno/concluido?cid=' + $json.checklist + '&desde=' + $json.desde + '&nome=' + encodeURIComponent($json.nome_checklist) + '&token=' + $json.token }}",
             "sendHeaders": True, "headerParameters": {"parameters": [{"name": "X-Interno", "value": "={{ $env.CHECKLIST_INTERNO }}"}]},
             "options": {"timeout": 20000}}, (560, 140), onError="continueErrorOutput"),
         node("Junta", "code", 2, {"mode": "runOnceForEachItem", "jsCode":
              "const e = $('Lembretes a verificar').item.json;\nreturn {json: {...e, concluido: !!$input.item.json.concluido}};"}, (820, 140)),
         IF_BOOL("Já preencheu?", "={{ $json.concluido }}", (1080, 140)),
         pg("Conclui e parabeniza", "SELECT tarefa_concluir($1) AS exec", "={{ [$json.exec_id] }}", (1340, 80)),
         pg("Envia lembrete", "SELECT tarefa_lembrar($1) AS exec", "={{ [$json.exec_id] }}", (1340, 220))]
    wf("wa-tarefas", "WA - Tarefas (checklist por WhatsApp)", N, {
        "A cada minuto": [["Envia checklists do dia", "Lembretes a verificar"]],
        "Lembretes a verificar": [["Checklist foi preenchido?"]],
        "Checklist foi preenchido?": [["Junta"], []],
        "Junta": [["Já preencheu?"]],
        "Já preencheu?": [["Conclui e parabeniza"], ["Envia lembrete"]]}, {"saveDataSuccessExecution": "none"})


def build_checklist_concluido():
    """O app de checklists avisa aqui, na hora, quando alguém envia: conclui a tarefa e manda a mensagem de incentivo."""
    N = [node("Webhook", "webhook", 2.2, {"httpMethod": "POST", "path": "checklist-concluido", "responseMode": "onReceived", "options": {}}, (0, 0), webhookId=str(uuid.uuid4())),
         code("Valida", r"""
const it = $input.first().json;
if (((it.headers || {})['x-interno'] || '') !== $env.CHECKLIST_INTERNO) return [];
const b = it.body || {};
return [{json: {token: String(b.token || '').replace(/[^A-Za-z0-9]/g, '').slice(0, 64), cid: String(b.cid || ''), nome: String(b.nome || '')}}];
""", (260, 0)),
         pg("Conclui a tarefa", "SELECT tarefa_concluir_por_envio($1,$2,$3) AS exec", "={{ [$json.token, $json.cid, $json.nome] }}", (520, 0))]
    wf("wa-checklist-concluido", "WA - Checklist concluído (aviso do app)", N,
       {"Webhook": [["Valida"]], "Valida": [["Conclui a tarefa"]]}, {"saveDataSuccessExecution": "none"})


build_recebimento(); build_agendador(); build_enviador(); build_cadastro(); build_agendar(); build_tarefas(); build_checklist_concluido()

# credenciais (o n8n criptografa ao importar)
env = dict(l.strip().split("=", 1) for l in open(os.path.join(os.path.dirname(OUT), ".env")) if "=" in l)
creds = [
    {"id": "cred-wa-db", "name": "wa-db", "type": "postgres", "data": {
        "host": "postgres", "port": 5432, "database": "wa", "user": "postgres", "password": env["POSTGRES_PASSWORD"],
        "ssl": "disable", "allowUnauthorizedCerts": False, "sshTunnel": False, "maxConnections": 5}},
    {"id": "cred-form-auth", "name": "form-auth", "type": "httpBasicAuth",
     "data": {"user": "admin", "password": env["N8N_OWNER_PASSWORD"]}},
]
os.makedirs(os.path.join(OUT, "_cred"), exist_ok=True)
fd = os.open(os.path.join(OUT, "_cred", "credentials.json"), os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
with os.fdopen(fd, "w") as f: json.dump(creds, f, ensure_ascii=False)
print("ok:", sorted(x for x in os.listdir(OUT) if x.endswith(".json")))
