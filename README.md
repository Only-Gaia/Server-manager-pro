# Server Manager Pro

Bot Discord in Python per gestire un server: moderazione, ruoli, canali, ticket, log, sicurezza e molto altro.
Tutto è in un unico file, `main.py`, e ogni comando funziona sia con `/` sia con il prefisso `.`.

## Caratteristiche

- **471 comandi** divisi in 25 categorie, con le descrizioni degli slash command in italiano.
- **Doppio prefisso:** `/comando` oppure `.comando` (funziona anche menzionando il bot).
- **Permessi a due livelli:** controlla sia il permesso Discord richiesto sia il livello interno (pubblico, staff, admin, owner, dev).
- **Sistema ticket completo e modificabile** direttamente da Discord con `/ticketpaneledit`.
- **Pulsanti persistenti:** i pannelli e i ticket restano funzionanti anche dopo un riavvio del bot.
- **Help integrato:** `/help` mostra le categorie con un menu a tendina in cui puoi sceglierne una o più. In alternativa scrivi `/help categoria:` e compaiono i nomi da selezionare (anche più di uno, separati da virgola), oppure `.help moderazione, ticket`.

## Stato dei comandi

Tutti i 471 comandi hanno una logica reale: nessuno risponde più "non ancora implementato".
Alcuni comandi sono pensati per funzionare dentro il server, quindi non vanno usati nei messaggi privati.

### Formati utili

- **Durate e orari:** `30s`, `10m`, `2h`, `1d`, `1w`. Un numero da solo vale minuti. Dove serve un orario si può scrivere `18:30` (fuso impostato con `/schedule timezone`).
- **Operazioni delicate** (reset, ripristino, import): vanno confermate aggiungendo `CONFERMA` come ultimo parametro.
- **Segnaposto nei messaggi** di benvenuto, addio, DM e livelli: `{user}`, `{name}`, `{server}`, `{count}`, `{level}`.
- **Riferimenti a messaggi:** si possono usare l'ID oppure il link del messaggio.

### Cosa fanno in automatico

Il bot controlla i messaggi (antispam, link, inviti, parole vietate, maiuscole, emoji, menzioni, allegati, regole automod), assegna gli autorole, conta XP e attività, scrive i log, aggiorna contatori e traguardi e gestisce i lavori pianificati (promemoria, annunci, giveaway, eventi, ruoli temporanei, backup automatici).
Le protezioni si attivano con `/security setup` o con i singoli comandi `enable`.

## Requisiti

