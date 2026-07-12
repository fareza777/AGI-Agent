# Audit Engram-Agent — Fokus: Halusinasi & Perilaku Aneh

*Audit engine, memory substrate, dan guard anti-halusinasi. Juli 2026.*

---

## Ringkasan eksekutif

Arsitektur ENGRAM (event log → konsolidasi → claims → composer) secara desain
sudah benar dan justru **lebih tahan halusinasi** daripada agent memory-file
biasa. Masalah "suka halusinasi dan aneh" datang dari **empat sumber**, urut
dari yang paling berdampak:

1. **Model chat yang lemah dalam tool-calling** (riwayat commit menunjukkan
   MiniMax M2 via OpenRouter/direct). Sebagian besar halusinasi — mengarang isi
   folder, mengaku sudah kirim file, menyebut pesan user "prompt injection" —
   adalah kegagalan model, bukan engine. Engine sudah menambal dengan banyak
   guard, tapi guard berbasis regex tidak bisa 100%.
2. **Guard regex yang menambal gejala, bukan akar** — dan beberapa guard itu
   sendiri menciptakan keanehan baru (false positive). Detail di bawah.
3. **Sisa jalur self-poisoning memory**: composer sudah diperbaiki (episode
   hanya me-replay pesan user), tapi **consolidator masih menyuling balasan
   agent menjadi "fakta"** — halusinasi agent bisa jadi belief permanen.
4. **System prompt yang membengkak dan berlawanan arah**: blok instruksi berisi
   puluhan larangan ALL-CAPS yang justru MENGUTIP teks halusinasi yang dilarang
   ("prompt injection", "duplikat", daftar frasa terlarang). Pada model lemah,
   mengutip failure mode bisa *memicu* failure mode (efek "jangan pikirkan
   gajah").

## Yang sudah diperbaiki di pass ini

| # | Perbaikan | File | Dampak |
|---|-----------|------|--------|
| 1 | Bug `str.lstrip("www.")` di guard link — memotong *karakter* w dan titik, bukan prefix, sehingga domain seperti `weather.com` jadi `eather.com` dan link **valid** dicap "belum terverifikasi" | `agent.py` | Menghapus peringatan sistem palsu (sumber "aneh") |
| 2 | `force_tools`: retry saat model stall/mengarang listing sekarang **memaksa tool call di level API** (`tool_choice: required`/`any`), bukan sekadar bujukan prompt. Fallback otomatis jika provider menolak | `llm.py`, `agent.py` | Membunuh pola "menjelaskan rencana tapi tidak mengerjakan" dan "mengarang listing tanpa list_dir" pada model lemah |
| 3 | Aturan grounding di consolidator: **fakta/preferensi hanya boleh disuling dari ucapan USER**, bukan dari balasan agent (balasan agent hanya boleh jadi bahan "lesson") | `consolidator.py` | Menutup jalur halusinasi agent → belief permanen |
| 4 | Filter `_looks_like_fs_dump` di-scope per aktor: pesan **user** yang menyebut "my drive" atau berisi ≥6 bullet tidak lagi dibuang dari riwayat percakapan | `composer.py` | Agent tidak lagi "amnesia" terhadap permintaan yang baru saja diketik user |
| 5 | `recent_lessons` sekarang di-scope per chat (+ global) seperti query memory lain | `store.py`, `composer.py` | Lesson dari chat lain tidak menyetir perilaku chat ini |
| 6 | FS-preflight tanpa path resolvable sekarang diam (return None) — sebelumnya menyuntik catatan "minta path" bahkan untuk "baca file config.py" | `agent.py` | Menghilangkan interupsi aneh pada permintaan file biasa |
| 7 | Knob `ENGRAM_TEMPERATURE` untuk provider OpenAI-compatible | `config.py`, `llm.py`, `.env.example` | Temperatur rendah (0.2–0.4) terbukti menekan pengarangan nama/link pada model lemah |

Semua tercakup regression test baru (`AntiHallucinationFixTests`, 6 test);
suite penuh 95 pass.

> **Status pass 2 (follow-up):** P2 (system prompt ditulis ulang ±80% lebih
> ramping, aturan positif, tanpa mengutip frasa halusinasi), P4 (karantina
> otomatis via `store.quarantine_poisoned_claims()` + marker terpusat di
> `engram/hygiene.py` + `reflect()` difilter), roadmap #1 (`/good`, `/bad` →
> lesson lewat jalur konsolidasi normal), #2 (`/audit`, `/forget`), dan #3
> (contradiction sweep S4 tahap 2, kolom `disputed`, tag DISPUTED di context)
> sudah diimplementasikan. Sisa: P1 (pilihan model), P5 (isi endpoint
> embeddings), roadmap #4 (regression suite) dan #5 (goal tree penuh).

## Temuan yang BELUM diperbaiki (rekomendasi, urut prioritas)

### P1 — Ganti / naikkan kelas model chat
Ini tuas terbesar. Guard sekuat apa pun kalah dengan model yang patuh tool.
Opsi berbiaya naik:
- Tetap MiniMax M2 untuk chat, tapi set `ENGRAM_TEMPERATURE` sesuai anjuran
  vendor dan **pakai model lebih kuat khusus `ENGRAM_CONSOLIDATE_MODEL`**
  (konsolidasi yang salah = racun permanen; ini panggilan kecil dan jarang,
  jadi murah).
