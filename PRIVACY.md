# Privacy

Vocalm listens to your microphone, so here is exactly what it does with it.

**It never records audio.** Sound is turned into a level (one number, about 90 times a second) and the sound itself
is thrown away straight after. Nothing you say is saved, transcribed or sent anywhere.

**What it keeps**, in `%LOCALAPPDATA%\Vocalm` on your PC:

- `settings.json`: your settings.
- `vocalm.db`: for each loud moment, when it was, how loud, how long and your line at the time; per minute, how long
  you talked, how long you were over your line and your average and peak level; and when Vocalm was running.
- With *Note which app or game is in front* on (Settings), the **name of the program** in front while you talked,
  like "VALORANT" or "Discord". Never window titles, so nothing about what you're browsing, watching or typing.
- `vocalm.log`: start-up messages and errors.

**Nothing leaves your PC.** There's no account and no telemetry. The dashboard is served only on `127.0.0.1`, so
other devices can't reach it, and it refuses requests that don't come from its own page, so other websites open in
your browser can't read your history or change your settings either. The only other connection Vocalm makes is to
`127.0.0.1:48650`, to see whether ClipTrove is running.

**Deleting it:** quit Vocalm and delete the `%LOCALAPPDATA%\Vocalm` folder. Export your loud moments first from
Settings if you want to keep them.
