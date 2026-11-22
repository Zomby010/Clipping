# Background music (BGM)

Music for Clipping Studio's automatic background music and Clip Editor's **Music and SFX** tab. Both read MP3 files straight from these folders, because downloading music automatically often fails (rate limits, sites changing).

## How to use it

1. Find a royalty-free MP3 that you are allowed to use in monetised videos.
2. Put it in the folder for its mood:
   - `chill/` relaxed, lo-fi
   - `epic/` cinematic, action
   - `sad/` sad, emotional
   - `upbeat/` happy, energetic
   - `suspense/` mystery, tension
3. You can put several tracks in one folder.
   - **Clipping Studio** picks one at random for each render. If a mood folder is empty it uses `chill/`; if that is empty too, it adds no music (the same as `--no-bgm`).
   - **Clip Editor** lets you pick the exact track, and has **Add your own…** to copy a file here.
4. Write where each track came from in Clip Editor's **Licence note** box. Notes are saved in `assets/bgm/licences.json` (kept on your computer only), and the note is added to the YouTube description when you export, so the music is credited.

## Free places to find music

1. **Pixabay Music**
   - Chill: https://pixabay.com/music/search/mood/chill/
   - Epic: https://pixabay.com/music/search/mood/epic/
   - Upbeat: https://pixabay.com/music/search/mood/upbeat/
2. **YouTube Audio Library**
   - Open YouTube Studio and go to **Audio Library**.
   - Filter by mood (Calm, Dramatic, Happy and so on).
   - Pick tracks marked "No attribution required" to be safest.
3. **Incompetech** (music by Kevin MacLeod)
   - https://incompetech.com/music/royalty-free/music.html
   - These usually need a credit in the description. Put it in the licence note.

## Example folder layout

```text
assets/
└── bgm/
    ├── chill/
    │   ├── lofi-study-112191.mp3
    │   └── calm-acoustic-2415.mp3
    ├── epic/
    │   └── trailer-music-325357.mp3
    ├── sad/
    ├── suspense/
    └── upbeat/
        └── happy-pop-307007.mp3
```
