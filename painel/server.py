"""Painel de mensagens do WhatsApp: lista conversas, mostra mensagens/mídias e envia texto ou arquivo.
Fala com a Evolution API (rede interna) e com o banco 'wa' (contatos cadastrados). Acesso protegido no nginx (login)."""
import base64, glob, hashlib, hmac, json, mimetypes, os, re, subprocess, threading, time
import psycopg2, requests
from flask import Flask, Response, abort, jsonify, redirect, request, send_file

EVO = os.environ.get("EVOLUTION_URL", "http://evolution:8080")
KEY = os.environ["EVOLUTION_API_KEY"]
INST = os.environ.get("EVOLUTION_INSTANCE", "bot")
DB = os.environ.get("WA_DB", "postgresql://postgres:%s@postgres:5432/wa" % os.environ.get("POSTGRES_PASSWORD", ""))
MEDIA = "/data/midia"
RESUMOS = "/resumos"
os.makedirs(MEDIA, exist_ok=True)
app = Flask(__name__)
app.config["MAX_CONTENT_LENGTH"] = 20 * 1024 * 1024
S = requests.Session()
S.headers.update({"apikey": KEY})
_cache, _lock = {}, threading.Lock()


def evo(path, body=None, timeout=90):
    r = S.post(f"{EVO}{path}/{INST}", json=body or {}, timeout=timeout) if body is not None else S.get(f"{EVO}{path}/{INST}", timeout=timeout)
    if r.status_code >= 400:
        raise RuntimeError(f"Evolution {r.status_code}: {r.text[:200]}")
    return r.json()


def cached(k, ttl, fn):
    with _lock:
        v = _cache.get(k)
        if v and time.time() - v[0] < ttl:
            return v[1]
    val = fn()
    with _lock:
        _cache[k] = (time.time(), val)
    return val










# ---------- normalização de mensagens
IGNORA = {"reactionMessage", "protocolMessage", "senderKeyDistributionMessage", "messageContextInfo", "editedMessage"}
TIPOS = {"conversation": "texto", "extendedTextMessage": "texto", "imageMessage": "imagem", "videoMessage": "video",
         "audioMessage": "audio", "documentMessage": "documento", "documentWithCaptionMessage": "documento",
         "stickerMessage": "figurinha", "contactMessage": "contato", "contactsArrayMessage": "contato",
         "locationMessage": "local", "liveLocationMessage": "local", "pollCreationMessage": "enquete"}


def resumos_idx():
    def q():
        idx = {}
        for f in glob.glob(RESUMOS + "/*.md"):
            try:
                t = open(f, encoding="utf8").read()
            except OSError:
                continue
            m = re.search(r"\*\*ID da mensagem:\*\* (\S+)", t)
            if m:
                idx[m.group(1)] = t
        return idx
    return cached("res", 10, q)


def _int(v):
    try:
        return int(v.get("low", 0) if isinstance(v, dict) else v or 0)
    except (TypeError, ValueError):
        return 0


def normaliza(rec):
    msg = rec.get("message") or {}
    mt = rec.get("messageType") or next(iter(msg), "")
    if mt in IGNORA or not msg:
        return None
    inner = msg.get("documentWithCaptionMessage", {}).get("message", {}) if mt == "documentWithCaptionMessage" else msg
    body = inner.get(mt if mt != "documentWithCaptionMessage" else "documentMessage")
    if not isinstance(body, dict):
        body = {}
    tipo = TIPOS.get(mt, "outro")
    texto = msg.get("conversation") or (msg.get("extendedTextMessage") or {}).get("text") or body.get("caption") or ""
    k = rec.get("key") or {}
    o = {"id": k.get("id"), "de_mim": bool(k.get("fromMe")), "ts": rec.get("messageTimestamp"), "tipo": tipo,
         "texto": texto, "midia": tipo in ("imagem", "video", "audio", "documento", "figurinha"),
         "mime": body.get("mimetype"), "tamanho": _int(body.get("fileLength")),
         "autor": rec.get("pushName") if not k.get("fromMe") else None}
    if tipo == "audio":
        o["duracao"] = body.get("seconds")
        r = resumos_idx().get(k.get("id"))
        if r:
            o["resumo"] = r
    if tipo == "documento":
        o["arquivo"] = body.get("fileName") or body.get("title") or "documento"
    if tipo == "contato":
        o["texto"] = body.get("displayName") or "Contato"
    if tipo == "local":
        o["texto"] = " – ".join(x for x in (body.get("name"), body.get("address")) if x) or "Localização"
    if tipo == "outro" and not texto:
        o["texto"] = f"[{mt}]"
    return o



