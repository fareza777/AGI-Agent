"""Telegram interface — long-polling getUpdates loop, no webhook needed.

Commands:
  /start, /help        — intro and command list
  /memory <query>      — search the agent's beliefs (active claims)
  /history <s> <p>     — full history of one belief slot (shows supersession)
  /goals               — list active goals
  /goal <text>         — add a goal
  /done <id>           — mark a goal done
  /reflect             — force a consolidation + reflection pass now
  /identity            — show the Identity Core
  /stats               — memory statistics
Anything else is a normal conversation turn.
"""

import logging
import time

import requests

from . import config, consolidator, identity, skills
from .agent import Agent

log = logging.getLogger("engram.telegram")

API = "https://api.telegram.org/bot{token}/{method}"
MAX_MSG = 4096

HELP = """\
Halo! Saya Engram — agen AI dengan memori permanen.

Saya mengingat semua percakapan kita, menyuling fakta & preferensi ke memori \
jangka panjang, mendeteksi saat informasi berubah, dan menyimpan goals lintas sesi.

Saya juga punya tools: cari web, buka URL, kalkulator, pengingat terjadwal, \
skills, dan akses langsung ke memori saya sendiri — cukup minta dalam obrolan \
("ingatkan aku besok jam 9", "cari berita tentang X", dll).

Perintah:
/memory <kata kunci> — cari apa yang saya ingat
/history <subjek> <atribut> — riwayat satu keyakinan (lihat perubahan)
/goals — daftar goal aktif
/goal <teks> — tambah goal
/done <id> — tandai goal selesai
/reminders — daftar pengingat terjadwal
/skills — daftar skill yang tersedia
/reflect — paksa konsolidasi memori + refleksi sekarang
/identity — lihat identitas inti saya
/stats — statistik memori

Selain itu, ngobrol saja seperti biasa."""


