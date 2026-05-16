#!/usr/bin/env python3
"""
manage_ecs_servers.py
─────────────────────
Gestión de servidores ECS en Huawei Cloud para todos los TDPs de Integratel.
Lee la configuración desde servers_config.txt y decide encender/apagar
según el día y hora actuales en Lima (UTC-5).

Dependencias:
    pip install huaweicloudsdkcore huaweicloudsdkecs requests
"""

import os
import sys
import json
import argparse
import logging
import requests
from datetime import datetime, timezone, timedelta

# ── Huawei Cloud SDK ──────────────────────────────────────────────────────────
try:
    from huaweicloudsdkcore.auth.credentials import BasicCredentials
    from huaweicloudsdkecs.v2 import EcsClient
    from huaweicloudsdkecs.v2.region.ecs_region import EcsRegion
    from huaweicloudsdkecs.v2 import ListServersDetailsRequest
    from huaweicloudsdkcore.exceptions import exceptions as sdk_exceptions
    SDK_AVAILABLE = True
except ImportError:
    SDK_AVAILABLE = False
    print("⚠ SDK Huawei no disponible. Usando llamadas REST directas.")

# ── Logging ───────────────────────────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S"
)
log = logging.getLogger(__name__)

# ── Zona horaria Lima UTC-5 ───────────────────────────────────────────────────
LIMA_TZ = timezone(timedelta(hours=-5))
DIA_MAP  = {0: "L", 1: "M", 2: "X", 3: "J", 4: "V", 5: "S", 6: "D"}


# ══════════════════════════════════════════════════════════════════════════════
#  Config
# ══════════════════════════════════════════════════════════════════════════════
def load_config(config_path: str) -> list:
    entries = []
    if not os.path.exists(config_path):
        log.error("Archivo de configuración no encontrado: %s", config_path)
        sys.exit(1)
    with open(config_path, encoding="utf-8") as f:
        for lineno, line in enumerate(f, 1):
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            parts = [p.strip() for p in line.split(";")]
            if len(parts) != 6:
                log.warning("Línea %d ignorada (formato incorrecto): %s", lineno, line)
                continue
            tenant, server_id, server_name, dias_raw, time_on, time_off = parts
            entries.append({
                "tenant"     : tenant,
                "server_id"  : server_id,
                "server_name": server_name,
                "dias"       : [d.strip().upper() for d in dias_raw.split(",")],
                "time_on"    : time_on.strip(),
                "time_off"   : time_off.strip(),
            })
    log.info("Configuración cargada: %d servidor(es).", len(entries))
    return entries


def get_active_servers(entries: list, action: str, tenant_filter: str = "ALL") -> list:
    now_lima = datetime.now(LIMA_TZ)
    dia_hoy  = DIA_MAP[now_lima.weekday()]
    hora_hoy = now_lima.strftime("%H:%M")
    log.info("Hora Lima: %s %s — acción: %s", dia_hoy, hora_hoy, action.upper())

    active = []
    for e in entries:
        if tenant_filter != "ALL" and e["tenant"].upper() != tenant_filter.upper():
            continue
        if dia_hoy not in e["dias"]:
            log.info("  ⏭ %s — hoy (%s) no es día activo.", e["server_name"], dia_hoy)
            continue
        if action == "start" and hora_hoy >= e["time_on"]:
            active.append(e)
        elif action == "stop" and hora_hoy >= e["time_off"]:
            active.append(e)

    log.info("Servidores a procesar: %d", len(active))
    return active


