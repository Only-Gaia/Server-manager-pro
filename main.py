"""
Server Manager Pro - bot Discord (discord.py 2.4+), tutto in un unico file: main.py
Prefissi: "." e "/" (comandi ibridi). Aiuto: .help  /help

Installazione:  pip install -U discord.py python-dotenv
Config (.env):  DISCORD_TOKEN=...   OWNER_IDS=123,456   DEV_IDS=789
Nel Developer Portal attiva: SERVER MEMBERS INTENT e MESSAGE CONTENT INTENT.
Avvio:          python main.py
"""
import asyncio
import io
import json
import os
import random
import re
import time
import traceback
from datetime import datetime, timedelta, timezone
from typing import Optional

import discord
from discord import app_commands
from discord.ext import commands

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

TOKEN = os.getenv("DISCORD_TOKEN")
OWNER_IDS = {int(x) for x in os.getenv("OWNER_IDS", "").split(",") if x.strip().isdigit()}
DEV_IDS = {int(x) for x in os.getenv("DEV_IDS", "").split(",") if x.strip().isdigit()}
DB_FILE = "data.json"
START = time.time()

intents = discord.Intents.default()
intents.members = True
intents.message_content = True


class Bot(commands.Bot):
    async def setup_hook(self):
        tickets_register(impl, gdata, save, self, is_staff, is_admin)
        build_commands()
        self.add_check(global_check)
        await self.tree.sync()  # sincronizza gli slash command


bot = Bot(command_prefix=commands.when_mentioned_or("."), intents=intents,
          help_command=None, case_insensitive=True)

# ----------------------------------------------------------------- DATABASE
try:
    with open(DB_FILE, encoding="utf-8") as f:
        DATA = json.load(f)
except (FileNotFoundError, json.JSONDecodeError):
    DATA = {"guilds": {}}


def save():
    with open(DB_FILE, "w", encoding="utf-8") as f:
        json.dump(DATA, f, indent=2, ensure_ascii=False)


def gdata(guild_id):
    return DATA["guilds"].setdefault(str(guild_id), {
        "cases": [], "case_counter": 0, "notes": {},
        "staff_roles": [], "admin_roles": []})


# ------------------------------------------------------------------ PERMESSI
def is_admin(m: discord.Member) -> bool:
    if m.id == m.guild.owner_id or m.guild_permissions.administrator:
        return True
    return any(r.id in gdata(m.guild.id)["admin_roles"] for r in m.roles)


def is_staff(m: discord.Member) -> bool:
    if is_admin(m):
        return True
    return any(r.id in gdata(m.guild.id)["staff_roles"] for r in m.roles)


async def global_check(ctx: commands.Context) -> bool:
    """Controlla livello interno + permesso Discord definiti in extras."""
    if ctx.command is None:
        return True
    level = ctx.command.extras.get("level", "P")
    perm = ctx.command.extras.get("perm", "")
    uid = ctx.author.id
    if level == "O":
        if uid in OWNER_IDS or await bot.is_owner(ctx.author):
            return True
        raise commands.CheckFailure("🛠️ Comando riservato al proprietario del bot.")
    if level == "D":
        if uid in DEV_IDS or uid in OWNER_IDS or await bot.is_owner(ctx.author):
            return True
        raise commands.CheckFailure("🛠️ Comando riservato agli sviluppatori autorizzati.")
    if (level in ("S", "A") or perm) and ctx.guild is None:
        raise commands.CheckFailure("Questo comando funziona solo in un server.")
    if level == "A" and not is_admin(ctx.author):
        raise commands.CheckFailure("👑 Serve il permesso interno Admin.")
    if level == "S" and not is_staff(ctx.author):
        raise commands.CheckFailure("🔒 Serve il permesso interno Staff.")
    if perm and not getattr(ctx.author.guild_permissions, perm, False):
        raise commands.CheckFailure(f"Ti manca il permesso Discord `{perm}`.")
    return True


def check_hierarchy(ctx, target: discord.Member):
    if target.id == ctx.guild.owner_id or target == ctx.guild.me:
        raise commands.CheckFailure("Non puoi agire su questo utente.")
    if ctx.author.id != ctx.guild.owner_id and target.top_role >= ctx.author.top_role:
        raise commands.CheckFailure("Quell'utente ha un ruolo pari o superiore al tuo.")
    if target.top_role >= ctx.guild.me.top_role:
        raise commands.CheckFailure("Il mio ruolo è troppo basso nella gerarchia.")


# -------------------------------------------------------------------- CASI
def add_case(ctx, kind, user_id, reason):
    gd = gdata(ctx.guild.id)
    gd["case_counter"] += 1
    case = {"id": gd["case_counter"], "type": kind, "user": user_id,
            "mod": ctx.author.id, "reason": reason, "ts": int(time.time())}
    gd["cases"].append(case)
    save()
    return case


def fmt_case(c):
    return f"`#{c['id']}` **{c['type']}** <@{c['user']}> da <@{c['mod']}> — {c['reason']} (<t:{c['ts']}:R>)"


# ------------------------------------------------------- IMPLEMENTAZIONI
IMPL = {}


def impl(key):
    def deco(fn):
        IMPL[key] = fn
        return fn
    return deco


# --- Moderazione
@impl("warn")
async def _warn(ctx, member: discord.Member, *, reason: str = "Nessun motivo"):
    """Avvisa un membro"""
    check_hierarchy(ctx, member)
    c = add_case(ctx, "warn", member.id, reason)
    await ctx.send(f"⚠️ {member.mention} avvisato (caso #{c['id']}): {reason}")


@impl("warnings")
async def _warnings(ctx, member: discord.Member):
    """Mostra gli avvisi di un membro"""
    cs = [c for c in gdata(ctx.guild.id)["cases"] if c["user"] == member.id and c["type"] == "warn"]
    await ctx.send("\n".join(fmt_case(c) for c in cs[-15:]) or "Nessun avviso.")


@impl("warning-remove")
async def _warning_remove(ctx, case_id: int):
    """Rimuove un avviso tramite ID caso"""
    gd = gdata(ctx.guild.id)
    for c in gd["cases"]:
        if c["id"] == case_id and c["type"] == "warn":
            gd["cases"].remove(c)
            save()
            return await ctx.send(f"✅ Avviso #{case_id} rimosso.")
    await ctx.send("Avviso non trovato.")


@impl("history")
async def _history(ctx, member: discord.User):
    """Storico moderazione di un utente"""
    cs = [c for c in gdata(ctx.guild.id)["cases"] if c["user"] == member.id]
    await ctx.send("\n".join(fmt_case(c) for c in cs[-15:]) or "Nessun caso.")


@impl("cases")
async def _cases(ctx):
    """Ultimi casi di moderazione del server"""
    cs = gdata(ctx.guild.id)["cases"][-10:]
    await ctx.send("\n".join(fmt_case(c) for c in cs) or "Nessun caso.")


@impl("case")
async def _case(ctx, case_id: int):
    """Mostra un caso"""
    for c in gdata(ctx.guild.id)["cases"]:
        if c["id"] == case_id:
            return await ctx.send(fmt_case(c))
    await ctx.send("Caso non trovato.")


@impl("reason")
async def _reason(ctx, case_id: int, *, reason: str):
    """Modifica il motivo di un caso"""
    for c in gdata(ctx.guild.id)["cases"]:
        if c["id"] == case_id:
            c["reason"] = reason
            save()
            return await ctx.send("✅ Motivo aggiornato.")
    await ctx.send("Caso non trovato.")


@impl("note")
async def _note(ctx, member: discord.Member, *, testo: str):
    """Aggiunge una nota staff su un membro"""
    gdata(ctx.guild.id)["notes"].setdefault(str(member.id), []).append(
        {"by": ctx.author.id, "text": testo, "ts": int(time.time())})
    save()
    await ctx.send("📝 Nota aggiunta.")


@impl("clear-notes")
async def _clear_notes(ctx, member: discord.Member):
    """Cancella le note di un membro"""
    gdata(ctx.guild.id)["notes"].pop(str(member.id), None)
    save()
    await ctx.send("🗑️ Note cancellate.")


@impl("timeout")
async def _timeout(ctx, member: discord.Member, minuti: int, *, reason: str = "Nessun motivo"):
    """Mette un membro in timeout (minuti)"""
    check_hierarchy(ctx, member)
    await member.timeout(timedelta(minutes=minuti), reason=reason)
    add_case(ctx, "timeout", member.id, reason)
    await ctx.send(f"⏳ {member.mention} in timeout per {minuti} minuti.")


@impl("timeout-remove")
async def _timeout_remove(ctx, member: discord.Member):
    """Rimuove il timeout"""
    await member.timeout(None)
    await ctx.send(f"✅ Timeout rimosso a {member.mention}.")


@impl("kick")
async def _kick(ctx, member: discord.Member, *, reason: str = "Nessun motivo"):
    """Espelle un membro"""
    check_hierarchy(ctx, member)
    await member.kick(reason=reason)
    add_case(ctx, "kick", member.id, reason)
    await ctx.send(f"👢 {member} espulso.")


@impl("ban")
async def _ban(ctx, user: discord.User, *, reason: str = "Nessun motivo"):
    """Banna un utente"""
    m = ctx.guild.get_member(user.id)
    if m:
        check_hierarchy(ctx, m)
    await ctx.guild.ban(user, reason=reason)
    add_case(ctx, "ban", user.id, reason)
    await ctx.send(f"🔨 {user} bannato.")


@impl("unban")
async def _unban(ctx, user: discord.User, *, reason: str = "Nessun motivo"):
    """Rimuove il ban a un utente"""
    await ctx.guild.unban(user, reason=reason)
    add_case(ctx, "unban", user.id, reason)
    await ctx.send(f"✅ {user} sbannato.")


@impl("softban")
async def _softban(ctx, member: discord.Member, *, reason: str = "Nessun motivo"):
    """Ban + unban: elimina i messaggi recenti"""
    check_hierarchy(ctx, member)
    await ctx.guild.ban(member, reason=reason, delete_message_seconds=86400)
    await ctx.guild.unban(member, reason="softban")
    add_case(ctx, "softban", member.id, reason)
    await ctx.send(f"🧹 {member} softbannato.")


@impl("purge")
async def _purge(ctx, quantita: int):
    """Elimina messaggi dal canale (max 100)"""
    quantita = max(1, min(quantita, 100))
    if ctx.interaction:
        await ctx.defer(ephemeral=True)
    deleted = await ctx.channel.purge(limit=quantita + (0 if ctx.interaction else 1))
    await ctx.send(f"🧹 Eliminati {len(deleted)} messaggi.", ephemeral=True, delete_after=5)


@impl("slowmode")
async def _slowmode(ctx, secondi: int):
    """Imposta la modalità lenta del canale"""
    await ctx.channel.edit(slowmode_delay=max(0, min(secondi, 21600)))
    await ctx.send(f"🐢 Slowmode: {secondi}s.")


@impl("lock")
async def _lock(ctx):
    """Blocca il canale"""
    await ctx.channel.set_permissions(ctx.guild.default_role, send_messages=False)
    await ctx.send("🔒 Canale bloccato.")


@impl("unlock")
async def _unlock(ctx):
    """Sblocca il canale"""
    await ctx.channel.set_permissions(ctx.guild.default_role, send_messages=None)
    await ctx.send("🔓 Canale sbloccato.")


@impl("nickname")
async def _nickname(ctx, member: discord.Member, *, nome: Optional[str] = None):
    """Cambia il soprannome di un membro"""
    check_hierarchy(ctx, member)
    await member.edit(nick=nome)
    await ctx.send("✅ Soprannome aggiornato.")


# --- Ruoli e membri
@impl("role create")
async def _role_create(ctx, *, nome: str):
    """Crea un ruolo"""
    r = await ctx.guild.create_role(name=nome)
    await ctx.send(f"✅ Creato {r.mention}", allowed_mentions=discord.AllowedMentions.none())


@impl("role delete")
async def _role_delete(ctx, ruolo: discord.Role):
    """Elimina un ruolo"""
    if ruolo >= ctx.guild.me.top_role or (ctx.author.id != ctx.guild.owner_id and ruolo >= ctx.author.top_role):
        raise commands.CheckFailure("Ruolo troppo alto in gerarchia.")
    await ruolo.delete()
    await ctx.send("🗑️ Ruolo eliminato.")