- Atau hybrid: Claude Haiku untuk chat sehari-hari (tool-calling andal, murah),
  model besar hanya untuk tugas berat.

### P2 — Rampingkan & restrukturisasi system prompt
`composer._INSTRUCTIONS` sekarang ± 2.500 kata, sebagian besar larangan
reaktif. Masalah:
- Mengutip verbatim frasa halusinasi yang dilarang → priming.
- Larangan ganda/bertumpuk ("NEVER" × 12) menurunkan kepatuhan per aturan.
- Blok "Trust" yang membahas prompt injection justru memperkenalkan konsep itu
  ke setiap giliran.

Saran: tulis ulang jadi ≤ 800 kata **aturan positif** ("selalu panggil list_dir
sebelum menyebut nama folder") dan pindahkan aturan per-fitur ke skill yang
dimuat on-demand. Hapus blok "Trust"/anti-injection sepenuhnya — filter stale
claim + fix konsolidasi sudah menutup akarnya.

### P3 — Verifikasi berbasis model, bukan regex
Guard regex (`_PROMISE_RE`, `_FILE_SENT_RE`, `_FS_LISTING_RE`, dst.) rapuh dua
arah (false positive → catatan sistem aneh; false negative → lolos). Ganti
bertahap dengan satu pass verifikasi murah: setelah reply pada giliran
"berisiko" (ada klaim file/listing/link), panggil model konsolidasi dengan
pertanyaan terstruktur *"apakah reply ini mengklaim sesuatu yang tidak
didukung tool_output giliran ini?"* → jika ya, retry dengan `force_tools`.
Satu mekanisme menggantikan ± 8 regex.

### P4 — Bersihkan belief yang sudah teracuni
DB produksi (lihat `memory_audit.txt`) berisi claim dari era sebelum fix.
Tambahkan perintah `/quarantine` atau skrip one-off yang menutup
(`valid_to = now`) semua claim aktif yang mengandung marker di
`_STALE_CLAIM_MARKERS` / `_FS_PRED_TOKENS` — sekarang marker itu hanya
difilter saat retrieval, padahal barisnya masih aktif dan bisa lolos lewat
jalur lain (mis. `reflect()` membaca `active_claims()` TANPA filter stale —
insight bisa lahir dari claim beracun).

### P5 — Aktifkan embeddings
Retrieval murni BM25 lintas bahasa (query Indonesia vs belief Inggris) sering
gagal match → model merasa "tidak ingat" → mengarang. `ENGRAM_EMBED_ENDPOINT`
sudah didukung penuh di kode; tinggal diisi. Ini peningkatan kualitas recall
termurah yang tersedia.

### P6 — Perbaikan kecil lain
- `check_memory.py` / `check_recent.py` crash di Windows cp1252 (lihat
  `memory_audit.txt`) — bungkus output dengan `sys.stdout.reconfigure(encoding="utf-8")`.
- `_est_tokens` (chars/4) meleset untuk teks non-Latin; cukup baik untuk ID/EN.
- Boost confidence duplikat (`+0.05` tiap kali) bisa memompa claim lemah ke
  1.0 hanya karena sering diulang model konsolidasi — pertimbangkan cap 0.98
  dan hanya boost bila batch sumber mengandung event user.
- `reflect()` memakai `active_claims()` global (lintas chat) — insight bisa
  bocor antar user; scope per chat seperti query lain.

## Fitur yang layak ditambah (roadmap)

1. **Outcome tracking eksplisit (S10)** — tombol/perintah 👍/👎 per jawaban →
   label outcome di event log → bahan lesson yang presisi, bukan tebakan LLM.
2. **`/audit` command** — tampilkan claim aktif + confidence + provenance
   langsung dari Telegram, dengan `/forget <id>` untuk menutup claim salah.
   (Sekarang koreksi belief hanya bisa lewat percakapan → lambat.)
3. **Contradiction sweep implisit (S4 tahap 2)** — sampling pasangan claim
   se-entitas dan minta model menandai konflik; sekarang hanya slot-collision
   yang terdeteksi.
4. **Regression suite kegagalan (S10)** — simpan giliran yang gagal sebagai
   fixture replay; jalankan tiap ganti model/prompt supaya "membaik" jadi
   terukur, bukan perasaan.
5. **Goal tree berhierarki + review terjadwal (S7 penuh)** — `goals` masih
   flat list tanpa deadline/review; DESIGN.md sudah menspesifikasikan bentuk
   penuhnya.

## Kesimpulan

Fondasi arsitektur sehat; yang perlu dihentikan adalah pola *menambal gejala
model lemah dengan regex baru setiap insiden*. Pass ini memperbaiki bug guard
yang menciptakan keanehan sendiri, menutup jalur self-poisoning terakhir di
consolidator, dan memberi engine kemampuan **memaksa** tool call alih-alih
membujuk. Langkah berikutnya yang paling bernilai per usaha: model konsolidasi
yang lebih kuat (P1), perampingan system prompt (P2), dan karantina belief
lama yang teracuni (P4).
