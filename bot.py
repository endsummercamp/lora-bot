"""Inoltra su un gruppo Telegram i messaggi ricevuti da un canale Meshtastic
specifico, tramite un dispositivo Meshtastic collegato via USB."""

import asyncio
import logging
import os
import sys

import meshtastic
import meshtastic.serial_interface
from dotenv import load_dotenv
from pubsub import pub
from telegram import Bot

load_dotenv()

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s [%(name)s] %(message)s",
)
log = logging.getLogger("lora-bot")

TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN")
TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID")
MESHTASTIC_CHANNEL_NAME = os.environ.get("MESHTASTIC_CHANNEL_NAME")
MESHTASTIC_PORT = os.environ.get("MESHTASTIC_PORT") or None

if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
    log.error("TELEGRAM_BOT_TOKEN e TELEGRAM_CHAT_ID sono obbligatori (vedi .env).")
    sys.exit(1)
if not MESHTASTIC_CHANNEL_NAME:
    log.error("MESHTASTIC_CHANNEL_NAME è obbligatorio (vedi .env).")
    sys.exit(1)


def resolve_channel_index(interface: "meshtastic.serial_interface.SerialInterface", name: str) -> int:
    """Trova l'indice del canale il cui nome corrisponde a `name`
    (case-insensitive). Se non lo trova, avvisa e ricade sul canale 0
    (Primary)."""
    channels = interface.localNode.channels or []
    for ch in channels:
        ch_name = getattr(ch.settings, "name", "") or ""
        if ch_name.strip().lower() == name.strip().lower():
            log.info("Canale '%s' trovato all'indice %d", name, ch.index)
            return ch.index

    available = [
        (ch.index, getattr(ch.settings, "name", "") or "(senza nome)")
        for ch in channels
        if ch.role != 0  # 0 = DISABLED
    ]
    log.warning(
        "Canale '%s' non trovato. Canali disponibili sul nodo: %s. "
        "Ricado sul canale 0 (Primary) — inoltrerò tutto ciò che arriva lì.",
        name,
        available,
    )
    return 0


def format_message(packet: dict, interface: "meshtastic.serial_interface.SerialInterface") -> str:
    from_id = packet.get("fromId", "sconosciuto")
    node_info = interface.nodes.get(from_id, {}) if hasattr(interface, "nodes") else {}
    user = node_info.get("user", {})
    full_name = user.get("longName") or from_id
    short_name = user.get("shortName")
    header = f"<{full_name} ({short_name})>" if short_name else f"<{full_name}>"
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


async def telegram_sender(bot: Bot, queue: "asyncio.Queue[str]"):
    while True:
        message = await queue.get()
        try:
            await bot.send_message(
                chat_id=TELEGRAM_CHAT_ID,
                text=message,
            )
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