@impl("role add")
async def _role_add(ctx, member: discord.Member, ruolo: discord.Role):
    """Assegna un ruolo a un membro"""
    if ruolo >= ctx.guild.me.top_role or (ctx.author.id != ctx.guild.owner_id and ruolo >= ctx.author.top_role):
        raise commands.CheckFailure("Ruolo troppo alto in gerarchia.")
    await member.add_roles(ruolo)
    await ctx.send(f"✅ {ruolo.name} aggiunto a {member.display_name}.")


@impl("role remove")
async def _role_remove(ctx, member: discord.Member, ruolo: discord.Role):
    """Rimuove un ruolo da un membro"""
    if ruolo >= ctx.guild.me.top_role or (ctx.author.id != ctx.guild.owner_id and ruolo >= ctx.author.top_role):
        raise commands.CheckFailure("Ruolo troppo alto in gerarchia.")
    await member.remove_roles(ruolo)
    await ctx.send(f"✅ {ruolo.name} rimosso a {member.display_name}.")


@impl("role info")
async def _role_info(ctx, ruolo: discord.Role):
    """Informazioni su un ruolo"""
    e = discord.Embed(title=ruolo.name, color=ruolo.color)
    e.add_field(name="ID", value=ruolo.id)
    e.add_field(name="Membri", value=len(ruolo.members))
    e.add_field(name="Creato", value=discord.utils.format_dt(ruolo.created_at, "R"))
    await ctx.send(embed=e)


@impl("role list")
async def _role_list(ctx):
    """Elenco dei ruoli del server"""
    txt = ", ".join(r.name for r in reversed(ctx.guild.roles[1:]))
    await ctx.send(txt[:1900] or "Nessun ruolo.")


@impl("role members")
async def _role_members(ctx, ruolo: discord.Role):
    """Membri di un ruolo"""
    txt = ", ".join(m.display_name for m in ruolo.members)
    await ctx.send(txt[:1900] or "Nessun membro.")


@impl("member info")
async def _member_info(ctx, member: Optional[discord.Member] = None):
    """Informazioni su un membro"""
    m = member or ctx.author
    e = discord.Embed(title=str(m), color=m.color)
    e.set_thumbnail(url=m.display_avatar.url)
    e.add_field(name="ID", value=m.id)
    e.add_field(name="Account creato", value=discord.utils.format_dt(m.created_at, "R"))
    e.add_field(name="Entrato", value=discord.utils.format_dt(m.joined_at, "R") if m.joined_at else "?")
    e.add_field(name="Ruolo più alto", value=m.top_role.mention)
    await ctx.send(embed=e)


@impl("member avatar")
async def _member_avatar(ctx, member: Optional[discord.Member] = None):
    """Mostra l'avatar di un membro"""
    m = member or ctx.author
    await ctx.send(m.display_avatar.url)


@impl("utility avatar")
async def _util_avatar(ctx, member: Optional[discord.Member] = None):
    """Mostra l'avatar di un membro"""
    await _member_avatar(ctx, member)


@impl("channel info")
async def _channel_info(ctx):
    """Informazioni sul canale corrente"""
    c = ctx.channel
    e = discord.Embed(title=f"#{getattr(c, 'name', 'DM')}")
    e.add_field(name="ID", value=c.id)
    e.add_field(name="Creato", value=discord.utils.format_dt(c.created_at, "R"))
    await ctx.send(embed=e)


@impl("channel list")
async def _channel_list(ctx):
    """Elenco dei canali del server"""
    await ctx.send(", ".join(c.mention for c in ctx.guild.channels if not isinstance(c, discord.CategoryChannel))[:1900])


@impl("server info")
async def _server_info(ctx):
    """Informazioni sul server"""
    g = ctx.guild
    e = discord.Embed(title=g.name)
    if g.icon:
        e.set_thumbnail(url=g.icon.url)
    e.add_field(name="Membri", value=g.member_count)
    e.add_field(name="Ruoli", value=len(g.roles))
    e.add_field(name="Canali", value=len(g.channels))
    e.add_field(name="Creato", value=discord.utils.format_dt(g.created_at, "R"))
    await ctx.send(embed=e)


# --- Staff interno
@impl("staff add")
async def _staff_add(ctx, ruolo: discord.Role, livello: str = "staff"):
    """Imposta un ruolo come staff o admin interno"""
    key = "admin_roles" if livello.lower().startswith("a") else "staff_roles"
    gd = gdata(ctx.guild.id)
    if ruolo.id not in gd[key]:
        gd[key].append(ruolo.id)
        save()
    await ctx.send(f"✅ {ruolo.name} aggiunto a `{key}`.")


@impl("staff remove")
async def _staff_remove(ctx, ruolo: discord.Role):
    """Rimuove un ruolo dallo staff interno"""
    gd = gdata(ctx.guild.id)
    for key in ("admin_roles", "staff_roles"):
        if ruolo.id in gd[key]:
            gd[key].remove(ruolo.id)
    save()
    await ctx.send("✅ Ruolo rimosso.")


@impl("staff list")
async def _staff_list(ctx):
    """Elenco ruoli staff/admin interni"""
    gd = gdata(ctx.guild.id)
    f = lambda ids: ", ".join(f"<@&{i}>" for i in ids) or "—"
    await ctx.send(f"**Admin:** {f(gd['admin_roles'])}\n**Staff:** {f(gd['staff_roles'])}",
                   allowed_mentions=discord.AllowedMentions.none())


# --- Utilità, sondaggi, promemoria
@impl("poll create")
async def _poll_create(ctx, *, domanda: str):
    """Crea un sondaggio sì/no"""
    msg = await ctx.send(embed=discord.Embed(title="📊 Sondaggio", description=domanda))
    await msg.add_reaction("✅")
    await msg.add_reaction("❌")


@impl("utility choose")
async def _choose(ctx, *, opzioni: str):
    """Sceglie a caso tra opzioni separate da virgola"""
    ops = [o.strip() for o in opzioni.split(",") if o.strip()]
    await ctx.send(f"🎲 {random.choice(ops)}" if ops else "Dammi delle opzioni separate da virgola.")


@impl("utility random-number")
async def _rand(ctx, minimo: int = 1, massimo: int = 100):
    """Numero casuale"""
    await ctx.send(f"🎲 {random.randint(min(minimo, massimo), max(minimo, massimo))}")


@impl("utility timestamp")
async def _timestamp(ctx):
    """Timestamp Discord attuale"""
    t = int(time.time())
    await ctx.send(f"<t:{t}:F> → `<t:{t}:F>`")


@impl("utility server-time")
async def _server_time(ctx):
    """Ora UTC attuale"""
    await ctx.send(datetime.now(timezone.utc).strftime("🕒 %d/%m/%Y %H:%M:%S UTC"))


@impl("utility user-id")
async def _user_id(ctx, member: Optional[discord.Member] = None):
    """ID di un utente"""
    await ctx.send(f"`{(member or ctx.author).id}`")


@impl("utility channel-id")
async def _channel_id(ctx):
    """ID del canale"""
    await ctx.send(f"`{ctx.channel.id}`")


@impl("reminder create")
async def _reminder_create(ctx, minuti: int, *, testo: str):
    """Promemoria personale (si perde se il bot si riavvia)"""
    await ctx.send(f"⏰ Ti ricorderò tra {minuti} minuti.")
    await asyncio.sleep(max(1, minuti) * 60)
    await ctx.channel.send(f"⏰ {ctx.author.mention} promemoria: {testo}")


# --- Diagnostica e bot
@impl("health")
async def _health(ctx):
    """Stato di salute del bot"""
    await ctx.send("✅ Tutto ok.")


@impl("uptime")
async def _uptime(ctx):
    """Da quanto tempo è attivo il bot"""
    await ctx.send(f"⏱️ Online da {timedelta(seconds=int(time.time() - START))}")


@impl("latency")
async def _latency(ctx):
    """Latenza del bot"""
    await ctx.send(f"🏓 {round(bot.latency * 1000)} ms")


@impl("version")
async def _version(ctx):
    """Versione del bot"""
    await ctx.send(f"Server Manager Pro 1.0 · discord.py {discord.__version__}")


@impl("status")
async def _status(ctx):
    """Stato del bot"""
    await ctx.send(f"🟢 Online · {len(bot.guilds)} server · {round(bot.latency * 1000)} ms")


@impl("bot info")
async def _bot_info(ctx):
    """Informazioni sul bot"""
    await ctx.send(f"🤖 **Server Manager Pro** · prefissi `.` e `/` · usa `.help`")


@impl("bot status")
async def _bot_status(ctx):
    """Stato del bot"""
    await _status(ctx)


@impl("bot servers")
async def _bot_servers(ctx):
    """Elenco dei server del bot"""
    await ctx.send("\n".join(f"{g.name} ({g.id}) — {g.member_count}" for g in bot.guilds)[:1900])


@impl("bot sync-commands")
async def _bot_sync(ctx):
    """Risincronizza gli slash command"""
    synced = await bot.tree.sync()
    await ctx.send(f"✅ Sincronizzati {len(synced)} comandi.")


@impl("bot shutdown")
async def _bot_shutdown(ctx):
    """Spegne il bot"""
    await ctx.send("👋 Spegnimento…")
    await bot.close()


# --- Help
ICON = {"P": "🌐", "S": "🔒", "A": "👑", "O": "🛠️", "D": "🛠️"}
REG = []  # {cat, full, level, perm}


def _clean(name):
    """'🛡️ Moderazione' -> 'Moderazione'"""
    return re.sub(r"^[^\w]+", "", name).strip()


def _help_cats():
    cats = {}
    for r in REG:
        cats.setdefault(r["cat"], []).append(r)
    return cats


def _parse_cats(text, names):
    """'moderazione, 6, ticket' -> (categorie trovate, testi non riconosciuti)"""
    text = text.strip()
    if text.lower().startswith("category "):
        text = text[9:]
    found, unknown = [], []
    for tok in re.split(r"[,;+]", text):
        tok = tok.strip()
        if not tok:
            continue
        low = tok.lower()
        if tok.isdigit() and 1 <= int(tok) <= len(names):
            key = names[int(tok) - 1]
        else:
            key = (next((n for n in names if _clean(n).lower() == low), None)
                   or next((n for n in names if low in n.lower()), None))
        if key is None:
            unknown.append(tok)
        elif key not in found:
            found.append(key)
    return found, unknown


def _cat_embed(key, rows):
    lines = [f"{ICON[r['level']]} `/{r['full']}`" + (f" · *{r['perm']}*" if r["perm"] else "")
             for r in rows]
    e = discord.Embed(title=f"📂 {key}", color=0x5865F2)
    chunk, size = [], 0
    for ln in lines:
        if size + len(ln) > 1000:
            e.add_field(name="\u200b", value="\n".join(chunk), inline=False)
            chunk, size = [], 0
        chunk.append(ln)
        size += len(ln) + 1
    if chunk:
        e.add_field(name="\u200b", value="\n".join(chunk), inline=False)
    return e


async def _send_embeds(send, embeds):
    """Invia gli embed a gruppi, rispettando i limiti di Discord (10 embed / ~6000 caratteri)."""
    batch, size = [], 0
    for e in embeds:
        n = len(e)
        if batch and (len(batch) >= 10 or size + n > 5500):
            await send(embeds=batch)
            batch, size = [], 0
        batch.append(e)
        size += n
    if batch:
        await send(embeds=batch)


class HelpView(discord.ui.View):
    """Menu a tendina: scegli una o più categorie e il bot le mostra."""

    def __init__(self, owner_id, names):
        super().__init__(timeout=300)
        self.owner_id, self.names = owner_id, names
        opts = []
        for i, n in enumerate(names):
            m = re.match(r"^([^\w]+)", n)
            emoji = m.group(1).strip() if m else None
            opts.append(discord.SelectOption(label=_clean(n)[:100], value=str(i), emoji=emoji or None))
        self.sel = discord.ui.Select(placeholder="Scegli una o più categorie", min_values=1,
                                     max_values=len(opts), options=opts)
        self.sel.callback = self.picked
        self.add_item(self.sel)

    async def interaction_check(self, interaction):
        if interaction.user.id != self.owner_id:
            await interaction.response.send_message("Usa /help per aprire il tuo menu.", ephemeral=True)
            return False
        return True

    async def picked(self, interaction: discord.Interaction):
        cats = _help_cats()
        keys = [self.names[int(v)] for v in self.sel.values]
        first = True

        async def send(**kw):
            nonlocal first
            if first:
                first = False
                await interaction.response.send_message(ephemeral=True, **kw)
            else:
                await interaction.followup.send(ephemeral=True, **kw)

        await _send_embeds(send, [_cat_embed(k, cats[k]) for k in keys])


