# remote-luffy

Lihat layar laptop (Hyprland/Wayland) dari browser HP. Tahap 1: **view-only**.

## Pasang & jalankan
```
sudo apt install grim python3
export LUFFY_PASSWORD='password-panjang-dan-kuat'
python3 server.py            # localhost:8080
```
Opsi: `--fps 8 --quality 60 --scale 0.5 --output eDP-1 --host 0.0.0.0`

Jalankan dari sesi Hyprland (butuh `WAYLAND_DISPLAY` terisi).

## Akses
- **Satu Wi-Fi:** `--host 0.0.0.0`, buka `http://IP-laptop:8080` di HP (HTTP biasa, hanya di jaringan tepercaya).
- **Dari luar:** biarkan `--host 127.0.0.1`, lalu `cloudflared tunnel --url http://localhost:8080`
  dan jalankan server dengan `--secure-cookie`. Jangan buka port ke internet tanpa HTTPS.

## Keamanan
Login password (dari env, min. 12 karakter), cookie HttpOnly/SameSite, kunci IP setelah 5 kali salah.
Tahap 2 (kontrol lewat `ydotool`) belum ada.
