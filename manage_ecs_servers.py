#!/usr/bin/env python3
"""
manage_ecs_servers.py
─────────────────────
Gestión de servidores ECS en Huawei Cloud para todos los TDPs de Integratel.
Encendido/apagado automático desde Jenkins Pipeline.

Dependencias:
    pip install huaweicloudsdkcore huaweicloudsdkecs requests

Uso desde Jenkins (Jenkinsfile):
    python3 manage_ecs_servers.py \
        --action  start \
        --project <project_id> \
        --region  la-south-2 \
        --filter  cert \
        --tdp-name tdp_sistemas \
        --output  results_tdp_sistemas.json
        [--dry-run]
"""

import os
import sys
import json
import time
import argparse
import logging
import requests       
from datetime import datetime
from typing import Optional

# ── Huawei Cloud SDK ──────────────────────────────────────────────────────────
try:
    from huaweicloudsdkcore.auth.credentials import BasicCredentials
    from huaweicloudsdkecs.v2 import EcsClient
    from huaweicloudsdkecs.v2.region.ecs_region import EcsRegion
    from huaweicloudsdkecs.v2 import (
        ListServersDetailsRequest,
        BatchStartServersRequest,
        BatchStopServersRequest,
        BatchStartServersRequestBody,
        BatchStopServersRequestBody,
        ServerId,
        BatchStartServersOption,
        BatchStopServersOption,
        ShowServerRequest,
    )
    from huaweicloudsdkcore.exceptions import exceptions as sdk_exceptions
    SDK_AVAILABLE = True
except ImportError:
    SDK_AVAILABLE = False
    print("SDK Huawei no disponible. Usando llamadas REST directas.")


# ── Logging ───────────────────────────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S"
)
log = logging.getLogger(__name__)