class TelegramBot:
    def __init__(self, agent: Agent):
        if not config.TELEGRAM_BOT_TOKEN:
            raise RuntimeError("TELEGRAM_BOT_TOKEN is not set (see .env.example)")
        self.agent = agent
        self.store = agent.store
        self.token = config.TELEGRAM_BOT_TOKEN
        self.session = requests.Session()

    # ---------------- transport ----------------

    def _call(self, method: str, **params):
        url = API.format(token=self.token, method=method)
        resp = self.session.post(url, json=params, timeout=70)
        data = resp.json()
        if not data.get("ok"):
            raise RuntimeError(f"Telegram {method} failed: {data}")
        return data["result"]

    def send(self, chat_id, text: str):
        for i in range(0, max(len(text), 1), MAX_MSG):
            self._call("sendMessage", chat_id=chat_id, text=text[i:i + MAX_MSG])

    # ---------------- main loop ----------------

    def run(self):
        me = self._call("getMe")
        log.info("connected as @%s", me.get("username"))
        offset = int(self.store.get_meta("tg_offset", "0"))
        while True:
            try:
                updates = self._call("getUpdates", offset=offset, timeout=60,
                                     allowed_updates=["message"])
            except (requests.RequestException, RuntimeError):
                log.exception("getUpdates failed; retrying in 5s")
                time.sleep(5)
                continue
            for upd in updates:
                offset = upd["update_id"] + 1
                self.store.set_meta("tg_offset", str(offset))
                try:
                    self._handle_update(upd)
                except Exception:
                    log.exception("failed to handle update %s", upd.get("update_id"))

    def _handle_update(self, upd: dict):
        msg = upd.get("message") or {}
        text = (msg.get("text") or "").strip()
        chat = msg.get("chat") or {}
        chat_id = str(chat.get("id", ""))
        if not text or not chat_id:
            return
        if config.ALLOWED_CHAT_IDS and chat_id not in config.ALLOWED_CHAT_IDS:
            log.warning("ignoring message from non-allowed chat %s", chat_id)
            return

        if text.startswith("/"):
            self._handle_command(chat_id, text)
        else:
            self._call("sendChatAction", chat_id=chat_id, action="typing")
            self.send(chat_id, self.agent.handle_message(chat_id, text))

    # ---------------- commands ----------------

    def _handle_command(self, chat_id: str, text: str):
        parts = text.split(maxsplit=1)
        cmd = parts[0].split("@")[0].lower()
        arg = parts[1].strip() if len(parts) > 1 else ""
        self.store.log_event("user", "command", text, chat_id)

        if cmd in ("/start", "/help"):
            self.send(chat_id, HELP)

        elif cmd == "/memory":
            if not arg:
                self.send(chat_id, "Pakai: /memory <kata kunci>")
                return
            claims = self.store.search_claims(arg, limit=15)
            if not claims:
                self.send(chat_id, "Tidak ada memori yang cocok.")
                return
            lines = [
                f"• {c['subject']} | {c['predicate']} = {c['value']}\n"
                f"  [{c['kind']}, conf {c['confidence']:.2f}, sejak {c['valid_from'][:10]}]"
                for c in claims
            ]
            self.send(chat_id, "Yang saya ingat:\n\n" + "\n".join(lines))

        elif cmd == "/history":
            bits = arg.split()
            if len(bits) < 2:
                self.send(chat_id, "Pakai: /history <subjek> <atribut>  (contoh: /history user works_at)")
                return
            rows = self.store.claim_history(bits[0].lower(), bits[1].lower())
            if not rows:
                self.send(chat_id, "Tidak ada riwayat untuk slot itu.")
                return
            lines = []
            for r in rows:
                status = "AKTIF" if r["valid_to"] is None else f"diganti {r['valid_to'][:10]}"
                lines.append(f"• {r['valid_from'][:10]}: {r['value']} ({status})")
            self.send(chat_id, f"Riwayat {bits[0]}.{bits[1]}:\n" + "\n".join(lines))

        elif cmd == "/goals":
            goals = self.store.goals(chat_id, "active")
            if not goals:
                self.send(chat_id, "Belum ada goal aktif. Tambah dengan /goal <teks>.")
                return
            lines = [f"#{g['id']} {g['title']} (sejak {g['created_at'][:10]})" for g in goals]
            self.send(chat_id, "Goal aktif:\n" + "\n".join(lines))

        elif cmd == "/goal":
            if not arg:
                self.send(chat_id, "Pakai: /goal <teks goal>")
                return
            gid = self.store.add_goal(chat_id, arg)
            self.send(chat_id, f"Goal #{gid} disimpan: {arg}")

        elif cmd == "/done":
            if not arg.isdigit() or not self.store.set_goal_status(int(arg), "done"):
                self.send(chat_id, "Pakai: /done <id goal>")
                return
            self.send(chat_id, f"Goal #{arg} selesai. 🎉")

        elif cmd == "/reflect":
            self._call("sendChatAction", chat_id=chat_id, action="typing")
            try:
                stats = consolidator.consolidate(self.store)
                insights = consolidator.reflect(self.store, chat_id)
            except Exception:
                log.exception("manual reflect failed")
                self.send(chat_id, "Konsolidasi gagal — cek log server.")
                return
            out = (f"Konsolidasi: {stats['events']} event diproses, "
                   f"{stats['new']} keyakinan baru, {stats['updated']} diperbarui.")
            if insights:
                out += "\n\nInsight baru:\n" + "\n".join(
                    f"• {i['value']}" for i in insights)
            self.send(chat_id, out)

        elif cmd == "/reminders":
            rows = self.store.pending_reminders(chat_id)
            if not rows:
                self.send(chat_id, "Tidak ada pengingat terjadwal.")
                return
            lines = [f"#{r['id']} {r['due_ts']} UTC — {r['message']}" for r in rows]
            self.send(chat_id, "Pengingat terjadwal:\n" + "\n".join(lines))

        elif cmd == "/skills":
            entries = skills.index()
            if not entries:
                self.send(chat_id, "Belum ada skill. Tambahkan file .md ke folder skills/.")
                return
            lines = [f"• {name} — {desc}" for name, desc in entries]
            self.send(chat_id, "Skill tersedia:\n" + "\n".join(lines))

        elif cmd == "/identity":
            self.send(chat_id, identity.load())

        elif cmd == "/stats":
            n_claims = len(self.store.active_claims(limit=100000))
            pending = self.store.unprocessed_count()
            self.send(chat_id,
                      f"Keyakinan aktif: {n_claims}\n"
                      f"Event belum dikonsolidasi: {pending}\n"
                      f"Model chat: {config.CHAT_MODEL}\n"
                      f"Model konsolidasi: {config.CONSOLIDATE_MODEL}")

        else:
            self.send(chat_id, "Perintah tidak dikenal. /help untuk daftar perintah.")