async def help_autocomplete(interaction: discord.Interaction, current: str):
    """Suggerisce i nomi delle categorie; si possono scegliere più categorie separate da virgola."""
    names = [_clean(n) for n in _help_cats()]
    parts = re.split(r"[,;+]", current)
    done = [p.strip() for p in parts[:-1] if p.strip()]
    frag = parts[-1].strip().lower()
    chosen = {d.lower() for d in done}
    prefix = ", ".join(done)
    out = []
    for n in names:
        if n.lower() in chosen or frag not in n.lower():
            continue
        value = f"{prefix}, {n}" if prefix else n
        if len(value) <= 100:
            out.append(app_commands.Choice(name=value, value=value))
    return out[:25]


@impl("help")
async def _help(ctx, *, categoria: Optional[str] = None):
    """Mostra l'elenco dei comandi (opzionale: una o più categorie)"""
    cats = _help_cats()
    names = list(cats)
    if not categoria:
        e = discord.Embed(title="📖 Server Manager Pro — Aiuto", color=0x5865F2,
                          description="Prefissi: `.` e `/`\n"
                                      "Scegli una o più categorie dal menu qui sotto, oppure scrivile: "
                                      "`/help categoria:Moderazione, Ticket` o `.help moderazione, ticket`.\n"
                                      "🌐 Pubblico · 🔒 Staff · 👑 Admin · 🛠️ Owner/Dev")
        e.add_field(name="Categorie", value="\n".join(
            f"`{i}` **{n}** ({len(cats[n])})" for i, n in enumerate(names, 1))[:1024])
        return await ctx.send(embed=e, view=HelpView(ctx.author.id, names))
    found, unknown = _parse_cats(categoria, names)
    if unknown:
        await ctx.send("Non ho trovato: " + ", ".join(f"`{u}`" for u in unknown) + ". Usa `.help` per l'elenco.")
    if not found:
        return
    await _send_embeds(lambda **kw: ctx.send(**kw), [_cat_embed(k, cats[k]) for k in found])


# ------------------------------------------------------ SISTEMA TICKET
G = {}  # riferimenti passati da bot.py

STYLE = {"blu": "primary", "primary": "primary", "grigio": "secondary", "secondary": "secondary",
         "verde": "success", "success": "success", "rosso": "danger", "danger": "danger"}
STYLE_IT = {"primary": "blu", "secondary": "grigio", "success": "verde", "danger": "rosso"}

ACTIONS = {
    "claim": "claim", "unclaim": "unclaim",
    "close": "close", "chiudi": "close",
    "reopen": "reopen", "riapri": "reopen",
    "transcript": "transcript", "trascrizione": "transcript",
    "add-member": "add_member", "add_member": "add_member", "aggiungi": "add_member",
    "remove-member": "remove_member", "remove_member": "remove_member", "rimuovi": "remove_member",
    "rename": "rename", "rinomina": "rename",
    "delete": "delete", "elimina": "delete",
    "messaggio": "message", "message": "message",
    "ping": "ping", "staff": "ping",
}
ACTION_IT = {"claim": "claim", "unclaim": "unclaim", "close": "close", "reopen": "reopen",
             "transcript": "transcript", "add_member": "add member", "remove_member": "remove member",
             "rename": "rename", "delete": "delete", "message": "messaggio", "ping": "ping staff"}
PERMS = {"tutti": "tutti", "aperto": "aperto", "staff": "staff", "admin": "admin"}
PERM_HELP = "tutti, aperto (staff + chi l'ha aperto), staff, admin"


def bstyle(name):
    return getattr(discord.ButtonStyle, name)


def _btn(id_, label, emoji, style, action, perm, enabled):
    return {"id": id_, "label": label, "emoji": emoji, "style": style, "action": action,
            "text": "", "perm": perm, "enabled": enabled}


def default_buttons():
    return [
        _btn("claim", "Claim", "🙋", "success", "claim", "staff", True),
        _btn("unclaim", "Unclaim", "↩️", "secondary", "unclaim", "staff", True),
        _btn("close", "Close", "🔒", "danger", "close", "aperto", True),
        _btn("reopen", "Reopen", "🔓", "primary", "reopen", "staff", False),
        _btn("transcript", "Transcript", "📄", "secondary", "transcript", "staff", False),
        _btn("add-member", "Add Member", "➕", "secondary", "add_member", "staff", False),
        _btn("remove-member", "Remove Member", "➖", "secondary", "remove_member", "staff", False),
        _btn("rename", "Rename", "✏️", "secondary", "rename", "staff", False),
        _btn("delete", "Delete", "🗑️", "danger", "delete", "admin", False),
    ]


def _cat(id_, label, emoji, style, msg):
    return {"id": id_, "label": label, "emoji": emoji, "style": style, "message": msg,
            "enabled": True, "role_id": None}


def default_td():
    return {
        "v": 2,
        "categories": [
            _cat("assistenza", "Assistenza", "🛠️", "primary", "Descrivi il tuo problema: uno staff ti risponderà a breve."),
            _cat("partnership", "Partnership", "🤝", "success", "Scrivi qui la tua richiesta di partnership."),
            _cat("giveaway", "Giveaway", "🎉", "secondary", "Scrivi qui la tua richiesta per un giveaway."),
            _cat("segnalazioni", "Segnalazioni", "🚨", "danger", "Descrivi cosa vuoi segnalare e allega le prove."),
            _cat("altro", "Altro", "❓", "secondary", "Spiega di cosa hai bisogno."),
        ],
        "buttons": default_buttons(),
        "panel": {"title": "🎫 Assistenza", "description": "Scegli una categoria qui sotto per aprire un ticket.",
                  "channel_id": None, "message_id": None},
        "category_id": None,
        "close_mode": "delete",  # delete = Close elimina il canale | lock = blocca (Reopen possibile)
        "open": {},
        "counter": 0,
    }


def tdata(guild_id):
    td = G["gdata"](guild_id).setdefault("tickets", default_td())
    if td.get("v") != 2:  # migrazione dalle versioni precedenti
        if isinstance(td.get("buttons"), dict):
            td["buttons"] = [{"id": k, "label": v["label"], "emoji": v["emoji"], "style": v["style"],
                              "action": k, "text": ""} for k, v in td["buttons"].items()]
        have = {b["id"] for b in td["buttons"]}
        for d in default_buttons():
            if d["id"] not in have:
                td["buttons"].append(d)
        for b in td["buttons"]:
            b.setdefault("enabled", True)
            b.setdefault("perm", "staff" if b["action"] in ("claim", "unclaim") else "aperto")
            b.setdefault("text", "")
        for c in td["categories"]:
            c.setdefault("enabled", True)
            c.setdefault("role_id", None)
        td.setdefault("close_mode", "delete")
        td["v"] = 2
        G["save"]()
    return td


def slug(s):
    return re.sub(r"[^a-z0-9]+", "-", s.lower()).strip("-") or "x"


def unique_id(base, items):
    ids = {i["id"] for i in items}
    cid, n = base, 2
    while cid in ids:
        cid, n = f"{base}-{n}", n + 1
    return cid


def valid_emoji(e):
    return e == "" or bool(re.fullmatch(r"<a?:\w+:\d+>", e)) or (len(e) <= 8 and not e.isascii())


def yes(s):
    return s.strip().lower() in ("si", "sì", "s", "yes", "y", "true", "1")


def staff_roles_of(guild):
    gd = G["gdata"](guild.id)
    return [r for r in (guild.get_role(i) for i in gd["staff_roles"] + gd["admin_roles"]) if r]


def allowed(user, tk, perm):
    if perm == "tutti":
        return True
    if perm == "aperto":
        return G["is_staff"](user) or user.id == tk["owner"]
    if perm == "admin":
        return G["is_admin"](user)
    return G["is_staff"](user)


def move_to(items, obj, pos):
    """Sposta obj alla posizione pos (1 = primo)."""
    items.remove(obj)
    items.insert(max(0, min(len(items), pos - 1)), obj)


# ----------------------------------------------------------- VISTE PERSISTENTI
class OpenButton(discord.ui.DynamicItem[discord.ui.Button], template=r"tk:open:(?P<cat>[a-z0-9_-]+)"):
    def __init__(self, cat, label=None, emoji=None, style=discord.ButtonStyle.primary):
        super().__init__(discord.ui.Button(label=label, emoji=emoji or None, style=style,
                                           custom_id=f"tk:open:{cat}"))
        self.cat = cat

    @classmethod
    async def from_custom_id(cls, interaction, item, match, /):
        return cls(match["cat"], item.label, item.emoji, item.style)

    async def callback(self, interaction: discord.Interaction):
        guild, user = interaction.guild, interaction.user
        td = tdata(guild.id)
        cat = next((c for c in td["categories"] if c["id"] == self.cat), None)
        if not cat or not cat["enabled"]:
            return await interaction.response.send_message("Questa categoria non è disponibile.", ephemeral=True)
        if cat.get("role_id") and not any(r.id == cat["role_id"] for r in user.roles):
            return await interaction.response.send_message("Questa categoria è riservata a un ruolo specifico.", ephemeral=True)
        for cid, tk in list(td["open"].items()):
            if guild.get_channel(int(cid)) is None:
                td["open"].pop(cid)
            elif tk["owner"] == user.id:
                return await interaction.response.send_message(f"Hai già un ticket aperto: <#{cid}>", ephemeral=True)
        await interaction.response.defer(ephemeral=True)

        base = discord.PermissionOverwrite(view_channel=True, send_messages=True,
                                           attach_files=True, read_message_history=True)
        ow = {
            guild.default_role: discord.PermissionOverwrite(view_channel=False),
            guild.me: discord.PermissionOverwrite(view_channel=True, send_messages=True,
                                                  manage_channels=True, read_message_history=True),
            user: base,
        }
        roles = staff_roles_of(guild)
        for r in roles:
            ow[r] = base
        parent = guild.get_channel(td["category_id"]) if td["category_id"] else None
        if not isinstance(parent, discord.CategoryChannel):
            parent = None
        td["counter"] += 1
        try:
            ch = await guild.create_text_channel(
                name=f"{cat['id']}-{td['counter']:04d}"[:100], category=parent, overwrites=ow,
                topic=f"Ticket di {user} ({user.id}) · {cat['label']}")
        except discord.Forbidden:
            return await interaction.followup.send("❌ Non ho il permesso di creare canali.", ephemeral=True)
        td["open"][str(ch.id)] = {"owner": user.id, "cat": cat["id"], "claimed_by": None, "closed": False}
        G["save"]()

        e = discord.Embed(title=f"{cat['emoji']} {cat['label']}".strip(),
                          description=cat.get("message") or "Uno staff ti risponderà a breve.", color=0x5865F2)
        e.add_field(name="Aperto da", value=user.mention)
        pings = " ".join(r.mention for r in roles)
        await ch.send(f"{user.mention} {pings}".strip(), embed=e, view=ticket_view(td),
                      allowed_mentions=discord.AllowedMentions(users=True, roles=True))
        await interaction.followup.send(f"✅ Ticket creato: {ch.mention}", ephemeral=True)


