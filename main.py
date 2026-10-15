"""
Server Manager Pro - bot Discord (discord.py 2.4+), tutto in un unico file: main.py
Prefissi: "." e "/" (comandi ibridi). Aiuto: .help  /help

Installazione:  pip install -U discord.py python-dotenv
Config (.env):  DISCORD_TOKEN=...   OWNER_IDS=123,456   DEV_IDS=789
Nel Developer Portal attiva: SERVER MEMBERS INTENT e MESSAGE CONTENT INTENT.
Avvio:          python main.py
"""
import asyncio
import collections
import copy
import io
import json
import os
import platform
import random
import re
import sys
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
        ext_register(self)
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
    level, perm = await extension_guard(ctx, level, perm)
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
async def _slowmode(ctx, secondi: int, canale: Optional[discord.TextChannel] = None):
    """Imposta la modalità lenta del canale"""
    c = canale or ctx.channel
    await c.edit(slowmode_delay=max(0, min(secondi, 21600)))
    await ctx.send(f"🐢 Slowmode di {c.mention}: {max(0, min(secondi, 21600))}s.")


@impl("lock")
async def _lock(ctx, canale: Optional[discord.TextChannel] = None):
    """Blocca il canale"""
    c = canale or ctx.channel
    await c.set_permissions(ctx.guild.default_role, send_messages=False)
    await ctx.send(f"🔒 {c.mention} bloccato.")


@impl("unlock")
async def _unlock(ctx, canale: Optional[discord.TextChannel] = None):
    """Sblocca il canale"""
    c = canale or ctx.channel
    await c.set_permissions(ctx.guild.default_role, send_messages=None)
    await ctx.send(f"🔓 {c.mention} sbloccato.")


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


def panels_of(gid):
    """Tutti i pannelli ticket del server: {"p1": configurazione, "p2": ...}."""
    gd = G["gdata"](gid)
    if "tickets" in gd:  # migrazione: il vecchio pannello unico diventa "p1"
        old = gd.pop("tickets")
        if "ping_roles" in old:
            gd["ping_roles"] = old.pop("ping_roles")
        old["pid"], old["name"] = "p1", old.get("name", "Pannello 1")
        gd.setdefault("ticket_panels", {})["p1"] = old
        G["save"]()
    return gd.setdefault("ticket_panels", {})


def new_panel(gid, name=None):
    ps = panels_of(gid)
    n = 1
    while f"p{n}" in ps:
        n += 1
    td = default_td()
    td["pid"], td["name"] = f"p{n}", name or f"Pannello {n}"
    ps[td["pid"]] = td
    G["save"]()
    return td


def find_ticket(guild_id, channel_id):
    """Trova (pannello, ticket) a cui appartiene un canale; (None, None) se non è un ticket."""
    for td in panels_of(guild_id).values():
        tk = td["open"].get(str(channel_id))
        if tk:
            return td, tk
    return None, None


def tdata(guild_id, pid=None):
    ps = panels_of(guild_id)
    if not ps:
        new_panel(guild_id)
    if pid not in ps:
        pid = min(ps, key=lambda k: int(k[1:]))
    td = ps[pid]
    td.setdefault("pid", pid)
    td.setdefault("name", f"Pannello {pid[1:]}")
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


def ping_roles_of(guild):
    """Ruoli menzionati (e aggiunti) nei ticket: quelli impostati con /ping, altrimenti i ruoli staff."""
    gd = G["gdata"](guild.id)
    panels_of(guild.id)
    if "ping_roles" not in gd:
        return staff_roles_of(guild)
    return [r for r in (guild.get_role(i) for i in gd["ping_roles"]) if r]


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
class OpenButton(discord.ui.DynamicItem[discord.ui.Button], template=r"tk:open:(?:(?P<pid>p\d+):)?(?P<cat>[a-z0-9_-]+)"):
    def __init__(self, pid, cat, label=None, emoji=None, style=discord.ButtonStyle.primary):
        super().__init__(discord.ui.Button(label=label, emoji=emoji or None, style=style,
                                           custom_id=f"tk:open:{pid}:{cat}"))
        self.pid, self.cat = pid, cat

    @classmethod
    async def from_custom_id(cls, interaction, item, match, /):
        return cls(match["pid"] or "p1", match["cat"], item.label, item.emoji, item.style)

    async def callback(self, interaction: discord.Interaction):
        guild, user = interaction.guild, interaction.user
        td = tdata(guild.id, self.pid)
        cat = next((c for c in td["categories"] if c["id"] == self.cat), None)
        if not cat or not cat["enabled"]:
            return await interaction.response.send_message("Questa categoria non è disponibile.", ephemeral=True)
        if cat.get("role_id") and not any(r.id == cat["role_id"] for r in user.roles):
            return await interaction.response.send_message("Questa categoria è riservata a un ruolo specifico.", ephemeral=True)
        for ptd in panels_of(guild.id).values():  # un solo ticket aperto per utente, su tutti i pannelli
            for cid, tk in list(ptd["open"].items()):
                if guild.get_channel(int(cid)) is None:
                    ptd["open"].pop(cid)
                elif tk["owner"] == user.id:
                    return await interaction.response.send_message(f"Hai già un ticket aperto: <#{cid}>", ephemeral=True)
        cdm = td.get("cooldown", 0)
        last = td.setdefault("last_open", {}).get(str(user.id), 0)
        if cdm and time.time() - last < cdm * 60 and not G["is_staff"](user):
            return await interaction.response.send_message(
                f"⏳ Aspetta ancora {int(cdm * 60 - (time.time() - last))}s prima di aprire un altro ticket.", ephemeral=True)
        td["last_open"][str(user.id)] = time.time()
        await interaction.response.defer(ephemeral=True)

        base = discord.PermissionOverwrite(view_channel=True, send_messages=True,
                                           attach_files=True, read_message_history=True)
        ow = {
            guild.default_role: discord.PermissionOverwrite(view_channel=False),
            guild.me: discord.PermissionOverwrite(view_channel=True, send_messages=True,
                                                  manage_channels=True, read_message_history=True),
            user: base,
        }
        staff = staff_roles_of(guild)
        pr = ping_roles_of(guild)
        for r in staff + [x for x in pr if x not in staff]:
            ow[r] = base
        parent = guild.get_channel(td["category_id"]) if td["category_id"] else None
        if not isinstance(parent, discord.CategoryChannel):
            parent = None
        n_ticket = nid(guild.id, "ticket")
        try:
            ch = await guild.create_text_channel(
                name=f"{cat['id']}-{n_ticket:04d}"[:100], category=parent, overwrites=ow,
                topic=f"Ticket di {user} ({user.id}) · {cat['label']}")
        except discord.Forbidden:
            return await interaction.followup.send("❌ Non ho il permesso di creare canali.", ephemeral=True)
        td["open"][str(ch.id)] = {"owner": user.id, "cat": cat["id"], "claimed_by": None, "closed": False, "ts": int(time.time())}
        G["save"]()
        log_event(guild, "tickets", f"Ticket aperto da {user} ({cat['label']}) → #{ch.name}", user.id)

        e = discord.Embed(title=f"{cat['emoji']} {cat['label']}".strip(),
                          description=cat.get("message") or "Uno staff ti risponderà a breve.", color=0x5865F2)
        e.add_field(name="Aperto da", value=user.mention)
        pings = " ".join(r.mention for r in pr)
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
        td, _tk0 = find_ticket(interaction.guild_id, interaction.channel.id)
        if td is None:
            return await interaction.response.send_message("Questo canale non è un ticket.", ephemeral=True)
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
            roles = ping_roles_of(interaction.guild)
            return await interaction.response.send_message(
                f"🔔 {user.mention} chiama lo staff {' '.join(r.mention for r in roles)}".strip(),
                allowed_mentions=discord.AllowedMentions(users=True, roles=True))
        await interaction.response.send_message(
            (b.get("text") or "…").replace("{user}", user.mention),
            allowed_mentions=discord.AllowedMentions(users=True))


DISPLAY_IT = {"auto": "automatico (1 categoria = pulsante, 2 o più = menu a tendina)",
              "select": "sempre menu a tendina", "buttons": "sempre pulsanti"}


def _build_cat_select(td, cats):
    opts = [discord.SelectOption(label=c["label"][:100], value=c["id"], emoji=c["emoji"] or None) for c in cats[:25]]
    return discord.ui.Select(custom_id=f"tk:sel:{td['pid']}", placeholder="Scegli una categoria per aprire un ticket",
                             min_values=1, max_values=1, options=opts)


class PanelCategorySelect(discord.ui.DynamicItem[discord.ui.Select], template=r"tk:sel:(?P<pid>p\d+)"):
    """Menu a tendina delle categorie sul pannello pubblico."""

    def __init__(self, pid, select):
        super().__init__(select)
        self.pid = pid

    @classmethod
    async def from_custom_id(cls, interaction, item, match, /):
        return cls(match["pid"], item)

    async def callback(self, interaction: discord.Interaction):
        vals = interaction.data.get("values") or []
        if not vals:
            return await interaction.response.defer()
        td = tdata(interaction.guild_id, self.pid)
        await OpenButton(self.pid, vals[0]).callback(interaction)
        try:  # rimette il menu "pulito", così si può scegliere di nuovo la stessa categoria
            await interaction.message.edit(view=panel_view(td))
        except discord.HTTPException:
            pass


def panel_view(td):
    v = discord.ui.View(timeout=None)
    cats = [c for c in td["categories"] if c["enabled"]][:25]
    mode = td.get("display", "auto")
    if cats and (mode == "select" or (mode == "auto" and len(cats) > 1)):
        v.add_item(PanelCategorySelect(td["pid"], _build_cat_select(td, cats)))
    else:
        for c in cats:
            v.add_item(OpenButton(td["pid"], c["id"], c["label"], c["emoji"], bstyle(c["style"])))
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
    return find_ticket(channel.guild.id, channel.id)[1]


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
    td, _tk0 = find_ticket(channel.guild.id, channel.id)
    if td is None:
        return
    await reply(text)
    await asyncio.sleep(5)
    tk = td["open"].pop(str(channel.id), None)
    if tk:
        td.setdefault("history", []).append({**tk, "closed_ts": int(time.time()), "closed_by": user.id})
        del td["history"][:-300]
        log_event(channel.guild, "tickets", f"Ticket #{channel.name} chiuso da {user}", user.id)
    G["save"]()
    try:
        await channel.delete(reason=f"Ticket chiuso da {user}")
    except discord.Forbidden:
        await channel.send("❌ Non ho il permesso di eliminare questo canale.")


async def do_close(channel, user, reply):
    td, tk = find_ticket(channel.guild.id, channel.id)
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
class SafeModal(discord.ui.Modal):
    """Modulo che, se qualcosa va storto, risponde con un messaggio chiaro e registra l'errore."""

    async def on_error(self, interaction, error):
        traceback.print_exception(type(error), error, error.__traceback__)
        try:
            errs = DATA.setdefault("errors", [])
            errs.append({"id": (errs[-1]["id"] + 1) if errs else 1, "ts": int(time.time()), "cmd": "modulo " + type(self).__name__,
                         "user": interaction.user.id, "guild": interaction.guild_id or 0, "type": type(error).__name__,
                         "tb": "".join(traceback.format_exception(type(error), error, error.__traceback__))[-1500:], "resolved": False})
            del errs[:-100]
        except Exception:
            pass
        msg = f"⚠️ Qualcosa è andato storto ({type(error).__name__}). Riprova; se succede ancora avvisa lo staff."
        try:
            if interaction.response.is_done():
                await interaction.followup.send(msg, ephemeral=True)
            else:
                await interaction.response.send_message(msg, ephemeral=True)
        except discord.HTTPException:
            pass


class MemberModal(SafeModal):
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


class RenameModal(SafeModal, title="Rinomina il ticket"):
    nome = discord.ui.TextInput(label="Nuovo nome del canale", max_length=90)

    async def on_submit(self, interaction: discord.Interaction):
        await do_rename(interaction.channel, self.nome.value,
                        lambda t, **kw: interaction.response.send_message(t, **kw))


class CategoryModal(SafeModal):
    def __init__(self, pid, cat=None):
        super().__init__(title="Nuova categoria" if not cat else f"Modifica {cat['label']}"[:45])
        self.pid, self.cat = pid, cat
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
        td = tdata(interaction.guild_id, self.pid)
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


class ButtonModal(SafeModal):
    def __init__(self, pid, btn=None):
        super().__init__(title="Nuovo pulsante" if not btn else f"Modifica {btn['label']}"[:45])
        self.pid, self.btn = pid, btn
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
        td = tdata(interaction.guild_id, self.pid)
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


class SettingsModal(SafeModal):
    """Posizione, attivazione e (permessi | ruolo richiesto) di una categoria o di un pulsante."""

    def __init__(self, pid, kind, obj, items):
        super().__init__(title=f"Impostazioni {obj['label']}"[:45])
        self.pid, self.kind, self.obj, self.items = pid, kind, obj, items
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
        td = tdata(interaction.guild_id, self.pid)
        if self.kind == "cat":
            await refresh_panel(interaction.guild, td)
        await interaction.response.send_message("✅ Impostazioni salvate.", ephemeral=True)


class PanelTextModal(SafeModal, title="Nome, titolo e descrizione"):
    def __init__(self, td):
        super().__init__()
        self.pid = td["pid"]
        self.nome = discord.ui.TextInput(label="Nome interno (solo per l'elenco)", max_length=40, default=td.get("name"))
        self.titolo = discord.ui.TextInput(label="Titolo del pannello", max_length=100, default=td["panel"]["title"])
        self.desc = discord.ui.TextInput(label="Descrizione del pannello", style=discord.TextStyle.paragraph,
                                         max_length=1500, default=td["panel"]["description"])
        for i in (self.nome, self.titolo, self.desc):
            self.add_item(i)

    async def on_submit(self, interaction):
        td = tdata(interaction.guild_id, self.pid)
        td["name"] = self.nome.value.strip() or td["name"]
        td["panel"]["title"], td["panel"]["description"] = self.titolo.value, self.desc.value
        G["save"]()
        await refresh_panel(interaction.guild, td)
        await interaction.response.send_message("✅ Pannello aggiornato.", ephemeral=True)


# ------------------------------------------------------------------ EDITOR
def summary_embed(td, guild):
    e = discord.Embed(title=f"⚙️ Editor · {td.get('name', 'pannello')}"[:256], color=0x5865F2)
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
    pub = guild.get_channel(td["panel"]["channel_id"]) if td["panel"].get("channel_id") else None
    e.add_field(name="Pubblicato in", value=pub.mention if pub else "non pubblicato")
    e.add_field(name="Categoria Discord", value=cat.name if cat else "Nessuna")
    e.add_field(name="Close", value="elimina il canale" if td["close_mode"] == "delete" else "blocca il canale (Reopen attivo)")
    e.add_field(name="Aspetto", value=DISPLAY_IT[td.get("display", "auto")], inline=False)
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
        self.pid, self.kind, self.mode = td["pid"], kind, mode
        items = td["categories"] if kind == "cat" else td["buttons"]
        self.sel = discord.ui.Select(placeholder="Scegli", options=[
            discord.SelectOption(label=i["label"][:100], value=i["id"], emoji=i["emoji"] or None)
            for i in items[:25]])
        self.sel.callback = self.picked
        self.add_item(self.sel)

    async def picked(self, interaction: discord.Interaction):
        td = tdata(interaction.guild_id, self.pid)
        items = td["categories"] if self.kind == "cat" else td["buttons"]
        obj = next((i for i in items if i["id"] == self.sel.values[0]), None)
        if not obj:
            return await interaction.response.send_message("Elemento non trovato.", ephemeral=True)
        if self.mode == "edit":
            return await interaction.response.send_modal(CategoryModal(self.pid, obj) if self.kind == "cat" else ButtonModal(self.pid, obj))
        if self.mode == "settings":
            return await interaction.response.send_modal(SettingsModal(self.pid, self.kind, obj, items))
        if self.kind == "cat" and len(items) <= 1:
            return await interaction.response.send_message("Deve restare almeno una categoria.", ephemeral=True)
        items.remove(obj)
        G["save"]()
        if self.kind == "cat":
            await refresh_panel(interaction.guild, td)
        await interaction.response.edit_message(content=f"🗑️ **{obj['label']}** rimosso.", view=None)


class DiscordCatView(OwnerView):
    def __init__(self, owner_id, pid):
        super().__init__(owner_id)
        self.pid = pid

    @discord.ui.select(cls=discord.ui.ChannelSelect, channel_types=[discord.ChannelType.category],
                       placeholder="Categoria Discord dove creare i ticket", min_values=1, max_values=1)
    async def pick(self, interaction, select):
        tdata(interaction.guild_id, self.pid)["category_id"] = select.values[0].id
        G["save"]()
        await interaction.response.edit_message(
            content=f"✅ I ticket verranno creati in **{select.values[0].name}**.", view=None)


async def delete_panel_message(guild, td):
    p = td["panel"]
    ch = guild.get_channel(p["channel_id"]) if p.get("channel_id") else None
    if ch and p.get("message_id"):
        try:
            await (await ch.fetch_message(p["message_id"])).delete()
        except discord.HTTPException:
            pass


class PublishView(OwnerView):
    def __init__(self, owner_id, pid):
        super().__init__(owner_id)
        self.pid = pid

    @discord.ui.select(cls=discord.ui.ChannelSelect, channel_types=[discord.ChannelType.text],
                       placeholder="Canale dove pubblicare il pannello", min_values=1, max_values=1)
    async def pick(self, interaction, select):
        td = tdata(interaction.guild_id, self.pid)
        ch = interaction.guild.get_channel(select.values[0].id)
        try:
            msg = await ch.send(embed=panel_embed(td), view=panel_view(td))
        except (discord.HTTPException, AttributeError):
            return await interaction.response.edit_message(content="❌ Non riesco a scrivere in quel canale.", view=None)
        await delete_panel_message(interaction.guild, td)
        td["panel"].update(channel_id=ch.id, message_id=msg.id)
        G["save"]()
        await interaction.response.edit_message(content=f"✅ Pannello pubblicato in {ch.mention} (il vecchio messaggio, se c'era, è stato eliminato).", view=None)


class PanelPickView(OwnerView):
    """Menu per scegliere quale pannello ticket usare: niente nomi da scrivere."""

    def __init__(self, owner_id, guild, on_pick):
        super().__init__(owner_id)
        self.on_pick = on_pick
        opts = []
        for pid, td in sorted(panels_of(guild.id).items(), key=lambda kv: int(kv[0][1:]))[:25]:
            ch = guild.get_channel(td["panel"]["channel_id"]) if td["panel"].get("channel_id") else None
            opts.append(discord.SelectOption(label=(td.get("name") or pid)[:100], value=pid,
                                             description=(f"Pubblicato in #{ch.name}" if ch else "Non pubblicato")[:100]))
        self.sel = discord.ui.Select(placeholder="Scegli un pannello", options=opts)
        self.sel.callback = self.picked
        self.add_item(self.sel)

    async def picked(self, interaction: discord.Interaction):
        await self.on_pick(interaction, self.sel.values[0])


class EditorView(OwnerView):
    def __init__(self, owner_id, pid):
        super().__init__(owner_id)
        self.pid = pid

    async def _pick(self, interaction, kind, mode, text):
        await interaction.response.send_message(
            text, view=PickView(self.owner_id, tdata(interaction.guild_id, self.pid), kind, mode), ephemeral=True)

    # riga 0: pulsanti esterni (categorie)
    @discord.ui.button(label="Aggiungi categoria", emoji="➕", style=discord.ButtonStyle.success, row=0)
    async def cat_add(self, interaction, button):
        await interaction.response.send_modal(CategoryModal(self.pid))

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
        await interaction.response.send_modal(PanelTextModal(tdata(interaction.guild_id, self.pid)))

    @discord.ui.button(label="Categoria Discord", emoji="📁", style=discord.ButtonStyle.secondary, row=1)
    async def dcat(self, interaction, button):
        await interaction.response.send_message("Scegli la categoria Discord:", view=DiscordCatView(self.owner_id, self.pid),
                                                ephemeral=True)

    @discord.ui.button(label="Modalità Close", emoji="🔒", style=discord.ButtonStyle.secondary, row=1)
    async def close_mode(self, interaction, button):
        td = tdata(interaction.guild_id, self.pid)
        td["close_mode"] = "lock" if td["close_mode"] == "delete" else "delete"
        G["save"]()
        await interaction.response.send_message(
            "🔒 Close ora **elimina il canale**." if td["close_mode"] == "delete" else
            "🔒 Close ora **blocca il canale**: lo staff può usare Reopen o Delete.", ephemeral=True)

    @discord.ui.button(label="Aspetto pannello", emoji="🎛️", style=discord.ButtonStyle.secondary, row=1)
    async def display_mode(self, interaction, button):
        td = tdata(interaction.guild_id, self.pid)
        order = ["auto", "select", "buttons"]
        td["display"] = order[(order.index(td.get("display", "auto")) + 1) % 3]
        G["save"]()
        ok = await refresh_panel(interaction.guild, td)
        await interaction.response.send_message(
            f"🎛️ Aspetto del pannello: **{DISPLAY_IT[td['display']]}**." + ("" if ok else "\n⚠️ Discord ha rifiutato l'aggiornamento del pannello (emoji non valida?)."),
            ephemeral=True)

    # riga 2: pulsanti interni
    @discord.ui.button(label="Aggiungi pulsante", emoji="➕", style=discord.ButtonStyle.success, row=2)
    async def btn_add(self, interaction, button):
        await interaction.response.send_modal(ButtonModal(self.pid))

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
        await interaction.response.send_message(embed=summary_embed(tdata(interaction.guild_id, self.pid), interaction.guild),
                                                ephemeral=True)

    @discord.ui.button(label="Aggiorna pannello", emoji="🔄", style=discord.ButtonStyle.success, row=3)
    async def refresh(self, interaction, button):
        ok = await refresh_panel(interaction.guild, tdata(interaction.guild_id, self.pid))
        await interaction.response.send_message(
            "✅ Pannello aggiornato." if ok else "⚠️ Aggiornamento rifiutato da Discord.", ephemeral=True)

    @discord.ui.button(label="Pubblica in un canale", emoji="📣", style=discord.ButtonStyle.primary, row=3)
    async def publish(self, interaction, button):
        await interaction.response.send_message("In quale canale vuoi (ri)pubblicare questo pannello?",
                                                view=PublishView(self.owner_id, self.pid), ephemeral=True)


# ------------------------------------------------------------------ COMANDI
async def _panel_create(ctx, canale: Optional[discord.TextChannel] = None, *, nome: Optional[str] = None):
    """Crea e pubblica un pannello ticket"""
    ps = panels_of(ctx.guild.id)
    td = next((p for p in ps.values() if not p["panel"].get("message_id")), None)  # riusa un pannello non ancora pubblicato
    if td is None:
        if len(ps) >= 25:
            raise commands.CheckFailure("Hai già 25 pannelli: eliminane uno con `/ticket panel-delete`.")
        td = new_panel(ctx.guild.id, nome)
    elif nome:
        td["name"] = nome
    ch = canale or ctx.channel
    msg = await ch.send(embed=panel_embed(td), view=panel_view(td))
    td["panel"].update(channel_id=ch.id, message_id=msg.id)
    G["save"]()
    await ctx.send(f"✅ Pannello **{td['name']}** pubblicato in {ch.mention}. Con `/ticketpaneledit` potrai scegliere quale modificare.", ephemeral=True)


async def _open_editor(interaction, pid):
    td = tdata(interaction.guild_id, pid)
    await interaction.response.edit_message(content=None, embed=summary_embed(td, interaction.guild),
                                            view=EditorView(interaction.user.id, pid))


async def _panel_edit(ctx):
    """Scegli un pannello ticket e modificane categorie, descrizione e pulsanti"""
    if not panels_of(ctx.guild.id):
        raise commands.CheckFailure("Non c'è ancora nessun pannello: creane uno con `/ticketpanelcreate`.")
    await ctx.send("Quale pannello vuoi modificare?", view=PanelPickView(ctx.author.id, ctx.guild, _open_editor), ephemeral=True)


async def _panel_create_g(ctx, canale: Optional[discord.TextChannel] = None, *, nome: Optional[str] = None):
    """Crea e pubblica un pannello ticket"""
    await _panel_create(ctx, canale, nome=nome)


async def _panel_edit_g(ctx):
    """Scegli un pannello ticket e modificalo"""
    await _panel_edit(ctx)


async def _panel_delete(ctx):
    """Elimina un pannello ticket"""
    if not panels_of(ctx.guild.id):
        raise commands.CheckFailure("Non c'è nessun pannello da eliminare.")

    async def on_pick(interaction, pid):
        ps = panels_of(interaction.guild_id)
        td = ps.get(pid)
        if td is None:
            return await interaction.response.edit_message(content="Pannello non trovato.", view=None)
        if td["open"]:
            return await interaction.response.edit_message(
                content=f"⚠️ Ci sono **{len(td['open'])}** ticket aperti in **{td['name']}**: chiudili prima di eliminare il pannello.", view=None)
        await delete_panel_message(interaction.guild, td)
        del ps[pid]
        G["save"]()
        await interaction.response.edit_message(content=f"🗑️ Pannello **{td['name']}** eliminato.", view=None)

    await ctx.send("Quale pannello vuoi eliminare?", view=PanelPickView(ctx.author.id, ctx.guild, on_pick), ephemeral=True)


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
    bot.add_dynamic_items(OpenButton, TicketButton, PanelCategorySelect)
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
ticket panel-create|Crea e pubblica un nuovo pannello dei ticket.
ticket panel-edit|Scegli un pannello e modifica descrizione, categorie e pulsanti.
ticket panel-delete|Scegli un pannello da eliminare.
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
application panel-edit|Modifica il pannello e il canale in cui arrivano le candidature.
application submit|Invia una candidatura.
application review|Esamina una candidatura.
application questions|Modifica le domande.
application set-reviewers|Imposta i revisori.
application role|Imposta il ruolo dato a chi viene accettato.
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
ping|Imposta i ruoli da menzionare nei ticket (massimo 40).
ping reset|Resetta tutti i ping dei ticket.
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
application|S||panel-create:A:manage_guild,panel-edit:A:manage_guild,questions:A:manage_guild,set-reviewers:A:manage_guild,role:A:manage_guild,submit:P,review,list,stats
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
    # /ping (ruoli menzionati nei ticket) e /ping reset
    ex = {"level": "A", "perm": ""}
    pg = commands.HybridGroup(IMPL["ping"], name="ping", fallback="ruoli", description=DESC["ping"][:100],
                              invoke_without_command=True, extras=ex)
    pg.command(name="reset", description=DESC["ping reset"][:100], extras=ex)(IMPL["ping reset"])
    bot.add_command(pg)
    REG.append({"cat": "🎫 Ticket", "full": "ping", "level": "A", "perm": ""})
    REG.append({"cat": "🎫 Ticket", "full": "ping reset", "level": "A", "perm": ""})


# ------------------------------------------- PING DEI TICKET
PING_MAX = 40


@impl("ping")
async def _ping_main(ctx, ruolo1: Optional[discord.Role] = None, ruolo2: Optional[discord.Role] = None,
                     ruolo3: Optional[discord.Role] = None, ruolo4: Optional[discord.Role] = None,
                     ruolo5: Optional[discord.Role] = None):
    panels_of(ctx.guild.id)
    td = G["gdata"](ctx.guild.id)
    new = [r for r in (ruolo1, ruolo2, ruolo3, ruolo4, ruolo5) if r is not None]
    if not new:
        if "ping_roles" not in td:
            return await ctx.send("📣 Nessun ruolo ping impostato: nei ticket vengono menzionati i ruoli staff.\n"
                                  "Aggiungi ruoli con `/ping ruoli` (o `.ping @ruolo1 @ruolo2 ...`, massimo 5 alla volta, 40 in tutto).")
        cur = td["ping_roles"]
        return await ctx.send(f"📣 **Ruoli ping dei ticket ({len(cur)}/{PING_MAX}):** " + (" ".join(f"<@&{i}>" for i in cur) or "nessuno"),
                              allowed_mentions=NOMENTION)
    cur = td.setdefault("ping_roles", [])
    added, skipped, full = [], [], 0
    for r in new:
        if r.is_default() or r.id in cur or any(r.id == a.id for a in added):
            skipped.append(r)
        elif len(cur) + len(added) >= PING_MAX:
            full += 1
        else:
            added.append(r)
    cur.extend(r.id for r in added)
    save()
    msg = f"✅ Aggiunti **{len(added)}** ruoli ai ping dei ticket ({len(cur)}/{PING_MAX}): " + (" ".join(r.mention for r in added) or "nessuno")
    if skipped:
        msg += f"\n↪️ Saltati {len(skipped)} (già presenti o @everyone)."
    if full:
        msg += f"\n⚠️ Limite di {PING_MAX} ruoli raggiunto: {full} non aggiunti. Usa `/ping reset` per ricominciare."
    msg += "\nDa ora, nei **nuovi** ticket questi ruoli entrano nel canale e vengono menzionati."
    await ctx.send(msg, allowed_mentions=NOMENTION)


@impl("ping reset")
async def _ping_reset(ctx):
    panels_of(ctx.guild.id)
    td = G["gdata"](ctx.guild.id)
    n = len(td.get("ping_roles", []))
    td["ping_roles"] = []
    save()
    await ctx.send(f"🧹 Ping dei ticket azzerati ({n} ruoli rimossi): nei nuovi ticket non verrà menzionato nessun ruolo. "
                   "I ticket già aperti restano come sono. Per ricominciare usa `/ping ruoli`.")


# ------------------------------------------- MEMBRI E AUTOROLE (completi)
NOMENTION = discord.AllowedMentions.none()
_last_activity_save = 0


@bot.listen("on_message")
async def _track_activity(message):
    """Conta i messaggi dei membri per /member activity."""
    global _last_activity_save
    if message.guild is None or message.author.bot:
        return
    _am = mod(message.guild.id, "activity")
    if not _am["enabled"] or str(message.channel.id) in _am["ignora_canali"]:
        return
    a = gdata(message.guild.id).setdefault("activity", {}).setdefault(
        str(message.author.id), {"messages": 0, "last": 0})
    a["messages"] += 1
    a["last"] = int(time.time())
    if time.time() - _last_activity_save > 30:  # salva al massimo ogni 30 secondi
        save()
        _last_activity_save = time.time()


@impl("member permissions")
async def _member_permissions(ctx, member: Optional[discord.Member] = None):
    """Mostra i permessi effettivi di un membro"""
    m = member or ctx.author
    e = discord.Embed(title=f"Permessi di {m.display_name}", color=m.color)
    if m.guild_permissions.administrator:
        e.description = "👑 **Administrator**: ha tutti i permessi."
    else:
        granted = [n.replace("_", " ").title() for n, v in m.guild_permissions if v]
        e.description = (", ".join(granted) or "Nessun permesso speciale.")[:4000]
    await ctx.send(embed=e)


@impl("member banner")
async def _member_banner(ctx, member: Optional[discord.Member] = None):
    """Mostra il banner di un membro"""
    user = await bot.fetch_user((member or ctx.author).id)
    await ctx.send(user.banner.url if user.banner else "Questo utente non ha un banner.")


@impl("member joined")
async def _member_joined(ctx, member: Optional[discord.Member] = None):
    """Mostra la data d'ingresso"""
    m = member or ctx.author
    fmt = discord.utils.format_dt
    ingresso = f"{fmt(m.joined_at, 'F')} ({fmt(m.joined_at, 'R')})" if m.joined_at else "sconosciuto"
    await ctx.send(f"📅 {m.mention} è entrato: {ingresso}\nAccount creato {fmt(m.created_at, 'R')}.",
                   allowed_mentions=NOMENTION)


