# 03 — Confiabilidad: cómo medirla y cómo protegerla

> Estado: metodología propuesta, 2026-10-12. **No hay resultados**: ningún porcentaje de este documento es una medición. Los números que aparecen son definiciones, tamaños de muestra o reglas a decidir.

## 1. Respuesta corta

**¿Qué tan confiable puede llegar a ser?** No se puede afirmar un porcentaje antes de medir con casos reales. Sí se puede afirmar qué parte es demostrable y qué parte no:

| Capa | ¿Se puede garantizar? | Cómo |
|---|---|---|
| **Matemática** (aplicar la fórmula) | **Sí, contra una política de redondeo explícita y versionada** (hoy **a verificar**: 01 §9.10) | Código determinista con `Decimal` y casos dorados del instructivo DGA y de DUAs reales. Igualdad exacta contra la política; objetivo 100%, no negociable. |
| **Tributaria** (elegir tasas y reglas para un inciso dado) | **Se reduce el riesgo, no se garantiza.** Un perfil completo curado y reciente es lo más confiable posible, pero un cambio normativo posterior puede invalidarlo | Una persona publica el perfil completo de cada inciso en TICA con fecha y evidencia; se audita contra DUAs; los cambios normativos conocidos lo suspenden (02 §9) |
| **Clasificación** (elegir el inciso) | **No se puede garantizar.** Clasificar es un acto técnico-jurídico que hasta los especialistas discuten. | Se mide con casos reales; la ambigüedad se declara; el usuario confirma |
| **Estimación final** (parecerse a lo que realmente se paga) | Depende de las tres anteriores y de los datos del usuario (precio, flete, seguro, origen) | Se mide contra DUAs reales; se acota con niveles de confianza |

El riesgo está en la **clasificación** y en los **datos que da el usuario**, no en la aritmética. Por eso las métricas van separadas: mezclarlas en un solo "% de precisión" escondería justo lo que importa.

## 2. Métricas

### 2.1 Confiabilidad de clasificación

Medida sobre el banco de casos con inciso real (de un DUA o de un especialista):
- **Exactitud jerárquica top-1:** porcentaje de casos en que el inciso propuesto coincide con el real a 4, 6, 8, 10 y 12 dígitos (capítulo/partida, subpartida SA, SAC, nacional).
- **Top-3:** el real está entre los 3 primeros candidatos mostrados. Importa porque el usuario elige.
- **Exactitud equivalente en tasas:** el inciso propuesto puede ser otro, pero con las mismas tasas (DAI, SC, específicos, IVA). Es lo que importa para el monto: un error de inciso sin efecto en las tasas no cambia la estimación.
- **Abstención:** porcentaje de casos en que el sistema pidió más datos o no propuso inciso.
- **Recall@K de la recuperación**, medido **antes** de que la IA elija: porcentaje de casos en que el inciso real está entre los K candidatos que devolvió la búsqueda. Se mide aparte de la selección, porque si la búsqueda no lo trae, la IA no puede elegirlo, y ni siquiera un rango mín-máx lo cubre.

### 2.2 Confiabilidad tributaria

Dado el **inciso correcto**:
- porcentaje de casos en que cada tasa del catálogo (DAI, SC, 6946, IVA, específicos, G/E y cargos) **y su base de cálculo** coinciden con las del DUA, por tributo;
- porcentaje de casos con un **tributo omitido** (el DUA cobró algo que el motor no aplicó) y con un tributo de más.

### 2.3 Confiabilidad matemática

Hay dos comparaciones con contratos distintos:
- **Contra la política de redondeo del motor** (tests): igualdad **exacta**. La política define si se redondea cada base, cada tributo o solo el total, el modo y los céntimos. Está **a verificar** con un especialista y DUAs reales (01 §9.10). Mientras tanto se versiona como supuesto.
- **Contra DUAs reales** (evaluación): con los mismos datos, tasas y tipo de cambio del DUA, la diferencia de cada monto y del total. Mientras la política no esté confirmada, se tolera **±¢1** por tributo. Cualquier diferencia mayor es un bug o una regla mal entendida, y se investiga antes de seguir.

Casos obligatorios: medias unidades, varias líneas en un mismo DUA y cascadas con SC, específicos y G/E. La ausencia de `float` es una condición necesaria, no una prueba de corrección.

### 2.4 Confiabilidad de la estimación final