class TicketButton(discord.ui.DynamicItem[discord.ui.Button], template=r"tk:(?:act|btn):(?P<bid>[a-z0-9_-]+)"):
    """Pulsante dentro il ticket: azione, attivazione e permessi letti dalla configurazione."""

    def __init__(self, bid, label=None, emoji=None, style=discord.ButtonStyle.secondary):
        super().__init__(discord.ui.Button(label=label, emoji=emoji or None, style=style,
                                           custom_id=f"tk:btn:{bid}"))
        self.bid = bid

    @classmethod
    async def from_custom_id(cls, interaction, item, match, /):
        return cls(match["bid"], item.label, item.emoji, item.style)

    async def callback(self, interaction: discord.Interaction):
        td = tdata(interaction.guild_id)
        b = next((x for x in td["buttons"] if x["id"] == self.bid), None)
        reply = lambda t, **kw: interaction.response.send_message(t, **kw)
        if not b or not b["enabled"]:
            return await reply("Questo pulsante non è più attivo.", ephemeral=True)
        ch, user, act = interaction.channel, interaction.user, b["action"]
        tk = td["open"].get(str(ch.id))
        if not tk:
            return await reply("Questo canale non è un ticket.", ephemeral=True)
        if not allowed(user, tk, b["perm"]):
            return await reply("Non hai l'autorizzazione per usare questo pulsante.", ephemeral=True)
        if act == "claim":
            return await do_claim(ch, user, reply)
        if act == "unclaim":
            return await do_unclaim(ch, user, reply)
        if act == "close":
            return await do_close(ch, user, reply)
        if act == "reopen":
            return await do_reopen(ch, user, reply)
        if act == "delete":
            return await do_delete(ch, user, reply)
        if act == "transcript":
            return await do_transcript(ch, user, reply)
        if act in ("add_member", "remove_member"):
            return await interaction.response.send_modal(MemberModal(act == "add_member"))
        if act == "rename":
            return await interaction.response.send_modal(RenameModal())
        if act == "ping":
            roles = staff_roles_of(interaction.guild)
            return await interaction.response.send_message(
                f"🔔 {user.mention} chiama lo staff {' '.join(r.mention for r in roles)}".strip(),
                allowed_mentions=discord.AllowedMentions(users=True, roles=True))
        await interaction.response.send_message(
            (b.get("text") or "…").replace("{user}", user.mention),
            allowed_mentions=discord.AllowedMentions(users=True))


def panel_view(td):
    v = discord.ui.View(timeout=None)
    for c in [c for c in td["categories"] if c["enabled"]][:25]:
        v.add_item(OpenButton(c["id"], c["label"], c["emoji"], bstyle(c["style"])))
    return v


def ticket_view(td):
    v = discord.ui.View(timeout=None)
    for b in [b for b in td["buttons"] if b["enabled"]][:25]:
        v.add_item(TicketButton(b["id"], b["label"], b["emoji"], bstyle(b["style"])))
    return v


def panel_embed(td):
    p = td["panel"]
    return discord.Embed(title=p["title"], description=p["description"], color=0x5865F2)


async def refresh_panel(guild, td) -> bool:
    p = td["panel"]
    if not (p["channel_id"] and p["message_id"]):
        return True
    ch = guild.get_channel(p["channel_id"])
    if ch is None:
        return True
    try:
        msg = await ch.fetch_message(p["message_id"])
        await msg.edit(embed=panel_embed(td), view=panel_view(td))
        return True
    except discord.NotFound:
        return True
    except discord.HTTPException:
        return False


# ------------------------------------------------------------- AZIONI TICKET
# Il controllo dei permessi è fatto da chi chiama (pulsante o comando).
def _tk(channel):
    return tdata(channel.guild.id)["open"].get(str(channel.id))


async def do_claim(channel, user, reply):
    tk = _tk(channel)
    if not tk:
        return await reply("Questo canale non è un ticket.", ephemeral=True)
    if tk["claimed_by"]:
        return await reply(f"Già preso in carico da <@{tk['claimed_by']}>.", ephemeral=True)
    tk["claimed_by"] = user.id
    G["save"]()
    await reply(f"🙋 {user.mention} ha preso in carico il ticket.")


async def do_unclaim(channel, user, reply):
    tk = _tk(channel)
    if not tk:
        return await reply("Questo canale non è un ticket.", ephemeral=True)
    if not tk["claimed_by"]:
        return await reply("Il ticket non è stato preso in carico.", ephemeral=True)
    if user.id != tk["claimed_by"] and not G["is_admin"](user):
        return await reply("Solo chi l'ha preso in carico (o un admin) può rilasciarlo.", ephemeral=True)
    tk["claimed_by"] = None
    G["save"]()
    await reply(f"↩️ {user.mention} ha rilasciato il ticket.")


async def _remove_channel(channel, user, reply, text):
    td = tdata(channel.guild.id)
    await reply(text)
    await asyncio.sleep(5)
    td["open"].pop(str(channel.id), None)
    G["save"]()
    try:
        await channel.delete(reason=f"Ticket chiuso da {user}")
    except discord.Forbidden:
        await channel.send("❌ Non ho il permesso di eliminare questo canale.")


async def do_close(channel, user, reply):
    td = tdata(channel.guild.id)
    tk = td["open"].get(str(channel.id))
    if not tk:
        return await reply("Questo canale non è un ticket.", ephemeral=True)
    if td["close_mode"] == "delete":
        return await _remove_channel(channel, user, reply,
                                     "🔒 Ticket chiuso: il canale verrà eliminato tra 5 secondi…")
    if tk.get("closed"):
        return await reply("Il ticket è già chiuso.", ephemeral=True)
    owner = channel.guild.get_member(tk["owner"])
    if owner:
        await channel.set_permissions(owner, view_channel=True, send_messages=False, read_message_history=True)
    tk["closed"] = True
    G["save"]()
    await reply(f"🔒 Ticket chiuso da {user.mention}. Lo staff può riaprirlo o eliminarlo.")


async def do_reopen(channel, user, reply):
    tk = _tk(channel)
    if not tk:
        return await reply("Questo canale non è un ticket.", ephemeral=True)
    if not tk.get("closed"):
        return await reply("Il ticket non è chiuso. Con la chiusura impostata su «elimina» "
                           "il canale sparisce e non si può riaprire.", ephemeral=True)
    owner = channel.guild.get_member(tk["owner"])
    if owner:
        await channel.set_permissions(owner, view_channel=True, send_messages=True,
                                      attach_files=True, read_message_history=True)
    tk["closed"] = False
    G["save"]()
    await reply(f"🔓 Ticket riaperto da {user.mention}.")


async def do_delete(channel, user, reply):
    if not _tk(channel):
        return await reply("Questo canale non è un ticket.", ephemeral=True)
    await _remove_channel(channel, user, reply, "🗑️ Il ticket verrà eliminato tra 5 secondi…")


async def do_transcript(channel, user, reply):
    if not _tk(channel):
        return await reply("Questo canale non è un ticket.", ephemeral=True)
    await reply("📄 Genero la trascrizione…", ephemeral=True)
    lines = []
    async for m in channel.history(limit=2000, oldest_first=True):
        atts = " ".join(a.url for a in m.attachments)
        lines.append(f"[{m.created_at:%Y-%m-%d %H:%M}] {m.author}: {m.clean_content} {atts}".rstrip())
    f = discord.File(io.BytesIO("\n".join(lines).encode("utf-8")), filename=f"trascrizione-{channel.name}.txt")
    await channel.send(f"📄 Trascrizione richiesta da {user.mention}", file=f,
                       allowed_mentions=discord.AllowedMentions.none())


async def do_add_member(channel, member, reply):
    if not _tk(channel):
        return await reply("Questo canale non è un ticket.", ephemeral=True)
    await channel.set_permissions(member, view_channel=True, send_messages=True,
                                  attach_files=True, read_message_history=True)
    await reply(f"➕ {member.mention} aggiunto al ticket.", allowed_mentions=discord.AllowedMentions(users=True))


async def do_remove_member(channel, member, reply):
    tk = _tk(channel)
    if not tk:
        return await reply("Questo canale non è un ticket.", ephemeral=True)
    if member.id == tk["owner"]:
        return await reply("Non puoi rimuovere chi ha aperto il ticket.", ephemeral=True)
    await channel.set_permissions(member, overwrite=None)
    await reply(f"➖ {member.display_name} rimosso dal ticket.")


async def do_rename(channel, nome, reply):
    if not _tk(channel):
        return await reply("Questo canale non è un ticket.", ephemeral=True)
    await channel.edit(name=slug(nome)[:90])
    await reply("✏️ Canale rinominato.")


async def _find_member(guild, text):
    m = re.search(r"\d{15,20}", text)
    if not m:
        return None
    uid = int(m.group())
    return guild.get_member(uid) or await guild.fetch_member(uid)


# ------------------------------------------------------------------ MODALI
class MemberModal(discord.ui.Modal):
    def __init__(self, add):
        super().__init__(title="Aggiungi membro" if add else "Rimuovi membro")
        self.add = add
        self.who = discord.ui.TextInput(label="ID o menzione dell'utente", max_length=60)
        self.add_item(self.who)

    async def on_submit(self, interaction: discord.Interaction):
        try:
            member = await _find_member(interaction.guild, self.who.value)
        except discord.NotFound:
            member = None
        if not member:
            return await interaction.response.send_message("Utente non trovato nel server.", ephemeral=True)
        reply = lambda t, **kw: interaction.response.send_message(t, **kw)
        fn = do_add_member if self.add else do_remove_member
        await fn(interaction.channel, member, reply)


class RenameModal(discord.ui.Modal, title="Rinomina il ticket"):
    nome = discord.ui.TextInput(label="Nuovo nome del canale", max_length=90)

    async def on_submit(self, interaction: discord.Interaction):
        await do_rename(interaction.channel, self.nome.value,
                        lambda t, **kw: interaction.response.send_message(t, **kw))


class CategoryModal(discord.ui.Modal):
    def __init__(self, cat=None):
        super().__init__(title="Nuova categoria" if not cat else f"Modifica {cat['label']}"[:45])
        self.cat = cat
        self.nome = discord.ui.TextInput(label="Nome (es. Partnership)", max_length=40,
                                         default=cat["label"] if cat else None)
        self.emoji = discord.ui.TextInput(label="Emoji (opzionale)", required=False, max_length=64,
                                          default=cat["emoji"] if cat else None)
        self.colore = discord.ui.TextInput(label="Colore: blu, grigio, verde, rosso", required=False,
                                           max_length=10, default=STYLE_IT[cat["style"]] if cat else "blu")
        self.msg = discord.ui.TextInput(label="Messaggio mostrato nel ticket", required=False, max_length=500,
                                        style=discord.TextStyle.paragraph,
                                        default=cat.get("message") if cat else None)
        for i in (self.nome, self.emoji, self.colore, self.msg):
            self.add_item(i)

    async def on_submit(self, interaction: discord.Interaction):
        td = tdata(interaction.guild_id)
        style = STYLE.get(self.colore.value.strip().lower() or "blu")
        emoji = self.emoji.value.strip()
        if not style:
            return await interaction.response.send_message("Colore non valido (blu, grigio, verde, rosso).", ephemeral=True)
        if not valid_emoji(emoji):
            return await interaction.response.send_message("Emoji non valida.", ephemeral=True)
        if self.cat is None:
            if len(td["categories"]) >= 25:
                return await interaction.response.send_message("Massimo 25 categorie.", ephemeral=True)
            td["categories"].append({"id": unique_id(slug(self.nome.value), td["categories"]),
                                     "label": self.nome.value.strip(), "emoji": emoji, "style": style,
                                     "message": self.msg.value.strip(), "enabled": True, "role_id": None})
        else:
            self.cat.update(label=self.nome.value.strip(), emoji=emoji, style=style,
                            message=self.msg.value.strip())
        G["save"]()
        ok = await refresh_panel(interaction.guild, td)
        await interaction.response.send_message(
            "✅ Categoria salvata e pannello aggiornato." if ok else
            "⚠️ Salvata, ma Discord ha rifiutato l'aggiornamento del pannello (emoji non valida?).",
            ephemeral=True)


