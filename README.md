# voxtap

Tap a key, get voice transcribed. Local speech-to-text powered by [faster-whisper](https://github.com/SYSTRAN/faster-whisper).

- Runs 100% locally — no cloud API, no data leaves your machine
- Auto-copies transcription to clipboard as you speak
- Dark themed tkinter GUI, always-on-top
- Toggle recording on/off with a single keybinding
- Cross-platform: Linux (X11 & Wayland) and macOS

## Quick Start

```bash
pip install voxtap
voxtap
```

The first run downloads the Whisper model (~500 MB for `small`). A window appears and recording starts automatically.

## System Dependencies

voxtap needs a working audio input and a clipboard utility.

### Linux (Debian/Ubuntu)

```bash
sudo apt install python3-tk portaudio19-dev xclip
# Wayland users: sudo apt install wl-clipboard
```

### Linux (Fedora)

```bash
sudo dnf install python3-tkinter portaudio-devel xclip
```

### Linux (Arch)

```bash
sudo pacman -S tk portaudio xclip
```

### macOS

```bash
brew install portaudio
# pbcopy ships with macOS — no extra clipboard tool needed
# tkinter ships with the Homebrew Python formula
```

## Usage

```bash
voxtap                     # Start with defaults (model=small, lang=en)
voxtap --model large-v3    # Use a larger model for better accuracy
voxtap --language de       # Transcribe German
voxtap --device cpu        # Force CPU (skip CUDA auto-detection)
```

### Toggle with a Keybinding

Use `voxtap-toggle` to start voxtap or stop an already-running instance:

```bash
voxtap-toggle              # First call: launches voxtap
voxtap-toggle              # Second call: sends stop signal (SIGUSR1)
```

Bind `voxtap-toggle` to a key in your window manager. See [docs/keybindings.md](docs/keybindings.md) for setup instructions for i3, Sway, Hyprland, GNOME, KDE, and macOS.

## Configuration

| Flag | Default | Description |
|------|---------|-------------|
| `--model` | `small` | Whisper model (`tiny`, `base`, `small`, `medium`, `large-v3`) |
| `--language` | `en` | Language code (`en`, `de`, `fr`, `es`, ...) |
| `--device` | auto | `cpu`, `cuda`, or `auto` (tries CUDA first) |

## How It Works

1. **voxtap** opens a tkinter window and starts recording from your microphone
2. Audio is buffered and transcribed every 3 seconds using faster-whisper
3. Transcribed text is displayed and auto-copied to the clipboard
4. You can edit the text in the window — edits are synced to clipboard
5. **voxtap-toggle** uses a PID file (`~/.cache/voxtap/voxtap.pid`) and POSIX signals to start/stop

## Troubleshooting

**"No module named tkinter"** — Install your distro's `python3-tk` package.

**"PortAudio library not found"** — Install `portaudio19-dev` (Debian) or `portaudio` (brew/pacman).

**"wl-copy/xclip not found"** — Install a clipboard utility for your display server (see System Dependencies above).

**CUDA not detected** — Install PyTorch with CUDA support, or use `--device cpu`.

## License

[MIT](LICENSE)