# ======================================================================
# Acesso restrito a CONTATOS CADASTRADOS: nenhuma rota aceita grupo ou número avulso.
# ======================================================================
DB_EVO = DB.rsplit("/", 1)[0] + "/evolution"


def _q(dsn, sql, args=()):
    with psycopg2.connect(dsn) as c, c.cursor() as cur:
        cur.execute(sql, args)
        return cur.fetchall()


def chave(num):
    """DDD + 8 últimos dígitos: casa o número com ou sem o 9º dígito."""
    return (num[2:4], num[-8:]) if num and num.startswith("55") and len(num) >= 12 else None


def mapa_lid():
    """identificador @lid -> telefone, aprendido nas mensagens recentes guardadas pela Evolution."""
    def q():
        rows = _q(DB_EVO, """SELECT DISTINCT key->>'remoteJid', key->>'remoteJidAlt' FROM "Message"
                             WHERE key->>'remoteJidAlt' IS NOT NULL""")
        m = {}
        for a, b in rows:
            lid, tel = (a, b) if a.endswith("@lid") else (b, a)
            if lid.endswith("@lid") and tel.endswith("@s.whatsapp.net"):
                m[lid] = re.sub(r"\D", "", tel.split("@")[0])
        return m
    return cached("lid", 30, q)


def contatos_db():
    def q():
        return _q(DB, """SELECT c.id, c.nome, c.numero, c.grupo, c.criado,
                         COALESCE(s.ativa AND s.ultima_msg > now() - make_interval(mins => (SELECT valor::int FROM config WHERE chave='timeout_min')), false),
                         (SELECT count(*) FROM audio_messages a WHERE right(a.numero,8)=right(c.numero,8)), c.ativo
                         FROM contacts c LEFT JOIN sessions s ON s.numero=c.numero ORDER BY c.nome""")
    return cached("cdb", 10, q)


def conversas():
    """Para cada contato cadastrado: jids associados (telefone e @lid) e última mensagem."""
    def q():
        chats = evo("/chat/findChats", {})
        lid = mapa_lid()
        por = {}
        for c in chats:
            jid = c.get("remoteJid") or ""
            if jid.endswith("@g.us") or jid.endswith("@broadcast") or jid.startswith("status@"):
                continue
            tel = re.sub(r"\D", "", jid.split("@")[0]) if jid.endswith("@s.whatsapp.net") else lid.get(jid)
            k = chave(tel)
            if k:
                por.setdefault(k, []).append(c)
        return por
    return cached("conv", 6, q)


def contato_jids(row):
    return [c["remoteJid"] for c in conversas().get(chave(row[2]), [])]


def jids_permitidos():
    out = {}
    for r in contatos_db():
        for j in contato_jids(r):
            out[j] = r[0]
    return out


def acha_contato(cid):
    for r in contatos_db():
        if r[0] == cid:
            return r
    abort(404)


def fmt_tel(n):
    return f"+{n[:2]} {n[2:4]} {n[4:-4]}-{n[-4:]}" if n and len(n) >= 12 else (n or "")


def previa(n):
    if not n:
        return ""
    return {"texto": n["texto"], "imagem": "📷 " + (n["texto"] or "Foto"), "video": "🎬 " + (n["texto"] or "Vídeo"),
            "audio": "🎤 Áudio", "documento": "📄 " + (n.get("arquivo") or "Documento"), "figurinha": "Figurinha"}.get(n["tipo"], n["texto"])


# ---------------------------------------------------------------- login por sessão (cookie assinado, 30 dias)
SENHA = os.environ.get("PAINEL_SENHA", "")
SEGREDO = os.environ.get("PAINEL_SEGREDO", "").encode()
DIAS = 30
_tentativas = {}


def _assina(exp):
    return hmac.new(SEGREDO, str(exp).encode(), hashlib.sha256).hexdigest()


def _sessao_ok():
    t = request.cookies.get("wa_auth", "")
    try:
        exp, sig = t.split(".", 1)
        return int(exp) > time.time() and hmac.compare_digest(sig, _assina(exp))
    except Exception:
        return False


from werkzeug.exceptions import HTTPException


@app.errorhandler(HTTPException)
def _erro_json(e):                # as telas leem {"erro": "..."}; inclui mensagens como "Muitos envios em 1 minuto"
    if request.path.startswith("/auth"):
        return e
    return jsonify(erro=e.description if e.code in (400, 403, 415, 429) else e.name), e.code