# ══════════════════════════════════════════════════════════════════════════════
#  Clase principal
# ══════════════════════════════════════════════════════════════════════════════
class ECSScheduler:
    """
    Gestiona el encendido y apagado de servidores ECS en Huawei Cloud.
    Compatible con el patrón existente de los pipelines de Jenkins/Integratel.
    """

    # Estados posibles de un ECS
    STATUS_ACTIVE    = "ACTIVE"     # encendido
    STATUS_SHUTOFF   = "SHUTOFF"    # apagado
    STATUS_ERROR     = "ERROR"
    STATUS_BUILDING  = "BUILD"
    STATUS_REBOOTING = "REBOOT"

    def __init__(self, access_key: str, secret_key: str,
                 project_id: str, region: str, dry_run: bool = False):
        self.access_key = access_key
        self.secret_key = secret_key
        self.project_id = project_id
        self.region     = region
        self.dry_run    = dry_run
        self.client     = None
        self._setup_client()

    def _setup_client(self):
        """Inicializa el cliente ECS (SDK o fallback REST)."""
        if SDK_AVAILABLE:
            try:
                credentials = BasicCredentials(
                    ak=self.access_key,
                    sk=self.secret_key,
                    project_id=self.project_id
                )
                self.client = EcsClient.new_builder() \
                    .with_credentials(credentials) \
                    .with_region(EcsRegion.value_of(self.region)) \
                    .build()
                log.info("✓ Cliente ECS (SDK) inicializado — región: %s", self.region)
            except Exception as e:
                log.warning("SDK init falló (%s). Usando REST.", e)
                self.client = None
        else:
            log.info("✓ Modo REST directo.")

    # ── Listar servidores ─────────────────────────────────────────────────────
    def list_servers(self, name_filter: str = "") -> list:
        """
        Retorna lista de servidores ECS del proyecto.
        Filtra por nombre si se especifica name_filter.
        """
        log.info("Listando servidores (filtro nombre: '%s')...", name_filter or "*")

        servers = []
        try:
            if SDK_AVAILABLE and self.client:
                servers = self._list_servers_sdk(name_filter)
            else:
                servers = self._list_servers_rest(name_filter)
        except Exception as e:
            log.error("Error listando servidores: %s", e)
            raise

        log.info("Servidores encontrados: %d", len(servers))
        for s in servers:
            log.info("  → [%s] %s  estado: %s", s["id"][:8], s["name"], s["status"])

        return servers

    def _list_servers_sdk(self, name_filter: str) -> list:
        req = ListServersDetailsRequest()
        if name_filter:
            req.name = name_filter   # filtro parcial por nombre
        resp = self.client.list_servers_details(req)

        return [
            {
                "id"    : s.id,
                "name"  : s.name,
                "status": s.status,
            }
            for s in (resp.servers or [])
        ]

    def _list_servers_rest(self, name_filter: str) -> list:
        token  = self._get_iam_token()
        url = f"https://ecs.{self.region}.myhuaweicloud.com/v1/{self.project_id}/cloudservers/action"

        params = {"name": name_filter} if name_filter else {}

        resp = requests.get(url, headers={"X-Auth-Token": token}, params=params, timeout=30)
        resp.raise_for_status()
        data = resp.json()

        return [
            {
                "id"    : s["id"],
                "name"  : s["name"],
                "status": s["status"],
            }
            for s in data.get("servers", [])
        ]

    # ── Acción: encender ──────────────────────────────────────────────────────
    def start_servers(self, servers: list) -> dict:
        """Enciende los servidores en estado SHUTOFF."""
        targets = [s for s in servers if s["status"] == self.STATUS_SHUTOFF]

        if not targets:
            log.info("No hay servidores apagados para encender.")
            return self._build_result("start", [], servers)

        log.info("Encendiendo %d servidor(es)...", len(targets))
        self._log_targets(targets)

        if self.dry_run:
            log.info("[DRY-RUN] Se omite llamada real a la API.")
            return self._build_result("start", targets, servers, dry_run=True)

        try:
            if SDK_AVAILABLE and self.client:
                self._start_sdk(targets)
            else:
                self._start_rest(targets)

            self._wait_for_status(targets, self.STATUS_ACTIVE)
            return self._build_result("start", targets, servers)

        except Exception as e:
            log.error("Error encendiendo servidores: %s", e)
            raise

    def _start_sdk(self, targets: list):
        server_ids  = [ServerId(id=s["id"]) for s in targets]
        option      = BatchStartServersOption(servers=server_ids)
        body        = BatchStartServersRequestBody(os_start=option)
        req         = BatchStartServersRequest(body=body)
        self.client.batch_start_servers(req)
        log.info("✓ Solicitud de encendido enviada (SDK).")

    def _start_rest(self, targets: list):
        token   = self._get_iam_token()
        url     = f"https://ecs.{self.region}.myhuaweicloud.com"
        url    += f"/v2/{self.project_id}/servers/action"
        payload = {
            "os-start": {
                "servers": [{"id": s["id"]} for s in targets]
            }
        }
        resp = requests.post(url, headers={"X-Auth-Token": token, "Content-Type": "application/json"},
                             json=payload, timeout=30)
        resp.raise_for_status()
        log.info("✓ Solicitud de encendido enviada (REST).")

    # ── Acción: apagar ────────────────────────────────────────────────────────
    def stop_servers(self, servers: list) -> dict:
        """Apaga los servidores en estado ACTIVE."""
        targets = [s for s in servers if s["status"] == self.STATUS_ACTIVE]

        if not targets:
            log.info("No hay servidores encendidos para apagar.")
            return self._build_result("stop", [], servers)

        log.info("Apagando %d servidor(es)...", len(targets))
        self._log_targets(targets)

        if self.dry_run:
            log.info("[DRY-RUN] Se omite llamada real a la API.")
            return self._build_result("stop", targets, servers, dry_run=True)

        try:
            if SDK_AVAILABLE and self.client:
                self._stop_sdk(targets)
            else:
                self._stop_rest(targets)

            self._wait_for_status(targets, self.STATUS_SHUTOFF)
            return self._build_result("stop", targets, servers)

        except Exception as e:
            log.error("Error apagando servidores: %s", e)
            raise

    def _stop_sdk(self, targets: list):
        server_ids  = [ServerId(id=s["id"]) for s in targets]
        option      = BatchStopServersOption(servers=server_ids, type="SOFT")
        body        = BatchStopServersRequestBody(os_stop=option)
        req         = BatchStopServersRequest(body=body)
        self.client.batch_stop_servers(req)
        log.info("✓ Solicitud de apagado (SOFT) enviada (SDK).")

    def _stop_rest(self, targets: list):
        token   = self._get_iam_token()
        url = f"https://ecs.{self.region}.myhuaweicloud.com/v1/{self.project_id}/cloudservers/action"
        payload = {
            "os-stop": {
                "type"   : "SOFT",
                "servers": [{"id": s["id"]} for s in targets]
            }
        }
        resp = requests.post(url, headers={"X-Auth-Token": token, "Content-Type": "application/json"},
                             json=payload, timeout=30)
        resp.raise_for_status()
        log.info("✓ Solicitud de apagado (SOFT) enviada (REST).")

    # ── IAM Token (fallback REST) ─────────────────────────────────────────────
    def _get_iam_token(self) -> str:
        """Obtiene un X-Auth-Token desde IAM (mismo patrón que los otros pipelines)."""
        if hasattr(self, '_token_cache'):
            return self._token_cache

        url     = f"https://iam.{self.region}.myhuaweicloud.com/v3/auth/tokens"
        payload = {
            "auth": {
                "identity": {
                    "methods"  : ["hw_ak_sk"],
                    "hw_ak_sk" : {
                        "access": {"key": self.access_key},
                        "secret": {"key": self.secret_key}
                    }
                },
                "scope": {
                    "project": {"id": self.project_id}
                }
            }
        }
        resp = requests.post(url, json=payload, timeout=30)
        resp.raise_for_status()
        token = resp.headers.get("X-Subject-Token")
        if not token:
            raise ValueError("No se obtuvo X-Subject-Token de IAM")
        self._token_cache = token
        log.info("✓ Token IAM obtenido.")
        return token

    # ── Polling de estado ─────────────────────────────────────────────────────
    def _wait_for_status(self, targets: list, expected_status: str,
                         max_retries: int = 20, interval: int = 15):
        """Polling hasta confirmar que los servidores alcanzaron el estado esperado."""
        ids   = {s["id"] for s in targets}
        found = set()

        log.info("Esperando estado '%s' para %d servidor(es)...", expected_status, len(ids))

        for attempt in range(1, max_retries + 1):
            time.sleep(interval)
            log.info("  Intento %d/%d...", attempt, max_retries)

            try:
                current = self.list_servers()
                for s in current:
                    if s["id"] in ids and s["status"] == expected_status:
                        found.add(s["id"])
                        log.info("  ✓ %s → %s", s["name"], s["status"])
            except Exception as e:
                log.warning("  Error en polling: %s", e)

            if found >= ids:
                log.info("✓ Todos los servidores en estado '%s'.", expected_status)
                return

        log.warning("⚠ Tiempo de espera agotado. Estado final puede no ser '%s'.", expected_status)

    # ── Helpers ───────────────────────────────────────────────────────────────
    def _log_targets(self, targets: list):
        for s in targets:
            log.info("  → [%s] %s", s["id"][:8], s["name"])

    def _build_result(self, action: str, processed: list,
                      all_servers: list, dry_run: bool = False) -> dict:
        return {
            "action"         : action,
            "dry_run"        : dry_run,
            "timestamp"      : datetime.utcnow().isoformat() + "Z",
            "total_servers"  : len(all_servers),
            "servers_acted"  : len(processed),
            "servers_skipped": len(all_servers) - len(processed),
            "detail"         : [
                {
                    "id"    : s["id"],
                    "name"  : s["name"],
                    "status_before": s["status"],
                    "acted" : s in processed
                }
                for s in all_servers
            ]
        }


