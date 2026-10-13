# 01 — Investigación aduanera: impuestos de importación a Costa Rica

> Estado: investigación, 2026-10-12. Nada de esto está implementado.
>
> Cada dato lleva su estado:
> - **VERIFICADO**: se leyó en la fuente primaria u oficial citada.
> - **INFERIDO**: viene de fuentes secundarias o de fragmentos de búsqueda.
> - **NO COMPROBADO**: no se pudo confirmar.
>
> Ninguna tasa de este documento debe usarse para calcular sin verificarla de nuevo en la fuente vigente.

## Resumen

1. **En esta investigación no se localizó ninguna API oficial de aranceles ni de impuestos de importación de Costa Rica.**
   - La fuente legal es la consulta TICA de Hacienda, por inciso de 12 dígitos. Bloquea clientes automatizados y no ofrece descarga.
2. **La mejor fuente estructurada tiene el esquema completo, pero las tasas pueden estar desactualizadas.**
   - Es un XLSX que COMEX publicó en mayo de 2022, exportado de TICA, con 17.319 incisos.
   - Trae todas las columnas de impuestos, los TLC y las notas técnicas.
   - Desde entonces cambiaron tratados, enmiendas y resoluciones.
3. **El tipo de cambio sí es automatizable con fuentes oficiales.**
   - Hay una API pública de Hacienda, sin token, y una API del BCCR, con token.
4. **Las fórmulas están documentadas oficialmente** en el instructivo de la DGA de 2023, con ejemplos que se pueden recalcular.
5. **Hay huecos que impiden un cálculo exacto en algunos casos:** seguro presunto, régimen courier vigente, vehículos, alcohol, tabaco y combustibles. El sistema tiene que declararlos, no ocultarlos.

---

## 1. Fuentes de datos

### 1.1 TICA — Consulta de Aranceles (Hacienda / Dirección General de Aduanas)

| Campo | Detalle |
|---|---|
| URL | https://ticaconsultas.hacienda.go.cr/Tica/hdbaranc.aspx. Espejo: https://portaltica.hacienda.go.cr/TicaExterno/hdbaranc.aspx. Histórica: https://www.hacienda.go.cr/tica/web/hdbaranc.aspx |
| Organismo | Ministerio de Hacienda, DGA |
| Información | Arancel Nacional oficial por inciso de 12 dígitos: DAI, impuestos internos (ISC, Ley 6946, específicos), tasas por tratado y documentos obligatorios o notas técnicas. La RES-DGA-119-2021 (7-abr-2021) oficializa su publicación en TICA. |
| Actualización | Continua (es el sistema operativo de la aduana) |
| Formato | Formularios ASP.NET. Sin exportación, descarga ni API (no se encontró ninguna) |
| Restricciones | Responde **HTTP 400** con página de Akamai Bot Manager ("su solicitud está en revisión") a curl y a clientes automatizados (**VERIFICADO**). `portaltica` respondió 503. Algunas consultas TICA piden CAPTCHA (**INFERIDO**). No se pudieron leer robots.txt ni términos de uso (también responden 400). |
| Automatización | Técnicamente muy difícil: anti-bot y CAPTCHA. **No recomendada**; sortear un anti-bot de un sitio gubernamental es un riesgo legal y reputacional. |
| Confiabilidad | Máxima: es la fuente legal |
| Estrategia | Verificación **manual** por un especialista, inciso por inciso, guardando fecha y captura. Pedir formalmente a la DGA (Técnica Aduanera) una exportación periódica del arancel: el XLSX de COMEX (1.2) muestra que ese archivo existe. |
| Estado | Bloqueo: **VERIFICADO**. Contenido de la página: **INFERIDO**. `hcimppon.aspx` ("impuestos por posición"): **NO COMPROBADO** (400). |

**Sistemas nuevos:**
- **ATENA** (Hacienda Digital) reemplazará TICA. La primera salida a producción, solo para exportaciones, está prevista para el 1T-2027 (Diario Extra, oct-2026; **INFERIDO**). Que las importaciones sigan en TICA durante 2027 es una **inferencia** a partir de eso, **a verificar** con una fuente oficial.
- **TRIBU-CR** reemplaza a ATV en tributación interna, no en aduanas (**INFERIDO**).

