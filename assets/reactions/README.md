# Reaction clips

Short clips (about 1 second) that get cut into a Short at a funny or shocking
moment: the clip pauses, the reaction plays with its own sound, and the clip
carries on from exactly where it stopped.

Put each clip in the folder for its feeling:

    laugh/    someone cracking up, a sitcom laugh, "HAHA"
    shock/    a gasp, a jaw drop, "no way"
    hype/     "let's go!", a fist pump, a crowd cheer
    sad/      a sigh, "bro...", a facepalm
    cringe/   a wince, "yikes", looking away

You can add any folder you like (for example `fail/` or `wow/`). The AI only
picks moods that have at least one clip, and picks a random clip from the
folder each time.

Tips

- .mp4, .mov, .webm, .mkv and .m4v all work. Any shape is fine: the clip is
  cropped to fill the Short.
- Keep them short. Anything past 1.2 seconds is cut (change it with
  `--reaction-seconds`).
- Use clips you filmed yourself or ones that are free to reuse, so they never
  cause a claim on your channel.
- At most 2 reactions go into one Short (`--max-reactions`). Turn them off
  with `--no-reactions`.
