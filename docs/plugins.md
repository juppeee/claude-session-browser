# Writing a plugin

A plugin is a folder with a manifest and a `ui.js` that fills a tab of its own.

```
%LOCALAPPDATA%\ClaudeSessionBrowser\plugins\<id>\<version>\
    manifest.json
    ui.js
    i18n.json     (optional)
    backend.py    (only with the "native" permission)
```

Versions sit side by side; the app loads the highest one it can use. While
developing, point the loader somewhere else with `CSB_PLUGINS_DIR`.

Starting the app with **Shift** held down loads no plugins at all. That is the
way back in when one of them misbehaves.

## manifest.json

```json
{
  "id": "ssd-health",
  "name": "Drive health",
  "version": "1.0.0",
  "api_version": 1,
  "min_app_version": "1.5.3",
  "description": "What it does, in one or two sentences.",
  "author": "your name",
  "permissions": ["native"],
  "ui": "ui.js",
  "i18n": "i18n.json",
  "backend": "backend.py"
}
```

`id` must match the folder name and may contain lowercase letters, digits,
`-` and `_`. `version` must match the version folder. `api_version` is the
plugin API the app speaks — currently `1`; a plugin built for another one is
not loaded. `min_app_version` is checked against the app's version.

## ui.js

The file runs in the window and receives one object, `csb`:

The app draws the tab header itself — icon, the plugin's name and a status
field on the right — so a plugin tab looks like every built-in one. Your
container sits below it.

| | |
|---|---|
| `csb.el` | your container, below the header; fill it with your own markup |
| `csb.call(method, args)` | calls `call()` in your `backend.py`, returns `{ok, result}` or `{ok:false, error}` |
| `csb.t(text, vars)` | translates through your `i18n.json` and the app's table |
| `csb.toast(text)` | short message at the bottom of the window |
| `csb.onEnter(fn)` | runs whenever your tab is opened |
| `csb.setStatus(text)` | the text on the right of the tab header |
| `csb.onLeave(fn)` | runs when it is left — stop your timers here |
| `csb.id`, `csb.version` | your own id and version |

There is no `api` and no access to the app's state: everything a plugin needs
goes through `csb.call()`. The app's own classes (`card`, `row2`, `lbl`,
`desc`, `sub`, `secthead`, `dt-rows`) are available, so a tab can look like the
rest of the window instead of bolted on.

If your code throws while loading, you get no tab. If it throws while opening,
the tab is removed and the window says so, rather than going down with it.

## i18n.json

The key is the string as you write it in your code, and you ship a table per
language you are not writing in. A plugin written in English:

```json
{ "de": { "Drives": "Laufwerke", "used": "belegt" } }
```

The table is merged into the app's when your plugin loads, so `csb.t()` and the
app's own `t()` behave the same.

The `name` from your manifest goes through the same table, so put it in there
too and your tab is named in the reader's language. Plugin tabs are placed
before Settings, which always stays last.

## backend.py

Only for plugins that declare `"native"`, and only when a tab genuinely needs
the system. Two functions:

```python
def setup(host):     # once at load; keep the host if you need it
    ...

def call(method, args):   # whatever csb.call() asks for
    return {...}          # must be JSON-serialisable
```

`host` is all a plugin gets from the app, and every part of it is tied to a
permission from the manifest:

| | |
|---|---|
| `host.log(msg)` | a line in the app's output |
| `host.get_setting(key, default)` / `host.set_setting(key, value)` | your own settings, stored under `plugins.<id>.data` |
| `host.usage()` | the last known rate-limit figures — needs `usage` |
| `host.sessions()` | the session list as the table shows it — needs `sessions` |

Anything not declared in the manifest does not exist at runtime. Raising an
exception is fine: the call is reported as failed, and your plugin stays
loaded.

Keep in mind that the app is a PyInstaller build. A plugin can only import
packages that are already bundled with it, or the standard library.

## Permissions

| | |
|---|---|
| `usage` | read the 5-hour and weekly rate-limit figures |
| `sessions` | read the session list |
| `network` | the plugin talks to the internet |
| `native` | the plugin ships Python code |

They are shown with the plugin, and they are the review checklist: what is not
declared does not have to be looked for in the code.

## Reference plugin

`plugins/ssd-health/` in this repository is a complete, working example with
all of the above.