class ButtonModal(discord.ui.Modal):
    def __init__(self, btn=None):
        super().__init__(title="Nuovo pulsante" if not btn else f"Modifica {btn['label']}"[:45])
        self.btn = btn
        self.label = discord.ui.TextInput(label="Nome del pulsante", max_length=80,
                                          default=btn["label"] if btn else None)
        self.emoji = discord.ui.TextInput(label="Emoji (opzionale)", required=False, max_length=64,
                                          default=btn["emoji"] if btn else None)
        self.colore = discord.ui.TextInput(label="Colore: blu, grigio, verde, rosso", max_length=10,
                                           default=STYLE_IT[btn["style"]] if btn else "grigio")
        self.azione = discord.ui.TextInput(label="Azione", max_length=15, placeholder="es. close, transcript, messaggio",
                                           default=btn["action"].replace("_", "-") if btn else "messaggio")
        self.testo = discord.ui.TextInput(label="Testo da inviare (azione messaggio)", required=False,
                                          max_length=1000, style=discord.TextStyle.paragraph,
                                          placeholder="{user} = chi clicca il pulsante",
                                          default=(btn.get("text") or None) if btn else None)
        for i in (self.label, self.emoji, self.colore, self.azione, self.testo):
            self.add_item(i)

    async def on_submit(self, interaction: discord.Interaction):
        td = tdata(interaction.guild_id)
        style = STYLE.get(self.colore.value.strip().lower())
        action = ACTIONS.get(self.azione.value.strip().lower())
        emoji = self.emoji.value.strip()
        text = self.testo.value.strip()
        if not style:
            return await interaction.response.send_message("Colore non valido (blu, grigio, verde, rosso).", ephemeral=True)
        if not action:
            return await interaction.response.send_message(
                "Azione non valida. Usa: claim, unclaim, close, reopen, transcript, add-member, "
                "remove-member, rename, delete, ping, messaggio.", ephemeral=True)
        if not valid_emoji(emoji):
            return await interaction.response.send_message("Emoji non valida.", ephemeral=True)
        if action == "message" and not text:
            return await interaction.response.send_message("Con l'azione *messaggio* devi scrivere il testo da inviare.", ephemeral=True)
        if self.btn is None:
            if len(td["buttons"]) >= 25:
                return await interaction.response.send_message("Massimo 25 pulsanti.", ephemeral=True)
            td["buttons"].append({"id": unique_id(slug(self.label.value), td["buttons"]),
                                  "label": self.label.value.strip(), "emoji": emoji, "style": style,
                                  "action": action, "text": text, "perm": "tutti" if action in ("message", "ping") else "staff",
                                  "enabled": True})
        else:
            self.btn.update(label=self.label.value.strip(), emoji=emoji, style=style, action=action, text=text)
        G["save"]()
        await interaction.response.send_message(
            "✅ Pulsante salvato. Vale per i **nuovi** ticket; quelli già aperti restano com'erano. "
            "Posizione, attivazione e permessi si cambiano con *Impostazioni pulsante*.", ephemeral=True)


class SettingsModal(discord.ui.Modal):
    """Posizione, attivazione e (permessi | ruolo richiesto) di una categoria o di un pulsante."""

    def __init__(self, kind, obj, items):
        super().__init__(title=f"Impostazioni {obj['label']}"[:45])
        self.kind, self.obj, self.items = kind, obj, items
        self.pos = discord.ui.TextInput(label="Posizione (numero, 1 = primo)", max_length=3,
                                        default=str(items.index(obj) + 1))
        self.on = discord.ui.TextInput(label="Attivo? (si / no)", max_length=3,
                                       default="si" if obj["enabled"] else "no")
        if kind == "btn":
            self.extra = discord.ui.TextInput(label="Permessi: tutti, aperto, staff, admin", max_length=10,
                                              default=obj["perm"])
        else:
            self.extra = discord.ui.TextInput(label="ID ruolo richiesto (opzionale)", required=False, max_length=25,
                                              default=str(obj["role_id"]) if obj.get("role_id") else None)
        for i in (self.pos, self.on, self.extra):
            self.add_item(i)

    async def on_submit(self, interaction: discord.Interaction):
        if not self.pos.value.strip().isdigit():
            return await interaction.response.send_message("La posizione deve essere un numero.", ephemeral=True)
        if self.kind == "btn":
            perm = PERMS.get(self.extra.value.strip().lower())
            if not perm:
                return await interaction.response.send_message(f"Permessi non validi. Usa: {PERM_HELP}.", ephemeral=True)
            self.obj["perm"] = perm
        else:
            raw = self.extra.value.strip()
            if raw and not raw.isdigit():
                return await interaction.response.send_message("L'ID del ruolo deve essere un numero.", ephemeral=True)
            self.obj["role_id"] = int(raw) if raw else None
        self.obj["enabled"] = yes(self.on.value)
        move_to(self.items, self.obj, int(self.pos.value))
        G["save"]()
        td = tdata(interaction.guild_id)
        if self.kind == "cat":
            await refresh_panel(interaction.guild, td)
        await interaction.response.send_message("✅ Impostazioni salvate.", ephemeral=True)


class PanelTextModal(discord.ui.Modal, title="Titolo e descrizione"):
    def __init__(self, td):
        super().__init__()
        self.titolo = discord.ui.TextInput(label="Titolo del pannello", max_length=100, default=td["panel"]["title"])
        self.desc = discord.ui.TextInput(label="Descrizione del pannello", style=discord.TextStyle.paragraph,
                                         max_length=1500, default=td["panel"]["description"])
        self.add_item(self.titolo)
        self.add_item(self.desc)

    async def on_submit(self, interaction):
        td = tdata(interaction.guild_id)
        td["panel"]["title"], td["panel"]["description"] = self.titolo.value, self.desc.value
        G["save"]()
        await refresh_panel(interaction.guild, td)
        await interaction.response.send_message("✅ Pannello aggiornato.", ephemeral=True)


# ------------------------------------------------------------------ EDITOR
def summary_embed(td, guild):
    e = discord.Embed(title="⚙️ Editor pannello ticket", color=0x5865F2)
    e.add_field(name=f"📌 {td['panel']['title']}"[:256], value=td["panel"]["description"][:1024], inline=False)
    e.add_field(name="Pulsanti esterni (categorie)", inline=False, value="\n".join(
        f"{i}. {c['emoji']} **{c['label']}** · {STYLE_IT[c['style']]}"
        f"{'' if c['enabled'] else ' · disattivata'}{' · ruolo' if c.get('role_id') else ''}"
        for i, c in enumerate(td["categories"], 1))[:1024] or "—")
    e.add_field(name="Pulsanti interni", inline=False, value="\n".join(
        f"{i}. {b['emoji']} **{b['label']}** · {ACTION_IT[b['action']]} · {b['perm']}"
        f"{'' if b['enabled'] else ' · disattivato'}"
        for i, b in enumerate(td["buttons"], 1))[:1024] or "—")
    cat = guild.get_channel(td["category_id"]) if td["category_id"] else None
    e.add_field(name="Categoria Discord", value=cat.name if cat else "Nessuna")
    e.add_field(name="Close", value="elimina il canale" if td["close_mode"] == "delete" else "blocca il canale (Reopen attivo)")
    e.set_footer(text="Permessi: " + PERM_HELP)
    return e


class OwnerView(discord.ui.View):
    def __init__(self, owner_id):
        super().__init__(timeout=300)
        self.owner_id = owner_id

    async def interaction_check(self, interaction):
        if interaction.user.id != self.owner_id:
            await interaction.response.send_message("Non è il tuo editor.", ephemeral=True)
            return False
        return True


class PickView(OwnerView):
    """Menu per scegliere quale categoria o pulsante modificare, rimuovere o configurare."""

    def __init__(self, owner_id, td, kind, mode):
        super().__init__(owner_id)
        self.kind, self.mode = kind, mode
        items = td["categories"] if kind == "cat" else td["buttons"]
        self.sel = discord.ui.Select(placeholder="Scegli", options=[
            discord.SelectOption(label=i["label"][:100], value=i["id"], emoji=i["emoji"] or None)
            for i in items[:25]])
        self.sel.callback = self.picked
        self.add_item(self.sel)

    async def picked(self, interaction: discord.Interaction):
        td = tdata(interaction.guild_id)
        items = td["categories"] if self.kind == "cat" else td["buttons"]
        obj = next((i for i in items if i["id"] == self.sel.values[0]), None)
        if not obj:
            return await interaction.response.send_message("Elemento non trovato.", ephemeral=True)
        if self.mode == "edit":
            return await interaction.response.send_modal(CategoryModal(obj) if self.kind == "cat" else ButtonModal(obj))
        if self.mode == "settings":
            return await interaction.response.send_modal(SettingsModal(self.kind, obj, items))
        if self.kind == "cat" and len(items) <= 1:
            return await interaction.response.send_message("Deve restare almeno una categoria.", ephemeral=True)
        items.remove(obj)
        G["save"]()
        if self.kind == "cat":
            await refresh_panel(interaction.guild, td)
        await interaction.response.edit_message(content=f"🗑️ **{obj['label']}** rimosso.", view=None)


class DiscordCatView(OwnerView):
    @discord.ui.select(cls=discord.ui.ChannelSelect, channel_types=[discord.ChannelType.category],
                       placeholder="Categoria Discord dove creare i ticket", min_values=1, max_values=1)
    async def pick(self, interaction, select):
        tdata(interaction.guild_id)["category_id"] = select.values[0].id
        G["save"]()
        await interaction.response.edit_message(
            content=f"✅ I ticket verranno creati in **{select.values[0].name}**.", view=None)


class EditorView(OwnerView):
    async def _pick(self, interaction, kind, mode, text):
        await interaction.response.send_message(
            text, view=PickView(self.owner_id, tdata(interaction.guild_id), kind, mode), ephemeral=True)

    # riga 0: pulsanti esterni (categorie)
    @discord.ui.button(label="Aggiungi categoria", emoji="➕", style=discord.ButtonStyle.success, row=0)
    async def cat_add(self, interaction, button):
        await interaction.response.send_modal(CategoryModal())

    @discord.ui.button(label="Modifica categoria", emoji="✏️", style=discord.ButtonStyle.primary, row=0)
    async def cat_edit(self, interaction, button):
        await self._pick(interaction, "cat", "edit", "Quale categoria vuoi modificare?")

    @discord.ui.button(label="Impostazioni categoria", emoji="⚙️", style=discord.ButtonStyle.secondary, row=0)
    async def cat_settings(self, interaction, button):
        await self._pick(interaction, "cat", "settings", "Posizione, attivazione e ruolo di quale categoria?")

    @discord.ui.button(label="Rimuovi categoria", emoji="🗑️", style=discord.ButtonStyle.danger, row=0)
    async def cat_remove(self, interaction, button):
        await self._pick(interaction, "cat", "remove", "Quale categoria vuoi rimuovere?")

    # riga 1: pannello e chiusura
    @discord.ui.button(label="Titolo e descrizione", emoji="📝", style=discord.ButtonStyle.secondary, row=1)
    async def text(self, interaction, button):
        await interaction.response.send_modal(PanelTextModal(tdata(interaction.guild_id)))

    @discord.ui.button(label="Categoria Discord", emoji="📁", style=discord.ButtonStyle.secondary, row=1)
    async def dcat(self, interaction, button):
        await interaction.response.send_message("Scegli la categoria Discord:", view=DiscordCatView(self.owner_id),
                                                ephemeral=True)

    @discord.ui.button(label="Modalità Close", emoji="🔒", style=discord.ButtonStyle.secondary, row=1)
    async def close_mode(self, interaction, button):
        td = tdata(interaction.guild_id)
        td["close_mode"] = "lock" if td["close_mode"] == "delete" else "delete"
        G["save"]()
        await interaction.response.send_message(
            "🔒 Close ora **elimina il canale**." if td["close_mode"] == "delete" else
            "🔒 Close ora **blocca il canale**: lo staff può usare Reopen o Delete.", ephemeral=True)

    # riga 2: pulsanti interni
    @discord.ui.button(label="Aggiungi pulsante", emoji="➕", style=discord.ButtonStyle.success, row=2)
    async def btn_add(self, interaction, button):
        await interaction.response.send_modal(ButtonModal())

    @discord.ui.button(label="Modifica pulsante", emoji="✏️", style=discord.ButtonStyle.primary, row=2)
    async def btn_edit(self, interaction, button):
        await self._pick(interaction, "btn", "edit", "Quale pulsante vuoi modificare?")

    @discord.ui.button(label="Impostazioni pulsante", emoji="⚙️", style=discord.ButtonStyle.secondary, row=2)
    async def btn_settings(self, interaction, button):
        await self._pick(interaction, "btn", "settings", "Posizione, attivazione e permessi di quale pulsante?")

    @discord.ui.button(label="Rimuovi pulsante", emoji="🗑️", style=discord.ButtonStyle.danger, row=2)
    async def btn_remove(self, interaction, button):
        await self._pick(interaction, "btn", "remove", "Quale pulsante vuoi rimuovere?")

    # riga 3
    @discord.ui.button(label="Riepilogo", emoji="📋", style=discord.ButtonStyle.secondary, row=3)
    async def recap(self, interaction, button):
        await interaction.response.send_message(embed=summary_embed(tdata(interaction.guild_id), interaction.guild),
                                                ephemeral=True)

    @discord.ui.button(label="Aggiorna pannello", emoji="🔄", style=discord.ButtonStyle.success, row=3)
    async def refresh(self, interaction, button):
        ok = await refresh_panel(interaction.guild, tdata(interaction.guild_id))
        await interaction.response.send_message(
            "✅ Pannello aggiornato." if ok else "⚠️ Aggiornamento rifiutato da Discord.", ephemeral=True)


