"""Inoltra su un gruppo Telegram i messaggi ricevuti da un canale Meshtastic
specifico, tramite un dispositivo Meshtastic collegato via USB."""

import asyncio
import logging
import os
import sys

import meshtastic
import meshtastic.serial_interface
import meshtastic.util
from dotenv import load_dotenv
from meshtastic.protobuf import channel_pb2
from pubsub import pub
from telegram import Bot, LinkPreviewOptions
from telegram.constants import ParseMode
from telegram.error import BadRequest

load_dotenv()

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s [%(name)s] %(message)s",
)
log = logging.getLogger("lora-bot")

TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN")
TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID")
MESHTASTIC_CHANNEL_NAME = os.environ.get("MESHTASTIC_CHANNEL_NAME")
MESHTASTIC_CHANNEL_PSK = os.environ.get("MESHTASTIC_CHANNEL_PSK") or None
MESHTASTIC_PORT = os.environ.get("MESHTASTIC_PORT") or None

if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
    log.error("TELEGRAM_BOT_TOKEN e TELEGRAM_CHAT_ID sono obbligatori (vedi .env).")
    sys.exit(1)
if not MESHTASTIC_CHANNEL_NAME:
    log.error("MESHTASTIC_CHANNEL_NAME è obbligatorio (vedi .env).")
    sys.exit(1)


def find_channel_by_name(interface: "meshtastic.serial_interface.SerialInterface", name: str):
    channels = interface.localNode.channels or []
    for ch in channels:
        ch_name = getattr(ch.settings, "name", "") or ""
        if ch_name.strip().lower() == name.strip().lower():
            return ch
    return None


def provision_channel(
    interface: "meshtastic.serial_interface.SerialInterface", name: str, psk: str
) -> int:
    """Crea (sul primo slot libero) un canale secondario con il nome e la
    chiave indicati e lo scrive sul dispositivo. Restituisce l'indice del
    canale creato."""
    node = interface.localNode
    ch = node.getDisabledChannel()
    if ch is None:
        raise RuntimeError(
            f"Nessuno slot canale libero sul nodo per provisionare '{name}' "
            "(tutti e 8 i canali sono già in uso)."
        )

    settings = channel_pb2.ChannelSettings()
    settings.psk = meshtastic.util.fromPSK(psk)
    settings.name = name
    ch.settings.CopyFrom(settings)
    ch.role = channel_pb2.Channel.Role.SECONDARY

    log.info("Provisioning canale '%s' sull'indice %d...", name, ch.index)
    node.writeChannel(ch.index)
    log.info("Canale '%s' scritto sul dispositivo (indice %d).", name, ch.index)
    return ch.index


def resolve_channel_index(interface: "meshtastic.serial_interface.SerialInterface", name: str) -> int:
    """Trova l'indice del canale il cui nome corrisponde a `name`
    (case-insensitive). Se non lo trova e MESHTASTIC_CHANNEL_PSK è
    configurata, lo crea (provisioning); altrimenti avvisa e ricade sul
    canale 0 (Primary)."""
    ch = find_channel_by_name(interface, name)
    if ch is not None:
        log.info("Canale '%s' trovato all'indice %d", name, ch.index)
        return ch.index

    if MESHTASTIC_CHANNEL_PSK:
        return provision_channel(interface, name, MESHTASTIC_CHANNEL_PSK)

    channels = interface.localNode.channels or []
    available = [
        (c.index, getattr(c.settings, "name", "") or "(senza nome)")
        for c in channels
        if c.role != channel_pb2.Channel.Role.DISABLED
    ]
    log.warning(
        "Canale '%s' non trovato. Canali disponibili sul nodo: %s. "
        "Imposta MESHTASTIC_CHANNEL_PSK per crearlo automaticamente, oppure "
        "ricado sul canale 0 (Primary) — inoltrerò tutto ciò che arriva lì.",
        name,
        available,
    )
    return 0


