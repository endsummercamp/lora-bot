# lora-bot

Bot che legge i messaggi di un canale Meshtastic specifico da un nodo
collegato via USB e li inoltra su un gruppo Telegram.

## Come funziona

- `bot.py` apre una connessione seriale al dispositivo Meshtastic
  (`meshtastic.serial_interface.SerialInterface`, autorilevamento porta).
- All'avvio legge l'elenco dei canali del nodo e trova l'indice
  corrispondente al nome impostato in `MESHTASTIC_CHANNEL_NAME`. Se il
  canale non esiste e `MESHTASTIC_CHANNEL_PSK` è configurata, lo crea
  (provisioning) sul primo slot libero con quel nome e quella chiave.
- Si mette in ascolto dell'evento `meshtastic.receive.text` e, per ogni
  messaggio ricevuto su quel canale, lo inoltra al gruppo Telegram
  (`TELEGRAM_CHAT_ID`) tramite il bot Telegram (`TELEGRAM_BOT_TOKEN`), nel
  formato `` `<Nome Completo (ShortName)>` messaggio `` — nome del nodo in
  monospace, testo del messaggio inviato come Markdown (così eventuale
  formattazione Markdown scritta da chi manda il messaggio via Meshtastic
  viene resa anche su Telegram; se il Markdown risulta malformato il bot
  ripiega automaticamente sul testo semplice), anteprime dei link
  disattivate.

## Setup

1. Crea un bot Telegram con [@BotFather](https://t.me/BotFather) e prendi il
   token.
2. Aggiungi il bot al gruppo Telegram di destinazione e recupera il
   `chat_id` del gruppo (es. inoltrando temporaneamente un messaggio del
   gruppo a [@userinfobot](https://t.me/userinfobot), oppure chiamando
   `https://api.telegram.org/bot<TOKEN>/getUpdates` dopo aver scritto un
   messaggio nel gruppo).
3. Scegli il canale Meshtastic da inoltrare, in uno dei due modi:
   - **Canale già esistente**: verifica il nome dall'app Meshtastic
     (sezione Canali) o via CLI `meshtastic --info`, e mettilo in
     `MESHTASTIC_CHANNEL_NAME` (lascia vuota `MESHTASTIC_CHANNEL_PSK`).
     Nota: il canale primario ha nome vuoto per i preset di default — se
     vuoi inoltrare il primario, assegnagli un nome esplicito con
     `meshtastic --ch-index 0 --ch-set name "Primary"` (senza toccare la
     PSK, altrimenti rompi la compatibilità con gli altri nodi che non la
     aggiornano).
   - **Canale nuovo (provisioning automatico)**: imposta sia
     `MESHTASTIC_CHANNEL_NAME` che `MESHTASTIC_CHANNEL_PSK` in `.env`. Al
     primo avvio, se il canale non esiste, il bot lo crea da solo sul
     primo slot secondario libero del nodo con quel nome e quella chiave
     (equivalente a `meshtastic --ch-add <nome> --ch-set psk <chiave>`).
     Dovrai poi condividere nome e chiave con gli altri nodi che devono
     partecipare al canale (es. tramite l'URL/QR del canale generato
     dall'app Meshtastic).
4. Copia `.env.example` in `.env` e compila i valori:
   ```
   cp .env.example .env
   ```
5. Installa le dipendenze (è già presente un virtualenv `.venv` con le
   dipendenze installate; in alternativa creane uno nuovo):
   ```
   python3 -m venv .venv
   .venv/bin/pip install -r requirements.txt
   ```
6. Collega il dispositivo Meshtastic via USB e avvia il bot:
   ```
   .venv/bin/python bot.py
   ```

Se tutto è configurato correttamente vedrai nei log l'indice del canale
risolto e, da quel momento, ogni messaggio ricevuto su quel canale verrà
inoltrato al gruppo Telegram.

## Esecuzione come servizio (systemd)

È incluso un file di esempio `lora-bot.service`. Per installarlo:

```
sudo cp lora-bot.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now lora-bot
```

Prima di abilitarlo, sostituisci `CHANGE_ME` con l'utente che deve eseguire
il bot (deve poter accedere al device seriale, es. essere nel gruppo
`dialout`). Il file usa `%h` per la home di quell'utente: se il progetto
non si trova in `~/lora-bot`, aggiorna anche `WorkingDirectory` ed
`ExecStart` con il percorso assoluto reale.

## Note

- Se al dispositivo USB sono collegati più nodi Meshtastic contemporaneamente
  l'autorilevamento fallisce: imposta `MESHTASTIC_PORT` in `.env` (es.
  `/dev/ttyUSB0`) per forzare una porta specifica.
- Se il nome canale in `.env` non corrisponde a nessun canale configurato
  sul nodo e `MESHTASTIC_CHANNEL_PSK` non è impostata, il bot lo segnala
  nei log, elenca i canali disponibili e ricade sul canale 0 (Primary) per
  non restare inattivo.
- Il provisioning automatico usa il primo canale "disabilitato" trovato
  sul nodo (max 8 canali totali, indice 0 riservato al Primary): se sono
  già tutti in uso il bot si ferma con un errore esplicito all'avvio.