- Python 3.10 o superiore
- [discord.py](https://github.com/Rapptz/discord.py) 2.4 o superiore

## Installazione

```bash
git clone <url-del-tuo-repository>
cd <cartella>
pip install -r requirements.txt
```

## Configurazione

1. Crea un'applicazione nel [Discord Developer Portal](https://discord.com/developers/applications) e aggiungi un bot.
2. Nella sezione **Bot** attiva questi intent:
   - **Server Members Intent**
   - **Message Content Intent** (serve per i comandi con il prefisso `.`)
3. Crea un file `.env` accanto a `main.py`:

```env
DISCORD_TOKEN=il_token_del_bot
OWNER_IDS=123456789012345678
DEV_IDS=123456789012345678,987654321098765432
```

| Variabile | Significato |
|---|---|
| `DISCORD_TOKEN` | Token del bot (obbligatorio) |
| `OWNER_IDS` | ID dei proprietari globali, per i comandi `/bot` (separati da virgola) |
| `DEV_IDS` | ID degli sviluppatori autorizzati, per i comandi `/dev` |

> **Non pubblicare mai il file `.env`.** Il `.gitignore` incluso lo esclude. Se il token finisce online per sbaglio, rigeneralo subito dal Developer Portal.

## Invito del bot

Genera il link di invito da **OAuth2 → URL Generator** scegliendo gli scope `bot` e `applications.commands`.
Non dare al bot il permesso Administrator: bastano quelli necessari.

- View Channels, Send Messages, Embed Links, Attach Files, Read Message History
- Manage Messages, Manage Channels, Manage Roles, Manage Threads, Manage Events
- Kick Members, Ban Members, Moderate Members, Manage Nicknames
- View Audit Log, Add Reactions

Il ruolo del bot deve stare **sopra** ai ruoli che deve gestire e ai membri che deve moderare, perché Discord applica la gerarchia dei ruoli.

## Avvio

```bash
python main.py
```

All'avvio il bot sincronizza gli slash command. La prima sincronizzazione globale può richiedere qualche minuto per comparire su Discord.

## Permessi

Ogni comando è controllato su due piani: il permesso Discord di chi lo usa (per esempio `Ban Members` per `/ban`) e il livello interno.

| Icona | Livello | Chi può usarlo |
|---|---|---|
| 🌐 | Pubblico | Tutti |
| 🔒 | Staff | Ruoli staff o admin impostati nel bot |
| 👑 | Admin | Amministratori del server o ruoli admin impostati |
| 🛠️ | Owner / Dev | Solo gli ID in `OWNER_IDS` / `DEV_IDS` |

Per far funzionare i livelli staff e admin, imposta i ruoli:

```
/staff add @Moderatori            ruolo staff
/staff add @Admin admin           ruolo admin
/staff list                       elenco dei ruoli impostati
```

Chi ha il permesso Discord Administrator o è il proprietario del server è sempre considerato admin.

## Sistema ticket

1. Imposta i ruoli staff (vedi sopra).
2. Pubblica il pannello nel canale scelto: `/ticketpanelcreate` (o `.ticketpanelcreate`).
3. Personalizzalo con `/ticketpaneledit` (o `.ticketpaneledit`).

### Cosa si può modificare

- **Titolo e descrizione** del pannello.
- **Pulsanti esterni (categorie):** di partenza Assistenza, Partnership, Giveaway, Segnalazioni e Altro. Puoi aggiungerne, modificarli e rimuoverli. Per ognuno si imposta nome, emoji, colore, messaggio mostrato nel ticket, posizione, attivazione e un ruolo richiesto facoltativo.
- **Pulsanti interni:** Claim, Unclaim, Close, Reopen, Transcript, Add Member, Remove Member, Rename e Delete, più pulsanti personalizzati. Per ognuno si imposta nome, emoji, colore, azione, posizione, attivazione e permessi (`tutti`, `aperto`, `staff`, `admin`).
- **Categoria Discord** dove vengono creati i canali dei ticket.
- **Modalità Close.**

Di partenza sono attivi solo Claim, Unclaim e Close. Gli altri si attivano da *Impostazioni pulsante*.

### Pulsanti personalizzati

Oltre alle azioni predefinite, puoi creare pulsanti con due azioni extra:

- `messaggio`: invia un testo scritto da te. Con `{user}` viene nominato chi clicca.
- `ping`: chiama i ruoli staff.

### Ping nei ticket

Con `/ping` scegli i ruoli che entrano nei nuovi ticket e vengono menzionati quando un ticket si apre (e dal pulsante *ping*). Il limite è di **40 ruoli**.

- `/ping ruoli` (oppure `.ping @ruolo1 @ruolo2 ...`) aggiunge fino a 5 ruoli alla volta. Senza ruoli mostra l'elenco attuale.
- `/ping reset` (oppure `.ping reset`) azzera **tutti** i ping: nei nuovi ticket non viene menzionato nessun ruolo.
- Finché non imposti nulla, nei ticket vengono menzionati i ruoli staff, come prima.
- Le modifiche valgono per i ticket nuovi, quelli già aperti restano com'erano.

### Close, Reopen e Delete

La modalità Close ha due impostazioni:

- **elimina** (predefinita): Close elimina il canale del ticket dopo 5 secondi.
- **blocca**: Close lascia il canale ma toglie la scrittura a chi ha aperto il ticket. Così Reopen può riaprirlo e Delete eliminarlo.

Le modifiche ai pulsanti interni valgono per i **nuovi** ticket. I ticket già aperti restano com'erano.

### Comandi ticket

`/ticket close`, `reopen`, `claim`, `unclaim`, `add-member`, `remove-member`, `rename`, `transcript`, `delete`, `panel-create`, `panel-edit`, `panel-delete`.

## Categorie di comandi

Usa `/help` per l'elenco completo. Le categorie sono:

1. Moderazione
2. Ruoli e membri
3. Canali, categorie e thread
4. Messaggi, embed e sondaggi
5. Benvenuto e verifica
6. Ticket
7. Candidature e segnalazioni
8. Log e audit
9. Sicurezza e automoderazione
10. Filtri
11. Automazioni e programmazioni
12. Attività e livelli
13. Statistiche e contatori
14. Giveaway ed eventi
15. Suggerimenti e votazioni
16. DM e notifiche
17. Backup
18. Dashboard e pannelli interattivi
19. Permessi e staff
20. Utilità
21. Configurazione
22. Diagnostica e stato
23. Comandi globali del bot
24. Comandi sviluppatore
25. Aiuto

## Dati

Il bot salva avvisi, casi, note, ruoli staff e configurazione ticket nel file `data.json`, creato accanto a `main.py`.
Se lo ospiti su un servizio con filesystem temporaneo, questo file va perso a ogni riavvio: usa un volume persistente o un database.

## Risoluzione problemi

| Problema | Soluzione |
|---|---|
| `401 Unauthorized` all'avvio | Il token è sbagliato o è stato rigenerato: controlla `DISCORD_TOKEN` nel `.env` |
| I comandi con `.` non rispondono | Attiva il Message Content Intent nel Developer Portal |
| Gli slash command non compaiono | Riinvita il bot con lo scope `applications.commands` e aspetta qualche minuto |
| "Il mio ruolo è troppo basso" | Sposta il ruolo del bot sopra a quelli che deve gestire |
| Nessuno vede i canali dei ticket | Imposta i ruoli staff con `/staff add` |

## Contribuire

Aprire una issue o una pull request è il modo migliore per segnalare bug o proporre nuovi comandi.
Per implementare un comando segnaposto, scrivi una funzione con il decoratore `@impl("gruppo sottocomando")` in `main.py`: il bot la collega da solo al comando registrato.

## Licenza

Aggiungi qui la licenza che preferisci (per esempio MIT).
