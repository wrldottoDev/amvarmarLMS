# Cómo es el proceso de despacho de principio a fin

<!-- palabras_clave: proceso de despacho, paso a paso, como despacho, cuando puedo despachar, lista para despachar, que sigue, despacho completo, sacar mi carga, retirar, entrega, bill of lading, cancelar solicitud -->

1. **La carga queda disponible.** Ocurre cuando AMVARMAR la almacena en Miami y pasa a `STORED`: aparece
   en **Miami → Inventario en Miami**. Los reportes de tránsito no se despachan desde el sistema. Los
   documentos pendientes no cambian esa disponibilidad.
2. **Revisá los documentos sugeridos.** En el detalle de la carga, el panel de documentos muestra qué
   archivos ayudarían a procesarla y si te toca a vos subirlos. Todos son opcionales.
3. **Pedí el despacho.** En **Miami → Inventario en Miami** marcá las cargas y tocá **Solicitar despacho**
   (o andá a **Despachos → Solicitar despacho**). Elegí el método (marítimo, aéreo o terrestre) y, si querés, una dirección de entrega e
   instrucciones. También me lo podés pedir a mí: decime qué cargas (por número, factura o ID) y por qué
   vía, y te preparo la solicitud para que la revises y la confirmes.
4. **Operaciones la revisa.** La solicitud queda `PENDING` y te llega el acuse. Los documentos pendientes
   no impiden aprobarla ni rechazarla. Mientras siga `PENDING` podés cancelarla desde el detalle del
   despacho con **Cancelar solicitud**; después de aprobada ya no.
5. **Aprobada y en preparación.** Te avisamos cuando se aprueba (`APPROVED`) y Operaciones alista las
   cargas (`PREPARING`).
6. **Sale de bodega.** Cuando las cargas salen, el despacho pasa a `DISPATCHED` y te avisamos. El Bill of
   Lading (BL) lo sube AMVARMAR al despacho; cuando está disponible te llega un aviso y lo descargás desde
   el detalle del despacho.
7. **Entrega.** Cada carga pasa a `DELIVERED` cuando se registra su entrega. La prueba de entrega es
   recomendada, pero no bloquea el cambio. Es un paso aparte del cierre del despacho (`COMPLETED`).