Se separan dos evaluaciones, porque un DUA histórico tiene fecha, tasas y tipo de cambio propios:
- **Reproducción histórica:** con la fecha del DUA, el perfil vigente **en esa fecha** y su tipo de cambio. Mide clasificación + tasas + matemática en condiciones idénticas.
- **Estimación actual:** con el perfil y el tipo de cambio de hoy, como la usaría el público. Sirve para la experiencia, no para medir la exactitud contra lo pagado.

Los casos fuera de alcance (preferencias aplicadas, exenciones, regímenes especiales, valoración ajustada o cargos atípicos) se **etiquetan y excluyen** o se normalizan documentando la diferencia. No se mezclan.

Métricas, sobre el flujo completo (descripción en lenguaje natural → total), comparando contra lo pagado en reproducción histórica:
- **error absoluto** en colones y **error relativo** |estimado − real| / real (sin calcular el relativo cuando el real es 0): mediana, P75, P90, máximo y la cola de errores críticos;
- **distribución por bandas:** porcentaje dentro de ±2%, ±5%, ±10% y ±25%. Son cubetas para informar, no metas;
- todo **por categoría** y **por nivel de confianza**.

### 2.5 Métricas operativas

| Métrica | Definición |
|---|---|
| Distribución por nivel | Porcentaje de consultas en alta, media, baja y no estimable |
| Información insuficiente | Porcentaje de consultas que terminan en "faltan datos" |
| Requiere revisión | Porcentaje que el sistema deriva a un especialista (exclusiones, regímenes, permisos) |
| **Errores críticos** | Estimación **alta** con error > umbral C (a decidir, D11), **o** un tributo omitido, **o** un permiso obligatorio sin aviso. Se cuentan uno por uno. **Meta: cero en nivel alto.** |
| Detección de ambigüedad | Entre los casos que el especialista marcó como ambiguos, porcentaje que el sistema también marcó (sensibilidad) y falsas alarmas |
| Frescura | Edad de la verificación de cada tasa usada; porcentaje de estimaciones con tasas cerca de vencer; edad del tipo de cambio |
| Calidad de la explicación | Revisión de una muestra por un especialista con una rúbrica: fuentes correctas, supuestos ciertos, nada inventado. Porcentaje que aprueba. |
| **Calibración** | El nivel de confianza tiene que significar algo. Por nivel se miden el error mediano, la cola (P90 y máximo) y la tasa de errores críticos: **alta** tiene que ser mejor que **media**, y esta mejor que **baja**, en las tres medidas. Si no, se corrigen los criterios. |

## 3. Evaluación con casos reales

### 3.1 De dónde salen los casos

El LMS **no los tiene**: 1 de 815 cargas tiene partida y 13 tienen factura comercial. Fuentes posibles, por orden de valor:
1. **DUAs reales liquidados** de clientes de Amvarmar o de su agencia aduanal aliada. Traen descripción, inciso, valores y montos pagados por tributo. Es la fuente más fuerte (decisión D4).
2. **Liquidaciones hechas por un especialista de Amvarmar** en TICA para casos armados a propósito, sobre todo los difíciles.
3. **Ejemplos del instructivo DGA:** solo validan la matemática (2.3), no la clasificación.

**Anonimización antes de entrar al banco:**
- se quitan importador, cédula, proveedor, números de factura, DUA y BL;
- se conserva la descripción comercial (generalizada si identifica a alguien), el inciso, el país de origen, el FOB, el flete, el seguro, el tipo de cambio, cada tributo pagado y el régimen;
- se guarda en `tax_eval_cases` (interna) y **nunca** en el repositorio.

### 3.2 Composición del banco

Categorías pedidas, con casos fáciles y difíciles en cada una:

| Categoría | Por qué está | Dificultad típica |
|---|---|---|
| Computadoras y laptops | Volumen alto; capítulo 84.71 | Baja |
| Teléfonos | 85.17; homologación SUTEL | Baja en tasas, media en permisos |
| Repuestos de vehículos | Clasificación por función y material; capítulos dispersos | **Alta** |
| Ropa | Capítulos 61/62: tejido de punto o no, material dominante | **Alta** |
| Zapatos | Capítulo 64: material de la suela y de la parte superior | Media-alta |
| Maquinaria | 84/85; partes frente a máquinas completas | Alta |
| Electrodomésticos | 84/85; posible SC | Media |
| Electrónica de consumo | Media | Media |
| Con impuestos especiales | Bebidas, cosméticos, vapeadores | **Alta** (específicos) |
| Con permisos | Alimentos, químicos, medicamentos, agro | Alta (notas técnicas) |
| Con tratados | Origen CAFTA, China, UE (fase 7) | Alta (origen) |
| Difíciles de clasificar | Kits, juegos, productos multifunción | **Muy alta** |