### 1.2 COMEX — XLSX "Arancel Actualizado 11 Mayo 2022" (exportación de TICA)

| Campo | Detalle |
|---|---|
| URL | https://portaldecomercio.comex.go.cr/media/ar1iswz4/arancel-actualizado-11-mayo-2022.xlsx (enlazado desde https://portaldecomercio.comex.go.cr/enlaces/derechos-y-cargas/derechos-y-cargas-aduaneros/) |
| Organismo | Ministerio de Comercio Exterior (COMEX) |
| Información | Hoja "Arancel 2022" con 17.319 filas e incisos de 12 dígitos (p. ej. `010121000000`). Columnas: DAI, IVA, ISC, L6946, IFAM, INDER, Ganancia Estimada, Golfito, Banano 5515/7147, carne bovina, forestal, arroz, ganado, tabaco. Impuestos específicos (hidrocarburos, bebidas sin alcohol, IFAM, INDER, bebidas alcohólicas) con fecha de inicio y fin. Tasas preferenciales por TLC: CA, Panamá, RD, México, Chile, Canadá, CARICOM, CAFTA, CAFTA-RD, China, Perú, Singapur, AACUE, Andorra, AELC, Colombia, Corea, AACRU. Documentos obligatorios y notas técnicas (**VERIFICADO**: se abrió el archivo). |
| Actualización | 11-may-2022. **No incluye** los TLC con Ecuador (vigente desde 1-oct-2024) ni con Emiratos Árabes Unidos (desde 1-abr-2025). |
| Formato | XLSX |
| Restricciones | curl recibió un desafío de Cloudflare (403); WebFetch sí lo descargó. Los términos de COMEX (https://www.comex.go.cr/media/5693/condiciones.pdf, 2014) presentan la información "tal cual" y prohíben reutilizar logos; no prohíben reutilizar datos. |
| Automatización | Carga única manual (se descarga a mano y se importa) |
| Confiabilidad | Alta como **esquema** y como estructura de incisos; **baja como tasas vigentes**, por tener más de 4 años |
| Estrategia | Plantilla del modelo de datos y carga inicial de incisos y descripciones. Nada se publica para calcular hasta que un especialista confirme en TICA el perfil completo del inciso (02 §4). |

### 1.3 SIECA — Arancel Centroamericano de Importación (SAC)

| Campo | Detalle |
|---|---|
| URL | https://www.sieca.int/producto/arancel-centroamericano-de-importacion/. El PDF se descarga con un formulario que redirige a S3: https://recepcionwebsieca.s3.ca-central-1.amazonaws.com/comunicacion/Integraci%C3%B3n+econ%C3%B3mica/%C3%81reas+de+la+Integraci%C3%B3n+Econ%C3%B3mica/Zona+de+libre+comercio/SAC+VII+ENM-WORD-+VERSION+6-AGOSTO+25-COMPRIMIDO.pdf |
| Organismo | Secretaría de Integración Económica Centroamericana |
| Información | SAC a 10 dígitos + DAI %, VII Enmienda, "Versión 6 Agosto 2025". Es el Anexo 1 de la Res. 450-2021; en la misma página están las Res. 492-2024, 486-2024 y 487-2024 (**VERIFICADO**). Correlaciones hasta SAC 2017↔2022: https://www.sieca.int/producto/correlaciones-sac/ |
| Actualización | `dateModified` 2025-09-08 (**VERIFICADO**) |
| Formato | PDF, unos 9,5 MB |
| Restricciones | robots.txt permisivo |
| Automatización | Posible: vigilar `dateModified` y parsear el PDF |
| Confiabilidad | Alta para el DAI regional a 10 dígitos. **No trae** los impuestos internos de Costa Rica ni el desdoble nacional a 12 dígitos. |
| Estrategia | Cruzar contra el DAI de nuestro catálogo; una diferencia marca el inciso para revisión |

**Próxima enmienda del Sistema Armonizado:** **HS 2028**, vigente desde el 1-ene-2028 (**INFERIDO**, según la OMA). No se encontró todavía una resolución SIECA de la VIII Enmienda (**NO COMPROBADO**). Habrá correlaciones y cambios de códigos.

### 1.4 COMEX — Acuerdos comerciales y programas de desgravación

| Campo | Detalle |
|---|---|
| URL | https://www.comex.go.cr/acuerdos-comerciales/ |
| Información | Lista de acuerdos vigentes (**VERIFICADO**): AELC, CAFTA-DR (desde 1-ene-2009, Ley 8622), Canadá, CARICOM, Centroamérica, Chile, China, Colombia, Corea, Ecuador (desde 1-oct-2024, Ley 10494), México, OMC, Panamá, Perú, Reino Unido (desde 1-ene-2021), República Dominicana, Singapur, Unión Europea (AACUE), Emiratos Árabes Unidos (desde 1-abr-2025, Ley 10618). En proceso: ACCTS, Alianza del Pacífico, DEPA, Israel, CPTPP. |
| Desgravación | Las listas oficiales están en SCIJ como anexos de cada ley (**VERIFICADO** con dos ejemplos). CAFTA, Anexo 3.3 (HTML): https://www.pgrweb.go.cr/docsdescargar/Normas/No%208622-%20TLC%20EEUU-Centroamerica-RD/Version1/Lista%20Arancelaria%20CR%20Anexo%203_3_Notas%20Generales.html. Perú (PDF): http://www.pgrweb.go.cr/docsdescargar/Normas/No%209133-TLC%20CR-Peru/Version1/ListaCosta%20Rica.pdf |
| Formato | HTML o PDF por tratado; cada inciso tiene una categoría de desgravación con su calendario |
| Automatización | Carga única por tratado (categoría por inciso → tasa por año) y recálculo cada 1 de enero |
| Confiabilidad | Alta, con mucho trabajo manual de carga |
| Herramienta pública | No se encontró una "consulta de aranceles preferenciales" de COMEX |

### 1.5 PROCOMER

- **URL:** https://procomer.com/plataformas-de-comercio-exterior/ (**VERIFICADO**). Ofrece Portal Estadístico, VUCE (https://vuce.cr/) y estudios de mercado.
- **No tiene calculadora ni consulta arancelaria.**
- **Utilidad para este proyecto:** baja. VUCE es relevante para los permisos (sección 7).

### 1.6 Espejos internacionales (solo para validación cruzada)

| Fuente | URL | Estado y utilidad |
|---|---|---|
| OMC Tariff & Trade Data | https://ttd.wto.org/en/profiles/costa-rica | **VERIFICADO**: perfil con el NMF aplicado de 2026. La descarga por línea (https://ttd.wto.org/en/download) exige login. Sirve para cruzar el DAI. |
| API OMC | https://apiportal.wto.org/ | `api.wto.org` pide clave de suscripción (401, **VERIFICADO**) |
| WITS (Banco Mundial) | https://wits.worldbank.org/API/V1/wits/datasource/trn/dataavailability/country/188/year/all | **VERIFICADO**: el último año de Costa Rica es **2021 en HS2017**. Rezago muy grande, no sirve como fuente. |
| ITC Market Access Map | https://www.macmap.org/ | Respondió 403. Registro gratuito según fuentes secundarias (**INFERIDO**); términos **NO COMPROBADOS** |

Estas fuentes cubren el DAI y las preferencias, **no** el ISC, la Ley 6946 ni los impuestos específicos nacionales.

### 1.7 Tipo de cambio

| Fuente | Detalle | Estado |
|---|---|---|
| **API pública de Hacienda** | https://api.hacienda.go.cr/docs (Last-Modified 24-sep-2025). `/indicadores/tc` respondió `dolar.venta 455.6 / compra 449.86`, fecha 2026-10-12, sin token. Límites documentados: 20 solicitudes/s en ráfaga y 10/s sostenidas; si se exceden, la IP queda bloqueada 10 minutos. `/indicadores/tc/dolar/historico?d=&h=` está documentado pero respondió 503 en la prueba. | **VERIFICADO** (el endpoint diario) |
| **API SDDE del BCCR** | Documento: https://gee.bccr.fi.cr/indicadoreseconomicos/Documentos/DocumentosMetodologiasNotasTecnicas/Estandar_API_SDDE.pdf (norma vigente desde el 7-abr-2025). Base `https://apim.bccr.fi.cr/SDDE/api/Bccr.GE.SDDE.Publico.Indicadores.API`, endpoints `/indicadoresEconomicos/{codigo}/series?fechaInicio=yyyy/mm/dd&fechaFin=…`, `Authorization: Bearer <token>` (el token se obtiene registrándose en https://sdd.bccr.fi.cr/). Sin token responde 401. Indicador 317 = compra (**VERIFICADO**); 318 = venta (**INFERIDO**). | **VERIFICADO** (el documento y el 401) |
| Servicio SOAP antiguo del BCCR | `wsindicadoreseconomicos.asmx` respondió 503 | Probablemente obsoleto (**INFERIDO**) |

**Qué tipo de cambio usa la aduana:** el de referencia del BCCR, **de venta**, vigente a la fecha de **aceptación de la declaración**. Base legal: art. 55 a) LGA (el hecho generador es la aceptación), art. 257 LGA y art. 16 del Reglamento Centroamericano sobre Valoración. El instructivo DGA dice: "Tipo de cambio a utilizar… es el vigente al momento del hecho generador" (**VERIFICADO**).

