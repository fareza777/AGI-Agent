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
import os
import threading
import time
import requests
from . import config, consolidator, identity, skill_compiler, skills, telegram_format
from .agent import Agent

log = logging.getLogger("engram.telegram")
API = "https://api.telegram.org/bot{token}/{method}"
MAX_MSG = 3800  # leave headroom under Telegram's 4096 for HTML entities
# sendChatAction "typing" expires after ~5s on Telegram's side, so a
# single call at turn start leaves the user staring at silence during a
# long multi-tool turn. TypingKeepAlive re-sends the action every
# _REFRESH_SEC until stopped. Best-effort — any send failure is swallowed.
_TYPING_REFRESH_SEC = 4


class TypingKeepAlive:
    def __init__(self, call, chat_id, action="typing"):
        self._call = call
        self._chat_id = chat_id
        self._action = action
        self._stop = threading.Event()
        self._thread = threading.Thread(
            target=self._loop, name=f"typing-{chat_id}", daemon=True
        )

    def start(self):
        self._thread.start()

    def stop(self):
        self._stop.set()

    def _loop(self):
        # One immediate send so the indicator appears at turn start, then
        # refresh every _TYPING_REFRESH_SEC until stop().
        self._send()
        while not self._stop.wait(_TYPING_REFRESH_SEC):
            self._send()

    def _send(self):
        try:
            self._call("sendChatAction", chat_id=self._chat_id, action=self._action)
        except Exception:
            log.debug("typing keepalive failed", exc_info=True)


def _split(text: str, limit: int):
    """Split a message at line boundaries so HTML tags aren't cut mid-tag."""
    text = text or ""
    if len(text) <= limit:
        yield text
        return
    buf = ""
    for line in text.split("\n"):
        if len(buf) + len(line) + 1 > limit:
            if buf:
                yield buf
                buf = ""
            while len(line) > limit:  # a single very long line
                yield line[:limit]
                line = line[limit:]
        buf = f"{buf}\n{line}" if buf else line
    if buf:
        yield buf


class _LiveReply:
    """A single chat message edited in place as the reply streams in. Edits are
    throttled so Telegram's rate limit isn't tripped; the final formatted reply
    replaces the live preview via finalize()."""

    MIN_EDIT_INTERVAL = 1.2  # seconds between editMessageText calls

    def __init__(self, bot, chat_id):
        self.bot = bot
        self.chat_id = chat_id
        self.message_id = None
        self.last_edit = 0.0
        self.text = ""

    def update(self, text: str):
        self.text = text or ""
        if not self.text.strip():
            return
        now = time.time()
        preview = self.text[:MAX_MSG]
        if self.message_id is None:
            try:
                res = self.bot._call("sendMessage", chat_id=self.chat_id,
                                     text=preview, disable_web_page_preview=True)
                self.message_id = res.get("message_id")
                self.last_edit = now
            except Exception:
                log.debug("live reply create failed", exc_info=True)
        elif now - self.last_edit >= self.MIN_EDIT_INTERVAL:
            try:
                self.bot._call("editMessageText", chat_id=self.chat_id,
                               message_id=self.message_id, text=preview,
                               disable_web_page_preview=True)
            except Exception:
                pass  # entity/no-change edits are harmless; keep streaming
            self.last_edit = now

    def finalize(self, final_html: str):
        """Replace the live message with the final formatted reply. Returns None
        if nothing was streamed (caller sends normally), else a list of any
        overflow chunks the caller should send as follow-up messages."""
        if self.message_id is None:
            return None
        chunks = list(_split(final_html, MAX_MSG)) or [self.text[:MAX_MSG]]
        for kwargs in ({"text": chunks[0], "parse_mode": "HTML"},
                       {"text": self.text[:MAX_MSG]}):
            try:
                self.bot._call("editMessageText", chat_id=self.chat_id,
                               message_id=self.message_id,
                               disable_web_page_preview=True, **kwargs)
                break
            except Exception:
                continue
        return chunks[1:]