@impl("member roles")
async def _member_roles(ctx, member: Optional[discord.Member] = None):
    """Elenca i ruoli di un membro"""
    m = member or ctx.author
    roles = [r.mention for r in reversed(m.roles[1:])]
    await ctx.send((", ".join(roles) or "Nessun ruolo.")[:1900], allowed_mentions=NOMENTION)


@impl("member rename")
async def _member_rename(ctx, member: discord.Member, *, nome: Optional[str] = None):
    """Cambia il soprannome di un membro"""
    await _nickname(ctx, member, nome=nome)


@impl("member notes")
async def _member_notes(ctx, member: discord.Member):
    """Mostra le note interne di un membro"""
    notes = gdata(ctx.guild.id)["notes"].get(str(member.id), [])
    lines = [f"`{i}` <t:{n['ts']}:d> da <@{n['by']}>: {n['text']}" for i, n in enumerate(notes, 1)]
    await ctx.send("\n".join(lines)[:1900] or "Nessuna nota su questo membro.", allowed_mentions=NOMENTION)


@impl("member activity")
async def _member_activity(ctx, member: Optional[discord.Member] = None):
    """Mostra l'attività registrata dal bot"""
    m = member or ctx.author
    a = gdata(ctx.guild.id).get("activity", {}).get(str(m.id))
    if not a:
        return await ctx.send("Nessuna attività registrata: il bot conta i messaggi da quando è attivo.")
    await ctx.send(f"📈 {m.mention}: **{a['messages']}** messaggi, ultimo <t:{a['last']}:R>.",
                   allowed_mentions=NOMENTION)


# --- Autorole
AR_TARGET = {"tutti": "all", "all": "all", "umani": "humans", "utenti": "humans", "humans": "humans",
             "bot": "bots", "bots": "bots"}
AR_TARGET_IT = {"all": "tutti", "humans": "solo umani", "bots": "solo bot"}


def _ar(guild_id):
    gd = gdata(guild_id)
    gd.setdefault("autoroles", [])
    gd.setdefault("autorole_on", True)
    return gd


async def _ar_save(ctx, ruolo, target, ritardo):
    t = AR_TARGET.get(target.lower())
    if not t:
        raise commands.BadArgument("target non valido")
    if ruolo.is_default() or ruolo.managed or ruolo >= ctx.guild.me.top_role:
        raise commands.CheckFailure("Non posso assegnare quel ruolo: è sopra al mio, è @everyone o è gestito da un'integrazione.")
    ritardo = max(0, min(ritardo, 86400))
    gd = _ar(ctx.guild.id)
    gd["autoroles"] = [r for r in gd["autoroles"] if r["role"] != ruolo.id]
    gd["autoroles"].append({"role": ruolo.id, "target": t, "delay": ritardo})
    gd["autorole_on"] = True
    save()
    extra = f", dopo {ritardo}s" if ritardo else ""
    await ctx.send(f"✅ Autorole attivo: {ruolo.mention} a **{AR_TARGET_IT[t]}**{extra}.", allowed_mentions=NOMENTION)


@impl("autorole set")
async def _autorole_set(ctx, ruolo: discord.Role, target: str = "tutti"):
    """Imposta un ruolo automatico (target: tutti, umani, bot)"""
    await _ar_save(ctx, ruolo, target, 0)


@impl("autorole configure")
async def _autorole_configure(ctx, ruolo: discord.Role, target: str = "tutti", ritardo: int = 0):
    """Configura target e ritardo (secondi) di un ruolo automatico"""
    await _ar_save(ctx, ruolo, target, ritardo)


@impl("autorole remove")
async def _autorole_remove(ctx, ruolo: discord.Role):
    """Rimuove una regola autorole"""
    gd = _ar(ctx.guild.id)
    before = len(gd["autoroles"])
    gd["autoroles"] = [r for r in gd["autoroles"] if r["role"] != ruolo.id]
    save()
    await ctx.send("✅ Regola rimossa." if len(gd["autoroles"]) < before else "Quel ruolo non ha una regola.")


@impl("autorole list")
async def _autorole_list(ctx):
    """Elenca le regole autorole"""
    gd = _ar(ctx.guild.id)
    lines = [f"<@&{r['role']}> · {AR_TARGET_IT[r['target']]}" + (f" · dopo {r['delay']}s" if r["delay"] else "")
             for r in gd["autoroles"]]
    stato = "🟢 attivo" if gd["autorole_on"] else "🔴 disattivato"
    await ctx.send(f"**Autorole** ({stato})\n" + ("\n".join(lines) or "Nessuna regola."),
                   allowed_mentions=NOMENTION)


@impl("autorole disable")
async def _autorole_disable(ctx):
    """Disattiva l'autorole (le regole restano salvate)"""
    _ar(ctx.guild.id)["autorole_on"] = False
    save()
    await ctx.send("🔴 Autorole disattivato. Per riattivarlo usa `/autorole set`.")


@impl("autorole test")
async def _autorole_test(ctx, member: Optional[discord.Member] = None):
    """Simula l'autorole su un membro"""
    m = member or ctx.author
    gd = _ar(ctx.guild.id)
    if not gd["autoroles"]:
        return await ctx.send("Nessuna regola impostata.")
    lines = []
    for r in gd["autoroles"]:
        role = ctx.guild.get_role(r["role"])
        if not role:
            lines.append("⚠️ Un ruolo della regola non esiste più.")
            continue
        applies = r["target"] == "all" or (r["target"] == "bots") == m.bot
        can = role < ctx.guild.me.top_role and not role.managed
        note = ("" if applies else " · non si applica a questo utente") + ("" if can else " · non posso assegnarlo")
        lines.append(f"{'✅' if applies and can else '❌'} {role.mention} · {AR_TARGET_IT[r['target']]}{note}")
    head = "" if gd["autorole_on"] else "⚠️ L'autorole è disattivato.\n"
    if not ctx.guild.me.guild_permissions.manage_roles:
        head += "⚠️ Al bot manca il permesso Manage Roles.\n"
    await ctx.send(head + "\n".join(lines), allowed_mentions=NOMENTION)


async def _give_role(member, role, delay):
    try:
        if delay:
            await asyncio.sleep(delay)
        await member.add_roles(role, reason="Autorole")
    except (discord.Forbidden, discord.HTTPException):
        pass


@bot.listen("on_member_join")
async def _autorole_join(member):
    gd = _ar(member.guild.id)
    if not gd["autorole_on"]:
        return
    for r in gd["autoroles"]:
        if (r["target"] == "humans" and member.bot) or (r["target"] == "bots" and not member.bot):
            continue
        role = member.guild.get_role(r["role"])
        if role:
            asyncio.create_task(_give_role(member, role, r["delay"]))


# ============================================================================
# ESTENSIONE 1: infrastruttura, sicurezza, filtri, log e audit
# ============================================================================
def now():
    return int(time.time())


def sec(gid, key, kind=dict):
    return gdata(gid).setdefault(key, kind())


def nid(gid, name):
    ids = sec(gid, "ids")
    ids[name] = ids.get(name, 0) + 1
    return ids[name]


def emb(title, desc="", color=0x5865F2):
    return discord.Embed(title=str(title)[:256], description=str(desc)[:4000], color=color)


def fail(msg):
    return commands.CheckFailure(msg)


COLORI = {"rosso": 0xED4245, "verde": 0x57F287, "blu": 0x5865F2, "giallo": 0xFEE75C, "arancione": 0xE67E22,
          "viola": 0x9B59B6, "rosa": 0xEB459E, "nero": 0x23272A, "bianco": 0xFFFFFF, "grigio": 0x99AAB5}


def pcolor(s, default=0x5865F2):
    s = (s or "").strip().lower().lstrip("#")
    if s in COLORI:
        return COLORI[s]
    try:
        return int(s, 16)
    except ValueError:
        return default


def pdur(s):
    """'30s', '10m', '2h', '1d', '1w' o un numero (= minuti) -> secondi. None se non valida."""
    m = re.fullmatch(r"\s*(\d+)\s*([smhdw]?)\s*", str(s).lower())
    if not m:
        return None
    return int(m.group(1)) * {"s": 1, "m": 60, "h": 3600, "d": 86400, "w": 604800}[m.group(2) or "m"]


def fdur(n):
    n, parts = int(n), []
    for lab, v in (("g", 86400), ("h", 3600), ("m", 60), ("s", 1)):
        if n >= v:
            parts.append(f"{n // v}{lab}")
            n %= v
    return " ".join(parts) or "0s"


def ids_in(text):
    return [int(x) for x in re.findall(r"\d{15,20}", text or "")]


def tz_of(gid):
    try:
        from zoneinfo import ZoneInfo
        return ZoneInfo(sec(gid, "config").get("timezone", "Europe/Rome"))
    except Exception:
        return timezone.utc


def pwhen(s, gid):
    """'10m', '2h' oppure 'HH:MM' (prossima occorrenza nel fuso del server) -> timestamp. None se non valido."""
    d = pdur(s)
    if d is not None:
        return now() + d
    m = re.fullmatch(r"\s*(\d{1,2}):(\d{2})\s*", str(s))
    if not m or int(m.group(1)) > 23 or int(m.group(2)) > 59:
        return None
    n = datetime.now(tz_of(gid))
    t = n.replace(hour=int(m.group(1)), minute=int(m.group(2)), second=0, microsecond=0)
    if t <= n:
        t += timedelta(days=1)
    return int(t.timestamp())


async def get_msg(ctx, ref, channel=None):
    m = re.search(r"(\d{15,20})/(\d{15,20})/(\d{15,20})", ref)
    try:
        if m:
            return await ctx.guild.get_channel(int(m.group(2))).fetch_message(int(m.group(3)))
        return await (channel or ctx.channel).fetch_message(int(re.search(r"\d{15,20}", ref).group()))
    except (AttributeError, discord.NotFound, discord.Forbidden, ValueError):
        raise fail("Messaggio non trovato (usa l'ID o il link, nel canale giusto).")


async def safe_send(ch, **kw):
    try:
        return await ch.send(**kw)
    except (discord.HTTPException, AttributeError):
        return None


def need_confirm(conferma):
    if (conferma or "").strip().upper() != "CONFERMA":
        raise fail("⚠️ Operazione delicata: ripeti il comando aggiungendo `CONFERMA` come ultimo parametro.")


def chunk_send_lines(lines, limit=1900):
    out, cur = [], ""
    for ln in lines:
        if len(cur) + len(ln) + 1 > limit:
            out.append(cur)
            cur = ""
        cur += ln + "\n"
    if cur:
        out.append(cur)
    return out or ["—"]


# ------------------------------------------------------------ SCHEDULER
JOB_HANDLERS = {}


def job(kind):
    def deco(fn):
        JOB_HANDLERS[kind] = fn
        return fn
    return deco


def add_job(gid, kind, at, **data):
    j = {"id": nid(gid, "job"), "kind": kind, "at": int(at), **data}
    sec(gid, "jobs", list).append(j)
    save()
    return j


def jobs_of(gid, *kinds):
    return [j for j in sec(gid, "jobs", list) if j["kind"] in kinds]


def del_job(gid, jid, *kinds):
    lst = sec(gid, "jobs", list)
    j = next((x for x in lst if x["id"] == jid and x["kind"] in kinds), None)
    if j:
        lst.remove(j)
        save()
    return j


async def scheduler():
    await bot.wait_until_ready()
    while not bot.is_closed():
        try:
            t, changed = now(), False
            for gid, gd in list(DATA["guilds"].items()):
                for j in [x for x in gd.get("jobs", []) if x["at"] <= t]:
                    gd["jobs"].remove(j)
                    changed = True
                    h = JOB_HANDLERS.get(j["kind"])
                    if h:
                        try:
                            await h(int(gid), j)
                        except Exception:
                            traceback.print_exc()
                    if j.get("every"):
                        j["at"] = t + j["every"]
                        gd["jobs"].append(j)
            if changed:
                save()
        except Exception:
            traceback.print_exc()
        await asyncio.sleep(15)


# ------------------------------------------------------------ LOG
LOG_KINDS = ["messages", "roles", "channels", "moderation", "tickets", "voice", "joins", "leaves", "automod", "bot-errors"]


def log_event(guild, kind, text, user_id=0):
    if guild is None:
        return
    logs = sec(guild.id, "logs", list)
    logs.append({"ts": now(), "kind": kind, "user": user_id, "text": str(text)[:500]})
    del logs[:-500]
    cfg = sec(guild.id, "logcfg")
    if kind in cfg.get("off", []):
        return
    ch = guild.get_channel(cfg.get(kind) or cfg.get("all") or 0)
    if ch:
        e = emb(f"📋 {kind}", text, 0x99AAB5)
        e.timestamp = datetime.now(timezone.utc)
        asyncio.create_task(safe_send(ch, embed=e, allowed_mentions=NOMENTION))


def case_raw(gid, kind, user_id, mod_id, reason):
    gd = gdata(gid)
    gd["case_counter"] += 1
    c = {"id": gd["case_counter"], "type": kind, "user": user_id, "mod": mod_id, "reason": reason, "ts": now()}
    gd["cases"].append(c)
    save()
    return c


def add_case(ctx, kind, user_id, reason):  # sostituisce la versione base
    c = case_raw(ctx.guild.id, kind, user_id, ctx.author.id, reason)
    log_event(ctx.guild, "moderation", f"{kind} · <@{user_id}> da {ctx.author} · {reason}", user_id)
    return c


# ------------------------------------------------------------ PERMESSI ESTESI
def is_admin(m):
    if m.id == m.guild.owner_id or m.guild_permissions.administrator:
        return True
    gd = gdata(m.guild.id)
    return gd.get("user_levels", {}).get(str(m.id)) == "admin" or any(r.id in gd["admin_roles"] for r in m.roles)


def is_staff(m):
    if is_admin(m):
        return True
    gd = gdata(m.guild.id)
    return gd.get("user_levels", {}).get(str(m.id)) in ("staff", "admin") or any(r.id in gd["staff_roles"] for r in m.roles)


_cooldowns = {}


async def extension_guard(ctx, level, perm):
    """Manutenzione, comandi/moduli disabilitati, cooldown, accessi e override: ritorna (livello, permesso)."""
    name = ctx.command.qualified_name
    top = name.split()[0]
    b = DATA.setdefault("bot", {})
    dev = ctx.author.id in OWNER_IDS or ctx.author.id in DEV_IDS
    if top in ("bot", "dev"):
        return level, perm
    if ctx.guild and ctx.guild.id in b.get("disabled_guilds", []) and not dev:
        raise fail("🚫 Il bot è disabilitato in questo server.")
    if b.get("maintenance") and not dev:
        raise fail("🛠️ Il bot è in manutenzione globale, riprova più tardi.")
    if ctx.guild is None:
        return level, perm
    gd = gdata(ctx.guild.id)
    cf = gd.setdefault("config", {})
    admin = is_admin(ctx.author)
    if not admin and top not in ("config", "server", "help"):
        if cf.get("maintenance"):
            raise fail("🛠️ Il bot è in manutenzione in questo server.")
        if name in cf.get("disabled_cmds", []) or top in cf.get("modules_off", []):
            raise fail("🚫 Questo comando è disabilitato.")
        cd = cf.get("cooldowns", {}).get(name)
        if cd:
            k = (ctx.guild.id, ctx.author.id, name)
            if time.time() - _cooldowns.get(k, 0) < cd:
                raise fail(f"⏳ Aspetta ancora {int(cd - (time.time() - _cooldowns[k]))}s.")
            _cooldowns[k] = time.time()
        deny = gd.get("access", {}).get("channels", {}).get(str(ctx.channel.id), {}).get("deny", [])
        if name in deny or top in deny:
            raise fail("🚫 Questo comando non si può usare in questo canale.")
    over = gd.get("perm_over", {}).get(name)
    if over in ("P", "S", "A"):
        level = over
    acc = gd.get("access", {})
    grants = list(acc.get("users", {}).get(str(ctx.author.id), []))
    for r in ctx.author.roles:
        grants += acc.get("roles", {}).get(str(r.id), [])
    if name in grants or top in grants:
        return "P", ""
    return level, perm


# ------------------------------------------------------------ MODULI DI SICUREZZA / FILTRI
MODS = {
    "antispam": {"enabled": False, "messaggi": 5, "secondi": 5, "azione": "timeout", "minuti": 5},
    "antilink": {"enabled": False, "azione": "elimina", "domini": []},
    "badwords": {"enabled": True, "parole": [], "azione": "elimina"},
    "filter": {"enabled": True, "regole": []},
    "caps-filter": {"enabled": False, "percentuale": 70, "minimo": 10, "azione": "elimina"},
    "invite-filter": {"enabled": False, "azione": "elimina"},
    "emoji-limit": {"enabled": False, "massimo": 10, "azione": "elimina"},
    "duplicate-filter": {"enabled": False, "ripetizioni": 3, "azione": "elimina"},
    "link-filter": {"enabled": False, "bloccati": [], "azione": "elimina"},
    "attachment-filter": {"enabled": False, "estensioni": [], "max_mb": 0, "azione": "elimina"},
    "mention-limit": {"enabled": False, "massimo": 5, "azione": "elimina"},
    "raid-protection": {"enabled": False, "ingressi": 5, "secondi": 10, "azione": "lockdown"},
    "automod": {"enabled": False, "rules": []},
    "leveling": {"enabled": False, "xp_min": 15, "xp_max": 25, "cooldown": 60, "canale": 0,
                 "messaggio": "🎉 {user} è salito al livello {level}!"},
    "activity": {"enabled": True, "ignora_canali": []},
    "giveaway": {"ruolo_richiesto": 0, "emoji": "🎉", "colore": "viola"},
    "suggestion": {"canale": 0, "approvati": 0, "rifiutati": 0, "ruolo_ping": 0},
    "report": {"canale": 0, "ruolo_ping": 0},
    "backup": {"max_backup": 5, "includi_dati": True},
    "ticketcfg": {"log_canale": 0},
}


def mod(gid, name):
    m = sec(gid, "mods").setdefault(name, {})
    for k, v in MODS[name].items():
        m.setdefault(k, copy.deepcopy(v))
    return m


def _cast(old, text):
    if isinstance(old, bool):
        return yes(text)
    if isinstance(old, int):
        mm = re.search(r"\d+", text)
        if not mm:
            raise ValueError
        return int(mm.group())
    if isinstance(old, list):
        return [x.strip().lower() for x in re.split(r"[,;]", text) if x.strip()]
    return text


def reg_settings(key, name):
    async def settings(ctx, chiave: Optional[str] = None, *, valore: Optional[str] = None):
        m = mod(ctx.guild.id, name)
        shown = {k: v for k, v in m.items() if k not in ("rules", "regole")}
        if chiave is None:
            lines = [f"`{k}` = {', '.join(map(str, v)) or '—' if isinstance(v, list) else v}" for k, v in shown.items()]
            return await ctx.send(embed=emb(f"⚙️ Impostazioni {name}", "\n".join(lines) + f"\n\nModifica: `/{key} <chiave> <valore>`"))
        if chiave not in shown:
            raise fail("Chiave sconosciuta. Disponibili: " + ", ".join(f"`{k}`" for k in shown))
        if valore is None:
            raise fail("Manca il valore.")
        try:
            m[chiave] = _cast(MODS[name][chiave], valore)
        except ValueError:
            raise fail("Valore non valido per questa chiave.")
        save()
        await ctx.send(f"✅ `{name}.{chiave}` aggiornato.")
    IMPL[key] = settings


def reg_toggle(name, label=None):
    async def enable(ctx):
        mod(ctx.guild.id, name)["enabled"] = True
        save()
        await ctx.send(f"🟢 `{name}` attivato.")

    async def disable(ctx):
        mod(ctx.guild.id, name)["enabled"] = False
        save()
        await ctx.send(f"🔴 `{name}` disattivato.")
    IMPL[f"{name} enable"] = enable
    IMPL[f"{name} disable"] = disable


for _n in ("antispam", "antilink", "automod", "raid-protection", "invite-filter", "caps-filter",
           "duplicate-filter", "filter"):
    reg_toggle(_n)
for _k, _n in (("antispam settings", "antispam"), ("antilink settings", "antilink"), ("badwords settings", "badwords"),
               ("link-filter settings", "link-filter"), ("attachment-filter settings", "attachment-filter"),
               ("raid-protection settings", "raid-protection"), ("leveling settings", "leveling"),
               ("activity settings", "activity"), ("giveaway settings", "giveaway"),
               ("suggestion settings", "suggestion"), ("report settings", "report"), ("backup settings", "backup")):
    reg_settings(_k, _n)


@impl("emoji-limit set")
async def _emoji_limit_set(ctx, massimo: int):
    m = mod(ctx.guild.id, "emoji-limit")
    m.update(enabled=True, massimo=max(1, massimo))
    save()
    await ctx.send(f"😀 Limite emoji: **{m['massimo']}** per messaggio.")


@impl("emoji-limit disable")
async def _emoji_limit_disable(ctx):
    mod(ctx.guild.id, "emoji-limit")["enabled"] = False
    save()
    await ctx.send("🔴 Limite emoji disattivato.")


@impl("mention-limit set")
async def _mention_limit_set(ctx, massimo: int):
    m = mod(ctx.guild.id, "mention-limit")
    m.update(enabled=True, massimo=max(1, massimo))
    save()
    await ctx.send(f"📢 Limite menzioni: **{m['massimo']}** per messaggio.")


@impl("mention-limit reset")
async def _mention_limit_reset(ctx):
    m = mod(ctx.guild.id, "mention-limit")
    m.update(enabled=False, massimo=MODS["mention-limit"]["massimo"])
    save()
    await ctx.send("🔄 Limite menzioni ripristinato e disattivato.")


@impl("antispam test")
async def _antispam_test(ctx):
    m = mod(ctx.guild.id, "antispam")
    me = ctx.guild.me.guild_permissions
    await ctx.send(embed=emb("🧪 Test antispam", (
        f"Stato: {'🟢 attivo' if m['enabled'] else '🔴 disattivo'}\n"
        f"Soglia: {m['messaggi']} messaggi in {m['secondi']}s → {m['azione']} ({m['minuti']} min)\n"
        f"Bot può eliminare messaggi: {'✅' if me.manage_messages else '❌'}\n"
        f"Bot può mettere in timeout: {'✅' if me.moderate_members else '❌'}\n"
        "Lo staff e gli utenti fidati sono esclusi dal controllo.")))


@impl("antilink allow-domain")
async def _antilink_allow(ctx, dominio: str):
    m = mod(ctx.guild.id, "antilink")
    d = dominio.lower().replace("https://", "").replace("http://", "").strip("/")
    if d not in m["domini"]:
        m["domini"].append(d)
    save()
    await ctx.send(f"✅ Dominio consentito: `{d}`")


@impl("antilink remove-domain")
async def _antilink_remove(ctx, dominio: str):
    m = mod(ctx.guild.id, "antilink")
    d = dominio.lower().strip("/")
    if d in m["domini"]:
        m["domini"].remove(d)
    save()
    await ctx.send(f"🗑️ Dominio rimosso: `{d}`")


# --- badwords e filtri personalizzati
@impl("badwords add")
async def _bw_add(ctx, *, parola: str):
    m = mod(ctx.guild.id, "badwords")
    p = parola.strip().lower()
    if p not in m["parole"]:
        m["parole"].append(p)
    save()
    await ctx.send("✅ Parola vietata aggiunta.", ephemeral=True)


@impl("badwords remove")
async def _bw_remove(ctx, *, parola: str):
    m = mod(ctx.guild.id, "badwords")
    p = parola.strip().lower()
    if p in m["parole"]:
        m["parole"].remove(p)
    save()
    await ctx.send("🗑️ Parola rimossa.", ephemeral=True)


@impl("badwords list")
async def _bw_list(ctx):
    m = mod(ctx.guild.id, "badwords")
    await ctx.send(f"🚫 {len(m['parole'])} parole vietate" + (f": ||{', '.join(m['parole'])}||" if m["parole"] else "."), ephemeral=True)


def _match(pattern, text):
    if pattern.startswith("re:"):
        try:
            return re.search(pattern[3:], text, re.I) is not None
        except re.error:
            return False
    return pattern.lower() in text.lower()


@impl("filter add")
async def _filter_add(ctx, pattern: str, azione: str = "elimina"):
    if azione not in ("elimina", "avvisa", "timeout"):
        raise fail("Azione valida: elimina, avvisa, timeout.")
    mod(ctx.guild.id, "filter")["regole"].append({"pattern": pattern, "azione": azione})
    save()
    await ctx.send(f"✅ Filtro aggiunto: `{pattern}` → {azione}. (Per le regex usa il prefisso `re:`)")


@impl("filter remove")
async def _filter_remove(ctx, pattern: str):
    m = mod(ctx.guild.id, "filter")
    n = len(m["regole"])
    m["regole"] = [r for r in m["regole"] if r["pattern"] != pattern]
    save()
    await ctx.send("🗑️ Filtro rimosso." if len(m["regole"]) < n else "Filtro non trovato.")


@impl("filter list")
async def _filter_list(ctx):
    m = mod(ctx.guild.id, "filter")
    await ctx.send("\n".join(f"`{r['pattern']}` → {r['azione']}" for r in m["regole"]) or "Nessun filtro.")


@impl("filter test")
async def _filter_test(ctx, *, testo: str):
    m = mod(ctx.guild.id, "filter")
    hits = [r["pattern"] for r in m["regole"] if _match(r["pattern"], testo)]
    await ctx.send(("🚫 Il testo viene bloccato da: " + ", ".join(f"`{h}`" for h in hits)) if hits else "✅ Il testo passa tutti i filtri.")


# --- regole automod personalizzate
@impl("automod rules")
async def _automod_rules(ctx):
    m = mod(ctx.guild.id, "automod")
    lines = [f"`{r['id']}` {'🟢' if r['enabled'] else '🔴'} **{r['nome']}** · {r['tipo']} `{r['pattern']}` → {r['azione']}" for r in m["rules"]]
    await ctx.send(f"**Automod** ({'attivo' if m['enabled'] else 'disattivo'})\n" + ("\n".join(lines) or "Nessuna regola."))


@impl("automod rule-create")
async def _automod_create(ctx, nome: str, pattern: str, tipo: str = "parola", azione: str = "elimina"):
    if tipo not in ("parola", "regex", "link") or azione not in ("elimina", "avvisa", "timeout"):
        raise fail("Tipo: parola, regex, link — Azione: elimina, avvisa, timeout.")
    m = mod(ctx.guild.id, "automod")
    rid = nid(ctx.guild.id, "automod")
    m["rules"].append({"id": rid, "nome": nome, "tipo": tipo, "pattern": pattern, "azione": azione, "enabled": True})
    save()
    await ctx.send(f"✅ Regola `{rid}` creata. Ricorda di attivare l'automod con `/automod enable`.")


@impl("automod rule-edit")
async def _automod_edit(ctx, id: int, campo: str, *, valore: str):
    r = next((x for x in mod(ctx.guild.id, "automod")["rules"] if x["id"] == id), None)
    if not r:
        raise fail("Regola non trovata.")
    if campo == "attiva":
        r["enabled"] = yes(valore)
    elif campo in ("nome", "pattern", "tipo", "azione"):
        r[campo] = valore
    else:
        raise fail("Campi: nome, pattern, tipo, azione, attiva.")
    save()
    await ctx.send("✅ Regola aggiornata.")


@impl("automod rule-delete")
async def _automod_delete(ctx, id: int):
    m = mod(ctx.guild.id, "automod")
    n = len(m["rules"])
    m["rules"] = [r for r in m["rules"] if r["id"] != id]
    save()
    await ctx.send("🗑️ Regola eliminata." if len(m["rules"]) < n else "Regola non trovata.")


# --- applicazione dei controlli ai messaggi
URL_RE = re.compile(r"(?:https?://|www\.)([^\s/<>]+)[^\s]*", re.I)
INVITE_RE = re.compile(r"(?:discord\.gg|discord(?:app)?\.com/invite)/[\w-]+", re.I)
EMOJI_RE = re.compile(r"<a?:\w+:\d+>|[\U0001F300-\U0001FAFF\u2600-\u27BF]")
_spam, _last_msg = {}, {}


def exempt(message):
    m = message.author
    if is_staff(m):
        return True
    sc = sec(message.guild.id, "security")
    if m.id in sc.get("trusted_users", []) or any(r.id in sc.get("trusted_roles", []) for r in m.roles):
        return True
    al = sc.get("allowlist", [])
    return message.channel.id in al or m.id in al or any(r.id in al for r in m.roles)


def check_message(message):
    gid, text = message.guild.id, message.content or ""
    low = text.lower()
    al = [x for x in sec(gid, "security").get("allowlist", []) if isinstance(x, str)]
    m = mod(gid, "badwords")
    if m["enabled"] and any(w in low for w in m["parole"]):
        return "badwords", "parola vietata", m["azione"]
    m = mod(gid, "filter")
    if m["enabled"]:
        for r in m["regole"]:
            if _match(r["pattern"], text):
                return "filter", f"filtro «{r['pattern']}»", r["azione"]
    m = mod(gid, "automod")
    if m["enabled"]:
        for r in m["rules"]:
            if not r["enabled"]:
                continue
            hit = (r["tipo"] == "parola" and r["pattern"].lower() in low) or \
                  (r["tipo"] == "regex" and _match("re:" + r["pattern"], text)) or \
                  (r["tipo"] == "link" and any(d.lower().endswith(r["pattern"].lower()) for d in URL_RE.findall(text)))
            if hit:
                return "automod", f"regola «{r['nome']}»", r["azione"]
    m = mod(gid, "invite-filter")
    if m["enabled"] and INVITE_RE.search(text):
        return "invite-filter", "invito Discord", m["azione"]
    domains = URL_RE.findall(text)
    m = mod(gid, "antilink")
    if m["enabled"] and domains:
        ok = m["domini"] + al
        if any(not any(d.lower().endswith(x.lower()) for x in ok) for d in domains):
            return "antilink", "link non consentito", m["azione"]
    m = mod(gid, "link-filter")
    if m["enabled"] and any(d.lower().endswith(b) for d in domains for b in m["bloccati"]):
        return "link-filter", "dominio bloccato", m["azione"]
    m = mod(gid, "caps-filter")
    letters = [c for c in text if c.isalpha()]
    if m["enabled"] and len(letters) >= m["minimo"] and sum(c.isupper() for c in letters) * 100 / len(letters) >= m["percentuale"]:
        return "caps-filter", "troppe maiuscole", m["azione"]
    m = mod(gid, "emoji-limit")
    if m["enabled"] and len(EMOJI_RE.findall(text)) > m["massimo"]:
        return "emoji-limit", "troppe emoji", m["azione"]
    m = mod(gid, "mention-limit")
    if m["enabled"] and len(message.mentions) + len(message.role_mentions) > m["massimo"]:
        return "mention-limit", "troppe menzioni", m["azione"]
    m = mod(gid, "attachment-filter")
    if m["enabled"]:
        for a in message.attachments:
            ext = a.filename.rsplit(".", 1)[-1].lower() if "." in a.filename else ""
            if ext in m["estensioni"] or (m["max_mb"] and a.size > m["max_mb"] * 1048576):
                return "attachment-filter", "allegato non consentito", m["azione"]
    m = mod(gid, "duplicate-filter")
    if m["enabled"] and text.strip():
        k = (gid, message.author.id)
        last, n = _last_msg.get(k, ("", 0))
        n = n + 1 if last == low else 1
        _last_msg[k] = (low, n)
        if n >= m["ripetizioni"]:
            return "duplicate-filter", "messaggio ripetuto", m["azione"]
    m = mod(gid, "antispam")
    if m["enabled"]:
        ts = _spam.setdefault((gid, message.author.id), [])
        ts.append(time.time())
        while ts and ts[0] < time.time() - m["secondi"]:
            ts.pop(0)
        if len(ts) >= m["messaggi"]:
            ts.clear()
            return "antispam", "spam", m["azione"]
    return None