@app.get("/auth")
def auth():                       # usado pelo nginx (auth_request): 204 = liberado, 401 = pedir login
    return ("", 204) if SEGREDO and _sessao_ok() else ("", 401)


def _destino(n):
    return n if n and n.startswith("/whatsapp/") and "//" not in n and "\\" not in n else "/whatsapp/mensagens/"


@app.post("/login")
def login():
    ip = request.headers.get("X-Real-IP") or request.remote_addr
    agora = time.time()
    tent = [t for t in _tentativas.get(ip, []) if agora - t < 600]
    nxt = _destino(request.form.get("next"))
    if len(tent) >= 8:
        return redirect("/whatsapp/entrar/?erro=2&next=" + requests.utils.quote(nxt), 303)
    ok = bool(SENHA and SEGREDO) and hmac.compare_digest(request.form.get("senha", "").encode(), SENHA.encode())
    if not ok:
        tent.append(agora); _tentativas[ip] = tent
        time.sleep(1)
        return redirect("/whatsapp/entrar/?erro=1&next=" + requests.utils.quote(nxt), 303)
    exp = int(agora + DIAS * 86400)
    r = redirect(nxt, 303)
    r.set_cookie("wa_auth", f"{exp}.{_assina(exp)}", max_age=DIAS * 86400, path="/whatsapp/", secure=True, httponly=True, samesite="Lax")
    return r


@app.route("/logout", methods=["GET", "POST"])
def logout():
    r = redirect("/whatsapp/entrar/", 303)
    r.delete_cookie("wa_auth", path="/whatsapp/")
    return r


@app.get("/resumo")
def resumo():
    cont = contatos_db()
    pend, env = _q(DB, """SELECT (SELECT count(*) FROM send_queue WHERE status='pendente'),
                          (SELECT count(*) FROM send_queue WHERE status='enviado' AND enviado_em >= (date_trunc('day', now() AT TIME ZONE 'America/Sao_Paulo') AT TIME ZONE 'America/Sao_Paulo'))""")[0]
    try:
        estado = S.get(f"{EVO}/instance/connectionState/{INST}", timeout=8).json().get("instance", {}).get("state")
    except Exception:
        estado = "erro"
    return jsonify(contatos=sum(1 for r in cont if r[7]), descadastrados=sum(1 for r in cont if not r[7]), assistente_ativo=sum(1 for r in cont if r[5] and r[7]), audios=sum(r[6] for r in cont),
                   fila=pend, enviadas_hoje=env, estado=estado)


@app.get("/contatos")
def contatos():
    q = (request.args.get("q") or "").strip().lower()
    out = []
    for r in contatos_db():
        cid, nome, numero, grupo, criado, ass, naud, ativo = r
        lm = None
        for c in conversas().get(chave(numero), []):
            m = c.get("lastMessage") or {}
            if m and (not lm or (m.get("messageTimestamp") or 0) > (lm.get("messageTimestamp") or 0)):
                lm = m
        n = normaliza(lm) if lm else None
        item = {"id": cid, "nome": nome, "numero": numero, "tel": fmt_tel(numero), "grupo": grupo, "assistente": bool(ass and ativo), "ativo": ativo,
                "previa": (previa(n) or "")[:90], "de_mim": bool(n and n["de_mim"]), "ts": (lm or {}).get("messageTimestamp") or 0}
        if q and q not in nome.lower() and q not in numero and q not in item["previa"].lower():
            continue
        out.append(item)
    out.sort(key=lambda x: (-x["ts"], x["nome"].lower()))
    return jsonify(out)


@app.get("/contatos/<int:cid>")
def contato(cid):
    r = acha_contato(cid)
    aud = _q(DB, "SELECT criado, duracao_s, resumo, transcricao FROM audio_messages WHERE right(numero,8)=right(%s,8) ORDER BY id DESC LIMIT 5", (r[2],))
    return jsonify(id=r[0], nome=r[1], numero=r[2], tel=fmt_tel(r[2]), grupo=r[3], criado=r[4].isoformat(), assistente=bool(r[5] and r[7]), ativo=r[7], audios_total=r[6],
                   audios=[{"criado": a[0].isoformat(), "duracao": float(a[1] or 0), "resumo": a[2], "transcricao": a[3]} for a in aud])