# ══════════════════════════════════════════════════════════════════════════════
#  ECS Client
# ══════════════════════════════════════════════════════════════════════════════
class ECSScheduler:

    STATUS_ACTIVE  = "ACTIVE"
    STATUS_SHUTOFF = "SHUTOFF"

    def __init__(self, access_key, secret_key, project_id, region, dry_run=False):
        self.access_key   = access_key
        self.secret_key   = secret_key
        self.project_id   = project_id
        self.region       = region
        self.dry_run      = dry_run
        self.client       = None
        self._token_cache = None
        self._setup_client()

    def _setup_client(self):
        if SDK_AVAILABLE:
            try:
                creds = BasicCredentials(ak=self.access_key, sk=self.secret_key,
                                         project_id=self.project_id)
                self.client = EcsClient.new_builder() \
                    .with_credentials(creds) \
                    .with_region(EcsRegion.value_of(self.region)) \
                    .build()
                log.info("✓ Cliente ECS (SDK) inicializado — región: %s", self.region)
            except Exception as e:
                log.warning("SDK init falló (%s). Usando REST.", e)
                self.client = None

    def _get_token(self):
        if self._token_cache:
            return self._token_cache
        url = f"https://iam.{self.region}.myhuaweicloud.com/v3/auth/tokens"
        payload = {"auth": {"identity": {"methods": ["hw_ak_sk"],
                    "hw_ak_sk": {"access": {"key": self.access_key},
                                 "secret": {"key": self.secret_key}}},
                    "scope": {"project": {"id": self.project_id}}}}
        resp = requests.post(url, json=payload, timeout=30)
        resp.raise_for_status()
        token = resp.headers.get("X-Subject-Token")
        if not token:
            raise ValueError("No se obtuvo X-Subject-Token de IAM")
        self._token_cache = token
        log.info("✓ Token IAM obtenido.")
        return token

    def list_servers(self) -> dict:
        log.info("Listando servidores del proyecto...")
        if SDK_AVAILABLE and self.client:
            resp = self.client.list_servers_details(ListServersDetailsRequest())
            return {s.id: {"name": s.name, "status": s.status}
                    for s in (resp.servers or [])}
        token = self._get_token()
        url   = f"https://ecs.{self.region}.myhuaweicloud.com/v2/{self.project_id}/servers/detail"
        resp  = requests.get(url, headers={"X-Auth-Token": token}, timeout=30)
        resp.raise_for_status()
        return {s["id"]: {"name": s["name"], "status": s["status"]}
                for s in resp.json().get("servers", [])}

    def _action_rest(self, server_id: str, payload: dict):
        token = self._get_token()
        url   = (f"https://ecs.{self.region}.myhuaweicloud.com"
                f"/v2/{self.project_id}/servers/{server_id}/action")
    
        resp  = requests.post(url,
                              headers={"X-Auth-Token": token,
                                       "Content-Type": "application/json"},
                              json=payload, timeout=30)
        resp.raise_for_status()

    def start_server(self, server_id: str, name: str):
        log.info("  ▶ Encendiendo %s...", name)
        if self.dry_run:
            log.info("    [DRY-RUN] omitido."); return
        if SDK_AVAILABLE and self.client:
            from huaweicloudsdkecs.v2 import (BatchStartServersRequest,
                BatchStartServersRequestBody, BatchStartServersOption, ServerId)
            option = BatchStartServersOption(servers=[ServerId(id=server_id)])
            body   = BatchStartServersRequestBody(os_start=option)
            self.client.batch_start_servers(BatchStartServersRequest(body=body))
        else:
            self._action_rest(server_id, {"os-start": {}})
        log.info("    ✓ Solicitud enviada.")

    def stop_server(self, server_id: str, name: str):
        log.info("  ⏹ Apagando %s...", name)
        if self.dry_run:
            log.info("    [DRY-RUN] omitido."); return
        if SDK_AVAILABLE and self.client:
            from huaweicloudsdkecs.v2 import (BatchStopServersRequest,
                BatchStopServersRequestBody, BatchStopServersOption, ServerId)
            option = BatchStopServersOption(servers=[ServerId(id=server_id)], type="SOFT")
            body   = BatchStopServersRequestBody(os_stop=option)
            self.client.batch_stop_servers(BatchStopServersRequest(body=body))
        else:
            self._action_rest(server_id, {"os-stop": {"type": "SOFT"}})
        log.info("    ✓ Solicitud enviada.")

    def process(self, action: str, entries: list) -> dict:
        all_svrs = self.list_servers()
        log.info("Total servidores en proyecto: %d", len(all_svrs))
        results  = {"processed": [], "skipped": [], "errors": []}

        for e in entries:
            sid, name = e["server_id"], e["server_name"]
            if sid not in all_svrs:
                log.warning("  ⚠ %s no encontrado en el proyecto.", name)
                results["errors"].append({"server": name, "reason": "not found"})
                continue
            status = all_svrs[sid]["status"]
            log.info("  [%s] %s — estado: %s", sid[:8], name, status)
            try:
                if action == "start":
                    if status == self.STATUS_ACTIVE:
                        log.info("    ↳ Ya encendido, omitido.")
                        results["skipped"].append(name)
                    else:
                        self.start_server(sid, name)
                        results["processed"].append(name)
                else:
                    if status == self.STATUS_SHUTOFF:
                        log.info("    ↳ Ya apagado, omitido.")
                        results["skipped"].append(name)
                    else:
                        self.stop_server(sid, name)
                        results["processed"].append(name)
            except Exception as ex:
                log.error("    ✗ Error: %s", ex)
                results["errors"].append({"server": name, "reason": str(ex)})

        return results


