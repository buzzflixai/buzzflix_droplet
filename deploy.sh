#!/bin/bash

# Configuration
APP_DIR=/opt/buzzflix_droplet
LOG_DIR=/var/log/buzzflix_droplet
GEN_DIR="$APP_DIR/prisma/client"

# Couleurs pour les logs
GREEN='\033[0;32m'
RED='\033[0;31m'
YELLOW='\033[1;33m'
NC='\033[0m'

log() {
    echo -e "${GREEN}[$(date +'%Y-%m-%dT%H:%M:%S%z')] ✅ $1${NC}"
}

error() {
    echo -e "${RED}[$(date +'%Y-%m-%dT%H:%M:%S%z')] ❌ ERROR: $1${NC}"
}

# Nettoyage complet
log "Nettoyage de l'installation existante..."
systemctl stop buzzflix-droplet 2>/dev/null || true
systemctl disable buzzflix-droplet 2>/dev/null || true
rm -rf $APP_DIR
rm -rf $LOG_DIR
rm -f /etc/systemd/system/buzzflix-droplet.service

# Installation des dépendances système
log "Installation des dépendances système..."
apt update
apt install -y python3 python3-pip python3-venv

# Création des répertoires
log "Création des répertoires..."
mkdir -p $APP_DIR
mkdir -p $LOG_DIR
mkdir -p $GEN_DIR
cd $APP_DIR

# Configuration Python
log "Configuration de l'environnement Python..."
python3 -m venv venv
source venv/bin/activate

# Installation des dépendances Python
log "Installation des dépendances Python..."
pip install wheel
pip install flask flask-cors requests gunicorn prisma==0.9.1 python-dotenv

# Copie des fichiers
log "Copie des fichiers..."
cp /root/buzzflix_droplet/app.py $APP_DIR/
cp /root/buzzflix_droplet/.env $APP_DIR/
cp /root/buzzflix_droplet/schema.prisma $APP_DIR/

# Génération et test de Prisma
log "Configuration de Prisma..."
cd $APP_DIR
export PRISMA_CLIENT_ENGINE_TYPE="binary"
export PRISMA_CLIENT_ENGINE_PROTOCOL="graphql"
export PRISMA_GENERATE_DATAPROXY="false"

# Génération du client Prisma
cat > generate_client.py << EOL
from prisma.cli import run_cli
import os

os.environ['PRISMA_CLIENT_ENGINE_TYPE'] = 'binary'
run_cli(['generate'])
EOL

log "Génération du client Prisma..."
python3 generate_client.py || {
    error "Échec de la génération du client Prisma"
    exit 1
}

# Test de l'importation
log "Test de Prisma..."
python3 -c "
from prisma import Prisma
print('Prisma import successful')
" || {
    error "Échec de l'importation Prisma"
    exit 1
}

# Configuration du service
log "Configuration du service systemd..."
cat > /etc/systemd/system/buzzflix-droplet.service << EOL
[Unit]
Description=Buzzflix Droplet Service
After=network.target

[Service]
Type=simple
User=www-data
Group=www-data
WorkingDirectory=$APP_DIR
Environment="PATH=$APP_DIR/venv/bin:/usr/bin"
Environment="PYTHONPATH=$APP_DIR"
Environment="PYTHONUNBUFFERED=1"
Environment="PRISMA_CLIENT_ENGINE_TYPE=binary"
EnvironmentFile=$APP_DIR/.env

ExecStart=/bin/bash -c 'cd $APP_DIR && \
    source venv/bin/activate && \
    exec gunicorn app:app \
    --bind 0.0.0.0:5000 \
    --workers 1 \
    --timeout 120 \
    --log-level debug \
    --access-logfile $LOG_DIR/access.log \
    --error-logfile $LOG_DIR/error.log'

Restart=always
RestartSec=5
StandardOutput=append:$LOG_DIR/output.log
StandardError=append:$LOG_DIR/error.log

[Install]
WantedBy=multi-user.target
EOL

# Configuration des permissions
log "Configuration des permissions..."
chown -R www-data:www-data $APP_DIR
chown -R www-data:www-data $LOG_DIR
chmod -R 755 $APP_DIR
chmod 600 $APP_DIR/.env
chmod -R 644 $LOG_DIR/*.log

# Démarrage du service
log "Démarrage du service..."
systemctl daemon-reload
systemctl enable buzzflix-droplet
systemctl start buzzflix-droplet

# Test du service
log "Test du service..."
sleep 5
if systemctl is-active --quiet buzzflix-droplet; then
    log "Service démarré avec succès!"
    curl -s localhost:5000 > /dev/null 2>&1
    if [ $? -eq 0 ] || [ $? -eq 7 ]; then
        log "API accessible!"
    else
        error "L'API ne répond pas"
        systemctl status buzzflix-droplet
        tail -n 50 $LOG_DIR/error.log
    fi
else
    error "Le service n'a pas démarré. Logs:"
    systemctl status buzzflix-droplet
    tail -n 50 $LOG_DIR/error.log
fi

log "Installation terminée!"
echo -e "\n${YELLOW}Commandes utiles:${NC}"
echo "tail -f $LOG_DIR/access.log    # Logs d'accès"
echo "tail -f $LOG_DIR/error.log     # Logs d'erreur"
echo "systemctl status buzzflix-droplet # Status du service"