@app.get("/contatos/<int:cid>/mensagens")
def mensagens(cid):
    r = acha_contato(cid)
    pagina = max(1, int(request.args.get("pagina", 1)))
    todas, paginas = {}, 1
    for jid in contato_jids(r):
        d = evo("/chat/findMessages", {"where": {"key": {"remoteJid": jid}}, "page": pagina, "offset": 30})["messages"]
        paginas = max(paginas, d.get("pages", 1))
        for m in d["records"]:
            n = normaliza(m)
            if n and n["id"]:
                todas[n["id"]] = n
    msgs = sorted(todas.values(), key=lambda x: x["ts"] or 0)
    return jsonify({"mensagens": msgs, "pagina": pagina, "paginas": paginas})


def _acha_registro(msgid):
    r = evo("/chat/findMessages", {"where": {"key": {"id": msgid}}, "page": 1, "offset": 1})["messages"]["records"]
    if not r:
        abort(404)
    if (r[0].get("key") or {}).get("remoteJid") not in jids_permitidos():      # só mídia de cadastrados
        abort(403)
    return r[0]

@app.get("/midia/<msgid>")
def midia(msgid):
    if not re.fullmatch(r"[0-9A-Za-z]{8,64}", msgid):
        abort(400)
    meta_p = f"{MEDIA}/{msgid}.json"
    if not os.path.exists(meta_p):
        rec = _acha_registro(msgid)          # já recusa (403) mídia de grupo ou de quem não é cadastrado
        try:
            d = evo("/chat/getBase64FromMediaMessage", {"message": {"key": rec["key"]}, "convertToMp4": False}, timeout=120)
        except RuntimeError:
            return jsonify(erro="Mídia indisponível (expirou no WhatsApp ou não foi baixada)."), 404
        raw = base64.b64decode(d["base64"])
        mime = (d.get("mimetype") or "application/octet-stream").split(";")[0]
        nome = d.get("fileName") or msgid
        ext = mimetypes.guess_extension(mime) or ""
        if mime in ("audio/ogg", "audio/opus"):
            ext = ".ogg"
        arq = f"{MEDIA}/{msgid}{ext}"
        open(arq, "wb").write(raw)
        if mime.startswith("audio/"):                        # navegadores (Safari) não tocam ogg/opus: converte para MP3
            mp3 = f"{MEDIA}/{msgid}.mp3"
            subprocess.run(["ffmpeg", "-loglevel", "error", "-y", "-i", arq, "-vn", "-codec:a", "libmp3lame", "-q:a", "5", mp3], check=False, timeout=120)
            if os.path.exists(mp3) and os.path.getsize(mp3) > 0:
                arq, mime = mp3, "audio/mpeg"
        json.dump({"arquivo": arq, "mime": mime, "nome": nome, "jid": rec["key"]["remoteJid"]}, open(meta_p, "w"))
    meta = json.load(open(meta_p))
    if meta.get("jid") not in jids_permitidos():          # vale também para mídia que já está em cache
        abort(403)
    resp = send_file(meta["arquivo"], mimetype=meta["mime"], conditional=True, max_age=86400)
    if request.args.get("baixar") == "1" or not (meta["mime"].startswith(("image/", "audio/", "video/")) or meta["mime"] == "application/pdf"):
        nome = meta["nome"].replace('"', "")
        resp.headers["Content-Disposition"] = "attachment; filename*=UTF-8''" + requests.utils.quote(nome)
    else:
        resp.headers["Content-Disposition"] = "inline"
    resp.headers["X-Content-Type-Options"] = "nosniff"
    return resp



_envios = []


def _limite():
    agora = time.time()
    _envios[:] = [t for t in _envios if agora - t < 60]
    if len(_envios) >= 20:
        abort(429, "Muitos envios em 1 minuto. Aguarde.")
    _envios.append(agora)


@app.post("/contatos/<int:cid>/enviar")
def enviar(cid):
    if not request.is_json:
        abort(415)
    r = acha_contato(cid)
    if not _ativo(cid):
        return jsonify(erro="Contato descadastrado: não recebe mensagens."), 403
    texto = (request.get_json().get("texto") or "").strip()
    if not texto or len(texto) > 4000:
        abort(400, "Mensagem vazia ou muito longa")
    _limite()
    x = S.post(f"{EVO}/message/sendText/{INST}", json={"number": r[2], "text": texto, "delay": 1200}, timeout=60)
    if x.status_code >= 400:
        return jsonify(erro="Falha ao enviar: " + x.text[:200]), 502
    with _lock:
        _cache.pop("conv", None)
    return jsonify(ok=True)