# ══════════════════════════════════════════════════════════════════════════════
#  Main
# ══════════════════════════════════════════════════════════════════════════════
def parse_args():
    p = argparse.ArgumentParser()
    p.add_argument("--action",  required=True, choices=["start", "stop", "AUTO"])
    p.add_argument("--project", required=True)
    p.add_argument("--region",  default="sa-peru-1")
    p.add_argument("--tenant",  default="ALL")
    p.add_argument("--config",  default="servers_config.txt")
    p.add_argument("--output",  default="results.json")
    p.add_argument("--dry-run", action="store_true")
    return p.parse_args()


def main():
    args   = parse_args()
    action = args.action

    if action == "AUTO":
        now_lima = datetime.now(LIMA_TZ)
        hour     = now_lima.hour
        minute   = now_lima.minute
         # El cron dispara 2 veces al día
        # El config decide qué servidores encender/apagar según time_on/time_off
        action = "start" if hour < 12 else "stop"
        log.info("AUTO → hora Lima: %02d:%02d → %s", hour, minute, action.upper())
        
    print("\n" + "=" * 70)
    print(f"  HUAWEI ECS SCHEDULER — {action.upper()}")
    print(f"  Tenant : {args.tenant}  |  Config: {args.config}")
    print(f"  Región : {args.region}  |  Dry-run: {args.dry_run}")
    print("=" * 70 + "\n")

    ak = os.environ.get("HUAWEI_ACCESS_KEY")
    sk = os.environ.get("HUAWEI_SECRET_KEY")
    if not ak or not sk:
        print("✗ HUAWEI_ACCESS_KEY / HUAWEI_SECRET_KEY no definidos.")
        sys.exit(1)

    results = {"action": action, "tenant": args.tenant,
               "dry_run": args.dry_run, "status": "error",
               "timestamp": datetime.now(LIMA_TZ).isoformat()}
    try:
        entries = load_config(args.config)
        active  = get_active_servers(entries, action, args.tenant)

        if not active:
            print(f"⚠ Sin servidores que procesar para '{action}' hoy.")
            results["status"] = "warning"
            results["message"] = "Sin servidores activos"
            _save(results, args.output)
            sys.exit(0)

        scheduler      = ECSScheduler(ak, sk, args.project, args.region, args.dry_run)
        action_results = scheduler.process(action, active)

        results["status"]  = "success" if not action_results["errors"] else "partial"
        results["summary"] = action_results
        print(f"\n✅ Procesados: {len(action_results['processed'])}"
              f" | Omitidos: {len(action_results['skipped'])}"
              f" | Errores: {len(action_results['errors'])}")

    except Exception as e:
        print(f"\n✗ ERROR CRÍTICO: {e}")
        import traceback; traceback.print_exc()
        results["error"] = str(e)
        _save(results, args.output)
        sys.exit(1)
    finally:
        _save(results, args.output)
    sys.exit(0)


def _save(data, path):
    try:
        with open(path, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, ensure_ascii=False)
        print(f"✓ Resultados guardados en '{path}'")
    except Exception as e:
        print(f"⚠ No se pudo guardar: {e}")


if __name__ == "__main__":
    main()