**Pendiente:** confirmar que el valor de la API de Hacienda coincide con el de referencia del BCCR del mismo día. No se pudo comparar porque el sitio del BCCR carga las cifras con JavaScript.

### 1.8 Instructivo oficial de cálculo

- **Documento:** MH-DGA-PRO09-INS-004, v01, agosto 2023, "Cálculo de la obligación tributaria para la importación de mercancías". https://www.hacienda.go.cr/docs/INSTRUCTIVO_CALCULO_OBLIGACION_TRIBUTARIA.pdf (**VERIFICADO**).
- **Contenido:** fórmulas por tributo y ejemplos completos. Es la fuente principal del motor de cálculo. Advertencia: algunos ejemplos traen errores internos (un "SC 15%" calculado al 10%) y montos de 2023.

---

## 2. Valor aduanero (base imponible)

- **Norma (VERIFICADO).** El art. 251 de la LGA (Ley 7557) remite al Acuerdo de Valoración de la OMC, al Título XI de la LGA, al art. 44 del CAUCA y al art. 187 del RECAUCA IV. El RECAUCA IV es el Decreto 42876-H-COMEX, vigente desde el 1-may-2021. Fuente: MH-DGA-RES-1578-2025, https://www.hacienda.go.cr/docs/MH-DGA-RES-1578-2025.pdf
- **La base es CIF (VERIFICADO).** El art. 4 del Reglamento Centroamericano sobre la Valoración Aduanera suma al precio realmente pagado el transporte hasta el puerto o lugar de importación, la carga, descarga y manipulación, y el seguro. "Lugar de importación" es el primer puerto de arribo al Territorio Aduanero Centroamericano. Texto: https://mineco.gob.gt/images/viceministerio_integracion_comercio/direccion_administracion_comercio_exterior/tratados_vigentes/int_economica_centroamericana/reglamento-c-a-sobre-la-valoracion-aduanera-de-las-mercancias_0.pdf
- **Flete no contratado o gratuito (VERIFICADO).** Según los arts. 5 y 6 del reglamento, se usa la tarifa normalmente aplicable que suministra el Servicio Aduanero. **No hay un porcentaje fijo de flete presunto.** Manual ONVVA: https://www.hacienda.go.cr/docs/DN-184-2009Anexo.pdf
- **Seguro no contratado.** Se aplican las tarifas de la **Circular ONVVA-002-2002**, "Determinación del costo de seguros" (**VERIFICADO que existe**; sus porcentajes **NO COMPROBADOS**). La RES-1578-2025 (13-oct-2025) agrega que en transporte multimodal se usa la tarifa del medio que cubra la mayor distancia.
- **Exclusiones (VERIFICADO).** No integran el valor los intereses de financiación que cumplan el art. 7 del reglamento (art. 256 LGA). Los descuentos comprobables sí se aceptan (art. 8).
- **Vehículos.** Tienen un régimen propio y cambiante:
  - La Ley 10390 (Gaceta 4-oct-2023) define su valor en aduana como factura + seguro + flete (**VERIFICADO**).
  - La RES-1897-2024 eliminó la "clase tributaria" desde el 20-nov-2024 (**INFERIDO**).
  - El Decreto 45920-H (agosto 2026) compara la factura con el "valor hacendario" y cobra sobre el mayor (**INFERIDO**, prensa).