@app.post("/contatos/<int:cid>/enviar-arquivo")
def enviar_arquivo(cid):
    r = acha_contato(cid)
    if not _ativo(cid):
        return jsonify(erro="Contato descadastrado: não recebe mensagens."), 403
    f = request.files.get("arquivo")
    if not f:
        abort(400, "Escolha um arquivo")
    _limite()
    raw = f.read()
    mime = (f.mimetype or mimetypes.guess_type(f.filename or "")[0] or "application/octet-stream")
    b64 = base64.b64encode(raw).decode()
    legenda = (request.form.get("legenda") or "")[:1000]
    if mime.startswith("audio/"):
        x = S.post(f"{EVO}/message/sendWhatsAppAudio/{INST}", json={"number": r[2], "audio": b64}, timeout=120)
    else:
        tipo = "image" if mime.startswith("image/") else "video" if mime.startswith("video/") else "document"
        x = S.post(f"{EVO}/message/sendMedia/{INST}", json={"number": r[2], "mediatype": tipo, "mimetype": mime, "caption": legenda,
                   "media": b64, "fileName": f.filename or "arquivo"}, timeout=180)
    if x.status_code >= 400:
        return jsonify(erro="Falha ao enviar: " + x.text[:200]), 502
    with _lock:
        _cache.pop("conv", None)
    return jsonify(ok=True)


def _exec(sql, args=()):
    with psycopg2.connect(DB) as c, c.cursor() as cur:
        cur.execute(sql, args)
        return cur.rowcount


def _ativo(cid):
    r = _q(DB, "SELECT ativo FROM contacts WHERE id=%s", (cid,))
    return bool(r and r[0][0])


@app.get("/gerenciar")
def gerenciar():
    pend = {n: q for n, q in _q(DB, "SELECT numero, count(*) FROM send_queue WHERE status='pendente' GROUP BY numero")}
    rows = _q(DB, "SELECT id, nome, numero, grupo, criado, ativo FROM contacts ORDER BY ativo DESC, lower(nome)")
    return jsonify([{"id": i, "nome": n, "numero": num, "tel": fmt_tel(num), "grupo": g, "criado": c.isoformat(), "ativo": a,
                     "pendentes": pend.get(num, 0)} for i, n, num, g, c, a in rows])


@app.post("/gerenciar/<int:cid>/ativo")
def gerenciar_ativo(cid):
    if not request.is_json:
        abort(415)
    ativo = bool((request.get_json() or {}).get("ativo"))
    r = _q(DB, "SELECT numero, nome FROM contacts WHERE id=%s", (cid,))
    if not r:
        abort(404)
    numero, nome = r[0]
    _exec("UPDATE contacts SET ativo=%s WHERE id=%s", (ativo, cid))
    cancel = 0
    if not ativo:                       # descadastrar: encerra a conversa e cancela avisos que ainda estavam na fila
        _exec("UPDATE sessions SET ativa=false WHERE numero=%s", (numero,))
        cancel = _exec("DELETE FROM send_queue WHERE numero=%s AND status='pendente'", (numero,))
    with _lock:
        _cache.pop("cdb", None)
    return jsonify(ok=True, nome=nome, ativo=ativo, avisos_cancelados=cancel)


# ---------------------------------------------------------------- informações da empresa (usadas pelo assistente)
LIM_TITULO, LIM_TEXTO, LIM_TOTAL = 80, 2000, 12000


def _conh_ok(titulo, texto):
    titulo, texto = (titulo or "").strip(), (texto or "").strip()
    if not titulo or not texto:
        abort(400, "Preencha o assunto e o texto.")
    if len(titulo) > LIM_TITULO or len(texto) > LIM_TEXTO:
        abort(400, f"Assunto até {LIM_TITULO} caracteres e texto até {LIM_TEXTO}.")
    return titulo, texto


def _conh_total(excluir=None):
    r = _q(DB, "SELECT COALESCE(sum(length(titulo)+length(texto)),0) FROM knowledge WHERE ativo AND id IS DISTINCT FROM %s", (excluir,))
    return int(r[0][0])


@app.get("/conhecimento")
def conhecimento_lista():
    rows = _q(DB, "SELECT id, titulo, texto, ativo, atualizado FROM knowledge ORDER BY ordem, id")
    return jsonify(itens=[{"id": i, "titulo": t, "texto": x, "ativo": a, "atualizado": u.isoformat()} for i, t, x, a, u in rows],
                   usados=_conh_total(), limite=LIM_TOTAL)


