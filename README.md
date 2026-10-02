# remote-luffy

Lihat dan kontrol laptop (Hyprland/Wayland) dari browser HP: klik, drag, scroll, ketik, copy-paste.

## Pasang
```
sudo apt install grim wl-clipboard python3-pil
sudo modprobe uinput
sudo setfacl -m u:$USER:rw /dev/uinput     # izin kontrol (hilang saat reboot)
```
`python3-pil` membuat gambar JPEG tajam dan ringan (tanpa itu jatuh ke PNG yang buram/berat).
Agar izin `/dev/uinput` permanen: `echo uinput | sudo tee /etc/modules-load.d/uinput.conf`
dan buat rule udev `KERNEL=="uinput", GROUP="input", MODE="0660"`, lalu masukkan user ke grup `input`.

## Jalankan (dari terminal di dalam sesi Hyprland)
```
export LUFFY_PASSWORD='password-panjang-dan-kuat'
python3 server.py --host 0.0.0.0
```
Buka `http://IP-laptop:8080` di HP. Opsi: `--scale 1.0` (lebih tajam), `--quality 80`, `--fps 8`,
`--output eDP-1`, `--view-only`, `--debug`.

## Cara pakai di HP
- **Touchpad (panel bawah)**: geser = gerakkan kursor (halus), ketuk = klik, ketuk 2 jari = klik kanan, geser 2 jari = scroll, ketuk lalu langsung geser = drag. Tombol *Tahan (drag)* untuk menahan klik kiri. Tombol 🖱 menyembunyikan panel.
- Layar sendiri juga bisa diketuk langsung (kurang halus).
- **Ketuk** = klik kiri, **tahan** = klik kanan, **geser 1 jari** = drag (pilih teks), **geser 2 jari** = scroll.
- **⌨** membuka keyboard HP. Ctrl/Alt/Shift/Super bersifat lengkap-sekali-pakai (tekan, lalu tombol lain).
- Tombol Copy/Paste/Cut/All/Undo, Esc, Tab, Enter, panah, dll ada di bar bawah.
- **📋 Panel clipboard**
  - HP → laptop: ketik/tempel teks di kotak, lalu *Kirim & tempel* (*Tempel (terminal)* untuk Ctrl+Shift+V).
  - Laptop → HP: pilih teks di laptop, tekan **Copy**, buka 📋, *Ambil dari laptop*, lalu *Salin ke HP*.
- Ketik langsung mengasumsikan layout keyboard laptop **US**; teks non-US dikirim lewat clipboard.

## Akses dari luar rumah
Biarkan `--host 127.0.0.1`, jalankan `cloudflared tunnel --url http://localhost:8080`, dan start server dengan
`--secure-cookie`. Jangan buka port ke internet tanpa HTTPS.

## Keamanan
Halaman ini mengendalikan seluruh laptop. Login password (env, min. 12 karakter), cookie HttpOnly/SameSite=Strict,
API hanya menerima JSON, IP dikunci setelah 5 kali salah. Pakai password unik dan kuat.
