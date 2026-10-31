MemuMusic is the user's own local music library (downloaded and imported tracks), not a streaming service.
- "включи / поставь / play <song or artist>" → music.play {query}. The query is matched against the library; if the
  answer says it is not there, offer music.download with a link (YouTube, YouTube Music, SoundCloud, Spotify, Яндекс Музыка, Deezer, Apple Music).
- "включи плейлист X" → music.playlist {name}; without a name it lists the playlists.
- pause / resume / next / previous / volume → music.pause, music.resume, music.next, music.prev, music.volume {level 0-100}.
- "что сейчас играет?" → music.now.
Music plays in the MemuAI window (or the MemuMusic window when it is open), also when MemuMusic is closed.
- "найди / search <song or artist>" → music.search {query, limit?}. Shows matches; it does not play anything.
- "радио / radio <song>" → music.radio {query? | track_id?}; no track — radio from the current one. It plays similar tracks from the library.
- like / unlike → music.like {track_id? | query?, liked?}; no track — the current one.
- "перемотай на N секунд" → music.seek {position}; shuffle → music.shuffle {enabled?} (no value — toggle); repeat → music.repeat {mode: off|all|one}.
- queue → music.queue {action: show|add|remove|move, track_id? | query?, position?}. No action: with a track — add, without — show. Several matches for a name list candidates first, nothing changes. Edits keep the current track playing.
- "удали трек X" → music.delete_track {track_id} only. A name alone only lists candidates with ids, never deletes. delete_file=false by default: only the library entry goes; the file is removed only with delete_file=true, and only from MemuMusic's own folders.
- rename a track → music.rename_track {track_id, title? | artist? | album?}, exact id only, at least one field. Never guess an id from a name: resolve it with music.search first. Renaming an artist for all tracks (a merge) is not offered — use the MemuMusic window.
- playlists → music.playlist_create {name, description?, query?}; music.playlist_delete {playlist_id | name} (exact name only, otherwise candidates); music.playlist_add / music.playlist_remove {playlist_id? | name?, track_id? | query?}; music.playlist_reorder {playlist_id? | name?, track_id? | query?, position?} moves one track (position counts from 1). A full-list reorder is not offered.
- settings → music.settings {key, value?}. Without a value it reads. Only the whitelisted keys: volume, normalize, normalize_target, crossfade, gapless, auto_radio, auto_lyrics, lyrics_sync, auto_analyze, resume_long, seek_step, play_click, show_genres, show_folders, show_history, artist_photos. Never download_dir, cookies_from_browser, hotkeys or any path. Volume may be 0–1 or 0–100.
- toggle / stop → music.toggle, music.stop. Duplicates → music.duplicates (metadata groups only, no file hashing). Statistics → music.stats (tracks/artists counts).
- There is no command to add a local file by path: add tracks by link with music.download, or import files in the MemuMusic window. Never invent track ids, playlist ids or file paths.

Model selector: the AI settings model pickers (lyricsModel, recModel) use the shared MemuModelSelector (chips preset via renderList, app/pgstatic/model-selector.js); provider names come from the catalogue via providerName(), never a raw "custom" provider. The legacy details/summary picker in feat-ai.js is only the fallback when the host did not inline the component.