# ------------------------------------------------------------------ COMANDI
async def _panel_create(ctx, canale: Optional[discord.TextChannel] = None):
    """Pubblica il pannello ticket"""
    td = tdata(ctx.guild.id)
    ch = canale or ctx.channel
    msg = await ch.send(embed=panel_embed(td), view=panel_view(td))
    td["panel"].update(channel_id=ch.id, message_id=msg.id)
    G["save"]()
    await ctx.send(f"✅ Pannello pubblicato in {ch.mention}. Usa `/ticketpaneledit` per modificarlo.", ephemeral=True)


async def _panel_edit(ctx):
    """Modifica categorie, descrizione e pulsanti del pannello ticket"""
    await ctx.send(embed=summary_embed(tdata(ctx.guild.id), ctx.guild), view=EditorView(ctx.author.id), ephemeral=True)


async def _panel_create_g(ctx, canale: Optional[discord.TextChannel] = None):
    """Pubblica il pannello ticket"""
    await _panel_create(ctx, canale)


async def _panel_edit_g(ctx):
    """Modifica categorie, descrizione e pulsanti del pannello ticket"""
    await _panel_edit(ctx)


async def _panel_delete(ctx):
    """Elimina il pannello pubblicato"""
    td = tdata(ctx.guild.id)
    p = td["panel"]
    ch = ctx.guild.get_channel(p["channel_id"]) if p["channel_id"] else None
    if ch and p["message_id"]:
        try:
            await (await ch.fetch_message(p["message_id"])).delete()
        except discord.HTTPException:
            pass
    p.update(channel_id=None, message_id=None)
    G["save"]()
    await ctx.send("🗑️ Pannello eliminato. Puoi ripubblicarlo con `/ticketpanelcreate`.")


def _reply(ctx):
    return lambda t, **kw: ctx.send(t, **kw)


async def _close(ctx):
    """Chiude il ticket corrente"""
    await do_close(ctx.channel, ctx.author, _reply(ctx))


async def _reopen(ctx):
    """Riapre il ticket corrente"""
    await do_reopen(ctx.channel, ctx.author, _reply(ctx))


async def _claim(ctx):
    """Prende in carico il ticket corrente"""
    await do_claim(ctx.channel, ctx.author, _reply(ctx))


async def _unclaim(ctx):
    """Rilascia il ticket corrente"""
    await do_unclaim(ctx.channel, ctx.author, _reply(ctx))


async def _add_member(ctx, member: discord.Member):
    """Aggiunge un membro al ticket"""
    await do_add_member(ctx.channel, member, _reply(ctx))


async def _remove_member(ctx, member: discord.Member):
    """Rimuove un membro dal ticket"""
    await do_remove_member(ctx.channel, member, _reply(ctx))


async def _rename(ctx, *, nome: str):
    """Cambia il nome del canale del ticket"""
    await do_rename(ctx.channel, nome, _reply(ctx))


async def _transcript(ctx):
    """Genera la trascrizione del ticket"""
    await do_transcript(ctx.channel, ctx.author, _reply(ctx))


async def _delete(ctx):
    """Elimina il canale del ticket"""
    await do_delete(ctx.channel, ctx.author, _reply(ctx))


def tickets_register(impl, gdata, save, bot, is_staff, is_admin):
    G.update(gdata=gdata, save=save, is_staff=is_staff, is_admin=is_admin)
    bot.add_dynamic_items(OpenButton, TicketButton)
    impl("ticketpanelcreate")(_panel_create)
    impl("ticketpaneledit")(_panel_edit)
    impl("ticket panel-create")(_panel_create_g)
    impl("ticket panel-edit")(_panel_edit_g)
    impl("ticket panel-delete")(_panel_delete)
    impl("ticket close")(_close)
    impl("ticket reopen")(_reopen)
    impl("ticket claim")(_claim)
    impl("ticket unclaim")(_unclaim)
    impl("ticket add-member")(_add_member)
    impl("ticket remove-member")(_remove_member)
    impl("ticket rename")(_rename)
    impl("ticket transcript")(_transcript)
    impl("ticket delete")(_delete)


