# Keybinding Setup

Bind `voxtap-toggle` to a key so you can start/stop transcription with a single press. Below are instructions for common window managers and desktop environments.

Make sure `voxtap-toggle` is on your `$PATH` (it is if you installed with `pip install voxtap`).

## i3

Add to `~/.config/i3/config`:

```
bindsym $mod+Shift+t exec --no-startup-id voxtap-toggle
```

Then reload: `$mod+Shift+r`

## Sway

Add to `~/.config/sway/config`:

```
bindsym $mod+Shift+t exec voxtap-toggle
```

Then reload: `swaymsg reload`

## Hyprland

Add to `~/.config/hypr/hyprland.conf`:

```
bind = $mainMod SHIFT, T, exec, voxtap-toggle
```

## GNOME

```bash
# Create custom shortcut via gsettings
gsettings set org.gnome.settings-daemon.plugins.media-keys custom-keybindings \
  "['/org/gnome/settings-daemon/plugins/media-keys/custom-keybindings/voxtap/']"

gsettings set org.gnome.settings-daemon.plugins.media-keys.custom-keybinding:/org/gnome/settings-daemon/plugins/media-keys/custom-keybindings/voxtap/ \
  name 'voxtap toggle'

gsettings set org.gnome.settings-daemon.plugins.media-keys.custom-keybinding:/org/gnome/settings-daemon/plugins/media-keys/custom-keybindings/voxtap/ \
  command 'voxtap-toggle'

gsettings set org.gnome.settings-daemon.plugins.media-keys.custom-keybinding:/org/gnome/settings-daemon/plugins/media-keys/custom-keybindings/voxtap/ \
  binding '<Super><Shift>t'
```

Or use **Settings > Keyboard > Custom Shortcuts** in the GUI.

## KDE Plasma

1. Open **System Settings > Shortcuts > Custom Shortcuts**
2. Click **Edit > New > Global Shortcut > Command/URL**
3. Set the trigger to your preferred key (e.g., `Meta+Shift+T`)
4. Set the command to `voxtap-toggle`

## macOS (skhd)

Install [skhd](https://github.com/koekeishiya/skhd):

```bash
brew install koekeishiya/formulae/skhd
brew services start skhd
```

Add to `~/.config/skhd/skhdrc`:

```
shift + cmd - t : voxtap-toggle
```

## macOS (Automator / Shortcuts)

1. Open **Automator** and create a new **Quick Action**
2. Add a **Run Shell Script** action with: `voxtap-toggle`
3. Save it (e.g., "Toggle voxtap")
4. Go to **System Settings > Keyboard > Keyboard Shortcuts > Services**
5. Find your Quick Action and assign a shortcut