@app.post("/conhecimento")
def conhecimento_novo():
    if not request.is_json:
        abort(415)
    j = request.get_json() or {}
    t, x = _conh_ok(j.get("titulo"), j.get("texto"))
    if _conh_total() + len(t) + len(x) > LIM_TOTAL:
        return jsonify(erro=f"Limite de {LIM_TOTAL} caracteres no total. Resuma ou desative algum assunto."), 400
    r = _q(DB, "INSERT INTO knowledge(titulo,texto,ordem) VALUES (%s,%s,(SELECT COALESCE(max(ordem),0)+1 FROM knowledge)) RETURNING id", (t, x))
    return jsonify(ok=True, id=r[0][0]), 201


@app.put("/conhecimento/<int:cid>")
def conhecimento_edita(cid):
    if not request.is_json:
        abort(415)
    j = request.get_json() or {}
    t, x = _conh_ok(j.get("titulo"), j.get("texto"))
    ativo = bool(j.get("ativo", True))
    if ativo and _conh_total(cid) + len(t) + len(x) > LIM_TOTAL:
        return jsonify(erro=f"Limite de {LIM_TOTAL} caracteres no total. Resuma ou desative algum assunto."), 400
    n = _exec("UPDATE knowledge SET titulo=%s, texto=%s, ativo=%s, atualizado=now() WHERE id=%s", (t, x, ativo, cid))
    if not n:
        abort(404)
    return jsonify(ok=True)


@app.delete("/conhecimento/<int:cid>")
def conhecimento_apaga(cid):
    if not _exec("DELETE FROM knowledge WHERE id=%s", (cid,)):
        abort(404)
    return jsonify(ok=True)



# ---------------------------------------------------------------- pausa dos envios aos vendedores
_VEND = "e.tipo !~ '^(aviso|resumo|entrega|conferencia)$'"   # mensagens aos vendedores (não conta os avisos ao admin)


@app.get("/pausa")
def pausa_estado():
    flag = _q(DB, "SELECT valor FROM config WHERE chave='envio_pausado'")
    na_fila = _q(DB, f"SELECT count(*) FROM send_queue f JOIN sales_rep_messages e ON e.fila_id=f.id WHERE f.status='pendente' AND {_VEND}")[0][0]
    pausadas = _q(DB, "SELECT count(*) FROM send_queue WHERE status='erro' AND erro='PAUSADO'")[0][0]
    return jsonify(pausado=bool(flag and flag[0][0] == "sim"), na_fila=na_fila, pausadas=pausadas)


@app.post("/pausa")
def pausa_acao():
    if not request.is_json:
        abort(415)
    acao = (request.get_json() or {}).get("acao")
    if acao == "pausar":
        _exec("INSERT INTO config(chave,valor) VALUES('envio_pausado','sim') ON CONFLICT (chave) DO UPDATE SET valor='sim'")
        n = _exec(f"UPDATE send_queue f SET status='erro', erro='PAUSADO' FROM sales_rep_messages e WHERE e.fila_id=f.id AND f.status='pendente' AND {_VEND}")
        return jsonify(ok=True, acao=acao, mensagens=n)
    if acao == "retomar":
        _exec("INSERT INTO config(chave,valor) VALUES('envio_pausado','nao') ON CONFLICT (chave) DO UPDATE SET valor='nao'")
        n = _exec("""UPDATE send_queue f SET status='pendente', erro=NULL, tentativas=0, liberar_em = now() + (x.rn * 70) * interval '1 second'
                     FROM (SELECT id, row_number() OVER (ORDER BY liberar_em, id) AS rn FROM send_queue WHERE status='erro' AND erro='PAUSADO') x
                     WHERE f.id = x.id""")
        return jsonify(ok=True, acao=acao, mensagens=n)
    if acao == "descartar":
        n = _exec("UPDATE send_queue SET erro='CANCELADO' WHERE status='erro' AND erro='PAUSADO'")
        return jsonify(ok=True, acao=acao, mensagens=n)
    abort(400)


@app.get("/saude")
def saude():
    try:
        s = S.get(f"{EVO}/instance/connectionState/{INST}", timeout=10).json()
        return jsonify(estado=s.get("instance", {}).get("state"))
    except Exception as e:
        return jsonify(estado="erro", detalhe=str(e)[:100]), 502