async def apply_action(message, rule, reason, action):
    gid = message.guild.id
    try:
        await message.delete()
    except discord.HTTPException:
        pass
    await safe_send(message.channel, content=f"🛡️ {message.author.mention}, messaggio rimosso ({reason}).",
                    delete_after=6, allowed_mentions=discord.AllowedMentions(users=True))
    if action in ("avvisa", "timeout"):
        case_raw(gid, "warn" if action == "avvisa" else "timeout", message.author.id, bot.user.id, f"[{rule}] {reason}")
    if action == "timeout":
        mins = mod(gid, rule).get("minuti", 5) if rule in MODS else 5
        try:
            await message.author.timeout(timedelta(minutes=mins), reason=f"{rule}: {reason}")
        except discord.HTTPException:
            pass
    log_event(message.guild, "automod", f"{rule}: {reason} — {message.author} in #{message.channel}", message.author.id)


@bot.listen("on_message")
async def _automod_listener(message):
    if message.guild is None or message.author.bot or not isinstance(message.author, discord.Member):
        return
    if exempt(message):
        return
    hit = check_message(message)
    if hit:
        await apply_action(message, *hit)


# --- raid protection e lockdown
async def do_lockdown(guild, on):
    st = sec(guild.id, "lockdown")
    role = guild.default_role
    if on:
        done = []
        for ch in guild.text_channels:
            ow = ch.overwrites_for(role)
            if ow.send_messages is False:
                continue
            ow.send_messages = False
            try:
                await ch.set_permissions(role, overwrite=ow, reason="Lockdown")
                done.append(ch.id)
            except discord.HTTPException:
                pass
        st.update(on=True, channels=done, since=now())
    else:
        for cid in st.get("channels", []):
            ch = guild.get_channel(cid)
            if not ch:
                continue
            ow = ch.overwrites_for(role)
            ow.send_messages = None
            try:
                await ch.set_permissions(role, overwrite=None if ow.is_empty() else ow, reason="Fine lockdown")
            except discord.HTTPException:
                pass
        st.update(on=False, channels=[])
    save()


_joins = {}


@bot.listen("on_member_join")
async def _raid_listener(member):
    m = mod(member.guild.id, "raid-protection")
    if not m["enabled"]:
        return
    ts = _joins.setdefault(member.guild.id, [])
    ts.append(time.time())
    while ts and ts[0] < time.time() - m["secondi"]:
        ts.pop(0)
    if len(ts) >= m["ingressi"]:
        if m["azione"] == "kick":
            try:
                await member.kick(reason="Raid protection")
            except discord.HTTPException:
                pass
        elif m["azione"] == "lockdown" and not sec(member.guild.id, "lockdown").get("on"):
            await do_lockdown(member.guild, True)
        log_event(member.guild, "automod", f"🚨 Raid sospetto: {len(ts)} ingressi in {m['secondi']}s → {m['azione']}")


@impl("raid-protection status")
async def _raid_status(ctx):
    m = mod(ctx.guild.id, "raid-protection")
    st = sec(ctx.guild.id, "lockdown")
    await ctx.send(embed=emb("🚨 Raid protection", (
        f"Stato: {'🟢 attiva' if m['enabled'] else '🔴 disattiva'}\n"
        f"Soglia: {m['ingressi']} ingressi in {m['secondi']}s → {m['azione']}\n"
        f"Lockdown: {'🔒 attivo' if st.get('on') else '🔓 non attivo'}")))


# --- security
def _sc_list(kind, title, resolver):
    async def cmd(ctx, azione: str = "lista", elemento: Optional[str] = None):
        sc = sec(ctx.guild.id, "security")
        lst = sc.setdefault(kind, [])
        a = azione.lower()
        if a in ("lista", "list"):
            return await ctx.send(f"**{title}:** " + (", ".join(resolver(ctx.guild, i) for i in lst) or "nessuno"), allowed_mentions=NOMENTION)
        ids = ids_in(elemento)
        if not ids:
            raise fail("Indica un utente/ruolo (menzione o ID).")
        if a in ("add", "aggiungi"):
            lst.extend(i for i in ids if i not in lst)
        elif a in ("remove", "rimuovi"):
            sc[kind] = [i for i in lst if i not in ids]
        else:
            raise fail("Azione: lista, add, remove.")
        save()
        await ctx.send("✅ Fatto.")
    return cmd


IMPL["security trusted-roles"] = _sc_list("trusted_roles", "Ruoli fidati", lambda g, i: f"<@&{i}>")
IMPL["security trusted-users"] = _sc_list("trusted_users", "Utenti fidati", lambda g, i: f"<@{i}>")


@impl("security allowlist-add")
async def _al_add(ctx, *, elemento: str):
    al = sec(ctx.guild.id, "security").setdefault("allowlist", [])
    new = ids_in(elemento) or [elemento.strip().lower()]
    al.extend(x for x in new if x not in al)
    save()
    await ctx.send("✅ Aggiunto alla lista consentita (utente, ruolo, canale o dominio).")


@impl("security allowlist-remove")
async def _al_remove(ctx, *, elemento: str):
    sc = sec(ctx.guild.id, "security")
    rem = ids_in(elemento) or [elemento.strip().lower()]
    sc["allowlist"] = [x for x in sc.get("allowlist", []) if x not in rem]
    save()
    await ctx.send("🗑️ Rimosso dalla lista consentita.")


@impl("security allowlist")
async def _al_show(ctx):
    al = sec(ctx.guild.id, "security").get("allowlist", [])
    await ctx.send("**Lista consentita:** " + (", ".join(f"<#{x}>/<@{x}>" if isinstance(x, int) else f"`{x}`" for x in al) or "vuota"),
                   allowed_mentions=NOMENTION)


@impl("security status")
async def _sec_status(ctx):
    on = [n for n in ("antispam", "antilink", "badwords", "filter", "caps-filter", "invite-filter", "emoji-limit",
                      "duplicate-filter", "link-filter", "attachment-filter", "mention-limit", "raid-protection", "automod")
          if mod(ctx.guild.id, n)["enabled"]]
    st = sec(ctx.guild.id, "lockdown")
    await ctx.send(embed=emb("🔐 Stato sicurezza", f"Protezioni attive ({len(on)}): " + (", ".join(on) or "nessuna") +
                             f"\nLockdown: {'🔒 attivo' if st.get('on') else '🔓 no'}"))


@impl("security setup")
async def _sec_setup(ctx):
    for n in ("antispam", "invite-filter", "mention-limit", "raid-protection", "badwords"):
        mod(ctx.guild.id, n)["enabled"] = True
    save()
    await ctx.send("🛡️ Protezioni base attivate: antispam, invite-filter, mention-limit, raid-protection, badwords. "
                   "Affina con i comandi `/antispam settings`, `/raid-protection settings`, ecc.")


@impl("security test")
async def _sec_test(ctx):
    me = ctx.guild.me.guild_permissions
    checks = [("Manage Messages", me.manage_messages), ("Moderate Members", me.moderate_members),
              ("Manage Channels", me.manage_channels), ("Manage Roles", me.manage_roles),
              ("Kick Members", me.kick_members), ("Ban Members", me.ban_members)]
    await ctx.send(embed=emb("🧪 Test sicurezza", "\n".join(f"{'✅' if v else '❌'} {k}" for k, v in checks)))


@impl("security lockdown")
async def _sec_lockdown(ctx):
    await ctx.defer() if ctx.interaction else None
    await do_lockdown(ctx.guild, True)
    log_event(ctx.guild, "automod", f"🔒 Lockdown attivato da {ctx.author}")
    await ctx.send("🔒 **Lockdown attivo**: scrittura bloccata per @everyone in tutti i canali di testo.")


@impl("security unlock")
async def _sec_unlock(ctx):
    await ctx.defer() if ctx.interaction else None
    await do_lockdown(ctx.guild, False)
    log_event(ctx.guild, "automod", f"🔓 Lockdown disattivato da {ctx.author}")
    await ctx.send("🔓 Lockdown disattivato: permessi ripristinati.")


# ------------------------------------------------------------ LOG E AUDIT
@impl("logs setup")
async def _logs_setup(ctx, canale: discord.TextChannel, categoria: str = "all"):
    if categoria != "all" and categoria not in LOG_KINDS:
        raise fail("Categorie: all, " + ", ".join(LOG_KINDS))
    cfg = sec(ctx.guild.id, "logcfg")
    cfg[categoria] = canale.id
    cfg["off"] = [k for k in cfg.get("off", []) if k != categoria]
    save()
    await ctx.send(f"✅ Log `{categoria}` inviati in {canale.mention}.")


@impl("logs disable")
async def _logs_disable(ctx, categoria: str = "all"):
    cfg = sec(ctx.guild.id, "logcfg")
    if categoria == "all":
        cfg["off"] = list(LOG_KINDS)
    elif categoria in LOG_KINDS:
        cfg.setdefault("off", []).append(categoria)
    else:
        raise fail("Categorie: all, " + ", ".join(LOG_KINDS))
    save()
    await ctx.send(f"🔴 Log `{categoria}` disattivati.")


@impl("logs test")
async def _logs_test(ctx):
    log_event(ctx.guild, "messages", f"Test log richiesto da {ctx.author}", ctx.author.id)
    cfg = sec(ctx.guild.id, "logcfg")
    ch = ctx.guild.get_channel(cfg.get("messages") or cfg.get("all") or 0)
    await ctx.send(f"✅ Test registrato. Canale log: {ch.mention if ch else 'non impostato (usa /logs setup)'}")


def _fmt_logs(entries):
    return [f"<t:{e['ts']}:T> `{e['kind']}` {e['text']}" for e in entries]


async def _show_logs(ctx, entries, n):
    entries = entries[-max(1, min(n, 50)):]
    for page in chunk_send_lines(list(reversed(_fmt_logs(entries)))):
        await ctx.send(page, allowed_mentions=NOMENTION)


@impl("logs recent")
async def _logs_recent(ctx, quanti: int = 15):
    await _show_logs(ctx, sec(ctx.guild.id, "logs", list), quanti)


@impl("logs search")
async def _logs_search(ctx, *, testo: str):
    await _show_logs(ctx, [e for e in sec(ctx.guild.id, "logs", list) if testo.lower() in e["text"].lower()], 20)


@impl("logs member")
async def _logs_member(ctx, member: discord.User):
    await _show_logs(ctx, [e for e in sec(ctx.guild.id, "logs", list) if e["user"] == member.id], 20)


def _logs_kind(kind):
    async def cmd(ctx, quanti: int = 15):
        await _show_logs(ctx, [e for e in sec(ctx.guild.id, "logs", list) if e["kind"] == kind], quanti)
    return cmd


for _k in LOG_KINDS:
    IMPL[f"logs {_k}"] = _logs_kind(_k)


@bot.listen("on_message_delete")
async def _log_msg_delete(message):
    if message.guild and not message.author.bot:
        log_event(message.guild, "messages", f"🗑️ Messaggio di {message.author} eliminato in #{message.channel}: {message.content[:200]}", message.author.id)


@bot.listen("on_message_edit")
async def _log_msg_edit(before, after):
    if after.guild and not after.author.bot and before.content != after.content:
        log_event(after.guild, "messages", f"✏️ {after.author} ha modificato un messaggio in #{after.channel}: «{before.content[:120]}» → «{after.content[:120]}»", after.author.id)


@bot.listen("on_member_update")
async def _log_member_update(before, after):
    if before.roles != after.roles:
        add = [r.name for r in after.roles if r not in before.roles]
        rem = [r.name for r in before.roles if r not in after.roles]
        log_event(after.guild, "roles", f"{after}: " + (f"+{', +'.join(add)} " if add else "") + (f"-{', -'.join(rem)}" if rem else ""), after.id)


@bot.listen("on_guild_channel_create")
async def _log_ch_create(ch):
    log_event(ch.guild, "channels", f"➕ Canale creato: #{ch.name}")


@bot.listen("on_guild_channel_delete")
async def _log_ch_delete(ch):
    log_event(ch.guild, "channels", f"➖ Canale eliminato: #{ch.name}")


@bot.listen("on_voice_state_update")
async def _log_voice(member, before, after):
    if before.channel != after.channel:
        what = f"entra in {after.channel}" if after.channel and not before.channel else \
               f"esce da {before.channel}" if before.channel and not after.channel else f"{before.channel} → {after.channel}"
        log_event(member.guild, "voice", f"🔊 {member} {what}", member.id)


@bot.listen("on_member_join")
async def _log_join(member):
    log_event(member.guild, "joins", f"📥 {member} è entrato (account creato <t:{int(member.created_at.timestamp())}:R>)", member.id)


@bot.listen("on_member_remove")
async def _log_leave(member):
    log_event(member.guild, "leaves", f"📤 {member} ha lasciato il server", member.id)


async def _audit_lines(guild, n, **kw):
    out = []
    async for e in guild.audit_logs(limit=max(1, min(n, 100)), **kw):
        out.append(f"<t:{int(e.created_at.timestamp())}:T> **{str(e.action).split('.')[-1]}** · {e.user} → {e.target} " + (f"· {e.reason}" if e.reason else ""))
    return out


@impl("audit latest")
async def _audit_latest(ctx, quanti: int = 10):
    for p in chunk_send_lines(await _audit_lines(ctx.guild, quanti) or ["Nessun evento."]):
        await ctx.send(p, allowed_mentions=NOMENTION)


@impl("audit search")
async def _audit_search(ctx, azione: str, quanti: int = 10):
    act = getattr(discord.AuditLogAction, azione.lower().replace("-", "_"), None)
    if act is None:
        raise fail("Azione sconosciuta. Esempi: ban, kick, member_update, role_create, channel_delete, message_delete.")
    for p in chunk_send_lines(await _audit_lines(ctx.guild, quanti, action=act) or ["Nessun evento."]):
        await ctx.send(p, allowed_mentions=NOMENTION)


@impl("audit member")
async def _audit_member(ctx, member: discord.User, quanti: int = 10):
    for p in chunk_send_lines(await _audit_lines(ctx.guild, quanti, user=member) or ["Nessun evento."]):
        await ctx.send(p, allowed_mentions=NOMENTION)


@impl("audit export")
async def _audit_export(ctx, quanti: int = 100):
    lines = await _audit_lines(ctx.guild, quanti)
    f = discord.File(io.BytesIO("\n".join(lines).encode("utf-8")), filename="audit.txt")
    await ctx.send("📤 Esportazione audit log:", file=f, ephemeral=True)


# ============================================================================
# ESTENSIONE 2: ruoli, canali, thread, messaggi, embed, annunci, sondaggi,
#               benvenuto/addio, promemoria e programmazioni
# ============================================================================
def role_ok(ctx, ruolo):
    if ruolo.is_default() or ruolo.managed or ruolo >= ctx.guild.me.top_role or \
            (ctx.author.id != ctx.guild.owner_id and ruolo >= ctx.author.top_role):
        raise fail("Quel ruolo è @everyone, gestito da un'integrazione o sopra di te/del bot in gerarchia.")


@impl("role edit")
async def _role_edit(ctx, ruolo: discord.Role, nome: Optional[str] = None, colore: Optional[str] = None,
                     separato: Optional[bool] = None, menzionabile: Optional[bool] = None):
    role_ok(ctx, ruolo)
    kw = {}
    if nome:
        kw["name"] = nome
    if colore:
        kw["colour"] = discord.Colour(pcolor(colore))
    if separato is not None:
        kw["hoist"] = separato
    if menzionabile is not None:
        kw["mentionable"] = menzionabile
    if not kw:
        raise fail("Indica almeno un campo da modificare.")
    await ruolo.edit(**kw)
    await ctx.send(f"✅ Ruolo {ruolo.name} modificato.")


@impl("role clone")
async def _role_clone(ctx, ruolo: discord.Role, nome: Optional[str] = None):
    n = await ctx.guild.create_role(name=nome or f"{ruolo.name} (copia)", permissions=ruolo.permissions,
                                    colour=ruolo.colour, hoist=ruolo.hoist, mentionable=ruolo.mentionable)
    await ctx.send(f"✅ Creato {n.mention}", allowed_mentions=NOMENTION)


@impl("role color")
async def _role_color(ctx, ruolo: discord.Role, colore: str):
    role_ok(ctx, ruolo)
    await ruolo.edit(colour=discord.Colour(pcolor(colore)))
    await ctx.send("🎨 Colore aggiornato.")


@impl("role icon")
async def _role_icon(ctx, ruolo: discord.Role, immagine: discord.Attachment):
    role_ok(ctx, ruolo)
    try:
        await ruolo.edit(display_icon=await immagine.read())
    except discord.HTTPException:
        raise fail("Discord ha rifiutato l'icona (servono i boost del server o l'immagine non è valida).")
    await ctx.send("🖼️ Icona aggiornata.")


@impl("role hoist")
async def _role_hoist(ctx, ruolo: discord.Role, valore: bool = True):
    role_ok(ctx, ruolo)
    await ruolo.edit(hoist=valore)
    await ctx.send("✅ Fatto.")


@impl("role mentionable")
async def _role_mentionable(ctx, ruolo: discord.Role, valore: bool = True):
    role_ok(ctx, ruolo)
    await ruolo.edit(mentionable=valore)
    await ctx.send("✅ Fatto.")


@impl("role reorder")
async def _role_reorder(ctx, ruolo: discord.Role, posizione: int):
    role_ok(ctx, ruolo)
    await ruolo.edit(position=max(1, min(posizione, ctx.guild.me.top_role.position - 1)))
    await ctx.send("↕️ Posizione aggiornata.")


@job("role")
async def _job_role(gid, j):
    g = bot.get_guild(gid)
    m, r = (g.get_member(j["user"]), g.get_role(j["role"])) if g else (None, None)
    if m and r:
        try:
            await (m.add_roles if j["op"] == "add" else m.remove_roles)(r, reason="Operazione programmata")
        except discord.HTTPException:
            pass


@impl("role temporary")
async def _role_temporary(ctx, member: discord.Member, ruolo: discord.Role, durata: str):
    role_ok(ctx, ruolo)
    d = pdur(durata)
    if not d:
        raise fail("Durata non valida (es. 30m, 2h, 7d).")
    await member.add_roles(ruolo, reason=f"Temporaneo per {fdur(d)}")
    add_job(ctx.guild.id, "role", now() + d, user=member.id, role=ruolo.id, op="remove")
    await ctx.send(f"⏳ {ruolo.name} assegnato a {member.display_name} per {fdur(d)}.")


@impl("role schedule")
async def _role_schedule(ctx, member: discord.Member, ruolo: discord.Role, azione: str, quando: str):
    role_ok(ctx, ruolo)
    op = {"add": "add", "aggiungi": "add", "remove": "remove", "rimuovi": "remove"}.get(azione.lower())
    at = pwhen(quando, ctx.guild.id)
    if not op or not at:
        raise fail("Azione: add/remove — Quando: 30m, 2h, 1d oppure HH:MM.")
    j = add_job(ctx.guild.id, "role", at, user=member.id, role=ruolo.id, op=op)
    await ctx.send(f"🗓️ Programmato (#{j['id']}): {op} {ruolo.name} a {member.display_name} <t:{at}:R>.")


# --------------------------------------------------------------- CANALI
@impl("category create")
async def _category_create(ctx, nome: str):
    c = await ctx.guild.create_category(nome)
    await ctx.send(f"✅ Categoria **{c.name}** creata.")


@impl("category delete")
async def _category_delete(ctx, categoria: discord.CategoryChannel):
    await categoria.delete()
    await ctx.send("🗑️ Categoria eliminata (i canali al suo interno restano).")


@impl("category rename")
async def _category_rename(ctx, categoria: discord.CategoryChannel, nome: str):
    await categoria.edit(name=nome)
    await ctx.send("✅ Rinominata.")


@impl("category move")
async def _category_move(ctx, categoria: discord.CategoryChannel, posizione: int):
    await categoria.edit(position=max(0, posizione - 1))
    await ctx.send("↕️ Categoria spostata.")


def _thread_of(ctx, thread):
    t = thread or (ctx.channel if isinstance(ctx.channel, discord.Thread) else None)
    if t is None:
        raise fail("Indica un thread (o usa il comando dentro un thread).")
    return t


@impl("thread create")
async def _thread_create(ctx, nome: str):
    if not isinstance(ctx.channel, discord.TextChannel):
        raise fail("Usa il comando in un canale di testo.")
    t = await ctx.channel.create_thread(name=nome, type=discord.ChannelType.public_thread)
    await ctx.send(f"🧵 Thread creato: {t.mention}")


@impl("thread archive")
async def _thread_archive(ctx, thread: Optional[discord.Thread] = None):
    await ctx.send("📦 Thread archiviato.")
    await _thread_of(ctx, thread).edit(archived=True)


@impl("thread unarchive")
async def _thread_unarchive(ctx, thread: Optional[discord.Thread] = None):
    await _thread_of(ctx, thread).edit(archived=False)
    await ctx.send("📂 Thread riaperto.")


@impl("thread lock")
async def _thread_lock(ctx, thread: Optional[discord.Thread] = None):
    await _thread_of(ctx, thread).edit(locked=True)
    await ctx.send("🔒 Thread bloccato.")


@impl("thread unlock")
async def _thread_unlock(ctx, thread: Optional[discord.Thread] = None):
    await _thread_of(ctx, thread).edit(locked=False)
    await ctx.send("🔓 Thread sbloccato.")


@impl("thread list")
async def _thread_list(ctx):
    await ctx.send("\n".join(f"🧵 {t.mention}" + (" 🔒" if t.locked else "") for t in ctx.guild.threads)[:1900] or "Nessun thread attivo.")


# --------------------------------------------------------------- MESSAGGI
def _ack(ctx, text="✅ Fatto."):
    return ctx.send(text, ephemeral=True) if ctx.interaction else asyncio.sleep(0)


@impl("message send")
async def _message_send(ctx, canale: discord.TextChannel, *, testo: str):
    await canale.send(testo.replace("\\n", "\n"), allowed_mentions=discord.AllowedMentions(users=True, roles=True))
    await _ack(ctx, f"✅ Inviato in {canale.mention}.")


@impl("message repeat")
async def _message_repeat(ctx, *, testo: str):
    if ctx.interaction is None:
        try:
            await ctx.message.delete()
        except discord.HTTPException:
            pass
    await ctx.channel.send(testo.replace("\\n", "\n"), allowed_mentions=discord.AllowedMentions(users=True))
    await _ack(ctx)


@impl("message edit")
async def _message_edit(ctx, riferimento: str, *, testo: str):
    m = await get_msg(ctx, riferimento)
    if m.author.id != bot.user.id:
        raise fail("Posso modificare solo i miei messaggi.")
    await m.edit(content=testo.replace("\\n", "\n"))
    await ctx.send("✏️ Messaggio modificato.", ephemeral=True)


@impl("message delete")
async def _message_delete(ctx, riferimento: str):
    await (await get_msg(ctx, riferimento)).delete()
    await ctx.send("🗑️ Messaggio eliminato.", ephemeral=True)


@impl("message pin")
async def _message_pin(ctx, riferimento: str):
    await (await get_msg(ctx, riferimento)).pin()
    await ctx.send("📌 Messaggio fissato.", ephemeral=True)


@impl("message unpin")
async def _message_unpin(ctx, riferimento: str):
    await (await get_msg(ctx, riferimento)).unpin()
    await ctx.send("📍 Messaggio rimosso dai fissati.", ephemeral=True)


@impl("message quote")
async def _message_quote(ctx, riferimento: str):
    m = await get_msg(ctx, riferimento)
    e = discord.Embed(description=m.content[:3900] or "*(nessun testo)*", color=m.author.color, timestamp=m.created_at)
    e.set_author(name=m.author.display_name, icon_url=m.author.display_avatar.url)
    e.add_field(name="Originale", value=f"[Vai al messaggio]({m.jump_url})")
    await ctx.send(embed=e)


# --- embed
def _draft(ctx):
    return sec(ctx.guild.id, "embed_drafts").setdefault(str(ctx.author.id), {})


def build_embed(d):
    e = discord.Embed(title=d.get("title") or None, description=(d.get("description") or "").replace("\\n", "\n") or None,
                      color=pcolor(d.get("color", "blu")))
    if d.get("footer"):
        e.set_footer(text=d["footer"])
    if d.get("image"):
        e.set_image(url=d["image"])
    if d.get("thumbnail"):
        e.set_thumbnail(url=d["thumbnail"])
    if d.get("author"):
        e.set_author(name=d["author"])
    if not (e.title or e.description or d.get("image")):
        e.description = "*(embed vuoto: usa `/embed edit`)*"
    return e


@impl("embed create")
async def _embed_create(ctx, titolo: str, descrizione: str = "", colore: str = "blu", immagine: Optional[str] = None,
                        footer: Optional[str] = None):
    sec(ctx.guild.id, "embed_drafts")[str(ctx.author.id)] = {"title": titolo, "description": descrizione, "color": colore,
                                                            "image": immagine, "footer": footer}
    save()
    await ctx.send("📝 Bozza creata. Anteprima:", embed=build_embed(_draft(ctx)))


@impl("embed edit")
async def _embed_edit(ctx, campo: str, *, valore: str):
    if campo not in ("title", "description", "color", "footer", "image", "thumbnail", "author"):
        raise fail("Campi: title, description, color, footer, image, thumbnail, author.")
    _draft(ctx)[campo] = valore
    save()
    await ctx.send("✏️ Bozza aggiornata:", embed=build_embed(_draft(ctx)))


@impl("embed preview")
async def _embed_preview(ctx):
    await ctx.send(embed=build_embed(_draft(ctx)))


@impl("embed publish")
async def _embed_publish(ctx, canale: Optional[discord.TextChannel] = None):
    d = _draft(ctx)
    if not d:
        raise fail("Non hai nessuna bozza: usa `/embed create`.")
    await (canale or ctx.channel).send(embed=build_embed(d))
    await _ack(ctx, "📣 Embed pubblicato.")


@impl("embed template-save")
async def _embed_tsave(ctx, nome: str):
    if not _draft(ctx):
        raise fail("Non hai nessuna bozza da salvare.")
    sec(ctx.guild.id, "embed_templates")[nome.lower()] = copy.deepcopy(_draft(ctx))
    save()
    await ctx.send(f"💾 Modello `{nome}` salvato.")


@impl("embed template-load")
async def _embed_tload(ctx, nome: str):
    t = sec(ctx.guild.id, "embed_templates").get(nome.lower())
    if not t:
        raise fail("Modello non trovato.")
    sec(ctx.guild.id, "embed_drafts")[str(ctx.author.id)] = copy.deepcopy(t)
    save()
    await ctx.send("📂 Modello caricato nella tua bozza:", embed=build_embed(t))


@impl("embed template-delete")
async def _embed_tdelete(ctx, nome: str):
    if sec(ctx.guild.id, "embed_templates").pop(nome.lower(), None) is None:
        raise fail("Modello non trovato.")
    save()
    await ctx.send("🗑️ Modello eliminato.")


@impl("embed list-templates")
async def _embed_tlist(ctx):
    await ctx.send("**Modelli:** " + (", ".join(f"`{k}`" for k in sec(ctx.guild.id, "embed_templates")) or "nessuno"))


# --- annunci
@impl("announcement send")
async def _announce_send(ctx, canale: discord.TextChannel, titolo: str, *, testo: str):
    e = emb(titolo, testo.replace("\\n", "\n"))
    e.set_footer(text=f"Annuncio di {ctx.author.display_name}")
    await canale.send(embed=e)
    topic = sec(ctx.guild.id, "notifications").get("annunci")
    sent = 0
    for uid in (topic or {}).get("subs", []):
        u = ctx.guild.get_member(uid)
        if u:
            try:
                await u.send(embed=e)
                sent += 1
            except discord.HTTPException:
                pass
    await _ack(ctx, f"📣 Annuncio inviato in {canale.mention}" + (f" e a {sent} iscritti." if sent else "."))


@job("announce")
async def _job_announce(gid, j):
    g = bot.get_guild(gid)
    ch = g.get_channel(j["channel"]) if g else None
    if ch:
        await safe_send(ch, embed=emb(j["title"], j["text"]))


@impl("announcement schedule")
async def _announce_schedule(ctx, canale: discord.TextChannel, quando: str, titolo: str, *, testo: str):
    at = pwhen(quando, ctx.guild.id)
    if not at:
        raise fail("Quando: 30m, 2h, 1d oppure HH:MM.")
    j = add_job(ctx.guild.id, "announce", at, channel=canale.id, title=titolo, text=testo.replace("\\n", "\n"))
    await ctx.send(f"🗓️ Annuncio #{j['id']} programmato <t:{at}:R> in {canale.mention}.")


@impl("announcement cancel")
async def _announce_cancel(ctx, id: int):
    await ctx.send("✅ Annuncio annullato." if del_job(ctx.guild.id, id, "announce") else "Annuncio non trovato.")


@impl("announcement list")
async def _announce_list(ctx):
    await ctx.send("\n".join(f"`{j['id']}` <t:{j['at']}:R> in <#{j['channel']}> — {j['title']}" for j in jobs_of(ctx.guild.id, "announce")) or "Nessun annuncio programmato.")


# --- sondaggi
NUM_EMOJI = ["1️⃣", "2️⃣", "3️⃣", "4️⃣", "5️⃣", "6️⃣", "7️⃣", "8️⃣", "9️⃣", "🔟"]


async def _poll_tally(guild, p):
    ch = guild.get_channel(p["channel"])
    m = await ch.fetch_message(p["message"])
    counts = {str(r.emoji): r.count - (1 if r.me else 0) for r in m.reactions}
    return [(o, counts.get(p["emojis"][i], 0)) for i, o in enumerate(p["options"])]


def _tally_text(res):
    tot = sum(n for _, n in res) or 1
    return "\n".join(f"{NUM_EMOJI[i] if i < 10 else ''} **{o}** — {n} ({n * 100 // tot}%)" for i, (o, n) in enumerate(res))


async def _make_poll(ctx, domanda, opzioni, durata, kind="poll"):
    opts = [o.strip() for o in opzioni.split(";") if o.strip()][:10]
    emojis = NUM_EMOJI[:len(opts)] if opts else ["✅", "❌"]
    opts = opts or ["Sì", "No"]
    d = pdur(durata) if durata else None
    e = emb(f"📊 {domanda}", "\n".join(f"{emojis[i]} {o}" for i, o in enumerate(opts)))
    if d:
        e.set_footer(text=f"Chiude tra {fdur(d)}")
    msg = await ctx.channel.send(embed=e)
    for em in emojis:
        await msg.add_reaction(em)
    pid = nid(ctx.guild.id, "poll")
    sec(ctx.guild.id, "polls")[str(pid)] = {"id": pid, "q": domanda, "options": opts, "emojis": emojis, "channel": ctx.channel.id,
                                            "message": msg.id, "open": True, "by": ctx.author.id, "kind": kind}
    if d:
        add_job(ctx.guild.id, "poll_close", now() + d, poll=pid)
    save()
    await _ack(ctx, f"✅ Sondaggio `{pid}` creato.")


@impl("poll create")
async def _poll_create(ctx, domanda: str, opzioni: str = "", durata: Optional[str] = None):
    await _make_poll(ctx, domanda, opzioni, durata)


@impl("utility poll")
async def _utility_poll(ctx, *, domanda: str):
    await _make_poll(ctx, domanda, "", None)