def _sanitize_for_code_span(value: str) -> str:
    """Un backtick letterale chiuderebbe prematuramente lo span di codice
    Markdown: i nomi dei nodi sono impostati liberamente dagli utenti
    Meshtastic, quindi vanno bonificati prima di finire nell'header."""
    return value.replace("`", "'")


def format_message(packet: dict, interface: "meshtastic.serial_interface.SerialInterface") -> str:
    from_id = packet.get("fromId", "sconosciuto")
    node_info = interface.nodes.get(from_id, {}) if hasattr(interface, "nodes") else {}
    user = node_info.get("user", {})
    full_name = _sanitize_for_code_span(user.get("longName") or from_id)
    short_name = user.get("shortName")
    name = f"{full_name} ({_sanitize_for_code_span(short_name)})" if short_name else full_name
    header = f"`<{name}>`"
    text = packet.get("decoded", {}).get("text", "")
    return f"{header} {text}"


class Bridge:
    def __init__(self, loop: asyncio.AbstractEventLoop, queue: "asyncio.Queue[str]"):
        self.loop = loop
        self.queue = queue
        self.channel_index = 0
        self.interface: meshtastic.serial_interface.SerialInterface | None = None

    def start_meshtastic(self):
        log.info("Connessione al dispositivo Meshtastic via USB...")
        self.interface = meshtastic.serial_interface.SerialInterface(devPath=MESHTASTIC_PORT)
        self.channel_index = resolve_channel_index(self.interface, MESHTASTIC_CHANNEL_NAME)

        pub.subscribe(self.on_receive_text, "meshtastic.receive.text")
        pub.subscribe(self.on_connection_lost, "meshtastic.connection.lost")
        log.info(
            "In ascolto sul canale '%s' (indice %d). In attesa di messaggi...",
            MESHTASTIC_CHANNEL_NAME,
            self.channel_index,
        )

    def on_receive_text(self, packet, interface):
        packet_channel = packet.get("channel", 0)
        if packet_channel != self.channel_index:
            return

        message = format_message(packet, interface)
        log.info("Messaggio ricevuto sul canale %d: %s", packet_channel, message)
        self.loop.call_soon_threadsafe(self.queue.put_nowait, message)

    def on_connection_lost(self, interface):
        log.warning("Connessione al dispositivo Meshtastic persa.")


async def send_to_telegram(bot: Bot, message: str):
    link_preview_options = LinkPreviewOptions(is_disabled=True)
    try:
        await bot.send_message(
            chat_id=TELEGRAM_CHAT_ID,
            text=message,
            parse_mode=ParseMode.MARKDOWN,
            link_preview_options=link_preview_options,
        )
    except BadRequest as e:
        if "can't parse entities" not in str(e).lower():
            raise
        # Markdown non bilanciato nel testo Meshtastic (contenuto libero,
        # scritto da terzi): invio come testo semplice piuttosto che perdere
        # il messaggio.
        log.warning("Markdown non valido nel messaggio, invio come testo semplice: %s", e)
        await bot.send_message(
            chat_id=TELEGRAM_CHAT_ID,
            text=message,
            link_preview_options=link_preview_options,
        )


async def telegram_sender(bot: Bot, queue: "asyncio.Queue[str]"):
    while True:
        message = await queue.get()
        try:
            await send_to_telegram(bot, message)
        except Exception:
            log.exception("Errore nell'invio del messaggio a Telegram")


async def main():
    loop = asyncio.get_running_loop()
    queue: "asyncio.Queue[str]" = asyncio.Queue()
    bridge = Bridge(loop, queue)

    try:
        await loop.run_in_executor(None, bridge.start_meshtastic)
    except Exception:
        log.exception("Impossibile connettersi al dispositivo Meshtastic")
        sys.exit(1)

    bot = Bot(token=TELEGRAM_BOT_TOKEN)
    async with bot:
        await telegram_sender(bot, queue)


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        log.info("Interrotto dall'utente.")
