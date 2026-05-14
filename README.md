# ECS Scheduler — Huawei Cloud / Integratel TDPs

Pipeline Jenkins para encendido y apagado automático de servidores ECS de certificación.

---

## Horario automático

| Evento    | Hora Lima | Cron UTC         |
|-----------|-----------|------------------|
| Encendido | Lun–Vie 07:00 AM | `0 12 * * 1-5` |
| Apagado   | Lun–Vie 00:30 PM | `0 0 * * 2-6`  |

---

## Credenciales a crear en Jenkins

Ve a **Manage Jenkins → Credentials → System → Global** y crea las siguientes:

### Tipo: Secret text

| ID en Jenkins                                | Valor                            |
|----------------------------------------------|----------------------------------|
| `jenkins-huawei-ecs-access-key`              | AK global o del IAM user ECS     |
| `jenkins-huawei-ecs-secret-key`              | SK correspondiente               |
| `huawei-project-id-tdp-sistemas`             | Project ID de TDP_SISTEMAS       |
| `huawei-project-id-tdp-mpay`                 | Project ID de TDP_MPAY           |
| `huawei-project-id-tdp-rpat`                 | Project ID de TDP_RPAT           |
| `huawei-project-id-tdp-bdfscloud`            | Project ID de TDP_BDFSCLOUD      |
| `huawei-project-id-tdp-soluciones`           | Project ID de TDP_SOLUCIONES     |
| `huawei-project-id-tdp-brybill`              | Project ID de TDP_BRYBILL        |
| `huawei-project-id-tdp-planificacioncom`     | Project ID de TDP_PLANIFICACIONCOM |
| `huawei-project-id-tdp-portalcu`             | Project ID de TDP_PORTALCU       |
| `huawei-project-id-tdp-stc-coe-averias`      | Project ID de TDP_STC_COE_AVERIAS |
| `huawei-project-id-tdp-contactcenter`        | Project ID de TDP_CONTACTCENTER  |
| `huawei-project-id-tdp-bi-iias`              | Project ID de TDP_BI_IIAS        |

### Tipo: Username with password

| ID en Jenkins    | Uso                        |
|------------------|----------------------------|
| `bitbucket-token`| Acceso al repo del script  |

---

## Cómo obtener el Project ID de cada TDP

1. Ingresar a **Huawei Cloud Console**
2. Seleccionar la cuenta/organización del TDP
3. Ir a **My Account → API Credentials → Projects**
4. Copiar el `Project ID` de la región `la-south-2`

---

## Ejecución manual

En el pipeline puedes seleccionar:

- **ACTION**: `AUTO` (detecta por hora), `start`, `stop`
- **TDP_TARGET**: `ALL` o un TDP específico
- **SERVER_FILTER**: nombre parcial del servidor (por defecto: `cert`)
- **DRY_RUN**: `true` para ver qué haría sin ejecutar

---

## Estructura del repositorio

```
huawei-ecs-scheduler/
├── Jenkinsfile              ← Pipeline principal
├── manage_ecs_servers.py    ← Script Python de operaciones ECS
└── README.md                ← Este archivo
```

---

## Dependencias Python

```bash
pip install huaweicloudsdkcore huaweicloudsdkecs requests
```