async def _poll_close_now(guild, p):
    p["open"] = False
    save()
    try:
        res = await _poll_tally(guild, p)
        ch = guild.get_channel(p["channel"])
        await safe_send(ch, embed=emb(f"📊 Risultati: {p['q']}", _tally_text(res), 0x57F287))
    except discord.HTTPException:
        pass


@job("poll_close")
async def _job_poll_close(gid, j):
    g = bot.get_guild(gid)
    p = sec(gid, "polls").get(str(j["poll"]))
    if g and p and p["open"]:
        await _poll_close_now(g, p)


def _poll_get(ctx, id):
    p = sec(ctx.guild.id, "polls").get(str(id))
    if not p:
        raise fail("Sondaggio non trovato.")
    return p


@impl("poll close")
async def _poll_close(ctx, id: int):
    p = _poll_get(ctx, id)
    if p["by"] != ctx.author.id and not is_staff(ctx.author):
        raise fail("Solo l'autore o lo staff può chiudere il sondaggio.")
    if not p["open"]:
        raise fail("Sondaggio già chiuso.")
    await _poll_close_now(ctx.guild, p)
    await _ack(ctx, "🔒 Sondaggio chiuso.")


@impl("poll results")
async def _poll_results(ctx, id: int):
    p = _poll_get(ctx, id)
    try:
        await ctx.send(embed=emb(f"📊 {p['q']} ({'aperto' if p['open'] else 'chiuso'})", _tally_text(await _poll_tally(ctx.guild, p))))
    except (discord.NotFound, AttributeError):
        raise fail("Non trovo più il messaggio del sondaggio.")


@impl("poll list")
async def _poll_list(ctx):
    ps = [p for p in sec(ctx.guild.id, "polls").values() if p.get("kind", "poll") == "poll"]
    await ctx.send("\n".join(f"`{p['id']}` {'🟢' if p['open'] else '🔴'} {p['q']}" for p in list(ps)[-15:]) or "Nessun sondaggio.")


# --- reazioni
@impl("reaction add")
async def _reaction_add(ctx, riferimento: str, emoji: str):
    await (await get_msg(ctx, riferimento)).add_reaction(emoji)
    await ctx.send("✅ Reazione aggiunta.", ephemeral=True)


@impl("reaction remove")
async def _reaction_remove(ctx, riferimento: str, emoji: str, member: Optional[discord.Member] = None):
    await (await get_msg(ctx, riferimento)).remove_reaction(emoji, member or bot.user)
    await ctx.send("✅ Reazione rimossa.", ephemeral=True)


@impl("reaction clear")
async def _reaction_clear(ctx, riferimento: str, emoji: Optional[str] = None):
    m = await get_msg(ctx, riferimento)
    await (m.clear_reaction(emoji) if emoji else m.clear_reactions())
    await ctx.send("🧹 Reazioni rimosse.", ephemeral=True)


# --------------------------------------------------------------- BENVENUTO / ADDIO / VERIFICA
def fmt_msg(text, member):
    return (text.replace("{user}", member.mention).replace("{name}", member.display_name)
            .replace("{server}", member.guild.name).replace("{count}", str(member.guild.member_count)))


def _greet(key, default):
    async def setup(ctx, canale: discord.TextChannel, *, messaggio: Optional[str] = None):
        sec(ctx.guild.id, key).update(enabled=True, channel=canale.id, message=messaggio or default)
        save()
        await ctx.send(f"✅ `{key}` attivo in {canale.mention}. Segnaposto: {{user}}, {{name}}, {{server}}, {{count}}.")

    async def edit(ctx, *, messaggio: str):
        sec(ctx.guild.id, key)["message"] = messaggio
        save()
        await ctx.send("✏️ Messaggio aggiornato.")

    async def disable(ctx):
        sec(ctx.guild.id, key)["enabled"] = False
        save()
        await ctx.send(f"🔴 `{key}` disattivato.")

    async def preview(ctx):
        c = sec(ctx.guild.id, key)
        await ctx.send(fmt_msg(c.get("message", default), ctx.author), allowed_mentions=NOMENTION)

    async def test(ctx):
        c = sec(ctx.guild.id, key)
        ch = ctx.guild.get_channel(c.get("channel", 0))
        if not ch:
            raise fail(f"Canale non impostato: usa `/{key} setup`.")
        await ch.send(fmt_msg(c.get("message", default), ctx.author), allowed_mentions=NOMENTION)
        await ctx.send("✅ Test inviato.", ephemeral=True)
    return setup, edit, disable, preview, test


(IMPL["welcome setup"], IMPL["welcome edit"], IMPL["welcome disable"], IMPL["welcome preview"],
 IMPL["welcome test"]) = _greet("welcome", "Benvenuto {user} in **{server}**! Sei il membro n°{count} 🎉")
(IMPL["goodbye setup"], IMPL["goodbye edit"], IMPL["goodbye disable"], IMPL["goodbye preview"],
 _unused) = _greet("goodbye", "{name} ha lasciato **{server}**. Ciao! 👋")


@bot.listen("on_member_join")
async def _welcome_join(member):
    c = sec(member.guild.id, "welcome")
    ch = member.guild.get_channel(c.get("channel", 0)) if c.get("enabled") else None
    if ch and not member.bot:
        await safe_send(ch, content=fmt_msg(c["message"], member), allowed_mentions=discord.AllowedMentions(users=True))
        c["sent"] = c.get("sent", 0) + 1


@bot.listen("on_member_remove")
async def _goodbye_leave(member):
    c = sec(member.guild.id, "goodbye")
    ch = member.guild.get_channel(c.get("channel", 0)) if c.get("enabled") else None
    if ch:
        await safe_send(ch, content=fmt_msg(c["message"], member), allowed_mentions=NOMENTION)


class VerifyButton(discord.ui.DynamicItem[discord.ui.Button], template=r"sc:verify"):
    def __init__(self):
        super().__init__(discord.ui.Button(label="Verificati", emoji="✅", style=discord.ButtonStyle.success, custom_id="sc:verify"))

    @classmethod
    async def from_custom_id(cls, interaction, item, match, /):
        return cls()

    async def callback(self, interaction: discord.Interaction):
        c = sec(interaction.guild_id, "screening")
        role = interaction.guild.get_role(c.get("role", 0)) if c.get("enabled") else None
        if not role:
            return await interaction.response.send_message("La verifica non è attiva.", ephemeral=True)
        if role in interaction.user.roles:
            return await interaction.response.send_message("Sei già verificato.", ephemeral=True)
        try:
            await interaction.user.add_roles(role, reason="Verifica membri")
        except discord.Forbidden:
            return await interaction.response.send_message("Non posso assegnarti il ruolo: avvisa lo staff.", ephemeral=True)
        await interaction.response.send_message("✅ Verifica completata, benvenuto!", ephemeral=True)


@impl("member-screening setup")
async def _screening_setup(ctx, ruolo: discord.Role, canale: discord.TextChannel, *, testo: Optional[str] = None):
    role_ok(ctx, ruolo)
    v = discord.ui.View(timeout=None)
    v.add_item(VerifyButton())
    msg = await canale.send(embed=emb("✅ Verifica", testo or "Premi il pulsante per verificarti e accedere al server."), view=v)
    sec(ctx.guild.id, "screening").update(enabled=True, role=ruolo.id, channel=canale.id, message=msg.id)
    save()
    await ctx.send(f"✅ Verifica attiva in {canale.mention}: chi preme il pulsante riceve {ruolo.mention}.", allowed_mentions=NOMENTION)


@impl("member-screening test")
async def _screening_test(ctx):
    c = sec(ctx.guild.id, "screening")
    role = ctx.guild.get_role(c.get("role", 0))
    ok = role and role < ctx.guild.me.top_role
    await ctx.send(f"Stato: {'🟢 attiva' if c.get('enabled') else '🔴 disattiva'}\nRuolo: {role.mention if role else '❌ mancante'}\n"
                   f"Il bot può assegnarlo: {'✅' if ok else '❌'}", allowed_mentions=NOMENTION)


@impl("member-screening disable")
async def _screening_disable(ctx):
    sec(ctx.guild.id, "screening")["enabled"] = False
    save()
    await ctx.send("🔴 Verifica disattivata (il pannello resta ma non fa più nulla).")


# --------------------------------------------------------------- PROMEMORIA E PROGRAMMAZIONI
@job("remind")
async def _job_remind(gid, j):
    text = f"⏰ **Promemoria:** {j['text']}"
    u = bot.get_user(j["user"])
    try:
        if u:
            await u.send(text)
            return
    except discord.HTTPException:
        pass
    g = bot.get_guild(gid)
    await safe_send(g.get_channel(j.get("channel", 0)) if g else None, content=f"<@{j['user']}> {text}",
                    allowed_mentions=discord.AllowedMentions(users=True))


async def _remind_create(ctx, quando, testo):
    at = pwhen(quando, ctx.guild.id)
    if not at:
        raise fail("Quando: 30m, 2h, 1d oppure HH:MM.")
    j = add_job(ctx.guild.id, "remind", at, user=ctx.author.id, channel=ctx.channel.id, text=testo)
    await ctx.send(f"⏰ Promemoria `{j['id']}` impostato <t:{at}:R>.")


@impl("reminder create")
async def _reminder_create(ctx, quando: str, *, testo: str):
    await _remind_create(ctx, quando, testo)


@impl("utility reminder")
async def _utility_reminder(ctx, quando: str, *, testo: str):
    await _remind_create(ctx, quando, testo)


@impl("reminder edit")
async def _reminder_edit(ctx, id: int, quando: Optional[str] = None, *, testo: Optional[str] = None):
    j = next((x for x in jobs_of(ctx.guild.id, "remind") if x["id"] == id and x["user"] == ctx.author.id), None)
    if not j:
        raise fail("Promemoria non trovato (puoi modificare solo i tuoi).")
    if quando:
        at = pwhen(quando, ctx.guild.id)
        if not at:
            raise fail("Quando non valido.")
        j["at"] = at
    if testo:
        j["text"] = testo
    save()
    await ctx.send("✏️ Promemoria aggiornato.")


@impl("reminder delete")
async def _reminder_delete(ctx, id: int):
    j = next((x for x in jobs_of(ctx.guild.id, "remind") if x["id"] == id and x["user"] == ctx.author.id), None)
    if not j:
        raise fail("Promemoria non trovato.")
    del_job(ctx.guild.id, id, "remind")
    await ctx.send("🗑️ Promemoria eliminato.")


@impl("reminder list")
async def _reminder_list(ctx):
    mine = [j for j in jobs_of(ctx.guild.id, "remind") if j["user"] == ctx.author.id]
    await ctx.send("\n".join(f"`{j['id']}` <t:{j['at']}:R> — {j['text']}" for j in mine) or "Nessun promemoria.", ephemeral=True)


@impl("reminder test")
async def _reminder_test(ctx):
    try:
        await ctx.author.send("⏰ Test promemoria: i messaggi privati funzionano.")
        await ctx.send("✅ Ti ho scritto in privato.", ephemeral=True)
    except discord.HTTPException:
        await ctx.send("⚠️ Non riesco a scriverti in privato: attiva i DM dai membri del server.", ephemeral=True)


@job("smsg")
async def _job_smsg(gid, j):
    g = bot.get_guild(gid)
    await safe_send(g.get_channel(j["channel"]) if g else None, content=j["text"], allowed_mentions=discord.AllowedMentions(users=True, roles=True))


@impl("schedule message")
async def _schedule_message(ctx, canale: discord.TextChannel, quando: str, ripeti: str = "no", *, testo: str):
    at = pwhen(quando, ctx.guild.id)
    every = None if ripeti.lower() in ("no", "0", "-") else pdur(ripeti)
    if not at or (every is None and ripeti.lower() not in ("no", "0", "-")):
        raise fail("Quando/ripeti non validi (es. 2h, 18:30; ripeti: no, 1d, 1w).")
    j = add_job(ctx.guild.id, "smsg", at, channel=canale.id, text=testo.replace("\\n", "\n"), **({"every": every} if every else {}))
    await ctx.send(f"🗓️ Messaggio `{j['id']}` programmato <t:{at}:R> in {canale.mention}" + (f", ripetuto ogni {fdur(every)}." if every else "."))


@impl("schedule edit")
async def _schedule_edit(ctx, id: int, quando: Optional[str] = None, *, testo: Optional[str] = None):
    j = next((x for x in jobs_of(ctx.guild.id, "smsg") if x["id"] == id), None)
    if not j:
        raise fail("Programmazione non trovata.")
    if quando:
        at = pwhen(quando, ctx.guild.id)
        if not at:
            raise fail("Quando non valido.")
        j["at"] = at
    if testo:
        j["text"] = testo
    save()
    await ctx.send("✏️ Programmazione aggiornata.")


@impl("schedule cancel")
async def _schedule_cancel(ctx, id: int):
    await ctx.send("✅ Programmazione annullata." if del_job(ctx.guild.id, id, "smsg") else "Programmazione non trovata.")


@impl("schedule list")
async def _schedule_list(ctx):
    await ctx.send("\n".join(f"`{j['id']}` <t:{j['at']}:R> in <#{j['channel']}>" + (f" (ogni {fdur(j['every'])})" if j.get("every") else "") + f" — {j['text'][:60]}"
                             for j in jobs_of(ctx.guild.id, "smsg")) or "Nessun messaggio programmato.")


@impl("schedule timezone")
async def _schedule_timezone(ctx, fuso: str):
    try:
        from zoneinfo import ZoneInfo
        ZoneInfo(fuso)
    except Exception:
        raise fail("Fuso non valido. Esempi: Europe/Rome, Europe/London, America/New_York.")
    sec(ctx.guild.id, "config")["timezone"] = fuso
    save()
    await ctx.send(f"🌍 Fuso orario impostato su `{fuso}`.")


# --------------------------------------------------------------- UTILITÀ
@impl("utility countdown")
async def _utility_countdown(ctx, durata: str, *, titolo: str = "Conto alla rovescia"):
    d = pdur(durata)
    if not d:
        raise fail("Durata non valida (es. 10m, 2h, 3d).")
    await ctx.send(embed=emb(f"⏳ {titolo}", f"Termina <t:{now() + d}:R> (<t:{now() + d}:F>)"))


@impl("utility role-id")
async def _utility_role_id(ctx, ruolo: discord.Role):
    await ctx.send(f"`{ruolo.id}`")


@impl("utility permissions-check")
async def _utility_perm_check(ctx, member: discord.Member, permesso: str):
    if not hasattr(discord.Permissions, permesso):
        raise fail("Permesso sconosciuto (es. ban_members, manage_roles).")
    ok = getattr(member.guild_permissions, permesso)
    await ctx.send(f"{'✅' if ok else '❌'} {member.display_name} {'ha' if ok else 'non ha'} `{permesso}`.")


@impl("utility bot-permissions")
async def _utility_bot_perms(ctx):
    p = ctx.guild.me.guild_permissions
    on = [n.replace("_", " ").title() for n, v in p if v]
    await ctx.send(embed=emb("🤖 Permessi del bot", ", ".join(on) or "Nessuno"))


@impl("utility invite-info")
async def _utility_invite(ctx, codice: str):
    try:
        inv = await bot.fetch_invite(codice.split("/")[-1], with_counts=True)
    except discord.NotFound:
        raise fail("Invito non valido o scaduto.")
    await ctx.send(embed=emb(f"🔗 {inv.guild.name if inv.guild else 'Invito'}", (
        f"Canale: {inv.channel}\nMembri: {inv.approximate_member_count}\nOnline: {inv.approximate_presence_count}\n"
        f"Creato da: {inv.inviter or 'sconosciuto'}")))


@impl("utility message-info")
async def _utility_message_info(ctx, riferimento: str):
    m = await get_msg(ctx, riferimento)
    e = emb("ℹ️ Messaggio", f"Autore: {m.author} (`{m.author.id}`)\nCanale: {m.channel.mention}\nInviato: <t:{int(m.created_at.timestamp())}:F>\n"
                            f"Allegati: {len(m.attachments)} · Reazioni: {sum(r.count for r in m.reactions)}\n[Vai al messaggio]({m.jump_url})")
    await ctx.send(embed=e)


@impl("utility banner")
async def _utility_banner(ctx, member: Optional[discord.Member] = None):
    await _member_banner(ctx, member)


# ============================================================================
# ESTENSIONE 3: ticket (comandi restanti), candidature, segnalazioni, DM, notifiche
# ============================================================================
class _Shim:
    """Fa passare un comando (ctx) per la stessa logica dei pulsanti dei ticket."""

    def __init__(self, ctx):
        self.guild, self.user, self.guild_id, self._ctx = ctx.guild, ctx.author, ctx.guild.id, ctx
        self.response = self
        self.followup = self

    async def send_message(self, text=None, **kw):
        await self._ctx.send(text, **kw)

    async def send(self, text=None, **kw):
        await self._ctx.send(text, **kw)

    async def defer(self, **kw):
        pass


def _all_panels(gid):
    tdata(gid)  # se non c'è nessun pannello ne crea uno di base
    return [tdata(gid, pid) for pid in list(panels_of(gid))]


def _ticket_here(ctx):
    td, tk = find_ticket(ctx.guild.id, ctx.channel.id)
    if not tk:
        raise fail("Questo canale non è un ticket.")
    return td, tk


@impl("ticket create")
async def _ticket_create(ctx, categoria: Optional[str] = None):
    cats = [(td, c) for td in _all_panels(ctx.guild.id) for c in td["categories"] if c["enabled"]]
    key = (categoria or "").lower()
    hit = next(((td, c) for td, c in cats if c["id"] == key or c["label"].lower() == key), None) or (cats[0] if cats and not categoria else None)
    if not hit:
        raise fail("Categoria non trovata. Disponibili: " + ", ".join(f"`{c['label']}`" for _, c in cats))
    await OpenButton(hit[0]["pid"], hit[1]["id"]).callback(_Shim(ctx))


@impl("ticket settings")
async def _ticket_settings(ctx, chiave: Optional[str] = None, *, valore: Optional[str] = None):
    ps = _all_panels(ctx.guild.id)
    if chiave is None:
        gd = gdata(ctx.guild.id)
        rows = []
        for td in ps:
            cat = ctx.guild.get_channel(td["category_id"]) if td["category_id"] else None
            rows.append(f"**{td['name']}** — categoria Discord: {cat.name if cat else 'nessuna'} · Close: "
                        f"{'elimina' if td['close_mode'] == 'delete' else 'blocca'} · cooldown {td.get('cooldown', 0)} min · aperti {len(td['open'])}")
        return await ctx.send(embed=emb("🎫 Impostazioni ticket", "\n".join(rows) + (
            f"\n\nStaff: {', '.join(f'<@&{i}>' for i in gd['staff_roles'] + gd['admin_roles']) or 'nessun ruolo'}\n"
            "Chiavi (valgono per **tutti** i pannelli): `cooldown` (minuti), `close_mode` (delete/lock). "
            "Per un pannello solo: `/ticketpaneledit`.")))
    if chiave == "cooldown":
        m = re.search(r"\d+", valore or "")
        for td in ps:
            td["cooldown"] = int(m.group()) if m else 0
    elif chiave == "close_mode" and valore in ("delete", "lock"):
        for td in ps:
            td["close_mode"] = valore
    else:
        raise fail("Chiavi valide: cooldown <minuti>, close_mode <delete|lock>.")
    save()
    await ctx.send("✅ Impostazione salvata per tutti i pannelli.")


@impl("ticket category-set")
async def _ticket_category_set(ctx, categoria: discord.CategoryChannel):
    for td in _all_panels(ctx.guild.id):
        td["category_id"] = categoria.id
    save()
    await ctx.send(f"✅ I ticket di **tutti i pannelli** verranno creati in **{categoria.name}** "
                   "(per cambiarla su un solo pannello usa `/ticketpaneledit`).")


@impl("ticket staff-set")
async def _ticket_staff_set(ctx, ruolo: discord.Role):
    gd = gdata(ctx.guild.id)
    if ruolo.id not in gd["staff_roles"]:
        gd["staff_roles"].append(ruolo.id)
    save()
    await ctx.send(f"✅ {ruolo.mention} è ora tra i ruoli staff dei ticket.", allowed_mentions=NOMENTION)


@impl("ticket cooldown")
async def _ticket_cooldown(ctx, minuti: int):
    for td in _all_panels(ctx.guild.id):
        td["cooldown"] = max(0, minuti)
    save()
    await ctx.send(f"⏳ Cooldown tra un ticket e l'altro: **{max(0, minuti)} min** (tutti i pannelli).")


@impl("ticket transfer")
async def _ticket_transfer(ctx, member: discord.Member):
    td, tk = _ticket_here(ctx)
    if not is_staff(member):
        raise fail("Puoi trasferire il ticket solo a un membro dello staff.")
    tk["claimed_by"] = member.id
    await ctx.channel.set_permissions(member, view_channel=True, send_messages=True, read_message_history=True, attach_files=True)
    save()
    await ctx.send(f"🔁 Ticket trasferito a {member.mention}.", allowed_mentions=discord.AllowedMentions(users=True))


@impl("ticket priority")
async def _ticket_priority(ctx, livello: str):
    td, tk = _ticket_here(ctx)
    lv = {"bassa": "🟢 bassa", "low": "🟢 bassa", "normale": "🟡 normale", "normal": "🟡 normale",
          "alta": "🟠 alta", "high": "🟠 alta", "urgente": "🔴 urgente", "urgent": "🔴 urgente"}.get(livello.lower())
    if not lv:
        raise fail("Priorità: bassa, normale, alta, urgente.")
    tk["priority"] = lv
    save()
    await ctx.send(f"🚩 Priorità del ticket: **{lv}**")


@impl("ticket note")
async def _ticket_note(ctx, *, testo: str):
    td, tk = _ticket_here(ctx)
    tk.setdefault("notes", []).append({"by": ctx.author.id, "text": testo, "ts": now()})
    save()
    await ctx.send("📝 Nota interna salvata (visibile solo con `/ticket list`/log).", ephemeral=True)
    log_event(ctx.guild, "tickets", f"Nota su #{ctx.channel.name} da {ctx.author}: {testo[:200]}", ctx.author.id)


@impl("ticket close-reason")
async def _ticket_close_reason(ctx, *, motivo: str):
    td, tk = _ticket_here(ctx)
    tk["reason"] = motivo
    save()
    await ctx.send(f"📝 Motivo di chiusura registrato: {motivo}")


@impl("ticket list")
async def _ticket_list(ctx):
    lines = []
    for td in _all_panels(ctx.guild.id):
        for cid, t in td["open"].items():
            lines.append(f"<#{cid}> · [{td['name']}] <@{t['owner']}> · {t['cat']}" + (f" · preso da <@{t['claimed_by']}>" if t.get("claimed_by") else "") +
                         (f" · {t['priority']}" if t.get("priority") else "") + (" · 🔒 chiuso" if t.get("closed") else ""))
    await ctx.send("\n".join(lines)[:1900] or "Nessun ticket aperto.", allowed_mentions=NOMENTION)


@impl("ticket stats")
async def _ticket_stats(ctx):
    ps = _all_panels(ctx.guild.id)
    hist = [h for td in ps for h in td.get("history", [])]
    opened = [t for td in ps for t in td["open"].values()]
    fb = [f for td in ps for f in td.get("feedback", [])]
    per = collections.Counter(t["cat"] for t in hist + opened)
    dur = [t["closed_ts"] - t["ts"] for t in hist if t.get("ts") and t.get("closed_ts")]
    rating = f"{sum(f['voto'] for f in fb) / len(fb):.1f}/5 ({len(fb)} voti)" if fb else "nessuna valutazione ancora"
    await ctx.send(embed=emb("📊 Statistiche ticket", (
        f"Pannelli: **{len(ps)}** · Aperti: **{len(opened)}** · Chiusi: **{len(hist)}**\n"
        f"Durata media: {fdur(sum(dur) / len(dur)) if dur else '—'}\nValutazione media: {rating}\n"
        "\n**Per categoria:** " + (", ".join(f"{k} {v}" for k, v in per.most_common()) or "—"))))


@impl("ticket feedback")
async def _ticket_feedback(ctx, voto: Optional[int] = None, *, commento: Optional[str] = None):
    ps = _all_panels(ctx.guild.id)
    if voto is None:
        fb = [f for td in ps for f in td.get("feedback", [])]
        return await ctx.send(f"⭐ Valutazione media: {sum(f['voto'] for f in fb) / len(fb):.1f}/5 ({len(fb)} voti). "
                              "Per votare: `/ticket feedback voto:5 commento:...`" if fb else "Nessuna valutazione ancora. Per votare: `/ticket feedback voto:5`.")
    if not 1 <= voto <= 5:
        raise fail("Il voto va da 1 a 5.")
    ps[0].setdefault("feedback", []).append({"user": ctx.author.id, "voto": voto, "commento": commento or "", "ts": now()})
    save()
    await ctx.send(f"⭐ Grazie! Voto {voto}/5 registrato.", ephemeral=True)


# --------------------------------------------------------------- CANDIDATURE
DEFAULT_QS = ["Quanti anni hai?", "Perché vuoi candidarti?", "Che esperienza hai?"]


def app_cfg(gid):
    a = sec(gid, "app")
    a.setdefault("questions", list(DEFAULT_QS))
    a.setdefault("reviewers", [])
    a.setdefault("title", "📝 Candidature")
    a.setdefault("description", "Premi il pulsante per candidarti.")
    return a


def app_embed(rec, user=None):
    e = emb(f"📝 Candidatura #{rec['id']} — {rec['status']}", f"Candidato: <@{rec['user']}>", {"in attesa": 0xFEE75C, "approvata": 0x57F287, "rifiutata": 0xED4245}.get(rec["status"], 0x5865F2))
    for q, a in rec["answers"]:
        e.add_field(name=q[:256], value=(a or "—")[:1000], inline=False)
    if rec.get("note"):
        e.add_field(name="Nota del revisore", value=rec["note"][:1000], inline=False)
    if rec["status"] != "in attesa" and rec.get("by"):
        e.add_field(name="Deciso da", value=f"<@{rec['by']}>")
    if rec.get("role_given"):
        e.add_field(name="Ruolo assegnato", value=f"<@&{rec['role_given']}>")
    return e


class AppModal(SafeModal):
    def __init__(self, questions):
        super().__init__(title="Candidatura")
        self.qs, self.inputs = questions[:5], []
        for q in self.qs:
            ti = discord.ui.TextInput(label=q[:45], style=discord.TextStyle.paragraph, max_length=1000)
            self.inputs.append(ti)
            self.add_item(ti)

    async def on_submit(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)
        gid = interaction.guild_id
        app = app_cfg(gid)
        rec = {"id": nid(gid, "application"), "user": interaction.user.id, "status": "in attesa", "ts": now(), "by": None,
               "note": "", "answers": [[q, i.value] for q, i in zip(self.qs, self.inputs)]}
        sec(gid, "applications")[str(rec["id"])] = rec
        ch = interaction.guild.get_channel(app.get("review_channel", 0))
        sent = None
        if ch:
            v = discord.ui.View(timeout=None)
            v.add_item(AppDecideButton("ok", rec["id"]))
            v.add_item(AppDecideButton("no", rec["id"]))
            ping = " ".join(f"<@&{r}>" for r in app["reviewers"])
            sent = await safe_send(ch, content=ping or None, embed=app_embed(rec), view=v, allowed_mentions=discord.AllowedMentions(roles=True))
            if sent:
                rec["msg"] = {"channel": ch.id, "id": sent.id}
        save()
        extra = "" if sent else "\n⚠️ Non ho un canale di revisione utilizzabile: lo staff la trova con `/application review`. Imposta il canale con `/application panel-edit revisione:#canale`."
        await interaction.followup.send(f"✅ Candidatura **#{rec['id']}** inviata! Ti avviseremo in privato." + extra, ephemeral=True)


DM_ACCETTATA = "La tua candidatura è stata accettata! Benvenuto nello staff!"
DM_RIFIUTATA = "La tua candidatura non è stata accettata, ci dispiace, prova la prossima volta!"


async def app_apply(guild, rec, status, by):
    """Registra la decisione, assegna il ruolo (se accettata), avvisa il candidato in privato e aggiorna il messaggio di revisione.
    Ritorna {"dm": messaggio privato arrivato?, "role": None / "ok" / "fail"}."""
    rec.update(status=status, by=by.id)
    role_res = None
    rid = app_cfg(guild.id).get("accept_role")
    if status == "approvata" and rid:
        role, mem = guild.get_role(rid), guild.get_member(rec["user"])
        role_res = "fail"
        if role and mem:
            try:
                await mem.add_roles(role, reason=f"Candidatura #{rec['id']} accettata")
                rec["role_given"] = role.id
                role_res = "ok"
            except discord.HTTPException:
                pass
    save()
    sent = False
    try:
        u = guild.get_member(rec["user"]) or await bot.fetch_user(rec["user"])
        await u.send(DM_ACCETTATA if status == "approvata" else DM_RIFIUTATA)
        sent = True
    except (discord.HTTPException, AttributeError):
        pass
    log_event(guild, "moderation", f"Candidatura #{rec['id']} {status} da {by}", rec["user"])
    m = rec.get("msg")
    if m:
        try:
            msg = await guild.get_channel(m["channel"]).fetch_message(m["id"])
            await msg.edit(embed=app_embed(rec), view=None)
        except (discord.HTTPException, AttributeError):
            pass
    return {"dm": sent, "role": role_res}


class AppDecideButton(discord.ui.DynamicItem[discord.ui.Button], template=r"ap:(?P<act>ok|no):(?P<id>\d+)"):
    def __init__(self, act, rid):
        ok = act == "ok"
        super().__init__(discord.ui.Button(label="Accetta" if ok else "Rifiuta", emoji="✅" if ok else "❌",
                                           style=discord.ButtonStyle.success if ok else discord.ButtonStyle.danger,
                                           custom_id=f"ap:{act}:{rid}"))
        self.act, self.rid = act, rid

    @classmethod
    async def from_custom_id(cls, interaction, item, match, /):
        return cls(match["act"], int(match["id"]))

    async def callback(self, interaction: discord.Interaction):
        rec = sec(interaction.guild_id, "applications").get(str(self.rid))
        if not rec:
            return await interaction.response.send_message("Candidatura non trovata.", ephemeral=True)
        m = interaction.user
        if not (is_staff(m) or any(r.id in app_cfg(interaction.guild_id)["reviewers"] for r in m.roles)):
            return await interaction.response.send_message("Solo lo staff o i revisori possono decidere.", ephemeral=True)
        if rec["status"] != "in attesa":
            return await interaction.response.send_message(f"Candidatura già **{rec['status']}**.", ephemeral=True)
        await interaction.response.defer(ephemeral=True)
        status = "approvata" if self.act == "ok" else "rifiutata"
        res = await app_apply(interaction.guild, rec, status, interaction.user)
        rid = app_cfg(interaction.guild_id).get("accept_role")
        extra = ""
        if res["role"] == "ok":
            extra = f"\n🎖️ Ruolo <@&{rid}> assegnato."
        elif res["role"] == "fail":
            extra = (f"\n⚠️ Non sono riuscito a dare il ruolo <@&{rid}>: controlla che il mio ruolo sia più in alto e che il candidato sia ancora nel server.")
        await interaction.followup.send(
            f"{'✅' if self.act == 'ok' else '❌'} Candidatura #{self.rid} **{status}**." +
            (" Il candidato è stato avvisato in privato." if res["dm"] else "\n⚠️ Non sono riuscito a scrivere in privato al candidato (messaggi privati chiusi).") + extra,
            ephemeral=True)