**Tamaño de muestra, sin inventar resultados:**
- La regla aproximada ±1/√n (100 casos ≈ ±10 puntos al 95%) supone casos independientes. Un banco curado y agrupado por familias de productos puede no cumplirlo. Se informan **intervalos de Wilson** y se agrupa por familia.
- **Cero errores no prueba cero riesgo.** Con 0 errores críticos en *n* casos, el límite superior al 95% es de aproximadamente 3/*n* (regla del tres): unos 10% con 30 casos y 14% con 20 (cálculo binomial exacto: 9,5% y 13,9%). Para afirmar que el error crítico de nivel **alto** es menor que *p*, hacen falta unos **3/p casos de nivel alto** en esa categoría (p. ej. 300 casos para menos de 1%).
- **Piloto:** 20-30 casos por categoría (unos 300 en total) para la primera lectura y para **fijar** los umbrales.
- **Prueba:** un conjunto **nuevo y reservado**, dimensionado según el error crítico tolerable que defina Amvarmar (D11). Solo ese conjunto habilita una categoría. Se informa el número efectivo de casos por categoría y por nivel.
- Las categorías con menos casos se informan como "insuficiente para concluir".

### 3.3 Protocolo

1. **Congelar el banco** antes de medir. Hay tres conjuntos: **desarrollo** (para ajustar términos de búsqueda y prompt), **piloto** (para fijar umbrales) y **prueba reservada** (solo para habilitar; no se mira mientras se ajusta). La división es **por familia de producto y por origen del caso**, para que productos casi iguales no queden a ambos lados.
2. **Adjudicación de etiquetas:** el inciso y los montos "reales" de cada caso los revisa un segundo especialista. Los desacuerdos se resuelven y quedan registrados. Los casos y adjudicaciones se versionan.
3. Correr `scripts/evaluar_estimador.py`. Para cada caso:
   - se da la descripción;
   - se registra si el sistema pidió datos, qué candidatos dio, qué inciso eligió y qué calculó;
   - se calculan todas las métricas de §2.
4. Reportar por métrica, por categoría y por nivel de confianza, con intervalos. Cada corrida guarda la versión de reglas, el modelo y la fecha.
5. Un especialista revisa los **errores críticos** y una muestra de las explicaciones.
6. Repetir con cada cambio de modelo, de prompt, de catálogo o de reglas. Las regresiones bloquean el despliegue. Cuando el conjunto reservado se usa muchas veces, se renueva.

### 3.4 Qué decide la evaluación

- **Habilitación del nivel alto:** una categoría solo puede dar estimaciones **altas** en público si en el **conjunto de prueba reservado** cumple los umbrales que Amvarmar fijó **con el piloto** (D11), con el número mínimo de casos de nivel alto que exija ese umbral (§3.2) y **cero errores críticos**.
- Si no lo cumple, su techo es **medio**.

## 4. Niveles de confianza: criterios verificables

Los calcula `confianza.py` a partir de hechos del sistema. **El LLM no participa.** Se aplica la regla del nivel más bajo: el resultado es el nivel más bajo que dispare cualquier criterio.

### No estimable

No se muestra un total. Se explica el motivo y se deriva a un especialista. Pasa si se cumple cualquiera de estas condiciones:
- el inciso tiene `exclusion` (vehículo, alcohol, tabaco, combustible, …) o el usuario indicó un régimen especial o una exención;
- el inciso no está en el catálogo, o no tiene un **perfil publicado** (02 §4), o su perfil está `SUSPENDED` (vencido por encima del límite duro o afectado por un cambio normativo; D2), o algún tributo del perfil está en `UNKNOWN` o `UNSUPPORTED`;
- no hay tipo de cambio de los últimos N días hábiles (D2);
- falta el precio o la cantidad;
- la clasificación no encontró ningún candidato con similitud mínima.

**Tres formas de resultado, con semántica distinta:**
- **Total:** un monto, con sus supuestos visibles (niveles alta y media).
- **Intervalo entre clasificaciones:** el mínimo y el máximo entre los candidatos **recuperados**. No cubre un inciso que la búsqueda no trajo, y se dice.
- **Piso incompleto:** el monto calculado con lo que se sabe, rotulado "faltan X". **Nunca** se muestra un máximo inventado para lo que falta.

### Baja

Pasa si se cumple cualquiera:
- hay **dos o más candidatos plausibles con tasas distintas** y el usuario no eligió uno. Resultado: **intervalo entre clasificaciones**, con aviso fuerte;
- se desconoce el **flete o el seguro**. Resultado: **piso incompleto**, que dice qué componente del valor aduanero falta. Hasta que se lea la Circular ONVVA-002-2002 (01 §2) no hay una cota respaldada para el seguro presunto, así que el flete y el seguro se tratan igual.

### Media

**Total** con supuestos visibles. Pasa si se cumple cualquiera:
- el perfil del inciso está **publicado con la revisión vencida**, pero dentro del límite duro (D2);
- el tipo de cambio no es del día (pero está dentro de N días hábiles);
- el inciso tiene **notas técnicas** (permiso obligatorio): el monto puede ser correcto, pero la importación no es posible sin el permiso;
- el inciso lo propuso el clasificador con candidatos alternativos de **iguales tasas**, o lo escribió el usuario sin pasar por el clasificador;
- la categoría todavía no cumple el umbral de evaluación (§3.4);
- hay un posible TLC que el MVP no aplica (se muestra el NMF).

### Alta

Pasa si se cumplen **todas** estas condiciones:
- un único inciso, confirmado por el usuario, sin alternativas de tasas distintas;
- perfil del inciso **publicado, completo y con la revisión vigente**;
- tipo de cambio del día de una fuente oficial;
- precio, cantidad, flete y seguro declarados;
- importación definitiva sin régimen especial ni exclusiones ni notas técnicas;
- categoría habilitada por la evaluación (§3.4).

**Hasta alta lleva el descargo:** "Estimación referencial; el monto definitivo lo determina la aduana al aceptar la declaración."

## 5. Protección contra errores

| Error | Protección |
|---|---|
| **Código inexistente** | La IA elige **solo** entre candidatos que devuelve el backend; el backend rechaza cualquier código fuera de esa lista o del catálogo. En la UI, el usuario solo puede elegir entre incisos que existen. |
| **Tasas desactualizadas** | Cada perfil tiene vigencia legal, fecha de observación y vencimiento de revisión; los vencidos bajan el nivel o se suspenden; un cambio normativo conocido los suspende de inmediato; hay una cola de re-verificación; una alerta semanal avisa de cambios en las fuentes (02 §9). |
| **TLC mal aplicado** | El MVP **no** aplica preferencias: siempre NMF, con aviso informativo. En la fase 7 la preferencia exige que el usuario declare que **tiene** la prueba de origen, y se muestran los dos montos (con y sin preferencia). |
| **Clasificación ambigua** | Las alternativas con tasas distintas fuerzan el nivel bajo y un rango; se hacen preguntas aclaratorias (material, uso, tipo de tejido, …). |
| **Errores matemáticos** | Motor determinista con `Decimal`; casos dorados en CI; la versión de reglas queda fijada por los tests. |
| **Impuestos omitidos** | El catálogo guarda **todos** los tributos del inciso (también ISC, específicos y G/E). Que falte uno que la fuente sí tiene impide marcarlo verificado. La evaluación mide las omisiones (§2.2). Las categorías con específicos complejos quedan excluidas en el MVP. |
| **Facturas mal interpretadas** | El MVP no lee facturas. Si se suma OCR más adelante, los datos extraídos se muestran para que el usuario los confirme antes de calcular. |
| **Requisitos especiales** | Las notas técnicas por inciso disparan avisos de permiso, y el nivel no puede ser alto. |
| **Respuestas inventadas** | La UI muestra los números del **resultado estructurado del motor**, nunca del texto del modelo. El prompt prohíbe afirmar tasas o códigos que no vengan de una herramienta. Las explicaciones se revisan por muestreo (§2.5). |
| **Presentar una estimación como liquidación** | Lenguaje fijo ("estimación referencial"), descargo legal (D10), rango cuando hay incertidumbre e identificación de la versión, la fuente y la fecha en cada resultado. |

## 6. Límites honestos

- Sin una exportación oficial del arancel, la frescura de las tasas depende de **trabajo humano**. Si la curaduría se abandona, el sistema degrada solo a "no estimable" en vez de mostrar tasas viejas.
- La exactitud de clasificación desde lenguaje natural va a ser **menor en ropa, repuestos y productos compuestos**. Lo esperable es que en esas categorías el techo práctico sea medio, con un rango.
- El monto final depende de datos que el usuario puede no saber: el flete real, el seguro y el tipo de cambio del día de aceptación, que es posterior a la consulta. Ninguna mejora técnica elimina eso. Por eso el resultado es una **estimación** y así se rotula.
