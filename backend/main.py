from pathlib import Path
import subprocess
import socket
import json

from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

BASE_DIR = Path(__file__).resolve().parent
APPS_FILE = BASE_DIR / "apps.json"
DEPLOY_SCRIPT = BASE_DIR / "deploy.sh"

app = FastAPI()
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

class DeployRequest(BaseModel):
    app_name: str


def load_apps() -> dict:
    if not APPS_FILE.exists():
        raise FileNotFoundError("apps.json introuvable")
    with APPS_FILE.open("r", encoding="utf-8") as f:
        return json.load(f)


@app.post("/deploy")
def deploy(
    app_name: str | None = Query(None),
    payload: DeployRequest | None = None,
):
    """Exécute deploy.sh pour lancer Terraform puis Ansible."""
    if payload is not None and payload.app_name:
        app_name = payload.app_name

    if not app_name:
        raise HTTPException(status_code=400, detail="Le paramètre app_name est requis.")

    apps = load_apps()
    if app_name not in apps:
        raise HTTPException(status_code=404, detail=f"Application inconnue : {app_name}")

    if not DEPLOY_SCRIPT.exists():
        raise HTTPException(status_code=500, detail="Le script deploy.sh est introuvable.")

    try:
        result = subprocess.run(
            ["bash", str(DEPLOY_SCRIPT), app_name],
            cwd=str(BASE_DIR),
            capture_output=True,
            text=True,
            timeout=600,
        )

        if result.returncode != 0:
            raise RuntimeError(result.stderr.strip() or result.stdout.strip() or "Erreur inconnue")

        return {"status": "success", "message": f"{app_name} déployé."}

    except subprocess.TimeoutExpired:
        raise HTTPException(status_code=504, detail="Le déploiement a expiré.")
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))


@app.get("/status/{app_name}")
def check_status(app_name: str):
    """Vérifie si l'application est UP ou DOWN."""
    apps = load_apps()
    app_info = apps.get(app_name)
    if not app_info:
        raise HTTPException(status_code=404, detail=f"Application inconnue : {app_name}")

    ip = app_info.get("ip")
    port = int(app_info.get("port", 0))
    if not ip or port <= 0:
        raise HTTPException(status_code=500, detail="Configuration IP/port invalide.")

    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.settimeout(2)
    try:
        result = sock.connect_ex((ip, port))
        return {"status": "UP" if result == 0 else "DOWN"}
    finally:
        sock.close()


@app.get("/apps")
def list_apps():
    """Retourne la liste des applications disponibles."""
    return load_apps()


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