class AppButton(discord.ui.DynamicItem[discord.ui.Button], template=r"ap:open"):
    def __init__(self, label="Candidati"):
        super().__init__(discord.ui.Button(label=label, emoji="📝", style=discord.ButtonStyle.primary, custom_id="ap:open"))

    @classmethod
    async def from_custom_id(cls, interaction, item, match, /):
        return cls()

    async def callback(self, interaction: discord.Interaction):
        await interaction.response.send_modal(AppModal(app_cfg(interaction.guild_id)["questions"]))


async def _app_panel_post(guild, a, ch):
    v = discord.ui.View(timeout=None)
    v.add_item(AppButton())
    msg = await ch.send(embed=emb(a["title"], a["description"]), view=v)
    a["panel"] = {"channel": ch.id, "message": msg.id}
    save()


@impl("application panel-create")
async def _app_panel_create(ctx, canale: discord.TextChannel, revisione: Optional[discord.TextChannel] = None):
    a = app_cfg(ctx.guild.id)
    if revisione:
        a["review_channel"] = revisione.id
    elif not a.get("review_channel"):
        a["review_channel"] = canale.id  # finché non scegli un canale, le candidature arrivano qui
    await _app_panel_post(ctx.guild, a, canale)
    await ctx.send(f"✅ Pannello candidature pubblicato in {canale.mention}. Le candidature arrivano in <#{a['review_channel']}>."
                   + ("" if revisione or a.get("review_channel") != canale.id else
                      "\n⚠️ È lo stesso canale del pannello: per sceglierne un altro usa `/application panel-edit revisione:#canale`."))


@impl("application panel-edit")
async def _app_panel_edit(ctx, titolo: Optional[str] = None, revisione: Optional[discord.TextChannel] = None, *, descrizione: Optional[str] = None):
    a = app_cfg(ctx.guild.id)
    if not (titolo or revisione or descrizione):
        rv = ctx.guild.get_channel(a.get("review_channel", 0))
        pn = ctx.guild.get_channel(a.get("panel", {}).get("channel", 0))
        ar_txt = f"<@&{a['accept_role']}>" if a.get("accept_role") else "nessuno"
        return await ctx.send(embed=emb("📝 Configurazione candidature", (
            f"**Canale delle candidature:** {rv.mention if rv else 'non impostato'}\n**Pannello:** {pn.mention if pn else 'non pubblicato'}\n"
            f"**Revisori:** {' '.join(f'<@&{r}>' for r in a['reviewers']) or 'solo lo staff'}\n**Ruolo se accettati:** {ar_txt}\n**Domande:** {len(a['questions'])}\n"
            f"**Titolo:** {a['title']}\n\nPer cambiare il canale: `/application panel-edit revisione:#canale`")))
    changes = []
    if titolo:
        a["title"] = titolo
        changes.append("titolo")
    if descrizione:
        a["description"] = descrizione
        changes.append("descrizione")
    if revisione:
        a["review_channel"] = revisione.id
        changes.append(f"canale delle candidature → {revisione.mention}")
    save()
    p = a.get("panel")
    if p and (titolo or descrizione):
        try:
            m = await ctx.guild.get_channel(p["channel"]).fetch_message(p["message"])
            await m.edit(embed=emb(a["title"], a["description"]))
        except (discord.HTTPException, AttributeError):
            pass
    await ctx.send("✏️ Aggiornato: " + ", ".join(changes) + ".")


@impl("application questions")
async def _app_questions(ctx, *, domande: Optional[str] = None):
    a = app_cfg(ctx.guild.id)
    if not domande:
        return await ctx.send("**Domande attuali:**\n" + "\n".join(f"{i}. {q}" for i, q in enumerate(a["questions"], 1)) +
                              "\n\nPer cambiarle: `/application questions domande: Domanda 1; Domanda 2; ...` (massimo 5)")
    qs = [q.strip() for q in domande.split(";") if q.strip()][:5]
    if not qs:
        raise fail("Scrivi almeno una domanda.")
    a["questions"] = qs
    save()
    await ctx.send(f"✅ {len(qs)} domande salvate.")


@impl("application role")
async def _app_role(ctx, ruolo: Optional[discord.Role] = None, rimuovi: bool = False):
    a = app_cfg(ctx.guild.id)
    if rimuovi:
        a.pop("accept_role", None)
        save()
        return await ctx.send("🗑️ Nessun ruolo verrà più dato a chi viene accettato.")
    if ruolo is None:
        cur = a.get("accept_role")
        return await ctx.send(f"🎖️ Ruolo dato a chi viene accettato: {f'<@&{cur}>' if cur else 'nessuno'}.\n"
                              "Per impostarlo: `/application role ruolo:@Ruolo` (per toglierlo: `rimuovi:True`).", allowed_mentions=NOMENTION)
    role_ok(ctx, ruolo)
    a["accept_role"] = ruolo.id
    save()
    await ctx.send(f"✅ Chi viene accettato riceverà il ruolo {ruolo.mention}.", allowed_mentions=NOMENTION)


@impl("application set-reviewers")
async def _app_reviewers(ctx, ruolo: discord.Role):
    a = app_cfg(ctx.guild.id)
    if ruolo.id in a["reviewers"]:
        a["reviewers"].remove(ruolo.id)
        msg = f"➖ {ruolo.mention} non è più revisore."
    else:
        a["reviewers"].append(ruolo.id)
        msg = f"➕ {ruolo.mention} è ora revisore."
    save()
    await ctx.send(msg, allowed_mentions=NOMENTION)


@impl("application submit")
async def _app_submit(ctx):
    if ctx.interaction is None:
        raise fail("Usa lo slash command `/application submit` o il pulsante del pannello.")
    await ctx.interaction.response.send_modal(AppModal(app_cfg(ctx.guild.id)["questions"]))


def _app_get(ctx, id):
    r = sec(ctx.guild.id, "applications").get(str(id))
    if not r:
        raise fail("Candidatura non trovata.")
    return r


@impl("application review")
async def _app_review(ctx, id: int):
    await ctx.send(embed=app_embed(_app_get(ctx, id)))


@impl("application list")
async def _app_list(ctx, stato: Optional[str] = None):
    rs = [r for r in sec(ctx.guild.id, "applications").values() if not stato or r["status"] == stato]
    await ctx.send("\n".join(f"`{r['id']}` <@{r['user']}> — {r['status']} (<t:{r['ts']}:R>)" for r in rs[-20:])[:1900] or "Nessuna candidatura.", allowed_mentions=NOMENTION)


@impl("application stats")
async def _app_stats(ctx):
    c = collections.Counter(r["status"] for r in sec(ctx.guild.id, "applications").values())
    await ctx.send(embed=emb("📊 Candidature", "\n".join(f"{k}: **{v}**" for k, v in c.items()) or "Nessuna candidatura."))


# --------------------------------------------------------------- SEGNALAZIONI
def _rep_get(ctx, id):
    r = sec(ctx.guild.id, "reports").get(str(id))
    if not r:
        raise fail("Segnalazione non trovata.")
    return r


def rep_embed(r):
    e = emb(f"🚨 Segnalazione #{r['id']} — {r['status']}", f"**Utente:** <@{r['target']}>\n**Da:** <@{r['reporter']}>\n**Motivo:** {r['reason']}",
            {"aperta": 0xED4245, "risolta": 0x57F287, "archiviata": 0x99AAB5}.get(r["status"], 0x5865F2))
    for n in r.get("notes", [])[-5:]:
        e.add_field(name=f"Nota · <t:{n['ts']}:d>", value=f"<@{n['by']}>: {n['text']}"[:1000], inline=False)
    return e


@impl("report create")
async def _report_create(ctx, utente: discord.User, *, motivo: str):
    m = mod(ctx.guild.id, "report")
    r = {"id": nid(ctx.guild.id, "report"), "reporter": ctx.author.id, "target": utente.id, "reason": motivo, "status": "aperta", "ts": now(), "notes": []}
    sec(ctx.guild.id, "reports")[str(r["id"])] = r
    save()
    ch = ctx.guild.get_channel(m["canale"]) if m["canale"] else None
    if ch:
        await safe_send(ch, content=f"<@&{m['ruolo_ping']}>" if m["ruolo_ping"] else None, embed=rep_embed(r),
                        allowed_mentions=discord.AllowedMentions(roles=True))
    log_event(ctx.guild, "moderation", f"Segnalazione #{r['id']} su {utente} da {ctx.author}", utente.id)
    await ctx.send(f"✅ Segnalazione **#{r['id']}** inviata allo staff. Grazie!", ephemeral=True)


@impl("report view")
async def _report_view(ctx, id: int):
    await ctx.send(embed=rep_embed(_rep_get(ctx, id)))


@impl("report list")
async def _report_list(ctx, stato: str = "aperta"):
    rs = [r for r in sec(ctx.guild.id, "reports").values() if stato == "tutte" or r["status"] == stato]
    await ctx.send("\n".join(f"`{r['id']}` <@{r['target']}> — {r['reason'][:60]} ({r['status']})" for r in rs[-20:])[:1900] or "Nessuna segnalazione.", allowed_mentions=NOMENTION)


async def _report_set(ctx, id, status, nota):
    r = _rep_get(ctx, id)
    r["status"] = status
    if nota:
        r["notes"].append({"by": ctx.author.id, "text": nota, "ts": now()})
    save()
    await ctx.send(f"✅ Segnalazione #{id} **{status}**.")


@impl("report resolve")
async def _report_resolve(ctx, id: int, *, nota: Optional[str] = None):
    await _report_set(ctx, id, "risolta", nota)


@impl("report dismiss")
async def _report_dismiss(ctx, id: int, *, nota: Optional[str] = None):
    await _report_set(ctx, id, "archiviata", nota)


@impl("report add-note")
async def _report_note(ctx, id: int, *, nota: str):
    r = _rep_get(ctx, id)
    r["notes"].append({"by": ctx.author.id, "text": nota, "ts": now()})
    save()
    await ctx.send("📝 Nota aggiunta.")


# --------------------------------------------------------------- DM E NOTIFICHE
@impl("dm send")
async def _dm_send(ctx, member: discord.Member, *, testo: str):
    t = sec(ctx.guild.id, "dm_templates")
    if testo.lower().startswith("template:") and testo[9:].strip().lower() in t:
        testo = t[testo[9:].strip().lower()]
    testo = fmt_msg(testo, member)
    try:
        await member.send(testo)
    except discord.HTTPException:
        raise fail("Non posso scrivere a questo utente (DM chiusi).")
    h = sec(ctx.guild.id, "dm_hist", list)
    h.append({"ts": now(), "by": ctx.author.id, "to": member.id, "text": testo[:120]})
    del h[:-200]
    save()
    await ctx.send(f"✉️ Messaggio inviato a {member.display_name}.", ephemeral=True)


@impl("dm template-create")
async def _dm_tcreate(ctx, nome: str, *, testo: str):
    sec(ctx.guild.id, "dm_templates")[nome.lower()] = testo
    save()
    await ctx.send(f"💾 Modello `{nome}` salvato. Usalo con `/dm send testo: template:{nome}`.")


@impl("dm template-edit")
async def _dm_tedit(ctx, nome: str, *, testo: str):
    t = sec(ctx.guild.id, "dm_templates")
    if nome.lower() not in t:
        raise fail("Modello non trovato.")
    t[nome.lower()] = testo
    save()
    await ctx.send("✏️ Modello aggiornato.")


@impl("dm template-delete")
async def _dm_tdelete(ctx, nome: str):
    if sec(ctx.guild.id, "dm_templates").pop(nome.lower(), None) is None:
        raise fail("Modello non trovato.")
    save()
    await ctx.send("🗑️ Modello eliminato.")


@impl("dm preview")
async def _dm_preview(ctx, nome: str):
    t = sec(ctx.guild.id, "dm_templates").get(nome.lower())
    if not t:
        raise fail("Modello non trovato. Disponibili: " + (", ".join(sec(ctx.guild.id, "dm_templates")) or "nessuno"))
    await ctx.send(fmt_msg(t, ctx.author), allowed_mentions=NOMENTION)


@impl("dm history")
async def _dm_history(ctx, quanti: int = 10):
    h = sec(ctx.guild.id, "dm_hist", list)[-max(1, min(quanti, 30)):]
    await ctx.send("\n".join(f"<t:{x['ts']}:R> <@{x['by']}> → <@{x['to']}>: {x['text']}" for x in reversed(h))[:1900] or "Nessun DM registrato.", allowed_mentions=NOMENTION)


def _notif(ctx, nome):
    n = sec(ctx.guild.id, "notifications").get(nome.lower())
    if not n:
        raise fail("Notifica non trovata. Disponibili: " + (", ".join(sec(ctx.guild.id, "notifications")) or "nessuna"))
    return n


@impl("notification create")
async def _notif_create(ctx, nome: str, *, descrizione: str = ""):
    sec(ctx.guild.id, "notifications")[nome.lower()] = {"desc": descrizione, "subs": []}
    save()
    await ctx.send(f"🔔 Notifica `{nome}` creata. I membri si iscrivono con `/notification subscribe {nome}`.")


@impl("notification edit")
async def _notif_edit(ctx, nome: str, *, descrizione: str):
    _notif(ctx, nome)["desc"] = descrizione
    save()
    await ctx.send("✏️ Notifica aggiornata.")


@impl("notification delete")
async def _notif_delete(ctx, nome: str):
    _notif(ctx, nome)
    del sec(ctx.guild.id, "notifications")[nome.lower()]
    save()
    await ctx.send("🗑️ Notifica eliminata.")


@impl("notification test")
async def _notif_test(ctx, nome: str, tutti: bool = False):
    n = _notif(ctx, nome)
    text = f"🔔 **{nome}** — {n['desc'] or 'nessuna descrizione'}"
    targets = [ctx.guild.get_member(u) for u in n["subs"]] if tutti and is_staff(ctx.author) else [ctx.author]
    sent = 0
    for m in filter(None, targets):
        try:
            await m.send(text)
            sent += 1
        except discord.HTTPException:
            pass
    await ctx.send(f"✅ Notifica inviata a {sent} persone.", ephemeral=True)


@impl("notification list")
async def _notif_list(ctx):
    ns = sec(ctx.guild.id, "notifications")
    await ctx.send("\n".join(f"🔔 **{k}** — {v['desc'] or '—'} ({len(v['subs'])} iscritti)" for k, v in ns.items()) or "Nessuna notifica.")


@impl("notification subscribe")
async def _notif_sub(ctx, nome: str):
    n = _notif(ctx, nome)
    if ctx.author.id not in n["subs"]:
        n["subs"].append(ctx.author.id)
    save()
    await ctx.send(f"🔔 Iscritto a `{nome}`.", ephemeral=True)


@impl("notification unsubscribe")
async def _notif_unsub(ctx, nome: str):
    n = _notif(ctx, nome)
    if ctx.author.id in n["subs"]:
        n["subs"].remove(ctx.author.id)
    save()
    await ctx.send(f"🔕 Iscrizione a `{nome}` annullata.", ephemeral=True)


@impl("notification preferences")
async def _notif_prefs(ctx, nome: Optional[str] = None):
    ns = sec(ctx.guild.id, "notifications")
    if nome:
        n = _notif(ctx, nome)
        if ctx.author.id in n["subs"]:
            n["subs"].remove(ctx.author.id)
            msg = f"🔕 Disattivata `{nome}`."
        else:
            n["subs"].append(ctx.author.id)
            msg = f"🔔 Attivata `{nome}`."
        save()
        return await ctx.send(msg, ephemeral=True)
    await ctx.send("\n".join(f"{'🔔' if ctx.author.id in v['subs'] else '🔕'} {k}" for k, v in ns.items()) +
                   "\n\nPer attivare/disattivare: `/notification preferences nome:...`" if ns else "Nessuna notifica disponibile.", ephemeral=True)


# ============================================================================
# ESTENSIONE 4: statistiche, contatori, traguardi, livelli, attività, automazioni, trigger
# ============================================================================
def day(ts=None):
    return datetime.fromtimestamp(ts or time.time(), timezone.utc).strftime("%Y-%m-%d")


def stt(gid):
    s = sec(gid, "stats")
    for k, d in (("msgs", {}), ("chan", {}), ("members", {}), ("joins", []), ("leaves", [])):
        s.setdefault(k, d)
    return s


@bot.listen("on_message")
async def _stats_msg(message):
    if message.guild is None or message.author.bot:
        return
    s, d = stt(message.guild.id), day()
    s["msgs"][d] = s["msgs"].get(d, 0) + 1
    c = str(message.channel.id)
    s["chan"][c] = s["chan"].get(c, 0) + 1
    if d not in s["members"]:
        s["members"][d] = message.guild.member_count
        for old in sorted(s["members"])[:-120]:
            s["members"].pop(old, None)
            s["msgs"].pop(old, None)


@bot.listen("on_member_join")
async def _stats_join(member):
    s = stt(member.guild.id)
    s["joins"].append(now())
    del s["joins"][:-5000]
    s["members"][day()] = member.guild.member_count


@bot.listen("on_member_remove")
async def _stats_leave(member):
    s = stt(member.guild.id)
    s["leaves"].append(now())
    del s["leaves"][:-5000]
    s["members"][day()] = member.guild.member_count


def _since(lst, secs):
    t = now() - secs
    return sum(1 for x in lst if x >= t)


def _msgs_days(s, n):
    days = [day(time.time() - i * 86400) for i in range(n)]
    return sum(s["msgs"].get(d, 0) for d in days)


@impl("stats overview")
async def _stats_overview(ctx):
    g, s = ctx.guild, stt(ctx.guild.id)
    bots = sum(1 for m in g.members if m.bot)
    e = emb(f"📊 {g.name}")
    e.add_field(name="Membri", value=f"{g.member_count} ({g.member_count - bots} umani, {bots} bot)")
    e.add_field(name="Canali / Ruoli", value=f"{len(g.channels)} / {len(g.roles) - 1}")
    e.add_field(name="Messaggi (oggi / 7g)", value=f"{s['msgs'].get(day(), 0)} / {_msgs_days(s, 7)}")
    e.add_field(name="Ingressi / uscite (7g)", value=f"{_since(s['joins'], 604800)} / {_since(s['leaves'], 604800)}")
    e.add_field(name="Boost", value=f"{g.premium_subscription_count} (livello {g.premium_tier})")
    e.add_field(name="Creato", value=discord.utils.format_dt(g.created_at, "R"))
    await ctx.send(embed=e)


@impl("stats members")
async def _stats_members(ctx):
    g = ctx.guild
    bots = sum(1 for m in g.members if m.bot)
    new7 = sum(1 for m in g.members if m.joined_at and (discord.utils.utcnow() - m.joined_at).days < 7)
    await ctx.send(embed=emb("👥 Membri", f"Totale: **{g.member_count}**\nUmani: **{g.member_count - bots}** · Bot: **{bots}**\nEntrati negli ultimi 7 giorni: **{new7}**"))


@impl("stats channels")
async def _stats_channels(ctx):
    g, s = ctx.guild, stt(ctx.guild.id)
    top = sorted(s["chan"].items(), key=lambda kv: -kv[1])[:5]
    await ctx.send(embed=emb("🗂️ Canali", f"Testo: **{len(g.text_channels)}** · Vocali: **{len(g.voice_channels)}** · Categorie: **{len(g.categories)}** · Thread: **{len(g.threads)}**\n\n"
                                         "**Più attivi:**\n" + ("\n".join(f"<#{c}> — {n} messaggi" for c, n in top) or "nessun dato ancora")))


@impl("stats roles")
async def _stats_roles(ctx):
    rs = sorted((r for r in ctx.guild.roles if not r.is_default()), key=lambda r: -len(r.members))[:10]
    await ctx.send(embed=emb("🏷️ Ruoli più popolati", "\n".join(f"{r.mention} — {len(r.members)}" for r in rs) or "Nessun ruolo."), allowed_mentions=NOMENTION)


@impl("stats activity")
async def _stats_activity(ctx):
    act = gdata(ctx.guild.id).get("activity", {})
    top = sorted(act.items(), key=lambda kv: -kv[1]["messages"])[:5]
    await ctx.send(embed=emb("📈 Attività", "\n".join(f"<@{u}> — {a['messages']} messaggi" for u, a in top) or "Nessun dato ancora."), allowed_mentions=NOMENTION)


@impl("stats messages")
async def _stats_messages(ctx):
    s = stt(ctx.guild.id)
    await ctx.send(embed=emb("💬 Messaggi", f"Oggi: **{s['msgs'].get(day(), 0)}**\nUltimi 7 giorni: **{_msgs_days(s, 7)}**\nUltimi 30 giorni: **{_msgs_days(s, 30)}**"))


@impl("stats joins")
async def _stats_joins(ctx):
    j = stt(ctx.guild.id)["joins"]
    await ctx.send(embed=emb("📥 Ingressi", f"24 ore: **{_since(j, 86400)}**\n7 giorni: **{_since(j, 604800)}**\n30 giorni: **{_since(j, 2592000)}**"))


@impl("stats leaves")
async def _stats_leaves(ctx):
    j = stt(ctx.guild.id)["leaves"]
    await ctx.send(embed=emb("📤 Uscite", f"24 ore: **{_since(j, 86400)}**\n7 giorni: **{_since(j, 604800)}**\n30 giorni: **{_since(j, 2592000)}**"))


@impl("stats growth")
async def _stats_growth(ctx):
    s = stt(ctx.guild.id)
    lines = []
    for n in (7, 30):
        d = _since(s["joins"], n * 86400) - _since(s["leaves"], n * 86400)
        lines.append(f"{n} giorni: **{d:+d}** ({_since(s['joins'], n * 86400)} ingressi, {_since(s['leaves'], n * 86400)} uscite)")
    await ctx.send(embed=emb("📈 Crescita", "\n".join(lines)))


@impl("stats history")
async def _stats_history(ctx):
    s = stt(ctx.guild.id)
    rows = [f"`{d}` {n} membri · {s['msgs'].get(d, 0)} messaggi" for d, n in sorted(s["members"].items())[-14:]]
    await ctx.send(embed=emb("🕒 Storico (14 giorni)", "\n".join(rows) or "Ancora nessun dato: si accumula col tempo."))


@impl("stats export")
async def _stats_export(ctx):
    f = discord.File(io.BytesIO(json.dumps(stt(ctx.guild.id), indent=2).encode()), filename="statistiche.json")
    await ctx.send("📤 Esportazione statistiche:", file=f, ephemeral=True)


@impl("welcome-stats")
async def _welcome_stats(ctx):
    s = stt(ctx.guild.id)
    c = sec(ctx.guild.id, "welcome")
    await ctx.send(embed=emb("👋 Statistiche benvenuti", f"Benvenuto: {'🟢 attivo' if c.get('enabled') else '🔴 disattivo'}\n"
                                                         f"Messaggi inviati: **{c.get('sent', 0)}**\nIngressi 24h: **{_since(s['joins'], 86400)}** · 7g: **{_since(s['joins'], 604800)}**"))


# --- contatori
COUNTER_TYPES = {"membri": lambda g: g.member_count, "umani": lambda g: sum(1 for m in g.members if not m.bot),
                 "bot": lambda g: sum(1 for m in g.members if m.bot), "canali": lambda g: len(g.channels),
                 "ruoli": lambda g: len(g.roles) - 1, "boost": lambda g: g.premium_subscription_count}


async def refresh_counters(guild, only=None):
    for c in sec(guild.id, "counters").values():
        if only and c["id"] != only:
            continue
        ch = guild.get_channel(c["channel"])
        name = c["format"].replace("{n}", str(COUNTER_TYPES[c["tipo"]](guild)))
        if ch and ch.name != name:
            try:
                await ch.edit(name=name)
            except discord.HTTPException:
                pass


@job("counters")
async def _job_counters(gid, j):
    g = bot.get_guild(gid)
    if g and sec(gid, "counters"):
        await refresh_counters(g)
    else:
        j.pop("every", None)


@impl("counter create")
async def _counter_create(ctx, tipo: str, *, formato: Optional[str] = None):
    tipo = tipo.lower()
    if tipo not in COUNTER_TYPES:
        raise fail("Tipi: " + ", ".join(COUNTER_TYPES))
    fmt = formato if formato and "{n}" in formato else f"{tipo.capitalize()}: {{n}}"
    ch = await ctx.guild.create_voice_channel("contatore", overwrites={ctx.guild.default_role: discord.PermissionOverwrite(connect=False)})
    c = {"id": nid(ctx.guild.id, "counter"), "tipo": tipo, "format": fmt, "channel": ch.id}
    sec(ctx.guild.id, "counters")[str(c["id"])] = c
    if not jobs_of(ctx.guild.id, "counters"):
        add_job(ctx.guild.id, "counters", now() + 600, every=600)
    save()
    await refresh_counters(ctx.guild, c["id"])
    await ctx.send(f"✅ Contatore `{c['id']}` creato: {ch.mention} (si aggiorna ogni 10 minuti).")


def _counter_get(ctx, id):
    c = sec(ctx.guild.id, "counters").get(str(id))
    if not c:
        raise fail("Contatore non trovato.")
    return c


@impl("counter edit")
async def _counter_edit(ctx, id: int, *, formato: str):
    c = _counter_get(ctx, id)
    if "{n}" not in formato:
        raise fail("Il formato deve contenere {n} (es. `Membri: {n}`).")
    c["format"] = formato
    save()
    await refresh_counters(ctx.guild, c["id"])
    await ctx.send("✏️ Contatore aggiornato.")


@impl("counter delete")
async def _counter_delete(ctx, id: int):
    c = _counter_get(ctx, id)
    ch = ctx.guild.get_channel(c["channel"])
    if ch:
        await ch.delete(reason="Contatore eliminato")
    del sec(ctx.guild.id, "counters")[str(id)]
    save()
    await ctx.send("🗑️ Contatore eliminato.")


@impl("counter list")
async def _counter_list(ctx):
    await ctx.send("\n".join(f"`{c['id']}` {c['tipo']} → <#{c['channel']}> `{c['format']}`" for c in sec(ctx.guild.id, "counters").values()) or "Nessun contatore.")


@impl("counter refresh")
async def _counter_refresh(ctx, id: Optional[int] = None):
    await refresh_counters(ctx.guild, id)
    await ctx.send("🔄 Contatori aggiornati (Discord limita i rinomini: possono servire alcuni minuti).")


# --- traguardi
@bot.listen("on_member_join")
async def _milestone_join(member):
    for m in sec(member.guild.id, "milestones").values():
        if not m["done"] and member.guild.member_count >= m["target"]:
            m["done"] = True
            await safe_send(member.guild.get_channel(m["channel"]), content=m["message"].replace("{count}", str(m["target"])))
            save()


@impl("milestone create")
async def _milestone_create(ctx, traguardo: int, canale: discord.TextChannel, *, messaggio: str = "🎉 Abbiamo raggiunto {count} membri! Grazie a tutti!"):
    m = {"id": nid(ctx.guild.id, "milestone"), "target": traguardo, "channel": canale.id, "message": messaggio, "done": ctx.guild.member_count >= traguardo}
    sec(ctx.guild.id, "milestones")[str(m["id"])] = m
    save()
    await ctx.send(f"🏁 Traguardo `{m['id']}`: **{traguardo}** membri → annuncio in {canale.mention}." + (" (già raggiunto)" if m["done"] else ""))


@impl("milestone edit")
async def _milestone_edit(ctx, id: int, traguardo: Optional[int] = None, *, messaggio: Optional[str] = None):
    m = sec(ctx.guild.id, "milestones").get(str(id))
    if not m:
        raise fail("Traguardo non trovato.")
    if traguardo:
        m["target"], m["done"] = traguardo, ctx.guild.member_count >= traguardo
    if messaggio:
        m["message"] = messaggio
    save()
    await ctx.send("✏️ Traguardo aggiornato.")


@impl("milestone delete")
async def _milestone_delete(ctx, id: int):
    if sec(ctx.guild.id, "milestones").pop(str(id), None) is None:
        raise fail("Traguardo non trovato.")
    save()
    await ctx.send("🗑️ Traguardo eliminato.")


@impl("milestone list")
async def _milestone_list(ctx):
    await ctx.send("\n".join(f"`{m['id']}` {'✅' if m['done'] else '⏳'} **{m['target']}** membri → <#{m['channel']}>" for m in sec(ctx.guild.id, "milestones").values()) or "Nessun traguardo.")


# --- livelli
reg_toggle("leveling")


def lvl_need(l):
    return 5 * l * l + 50 * l + 100


def level_of(xp):
    l = 0
    while xp >= lvl_need(l):
        xp -= lvl_need(l)
        l += 1
    return l, xp, lvl_need(l)


@bot.listen("on_message")
async def _xp_listener(message):
    if message.guild is None or message.author.bot:
        return
    gid = message.guild.id
    m = mod(gid, "leveling")
    if not m["enabled"]:
        return
    r = sec(gid, "xp").setdefault(str(message.author.id), {"xp": 0, "last": 0})
    if time.time() - r["last"] < m["cooldown"]:
        return
    old = level_of(r["xp"])[0]
    r["xp"] += random.randint(min(m["xp_min"], m["xp_max"]), max(m["xp_min"], m["xp_max"]))
    r["last"] = time.time()
    new = level_of(r["xp"])[0]
    if new > old:
        ch = message.guild.get_channel(m["canale"]) if m["canale"] else message.channel
        await safe_send(ch, content=m["messaggio"].replace("{user}", message.author.mention).replace("{level}", str(new)),
                        allowed_mentions=discord.AllowedMentions(users=True))
        for lvl, rid in sec(gid, "rewards").items():
            role = message.guild.get_role(rid)
            if role and int(lvl) <= new and role not in message.author.roles:
                try:
                    await message.author.add_roles(role, reason=f"Premio livello {lvl}")
                except discord.HTTPException:
                    pass
        save()


@impl("leveling rewards")
async def _lvl_rewards(ctx):
    rw = sec(ctx.guild.id, "rewards")
    await ctx.send("\n".join(f"Livello **{k}** → <@&{v}>" for k, v in sorted(rw.items(), key=lambda kv: int(kv[0]))) or "Nessun premio.", allowed_mentions=NOMENTION)


@impl("leveling reward-add")
async def _lvl_reward_add(ctx, livello: int, ruolo: discord.Role):
    role_ok(ctx, ruolo)
    sec(ctx.guild.id, "rewards")[str(livello)] = ruolo.id
    save()
    await ctx.send(f"🏆 Al livello **{livello}** si ottiene {ruolo.mention}.", allowed_mentions=NOMENTION)


@impl("leveling reward-remove")
async def _lvl_reward_remove(ctx, livello: int):
    if sec(ctx.guild.id, "rewards").pop(str(livello), None) is None:
        raise fail("Nessun premio a quel livello.")
    save()
    await ctx.send("🗑️ Premio rimosso.")


@impl("leveling leaderboard")
async def _lvl_leaderboard(ctx, pagina: int = 1):
    xp = sorted(sec(ctx.guild.id, "xp").items(), key=lambda kv: -kv[1]["xp"])
    p = max(1, pagina)
    rows = xp[(p - 1) * 10:p * 10]
    await ctx.send(embed=emb(f"🏆 Classifica XP (pagina {p})", "\n".join(f"`{(p - 1) * 10 + i}.` <@{u}> — livello **{level_of(r['xp'])[0]}** · {r['xp']} XP" for i, (u, r) in enumerate(rows, 1)) or "Nessun dato."), allowed_mentions=NOMENTION)


