# voxtap

Tap a key, get voice transcribed. Local speech-to-text powered by [faster-whisper](https://github.com/SYSTRAN/faster-whisper).

- Runs 100% locally — no cloud API, no data leaves your machine
- Rich text editor (Qt) with bold, italic, underline, headings, lists, alignment
- Copies text as **Markdown** to clipboard on close or on demand
- Animated waveform + breathing border while recording
- Pauses Spotify automatically during recording
- Paste screenshots as file paths (Ctrl+V)
- Toggle recording on/off with a single keybinding (`voxtap-toggle`)
- Optional LLM polish via local [Ollama](https://ollama.com/) — cleans filler words, fixes punctuation
- Cross-platform: Linux (X11 & Wayland), macOS, and Windows

## Quick Start

```bash
pip install voxtap
voxtap
```

The first run downloads the Whisper model (~1.5 GB for `distil-large-v3`). A progress dialog shows the download status. Recording starts automatically once the model is loaded.

## System Dependencies

voxtap needs a working audio input, a clipboard utility, and Qt6.

### Linux (Debian/Ubuntu)

```bash
sudo apt install portaudio19-dev xclip
# Wayland users: sudo apt install wl-clipboard
```

### Linux (Fedora)

```bash
sudo dnf install portaudio-devel xclip
```

### Linux (Arch)

```bash
sudo pacman -S portaudio xclip
```

### macOS

```bash
brew install portaudio
# pbcopy ships with macOS — no extra clipboard tool needed
```

### Windows

No extra system dependencies are needed — PortAudio is bundled with the `sounddevice` Python package and clipboard access uses PowerShell.

## Usage

```bash
voxtap                              # Start with defaults (distil-large-v3, English)
voxtap --model small                # Use a smaller/faster model
voxtap --model large-v3             # Use the full large model for max accuracy
voxtap --language de                # Transcribe German
voxtap --device cpu                 # Force CPU (skip CUDA auto-detection)
```

### Toggle Keybinding

Bind `voxtap-toggle` to a key in your window manager for quick access. If an instance is already running, it toggles recording on/off; otherwise it launches a new one. See [docs/keybindings.md](docs/keybindings.md) for setup instructions for i3, Sway, Hyprland, GNOME, KDE, Windows, and macOS.

## Configuration

| Flag | Default | Description |
|------|---------|-------------|
| `--model` | `distil-large-v3` | Whisper model (`tiny`, `small`, `medium`, `large-v3`, `distil-large-v3`, ...) |
| `--language` | `en` | Language code (`en`, `de`, `fr`, `es`, ...) |
| `--device` | auto | `cpu`, `cuda`, or `auto` (tries CUDA first) |

## Editor Features

- **Bold** (Ctrl+B), **Italic** (Ctrl+I), **Underline** (Ctrl+U), **Strikethrough** (Ctrl+Shift+S)
- **Headings** (H1, H2, H3)
- **Bullet lists** and **numbered lists**
- **Text alignment** (left, center, right)
- **Paste image paths** — Ctrl+V with a screenshot in clipboard inserts the file path
- **Copy as Markdown** — button or automatic on window close
- Full undo/redo support

## How It Works

1. **voxtap** opens a Qt window and starts recording from your microphone
2. Audio is buffered and transcribed every 1.5 seconds using faster-whisper
3. Transcribed text is appended to the editor (or replaces selected text)
4. You can pause recording, edit text freely, then resume
5. If [Ollama](https://ollama.com/) is running locally, transcribed text is polished (filler words removed, punctuation fixed)
6. On close (Escape), the editor content is copied to clipboard as Markdown
7. Spotify is automatically paused during recording and resumed when you stop (Linux)

## LLM Polish (Optional)

voxtap can use a local LLM via [Ollama](https://ollama.com/) to clean up transcriptions — removing filler words, fixing punctuation, and correcting repeated words. This step is entirely optional; transcription works fine without it.

1. Install Ollama: https://ollama.com/download
2. Pull the model:

```bash
ollama pull gpt-oss:20b
```

3. Make sure Ollama is running (`ollama serve` or the desktop app), then start voxtap as usual. The status bar will show the active LLM model.

If Ollama is not running or the model is not available, voxtap silently skips the polish step.

## Troubleshooting

**"PortAudio library not found"** — Install `portaudio19-dev` (Debian) or `portaudio` (brew/pacman).

**"wl-copy/xclip not found"** — Install a clipboard utility for your display server (see System Dependencies above).

**CUDA not detected** — Install PyTorch with CUDA support, or use `--device cpu`.

**Slow on CPU** — Use a smaller model: `voxtap --model small`.

**LLM polish not working** — Make sure [Ollama](https://ollama.com/) is running and the model is available. This feature is optional; transcription works fine without it.

## License

[MIT](LICENSE)