# ══════════════════════════════════════════════════════════════════════════════
#  Punto de entrada (invocado desde Jenkins)
# ══════════════════════════════════════════════════════════════════════════════
def parse_args():
    p = argparse.ArgumentParser(description="Huawei ECS Scheduler — Integratel TDPs")
    p.add_argument("--action",     required=True, choices=["start", "stop"])
    p.add_argument("--project",    required=True, help="Huawei project_id")
    p.add_argument("--region",     default="la-south-2")
    p.add_argument("--filter",     default="", help="Filtro parcial por nombre de servidor")
    p.add_argument("--server-ids", default="", help="IDs exactos separados por coma (más seguro que --filter)")
    p.add_argument("--tdp-name",   default="unknown", help="Nombre del TDP (para logs)")
    p.add_argument("--output",     default="results.json", help="Archivo JSON de salida")
    p.add_argument("--dry-run",    action="store_true", help="Solo muestra, no ejecuta")
    return p.parse_args()


def main():
    args = parse_args()

    print("\n" + "=" * 70)
    print(f"  HUAWEI ECS SCHEDULER — {args.action.upper()}")
    print(f"  TDP       : {args.tdp_name}")
    print(f"  Project ID: {args.project}")
    print(f"  Región    : {args.region}")
    print(f"  Filtro    : {args.filter or '(ninguno)'}")
    print(f"  Server IDs: {args.server_ids or '(ninguno)'}")
    print(f"  Dry-run   : {args.dry_run}")
    print("=" * 70 + "\n")

    access_key = os.environ.get("HUAWEI_ACCESS_KEY")
    secret_key = os.environ.get("HUAWEI_SECRET_KEY")

    if not access_key or not secret_key:
        print("✗ ERROR: HUAWEI_ACCESS_KEY / HUAWEI_SECRET_KEY no definidos en el entorno.")
        sys.exit(1)

    results = {
        "tdp_name"  : args.tdp_name,
        "action"    : args.action,
        "project_id": args.project,
        "region"    : args.region,
        "filter"    : args.filter,
        "server_ids": args.server_ids,
        "dry_run"   : args.dry_run,
        "status"    : "error",
        "timestamp" : datetime.utcnow().isoformat() + "Z"
    }

    try:
        scheduler = ECSScheduler(
            access_key=access_key,
            secret_key=secret_key,
            project_id=args.project,
            region=args.region,
            dry_run=args.dry_run
        )

        # ── Obtener servidores por IDs exactos o por filtro de nombre ─────────
        if args.server_ids:
            # Modo seguro: IDs exactos — lista todos y filtra por UUID
            target_ids = [i.strip() for i in args.server_ids.split(",") if i.strip()]
            all_servers = scheduler.list_servers()
            servers = [s for s in all_servers if s["id"] in target_ids]
            log.info("Servidores por ID exacto: %d de %d solicitados encontrados",
                     len(servers), len(target_ids))
            # Alertar si falta algún ID
            found_ids = {s["id"] for s in servers}
            for tid in target_ids:
                if tid not in found_ids:
                    log.warning("  ⚠ ID no encontrado en el proyecto: %s", tid)
        else:
            # Modo filtro por nombre
            servers = scheduler.list_servers(name_filter=args.filter)

        if not servers:
            print(f"⚠ No se encontraron servidores para operar en {args.tdp_name}.")
            results["status"]  = "warning"
            results["message"] = "Sin servidores encontrados"
            _save_results(results, args.output)
            sys.exit(0)

        # Ejecutar acción
        if args.action == "start":
            action_result = scheduler.start_servers(servers)
        else:
            action_result = scheduler.stop_servers(servers)

        results["status"]  = "success"
        results["summary"] = action_result
        print(f"\n✅ Acción '{args.action}' completada para {args.tdp_name}")

    except Exception as e:
        print(f"\n✗ ERROR CRÍTICO en {args.tdp_name}: {e}")
        import traceback; traceback.print_exc()
        results["status"] = "error"
        results["error"]  = str(e)
        _save_results(results, args.output)
        sys.exit(1)

    finally:
        _save_results(results, args.output)

    sys.exit(0)


def _save_results(data: dict, path: str):
    try:
        with open(path, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, ensure_ascii=False)
        print(f"✓ Resultados guardados en '{path}'")
    except Exception as e:
        print(f"⚠ No se pudo guardar resultados: {e}")


if __name__ == "__main__":
    main()