@impl("leveling profile")
async def _lvl_profile(ctx, member: Optional[discord.Member] = None):
    m = member or ctx.author
    xps = sec(ctx.guild.id, "xp")
    r = xps.get(str(m.id), {"xp": 0})
    lvl, prog, need = level_of(r["xp"])
    rank = 1 + sum(1 for x in xps.values() if x["xp"] > r["xp"])
    bar = "█" * int(prog / need * 10) + "░" * (10 - int(prog / need * 10))
    e = emb(f"⭐ {m.display_name}", f"Livello **{lvl}** · Posizione **#{rank}**\n`{bar}` {prog}/{need} XP\nXP totali: {r['xp']}", m.color)
    e.set_thumbnail(url=m.display_avatar.url)
    await ctx.send(embed=e)


@impl("leveling set-xp")
async def _lvl_set_xp(ctx, member: discord.Member, xp: int):
    sec(ctx.guild.id, "xp").setdefault(str(member.id), {"xp": 0, "last": 0})["xp"] = max(0, xp)
    save()
    await ctx.send(f"✅ {member.display_name} ora ha {max(0, xp)} XP (livello {level_of(max(0, xp))[0]}).")


@impl("leveling reset-xp")
async def _lvl_reset_xp(ctx, member: Optional[discord.Member] = None, conferma: str = ""):
    if member:
        sec(ctx.guild.id, "xp").pop(str(member.id), None)
    else:
        need_confirm(conferma)
        sec(ctx.guild.id, "xp").clear()
    save()
    await ctx.send("🧹 XP reimpostati.")


# --- attività
@impl("activity stats")
async def _activity_stats(ctx):
    a = gdata(ctx.guild.id).get("activity", {})
    await ctx.send(embed=emb("📈 Attività", f"Utenti tracciati: **{len(a)}**\nMessaggi contati: **{sum(x['messages'] for x in a.values())}**\nIl bot conta i messaggi dall'attivazione."))


@impl("activity member")
async def _activity_member(ctx, member: Optional[discord.Member] = None):
    await _member_activity(ctx, member)


@impl("activity leaderboard")
async def _activity_leaderboard(ctx):
    a = sorted(gdata(ctx.guild.id).get("activity", {}).items(), key=lambda kv: -kv[1]["messages"])[:10]
    await ctx.send(embed=emb("🏅 Classifica attività", "\n".join(f"`{i}.` <@{u}> — {x['messages']} messaggi" for i, (u, x) in enumerate(a, 1)) or "Nessun dato."), allowed_mentions=NOMENTION)


@impl("activity reset")
async def _activity_reset(ctx, conferma: str = ""):
    need_confirm(conferma)
    gdata(ctx.guild.id)["activity"] = {}
    save()
    await ctx.send("🧹 Statistiche di attività azzerate.")


# --------------------------------------------------------------- AUTOMAZIONI E TRIGGER
def _auto_ok(evento):
    return evento in ("join", "leave") or evento.startswith("parola:")


async def run_automation(guild, a, member=None, channel=None):
    act, val = a["azione"], a["valore"]
    try:
        if act == "messaggio":
            ch = guild.get_channel(a.get("canale") or 0) or channel or guild.system_channel
            await ch.send(fmt_msg(val, member) if member else val, allowed_mentions=discord.AllowedMentions(users=True))
        elif act == "ruolo" and member:
            role = guild.get_role((ids_in(val) or [0])[0])
            await member.add_roles(role, reason=f"Automazione {a['nome']}")
        elif act == "dm" and member:
            await member.send(fmt_msg(val, member))
    except Exception:
        pass
    a["runs"] = a.get("runs", 0) + 1
    h = sec(guild.id, "auto_hist", list)
    h.append({"ts": now(), "id": a["id"], "user": member.id if member else 0})
    del h[:-100]
    save()


def _autos(gid, evento_prefix):
    return [a for a in sec(gid, "automations").values() if a["enabled"] and a["evento"].startswith(evento_prefix)]


@bot.listen("on_member_join")
async def _auto_join(member):
    for a in _autos(member.guild.id, "join"):
        await run_automation(member.guild, a, member)


@bot.listen("on_member_remove")
async def _auto_leave(member):
    for a in _autos(member.guild.id, "leave"):
        await run_automation(member.guild, a, member)


@bot.listen("on_message")
async def _auto_message(message):
    if message.guild is None or message.author.bot:
        return
    low = message.content.lower()
    for a in _autos(message.guild.id, "parola:"):
        if a["evento"][7:].lower() in low:
            await run_automation(message.guild, a, message.author, message.channel)


@impl("automation create")
async def _auto_create(ctx, nome: str, evento: str, azione: str, canale: Optional[discord.TextChannel] = None, *, valore: str):
    if not _auto_ok(evento) or azione not in ("messaggio", "ruolo", "dm"):
        raise fail("Evento: `join`, `leave`, `parola:<testo>` — Azione: `messaggio`, `ruolo` (valore = ruolo), `dm`.")
    a = {"id": nid(ctx.guild.id, "automation"), "nome": nome, "evento": evento, "azione": azione, "valore": valore,
         "canale": canale.id if canale else 0, "enabled": True, "runs": 0}
    sec(ctx.guild.id, "automations")[str(a["id"])] = a
    save()
    await ctx.send(f"⚙️ Automazione `{a['id']}` **{nome}** creata: quando `{evento}` → {azione}.")


def _auto_get(ctx, id):
    a = sec(ctx.guild.id, "automations").get(str(id))
    if not a:
        raise fail("Automazione non trovata.")
    return a


@impl("automation edit")
async def _auto_edit(ctx, id: int, campo: str, *, valore: str):
    a = _auto_get(ctx, id)
    if campo not in ("nome", "evento", "azione", "valore", "canale"):
        raise fail("Campi: nome, evento, azione, valore, canale.")
    if campo == "evento" and not _auto_ok(valore):
        raise fail("Evento non valido.")
    a[campo] = (ids_in(valore) or [0])[0] if campo == "canale" else valore
    save()
    await ctx.send("✏️ Automazione aggiornata.")


@impl("automation delete")
async def _auto_delete(ctx, id: int):
    _auto_get(ctx, id)
    del sec(ctx.guild.id, "automations")[str(id)]
    save()
    await ctx.send("🗑️ Automazione eliminata.")


@impl("automation enable")
async def _auto_enable(ctx, id: int):
    _auto_get(ctx, id)["enabled"] = True
    save()
    await ctx.send("🟢 Automazione attivata.")


@impl("automation disable")
async def _auto_disable(ctx, id: int):
    _auto_get(ctx, id)["enabled"] = False
    save()
    await ctx.send("🔴 Automazione disattivata.")


@impl("automation list")
async def _auto_list(ctx):
    await ctx.send("\n".join(f"`{a['id']}` {'🟢' if a['enabled'] else '🔴'} **{a['nome']}** · `{a['evento']}` → {a['azione']} ({a.get('runs', 0)} esecuzioni)"
                             for a in sec(ctx.guild.id, "automations").values()) or "Nessuna automazione.")


@impl("automation test")
async def _auto_test(ctx, id: int):
    await run_automation(ctx.guild, _auto_get(ctx, id), ctx.author, ctx.channel)
    await ctx.send("🧪 Automazione eseguita su di te come prova.", ephemeral=True)


@impl("automation history")
async def _auto_history(ctx, id: Optional[int] = None):
    h = [x for x in sec(ctx.guild.id, "auto_hist", list) if id is None or x["id"] == id][-15:]
    await ctx.send("\n".join(f"<t:{x['ts']}:R> automazione `{x['id']}`" + (f" su <@{x['user']}>" if x["user"] else "") for x in reversed(h)) or "Nessuna esecuzione.", allowed_mentions=NOMENTION)


_trig_cd = {}


@bot.listen("on_message")
async def _trigger_listener(message):
    if message.guild is None or message.author.bot:
        return
    low = message.content.lower()
    for t in sec(message.guild.id, "triggers").values():
        if t["enabled"] and t["pattern"].lower() in low and time.time() - _trig_cd.get((message.channel.id, t["id"]), 0) > 5:
            _trig_cd[(message.channel.id, t["id"])] = time.time()
            try:
                await message.reply(t["response"], mention_author=False, allowed_mentions=NOMENTION)
            except discord.HTTPException:
                pass
            break


@impl("trigger create")
async def _trigger_create(ctx, parola: str, *, risposta: str):
    t = {"id": nid(ctx.guild.id, "trigger"), "pattern": parola, "response": risposta, "enabled": True}
    sec(ctx.guild.id, "triggers")[str(t["id"])] = t
    save()
    await ctx.send(f"⚡ Trigger `{t['id']}`: quando qualcuno scrive «{parola}» rispondo.")


@impl("trigger edit")
async def _trigger_edit(ctx, id: int, parola: Optional[str] = None, *, risposta: Optional[str] = None):
    t = sec(ctx.guild.id, "triggers").get(str(id))
    if not t:
        raise fail("Trigger non trovato.")
    if parola:
        t["pattern"] = parola
    if risposta:
        t["response"] = risposta
    save()
    await ctx.send("✏️ Trigger aggiornato.")


@impl("trigger delete")
async def _trigger_delete(ctx, id: int):
    if sec(ctx.guild.id, "triggers").pop(str(id), None) is None:
        raise fail("Trigger non trovato.")
    save()
    await ctx.send("🗑️ Trigger eliminato.")


@impl("trigger list")
async def _trigger_list(ctx):
    await ctx.send("\n".join(f"`{t['id']}` «{t['pattern']}» → {t['response'][:60]}" for t in sec(ctx.guild.id, "triggers").values()) or "Nessun trigger.")


@impl("trigger test")
async def _trigger_test(ctx, *, testo: str):
    hit = [t for t in sec(ctx.guild.id, "triggers").values() if t["enabled"] and t["pattern"].lower() in testo.lower()]
    await ctx.send(f"⚡ Scatterebbe il trigger `{hit[0]['id']}`: {hit[0]['response']}" if hit else "Nessun trigger scatta con questo testo.", allowed_mentions=NOMENTION)


# ============================================================================
# ESTENSIONE 5: giveaway, eventi, suggerimenti, votazioni
# ============================================================================
def gw_embed(g):
    cfg_color = pcolor(g.get("color", "viola"))
    if g["status"] == "active":
        e = discord.Embed(title=f"🎉 {g['prize']}", color=cfg_color, description=(
            f"Premi il pulsante per partecipare!\n**Vincitori:** {g['winners']}\n**Termina:** <t:{g['end']}:R>\n"
            f"**Organizzato da:** <@{g['host']}>"))
        e.set_footer(text=f"Partecipanti: {len(g['entrants'])} · ID {g['id']}")
    else:
        w = " ".join(f"<@{u}>" for u in g["winner_ids"]) or "nessun partecipante"
        titles = {"ended": "Terminato", "cancelled": "Annullato"}
        e = discord.Embed(title=f"🎉 {g['prize']} — {titles[g['status']]}", color=0x99AAB5,
                          description=f"**Vincitori:** {w}\n**Organizzato da:** <@{g['host']}>")
        e.set_footer(text=f"Partecipanti: {len(g['entrants'])} · ID {g['id']}")
    return e


class GiveawayButton(discord.ui.DynamicItem[discord.ui.Button], template=r"gw:join:(?P<id>\d+)"):
    def __init__(self, gid, label="Partecipa"):
        super().__init__(discord.ui.Button(label=label, emoji="🎉", style=discord.ButtonStyle.primary, custom_id=f"gw:join:{gid}"))
        self.gid = gid

    @classmethod
    async def from_custom_id(cls, interaction, item, match, /):
        return cls(int(match["id"]))

    async def callback(self, interaction: discord.Interaction):
        g = sec(interaction.guild_id, "giveaways").get(str(self.gid))
        if not g or g["status"] != "active":
            return await interaction.response.send_message("Questo giveaway è terminato.", ephemeral=True)
        req = mod(interaction.guild_id, "giveaway")["ruolo_richiesto"]
        if req and not any(r.id == req for r in interaction.user.roles):
            return await interaction.response.send_message(f"Serve il ruolo <@&{req}> per partecipare.", ephemeral=True)
        if interaction.user.id in g["entrants"]:
            g["entrants"].remove(interaction.user.id)
            msg = "❌ Hai ritirato la tua partecipazione."
        else:
            g["entrants"].append(interaction.user.id)
            msg = "✅ Partecipi al giveaway! Buona fortuna 🍀"
        save()
        await interaction.response.send_message(msg, ephemeral=True)
        try:
            await interaction.message.edit(embed=gw_embed(g))
        except discord.HTTPException:
            pass


async def gw_finish(guild, g, n=None, reroll=False):
    pool = [u for u in g["entrants"] if u not in g["winner_ids"]] if reroll else list(g["entrants"])
    pick = random.sample(pool, min(n or g["winners"], len(pool)))
    g["winner_ids"] = (g["winner_ids"] + pick) if reroll else pick
    g["status"] = "ended"
    save()
    ch = guild.get_channel(g["channel"])
    if ch:
        try:
            m = await ch.fetch_message(g["message"])
            await m.edit(embed=gw_embed(g), view=None)
        except discord.HTTPException:
            pass
        await safe_send(ch, content=(f"🎉 Congratulazioni {' '.join(f'<@{u}>' for u in pick)}! Avete vinto **{g['prize']}**!" if pick
                                     else f"😕 Nessun partecipante valido per **{g['prize']}**."),
                        allowed_mentions=discord.AllowedMentions(users=True))
    return pick


@job("giveaway_end")
async def _job_gw_end(gid, j):
    g = sec(gid, "giveaways").get(str(j["gw"]))
    guild = bot.get_guild(gid)
    if g and guild and g["status"] == "active":
        await gw_finish(guild, g)


def _gw_get(ctx, id):
    g = sec(ctx.guild.id, "giveaways").get(str(id))
    if not g:
        raise fail("Giveaway non trovato.")
    return g


@impl("giveaway create")
async def _gw_create(ctx, durata: str, vincitori: int, *, premio: str):
    d = pdur(durata)
    if not d or vincitori < 1:
        raise fail("Durata non valida (es. 30m, 2h, 7d) o vincitori < 1.")
    g = {"id": nid(ctx.guild.id, "giveaway"), "prize": premio, "winners": vincitori, "end": now() + d, "host": ctx.author.id,
         "channel": ctx.channel.id, "message": 0, "entrants": [], "winner_ids": [], "status": "active",
         "color": mod(ctx.guild.id, "giveaway")["colore"]}
    v = discord.ui.View(timeout=None)
    v.add_item(GiveawayButton(g["id"]))
    msg = await ctx.channel.send(embed=gw_embed(g), view=v)
    g["message"] = msg.id
    sec(ctx.guild.id, "giveaways")[str(g["id"])] = g
    add_job(ctx.guild.id, "giveaway_end", g["end"], gw=g["id"])
    save()
    await _ack(ctx, f"✅ Giveaway `{g['id']}` avviato.")


@impl("giveaway edit")
async def _gw_edit(ctx, id: int, vincitori: Optional[int] = None, *, premio: Optional[str] = None):
    g = _gw_get(ctx, id)
    if g["status"] != "active":
        raise fail("Il giveaway non è più attivo.")
    if vincitori and vincitori > 0:
        g["winners"] = vincitori
    if premio:
        g["prize"] = premio
    save()
    try:
        m = await ctx.guild.get_channel(g["channel"]).fetch_message(g["message"])
        await m.edit(embed=gw_embed(g))
    except (discord.HTTPException, AttributeError):
        pass
    await ctx.send("✏️ Giveaway aggiornato.")


@impl("giveaway end")
async def _gw_end(ctx, id: int):
    g = _gw_get(ctx, id)
    if g["status"] != "active":
        raise fail("Il giveaway è già terminato.")
    del_job(ctx.guild.id, next((j["id"] for j in jobs_of(ctx.guild.id, "giveaway_end") if j["gw"] == id), -1), "giveaway_end")
    await gw_finish(ctx.guild, g)
    await _ack(ctx, "🏁 Giveaway terminato.")


@impl("giveaway reroll")
async def _gw_reroll(ctx, id: int, quanti: int = 1):
    g = _gw_get(ctx, id)
    if g["status"] != "ended":
        raise fail("Puoi rifare l'estrazione solo su giveaway terminati.")
    pick = await gw_finish(ctx.guild, g, n=max(1, quanti), reroll=True)
    await _ack(ctx, "🔁 Nuova estrazione fatta." if pick else "Nessun altro partecipante disponibile.")


@impl("giveaway cancel")
async def _gw_cancel(ctx, id: int):
    g = _gw_get(ctx, id)
    if g["status"] != "active":
        raise fail("Il giveaway non è attivo.")
    g["status"] = "cancelled"
    save()
    try:
        m = await ctx.guild.get_channel(g["channel"]).fetch_message(g["message"])
        await m.edit(embed=gw_embed(g), view=None)
    except (discord.HTTPException, AttributeError):
        pass
    await ctx.send("🚫 Giveaway annullato.")


@impl("giveaway list")
async def _gw_list(ctx):
    gs = [g for g in sec(ctx.guild.id, "giveaways").values() if g["status"] == "active"]
    await ctx.send("\n".join(f"`{g['id']}` 🎉 **{g['prize']}** — termina <t:{g['end']}:R> ({len(g['entrants'])} partecipanti) <#{g['channel']}>" for g in gs) or "Nessun giveaway attivo.")


@impl("giveaway entrants")
async def _gw_entrants(ctx, id: int):
    g = _gw_get(ctx, id)
    await ctx.send(f"**{len(g['entrants'])} partecipanti:** " + (", ".join(f"<@{u}>" for u in g["entrants"])[:1800] or "nessuno"), allowed_mentions=NOMENTION)


# --------------------------------------------------------------- EVENTI
EV_STATUS_COLOR = {"programmato": 0x5865F2, "in corso": 0x57F287, "concluso": 0x99AAB5, "annullato": 0xED4245}


def ev_embed(e):
    em = discord.Embed(title=f"📅 {e['name']}", description=e["desc"] or None, color=EV_STATUS_COLOR[e["status"]])
    em.add_field(name="Inizio", value=f"<t:{e['start']}:F> (<t:{e['start']}:R>)")
    em.add_field(name="Stato", value=e["status"])
    em.add_field(name="Partecipanti", value=str(len(e["participants"])))
    if e.get("results"):
        em.add_field(name="Risultati", value=e["results"][:1000], inline=False)
    em.set_footer(text=f"ID {e['id']}")
    return em


class EventButton(discord.ui.DynamicItem[discord.ui.Button], template=r"ev:join:(?P<id>\d+)"):
    def __init__(self, eid):
        super().__init__(discord.ui.Button(label="Partecipo", emoji="🙋", style=discord.ButtonStyle.success, custom_id=f"ev:join:{eid}"))
        self.eid = eid

    @classmethod
    async def from_custom_id(cls, interaction, item, match, /):
        return cls(int(match["id"]))

    async def callback(self, interaction: discord.Interaction):
        e = sec(interaction.guild_id, "events").get(str(self.eid))
        if not e or e["status"] in ("concluso", "annullato"):
            return await interaction.response.send_message("Questo evento è concluso.", ephemeral=True)
        if interaction.user.id in e["participants"]:
            e["participants"].remove(interaction.user.id)
            msg = "❌ Non partecipi più."
        else:
            e["participants"].append(interaction.user.id)
            msg = "✅ Ti sei iscritto all'evento!"
        save()
        await interaction.response.send_message(msg, ephemeral=True)
        try:
            await interaction.message.edit(embed=ev_embed(e))
        except discord.HTTPException:
            pass


def _ev_get(ctx, id):
    e = sec(ctx.guild.id, "events").get(str(id))
    if not e:
        raise fail("Evento non trovato.")
    return e


async def ev_refresh(guild, e, view=True):
    try:
        m = await guild.get_channel(e["channel"]).fetch_message(e["message"])
        v = None
        if view and e["status"] in ("programmato", "in corso"):
            v = discord.ui.View(timeout=None)
            v.add_item(EventButton(e["id"]))
        await m.edit(embed=ev_embed(e), view=v)
    except (discord.HTTPException, AttributeError):
        pass


@job("event_start")
async def _job_ev_start(gid, j):
    e, g = sec(gid, "events").get(str(j["ev"])), bot.get_guild(gid)
    if e and g and e["status"] == "programmato":
        e["status"] = "in corso"
        save()
        await ev_refresh(g, e)
        await safe_send(g.get_channel(e["channel"]), content=f"🚀 **{e['name']}** è iniziato! " + " ".join(f"<@{u}>" for u in e["participants"][:50]),
                        allowed_mentions=discord.AllowedMentions(users=True))


@job("event_remind")
async def _job_ev_remind(gid, j):
    e = sec(gid, "events").get(str(j["ev"]))
    g = bot.get_guild(gid)
    if e and g and e["status"] == "programmato":
        for u in e["participants"]:
            m = g.get_member(u)
            if m:
                try:
                    await m.send(f"⏰ **{e['name']}** inizia <t:{e['start']}:R> in **{g.name}**!")
                except discord.HTTPException:
                    pass


@impl("event create")
async def _ev_create(ctx, tra: str, nome: str, *, descrizione: str = ""):
    at = pwhen(tra, ctx.guild.id)
    if not at:
        raise fail("Quando: 2h, 1d oppure HH:MM.")
    e = {"id": nid(ctx.guild.id, "event"), "name": nome, "desc": descrizione, "start": at, "channel": ctx.channel.id,
         "message": 0, "status": "programmato", "participants": [], "results": ""}
    v = discord.ui.View(timeout=None)
    v.add_item(EventButton(e["id"]))
    msg = await ctx.channel.send(embed=ev_embed(e), view=v)
    e["message"] = msg.id
    sec(ctx.guild.id, "events")[str(e["id"])] = e
    add_job(ctx.guild.id, "event_start", at, ev=e["id"])
    save()
    await _ack(ctx, f"✅ Evento `{e['id']}` creato.")


@impl("event edit")
async def _ev_edit(ctx, id: int, nome: Optional[str] = None, *, descrizione: Optional[str] = None):
    e = _ev_get(ctx, id)
    if nome:
        e["name"] = nome
    if descrizione is not None:
        e["desc"] = descrizione
    save()
    await ev_refresh(ctx.guild, e)
    await ctx.send("✏️ Evento aggiornato.")


@impl("event start")
async def _ev_start(ctx, id: int):
    e = _ev_get(ctx, id)
    if e["status"] != "programmato":
        raise fail("L'evento non è in stato «programmato».")
    del_job(ctx.guild.id, next((j["id"] for j in jobs_of(ctx.guild.id, "event_start") if j["ev"] == id), -1), "event_start")
    await _job_ev_start(ctx.guild.id, {"ev": id})
    await _ack(ctx, "🚀 Evento avviato.")


@impl("event end")
async def _ev_end(ctx, id: int, *, risultati: str = ""):
    e = _ev_get(ctx, id)
    e.update(status="concluso", results=risultati)
    save()
    await ev_refresh(ctx.guild, e)
    await safe_send(ctx.guild.get_channel(e["channel"]), embed=emb(f"🏁 {e['name']} concluso", risultati or "Grazie a tutti i partecipanti!", 0x99AAB5))
    await _ack(ctx, "🏁 Evento concluso.")


@impl("event cancel")
async def _ev_cancel(ctx, id: int):
    e = _ev_get(ctx, id)
    e["status"] = "annullato"
    save()
    await ev_refresh(ctx.guild, e)
    await ctx.send("🚫 Evento annullato.")


@impl("event announce")
async def _ev_announce(ctx, id: int, canale: Optional[discord.TextChannel] = None):
    e = _ev_get(ctx, id)
    v = discord.ui.View(timeout=None)
    v.add_item(EventButton(e["id"]))
    await (canale or ctx.channel).send(embed=ev_embed(e), view=v)
    await _ack(ctx, "📣 Evento annunciato.")


@impl("event reminder")
async def _ev_reminder(ctx, id: int, prima: str):
    e = _ev_get(ctx, id)
    d = pdur(prima)
    if not d or e["start"] - d <= now():
        raise fail("Durata non valida o già passata (es. 30m, 1h).")
    add_job(ctx.guild.id, "event_remind", e["start"] - d, ev=id)
    await ctx.send(f"⏰ I partecipanti riceveranno un promemoria {fdur(d)} prima dell'inizio.")


@impl("event list")
async def _ev_list(ctx):
    es = [e for e in sec(ctx.guild.id, "events").values() if e["status"] in ("programmato", "in corso")]
    await ctx.send("\n".join(f"`{e['id']}` **{e['name']}** — {e['status']}, <t:{e['start']}:R> ({len(e['participants'])} iscritti)" for e in es) or "Nessun evento in programma.")


@impl("event participants")
async def _ev_participants(ctx, id: int):
    e = _ev_get(ctx, id)
    await ctx.send(f"**{len(e['participants'])} partecipanti:** " + (", ".join(f"<@{u}>" for u in e["participants"])[:1800] or "nessuno"), allowed_mentions=NOMENTION)


@impl("event results")
async def _ev_results(ctx, id: int):
    e = _ev_get(ctx, id)
    await ctx.send(embed=emb(f"🏁 {e['name']}", e["results"] or "Nessun risultato registrato (usa `/event end`)."))


# --------------------------------------------------------------- SUGGERIMENTI
SG_COLOR = {"in attesa": 0xFEE75C, "approvato": 0x57F287, "rifiutato": 0xED4245, "implementato": 0x3498DB, "in rivalutazione": 0xE67E22}


def sg_embed(s):
    e = emb(f"💡 Suggerimento #{s['id']}", s["text"], SG_COLOR[s["status"]])
    e.add_field(name="Autore", value=f"<@{s['user']}>")
    e.add_field(name="Stato", value=s["status"])
    if s.get("reason"):
        e.add_field(name="Motivazione", value=s["reason"][:1000], inline=False)
    return e


async def sg_post(guild, user_id, text, fallback_channel):
    m = mod(guild.id, "suggestion")
    ch = guild.get_channel(m["canale"]) if m["canale"] else fallback_channel
    s = {"id": nid(guild.id, "suggestion"), "user": user_id, "text": text, "status": "in attesa", "reason": "", "ts": now(), "channel": ch.id, "message": 0}
    msg = await ch.send(embed=sg_embed(s))
    for em in ("👍", "👎"):
        await msg.add_reaction(em)
    s["message"] = msg.id
    sec(guild.id, "suggestions")[str(s["id"])] = s
    save()
    return s


class SuggestModal(SafeModal, title="Nuovo suggerimento"):
    testo = discord.ui.TextInput(label="Il tuo suggerimento", style=discord.TextStyle.paragraph, max_length=1500)

    async def on_submit(self, interaction: discord.Interaction):
        s = await sg_post(interaction.guild, interaction.user.id, self.testo.value, interaction.channel)
        await interaction.response.send_message(f"✅ Suggerimento **#{s['id']}** inviato!", ephemeral=True)


class SuggestButton(discord.ui.DynamicItem[discord.ui.Button], template=r"sg:new"):
    def __init__(self):
        super().__init__(discord.ui.Button(label="Suggerisci", emoji="💡", style=discord.ButtonStyle.primary, custom_id="sg:new"))

    @classmethod
    async def from_custom_id(cls, interaction, item, match, /):
        return cls()

    async def callback(self, interaction: discord.Interaction):
        await interaction.response.send_modal(SuggestModal())


@impl("suggestion panel-create")
async def _sg_panel_create(ctx, canale: discord.TextChannel):
    sg = sec(ctx.guild.id, "sg_panel")
    sg.setdefault("title", "💡 Suggerimenti")
    sg.setdefault("description", "Hai un'idea per il server? Premi il pulsante e raccontacela!")
    v = discord.ui.View(timeout=None)
    v.add_item(SuggestButton())
    msg = await canale.send(embed=emb(sg["title"], sg["description"]), view=v)
    sg.update(channel=canale.id, message=msg.id)
    save()
    await ctx.send(f"✅ Pannello suggerimenti pubblicato in {canale.mention}.")


@impl("suggestion panel-edit")
async def _sg_panel_edit(ctx, titolo: Optional[str] = None, *, descrizione: Optional[str] = None):
    sg = sec(ctx.guild.id, "sg_panel")
    if titolo:
        sg["title"] = titolo
    if descrizione:
        sg["description"] = descrizione
    save()
    try:
        m = await ctx.guild.get_channel(sg["channel"]).fetch_message(sg["message"])
        await m.edit(embed=emb(sg.get("title", "💡 Suggerimenti"), sg.get("description", "")))
    except (discord.HTTPException, AttributeError, KeyError):
        pass
    await ctx.send("✏️ Pannello aggiornato.")


@impl("suggestion create")
async def _sg_create(ctx, *, testo: str):
    s = await sg_post(ctx.guild, ctx.author.id, testo, ctx.channel)
    await ctx.send(f"✅ Suggerimento **#{s['id']}** inviato!", ephemeral=True)


def _sg_get(ctx, id):
    s = sec(ctx.guild.id, "suggestions").get(str(id))
    if not s:
        raise fail("Suggerimento non trovato.")
    return s


async def _sg_set(ctx, id, status, motivo):
    s = _sg_get(ctx, id)
    s.update(status=status, reason=motivo or "")
    m = mod(ctx.guild.id, "suggestion")
    if status == "approvato":
        m["approvati"] += 1
    elif status == "rifiutato":
        m["rifiutati"] += 1
    save()
    try:
        msg = await ctx.guild.get_channel(s["channel"]).fetch_message(s["message"])
        await msg.edit(embed=sg_embed(s))
    except (discord.HTTPException, AttributeError):
        pass
    u = ctx.guild.get_member(s["user"])
    if u:
        try:
            await u.send(f"💡 Il tuo suggerimento **#{id}** in **{ctx.guild.name}** è ora: **{status}**." + (f"\nMotivazione: {motivo}" if motivo else ""))
        except discord.HTTPException:
            pass
    await ctx.send(f"✅ Suggerimento #{id}: **{status}**.")


@impl("suggestion approve")
async def _sg_approve(ctx, id: int, *, motivo: Optional[str] = None):
    await _sg_set(ctx, id, "approvato", motivo)


@impl("suggestion reject")
async def _sg_reject(ctx, id: int, *, motivo: Optional[str] = None):
    await _sg_set(ctx, id, "rifiutato", motivo)


@impl("suggestion implement")
async def _sg_implement(ctx, id: int, *, nota: Optional[str] = None):
    await _sg_set(ctx, id, "implementato", nota)


@impl("suggestion reconsider")
async def _sg_reconsider(ctx, id: int, *, motivo: Optional[str] = None):
    await _sg_set(ctx, id, "in rivalutazione", motivo)


@impl("suggestion list")
async def _sg_list(ctx, stato: Optional[str] = None):
    ss = [s for s in sec(ctx.guild.id, "suggestions").values() if not stato or s["status"] == stato]
    await ctx.send("\n".join(f"`{s['id']}` **{s['status']}** — {s['text'][:70]}" for s in ss[-20:])[:1900] or "Nessun suggerimento.")


@impl("suggestion view")
async def _sg_view(ctx, id: int):
    await ctx.send(embed=sg_embed(_sg_get(ctx, id)))


@impl("suggestion leaderboard")
async def _sg_leaderboard(ctx):
    rows = []
    for s in list(sec(ctx.guild.id, "suggestions").values())[-15:]:
        try:
            m = await ctx.guild.get_channel(s["channel"]).fetch_message(s["message"])
            up = next((r.count - 1 for r in m.reactions if str(r.emoji) == "👍"), 0)
            rows.append((up, s))
        except (discord.HTTPException, AttributeError):
            pass
    rows.sort(key=lambda x: -x[0])
    await ctx.send(embed=emb("🏆 Suggerimenti più votati", "\n".join(f"`{i}.` 👍 **{up}** — #{s['id']} {s['text'][:60]}" for i, (up, s) in enumerate(rows[:10], 1)) or "Nessun dato."))


