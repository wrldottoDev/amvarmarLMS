# Cómo es el proceso de despacho de principio a fin

<!-- palabras_clave: proceso de despacho, paso a paso, como despacho, cuando puedo despachar, lista para despachar, que sigue, despacho completo, sacar mi carga, retirar, entrega, bill of lading, cancelar solicitud -->

1. **La carga llega y se almacena.** Cuando AMVARMAR la recibe y la almacena, pasa a `STORED`. Si no le
   falta nada, te llega el aviso **"Su carga está lista para despachar"**. Si le falta algo (por ejemplo la
   factura comercial o el packing list), primero te llega "Su carga está en bodega" y, cuando se resuelve
   lo último que faltaba, el aviso de que ya está lista.
2. **Revisá qué falta.** En el detalle de la carga, el panel de documentos muestra cada requisito y si te
   toca a vos subirlo. También podés preguntarme "¿qué le falta a mi carga?".
3. **Pedí el despacho.** En **Despachos → Solicitar despacho** elegí una o más cargas almacenadas de tu
   empresa, el método (marítimo, aéreo o terrestre) y, si querés, una dirección de entrega e
   instrucciones. También me lo podés pedir a mí: decime qué cargas (por número, factura o ID) y por qué
   vía, y te preparo la solicitud para que la revises y la confirmes.
4. **Operaciones la revisa.** La solicitud queda `PENDING` y te llega el acuse. Si algún requisito
   bloqueante sigue pendiente, Operaciones espera a que se resuelva antes de aprobar. Mientras siga
   `PENDING` podés cancelarla desde el detalle del despacho con **Cancelar solicitud**; después de aprobada
   ya no.
5. **Aprobada y en preparación.** Te avisamos cuando se aprueba (`APPROVED`) y Operaciones alista las
   cargas (`PREPARING`).
6. **Sale de bodega.** Cuando las cargas salen, el despacho pasa a `DISPATCHED` y te avisamos. El Bill of
   Lading (BL) lo sube AMVARMAR al despacho; cuando está disponible te llega un aviso y lo descargás desde
   el detalle del despacho.
7. **Entrega.** Cada carga pasa a `DELIVERED` cuando se registra su entrega, con su prueba de entrega. Es
   un paso aparte del cierre del despacho (`COMPLETED`).
