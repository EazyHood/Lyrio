# Publicar una versión de Lyrio

El actualizador consume únicamente versiones estables publicadas en `EazyHood/Lyrio`. Un borrador o una preversión no se descarga automáticamente.

1. Actualiza `APP_VERSION` en `appconfig.py` y `AppVersion` en `installer.iss` con el mismo número `X.Y.Z`.
2. Con Python 3.12 en Windows x64, instala `requirements-build.txt` y ejecuta `python -m unittest discover -s tests -v` y `python -m pip check`.
3. Revisa y fusiona el código. Crea y sube un tag `vX.Y.Z` sobre ese commit.
4. El workflow `build` verifica que el tag coincide, ejecuta las pruebas, compila ambas ediciones y comprueba cada ejecutable sin abrir ventanas ni capturar audio. Crea un **borrador** de release con `Lyrio.exe`, `Lyrio-Lite.exe` y `SHA256SUMS.txt`. También guarda los tres archivos como artefacto de Actions.
5. Descarga y prueba ambas ediciones. Revisa las notas y **publica el borrador** cuando estén listas. La publicación sigue siendo una acción tuya; el workflow no la realiza.

El manifiesto contiene una línea SHA-256 por ejecutable, con dos espacios antes del nombre. Lo genera el workflow después de compilar. Conserva los nombres exactos de los tres archivos. No reemplaces archivos de una release ya publicada: utiliza un nuevo número y tag; el workflow lo exige.

Para comprobar una build sin publicar, ejecuta el workflow manualmente sobre una rama; al no ser un tag, solo genera el artefacto de Actions.

La comprobación offline del paquete también se puede ejecutar localmente con `Lyrio.exe --self-test resultado.json` (o `Lyrio-Lite.exe`). Escribe un informe de dependencias, diccionarios, canal y versión; no inicia la interfaz ni descarga modelos.

## Comportamiento del actualizador

- Busca al iniciar y cada seis horas. La descarga en segundo plano está activada por defecto y se puede desactivar en Ajustes.
- Conserva la edición Full/Lite mediante un identificador integrado durante la compilación, incluso con un nombre de ejecutable distinto.
- Verifica origen HTTPS de GitHub, versión, tamaño, formato del ejecutable y SHA-256 antes de preparar la instalación. Esto comprueba integridad; no equivale a una firma Authenticode.
- Aplica la descarga al elegir **Salir** en la bandeja, o **Reiniciar y actualizar** en Ajustes. El cierre de ventana a la bandeja no instala. Una descarga manual también se aplica al salir.
- Espera a que termine el proceso, reemplaza el archivo de forma atómica y conserva el anterior como `<ejecutable>.lyrio-previous`. Al reiniciar desde Ajustes abre la nueva versión; al salir normalmente la deja cerrada.
- Los ajustes y letras quedan en AppData. Un error de red, checksum o permisos conserva la versión actual. Una carpeta sin permisos de escritura requiere mover la app o actualizarla manualmente.
- Las versiones hasta 1.1 necesitan reemplazarse manualmente una vez para incorporar este actualizador.

Si falla una instalación, revisa `%LOCALAPPDATA%\Lyrio\updates\<id>\install-result.json` y `%APPDATA%\Lyrio\lyrio.log`. Para recuperar una versión anterior, cierra Lyrio y restaura el archivo `.lyrio-previous` con extensión `.exe`.

## Comprobar las letras

La edición Full conserva el inicio y fin de cada palabra de Whisper y los intervalos enhanced LRC de las fuentes. Las letras con tiempos solo por línea muestran una barra; no se inventan tiempos de palabra. La corrección de LRC atrasado/adelantado está limitada a ±4 segundos y exige frases distintivas o evidencia concordante entre líneas vecinas. Los ajustes manuales por canción tienen prioridad.

La alineación necesita grabar y procesar audio antes de mejorar la caché. No es reconocimiento fonético instantáneo: un alargamiento dentro de una palabra permanece dentro de su intervalo, pero no identifica exactamente la vocal sostenida. Para evaluar calidad real, prueba la misma grabación con notas largas, silencios, pausas y saltos, además de los casos automáticos del repositorio.

En japonés, comprueba un verso con kana seguido de uno solo con kanji. Ambos deben aparecer en romaji en todas las vistas. Para canciones exclusivamente en kanji, selecciona explícitamente Japonés. Las lecturas poéticas o ambiguas siguen dependiendo del diccionario; la edición o publicación de sincronizaciones conserva el texto original.