HELP = """\

Halo! Saya Engram — asisten digital dengan memori permanen.

Saya mengingat semua percakapan kita, menyuling fakta & preferensi ke memori \

jangka panjang, mendeteksi saat informasi berubah, dan menyimpan goals lintas sesi.

Saya juga bisa bertindak, bukan cuma menjawab:

• Bikin dokumen — laporan docx, spreadsheet xlsx, pdf, dll ("buatkan laporan ...")

• Tugas terjadwal & berulang — "kirim analisis saham tiap pagi jam 7" → saya \

kerjakan sendiri dan kirim hasilnya, rutin

• Terima file — kirim dokumen ke chat ini, saya simpan dan bisa langsung olah

• Kelola file di workspace — tulis, baca, cari, rapikan

• Cek repository git (status, log, diff)

• Cari web, buka URL, kalkulator, pengingat terjadwal

• Akses langsung ke memori saya sendiri

Cukup minta dalam obrolan ("buatkan laporan penjualan dalam docx", \

"ringkas berita crypto tiap pagi", "cek status repo di /path/repo").

Perintah:

/memory <kata kunci> — cari apa yang saya ingat

/history <subjek> <atribut> — riwayat satu keyakinan (lihat perubahan)

/audit [n] — daftar keyakinan aktif terbaru beserta id-nya

/forget <id> — tutup keyakinan yang salah (riwayat tetap tersimpan)

/good · /bad <alasan> — nilai jawaban terakhir; jadi bahan belajar saya

/goals — daftar goal aktif

/goal <teks> — tambah goal

/done <id> — tandai goal selesai

/reminders — daftar pengingat terjadwal

/tasks — daftar tugas terjadwal (yang saya kerjakan otomatis)

/canceltask <id> — batalkan tugas terjadwal

/skills — daftar skill (termasuk draft hasil belajar)

/skill <nama> — lihat isi satu skill

/approve <nama> — aktifkan draft skill yang saya usulkan

/reflect — paksa konsolidasi memori + refleksi + penambangan skill

/doctor — cek kesehatan: provider, tools dokumen, workspace, git

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
        # Delivery channels back into the agent (reminders, task results,
        # live activity feed).
        agent.notifier = self.send
        agent.file_notifier = self.send_document
        agent.activity_notifier = self.send_activity

    # ---------------- transport ----------------
    def _call(self, method: str, **params):
        url = API.format(token=self.token, method=method)
        resp = self.session.post(url, json=params, timeout=70)
        data = resp.json()
        if not data.get("ok"):
            raise RuntimeError(f"Telegram {method} failed: {data}")
        return data["result"]

    def send(self, chat_id, text: str, rich: bool = True):
        """Send a message. rich=True renders model markdown as Telegram HTML

        (clean bullets, bold, code, flattened tables); falls back to plain text

        if Telegram rejects the entities. rich=False sends plain text."""
        if not rich:
            for chunk in _split(text, MAX_MSG):
                self._call(
                    "sendMessage",
                    chat_id=chat_id,
                    text=chunk,
                    disable_web_page_preview=True,
                )
            return
        body = telegram_format.to_telegram_html(text)
        for chunk in _split(body, MAX_MSG):
            try:
                self._call(
                    "sendMessage",
                    chat_id=chat_id,
                    text=chunk,
                    parse_mode="HTML",
                    disable_web_page_preview=True,
                )
            except RuntimeError:
                # Malformed entity (rare) — resend as plain text so the user
                # always gets the message.
                plain = next(iter(_split(text, MAX_MSG)), text[:MAX_MSG])
                self._call("sendMessage", chat_id=chat_id, text=plain)
                break

    def send_keyboard(self, chat_id, text: str, buttons: list):
        """Send a message with an inline keyboard. `buttons` is a list of rows,
        each row a list of (label, callback_data) tuples. Best-effort."""
        keyboard = [
            [{"text": label, "callback_data": data} for label, data in row]
            for row in buttons
        ]
        try:
            self._call(
                "sendMessage",
                chat_id=chat_id,
                text=telegram_format.to_telegram_html(text),
                parse_mode="HTML",
                reply_markup={"inline_keyboard": keyboard},
                disable_web_page_preview=True,
            )
        except RuntimeError:
            self.send(chat_id, text)

    # Quick-action menu shown by /menu and /start.
    _MENU_BUTTONS = [
        [("🧠 Memori", "/memory"), ("🎯 Goals", "/goals")],
        [("⏰ Reminder", "/reminders"), ("🗓️ Tugas", "/tasks")],
        [("📚 Skills", "/skills"), ("💤 Reflect", "/reflect")],
        [("🩺 Doctor", "/doctor"), ("📊 Stats", "/stats")],
    ]

    # Registered into Telegram's UI command menu via setMyCommands on startup.
    _BOT_COMMANDS = [
        ("menu", "Tombol aksi cepat"),
        ("help", "Bantuan & daftar perintah"),
        ("memory", "Lihat beberapa belief terbaru"),
        ("audit", "Daftar keyakinan aktif + id"),
        ("forget", "Hapus keyakinan yang salah"),
        ("good", "Tandai jawaban terakhir bagus"),
        ("bad", "Tandai jawaban terakhir salah"),
        ("goals", "Daftar goal aktif"),
        ("reminders", "Pengingat terjadwal"),
        ("tasks", "Tugas otomatis terjadwal"),
        ("skills", "Daftar skill (termasuk draft)"),
        ("reflect", "Konsolidasi memori + refleksi sekarang"),
        ("timezone", "Setel zona waktu untuk reminder"),
        ("doctor", "Cek kesehatan sistem"),
        ("stats", "Statistik memori"),
    ]

    def send_activity(self, chat_id, text: str):
        """One compact live-status line (🔎 web_search: ...). Best-effort —

        never let the feed break the turn."""
        try:
            self._call(
                "sendMessage",
                chat_id=chat_id,
                text=text[:200],
                disable_web_page_preview=True,
                disable_notification=True,
            )
        except Exception:
            log.debug("activity line failed", exc_info=True)

    @staticmethod
    def _file_caption(path: str) -> str:
        """A short descriptor shown with the file so the user knows what it is
        before opening it: name, type, and human-readable size."""
        name = os.path.basename(path)
        ext = (os.path.splitext(name)[1].lstrip(".") or "file").upper()
        size = os.path.getsize(path)
        human = (
            f"{size} B" if size < 1024
            else f"{size / 1024:.0f} KB" if size < 1024 * 1024
            else f"{size / (1024 * 1024):.1f} MB"
        )
        return f"📄 {name} · {ext} · {human}"

    def send_document(self, chat_id, path: str) -> bool:
        """Upload a workspace file. Returns True on success."""
        if not os.path.isfile(path):
            log.warning("sendDocument skipped — not a file: %s", path)
            return False
        url = API.format(token=self.token, method="sendDocument")
        try:
            with open(path, "rb") as fh:
                resp = self.session.post(
                    url,
                    data={"chat_id": chat_id, "caption": self._file_caption(path)},
                    files={"document": (os.path.basename(path), fh)},
                    timeout=120,
                )
            data = resp.json()
            if not data.get("ok"):
                log.warning("sendDocument failed for %s: %s", path, resp.text[:200])
                return False
            return True
        except Exception:
            log.exception("sendDocument failed for %s", path)
            return False

    # ---------------- main loop ----------------
    def run(self):
        me = self._call("getMe")
        log.info("connected as @%s", me.get("username"))
        # Register the slash-command menu so commands autocomplete in the
        # Telegram UI. Best-effort — never let it block startup.
        try:
            self._call("setMyCommands", commands=[
                {"command": c, "description": d} for c, d in self._BOT_COMMANDS])
        except Exception:
            log.debug("setMyCommands failed", exc_info=True)
        offset = int(self.store.get_meta("tg_offset", "0"))
        while True:
            try:
                updates = self._call(
                    "getUpdates", offset=offset, timeout=60,
                    allowed_updates=["message", "callback_query"],
                )
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
                    chat_id = str(
                        (upd.get("message") or {}).get("chat", {}).get("id", "")
                    )
                    if chat_id:
                        try:
                            self.send(
                                chat_id,
                                "⚠️ Error internal — pesan tidak diproses. "
                                "Coba kirim lagi.",
                                rich=False,
                            )
                        except Exception:
                            log.debug(
                                "could not notify user of handler error", exc_info=True
                            )

    def _handle_update(self, upd: dict):
        # Inline-keyboard button press: ack it, then run its callback_data as if
        # the user had typed it (the buttons carry slash-commands).
        cb = upd.get("callback_query")
        if cb:
            cb_chat = str((cb.get("message") or {}).get("chat", {}).get("id", ""))
            data = (cb.get("data") or "").strip()
            try:
                self._call("answerCallbackQuery", callback_query_id=cb["id"])
            except Exception:
                log.debug("answerCallbackQuery failed", exc_info=True)
            if cb_chat and data:
                if config.ALLOWED_CHAT_IDS and cb_chat not in config.ALLOWED_CHAT_IDS:
                    return
                if data.startswith("/"):
                    self._handle_command(cb_chat, data)
                else:
                    self.agent.handle_message(cb_chat, data)
            return
        msg = upd.get("message") or {}
        text = (msg.get("text") or "").strip()
        chat = msg.get("chat") or {}
        chat_id = str(chat.get("id", ""))
        if not chat_id:
            return
        if config.ALLOWED_CHAT_IDS and chat_id not in config.ALLOWED_CHAT_IDS:
            log.warning("ignoring message from non-allowed chat %s", chat_id)
            return
        # Incoming files (documents/photos) land in workspace/inbox and become
        # part of the turn, so the agent can read/process them immediately.
        # Images are also attached to the model message (vision).
        attachment_note, image_paths = self._receive_attachments(chat_id, msg)
        if attachment_note:
            text = f"{msg.get('caption', '').strip()}\n{attachment_note}".strip()
        if not text:
            return
        if text.startswith("/"):
            self._handle_command(chat_id, text)
        else:
            # Start the typing keepalive BEFORE handle_message so the
            # indicator stays visible the whole time the model is thinking
            # and tools are running. stop() is called in a finally block
            # so the thread always exits, even on exception.
            typing_ka = TypingKeepAlive(self._call, chat_id, action="typing")
            typing_ka.start()
            live = _LiveReply(self, chat_id) if config.STREAM_REPLIES else None
            try:
                reply, files = self.agent.handle_message(
                    chat_id, text, images=image_paths,
                    on_delta=(live.update if live else None),
                )
            finally:
                typing_ka.stop()
            # If we streamed, finalize the SAME message with formatted text and
            # send any overflow chunks; otherwise send a fresh message.
            overflow = live.finalize(telegram_format.to_telegram_html(reply)) \
                if live else None
            if overflow is None:
                self.send(chat_id, reply)
            else:
                for extra in overflow:
                    try:
                        self._call("sendMessage", chat_id=chat_id, text=extra,
                                   parse_mode="HTML", disable_web_page_preview=True)
                    except RuntimeError:
                        self._call("sendMessage", chat_id=chat_id, text=extra)
            failed = []
            for path in files:
                # Use upload_document action while we send each file so the
                # user sees the right indicator (paperclip) instead of dots.
                doc_ka = TypingKeepAlive(self._call, chat_id, action="upload_document")
                doc_ka.start()
                try:
                    if not self.send_document(chat_id, path):
                        failed.append(os.path.basename(path))
                finally:
                    doc_ka.stop()
            if failed:
                self.send(
                    chat_id,
                    "⚠️ Gagal kirim file ke Telegram: "
                    + ", ".join(failed)
                    + "\nFile masih ada di workspace — minta kirim ulang "
                    "dengan send_file.",
                )

    # ---------------- incoming files ----------------
    MAX_DOWNLOAD = 20 * 1024 * 1024  # Telegram bot API download cap
    IMAGE_EXTS = {".jpg", ".jpeg", ".png", ".webp", ".gif"}

    def _receive_attachments(self, chat_id: str, msg: dict):
        """Download document/photo attachments into workspace/inbox.

        Returns (note_for_model, image_paths). Images are passed to the model

        as vision input for this turn."""
        saved = []
        doc = msg.get("document")
        if doc:
            path = self._download(
                doc.get("file_id"),
                doc.get("file_name", "file.bin"),
                doc.get("file_size"),
            )
            if path:
                saved.append(path)
        photos = msg.get("photo") or []
        if photos:
            largest = max(photos, key=lambda p: p.get("file_size", 0))
            path = self._download(
                largest.get("file_id"), "photo.jpg", largest.get("file_size")
            )
            if path:
                saved.append(path)
        # Voice notes / audio: download and transcribe (opt-in STT).
        voice = msg.get("voice") or msg.get("audio")
        voice_note = ""
        if voice:
            vpath = self._download(
                voice.get("file_id"), "voice.ogg", voice.get("file_size")
            )
            if vpath:
                from . import voice as voice_stt

                if voice_stt.available():
                    text = voice_stt.transcribe(str(vpath))
                    voice_note = (
                        f'[Transkrip pesan suara pengguna]: "{text}"'
                        if text
                        else "[Pesan suara diterima tapi transkripsi gagal — "
                        "minta pengguna ketik ulang.]"
                    )
                else:
                    voice_note = (
                        "[Pengguna mengirim pesan suara, tapi transkripsi suara "
                        "(STT) belum dikonfigurasi (ENGRAM_STT_ENDPOINT). Beri "
                        "tahu pengguna dan minta versi teks. Jangan mengarang "
                        "isi suaranya.]"
                    )
        if not saved and not voice_note:
            return "", []
        images = [p for p in saved if p.suffix.lower() in self.IMAGE_EXTS]
        others = [p for p in saved if p not in images]
        rel = lambda p: str(p.relative_to(config.WORKSPACE_DIR))  # noqa: E731
        notes = []
        if voice_note:
            notes.append(voice_note)
        if images:
            notes.append(
                "[Pengguna mengirim gambar (terlampir di pesan ini — kamu "
                "BISA melihatnya): "
                + ", ".join(rel(p) for p in images)
                + ". Gambar juga tersimpan di workspace; gunakan view_image "
                "untuk melihatnya lagi nanti.]"
            )
        if others:
            notes.append(
                "[Pengguna mengirim file, tersimpan di workspace: "
                + ", ".join(rel(p) for p in others)
                + ". Gunakan read_file untuk membacanya — termasuk PDF, "
                "Word (.docx), Excel (.xlsx), dan PowerPoint (.pptx), bukan "
                "hanya teks. Tanggapi sesuai konteks.]"
            )
        return "\n".join(notes), [str(p) for p in images]

    def _download(self, file_id: str, fallback_name: str, size):
        from . import desktop

        if not file_id or (size and size > self.MAX_DOWNLOAD):
            return None
        try:
            info = self._call("getFile", file_id=file_id)
            file_path = info.get("file_path", "")
            url = f"https://api.telegram.org/file/bot{self.token}/{file_path}"
            resp = self.session.get(url, timeout=120)
            resp.raise_for_status()
            name = os.path.basename(file_path) or fallback_name
            return desktop.save_inbox_bytes(name, resp.content)
        except Exception:
            log.exception("failed to download attachment %s", file_id)
            return None

    # ---------------- commands ----------------
    def _handle_command(self, chat_id: str, text: str):
        parts = text.split(maxsplit=1)
        cmd = parts[0].split("@")[0].lower()
        arg = parts[1].strip() if len(parts) > 1 else ""
        self.store.log_event("user", "command", text, chat_id)
        if cmd == "/menu":
            self.send_keyboard(chat_id, "Aksi cepat:", self._MENU_BUTTONS)
        elif cmd in ("/start", "/help"):
            self.send(chat_id, HELP)
            if cmd == "/start":
                self.send_keyboard(chat_id, "Aksi cepat:", self._MENU_BUTTONS)
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
        elif cmd == "/audit":
            limit = int(arg) if arg.isdigit() else 15
            claims = self.store.active_claims(limit=limit, chat_id=chat_id)
            if not claims:
                self.send(chat_id, "Belum ada keyakinan aktif.")
                return
            lines = [
                f"#{c['id']} {c['subject']} | {c['predicate']} = {c['value'][:80]}\n"
                f"  [{c['kind']}, conf {c['confidence']:.2f}, sejak {c['valid_from'][:10]}]"
                for c in claims
            ]
            self.send(
                chat_id,
                "Keyakinan aktif terbaru (hapus yang salah dengan /forget <id>):\n\n"
                + "\n".join(lines),
            )
        elif cmd == "/forget":
            if not arg.isdigit():
                self.send(chat_id, "Pakai: /forget <id keyakinan> (lihat id di /audit)")
                return
            if not self.store.retire_claim(int(arg), chat_id):
                self.send(chat_id, f"Keyakinan #{arg} tidak ditemukan / sudah nonaktif.")
                return
            self.store.log_event(
                "user", "message",
                f"[FEEDBACK] Keyakinan memori #{arg} salah — user menghapusnya "
                f"lewat /forget.", chat_id,
            )
            self.send(chat_id, f"Oke, keyakinan #{arg} saya tutup — tidak akan "
                               f"saya percaya lagi. Riwayatnya tetap tersimpan.")
        elif cmd in ("/good", "/bad"):
            # Explicit outcome signal (S10). Logged as a normal user message so
            # the consolidation engine distills it into a lesson naturally.
            last = next(
                (e["content"] for e in reversed(self.store.recent_events(chat_id, 8))
                 if e["actor"] == "agent"), "")
            snippet = (last[:200] + "…") if len(last) > 200 else last
            if cmd == "/good":
                self.store.log_event(
                    "user", "message",
                    f"[FEEDBACK 👍] Jawaban terakhir bagus"
                    + (f": {arg}" if arg else ".")
                    + (f' (jawaban: "{snippet}")' if snippet else ""),
                    chat_id,
                )
                self.send(chat_id, "Siap, dicatat. 👍")
            else:
                if not arg:
                    self.send(chat_id,
                              "Pakai: /bad <apa yang salah> — biar saya belajar "
                              "persisnya (contoh: /bad formatnya kepanjangan).")
                    return
                self.store.log_event(
                    "user", "message",
                    f"[FEEDBACK 👎] Jawaban terakhir bermasalah: {arg}."
                    f' (jawaban yang dimaksud: "{snippet}") — sinyal outcome '
                    f"eksplisit; suling lesson yang actionable dari ini.",
                    chat_id,
                )
                self.send(chat_id,
                          "Dicatat — ini masuk memori pelajaran saya supaya "
                          "tidak terulang. 🙏")
        elif cmd == "/history":
            bits = arg.split()
            if len(bits) < 2:
                self.send(
                    chat_id,
                    "Pakai: /history <subjek> <atribut>  (contoh: /history user works_at)",
                )
                return
            rows = self.store.claim_history(bits[0].lower(), bits[1].lower())
            if not rows:
                self.send(chat_id, "Tidak ada riwayat untuk slot itu.")
                return
            lines = []
            for r in rows:
                status = (
                    "AKTIF"
                    if r["valid_to"] is None
                    else f"diganti {r['valid_to'][:10]}"
                )
                lines.append(f"• {r['valid_from'][:10]}: {r['value']} ({status})")
            self.send(chat_id, f"Riwayat {bits[0]}.{bits[1]}:\n" + "\n".join(lines))
        elif cmd == "/goals":
            goals = self.store.goals(chat_id, "active")
            if not goals:
                self.send(chat_id, "Belum ada goal aktif. Tambah dengan /goal <teks>.")
                return
            lines = [
                f"#{g['id']} {g['title']} (sejak {g['created_at'][:10]})" for g in goals
            ]
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
                conflicts = consolidator.sweep_contradictions(self.store)
                drafts = skill_compiler.mine(self.store)
            except Exception:
                log.exception("manual reflect failed")
                self.send(chat_id, "Konsolidasi gagal — cek log server.")
                return
            out = (
                f"Konsolidasi: {stats['events']} event diproses, "
                f"{stats['new']} keyakinan baru, {stats['updated']} diperbarui."
            )
            if insights:
                out += "\n\nInsight baru:\n" + "\n".join(
                    f"• {i['value']}" for i in insights
                )
            if conflicts:
                out += (
                    f"\n\n⚠️ {len(conflicts)} pasang keyakinan saling "
                    "bertentangan — saya tandai DISPUTED:\n" + "\n".join(
                        f"• #{c['id_a']} vs #{c['id_b']}: {c['reason']}"
                        for c in conflicts
                    ) + "\nCek dengan /audit, koreksi dengan /forget <id> atau "
                        "beri tahu saya mana yang benar."
                )
            if drafts:
                out += "\n\nDraft skill baru dari pengalaman kita:\n" + "\n".join(
                    f"• {d['name']} — {d['description']}\n  ({d['rationale']})\n"
                    f"  Aktifkan: /approve {d['name']}"
                    for d in drafts
                )
            self.send(chat_id, out)
        elif cmd == "/reminders":
            rows = self.store.pending_reminders(chat_id)
            if not rows:
                self.send(chat_id, "Tidak ada pengingat terjadwal.")
                return
            lines = [f"#{r['id']} {r['due_ts']} UTC — {r['message']}" for r in rows]
            self.send(chat_id, "Pengingat terjadwal:\n" + "\n".join(lines))
        elif cmd == "/tasks":
            rows = self.store.active_tasks(chat_id)
            if not rows:
                self.send(
                    chat_id,
                    "Tidak ada tugas terjadwal. Minta saja dalam obrolan, "
                    'mis. "kirim ringkasan berita AI tiap pagi jam 7".',
                )
                return
            lines = [
                f"#{r['id']} [{r['recurrence'] or 'sekali'}] "
                f"berikutnya {r['due_ts']} UTC\n   {r['prompt'][:150]}"
                for r in rows
            ]
            self.send(
                chat_id,
                "Tugas terjadwal:\n"
                + "\n".join(lines)
                + "\n\nBatalkan lewat obrolan atau /canceltask <id>.",
            )
        elif cmd == "/canceltask":
            if not arg.strip().isdigit() or not self.store.set_task_status(
                int(arg.strip()), "cancelled"
            ):
                self.send(chat_id, "Pakai: /canceltask <id>  (lihat /tasks)")
                return
            self.send(chat_id, f"Tugas #{arg.strip()} dibatalkan.")
        elif cmd == "/skills":
            metas = skills.entries()
            if not metas:
                self.send(
                    chat_id,
                    "Belum ada skill. Tambahkan file .md ke folder skills/, "
                    "atau ajari saya prosedur lewat obrolan.",
                )
                return
            lines = []
            for m in metas:
                badge = "📝 DRAFT" if m["status"] == "draft" else f"v{m['version']}"
                lines.append(f"• {m['name']} [{badge}] — {m['description']}")
            self.send(
                chat_id,
                "Skill:\n"
                + "\n".join(lines)
                + "\n\nDraft diaktifkan dengan /approve <nama>.",
            )
        elif cmd == "/skill":
            meta = skills.get(skills.sanitize(arg)) if arg else None
            if meta is None:
                self.send(chat_id, "Pakai: /skill <nama>  (lihat daftar di /skills)")
                return
            self.send(
                chat_id,
                f"{meta['name']} [{meta['status']}, v{meta['version']}]\n"
                f"{meta['description']}\n\n{meta['body']}",
            )
        elif cmd == "/approve":
            name = skills.sanitize(arg)
            if not name or not skills.approve(name):
                self.send(chat_id, "Pakai: /approve <nama draft>  (lihat /skills)")
                return
            self.store.log_event(
                "user", "command_result", f"skill '{name}' approved", chat_id
            )
            self.send(
                chat_id, f"Skill '{name}' aktif. Saya akan memakainya mulai sekarang."
            )
        elif cmd == "/doctor":
            self.send(chat_id, self._doctor_report(), rich=False)
        elif cmd == "/timezone":
            if not arg:
                cur = self.store.get_meta(f"tz_{chat_id}")
                self.send(
                    chat_id,
                    (f"Zona waktu kamu saat ini: {cur}." if cur
                     else "Zona waktu belum diset — reminder dihitung dalam UTC.")
                    + "\nSetel dengan: /timezone Asia/Jakarta "
                    "(atau Europe/London, America/New_York, dll).",
                )
            else:
                try:
                    from zoneinfo import ZoneInfo
                    ZoneInfo(arg.strip())
                except Exception:
                    self.send(chat_id, f"Zona waktu '{arg}' tidak dikenal. "
                              "Pakai format IANA, mis. Asia/Jakarta.")
                    return
                self.store.set_meta(f"tz_{chat_id}", arg.strip())
                self.send(chat_id, f"✅ Zona waktu diset ke {arg.strip()}. "
                          "Reminder/jadwal sekarang mengikuti waktu lokalmu.")
        elif cmd == "/identity":
            self.send(chat_id, identity.load())
        elif cmd == "/stats":
            from . import llm
            n_claims = len(self.store.active_claims(limit=100000))
            pending = self.store.unprocessed_count()
            u = llm.usage_snapshot()
            total = u["input_tokens"] + u["output_tokens"]
            self.send(
                chat_id,
                f"Keyakinan aktif: {n_claims}\n"
                f"Event belum dikonsolidasi: {pending}\n"
                f"Model chat: {config.CHAT_MODEL}\n"
                f"Model konsolidasi: {config.CONSOLIDATE_MODEL}\n"
                f"\nToken (sesi ini, reset saat restart):\n"
                f"  panggilan LLM: {u['calls']}\n"
                f"  input: {u['input_tokens']:,}\n"
                f"  output: {u['output_tokens']:,}\n"
                f"  total: {total:,}",
            )
        else:
            self.send(chat_id, "Perintah tidak dikenal. /help untuk daftar perintah.")

    # ---------------- diagnostics ----------------
    def _doctor_report(self) -> str:
        from . import desktop
        import shutil as _shutil

        lines = ["🩺 Engram Doctor", ""]
        lines.append(f"Provider: {config.PROVIDER}")
        lines.append(f"Model chat: {config.CHAT_MODEL or '(belum diset!)'}")
        lines.append(
            f"Model konsolidasi: {config.CONSOLIDATE_MODEL or '(belum diset)'}"
        )
        if config.PROVIDER != "anthropic":
            lines.append(f"Base URL: {config.OPENAI_BASE_URL or '(belum diset!)'}")
            lines.append(f"API key: {'✅ ada' if config.LLM_API_KEY else '❌ KOSONG'}")
        else:
            lines.append(
                f"API key: {'✅ ada' if config.ANTHROPIC_API_KEY else '❌ KOSONG'}"
            )
        lines.append("")
        caps = desktop.document_capabilities()
        label = {
            "native": "✅ native",
            "builtin": "✅ builtin (tanpa library — " "hasil lebih polos)",
        }
        lines.append("Dokumen:")
        for fmt in ("docx", "xlsx", "pptx", "pdf", "md", "html", "csv"):
            lines.append(f"  {fmt}: {label[caps[fmt]]}")
        libs = {"docx": "python-docx", "xlsx": "openpyxl",
                "pptx": "python-pptx", "pdf": "reportlab"}
        builtin = [f for f in ("docx", "xlsx", "pptx", "pdf") if caps[f] == "builtin"]
        if builtin:
            lines.append(
                "  → untuk hasil maksimal: pip install "
                + " ".join(libs[f] for f in builtin)
            )
        lines.append("")
        try:
            desktop.ensure_workspace()
            probe = desktop.write_file(".doctor_probe", "ok")
            probe.unlink()
            ws = f"✅ {config.WORKSPACE_DIR}"
        except Exception as exc:
            ws = f"❌ tidak bisa menulis: {exc}"
        lines.append(f"Workspace: {ws}")
        lines.append(
            f"Git: {'✅ terpasang' if _shutil.which('git') else '❌ tidak ada di PATH'}"
        )
        lines.append(f"Activity feed: {'aktif' if config.SHOW_ACTIVITY else 'mati'}")
        lines.append(
            f"run_python: {'aktif' if config.ENABLE_CODE_TOOL else 'mati (default)'}"
        )
        lines.append(
            f"run_shell: {'aktif' if config.ENABLE_SHELL_TOOL else 'mati (default)'}"
        )
        lines.append(f"Event belum dikonsolidasi: {self.store.unprocessed_count()}")
        # Opt-in capabilities (voice, semantic memory, integrations).
        from . import voice, embeddings, connectors, imagegen

        lines.append("")
        lines.append(f"Suara (STT): {'✅ aktif' if voice.available() else 'mati (set ENGRAM_STT_ENDPOINT)'}")
        lines.append(
            f"Memori semantik (embeddings): {'✅ aktif' if embeddings.available() else 'mati (BM25 saja)'}"
        )
        lines.append(
            f"Generasi gambar: {'✅ aktif' if imagegen.available() else 'mati (set ENGRAM_IMAGE_ENDPOINT)'}"
        )
        lines.append("Integrasi:")
        for name, state in connectors.status().items():
            mark = "✅" if state == "ready" else "—"
            lines.append(f"  {mark} {name}: {state}")
        return "\n".join(lines)