# ------------------------------------------- DESCRIZIONI (dal documento)
_RAW = """
warn|Avvisa un membro.
warnings|Mostra gli avvertimenti di un membro.
warning-remove|Rimuove un avvertimento.
timeout|Limita temporaneamente le interazioni di un membro.
timeout-remove|Rimuove il timeout.
kick|Espelle un membro.
ban|Bandisce un membro.
unban|Rimuove un ban.
softban|Bandisce e sbanna per rimuovere i messaggi recenti.
purge|Elimina più messaggi.
slowmode|Imposta il rallentamento dei messaggi.
lock|Blocca la scrittura in un canale.
unlock|Riabilita la scrittura.
nickname|Modifica il soprannome.
history|Mostra la cronologia delle sanzioni.
cases|Elenca i casi di moderazione.
case|Mostra i dettagli di un caso.
reason|Modifica la motivazione di una sanzione.
note|Aggiunge una nota interna a un membro.
clear-notes|Cancella le note interne.
role create|Crea un ruolo.
role delete|Elimina un ruolo.
role edit|Modifica un ruolo.
role add|Assegna un ruolo.
role remove|Rimuove un ruolo.
role info|Mostra informazioni su un ruolo.
role list|Elenca i ruoli.
role clone|Duplica un ruolo.
role color|Cambia il colore.
role icon|Modifica l'icona, se supportata.
role hoist|Mostra il ruolo separatamente nella lista membri.
role mentionable|Abilita o disabilita le menzioni del ruolo.
role reorder|Cambia la posizione nella gerarchia.
role members|Elenca i membri con quel ruolo.
role temporary|Assegna un ruolo temporaneo.
role schedule|Programma l'assegnazione o la rimozione.
autorole set|Imposta un ruolo automatico.
autorole remove|Rimuove una regola autorole.
autorole list|Elenca le regole.
autorole configure|Configura l'assegnazione automatica.
autorole test|Prova il sistema.
autorole disable|Disattiva l'autorole.
member info|Mostra informazioni sul membro.
member avatar|Mostra l'avatar.
member banner|Mostra il banner, se disponibile.
member joined|Mostra la data d'ingresso.
member roles|Elenca i suoi ruoli.
member permissions|Mostra i suoi permessi effettivi.
member rename|Cambia il soprannome.
member notes|Mostra le note interne.
member activity|Mostra l'attività registrata dal bot.
channel create|Crea un canale.
channel delete|Elimina un canale.
channel edit|Modifica le impostazioni.
channel info|Mostra le informazioni.
channel clone|Duplica un canale.
channel rename|Cambia il nome.
channel topic|Modifica la descrizione.
channel slowmode|Imposta il rallentamento.
channel lock|Blocca la scrittura.
channel unlock|Sblocca la scrittura.
channel hide|Nasconde il canale a determinati membri.
channel unhide|Ripristina la visibilità.
channel permissions|Visualizza o modifica i permessi.
channel sync-permissions|Sincronizza i permessi con la categoria.
channel clear-permissions|Rimuove le autorizzazioni personalizzate.
channel move|Sposta il canale.
channel list|Elenca i canali.
category create|Crea una categoria.
category delete|Elimina una categoria.
category rename|Rinomina una categoria.
category move|Sposta una categoria.
thread create|Crea un thread.
thread archive|Archivia un thread.
thread unarchive|Riapre un thread archiviato.
thread lock|Blocca un thread.
thread unlock|Sblocca un thread.
thread list|Elenca i thread.
message send|Invia un messaggio tramite il bot.
message edit|Modifica un messaggio.
message delete|Elimina un messaggio.
message pin|Fissa un messaggio.
message unpin|Rimuove il messaggio dai fissati.
message quote|Crea una citazione.
message repeat|Fa ripetere un testo al bot.
embed create|Crea un embed.
embed edit|Modifica un embed.
embed preview|Mostra un'anteprima.
embed publish|Pubblica un embed.
embed template-save|Salva un modello.
embed template-load|Carica un modello.
embed template-delete|Elimina un modello.
embed list-templates|Elenca i modelli.
announcement send|Pubblica un annuncio.
announcement schedule|Programma un annuncio.
announcement cancel|Annulla un annuncio programmato.
announcement list|Elenca gli annunci programmati.
poll create|Crea un sondaggio.
poll close|Chiude un sondaggio.
poll results|Mostra i risultati.
poll list|Elenca i sondaggi.
reaction add|Aggiunge una reazione.
reaction remove|Rimuove una reazione.
reaction clear|Rimuove tutte le reazioni.
welcome setup|Configura il benvenuto.
welcome edit|Modifica il messaggio di benvenuto.
welcome preview|Mostra un'anteprima.
welcome test|Invia un test.
welcome disable|Disattiva il benvenuto.
goodbye setup|Configura il messaggio di uscita.
goodbye edit|Modifica il messaggio di uscita.
goodbye preview|Mostra un'anteprima.
goodbye disable|Disattiva il messaggio di uscita.
member-screening setup|Configura la verifica dei nuovi membri.
member-screening test|Prova la verifica.
member-screening disable|Disattiva la verifica gestita dal bot.
welcome-stats|Mostra statistiche sui benvenuti.
ticket panel-create|Crea il pannello pubblico dei ticket.
ticket panel-edit|Modifica titolo, descrizione, categorie, pulsanti esterni e interni.
ticket panel-delete|Elimina un pannello.
ticket create|Apre un ticket.
ticket settings|Configura il sistema ticket.
ticket category-set|Imposta la categoria Discord di destinazione.
ticket staff-set|Imposta il ruolo dello staff autorizzato.
ticket cooldown|Imposta l'intervallo tra aperture di ticket.
ticket close|Chiude un ticket.
ticket reopen|Riapre un ticket.
ticket claim|Prende in carico un ticket.
ticket unclaim|Rimuove l'assegnazione dello staff.
ticket add-member|Aggiunge un membro al ticket.
ticket remove-member|Rimuove un membro dal ticket.
ticket rename|Cambia il nome del canale.
ticket transfer|Trasferisce il ticket a un altro membro dello staff.
ticket priority|Modifica la priorità.
ticket note|Aggiunge una nota interna.
ticket transcript|Genera la trascrizione.
ticket delete|Elimina il canale del ticket dopo le verifiche.
ticket list|Elenca i ticket.
ticket stats|Mostra le statistiche.
ticket feedback|Gestisce le valutazioni.
ticket close-reason|Registra il motivo di chiusura.
application panel-create|Crea il pannello candidature.
application panel-edit|Modifica il pannello.
application submit|Invia una candidatura.
application review|Esamina una candidatura.
application approve|Approva una candidatura.
application reject|Rifiuta una candidatura.
application questions|Modifica le domande.
application set-reviewers|Imposta i revisori.
application list|Elenca le candidature.
application stats|Mostra le statistiche.
report create|Invia una segnalazione.
report view|Mostra una segnalazione.
report list|Elenca le segnalazioni.
report resolve|Segna una segnalazione come risolta.
report dismiss|Archivia una segnalazione.
report add-note|Aggiunge una nota.
report settings|Configura le segnalazioni.
logs setup|Configura i log.
logs disable|Disattiva una categoria di log.
logs test|Verifica i log.
logs recent|Mostra gli eventi recenti.
logs search|Cerca negli eventi.
logs member|Filtra i log per membro.
logs messages|Mostra i log dei messaggi.
logs roles|Mostra le modifiche ai ruoli.
logs channels|Mostra le modifiche ai canali.
logs moderation|Mostra le azioni di moderazione.
logs tickets|Mostra gli eventi dei ticket.
logs voice|Mostra gli eventi vocali.
logs joins|Mostra gli ingressi.
logs leaves|Mostra le uscite.
logs automod|Mostra le azioni di automoderazione.
logs bot-errors|Mostra gli errori del bot.
audit latest|Mostra gli eventi più recenti.
audit search|Cerca nell'audit.
audit member|Filtra per membro.
audit export|Esporta i registri autorizzati.
security status|Mostra lo stato della sicurezza.
security setup|Configura le protezioni.
security test|Prova le protezioni.
security lockdown|Attiva il blocco d'emergenza.
security unlock|Disattiva il blocco d'emergenza.
security trusted-roles|Gestisce i ruoli fidati.
security trusted-users|Gestisce gli utenti fidati.
security allowlist-add|Aggiunge alla lista consentita.
security allowlist-remove|Rimuove dalla lista consentita.
security allowlist|Mostra la lista consentita.
antispam enable|Attiva l'antispam.
antispam disable|Disattiva l'antispam.
antispam settings|Configura l'antispam.
antispam test|Prova l'antispam.
antilink enable|Attiva il filtro link.
antilink disable|Disattiva il filtro link.
antilink allow-domain|Consente un dominio.
antilink remove-domain|Rimuove un dominio consentito.
antilink settings|Configura il filtro link.
automod rules|Elenca le regole.
automod rule-create|Crea una regola.
automod rule-edit|Modifica una regola.
automod rule-delete|Elimina una regola.
automod enable|Attiva l'automoderazione.
automod disable|Disattiva l'automoderazione.
raid-protection enable|Attiva la protezione raid.
raid-protection disable|Disattiva la protezione raid.
raid-protection settings|Configura la protezione raid.
raid-protection status|Mostra lo stato della protezione.
mention-limit set|Imposta il limite di menzioni.
mention-limit reset|Ripristina il limite.
filter add|Aggiunge una regola.
filter remove|Rimuove una regola.
filter list|Elenca le regole.
filter test|Prova una regola.
filter enable|Attiva il filtro.
filter disable|Disattiva il filtro.
badwords add|Aggiunge una parola vietata.
badwords remove|Rimuove una parola vietata.
badwords list|Elenca le parole vietate.
badwords settings|Configura il filtro.
caps-filter enable|Attiva il filtro maiuscole.
caps-filter disable|Disattiva il filtro maiuscole.
invite-filter enable|Attiva il filtro inviti.
invite-filter disable|Disattiva il filtro inviti.
emoji-limit set|Imposta il limite emoji.
emoji-limit disable|Disattiva il limite emoji.
duplicate-filter enable|Attiva il filtro messaggi duplicati.
duplicate-filter disable|Disattiva il filtro duplicati.
link-filter settings|Configura le regole per i link.
attachment-filter settings|Configura le regole per gli allegati.
automation create|Crea un'automazione.
automation edit|Modifica un'automazione.
automation delete|Elimina un'automazione.
automation enable|Attiva un'automazione.
automation disable|Disattiva un'automazione.
automation list|Elenca le automazioni.
automation test|Prova un'automazione.
automation history|Mostra lo storico.
schedule message|Programma un messaggio.
schedule edit|Modifica una programmazione.
schedule cancel|Annulla una programmazione.
schedule list|Elenca le programmazioni.
schedule timezone|Imposta il fuso orario.
reminder create|Crea un promemoria.
reminder edit|Modifica un promemoria.
reminder delete|Elimina un promemoria.
reminder list|Elenca i promemoria.
reminder test|Prova il sistema.
trigger create|Crea un'attivazione automatica.
trigger edit|Modifica un'attivazione.
trigger delete|Elimina un'attivazione.
trigger list|Elenca le attivazioni.
trigger test|Prova un'attivazione.
activity stats|Mostra statistiche generali.
activity member|Mostra l'attività di un membro.
activity leaderboard|Mostra la classifica.
activity reset|Reimposta le statistiche.
activity settings|Configura il tracciamento.
leveling enable|Attiva i livelli.
leveling disable|Disattiva i livelli.
leveling settings|Configura gli XP.
leveling rewards|Elenca i premi.
leveling reward-add|Aggiunge un premio.
leveling reward-remove|Rimuove un premio.
leveling leaderboard|Mostra la classifica XP.
leveling profile|Mostra il profilo XP.
leveling set-xp|Imposta gli XP.
leveling reset-xp|Reimposta gli XP.
stats overview|Panoramica del server.
stats members|Statistiche sui membri.
stats channels|Statistiche sui canali.
stats roles|Statistiche sui ruoli.
stats activity|Statistiche di attività.
stats messages|Statistiche dei messaggi.
stats joins|Statistiche sugli ingressi.
stats leaves|Statistiche sulle uscite.
stats growth|Crescita del server.
stats history|Storico delle statistiche.
stats export|Esporta i dati statistici.
counter create|Crea un contatore.
counter edit|Modifica un contatore.
counter delete|Elimina un contatore.
counter list|Elenca i contatori.
counter refresh|Aggiorna un contatore.
milestone create|Crea un traguardo.
milestone edit|Modifica un traguardo.
milestone delete|Elimina un traguardo.
milestone list|Elenca i traguardi.
giveaway create|Crea un giveaway.
giveaway edit|Modifica un giveaway.
giveaway end|Termina un giveaway.
giveaway reroll|Estrae un nuovo vincitore.
giveaway cancel|Annulla un giveaway.
giveaway list|Elenca i giveaway.
giveaway entrants|Mostra i partecipanti.
giveaway settings|Configura i giveaway.
event create|Crea un evento.
event edit|Modifica un evento.
event start|Avvia o contrassegna l'inizio.
event end|Termina un evento.
event cancel|Annulla un evento.
event list|Elenca gli eventi.
event announce|Annuncia un evento.
event reminder|Configura i promemoria.
event participants|Elenca i partecipanti.
event results|Mostra i risultati.
suggestion panel-create|Crea il pannello suggerimenti.
suggestion panel-edit|Modifica il pannello.
suggestion create|Invia un suggerimento.
suggestion approve|Approva un suggerimento.
suggestion reject|Rifiuta un suggerimento.
suggestion implement|Segna un suggerimento come realizzato.
suggestion reconsider|Riapre la valutazione.
suggestion list|Elenca i suggerimenti.
suggestion view|Mostra un suggerimento.
suggestion settings|Configura i suggerimenti.
suggestion leaderboard|Classifica dei suggerimenti.
vote create|Crea una votazione.
vote close|Chiude una votazione.
vote results|Mostra i risultati.
vote list|Elenca le votazioni.
dm send|Invia un DM tramite il bot.
dm template-create|Crea un modello DM.
dm template-edit|Modifica un modello.
dm template-delete|Elimina un modello.
dm preview|Mostra l'anteprima.
dm history|Mostra la cronologia dei DM registrati.
notification create|Crea una notifica.
notification edit|Modifica una notifica.
notification delete|Elimina una notifica.
notification list|Elenca le notifiche.
notification test|Prova una notifica.
notification subscribe|Si iscrive alle notifiche.
notification unsubscribe|Annulla l'iscrizione.
notification preferences|Modifica le preferenze.
backup create|Crea un backup dei dati supportati.
backup list|Elenca i backup.
backup info|Mostra i dettagli.
backup export|Esporta un backup.
backup restore-preview|Mostra un'anteprima del ripristino.
backup restore|Ripristina un backup.
backup delete|Elimina un backup.
backup schedule|Programma backup automatici.
backup schedule-disable|Disattiva la programmazione.
backup settings|Configura i backup.
backup history|Mostra lo storico.
backup test|Verifica il sistema.
dashboard link|Mostra il link della dashboard.
dashboard status|Mostra lo stato della dashboard.
dashboard settings|Configura la dashboard.
panel create|Crea un pannello interattivo.
panel edit|Modifica un pannello.
panel publish|Pubblica un pannello.
panel delete|Elimina un pannello.
panel list|Elenca i pannelli.
panel preview|Mostra l'anteprima.
panel clone|Duplica un pannello.
panel button-add|Aggiunge un pulsante.
panel button-edit|Modifica un pulsante.
panel button-remove|Rimuove un pulsante.
panel select-add|Aggiunge un menu a selezione.
panel select-edit|Modifica un menu a selezione.
panel select-remove|Rimuove un menu a selezione.
access show|Mostra le regole di accesso.
access grant|Concede un'autorizzazione interna.
access revoke|Revoca un'autorizzazione.
access set-role|Configura l'accesso per ruolo.
access set-user|Configura l'accesso per utente.
access set-channel|Configura l'accesso per canale.
access reset|Ripristina una regola.
access test|Verifica l'accesso a una funzione.
staff list|Elenca lo staff.
staff add|Aggiunge un membro allo staff interno.
staff remove|Rimuove un membro dallo staff interno.
staff permissions|Gestisce i permessi interni dello staff.
staff audit|Mostra le attività dello staff.
utility poll|Crea un sondaggio rapido.
utility choose|Sceglie tra diverse opzioni.
utility random-number|Genera un numero casuale.
utility countdown|Crea un conto alla rovescia.
utility timestamp|Genera un timestamp Discord.
utility server-time|Mostra l'ora configurata.
utility reminder|Crea un promemoria rapido.
utility user-id|Mostra l'ID utente.
utility channel-id|Mostra l'ID canale.
utility role-id|Mostra l'ID ruolo.
utility permissions-check|Controlla i permessi di un utente.
utility bot-permissions|Mostra i permessi del bot.
utility invite-info|Mostra informazioni su un invito.
utility message-info|Mostra informazioni su un messaggio.
utility avatar|Mostra un avatar.
utility banner|Mostra il banner, se disponibile.
config view|Visualizza la configurazione.
config edit|Modifica la configurazione.
config reset|Ripristina le impostazioni.
config validate|Controlla la validità della configurazione.
config export|Esporta le impostazioni.
config import|Importa le impostazioni.
config modules|Elenca i moduli.
config module-enable|Attiva un modulo.
config module-disable|Disattiva un modulo.
config command-enable|Abilita un comando.
config command-disable|Disabilita un comando.
config cooldown-set|Imposta un cooldown.
config cooldown-reset|Ripristina il cooldown.
config locale|Imposta la lingua.
config timezone|Imposta il fuso orario.
config maintenance|Gestisce la manutenzione prevista dalla configurazione.
server info|Mostra informazioni sul server.
server settings|Mostra o gestisce le impostazioni.
server setup|Avvia la configurazione iniziale.
server language|Imposta la lingua del server.
server prefix|Mostra o modifica il prefisso, se supportato.
server export-settings|Esporta le impostazioni.
server import-settings|Importa le impostazioni.
server reset-settings|Ripristina le impostazioni del bot.
permissions view|Mostra le autorizzazioni configurate.
permissions set|Imposta un'autorizzazione.
permissions remove|Rimuove un'autorizzazione.
diagnostics run|Esegue una diagnostica generale.
diagnostics permissions|Controlla i permessi del bot.
diagnostics channels|Verifica l'accesso ai canali.
diagnostics roles|Controlla ruoli e gerarchia.
diagnostics database|Verifica il database.
diagnostics latency|Controlla la latenza.
diagnostics integrations|Verifica le integrazioni.
diagnostics report|Genera un rapporto diagnostico.
health|Mostra lo stato dei componenti monitorati.
uptime|Mostra da quanto tempo il bot è attivo.
latency|Mostra la latenza.
version|Mostra la versione.
status|Mostra lo stato generale.
errors recent|Elenca gli errori recenti.
errors details|Mostra i dettagli di un errore.
errors resolve|Segna un errore come risolto nel sistema di tracciamento.
bot servers|Elenca i server in cui è presente il bot.
bot server-info|Mostra informazioni su un server.
bot server-config|Gestisce la configurazione del bot per un server.
bot maintenance-on|Attiva la manutenzione globale.
bot maintenance-off|Disattiva la manutenzione globale.
bot announce|Invia un annuncio globale.
bot reload-config|Ricarica la configurazione globale.
bot sync-commands|Sincronizza i comandi Discord.
bot disable-server|Disabilita il bot in un server.
bot enable-server|Riabilita il bot in un server.
bot database-status|Mostra lo stato del database globale.
bot backup-status|Mostra lo stato dei backup globali.
bot error-report|Mostra i rapporti sugli errori.
bot shutdown|Arresta il bot globalmente.
bot info|Mostra informazioni pubbliche sul bot.
bot status|Mostra lo stato pubblico del bot.
dev test-command|Prova un comando.
dev test-embed|Prova gli embed.
dev test-permissions|Verifica i controlli dei permessi.
dev test-logs|Prova i log.
dev test-ticket|Prova il sistema ticket.
dev test-automation|Prova le automazioni.
dev database-check|Controlla il database.
dev database-migrations|Controlla le migrazioni del database.
dev cache-status|Mostra lo stato della cache.
dev queue-status|Mostra lo stato delle code.
dev job-list|Elenca i lavori in coda o in esecuzione.
dev job-retry|Riprova un lavoro fallito.
dev feature-flags|Mostra le funzionalità controllate da flag.
dev feature-enable|Attiva una funzionalità.
dev feature-disable|Disattiva una funzionalità.
dev diagnostics|Esegue controlli tecnici approfonditi.
help|Mostra i comandi accessibili all'utente (opzionale: categoria).
"""