# --------------------------------------------------------------- VOTAZIONI
@impl("vote create")
async def _vote_create(ctx, domanda: str, opzioni: str = "", durata: Optional[str] = None):
    await _make_poll(ctx, domanda, opzioni, durata, kind="vote")


@impl("vote close")
async def _vote_close(ctx, id: int):
    await _poll_close(ctx, id)


@impl("vote results")
async def _vote_results(ctx, id: int):
    await _poll_results(ctx, id)


@impl("vote list")
async def _vote_list(ctx):
    ps = [p for p in sec(ctx.guild.id, "polls").values() if p.get("kind") == "vote"]
    await ctx.send("\n".join(f"`{p['id']}` {'🟢' if p['open'] else '🔴'} {p['q']}" for p in ps[-15:]) or "Nessuna votazione.")


# ============================================================================
# ESTENSIONE 6: pannelli, dashboard, backup, permessi/accessi, configurazione,
#               diagnostica, errori, comandi globali e sviluppatore
# ============================================================================
async def prefix_fn(b, message):
    pre = ["."]
    if message.guild:
        p = gdata(message.guild.id).get("config", {}).get("prefix")
        if p and p not in pre:
            pre.append(p)
    return commands.when_mentioned_or(*pre)(b, message)


bot.command_prefix = prefix_fn


# --------------------------------------------------------------- PANNELLI INTERATTIVI
PN_RE = r"[a-z0-9_-]+"


def _pn_get(ctx, nome):
    p = sec(ctx.guild.id, "panels").get(nome.lower())
    if not p:
        raise fail("Pannello non trovato. Disponibili: " + (", ".join(sec(ctx.guild.id, "panels")) or "nessuno"))
    return p


def pn_embed(p):
    return discord.Embed(title=p["title"] or None, description=p["description"] or None, color=pcolor(p.get("color", "blu")))


async def _pn_toggle_roles(interaction, role_ids, selected):
    guild, member = interaction.guild, interaction.user
    add, rem = [], []
    for rid in role_ids:
        r = guild.get_role(rid)
        if not r or r >= guild.me.top_role:
            continue
        if rid in selected and r not in member.roles:
            add.append(r)
        elif rid not in selected and r in member.roles:
            rem.append(r)
    try:
        if add:
            await member.add_roles(*add, reason="Pannello ruoli")
        if rem:
            await member.remove_roles(*rem, reason="Pannello ruoli")
    except discord.Forbidden:
        return await interaction.response.send_message("Non posso gestire quei ruoli: avvisa lo staff.", ephemeral=True)
    txt = ("➕ " + ", ".join(r.name for r in add) if add else "") + (" ➖ " + ", ".join(r.name for r in rem) if rem else "")
    await interaction.response.send_message(txt.strip() or "Nessuna modifica ai tuoi ruoli.", ephemeral=True)


class PanelButton(discord.ui.DynamicItem[discord.ui.Button], template=rf"pn:b:(?P<name>{PN_RE}):(?P<bid>{PN_RE})"):
    def __init__(self, name, b):
        super().__init__(discord.ui.Button(label=b["label"], emoji=b.get("emoji") or None, style=bstyle(b.get("style", "secondary")),
                                           custom_id=f"pn:b:{name}:{b['id']}"))
        self.name, self.bid = name, b["id"]

    @classmethod
    async def from_custom_id(cls, interaction, item, match, /):
        return cls(match["name"], {"id": match["bid"], "label": item.label, "emoji": item.emoji, "style": "secondary"})

    async def callback(self, interaction: discord.Interaction):
        p = sec(interaction.guild_id, "panels").get(self.name)
        b = next((x for x in (p or {}).get("buttons", []) if x["id"] == self.bid), None)
        if not b:
            return await interaction.response.send_message("Questo pulsante non esiste più.", ephemeral=True)
        if b["action"] == "ruolo":
            rid = (ids_in(b["value"]) or [0])[0]
            has = any(r.id == rid for r in interaction.user.roles)
            return await _pn_toggle_roles(interaction, [rid], [] if has else [rid])
        await interaction.response.send_message(b["value"][:1900], ephemeral=True)


def _build_select(name, s):
    opts = [discord.SelectOption(label=o["label"][:100], value=str(o["role"])) for o in s["options"][:25]]
    return discord.ui.Select(custom_id=f"pn:s:{name}:{s['id']}", placeholder=s["placeholder"][:150], min_values=0,
                             max_values=len(opts) if s.get("multi") else 1, options=opts)


class PanelSelect(discord.ui.DynamicItem[discord.ui.Select], template=rf"pn:s:(?P<name>{PN_RE}):(?P<sid>{PN_RE})"):
    def __init__(self, name, sid, select):
        super().__init__(select)
        self.name, self.sid = name, sid

    @classmethod
    async def from_custom_id(cls, interaction, item, match, /):
        return cls(match["name"], match["sid"], item)

    async def callback(self, interaction: discord.Interaction):
        p = sec(interaction.guild_id, "panels").get(self.name)
        s = next((x for x in (p or {}).get("selects", []) if x["id"] == self.sid), None)
        if not s:
            return await interaction.response.send_message("Questo menu non esiste più.", ephemeral=True)
        chosen = [int(v) for v in interaction.data.get("values", [])]
        await _pn_toggle_roles(interaction, [o["role"] for o in s["options"]], chosen)


def pn_view(name, p):
    v = discord.ui.View(timeout=None)
    for b in p["buttons"][:15]:
        if b["action"] == "link":
            v.add_item(discord.ui.Button(label=b["label"], emoji=b.get("emoji") or None, url=b["value"]))
        else:
            v.add_item(PanelButton(name, b))
    for s in p["selects"][:3]:
        v.add_item(PanelSelect(name, s["id"], _build_select(name, s)))
    return v


async def pn_refresh(guild, name, p):
    m = p.get("message")
    if not m:
        return
    try:
        msg = await guild.get_channel(m["channel"]).fetch_message(m["id"])
        await msg.edit(embed=pn_embed(p), view=pn_view(name, p))
    except (discord.HTTPException, AttributeError):
        pass


@impl("panel create")
async def _pn_create(ctx, nome: str, titolo: str, *, descrizione: str = ""):
    key = slug(nome)
    if key in sec(ctx.guild.id, "panels"):
        raise fail("Esiste già un pannello con questo nome.")
    sec(ctx.guild.id, "panels")[key] = {"title": titolo, "description": descrizione.replace("\\n", "\n"), "color": "blu", "buttons": [], "selects": [], "message": None}
    save()
    await ctx.send(f"✅ Pannello `{key}` creato. Aggiungi pulsanti con `/panel button-add`, poi pubblicalo con `/panel publish`.")


@impl("panel edit")
async def _pn_edit(ctx, nome: str, titolo: Optional[str] = None, colore: Optional[str] = None, *, descrizione: Optional[str] = None):
    p = _pn_get(ctx, nome)
    if titolo:
        p["title"] = titolo
    if colore:
        p["color"] = colore
    if descrizione is not None:
        p["description"] = descrizione.replace("\\n", "\n")
    save()
    await pn_refresh(ctx.guild, nome.lower(), p)
    await ctx.send("✏️ Pannello aggiornato (anche quello già pubblicato).")


@impl("panel publish")
async def _pn_publish(ctx, nome: str, canale: Optional[discord.TextChannel] = None):
    p = _pn_get(ctx, nome)
    if not p["buttons"] and not p["selects"]:
        raise fail("Il pannello è vuoto: aggiungi almeno un pulsante o un menu.")
    ch = canale or ctx.channel
    msg = await ch.send(embed=pn_embed(p), view=pn_view(nome.lower(), p))
    p["message"] = {"channel": ch.id, "id": msg.id}
    save()
    await _ack(ctx, f"📣 Pannello pubblicato in {ch.mention}.")


@impl("panel delete")
async def _pn_delete(ctx, nome: str):
    p = _pn_get(ctx, nome)
    if p.get("message"):
        try:
            await (await ctx.guild.get_channel(p["message"]["channel"]).fetch_message(p["message"]["id"])).delete()
        except (discord.HTTPException, AttributeError):
            pass
    del sec(ctx.guild.id, "panels")[nome.lower()]
    save()
    await ctx.send("🗑️ Pannello eliminato.")


@impl("panel list")
async def _pn_list(ctx):
    await ctx.send("\n".join(f"`{k}` — {p['title']} ({len(p['buttons'])} pulsanti, {len(p['selects'])} menu)" + (" 📣" if p.get("message") else "")
                             for k, p in sec(ctx.guild.id, "panels").items()) or "Nessun pannello.")


@impl("panel preview")
async def _pn_preview(ctx, nome: str):
    await ctx.send("👁️ Anteprima (i pulsanti funzionano davvero):", embed=pn_embed(_pn_get(ctx, nome)), view=pn_view(nome.lower(), _pn_get(ctx, nome)))


@impl("panel clone")
async def _pn_clone(ctx, nome: str, nuovo: str):
    p = _pn_get(ctx, nome)
    key = slug(nuovo)
    if key in sec(ctx.guild.id, "panels"):
        raise fail("Esiste già un pannello con quel nome.")
    c = copy.deepcopy(p)
    c["message"] = None
    sec(ctx.guild.id, "panels")[key] = c
    save()
    await ctx.send(f"📄 Pannello duplicato come `{key}`.")


@impl("panel button-add")
async def _pn_button_add(ctx, pannello: str, label: str, azione: str, valore: str, emoji: Optional[str] = None, stile: str = "grigio"):
    p = _pn_get(ctx, pannello)
    if azione not in ("ruolo", "messaggio", "link") or STYLE.get(stile.lower()) is None:
        raise fail("Azione: ruolo (valore = ruolo), messaggio (valore = testo), link (valore = URL). Stile: blu, grigio, verde, rosso.")
    if azione == "link" and not valore.startswith(("http://", "https://")):
        raise fail("Il link deve iniziare con http:// o https://")
    if azione == "ruolo" and not ids_in(valore):
        raise fail("Per l'azione ruolo menziona il ruolo (o scrivi il suo ID).")
    if len(p["buttons"]) >= 15 or (emoji and not valid_emoji(emoji)):
        raise fail("Massimo 15 pulsanti, oppure emoji non valida.")
    b = {"id": unique_id(slug(label), p["buttons"]), "label": label, "emoji": emoji or "", "style": STYLE[stile.lower()], "action": azione, "value": valore}
    p["buttons"].append(b)
    save()
    await pn_refresh(ctx.guild, pannello.lower(), p)
    await ctx.send(f"✅ Pulsante `{b['id']}` aggiunto.")


@impl("panel button-edit")
async def _pn_button_edit(ctx, pannello: str, id: str, campo: str, *, valore: str):
    p = _pn_get(ctx, pannello)
    b = next((x for x in p["buttons"] if x["id"] == id), None)
    if not b:
        raise fail("Pulsante non trovato: " + ", ".join(x["id"] for x in p["buttons"]))
    if campo == "stile":
        valore = STYLE.get(valore.lower()) or fail_("Stile non valido.")
        campo = "style"
    if campo not in ("label", "emoji", "style", "action", "value"):
        raise fail("Campi: label, emoji, stile, action, value.")
    b[campo] = valore
    save()
    await pn_refresh(ctx.guild, pannello.lower(), p)
    await ctx.send("✏️ Pulsante aggiornato.")


def fail_(msg):
    raise fail(msg)


@impl("panel button-remove")
async def _pn_button_remove(ctx, pannello: str, id: str):
    p = _pn_get(ctx, pannello)
    n = len(p["buttons"])
    p["buttons"] = [b for b in p["buttons"] if b["id"] != id]
    if len(p["buttons"]) == n:
        raise fail("Pulsante non trovato.")
    save()
    await pn_refresh(ctx.guild, pannello.lower(), p)
    await ctx.send("🗑️ Pulsante rimosso.")


def _roles_opts(ctx, text):
    opts = []
    for rid in ids_in(text)[:25]:
        r = ctx.guild.get_role(rid)
        if r and not r.is_default():
            opts.append({"label": r.name, "role": r.id})
    if not opts:
        raise fail("Menziona almeno un ruolo valido.")
    return opts


@impl("panel select-add")
async def _pn_select_add(ctx, pannello: str, placeholder: str, ruoli: str, multi: bool = False):
    p = _pn_get(ctx, pannello)
    if len(p["selects"]) >= 3:
        raise fail("Massimo 3 menu per pannello.")
    s = {"id": unique_id(slug(placeholder)[:20], p["selects"]), "placeholder": placeholder, "options": _roles_opts(ctx, ruoli), "multi": multi}
    p["selects"].append(s)
    save()
    await pn_refresh(ctx.guild, pannello.lower(), p)
    await ctx.send(f"✅ Menu `{s['id']}` aggiunto ({len(s['options'])} ruoli).")


@impl("panel select-edit")
async def _pn_select_edit(ctx, pannello: str, id: str, placeholder: Optional[str] = None, ruoli: Optional[str] = None, multi: Optional[bool] = None):
    p = _pn_get(ctx, pannello)
    s = next((x for x in p["selects"] if x["id"] == id), None)
    if not s:
        raise fail("Menu non trovato: " + ", ".join(x["id"] for x in p["selects"]))
    if placeholder:
        s["placeholder"] = placeholder
    if ruoli:
        s["options"] = _roles_opts(ctx, ruoli)
    if multi is not None:
        s["multi"] = multi
    save()
    await pn_refresh(ctx.guild, pannello.lower(), p)
    await ctx.send("✏️ Menu aggiornato.")


@impl("panel select-remove")
async def _pn_select_remove(ctx, pannello: str, id: str):
    p = _pn_get(ctx, pannello)
    n = len(p["selects"])
    p["selects"] = [s for s in p["selects"] if s["id"] != id]
    if len(p["selects"]) == n:
        raise fail("Menu non trovato.")
    save()
    await pn_refresh(ctx.guild, pannello.lower(), p)
    await ctx.send("🗑️ Menu rimosso.")


# --------------------------------------------------------------- DASHBOARD
@impl("dashboard link")
async def _dash_link(ctx):
    url = sec(ctx.guild.id, "config").get("dashboard_url")
    await ctx.send(f"🖥️ Dashboard: {url}" if url else "🖥️ Non c'è una dashboard web collegata. Un admin può impostarne l'indirizzo con `/dashboard settings`. "
                                                    "Tutta la gestione si fa comunque con i comandi del bot.")


@impl("dashboard status")
async def _dash_status(ctx):
    url = sec(ctx.guild.id, "config").get("dashboard_url")
    await ctx.send(embed=emb("🖥️ Stato", f"Bot: 🟢 online ({round(bot.latency * 1000)} ms)\nDashboard web: {'collegata' if url else 'non collegata'}\nModuli attivi: {len(REG)} comandi"))


@impl("dashboard settings")
async def _dash_settings(ctx, url: Optional[str] = None):
    cf = sec(ctx.guild.id, "config")
    if url is None:
        return await ctx.send(f"URL attuale: {cf.get('dashboard_url') or 'nessuno'}. Per cambiarlo: `/dashboard settings url:https://...` (`-` per rimuoverlo).")
    if url == "-":
        cf.pop("dashboard_url", None)
    elif url.startswith(("http://", "https://")):
        cf["dashboard_url"] = url
    else:
        raise fail("L'URL deve iniziare con http:// o https://")
    save()
    await ctx.send("✅ Impostazione salvata.")


# --------------------------------------------------------------- BACKUP
def snapshot_guild(guild, include_data=True):
    snap = {"ts": now(), "guild": guild.name,
            "roles": [{"name": r.name, "color": r.colour.value, "hoist": r.hoist, "mentionable": r.mentionable,
                       "permissions": r.permissions.value, "position": r.position} for r in guild.roles if not r.is_default() and not r.managed],
            "channels": [{"name": c.name, "type": str(c.type), "category": c.category.name if c.category else None, "position": c.position}
                         for c in guild.channels],
            "data": None}
    if include_data:
        snap["data"] = copy.deepcopy({k: v for k, v in gdata(guild.id).items() if k not in ("backups", "logs", "jobs")})
    return snap


def make_backup(guild, by, note=""):
    snap = snapshot_guild(guild, mod(guild.id, "backup")["includi_dati"])
    lst = sec(guild.id, "backups", list)
    b = {"id": nid(guild.id, "backup"), "ts": now(), "by": by, "note": note, "snap": snap}
    lst.append(b)
    del lst[:-max(1, mod(guild.id, "backup")["max_backup"])]
    save()
    return b


def _bk_get(ctx, id):
    b = next((x for x in sec(ctx.guild.id, "backups", list) if x["id"] == id), None)
    if not b:
        raise fail("Backup non trovato (usa `/backup list`).")
    return b


@job("backup")
async def _job_backup(gid, j):
    g = bot.get_guild(gid)
    if g:
        make_backup(g, 0, "automatico")


@impl("backup create")
async def _bk_create(ctx, *, nota: str = ""):
    b = make_backup(ctx.guild, ctx.author.id, nota)
    await ctx.send(f"💾 Backup **#{b['id']}** creato: {len(b['snap']['roles'])} ruoli, {len(b['snap']['channels'])} canali" + (", dati del bot inclusi." if b["snap"]["data"] else "."))


@impl("backup list")
async def _bk_list(ctx):
    await ctx.send("\n".join(f"`{b['id']}` <t:{b['ts']}:f> — {b['note'] or 'senza nota'} ({'bot' if not b['by'] else f'<@{b['by']}>'})" for b in sec(ctx.guild.id, "backups", list)) or "Nessun backup.", allowed_mentions=NOMENTION)


@impl("backup info")
async def _bk_info(ctx, id: int):
    b = _bk_get(ctx, id)
    s = b["snap"]
    await ctx.send(embed=emb(f"💾 Backup #{id}", f"Creato: <t:{b['ts']}:F>\nNota: {b['note'] or '—'}\nRuoli: {len(s['roles'])} · Canali: {len(s['channels'])}\n"
                                                  f"Dati del bot: {'sì (' + str(len(s['data'])) + ' sezioni)' if s['data'] else 'no'}\nDimensione: ~{len(json.dumps(s)) // 1024} KB"))


@impl("backup export")
async def _bk_export(ctx, id: int):
    b = _bk_get(ctx, id)
    await ctx.send("📤 Backup esportato:", file=discord.File(io.BytesIO(json.dumps(b, indent=2, ensure_ascii=False).encode()), filename=f"backup-{id}.json"), ephemeral=True)


def _bk_plan(guild, snap):
    have_r = {r.name for r in guild.roles}
    have_c = {c.name for c in guild.channels}
    return ([r for r in snap["roles"] if r["name"] not in have_r],
            [c for c in snap["channels"] if c["name"] not in have_c and c["type"] in ("text", "voice", "category")])


@impl("backup restore-preview")
async def _bk_preview(ctx, id: int):
    b = _bk_get(ctx, id)
    roles, chans = _bk_plan(ctx.guild, b["snap"])
    await ctx.send(embed=emb(f"🔍 Anteprima ripristino #{id}", (
        f"Ruoli da ricreare ({len(roles)}): {', '.join(r['name'] for r in roles[:15]) or '—'}\n"
        f"Canali da ricreare ({len(chans)}): {', '.join(c['name'] for c in chans[:15]) or '—'}\n"
        f"Dati del bot: {'verranno sostituiti con quelli del backup' if b['snap']['data'] else 'non inclusi'}\n\n"
        "Non verrà eliminato nulla. Per procedere: `/backup restore id conferma:CONFERMA`")))


@impl("backup restore")
async def _bk_restore(ctx, id: int, conferma: str = ""):
    need_confirm(conferma)
    b = _bk_get(ctx, id)
    snap = b["snap"]
    if ctx.interaction:
        await ctx.defer()
    roles, chans = _bk_plan(ctx.guild, snap)
    nr = nc = 0
    for r in sorted(roles, key=lambda x: x["position"]):
        try:
            await ctx.guild.create_role(name=r["name"], permissions=discord.Permissions(r["permissions"]), colour=discord.Colour(r["color"]),
                                        hoist=r["hoist"], mentionable=r["mentionable"], reason=f"Ripristino backup #{id}")
            nr += 1
        except discord.HTTPException:
            pass
    cats = {c.name: c for c in ctx.guild.categories}
    for c in [x for x in chans if x["type"] == "category"]:
        try:
            cats[c["name"]] = await ctx.guild.create_category(c["name"])
            nc += 1
        except discord.HTTPException:
            pass
    for c in [x for x in chans if x["type"] != "category"]:
        try:
            await (ctx.guild.create_text_channel if c["type"] == "text" else ctx.guild.create_voice_channel)(c["name"], category=cats.get(c["category"]))
            nc += 1
        except discord.HTTPException:
            pass
    if snap["data"]:
        gd = gdata(ctx.guild.id)
        keep = {k: gd[k] for k in ("backups", "logs", "jobs") if k in gd}
        gd.clear()
        gd.update(copy.deepcopy(snap["data"]))
        gd.update(keep)
    save()
    log_event(ctx.guild, "moderation", f"Backup #{id} ripristinato da {ctx.author}")
    await ctx.send(f"✅ Ripristino completato: {nr} ruoli e {nc} canali ricreati" + (", dati del bot ripristinati." if snap["data"] else "."))


@impl("backup delete")
async def _bk_delete(ctx, id: int):
    b = _bk_get(ctx, id)
    sec(ctx.guild.id, "backups", list).remove(b)
    save()
    await ctx.send("🗑️ Backup eliminato.")


@impl("backup schedule")
async def _bk_schedule(ctx, ogni: str = "1d"):
    d = pdur(ogni)
    if not d or d < 3600:
        raise fail("Intervallo minimo 1 ora (es. 12h, 1d, 1w).")
    for j in jobs_of(ctx.guild.id, "backup"):
        del_job(ctx.guild.id, j["id"], "backup")
    add_job(ctx.guild.id, "backup", now() + d, every=d)
    await ctx.send(f"🗓️ Backup automatico ogni **{fdur(d)}** (si conservano gli ultimi {mod(ctx.guild.id, 'backup')['max_backup']}).")


@impl("backup schedule-disable")
async def _bk_schedule_disable(ctx):
    n = 0
    for j in jobs_of(ctx.guild.id, "backup"):
        del_job(ctx.guild.id, j["id"], "backup")
        n += 1
    await ctx.send("🔴 Backup automatici disattivati." if n else "Nessun backup automatico attivo.")


@impl("backup history")
async def _bk_history(ctx):
    await _bk_list(ctx)


@impl("backup test")
async def _bk_test(ctx):
    try:
        size = len(json.dumps(snapshot_guild(ctx.guild))) // 1024
        res = f"✅ Snapshot possibile (~{size} KB)."
    except Exception as e:
        res = f"❌ Errore: {e}"
    me = ctx.guild.me.guild_permissions
    await ctx.send(f"{res}\nPermessi per il ripristino: Manage Roles {'✅' if me.manage_roles else '❌'} · Manage Channels {'✅' if me.manage_channels else '❌'}\n"
                   f"Backup automatico: {'🟢 attivo' if jobs_of(ctx.guild.id, 'backup') else '🔴 no'}")


# --------------------------------------------------------------- PERMESSI, ACCESSI, STAFF
LV = {"pubblico": "P", "tutti": "P", "staff": "S", "admin": "A"}
LV_IT = {"P": "pubblico", "S": "staff", "A": "admin", "O": "owner", "D": "dev"}


def _known_cmd(c):
    c = c.lower().strip().lstrip("/.")
    if not any(r["full"] == c or r["full"].split()[0] == c for r in REG):
        raise fail("Comando sconosciuto. Scrivi nome e sottocomando come `role add` (o solo il gruppo, es. `role`).")
    return c


@impl("permissions view")
async def _perm_view(ctx):
    gd = gdata(ctx.guild.id)
    over = gd.get("perm_over", {})
    await ctx.send(embed=emb("🔑 Autorizzazioni", (
        "**Livelli modificati:**\n" + ("\n".join(f"`/{k}` → {LV_IT[v]}" for k, v in over.items()) or "nessuno") +
        f"\n\n**Ruoli staff:** {', '.join(f'<@&{i}>' for i in gd['staff_roles']) or '—'}\n**Ruoli admin:** {', '.join(f'<@&{i}>' for i in gd['admin_roles']) or '—'}")))


@impl("permissions set")
async def _perm_set(ctx, comando: str, livello: str):
    c, lv = _known_cmd(comando), LV.get(livello.lower())
    if not lv:
        raise fail("Livello: pubblico, staff, admin.")
    sec(ctx.guild.id, "perm_over")[c] = lv
    save()
    await ctx.send(f"✅ `/{c}` ora richiede il livello **{LV_IT[lv]}** (oltre al permesso Discord).")


@impl("permissions remove")
async def _perm_remove(ctx, comando: str):
    if sec(ctx.guild.id, "perm_over").pop(comando.lower().strip().lstrip("/."), None) is None:
        raise fail("Nessuna modifica su quel comando.")
    save()
    await ctx.send("🗑️ Livello originale ripristinato.")


def _acc(gid):
    a = sec(gid, "access")
    for k in ("users", "roles", "channels"):
        a.setdefault(k, {})
    return a


@impl("access show")
async def _access_show(ctx):
    a = _acc(ctx.guild.id)
    lines = [f"<@{u}> → {', '.join(c)}" for u, c in a["users"].items()] + [f"<@&{r}> → {', '.join(c)}" for r, c in a["roles"].items()] + \
            [f"<#{ch}> ✖ {', '.join(v.get('deny', []))}" for ch, v in a["channels"].items() if v.get("deny")]
    await ctx.send(embed=emb("🔑 Regole di accesso", "\n".join(lines) or "Nessuna regola."), allowed_mentions=NOMENTION)


@impl("access grant")
async def _access_grant(ctx, bersaglio: str, comando: str):
    c, ids = _known_cmd(comando), ids_in(bersaglio)
    if not ids:
        raise fail("Menziona un utente o un ruolo.")
    kind = "roles" if ctx.guild.get_role(ids[0]) else "users"
    lst = _acc(ctx.guild.id)[kind].setdefault(str(ids[0]), [])
    if c not in lst:
        lst.append(c)
    save()
    await ctx.send(f"✅ Autorizzazione concessa per `/{c}` (salta livello interno e permesso Discord).")


@impl("access revoke")
async def _access_revoke(ctx, bersaglio: str, comando: str):
    c, ids = comando.lower().strip().lstrip("/."), ids_in(bersaglio)
    a = _acc(ctx.guild.id)
    for kind in ("users", "roles"):
        lst = a[kind].get(str(ids[0]) if ids else "", [])
        if c in lst:
            lst.remove(c)
    save()
    await ctx.send("🗑️ Autorizzazione revocata.")


@impl("access set-role")
async def _access_set_role(ctx, ruolo: discord.Role, livello: str):
    gd = gdata(ctx.guild.id)
    for k in ("staff_roles", "admin_roles"):
        if ruolo.id in gd[k]:
            gd[k].remove(ruolo.id)
    lv = livello.lower()
    if lv == "staff":
        gd["staff_roles"].append(ruolo.id)
    elif lv == "admin":
        gd["admin_roles"].append(ruolo.id)
    elif lv not in ("nessuno", "none"):
        raise fail("Livello: staff, admin, nessuno.")
    save()
    await ctx.send(f"✅ {ruolo.mention}: livello **{lv}**.", allowed_mentions=NOMENTION)


async def _set_user_level(ctx, member, livello):
    ul = sec(ctx.guild.id, "user_levels")
    lv = livello.lower()
    if lv in ("staff", "admin"):
        ul[str(member.id)] = lv
    elif lv in ("nessuno", "none"):
        ul.pop(str(member.id), None)
    else:
        raise fail("Livello: staff, admin, nessuno.")
    save()
    await ctx.send(f"✅ {member.display_name}: livello interno **{lv}**.")


@impl("access set-user")
async def _access_set_user(ctx, member: discord.Member, livello: str):
    await _set_user_level(ctx, member, livello)


@impl("staff permissions")
async def _staff_permissions(ctx, member: discord.Member, livello: str):
    await _set_user_level(ctx, member, livello)


@impl("access set-channel")
async def _access_set_channel(ctx, canale: discord.TextChannel, comando: str, consenti: bool = False):
    c = comando.lower().strip().lstrip("/.")
    deny = _acc(ctx.guild.id)["channels"].setdefault(str(canale.id), {}).setdefault("deny", [])
    if consenti and c in deny:
        deny.remove(c)
    elif not consenti and c not in deny:
        deny.append(c)
    save()
    await ctx.send(f"{'✅ consentito' if consenti else '🚫 bloccato'}: `/{c}` in {canale.mention}.")


@impl("access reset")
async def _access_reset(ctx, comando: str, conferma: str = ""):
    gd = gdata(ctx.guild.id)
    if comando.lower() == "tutto":
        need_confirm(conferma)
        gd["access"], gd["perm_over"] = {}, {}
    else:
        c = comando.lower().strip().lstrip("/.")
        gd.get("perm_over", {}).pop(c, None)
        a = _acc(ctx.guild.id)
        for kind in ("users", "roles"):
            for lst in a[kind].values():
                if c in lst:
                    lst.remove(c)
        for v in a["channels"].values():
            if c in v.get("deny", []):
                v["deny"].remove(c)
    save()
    await ctx.send("🔄 Regole ripristinate.")


@impl("access test")
async def _access_test(ctx, comando: str, member: Optional[discord.Member] = None):
    c = _known_cmd(comando)
    m = member or ctx.author
    r = next((x for x in REG if x["full"] == c), None) or next(x for x in REG if x["full"].split()[0] == c)
    gd = gdata(ctx.guild.id)
    level = gd.get("perm_over", {}).get(r["full"], r["level"])
    acc = _acc(ctx.guild.id)
    grants = list(acc["users"].get(str(m.id), [])) + [x for ro in m.roles for x in acc["roles"].get(str(ro.id), [])]
    if r["full"] in grants or r["full"].split()[0] in grants:
        return await ctx.send(f"✅ {m.display_name} può usare `/{r['full']}` (autorizzazione speciale).")
    ok_level = {"P": True, "S": is_staff(m), "A": is_admin(m), "O": m.id in OWNER_IDS, "D": m.id in DEV_IDS or m.id in OWNER_IDS}[level]
    ok_perm = not r["perm"] or getattr(m.guild_permissions, r["perm"], False)
    await ctx.send(f"Comando `/{r['full']}` per {m.display_name}:\nLivello richiesto **{LV_IT[level]}**: {'✅' if ok_level else '❌'}\n"
                   f"Permesso Discord {('`' + r['perm'] + '`') if r['perm'] else '(nessuno)'}: {'✅' if ok_perm else '❌'}\n"
                   f"**Risultato:** {'✅ consentito' if ok_level and ok_perm else '❌ negato'}")


@impl("staff audit")
async def _staff_audit(ctx, giorni: int = 30):
    t = now() - max(1, giorni) * 86400
    c = collections.Counter(x["mod"] for x in gdata(ctx.guild.id)["cases"] if x["ts"] >= t and x["mod"])
    await ctx.send(embed=emb(f"🧾 Attività staff ({giorni} giorni)", "\n".join(f"<@{u}> — {n} azioni di moderazione" for u, n in c.most_common(15)) or "Nessuna azione registrata."), allowed_mentions=NOMENTION)


# --------------------------------------------------------------- CONFIGURAZIONE E SERVER
def _cf(gid):
    return sec(gid, "config")


