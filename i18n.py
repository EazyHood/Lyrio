# -*- coding: utf-8 -*-
"""Textos de Lyrio. Ingles por defecto, espanol disponible."""

STRINGS = {
    "en": {
        # generales / estado
        "waiting_spotify": "Waiting for Spotify…",
        "connected": "Listening to Spotify",
        "searching": "Searching for lyrics…",
        "no_lyrics": "No lyrics found",
        "paused": "Paused",
        "ad_playing": "Ad break",
        "source_lrclib": "Synced lyrics · lrclib",
        "source_netease": "Synced lyrics · NetEase",
        "source_user": "Synced by you",
        "source_auto": "Auto-synced (approximate)",
        "estimated_notice": "No official timing — auto-synced. Fine-tune it below.",

        # ventana principal
        "nothing_playing": "Nothing playing",
        "open_spotify_hint": "Play something on Spotify and it will show up here.",
        "click_to_jump": "Click any line to jump there",
        "offset": "Lyrics timing",
        "offset_hint": "positive = earlier, negative = later",
        "reset": "Reset",
        "overlay_switch": "Floating lyrics",
        "ghost_switch": "Click-through",
        "ghost_hint": "Clicks pass through the floating lyrics. Use the green dot to grab them again.",
        "re_search": "Search again",
        "wrong_lyrics": "Wrong lyrics?",
        "sync_editor": "Sync by hand",
        "settings": "Settings",
        "back": "Back",

        # ajustes
        "language": "Language",
        "theme": "Theme",
        "theme_light": "Light",
        "theme_dark": "Dark",
        "theme_system": "System",
        "overlay_style": "Floating lyrics style",
        "font_size": "Text size",
        "text_color": "Text color",
        "show_next_line": "Show upcoming line",
        "reset_position": "Re-center on screen",
        "autostart": "Start with Windows",
        "about": "About",
        "about_text": ("Lyrio shows the lyrics of whatever is playing on Spotify, "
                       "floating over your screen. It finds synced lyrics online and "
                       "auto-syncs the ones that have no timing.\n\n"
                       "Sources: lrclib · Musixmatch · QQ Music · Kugou · NetEase · letras.com · Genius · lyrics.ovh"),
        "color_white": "White",
        "color_green": "Green",
        "color_yellow": "Yellow",
        "color_cyan": "Cyan",
        "color_pink": "Pink",

        # buscador manual
        "search_title": "Find the right lyrics",
        "search_help": "Fix the artist or title and search again. The result will be remembered for this song.",
        "artist": "Artist",
        "title": "Title",
        "search_btn": "Search",
        "cancel": "Cancel",
        "not_found_retry": "Nothing found with those terms.",

        # editor de sincronizacion
        "sync_title": "Sync by hand",
        "sync_help": ("Play the song. When the highlighted line actually starts, "
                      "press SPACE: that line locks in and everything after it "
                      "shifts along. Click any line to select it; fine-tune with "
                      "−¼s / +¼s. You only fix the lines that feel off."),
        "tap": "Starts now  (Space)",
        "undo_tap": "Undo",
        "save": "Save",
        "sync_saved": "Saved. Using your timing now.",
        "line_of": "Line {a} of {b}",

        # overlay v4
        "overlay_width": "Width on screen",
        "overlay_lines": "Visible lines",
        "lines_1": "Current",
        "lines_2": "+ Next",
        "lines_3": "+ Previous",
        "overlay_align": "Alignment",
        "align_left": "Left",
        "align_center": "Center",
        "align_right": "Right",
        "overlay_bg": "Dark card behind text",
        "overlay_progress": "Line progress bar",
        "overlay_first_hint": "Drag me wherever you like — right-click for options",

        # fuente / reproductor
        "source_mode": "Music source",
        "source_spotify": "Spotify only",
        "source_any": "Any player",
        "listening_to": "Listening to {app}",

        # sugerencias (tooltips)
        "tip_overlay": "Show or hide the floating lyrics",
        "tip_ghost": "Clicks pass through the lyrics; a green dot lets you grab them back",
        "tip_offset_minus": "Show lyrics a bit later (−0.25 s)",
        "tip_offset_plus": "Show lyrics a bit earlier (+0.25 s)",
        "tip_offset_reset": "Clear this song's timing adjustment",
        "tip_research": "Search all sources again, ignoring the saved copy",
        "tip_wrong": "Fix the artist or title and search with those terms",
        "tip_sync": "Tap along with the song to set each line's timing",
        "tip_progress": "Click anywhere to jump there",
        "now_button": "Now",

        # comunidad / creditos / calibracion
        "sync_author_label": "Your sync signature (shown as credit)",
        "publish_btn": "Share on lrclib",
        "publish_ok": "Published on lrclib — thank you for contributing!",
        "publish_fail": "Could not publish ({err})",
        "publishing": "Publishing…",
        "synced_by": "♪ Synced by {name}",
        "calibrated_notice": "Re-anchored with the actual audio ♪",
        "cache_label": "Local cache: {n} songs · {mb} MB (auto-pruned)",
        "clear_cache_btn": "Clear cache",
        "hotkeys_label": "Global shortcuts (Ctrl+Alt+L lyrics · ←→ timing · ↑↓ size)",
        "overlay_font": "Overlay font",

        # temas / barrido / multi-fuente
        "overlay_theme": "Style theme",
        "theme_classic": "Classic",
        "theme_cartoon": "Cartoon",
        "theme_neon": "Neon",
        "theme_minimal": "Minimal",
        "sweep_label": "Karaoke sweep",
        "sweep_word": "By word",
        "sweep_char": "By letter",
        "both_lyrics": "2 lyrics at once",
        "sources_title": "Playing now — pick the source:",
        "identify_btn": "Identify song ♪",
        "identifying": "Listening to identify the song…",
        "identified": "Identified: {a} — {b}",
        "identify_fail": "Could not identify the song",
        "tip_identify": ("Recognizes the ACTUAL song playing (Shazam-style). "
                         "Perfect when a video's title isn't the song name."),
        "ai_synced": "AI synced this song by listening to it ♪",
        "source_ai": "AI-synced (listened & aligned)",

        # v6: traduccion, fiesta, movil, updates
        "translate_label": "Live translation (local AI, Ollama)",
        "romanize_label": "Romanize Japanese / Korean / Chinese lyrics",
        "party_mode": "Party mode (fullscreen lyrics)",
        "party_exit_hint": "Esc or click to exit",
        "phone_label": "Phone companion (same Wi-Fi)",
        "phone_qr_btn": "Show QR",
        "phone_qr_title": "Open on your phone",
        "phone_qr_hint": "Scan with your phone camera (same Wi-Fi network).",
        "update_available": "New version v{v} available on GitHub!",
        "ai_model_label": "AI sync model",
        "lbl_extras": "Extras",

        # extras
        "still_running": "Lyrio keeps running in the tray. Right-click the icon to quit.",
        "no_lyrics_hint": "Try \"Wrong lyrics?\" or \"Search again\" on the left.",
        "global_offset": "Global offset (Bluetooth delay)",
        "sync_track_changed": "The song changed — taps are disabled.",
        "sync_paused_hint": "Play the song to tap.",

        # bandeja / overlay menu
        "open_app": "Open Lyrio",
        "hide_lyrics": "Hide floating lyrics",
        "show_lyrics": "Show floating lyrics",
        "quit": "Quit",
        "forward_half": "Lyrics earlier by 0.5 s",
        "backward_half": "Lyrics later by 0.5 s",
        "reset_offset": "Reset timing",
        "bigger": "Bigger text",
        "smaller": "Smaller text",
        "color_menu": "Color",
        "hide_next": "Hide upcoming line",
        "show_next": "Show upcoming line",
        "ghost_on": "Click-through mode",
        "ghost_off": "Disable click-through",
        "reload": "Reload lyrics",
    },
    "es": {
        "waiting_spotify": "Esperando a Spotify…",
        "connected": "Escuchando Spotify",
        "searching": "Buscando la letra…",
        "no_lyrics": "No se encontró la letra",
        "paused": "En pausa",
        "ad_playing": "Anuncio",
        "source_lrclib": "Letra sincronizada · lrclib",
        "source_netease": "Letra sincronizada · NetEase",
        "source_user": "Sincronizada por ti",
        "source_auto": "Sincronización automática (aproximada)",
        "estimated_notice": "Sin tiempos oficiales — se sincronizó sola. Afínala abajo.",

        "nothing_playing": "Nada sonando",
        "open_spotify_hint": "Pon algo en Spotify y aparecerá aquí.",
        "click_to_jump": "Haz clic en una línea para saltar ahí",
        "offset": "Ajuste de tiempo",
        "offset_hint": "positivo = antes, negativo = después",
        "reset": "Reiniciar",
        "overlay_switch": "Letra flotante",
        "ghost_switch": "Clic atraviesa",
        "ghost_hint": "Los clics atraviesan la letra flotante. Usa el punto verde para agarrarla de nuevo.",
        "re_search": "Buscar de nuevo",
        "wrong_lyrics": "¿Letra equivocada?",
        "sync_editor": "Sincronizar a mano",
        "settings": "Ajustes",
        "back": "Volver",

        "language": "Idioma",
        "theme": "Tema",
        "theme_light": "Claro",
        "theme_dark": "Oscuro",
        "theme_system": "Sistema",
        "overlay_style": "Estilo de la letra flotante",
        "font_size": "Tamaño del texto",
        "text_color": "Color del texto",
        "show_next_line": "Mostrar línea siguiente",
        "reset_position": "Centrar en pantalla",
        "autostart": "Iniciar con Windows",
        "about": "Acerca de",
        "about_text": ("Lyrio muestra la letra de lo que suena en Spotify, "
                       "flotando sobre tu pantalla. Busca letras sincronizadas en "
                       "internet y sincroniza sola las que no traen tiempos.\n\n"
                       "Fuentes: lrclib · Musixmatch · QQ Music · Kugou · NetEase · letras.com · Genius · lyrics.ovh"),
        "color_white": "Blanco",
        "color_green": "Verde",
        "color_yellow": "Amarillo",
        "color_cyan": "Cian",
        "color_pink": "Rosa",

        "search_title": "Encontrar la letra correcta",
        "search_help": "Corrige el artista o el título y busca de nuevo. El resultado se recordará para esta canción.",
        "artist": "Artista",
        "title": "Título",
        "search_btn": "Buscar",
        "cancel": "Cancelar",
        "not_found_retry": "No se encontró nada con esos términos.",

        "sync_title": "Sincronizar a mano",
        "sync_help": ("Reproduce la canción. Cuando la línea resaltada empiece de "
                      "verdad, presiona ESPACIO: esa línea queda fijada y todas las "
                      "siguientes se corren con ella. Haz clic en una línea para "
                      "seleccionarla; afina con −¼s / +¼s. Solo corriges las líneas "
                      "que se sientan mal."),
        "tap": "Empieza ahora  (Espacio)",
        "undo_tap": "Deshacer",
        "save": "Guardar",
        "sync_saved": "Guardado. Usando tu sincronización.",
        "line_of": "Línea {a} de {b}",

        "overlay_width": "Ancho en pantalla",
        "overlay_lines": "Líneas visibles",
        "lines_1": "Actual",
        "lines_2": "+ Siguiente",
        "lines_3": "+ Anterior",
        "overlay_align": "Alineación",
        "align_left": "Izquierda",
        "align_center": "Centro",
        "align_right": "Derecha",
        "overlay_bg": "Tarjeta oscura tras el texto",
        "overlay_progress": "Barra de progreso de línea",
        "overlay_first_hint": "Arrástrame a donde quieras — clic derecho para opciones",

        "source_mode": "Fuente de música",
        "source_spotify": "Solo Spotify",
        "source_any": "Cualquier reproductor",
        "listening_to": "Escuchando {app}",

        "tip_overlay": "Mostrar u ocultar la letra flotante",
        "tip_ghost": "Los clics atraviesan la letra; un punto verde te deja recuperarla",
        "tip_offset_minus": "Mostrar la letra un poco después (−0.25 s)",
        "tip_offset_plus": "Mostrar la letra un poco antes (+0.25 s)",
        "tip_offset_reset": "Borrar el ajuste de tiempo de esta canción",
        "tip_research": "Buscar de nuevo en todas las fuentes, ignorando lo guardado",
        "tip_wrong": "Corrige el artista o el título y busca con esos términos",
        "tip_sync": "Marca el ritmo de la canción para fijar el tiempo de cada línea",
        "tip_progress": "Haz clic en cualquier punto para saltar ahí",
        "now_button": "Ahora",

        "sync_author_label": "Tu firma de sincronización (aparece como crédito)",
        "publish_btn": "Compartir en lrclib",
        "publish_ok": "Publicada en lrclib — ¡gracias por aportar!",
        "publish_fail": "No se pudo publicar ({err})",
        "publishing": "Publicando…",
        "synced_by": "♪ Sincronizada por {name}",
        "calibrated_notice": "Re-anclada con el audio real ♪",
        "cache_label": "Caché local: {n} canciones · {mb} MB (se poda sola)",
        "clear_cache_btn": "Vaciar caché",
        "hotkeys_label": "Atajos globales (Ctrl+Alt+L letra · ←→ tiempo · ↑↓ tamaño)",
        "overlay_font": "Fuente de la letra flotante",

        "overlay_theme": "Tema de estilo",
        "theme_classic": "Clásico",
        "theme_cartoon": "Cartoon",
        "theme_neon": "Neón",
        "theme_minimal": "Mínimo",
        "sweep_label": "Barrido karaoke",
        "sweep_word": "Por palabra",
        "sweep_char": "Por letra",
        "both_lyrics": "2 letras a la vez",
        "sources_title": "Sonando ahora — elige la fuente:",
        "identify_btn": "Identificar canción ♪",
        "identifying": "Escuchando para identificar la canción…",
        "identified": "Identificada: {a} — {b}",
        "identify_fail": "No se pudo identificar la canción",
        "tip_identify": ("Reconoce la canción REAL que suena (estilo Shazam). "
                         "Perfecto cuando el título del video no es el nombre "
                         "de la canción."),
        "ai_synced": "La IA sincronizó esta canción escuchándola ♪",
        "source_ai": "Sincronizada por IA (escuchada y alineada)",

        "translate_label": "Traducción en vivo (IA local, Ollama)",
        "romanize_label": "Romanizar letras en japonés / coreano / chino",
        "party_mode": "Modo fiesta (letra a pantalla completa)",
        "party_exit_hint": "Esc o clic para salir",
        "phone_label": "Compañero móvil (misma Wi-Fi)",
        "phone_qr_btn": "Mostrar QR",
        "phone_qr_title": "Ábrelo en tu teléfono",
        "phone_qr_hint": "Escanéalo con la cámara (misma red Wi-Fi).",
        "update_available": "¡Nueva versión v{v} disponible en GitHub!",
        "ai_model_label": "Modelo de la IA de sincronía",
        "lbl_extras": "Extras",

        "still_running": "Lyrio sigue en la bandeja. Clic derecho en el icono para salir.",
        "no_lyrics_hint": "Prueba \"¿Letra equivocada?\" o \"Buscar de nuevo\" a la izquierda.",
        "global_offset": "Ajuste global (retardo Bluetooth)",
        "sync_track_changed": "La canción cambió — los taps se desactivaron.",
        "sync_paused_hint": "Reproduce la canción para marcar.",

        "open_app": "Abrir Lyrio",
        "hide_lyrics": "Ocultar letra flotante",
        "show_lyrics": "Mostrar letra flotante",
        "quit": "Salir",
        "forward_half": "Letra 0.5 s antes",
        "backward_half": "Letra 0.5 s después",
        "reset_offset": "Reiniciar ajuste",
        "bigger": "Texto más grande",
        "smaller": "Texto más chico",
        "color_menu": "Color",
        "hide_next": "Ocultar línea siguiente",
        "show_next": "Mostrar línea siguiente",
        "ghost_on": "Modo clic-atraviesa",
        "ghost_off": "Desactivar clic-atraviesa",
        "reload": "Recargar letra",
    },
}

_lang = "en"


def set_language(lang):
    global _lang
    _lang = lang if lang in STRINGS else "en"


def t(key, **kw):
    s = STRINGS.get(_lang, STRINGS["en"]).get(key) or STRINGS["en"].get(key, key)
    return s.format(**kw) if kw else s
