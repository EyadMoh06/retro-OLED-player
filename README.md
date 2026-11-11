<p align="center">
  <img src="https://github.com/user-attachments/assets/454544c8-7f91-4c38-af99-76a4a5abb716" alt="Retro OLED Player logo" width="128">
</p>

<h1 align="center">Retro OLED Player</h1>

<p align="center">Turn any video into a tiny old screen with a tiny old speaker.</p>

Retro OLED Player plays your videos the way an old device would: a 128x128 4-bit OLED on a 2005 phone, a curved CRT monitor, or a grainy VHS camcorder, with crunchy low-quality audio to match.

<p align="center">
  <img src="https://github.com/user-attachments/assets/d7e7e1ae-4006-46ee-800f-a3e47cd3a2df" alt="Retro OLED Player preview" width="400">
</p>

testing on "Mischievous Function"
<p align="center">
  <img width="642" height="636" alt="Screenshot 2026-11-12 013640" src="https://github.com/user-attachments/assets/d7a69e71-1edb-4caa-8640-01d541164a7e" />

</p>

## Features

**Video**
- Resolution: 64x64, 128x128 or 256x256 (aspect ratio is kept, with black bars)
- Bit depth from 1-bit to 8-bit, with Bayer dithering so low bit depths still look rich
- Frame rate: 10, 15, 24 or 30 fps
- Screen tints: amber, green, blue-white, white
- Brightness, contrast and invert

**Effects**
- Pixel grid like a real small display
- CRT screen: curved glass, scanlines, vignette
- Glow / bloom and phosphor ghosting
- Old recording devices: tape grain, VHS tracking glitches, rolling interference bar, camcorder overlay (blinking REC, battery, timecode)

**Audio**
- Bit depth from 4-bit to 16-bit and sample rates from 4000 Hz to 22050 Hz
- Tiny-speaker band-limiting, distortion and hiss (each can be switched off), or mute

**Presets:** 2005 Phone OLED, CRT Monitor, VHS Camcorder, Security Cam. Pick one, then tweak anything.

Videos play on a loop and can be paused and resumed.

## Download (Windows)

1. Open the [Releases](../../releases) page and download `retro_oled.exe`.
2. Double-click it. That's it. No installation and no Python needed.

Windows or your antivirus may show a warning because the app is unsigned. This is common for small programs like this one. Choose "More info" and then "Run anyway" if you trust it.

## How to use

1. Click **Browse** and choose a video file.
2. Pick a preset, or set the options yourself.
3. Press **PLAY**.

Controls in the video window:

| Key | Action |
|---|---|
| Space / left click | Pause / resume |
| T | Change tint |
| Esc | Close the window |

The video is converted before it plays, so a long video takes longer to load. Short clips start almost immediately. The 256x256 size with the CRT effect is the heaviest setting; if playback stutters, try 128x128 or turn off the glow.

## How it works

- **Video:** frames are read with OpenCV, shrunk, turned to grayscale, adjusted, dithered and quantized to the chosen bit depth. Each tiny pixel is then enlarged and drawn with the grid, CRT and recording-device effects.
- **Audio:** ffmpeg extracts and resamples the sound, then it is band-limited, distorted and bit-crushed. Video and audio stay in sync by following the clock and restarting together on each loop.

## About AI use

This project was made with AI, and I want to be open about it.

- **The code was written by Claude Sonnet 5.5**, an AI model from Anthropic.
- **Everything else was done by me:** the idea and design decisions, the logo, revising and refining the program, testing, building the exe, and uploading and publishing the project.

## Built with

[Python](https://www.python.org/), [OpenCV](https://opencv.org/), [NumPy](https://numpy.org/), [pygame](https://www.pygame.org/), [imageio-ffmpeg](https://github.com/imageio/imageio-ffmpeg) and [FFmpeg](https://ffmpeg.org/).

## License

MIT. See the [LICENSE](LICENSE) file.
