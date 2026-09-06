# Pendiente (no cargado como respuesta de `como_hago`)

El cargador de `base_de_conocimiento.py` ignora los archivos que empiezan con `_`: esto es
documentación interna, no una entrada que el ejecutor pueda devolver.

Temas de "cómo hago X" que un usuario probablemente pregunte y que **no** se agregaron a la base porque
no se pudo verificar el procedimiento exacto contra la interfaz actual (Fase 3, ADR-0012):

- **Ver el historial/timeline de eventos de una carga.** `shipments/queries.py` tiene una función
  `timeline()` y el frontend tiene tipos para `PaginaTimeline`/`EventoCarga`, pero no se confirmó en esta
  fase desde qué pantalla ni con qué botón se llega a esa vista.
- **Editar una carga ya creada.** Existe la ruta `cargas/[id]/editar`, pero no se confirmó qué campos son
  editables una vez creada la carga, ni si un `CLIENT_ADMIN`/`CLIENT_USER` puede acceder a ella o es
  exclusiva de Operaciones.
- **Administrar usuarios de la propia empresa** (alta de un nuevo usuario `CLIENT_USER`, por ejemplo).
  Las reglas de negocio (`04-reglas-negocio-objetivo.md`, sección 1.1) dicen que `CLIENT_ADMIN` puede
  "gestionar usuarios de su empresa", pero no se revisó la pantalla real para describir el procedimiento.

Cuando alguien complete la verificación de estos flujos, agregar el `.md` correspondiente en este mismo
directorio (ver los archivos existentes como plantilla: título, comentario `palabras_clave`, cuerpo).