**Consecuencia para el diseño:**
- El estimador pide flete y seguro reales.
- Si el usuario no conoce el seguro, se marca el supuesto y **baja la confianza**. No se inventa un porcentaje mientras no se lea la circular.
- Los vehículos quedan fuera del MVP.

## 3. Tributos, bases y orden de cálculo

El instructivo DGA calcula en **cascada**. Todo se pasa primero a colones: **VA × TC** (**VERIFICADO**).

| Paso | Tributo | Base legal | Fórmula (instructivo DGA) | Estado |
|---|---|---|---|---|
| 1 | **DAI** | Arancel Centroamericano; tasa por inciso | (VA×TC) × %DAI | VERIFICADO |
| 2 | **Selectivo de Consumo (SC/ISC)** | Ley 4961 y anexos 1-3; tasa por inciso | ((VA×TC) + DAI) × %SC | VERIFICADO (ver duda 2) |
| 3 | **Ley 6946** | Ley 6879 reformada por la Ley 6946; 1% | (VA×TC) × 1% | VERIFICADO |
| 4 | **Específicos y destinos especiales** | Varias leyes, solo si el inciso los tiene | ver lista abajo | VERIFICADO (montos de 2023) |
| 5 | **Margen de Valor Agregado (G/E)** | Anexo 4; p. ej. 25% en ciertos incisos | (VA×TC + DAI + SC + 6946) × %GE. **No es un impuesto**: solo aumenta la base del IVA | VERIFICADO |
| 6 | **IVA** | Ley 9635, art. 14 | [VA×TC + DAI + SC + 6946 + INDER + IFAM + Forestal + Específicos + G/E + Caldera + otros cargos de la declaración] × % | VERIFICADO |
| Total | | | DAI + SC + 6946 + específicos + IVA (sin el G/E) | |

