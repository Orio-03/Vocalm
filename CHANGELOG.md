# Changelog

## Unreleased

- **Games, with ClipTrove.** A new page shows, for each game, how far over your line you go, how often, and whether
  you're getting calmer (the last two weeks against the four before, with a projection when the trend is steady).
  It also lists your ClipTrove clips, each marked calm or with how many dB over your line you were in the 90 seconds
  before you saved it. The game comes from the app in front, or from your clips. An app that's always in front when
  you clip a game is learned to be that game (`cs2` is *Counter-Strike 2*). Loud moments in Discord, or from the
  old script, which never noted apps, count for the game you were clipping at the time. Vocalm copies only each
  clip's game and time, and keeps them even if ClipTrove is closed or the clip is deleted.
- **Fixed:** a damaged history file made Insights say *Couldn't load your history* for good. Vocalm now checks
  the file as it starts and, if it's damaged, rebuilds it from everything still readable. The damaged file is
  kept beside it as `vocalm.damaged-<time>.db`.
- **Runs where Smart App Control is on.** Smart App Control blocks unsigned programs, Vocalm.exe included, with no
  way to run them anyway. `python packaging/install_local.py --from-source` points Vocalm's shortcuts (desktop,
  Start menu, start with Windows) at the source run by your own Python, which is signed and allowed. The README,
  the website and the zip's *Read me* now explain this.

## [0.1.3] - 2026-10-15

- **A website:** [orio-03.github.io/Vocalm](https://orio-03.github.io/Vocalm/), with the bar filmed over a game, a
  tour of the dashboard, and a *Try it* section that runs the bar on your own voice right in the page (nothing is
  recorded or sent anywhere).
- **A new README**, with videos of the bar and the dashboard, a step-by-step start, and answers to common questions.
- **Fixed:** on the dashboard, the last red peak's label could sit on top of the big level readout.
- **Fixed:** the bars under *Where you talked today* and *Where you get loud* didn't all start at the same place.
- **Fixed:** the *Beside the bar* choice in Settings was cut off.
- **For developers:** `python -m vocalm --demo` runs Vocalm with a scripted voice and made-up history, beside your
  own copy, and `packaging/media/make_media.py` makes every screenshot and video from it, so none of them ever
  show anyone's real history.

## [0.1.2] - 2026-10-15

- **High-pitched shouts now count.** Loudness is measured the way ears hear it (A-weighted, like a dB(A) sound
  level meter) instead of as raw energy, which is dominated by the low end of a voice. A squeal or a shrill shout
  sounds much louder than its raw energy says, and now reads that way too. If you set your line up before this
  version, Vocalm measures both ways during your first minute or so of talking and then moves your line by the
  typical difference, so your normal voice stays exactly as far under it as before. Settings → Microphone shows how
  it's going and by how much the line moved.
- **The bar only speaks up when you've been too loud.** The needle and readout now show your last **red** peak
  (the loudest point of the last time you went over your line), not green or amber ones, and they disappear after a
  time you choose in Settings → The bar: 1, 5, 10 or 15 minutes, never, or any number of minutes.
- The readout's box now fits what's in it, instead of always being as wide as its longest possible text, and the
  number and its unit sit on one baseline.
- The bar's scale now reaches 15 dB past your line (it was 6), so the needle shows how far over you went instead of
  every red peak piling up at the end. It starts where talking starts rather than at the silence gate.
- Vocalm is on GitHub: [Orio-03/Vocalm](https://github.com/Orio-03/Vocalm), with tests run on every change and
  each version's Windows zip as a release.

## [0.1.1] - 2026-10-15

- **Put the bar anywhere**, the taskbar included. It only has to stay on a monitor. While it's over the taskbar it
  wins the front back within half a second whenever the taskbar is clicked.
- **Your last peak, on the bar.** A small needle marks how loud your last burst of talking got, and stays there
  until you speak again, so a quick glance tells you how close you came. Beside the bar, a readout shows that peak in
  dB, with a dot in its zone's colour, or your calm streak instead. Both can be turned off in Settings → The bar.
  The dashboard's meter shows the same mark.
- **A new look.** Sea-glass turquoise instead of purple, everywhere: the icon, the tray, the dashboard and the bar.
- **Better type.** Numbers, labels and headings are set in Bahnschrift (Windows' own DIN-style face), with units
  set smaller beside their numbers, a title on each page, and the meter's track tinted where amber and red begin.
- **Fixed:** keyboard clatter, breathing and room noise counted as talking. Talking now starts 18 dB under your
  normal voice (it used to start at the -60 dB silence gate), so talking time, typical level and your last peak are
  about your voice.

## [0.1.0] - 2026-10-15

The first release: the *Loud Mic* script (calm_mic.py) becomes an app.

- **A real Windows app.** `Vocalm.exe` with its own icon, a tray icon (red while you're loud, grey while paused)
  and a menu for pause, show/hide the bar, move the bar, start with Windows and quit. Opening it again while it's
  running just opens the dashboard.
- **A dashboard** at `http://127.0.0.1:48680`, only on your PC:
  - **Live:** your level as it happens with the last 30 seconds traced out, your calm streak, which app is in front,
    and today's talking time and loud moments.
  - **Insights:** this week against the last, loud moments per day, per hour of talking, when you get loud (day of
    the week by hour), where you get loud (by app or game), your loudest moments and every run. Each chart has a table
    view.
  - **Settings:** calibrate your voice in the browser, your line and how long counts as a loud moment (both shown
    against your live voice), the bar's size, opacity and corner, the breathe card, the microphone, start with
    Windows, and exporting your loud moments as CSV.
- **Talking time is now measured**, per minute and per app, so days and games can be compared fairly, per hour of
  talking. Only the program's name is kept, never window titles.
- **Nothing is lost on shutdown.** The run is saved every 30 seconds instead of only on Ctrl+Alt+Q, and talking time
  every 10 seconds.
- **History in one place:** `%LOCALAPPDATA%\Vocalm\vocalm.db`. Your old settings and logs are brought over on the
  first start.
- The bar now stays on the monitor it's on (and off the taskbar) even if your monitors change, recovers by itself if
  the microphone disappears (a headset unplugged), and a long sleep is never counted as talking.
- **Fixed:** the first line of the old sessions log couldn't be read because of a byte-order mark.
