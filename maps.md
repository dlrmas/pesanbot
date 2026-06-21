## Peta Kode

```
main.py               titik masuk (bot + asisten)
app/config.py         konfigurasi .env
app/db.py             koneksi Mongo, indeks, pengaturan sistem
app/clients.py        klien Kurigram (bot & asisten)
app/core/             ── mesin sarang laba-laba (reusable) ──
  router.py           satu pintu callback + state FSM + command, guard peran/ban
  screens.py          Clean Chat UI: satu pesan bot aktif per chat
  drafts.py           mesin draf + preview + pipeline kirim (semua fitur)
  delivery.py         kirim ke channel / target / thread anonim
  content.py          terima & kirim teks/foto/video/audio/voice
  moderation.py       kata terlarang → clean/suspicious/blocked (ternormalisasi)
  hashtags.py         validasi, ikon admin, saran pintar
  wallet.py           poin harian (lazy reset WIB) + coin + VIP, ledger idempoten
  ratelimit.py        lock anti-dobel, cooldown per pengguna
  effects.py          pemilihan animated message effect semantik
  audit.py            log audit + skor risiko
  ui.py               bahasa visual (tombol berwarna, blockquote, bar energi)
  users.py, utils.py, normalize.py
app/features/         ── alur fitur (tipis) ──
  start.py  menfes.py  confes.py  replies.py  profile.py  inline.py
  admin/              panel, review, pengguna+VIP, voucher, broadcast,
                      kata terlarang/hashtag/channel/konfigurasi, statistik,
                      updater (update dari GitHub + restart)
app/assistant/        runner akun asisten (peringatan satu kali, auto-stop saat limit)
tools/gen_session.py  pembuat session string asisten
```