**Detalle de cada tributo:**
- **SC.** El art. 10 a) de la Ley 4961 define la base en importación como "valor CIF aduana de ingreso + derechos de importación + Impuesto de Estabilización Económica efectivamente pagados" (**VERIFICADO** en copia del texto). Las tasas varían por inciso; en vehículos, también por antigüedad (en los ejemplos oficiales, 48% y 78%).
- **IVA.** La tarifa general es 13%. Tarifas reducidas del art. 11 de la Ley 9635 (**VERIFICADO**):
  - **2%**: medicamentos y sus insumos autorizados.
  - **1%**: canasta básica; bienes agropecuarios y sus insumos; insumos veterinarios y de pesca no deportiva.
  - Las exenciones o tarifas reducidas fuera de la lista se tramitan en **EXONET** (Decreto 31611-H).
  - El 4% aplica sobre todo a servicios (**INFERIDO**).
- **Específicos (VERIFICADO, instructivo DGA 2023; los montos se actualizan periódicamente):**
  - **Leyes 7972 / 8114**: monto por unidad en bebidas alcohólicas (ml × grado × ¢), bebidas envasadas (por 250 ml) y jabón de tocador (por gramo).
  - **INDER** (Leyes 6735/9036): alcohol, cerveza, gaseosas y cigarrillos.
  - **IFAM** (Ley 10): licores. La cerveza quedó exenta por la Ley 10337.
  - **Forestal** (Ley 7575): 3% del VA en madera.
  - **Arroz** (Ley 8285): 1,5% del CIF.
  - **Salvaguardia de azúcar**: 9,23% adicional.
  - **Combustibles** (Ley 8114): impuesto único por litro, que **excluye** DAI, SC, 6946 e IVA.
  - **Vapeadores** (Ley 10066): (VA×TC) × 20%.
  - **Caldera**: US$0,40 y US$0,20 por tonelada, más ¢1 y ¢2 por tonelada métrica.
  - **Canon de carne**: US$3 por cada 200 kg.