@impl("config view")
async def _config_view(ctx):
    cf = _cf(ctx.guild.id)
    await ctx.send(embed=emb("⚙️ Configurazione", (
        f"Lingua: **{cf.get('locale', 'it')}** · Fuso: **{cf.get('timezone', 'Europe/Rome')}**\nPrefisso: `.` e `{cf.get('prefix', '—')}` · Manutenzione: **{'sì' if cf.get('maintenance') else 'no'}**\n"
        f"Comandi disabilitati: {', '.join(f'`{c}`' for c in cf.get('disabled_cmds', [])) or '—'}\nModuli disattivati: {', '.join(cf.get('modules_off', [])) or '—'}\n"
        f"Cooldown: {', '.join(f'`{k}` {v}s' for k, v in cf.get('cooldowns', {}).items()) or '—'}")))


@impl("server settings")
async def _server_settings(ctx):
    await _config_view(ctx)


@impl("config edit")
async def _config_edit(ctx, chiave: str, *, valore: str):
    if chiave == "timezone":
        return await _schedule_timezone(ctx, valore)
    if chiave == "locale":
        return await _config_locale(ctx, valore)
    if chiave == "prefix":
        return await _server_prefix(ctx, valore)
    raise fail("Chiavi modificabili: locale, timezone, prefix. Per il resto usa i comandi `/config ...`.")


@impl("config reset")
async def _config_reset(ctx, conferma: str = ""):
    need_confirm(conferma)
    gd = gdata(ctx.guild.id)
    for k in ("config", "mods", "perm_over", "access", "logcfg", "security"):
        gd.pop(k, None)
    save()
    await ctx.send("🔄 Impostazioni ripristinate ai valori di default (dati come ticket, casi e note non toccati).")


@impl("server reset-settings")
async def _server_reset(ctx, conferma: str = ""):
    await _config_reset(ctx, conferma)


@impl("config validate")
async def _config_validate(ctx):
    g, gd, issues = ctx.guild, gdata(ctx.guild.id), []
    me = g.me.guild_permissions
    for p, label in ((me.send_messages, "Send Messages"), (me.embed_links, "Embed Links"), (me.manage_messages, "Manage Messages"), (me.manage_roles, "Manage Roles")):
        if not p:
            issues.append(f"Al bot manca il permesso {label}.")
    if not gd["staff_roles"] and not gd["admin_roles"]:
        issues.append("Nessun ruolo staff/admin impostato (`/staff add`).")
    for rid in gd["staff_roles"] + gd["admin_roles"]:
        if not g.get_role(rid):
            issues.append(f"Il ruolo staff {rid} non esiste più.")
    cfg = gd.get("logcfg", {})
    if not any(isinstance(v, int) and g.get_channel(v) for v in cfg.values()):
        issues.append("Nessun canale log impostato (`/logs setup`).")
    panels_of(g.id)
    for td in gd.get("ticket_panels", {}).values():
        if td.get("category_id") and not g.get_channel(td["category_id"]):
            issues.append(f"La categoria Discord del pannello «{td.get('name')}» non esiste più.")
    await ctx.send(embed=emb("🔎 Validazione configurazione", "\n".join(f"⚠️ {i}" for i in issues) or "✅ Nessun problema trovato.", 0xFEE75C if issues else 0x57F287))


@impl("config export")
async def _config_export(ctx):
    data = {k: v for k, v in gdata(ctx.guild.id).items() if k not in ("logs",)}
    await ctx.send("📤 Impostazioni esportate:", file=discord.File(io.BytesIO(json.dumps(data, indent=2, ensure_ascii=False).encode()), filename="impostazioni.json"), ephemeral=True)


@impl("server export-settings")
async def _server_export(ctx):
    await _config_export(ctx)


@impl("config import")
async def _config_import(ctx, file: discord.Attachment, conferma: str = ""):
    need_confirm(conferma)
    try:
        data = json.loads((await file.read()).decode("utf-8"))
        assert isinstance(data, dict)
    except Exception:
        raise fail("File JSON non valido (usa quello prodotto da `/config export`).")
    gd = gdata(ctx.guild.id)
    keep = {k: gd[k] for k in ("logs", "jobs") if k in gd}
    gd.clear()
    gd.update(data)
    gd.update(keep)
    gd.setdefault("cases", [])
    gd.setdefault("case_counter", 0)
    gd.setdefault("notes", {})
    gd.setdefault("staff_roles", [])
    gd.setdefault("admin_roles", [])
    save()
    await ctx.send("✅ Impostazioni importate.")


@impl("server import-settings")
async def _server_import(ctx, file: discord.Attachment, conferma: str = ""):
    await _config_import(ctx, file, conferma)


def _top_modules():
    seen = []
    for r in REG:
        t = r["full"].split()[0]
        if t not in seen and t not in ("config", "server", "help", "bot", "dev"):
            seen.append(t)
    return seen


@impl("config modules")
async def _config_modules(ctx):
    off = _cf(ctx.guild.id).get("modules_off", [])
    await ctx.send(embed=emb("🧩 Moduli", " · ".join(f"{'🚫' if m in off else '🟢'} {m}" for m in _top_modules())))


@impl("config module-enable")
async def _config_module_enable(ctx, modulo: str):
    off = _cf(ctx.guild.id).setdefault("modules_off", [])
    if modulo.lower() in off:
        off.remove(modulo.lower())
    save()
    await ctx.send(f"🟢 Modulo `{modulo}` abilitato.")


@impl("config module-disable")
async def _config_module_disable(ctx, modulo: str):
    m = modulo.lower()
    if m not in _top_modules():
        raise fail("Modulo sconosciuto: vedi `/config modules`.")
    off = _cf(ctx.guild.id).setdefault("modules_off", [])
    if m not in off:
        off.append(m)
    save()
    await ctx.send(f"🚫 Modulo `{m}` disabilitato (gli admin possono ancora usarlo).")


@impl("config command-enable")
async def _config_command_enable(ctx, comando: str):
    off = _cf(ctx.guild.id).setdefault("disabled_cmds", [])
    c = comando.lower().strip().lstrip("/.")
    if c in off:
        off.remove(c)
    save()
    await ctx.send(f"🟢 `/{c}` abilitato.")


@impl("config command-disable")
async def _config_command_disable(ctx, comando: str):
    c = _known_cmd(comando)
    if c.split()[0] in ("config", "server", "help"):
        raise fail("Questi comandi non si possono disabilitare.")
    off = _cf(ctx.guild.id).setdefault("disabled_cmds", [])
    if c not in off:
        off.append(c)
    save()
    await ctx.send(f"🚫 `/{c}` disabilitato (gli admin possono ancora usarlo).")


@impl("config cooldown-set")
async def _config_cooldown_set(ctx, comando: str, secondi: int):
    c = _known_cmd(comando)
    _cf(ctx.guild.id).setdefault("cooldowns", {})[c] = max(1, secondi)
    save()
    await ctx.send(f"⏳ Cooldown di `/{c}`: **{max(1, secondi)}s** (per utente).")


@impl("config cooldown-reset")
async def _config_cooldown_reset(ctx, comando: str):
    _cf(ctx.guild.id).setdefault("cooldowns", {}).pop(comando.lower().strip().lstrip("/."), None)
    save()
    await ctx.send("🔄 Cooldown rimosso.")


@impl("config locale")
async def _config_locale(ctx, lingua: str):
    if lingua.lower() not in ("it", "en"):
        raise fail("Lingue disponibili: it, en (per ora i messaggi del bot sono in italiano).")
    _cf(ctx.guild.id)["locale"] = lingua.lower()
    save()
    await ctx.send(f"🌐 Lingua impostata su `{lingua.lower()}`.")


@impl("server language")
async def _server_language(ctx, lingua: str):
    await _config_locale(ctx, lingua)


@impl("config timezone")
async def _config_timezone(ctx, fuso: str):
    await _schedule_timezone(ctx, fuso)


@impl("config maintenance")
async def _config_maintenance(ctx, stato: Optional[str] = None):
    cf = _cf(ctx.guild.id)
    cf["maintenance"] = (not cf.get("maintenance")) if stato is None else yes(stato) or stato.lower() in ("on", "attiva")
    save()
    await ctx.send("🛠️ Manutenzione **attiva**: solo gli admin possono usare il bot." if cf["maintenance"] else "✅ Manutenzione disattivata.")


@impl("server prefix")
async def _server_prefix(ctx, nuovo: Optional[str] = None):
    cf = _cf(ctx.guild.id)
    if nuovo is None:
        return await ctx.send(f"Prefissi: `.` e `/`" + (f" e `{cf['prefix']}`" if cf.get("prefix") else "") + ". Per aggiungerne uno: `/server prefix nuovo:!` (`-` per toglierlo).")
    if nuovo == "-":
        cf.pop("prefix", None)
    elif 0 < len(nuovo) <= 3 and nuovo != ".":
        cf["prefix"] = nuovo
    else:
        raise fail("Il prefisso deve essere di 1-3 caratteri (il punto resta sempre attivo).")
    save()
    await ctx.send("✅ Prefisso aggiornato.")


@impl("server setup")
async def _server_setup(ctx):
    gd, g = gdata(ctx.guild.id), ctx.guild
    await _sec_setup_quiet(ctx.guild.id)
    panels_of(ctx.guild.id)
    steps = [("Ruoli staff", bool(gd["staff_roles"] or gd["admin_roles"]), "`/staff add @ruolo`"),
             ("Canale log", bool(sec(g.id, "logcfg")), "`/logs setup #canale`"),
             ("Benvenuto", bool(sec(g.id, "welcome").get("enabled")), "`/welcome setup #canale`"),
             ("Autorole", bool(sec(g.id, "autoroles", list)), "`/autorole set @ruolo`"),
             ("Pannello ticket", any(p["panel"].get("message_id") for p in gd.get("ticket_panels", {}).values()), "`/ticketpanelcreate`"),
             ("Verifica membri", bool(sec(g.id, "screening").get("enabled")), "`/member-screening setup`")]
    await ctx.send(embed=emb("🧭 Configurazione iniziale", "Ho attivato le protezioni base (antispam, invite-filter, mention-limit, raid-protection, badwords).\n\n" +
                             "\n".join(f"{'✅' if ok else '⬜'} **{n}**" + ("" if ok else f" → {how}") for n, ok, how in steps)))


async def _sec_setup_quiet(gid):
    for n in ("antispam", "invite-filter", "mention-limit", "raid-protection", "badwords"):
        mod(gid, n)["enabled"] = True
    save()


# --------------------------------------------------------------- DIAGNOSTICA
NEEDED_PERMS = ["view_channel", "send_messages", "embed_links", "attach_files", "read_message_history", "manage_messages", "manage_channels",
                "manage_roles", "kick_members", "ban_members", "moderate_members", "manage_nicknames", "view_audit_log", "manage_events",
                "manage_threads", "add_reactions"]


def diag_lines(g):
    me = g.me.guild_permissions
    lines = [f"Latenza: {round(bot.latency * 1000)} ms", f"Uptime: {timedelta(seconds=int(time.time() - START))}"]
    miss = [p for p in NEEDED_PERMS if not getattr(me, p)]
    lines.append("Permessi del bot: " + ("✅ tutti presenti" if not miss else "❌ mancano " + ", ".join(miss)))
    top = g.me.top_role
    above = [r for r in g.roles if r > top and not r.managed]
    lines.append(f"Ruolo del bot: {top.name} (posizione {top.position}) — ruoli sopra di lui: {len(above)}")
    bad = [c.name for c in g.text_channels if not c.permissions_for(g.me).send_messages]
    lines.append("Canali dove il bot non può scrivere: " + (", ".join(bad[:10]) or "nessuno"))
    try:
        size = os.path.getsize(DB_FILE)
        lines.append(f"Database: {DB_FILE} ({size // 1024} KB, {len(DATA['guilds'])} server)")
    except OSError:
        lines.append("Database: file non ancora creato")
    lines.append(f"Intents: members={'✅' if bot.intents.members else '❌'} message_content={'✅' if bot.intents.message_content else '❌'}")
    return lines


@impl("diagnostics run")
async def _diag_run(ctx):
    await ctx.send(embed=emb("🩺 Diagnostica", "\n".join(diag_lines(ctx.guild))))


@impl("diagnostics permissions")
async def _diag_permissions(ctx):
    me = ctx.guild.me.guild_permissions
    await ctx.send(embed=emb("🔐 Permessi del bot", "\n".join(f"{'✅' if getattr(me, p) else '❌'} {p.replace('_', ' ').title()}" for p in NEEDED_PERMS)))


@impl("diagnostics channels")
async def _diag_channels(ctx):
    bad = []
    for c in ctx.guild.text_channels:
        p = c.permissions_for(ctx.guild.me)
        if not (p.view_channel and p.send_messages and p.embed_links):
            bad.append(f"{c.mention} ({'no vista' if not p.view_channel else 'no scrittura/embed'})")
    await ctx.send(embed=emb("📡 Accesso ai canali", f"Canali di testo: {len(ctx.guild.text_channels)}\nProblemi: {len(bad)}\n" + "\n".join(bad[:20])))


@impl("diagnostics roles")
async def _diag_roles(ctx):
    g, gd = ctx.guild, gdata(ctx.guild.id)
    top = g.me.top_role
    prob = [f"{r.mention} è sopra al bot" for r in (g.get_role(i) for i in gd["staff_roles"] + gd["admin_roles"]) if r and r > top]
    await ctx.send(embed=emb("🏷️ Ruoli e gerarchia", f"Ruolo del bot: {top.mention} (posizione {top.position}/{len(g.roles) - 1})\n"
                                                      f"Ruoli sopra al bot: {sum(1 for r in g.roles if r > top)}\n" + ("\n".join(prob) or "I ruoli staff sono tutti sotto il bot ✅")), allowed_mentions=NOMENTION)


@impl("diagnostics database")
async def _diag_database(ctx):
    try:
        save()
        with open(DB_FILE, encoding="utf-8") as f:
            json.load(f)
        res = f"✅ Lettura/scrittura ok · {os.path.getsize(DB_FILE) // 1024} KB · {len(DATA['guilds'])} server"
    except Exception as e:
        res = f"❌ Problema col database: {e}"
    await ctx.send(embed=emb("🗄️ Database", res))


@impl("diagnostics latency")
async def _diag_latency(ctx):
    t = time.perf_counter()
    m = await ctx.send("🏓 …")
    api = round((time.perf_counter() - t) * 1000)
    await m.edit(content=f"🏓 Gateway: **{round(bot.latency * 1000)} ms** · API: **{api} ms**")


@impl("diagnostics integrations")
async def _diag_integrations(ctx):
    try:
        import zoneinfo
        zi = "✅"
    except ImportError:
        zi = "❌"
    try:
        import dotenv
        de = "✅"
    except ImportError:
        de = "❌"
    await ctx.send(embed=emb("🔌 Integrazioni", f"discord.py {discord.__version__} · Python {sys.version.split()[0]}\nzoneinfo (fusi orari): {zi}\npython-dotenv: {de}\n"
                                                f"Slash command sincronizzati: {len(bot.tree.get_commands())}"))


@impl("diagnostics report")
async def _diag_report(ctx):
    txt = f"Rapporto diagnostico — {ctx.guild.name} — {datetime.now(timezone.utc):%Y-%m-%d %H:%M} UTC\n\n" + "\n".join(diag_lines(ctx.guild))
    await ctx.send("📄 Rapporto generato:", file=discord.File(io.BytesIO(txt.encode()), filename="diagnostica.txt"), ephemeral=True)


# --------------------------------------------------------------- ERRORI
def record_error(ctx, error):
    errs = DATA.setdefault("errors", [])
    e = {"id": (errs[-1]["id"] + 1) if errs else 1, "ts": now(), "cmd": ctx.command.qualified_name if ctx.command else "?",
         "user": ctx.author.id, "guild": ctx.guild.id if ctx.guild else 0, "type": type(error).__name__,
         "tb": "".join(traceback.format_exception(type(error), error, error.__traceback__))[-1500:], "resolved": False}
    errs.append(e)
    del errs[:-100]
    save()
    if ctx.guild:
        log_event(ctx.guild, "bot-errors", f"#{e['id']} `{e['cmd']}` → {e['type']}", ctx.author.id)


def _errs_for(ctx):
    owner = ctx.author.id in OWNER_IDS or ctx.author.id in DEV_IDS
    return [e for e in DATA.get("errors", []) if owner or e["guild"] == ctx.guild.id]


@impl("errors recent")
async def _errors_recent(ctx, quanti: int = 5):
    es = _errs_for(ctx)[-max(1, min(quanti, 15)):]
    await ctx.send("\n".join(f"`{e['id']}` {'✅' if e['resolved'] else '🔴'} <t:{e['ts']}:R> `/{e['cmd']}` — {e['type']}" for e in reversed(es)) or "Nessun errore registrato 🎉", allowed_mentions=NOMENTION)


@impl("errors details")
async def _errors_details(ctx, id: int):
    e = next((x for x in _errs_for(ctx) if x["id"] == id), None)
    if not e:
        raise fail("Errore non trovato.")
    await ctx.send(embed=emb(f"🐞 Errore #{id} ({'risolto' if e['resolved'] else 'aperto'})", f"Comando: `/{e['cmd']}` · Utente: <@{e['user']}> · <t:{e['ts']}:F>\n```py\n{e['tb'][-1500:]}\n```"), allowed_mentions=NOMENTION)


@impl("errors resolve")
async def _errors_resolve(ctx, id: int):
    e = next((x for x in _errs_for(ctx) if x["id"] == id), None)
    if not e:
        raise fail("Errore non trovato.")
    e["resolved"] = True
    save()
    await ctx.send(f"✅ Errore #{id} segnato come risolto.")


# --------------------------------------------------------------- COMANDI GLOBALI /bot
def _bot_flags():
    return DATA.setdefault("bot", {})


def _guild_arg(id):
    g = bot.get_guild(int(re.sub(r"\D", "", id) or 0))
    if not g:
        raise fail("Server non trovato (il bot deve esserci dentro). Usa `/bot servers`.")
    return g


@impl("bot server-info")
async def _bot_server_info(ctx, id: str):
    g = _guild_arg(id)
    await ctx.send(embed=emb(f"🌐 {g.name}", f"ID: `{g.id}`\nProprietario: <@{g.owner_id}>\nMembri: {g.member_count} · Canali: {len(g.channels)} · Ruoli: {len(g.roles)}\n"
                                              f"Creato: <t:{int(g.created_at.timestamp())}:R>\nDisabilitato: {'sì' if g.id in _bot_flags().get('disabled_guilds', []) else 'no'}"), allowed_mentions=NOMENTION)


@impl("bot server-config")
async def _bot_server_config(ctx, id: str, chiave: Optional[str] = None, *, valore: Optional[str] = None):
    g = _guild_arg(id)
    cf = sec(g.id, "config")
    if chiave is None:
        return await ctx.send(f"```json\n{json.dumps(cf, indent=1, ensure_ascii=False)[:1800]}\n```")
    if valore is None or valore == "-":
        cf.pop(chiave, None)
    else:
        cf[chiave] = yes(valore) if valore.lower() in ("true", "false", "si", "no") else valore
    save()
    await ctx.send(f"✅ `{chiave}` aggiornato per **{g.name}**.")


@impl("bot maintenance-on")
async def _bot_maint_on(ctx):
    _bot_flags()["maintenance"] = True
    save()
    await ctx.send("🛠️ Manutenzione globale **attiva**: solo owner e dev possono usare il bot.")


@impl("bot maintenance-off")
async def _bot_maint_off(ctx):
    _bot_flags()["maintenance"] = False
    save()
    await ctx.send("✅ Manutenzione globale disattivata.")


@impl("bot announce")
async def _bot_announce(ctx, *, testo: str):
    sent = 0
    for g in bot.guilds:
        ch = g.system_channel or next((c for c in g.text_channels if c.permissions_for(g.me).send_messages), None)
        if ch and await safe_send(ch, embed=emb("📢 Annuncio dal team del bot", testo)):
            sent += 1
    await ctx.send(f"📢 Annuncio inviato in {sent}/{len(bot.guilds)} server.")


@impl("bot reload-config")
async def _bot_reload(ctx):
    try:
        with open(DB_FILE, encoding="utf-8") as f:
            new = json.load(f)
    except (OSError, json.JSONDecodeError) as e:
        raise fail(f"Impossibile ricaricare: {e}")
    DATA.clear()
    DATA.update(new)
    DATA.setdefault("guilds", {})
    await ctx.send(f"🔄 Configurazione ricaricata da `{DB_FILE}` ({len(DATA['guilds'])} server).")


@impl("bot disable-server")
async def _bot_disable_server(ctx, id: str):
    g = _guild_arg(id)
    lst = _bot_flags().setdefault("disabled_guilds", [])
    if g.id not in lst:
        lst.append(g.id)
    save()
    await ctx.send(f"🚫 Bot disabilitato in **{g.name}**.")


@impl("bot enable-server")
async def _bot_enable_server(ctx, id: str):
    g = _guild_arg(id)
    lst = _bot_flags().setdefault("disabled_guilds", [])
    if g.id in lst:
        lst.remove(g.id)
    save()
    await ctx.send(f"✅ Bot riabilitato in **{g.name}**.")


@impl("bot database-status")
async def _bot_db_status(ctx):
    try:
        st = os.stat(DB_FILE)
        info = f"{st.st_size // 1024} KB, modificato <t:{int(st.st_mtime)}:R>"
    except OSError:
        info = "file non ancora creato"
    await ctx.send(embed=emb("🗄️ Database globale", f"`{DB_FILE}`: {info}\nServer registrati: **{len(DATA['guilds'])}**\nErrori registrati: **{len(DATA.get('errors', []))}**"))


@impl("bot backup-status")
async def _bot_backup_status(ctx, crea: bool = False):
    os.makedirs("backups", exist_ok=True)
    if crea:
        save()
        name = f"backups/data-{datetime.now():%Y%m%d-%H%M%S}.json"
        with open(name, "w", encoding="utf-8") as f:
            json.dump(DATA, f, ensure_ascii=False)
        for old in sorted(os.listdir("backups"))[:-5]:
            os.remove(os.path.join("backups", old))
    files = sorted(os.listdir("backups"))
    n_guild = sum(len(gd.get("backups", [])) for gd in DATA["guilds"].values())
    await ctx.send(embed=emb("💾 Backup globali", f"Copie del database ({len(files)}): " + (", ".join(files[-5:]) or "nessuna — usa `/bot backup-status crea:True`") +
                                                  f"\nBackup dei server: **{n_guild}**"))


@impl("bot error-report")
async def _bot_error_report(ctx):
    errs = DATA.get("errors", [])
    c = collections.Counter(e["cmd"] for e in errs if not e["resolved"])
    await ctx.send(embed=emb("🐞 Rapporto errori", f"Totali: **{len(errs)}** · Aperti: **{sum(c.values())}**\n" + ("\n".join(f"`/{k}` — {v}" for k, v in c.most_common(10)) or "Nessun errore aperto 🎉")))


# --------------------------------------------------------------- STRUMENTI SVILUPPATORE /dev
@impl("dev test-command")
async def _dev_test_command(ctx, comando: str):
    c = comando.lower().strip().lstrip("/.")
    r = next((x for x in REG if x["full"] == c), None)
    if not r:
        raise fail("Comando non registrato.")
    await ctx.send(f"`/{c}` — categoria {r['cat']} · livello **{LV_IT[r['level']]}** · permesso Discord: {r['perm'] or 'nessuno'} · "
                   f"logica: {'✅ implementata' if c in IMPL else '⚠️ segnaposto'}")


@impl("dev test-embed")
async def _dev_test_embed(ctx):
    e = emb("🧪 Embed di prova", "Descrizione con **grassetto**, *corsivo* e `codice`.")
    e.add_field(name="Campo 1", value="Valore")
    e.add_field(name="Campo 2", value="Valore")
    e.set_footer(text="Footer di prova")
    e.timestamp = datetime.now(timezone.utc)
    await ctx.send(embed=e)


@impl("dev test-permissions")
async def _dev_test_permissions(ctx):
    m = ctx.author
    await ctx.send(f"Staff: {'✅' if is_staff(m) else '❌'} · Admin: {'✅' if is_admin(m) else '❌'} · Owner bot: {'✅' if m.id in OWNER_IDS else '❌'} · "
                   f"Dev: {'✅' if m.id in DEV_IDS or m.id in OWNER_IDS else '❌'}\nPermessi Discord: {', '.join(n for n, v in m.guild_permissions if v)[:900] or 'nessuno'}")


@impl("dev test-logs")
async def _dev_test_logs(ctx):
    for k in LOG_KINDS:
        log_event(ctx.guild, k, f"Test log `{k}` da {ctx.author}", ctx.author.id)
    await ctx.send(f"✅ Scritti {len(LOG_KINDS)} eventi di prova.")


@impl("dev test-ticket")
async def _dev_test_ticket(ctx):
    g, out = ctx.guild, []
    for td in _all_panels(g.id):
        p = td["panel"]
        out.append(f"**{td['name']}** (`{td['pid']}`): categorie attive {sum(c['enabled'] for c in td['categories'])}, "
                   f"pulsanti interni attivi {sum(b['enabled'] for b in td['buttons'])}, pubblicato {'sì' if p['message_id'] else 'no'}, "
                   f"categoria Discord {'ok' if td['category_id'] and g.get_channel(td['category_id']) else 'no'}, aperti {len(td['open'])}, schema {td['v']}")
    out.append(f"Ruoli staff: {len(gdata(g.id)['staff_roles'] + gdata(g.id)['admin_roles'])} · Ruoli ping: " + str(len(gdata(g.id).get('ping_roles', [])) if 'ping_roles' in gdata(g.id) else 'default (staff)'))
    await ctx.send(embed=emb("🧪 Sistema ticket", "\n".join(out)[:4000]))


@impl("dev test-automation")
async def _dev_test_automation(ctx):
    rows = []
    for a in sec(ctx.guild.id, "automations").values():
        ok = _auto_ok(a["evento"]) and a["azione"] in ("messaggio", "ruolo", "dm") and (a["azione"] != "ruolo" or ctx.guild.get_role((ids_in(a["valore"]) or [0])[0]))
        rows.append(f"`{a['id']}` {a['nome']} — {'✅ valida' if ok else '❌ configurazione non valida'}")
    await ctx.send("\n".join(rows) or "Nessuna automazione da provare.")


@impl("dev database-check")
async def _dev_db_check(ctx):
    problems = []
    for gid, gd in DATA["guilds"].items():
        for k in ("cases", "notes", "staff_roles", "admin_roles"):
            if k not in gd:
                problems.append(f"{gid}: manca `{k}`")
    try:
        json.dumps(DATA)
    except TypeError as e:
        problems.append(f"non serializzabile: {e}")
    await ctx.send("✅ Database coerente." if not problems else "⚠️ " + "\n".join(problems[:15]))


@impl("dev database-migrations")
async def _dev_db_migrations(ctx):
    rows = []
    for gid, gd in list(DATA["guilds"].items()):
        if "tickets" in gd:
            panels_of(int(gid))  # migra il vecchio pannello unico in "p1"
            rows.append(f"{gid}: pannello unico → p1")
        for pid, td in list(gd.get("ticket_panels", {}).items()):
            v0 = td.get("v")
            tdata(int(gid), pid)
            rows.append(f"{gid}/{pid}: schema {v0} → {td['v']}")
    await ctx.send("\n".join(rows)[:1900] or "Nessun dato da migrare.")


@impl("dev cache-status")
async def _dev_cache(ctx):
    await ctx.send(embed=emb("🧠 Cache", f"Server: {len(bot.guilds)} · Utenti: {len(bot.users)} · Canali: {sum(len(g.channels) for g in bot.guilds)}\n"
                                        f"Membri in cache: {sum(len(g.members) for g in bot.guilds)} · Messaggi in cache: {len(bot.cached_messages)}"))


@impl("dev queue-status")
async def _dev_queue(ctx):
    c = collections.Counter(j["kind"] for gd in DATA["guilds"].values() for j in gd.get("jobs", []))
    nxt = min((j["at"] for gd in DATA["guilds"].values() for j in gd.get("jobs", [])), default=None)
    await ctx.send(embed=emb("📬 Code", "\n".join(f"`{k}`: {v}" for k, v in c.items()) + (f"\nProssimo lavoro: <t:{nxt}:R>" if nxt else "") if c else "Nessun lavoro in coda."))


@impl("dev job-list")
async def _dev_job_list(ctx, quanti: int = 15):
    js = [(gid, j) for gid, gd in DATA["guilds"].items() for j in gd.get("jobs", [])]
    js.sort(key=lambda x: x[1]["at"])
    await ctx.send("\n".join(f"server `{gid}` · `#{j['id']}` {j['kind']} <t:{j['at']}:R>" + (" 🔁" if j.get("every") else "") for gid, j in js[:max(1, min(quanti, 30))])[:1900] or "Nessun lavoro.")


@impl("dev job-retry")
async def _dev_job_retry(ctx, id: int):
    j = next((x for x in sec(ctx.guild.id, "jobs", list) if x["id"] == id), None)
    if not j:
        raise fail("Lavoro non trovato in questo server (vedi `/dev job-list`).")
    sec(ctx.guild.id, "jobs", list).remove(j)
    save()
    h = JOB_HANDLERS.get(j["kind"])
    if not h:
        raise fail(f"Nessun gestore per `{j['kind']}`.")
    await h(ctx.guild.id, j)
    await ctx.send(f"🔁 Lavoro `{id}` ({j['kind']}) eseguito ora.")


@impl("dev feature-flags")
async def _dev_flags(ctx):
    fl = _bot_flags().setdefault("flags", {})
    await ctx.send("\n".join(f"{'🟢' if v else '🔴'} `{k}`" for k, v in fl.items()) or "Nessun flag. Creane uno con `/dev feature-enable nome`.")


@impl("dev feature-enable")
async def _dev_flag_on(ctx, nome: str):
    _bot_flags().setdefault("flags", {})[nome.lower()] = True
    save()
    await ctx.send(f"🟢 Flag `{nome}` attivo.")


@impl("dev feature-disable")
async def _dev_flag_off(ctx, nome: str):
    _bot_flags().setdefault("flags", {})[nome.lower()] = False
    save()
    await ctx.send(f"🔴 Flag `{nome}` disattivo.")


@impl("dev diagnostics")
async def _dev_diagnostics(ctx):
    try:
        import resource
        mem = f"{resource.getrusage(resource.RUSAGE_SELF).ru_maxrss // 1024} MB"
    except Exception:
        mem = "n/d"
    await ctx.send(embed=emb("🧑‍💻 Diagnostica tecnica", f"Python {sys.version.split()[0]} · discord.py {discord.__version__} · {platform.system()}\n"
                                                         f"Task asyncio: {len(asyncio.all_tasks())} · Memoria picco: {mem}\nUptime: {timedelta(seconds=int(time.time() - START))}\n"
                                                         f"Comandi: {len(REG)} ({sum(1 for r in REG if r['full'] in IMPL)} implementati) · Latenza: {round(bot.latency * 1000)} ms\n"
                                                         f"Lavori pianificati: {sum(len(g.get('jobs', [])) for g in DATA['guilds'].values())}"))


# --------------------------------------------------------------- REGISTRAZIONE FINALE
def ext_register(b):
    """Pulsanti persistenti e scheduler: chiamato da setup_hook."""
    b.add_dynamic_items(VerifyButton, AppButton, AppDecideButton, GiveawayButton, EventButton, SuggestButton, PanelButton, PanelSelect)
    b._scheduler_task = asyncio.create_task(scheduler())


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
        try:
            record_error(ctx, error)
        except Exception:
            pass
        msg = "⚠️ Si è verificato un errore."
    try:
        await ctx.send(msg, ephemeral=True)
    except discord.HTTPException:
        pass


if __name__ == "__main__":
    if not TOKEN:
        raise SystemExit("Imposta DISCORD_TOKEN nel file .env")
    bot.run(TOKEN)
