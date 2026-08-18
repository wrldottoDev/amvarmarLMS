# 🔐 Incidente de Credenciales — Amvarmar

> **Repositorio afectado:** `amvarmarProduccion` (repositorio viejo)
> **Estado:** ✅ Resuelto y cerrado

| Campo | Detalle |
|---|---|
| 📅 **Fecha de corrección** | 18 de agosto de 2026 |
| 👤 **Responsable** | Otonie González |
| 🗂️ **Repositorio** | `amvarmarProduccion` |
| 🔴 **Severidad** | Alta (credenciales de producción versionadas) |
| 🌐 **Exposición externa** | Sin evidencia de acceso externo |
| ✅ **Estado final** | Corregido, rotado y limpiado |

---

## 📋 Tabla de contenido

1. [Qué pasó](#-qué-pasó)
2. [Información expuesta](#-información-expuesta)
3. [Impacto](#-impacto)
4. [Corrección aplicada](#-corrección-aplicada)
5. [Lecciones aprendidas](#-lecciones-aprendidas)

---

## 🕵️ Qué pasó

En el repositorio viejo se detectó que el archivo **`.env.example`** había sido usado, por error, como si fuera un **`.env` real de producción**. Como consecuencia, credenciales reales quedaron versionadas dentro del historial de Git.

> ⚠️ El repositorio era **privado** y no había evidencia de acceso externo. Aun así, las credenciales se trataron como **comprometidas**, siguiendo el principio de que *todo secreto versionado debe considerarse filtrado*.

---

## 🚨 Información expuesta

Se identificaron los siguientes datos sensibles en el historial del repositorio:

| Secreto | Descripción |
|---|---|
| `DJANGO_SECRET_KEY` | Clave real de producción de Django |
| `POSTGRES_PASSWORD` | Contraseña del usuario/base de datos de producción |
| `EMAIL_HOST_PASSWORD` | Contraseña del correo SMTP |
| Configuración de entorno | Host, correo y parámetros de despliegue reales en `.env.example` |

> 🚫 **Nota de seguridad:** los valores reales nunca se documentan ni se vuelven a escribir en commits, README o cualquier archivo del repositorio.

---

## 💥 Impacto

| Secreto expuesto | Riesgo potencial |
|---|---|
| 🔑 `DJANGO_SECRET_KEY` | Podía comprometer la confianza sobre sesiones, cookies y datos firmados por Django |
| 🗄️ `POSTGRES_PASSWORD` | Podía permitir acceso a la base de datos si existía conectividad al servidor |
| 📧 `EMAIL_HOST_PASSWORD` | Podía permitir uso no autorizado de la cuenta SMTP |

**Decisión tomada:** aunque el repositorio era privado, se optó por **rotar y limpiar todo** el historial siguiendo buenas prácticas de seguridad, sin asumir riesgo por confianza en la privacidad del repo.

---

## 🛠️ Corrección aplicada

- [x] **1.** Se reemplazaron los valores reales de `.env.example` por *placeholders*.
- [x] **2.** Se agregó documentación de rotación y limpieza de historial.
- [x] **3.** Se rotó `DJANGO_SECRET_KEY` en el `.env` real del servidor.
- [x] **4.** Se revisó el despliegue real: la aplicación corre con **Nginx + Gunicorn**.
- [x] **5.** Se identificó que el servicio correcto es `gunicorn.service`, **no** `amvarmar.service`.
- [x] **6.** Al reiniciar producción apareció un error `Bad Gateway` porque Gunicorn no arrancaba.
- [x] **7.** Se diagnosticó la causa raíz: un problema de **permisos del `.env`**.

### 🔎 Diagnóstico del error

```text
PermissionError: [Errno 13] Permission denied: '/home/ubuntu/amvarmar/amvarmarProduccion/.env'
```

**Causa:** el usuario bajo el cual corre `gunicorn.service` no tenía permisos de lectura sobre el archivo `.env` real.

**Solución aplicada:**

```bash
sudo chown ubuntu:www-data /home/ubuntu/amvarmar/amvarmarProduccion/.env
sudo chmod 640 /home/ubuntu/amvarmar/amvarmarProduccion/.env
sudo systemctl restart gunicorn.service
```

> 💡 `Bad Gateway` normalmente significa que **Nginx responde correctamente**, pero **Gunicorn está caído** o no logra crear el socket — el problema no estaba en Nginx.

### 🧹 Limpieza de historial

Además de la rotación de credenciales, se reescribió el historial de Git con `git filter-repo` desde un clon limpio, y se verificó con `git log -S` que los secretos ya no aparecen en ningún commit antiguo antes de publicar el historial reescrito con force push protegido.

---

## 📚 Lecciones aprendidas

| # | Lección | Aplicación al proyecto nuevo |
|---|---|---|
| 1 | `.env.example` solo debe tener nombres de variables y placeholders | Nunca valores reales, ni siquiera "temporalmente" |
| 2 | El `.env` real **nunca** se commitea | Debe estar siempre en `.gitignore` |
| 3 | Todo secreto que toca Git debe rotarse | Aunque el repositorio sea privado |
| 4 | Documentar el nombre real del servicio systemd | Ej: `gunicorn.service`, no nombres genéricos o supuestos |
| 5 | El troubleshooting de producción debe ser sistemático | Empezar por logs, permisos y verificación del servicio |

---

<div align="center">

**Estado del incidente:** 🟢 Cerrado y verificado

*Este documento no contiene valores reales de credenciales.*

</div>