- **Tabaco** (Ley 9028): monto específico vigente **NO COMPROBADO**.
- **Cargos fijos por declaración** (ejemplo del instructivo, montos de 2023; **VERIFICADO**). Integran la base del IVA:
  - timbre de Archivo Nacional ¢20;
  - timbre de Agentes de Aduana ¢50 (Ley 7017);
  - Colegio de Contadores Privados ¢2;
  - PROCOMER US$3 por formulario;
  - servicio cuarentenario del MAG ¢5.756, cuando aplica.
- **No son tributos:** honorarios del agente aduanero, almacenaje, flete local, cargos de courier o casillero y muellaje privado. Son costos logísticos y la calculadora debe mostrarlos **aparte**, nunca como impuesto. No se encontró una tarifa oficial de honorarios (**NO COMPROBADO**).

## 4. Origen y tratados

- **El país de compra o de embarque no determina el trato preferencial.** Lo determina el **origen** según las reglas de origen del tratado (**INFERIDO** de la normativa; los ejemplos oficiales lo reflejan). Por ejemplo, unos pantalones "procedentes de Miami" pagan el DAI normal, mientras que los "originarios de Guatemala" pagan 0%.
- **Cómo se aplica la preferencia (VERIFICADO, instructivo DGA §N).** La tasa preferencial **sustituye al DAI y a la Ley 6946**; el SC y el IVA se cobran igual. Por ejemplo, México 4% = 3,80 DAI + 0,20 Ley 6946.
- **Prueba de origen.** Sin ella se paga la tasa normal (NMF) (**INFERIDO**). El art. 86 de la LGA exige el certificado "cuando proceda" (**VERIFICADO**). Formas de prueba según COMEX (https://www.comex.go.cr/certificación-de-origen/, **VERIFICADO**):
  - **Certificado emitido por una autoridad:** China, Colombia, CARICOM, Perú, Ecuador, EAU.
  - **EUR.1 o declaración en factura:** UE, Reino Unido, AELC.
  - **Autocertificación:** CAFTA-DR, Canadá, Centroamérica, Chile, México, Panamá, República Dominicana, Singapur, Corea.
- **Consecuencia para el diseño.** El MVP calcula **siempre la tasa NMF** y, como mucho, informa que "podría aplicar una preferencia si demuestra origen X", sin restarla del total. Las preferencias entran en una fase posterior, con los calendarios de desgravación verificados.

## 5. Courier y envíos de bajo valor

- **Sin tasa única (VERIFICADO).** El art. 127 de la LGA da trato postal a documentos e impresos y "el tratamiento tributario correspondiente" al resto. Es decir, un courier paga DAI + 1% + SC + IVA según el inciso. LGA: https://faolex.fao.org/docs/pdf/cos218199.pdf
- **Envíos familiares sin carácter comercial (VERIFICADO).** Están exentos hasta 500 pesos centroamericanos (art. 137 LGA; art. 116 CAUCA: https://faolex.fao.org/docs/pdf/sica205668.pdf). Correos de Costa Rica exige además remitente persona física, uso personal y una vez cada 6 meses: https://correos.go.cr/aduana-paqueteria-internacional/
- **Compras en línea de hasta US$500.** Ya no se exoneran desde mayo de 2021, con el CAUCA IV (**INFERIDO**, prensa).
- **Importaciones no comerciales de hasta 100 pesos centroamericanos** (art. 136 LGA): es una facilidad de trámite sin agente, no una exención (**VERIFICADO**).
- **Procedimiento courier vigente:** está en el RLGA, Decreto 44051-H, arts. 387-402, que **no se pudo leer** (**NO COMPROBADO**). La resolución DGA-021-2006 (DUA simplificado hasta 1.000 pesos CA) puede estar superada.
- **Umbrales "de minimis" de US$50 o US$100** citados por guías comerciales: **NO COMPROBADO**.

## 6. Exenciones y regímenes especiales

- **Equipaje y franquicia del viajero.** El equipaje entra exento (art. 113 CAUCA). Otras mercancías que traiga el viajero están exentas hasta 500 pesos CA (art. 114) (**VERIFICADO**). La periodicidad que aplica Costa Rica: **NO COMPROBADO**.
- **Zona franca, ventas locales** (instructivo §Q; **VERIFICADO**). Se paga DAI + 1% sobre los insumos importados y luego SC + IVA sobre el producto final.
- **Perfeccionamiento activo** (Decreto 41098-COMEX-H) y **regímenes temporales** (art. 55 LGA): **VERIFICADO**.
- **Otras exenciones por ley:** CCSS, universidades e insumos agropecuarios (Ley 7293); Depósito Libre de Golfito (Ley 9356); vehículos eléctricos (Leyes 9518 y 10209, **INFERIDO**).
- **Alcance del MVP:** **solo importación definitiva sin exenciones.** Cualquier régimen especial es "no estimable" y se deriva a un especialista.

## 7. Requisitos no arancelarios (permisos)

- **Notas técnicas obligatorias.** Cada inciso en TICA puede llevar notas de Salud, MAG/SFE, SENASA, MINAE, etc. Deben presentarse con el DUA (**VERIFICADO**, instructivo §Q). Sin ellas el DUA no se tramita (**INFERIDO**).
- **Trámite.** Se gestionan en la VUCE de PROCOMER (Ley 7638, art. 8; **INFERIDO**). La circular MH-DGA-CIR-0010-2026 cambió el control de algunas notas (**INFERIDO**).
- **Celulares.** Homologación de terminales en SUTEL: https://www.sutel.go.cr/sites/default/files/rcs-141-2022_modificacion_parcial_del_procedimiento_homologacion_terminales_moviles.pdf (**INFERIDO** que aplique como requisito aduanero).
- **Consecuencia para el diseño.** El catálogo guarda las notas técnicas por inciso. Si un inciso las tiene, la respuesta muestra un aviso "requiere permiso de X" y la confianza no puede ser alta.

## 8. Ejemplo oficial recalculado

Instructivo DGA, p. 18: pantalones de algodón procedentes de Miami. VA US$9.600, TC ¢602, DAI 14%, Ley 6946 1%, IVA 13%.

| Concepto | Cálculo | Monto |
|---|---|---|
| VA × TC | 9.600 × 602 | ¢5.779.200,00 |
| DAI 14% | 5.779.200 × 0,14 | ¢809.088,00 |
| Ley 6946 1% | 5.779.200 × 0,01 | ¢57.792,00 |
| Base IVA | 5.779.200 + 809.088 + 57.792 | ¢6.646.080,00 |
| IVA 13% | 6.646.080 × 0,13 | ¢863.990,40 |
| **Total tributos** | 809.088 + 57.792 + 863.990,40 | **¢1.730.870,40** |

Este caso y los demás ejemplos del instructivo (SC 15%, vehículo con SC 78% y G/E 25%) son los **casos dorados** de las pruebas del motor (ver 03). Corregidos donde el instructivo tenga errores internos, con la corrección documentada.

## 9. Dudas abiertas (no verificado)

1. **Tasas de seguro presunto** de la Circular ONVVA-002-2002.
2. **¿El 1% de la Ley 6946 integra la base del SC?** La fórmula general del instructivo no lo incluye y su ejemplo de vehículo sí. La Ley 4961 dice "derechos de importación". Hay que consultarlo a un especialista o a la DGA.
3. **Errores internos del instructivo:** por ejemplo, "SC 15%" calculado al 10%.
4. **Régimen courier vigente:** RLGA, arts. 387-402.
5. **Montos de 2025-2026** de los específicos (Leyes 7972, 8114, 9028), los timbres y la tarifa de PROCOMER.
6. **Decreto 45920-H (vehículos):** texto y vigencia.
7. **Equivalencia del peso centroamericano** (se usa como US$1, **INFERIDO**) y periodicidad de la franquicia del viajero.
8. **Valor de la API de Hacienda frente a la referencia del BCCR del mismo día.**
9. **Si la DGA entregaría hoy una exportación del arancel**, y en qué formato.
10. **Reglas de redondeo por tributo:** no se encontró una regla; los ejemplos oficiales usan céntimos.
