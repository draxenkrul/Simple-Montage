# bin/

Put the external programs the app needs here (they are not committed to git):

| File | Where to get it |
|------|-----------------|
| `ffmpeg.exe`, `ffprobe.exe` | "release essentials" build from https://www.gyan.dev/ffmpeg/builds/ (copy them from its `bin` folder) |
| `libmpv-2.dll` | `mpv-dev-x86_64-*.7z` from https://github.com/zhongfly/mpv-winbuild/releases or https://sourceforge.net/projects/mpv-player-windows/files/libmpv/ |

The app looks here first, then on the system `PATH`.
