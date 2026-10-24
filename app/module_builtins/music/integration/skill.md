MemuMusic is the user's own local music library (downloaded and imported tracks), not a streaming service.
- "включи / поставь / play <song or artist>" → music.play {query}. The query is matched against the library; if the
  answer says it is not there, offer music.download with a link (YouTube, YouTube Music, SoundCloud, Spotify, Яндекс Музыка, Deezer, Apple Music).
- "включи плейлист X" → music.playlist {name}; without a name it lists the playlists.
- pause / resume / next / previous / volume → music.pause, music.resume, music.next, music.prev, music.volume {level 0-100}.
- "что сейчас играет?" → music.now.
Music plays in the MemuAI window (or the MemuMusic window when it is open), also when MemuMusic is closed.