DESC = {}
for _line in _RAW.strip().splitlines():
    _k, _v = _line.split("|", 1)
    DESC[_k.strip()] = _v.strip()


# ------------------------------------------------- SPECIFICA DEI COMANDI
# formato: gruppo|livello|permesso_discord|sotto1,sotto2:LIVELLO:permesso,...
# gruppo "-" = comandi di primo livello. Nei sotto-token: livello/permesso
# vuoti = ereditati; "~" come permesso = nessun permesso Discord.
# Livelli: P pubblico, S staff, A admin, O owner bot, D dev.
SPEC = """
## 🛡️ Moderazione
-|S||warn,warnings,warning-remove,history,cases,case,reason,note,clear-notes
-|S|moderate_members|timeout,timeout-remove
-|S|kick_members|kick
-|S|ban_members|ban,unban,softban
-|S|manage_messages|purge
-|S|manage_channels|slowmode,lock,unlock
-|S|manage_nicknames|nickname
## 👥 Ruoli e membri
role|P|manage_roles|create,delete,edit,add,remove,clone,color,icon,hoist,mentionable,reorder,temporary:S,schedule:S,info:P:~,list:P:~,members:P:~
autorole|P|manage_roles|set,remove,configure,disable,list:S:~,test:S
member|P||info,avatar,banner,joined,roles,activity,permissions:S,rename:P:manage_nicknames,notes:S
## 🗂️ Canali, categorie e thread
channel|P|manage_channels|create,delete,edit,clone,rename,topic,move,slowmode,lock,unlock,hide,unhide,permissions,sync-permissions,clear-permissions,info:P:~,list:P:~
category|P|manage_channels|create,delete,rename,move
thread|P|manage_threads|create:P:create_public_threads,archive,unarchive,lock,unlock,list:P:~
## 💬 Messaggi, embed e sondaggi
message|P||send,quote,repeat,edit:P:manage_messages,delete:P:manage_messages,pin:P:manage_messages,unpin:P:manage_messages
embed|P||create,edit,preview,template-save,template-load,template-delete,list-templates,publish:S
announcement|S||send,schedule,cancel,list:P
poll|P||create,close,results,list
reaction|P|manage_messages|add,remove,clear
## 👋 Benvenuto e verifica
welcome|A|manage_guild|setup,edit,disable,preview:S:~,test:S:~
goodbye|A|manage_guild|setup,edit,disable,preview:P:~
member-screening|P|manage_guild|setup,test,disable
-|P||welcome-stats
## 🎫 Ticket
ticket|S||panel-create:A:manage_channels,panel-edit:A:manage_channels,panel-delete:A:manage_channels,settings:A:manage_channels,category-set:A:manage_channels,staff-set:A:manage_channels,cooldown:A:manage_channels,create:P,close,reopen,claim,unclaim,add-member,remove-member,rename,transfer,priority,note,close-reason,transcript,delete:A:manage_channels,list,stats,feedback:P
-|A|manage_channels|ticketpanelcreate,ticketpaneledit
## 📝 Candidature e segnalazioni
application|S||panel-create:A:manage_guild,panel-edit:A:manage_guild,questions:A:manage_guild,set-reviewers:A:manage_guild,submit:P,review,approve,reject,list,stats
report|S||create:P,view,list,resolve,dismiss,add-note,settings:A
## 📋 Log e audit
logs|S||setup:A:manage_guild,disable:A:manage_guild,test:A:manage_guild,recent,search,member,messages,roles,channels,moderation,tickets,voice,joins,leaves,automod,bot-errors
audit|S|view_audit_log|latest,search,member,export
## 🔐 Sicurezza e automoderazione
security|A||status:P,setup,test,lockdown,unlock,trusted-roles,trusted-users,allowlist-add,allowlist-remove,allowlist
antispam|A||enable,disable,settings,test
antilink|A||enable,disable,allow-domain,remove-domain,settings
automod|A||rules,rule-create,rule-edit,rule-delete,enable,disable
raid-protection|A||enable,disable,settings,status:P
mention-limit|A||set,reset
## 🧹 Filtri
filter|A||add,remove,enable,disable,list:P,test:P
badwords|A||add,remove,settings,list:P
caps-filter|A||enable,disable
invite-filter|A||enable,disable
emoji-limit|A||set,disable
duplicate-filter|A||enable,disable
link-filter|A||settings
attachment-filter|A||settings
## ⚙️ Automazioni e programmazioni
automation|A||create,edit,delete,enable,disable,list:S,test:S,history:S
schedule|A||message,edit,cancel,timezone,list:P
reminder|P||create,edit,delete,list,test
trigger|A||create,edit,delete,test,list:P
## 📈 Attività e livelli
activity|A||reset,settings,stats:P,member:P,leaderboard:P
leveling|A||enable,disable,settings,rewards,reward-add,reward-remove,set-xp,reset-xp,leaderboard:P,profile:P
## 📊 Statistiche e contatori
stats|P||overview,members,channels,roles,activity,messages,joins,leaves,growth,history,export:S
counter|S||create,edit,delete,list,refresh
milestone|S||create,edit,delete,list
## 🎉 Giveaway ed eventi
giveaway|S||create,edit,end,reroll,cancel,settings:A,list:P,entrants
event|S||create,edit,start,end,cancel,announce,reminder,list:P,participants:P,results:P
## 💡 Suggerimenti e votazioni
suggestion|S||panel-create,panel-edit,approve,reject,implement,reconsider,settings,create:P,view:P,list:P,leaderboard:P
vote|P||create,results,list,close
## ✉️ DM e notifiche
dm|A||send,template-create:S,template-edit:S,template-delete:S,preview:S,history:S
notification|S||create,edit,delete,test,list:P,subscribe:P,unsubscribe:P,preferences:P
## 💾 Backup
backup|A||create,export,restore-preview,restore,delete,schedule,schedule-disable,settings,test,list:S,info:S,history:S
## 🖥️ Dashboard e pannelli interattivi
dashboard|P||link,status,settings:A
panel|A||create,edit,publish,delete,clone,button-add,button-edit,button-remove,select-add,select-edit,select-remove,list:P,preview:P
## 🔑 Permessi e staff
access|A||grant,revoke,set-role,set-user,set-channel,reset,show:S,test:S
staff|A||add,remove,permissions,list:S,audit:S
## 🧰 Utilità
utility|P||poll,choose,random-number,countdown,timestamp,server-time,reminder,user-id,channel-id,role-id,invite-info,avatar,banner,permissions-check:S,bot-permissions:S,message-info:S
## ⚙️ Configurazione
server|A||setup,language,prefix,export-settings,import-settings,reset-settings,info:P,settings:P
config|A||edit,reset,export,import,modules,module-enable,module-disable,command-enable,command-disable,cooldown-set,cooldown-reset,locale,timezone,maintenance,view:P,validate:P
permissions|A||set,remove,view:S
## 🩺 Diagnostica e stato
-|P||health,uptime,latency,version,status
diagnostics|S||run,permissions,channels,roles,database,latency,integrations,report
errors|S||recent,details,resolve
## 👑 Comandi globali del bot
bot|O||servers,server-info,server-config,maintenance-on,maintenance-off,announce,reload-config,sync-commands,disable-server,enable-server,database-status,backup-status,error-report,shutdown,info:P,status:P
## 🧑‍💻 Comandi sviluppatore
dev|D||test-command,test-embed,test-permissions,test-logs,test-ticket,test-automation,database-check,database-migrations,cache-status,queue-status,job-list,job-retry,feature-flags,feature-enable,feature-disable,diagnostics
## 📚 Aiuto
-|P||help
"""


def make_stub(full):
    async def stub(ctx, args: Optional[str] = None):
        await ctx.send(f"⚙️ `/{full}` è registrato e i permessi funzionano, "
                       f"ma la logica non è ancora implementata.")
    return stub


def make_group_cb(name):
    async def cb(ctx):
        subs = ", ".join(f"`{c.name}`" for c in sorted(ctx.command.commands, key=lambda c: c.name))
        await ctx.send(f"**/{name}** — sottocomandi: {subs}")
    return cb


def build_commands():
    cat = ""
    for line in SPEC.strip().splitlines():
        if line.startswith("##"):
            cat = line[2:].strip()
            continue
        grp, lvl, perm, subs = line.split("|")
        group = None
        if grp != "-":
            group = commands.HybridGroup(make_group_cb(grp), name=grp,
                                         description=f"Gruppo {grp}"[:100],
                                         invoke_without_command=True)
        for tok in subs.split(","):
            parts = tok.split(":")
            name = parts[0]
            level = parts[1] if len(parts) > 1 and parts[1] else lvl
            p = parts[2] if len(parts) > 2 and parts[2] else perm
            p = "" if p == "~" else p
            full = f"{grp} {name}" if group else name
            fn = IMPL.get(full) or make_stub(full)
            desc = (DESC.get(full) or fn.__doc__ or f"Comando {full}").strip().splitlines()[0][:100]
            extras = {"level": level, "perm": p}
            if group:
                group.command(name=name, description=desc, extras=extras)(fn)
            else:
                cmd = commands.HybridCommand(fn, name=name, description=desc, extras=extras)
                if full == "help":
                    cmd.autocomplete("categoria")(help_autocomplete)
                bot.add_command(cmd)
            REG.append({"cat": cat, "full": full, "level": level, "perm": p})
        if group:
            bot.add_command(group)


# ------------------------------------------------------------ ERRORI / AVVIO
@bot.event
async def on_ready():
    print(f"✅ Online come {bot.user} — {len(REG)} comandi registrati")
    await bot.change_presence(activity=discord.Game(".help | /help"))


@bot.event
async def on_command_error(ctx, error):
    error = getattr(error, "original", error)
    if isinstance(error, commands.CommandNotFound):
        return
    if isinstance(error, commands.CheckFailure):
        msg = str(error) or "Non hai i permessi per questo comando."
    elif isinstance(error, commands.MissingRequiredArgument):
        msg = f"Manca l'argomento `{error.param.name}`. Uso: `.{ctx.command.qualified_name} {ctx.command.signature}`"
    elif isinstance(error, (commands.BadArgument, commands.BadUnionArgument)):
        msg = "Argomento non valido."
    elif isinstance(error, discord.Forbidden):
        msg = "❌ Al bot mancano permessi o il suo ruolo è troppo basso nella gerarchia."
    else:
        traceback.print_exception(type(error), error, error.__traceback__)
        msg = "⚠️ Si è verificato un errore."
    try:
        await ctx.send(msg, ephemeral=True)
    except discord.HTTPException:
        pass


if __name__ == "__main__":
    if not TOKEN:
        raise SystemExit("Imposta DISCORD_TOKEN nel file .env")
    bot.run(TOKEN)
