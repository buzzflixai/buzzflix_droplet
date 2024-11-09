#!/bin/bash

# Configuration
APP_DIR=/opt/buzzflix_droplet
LOG_DIR=/var/log/buzzflix_droplet

# Couleurs pour les logs
GREEN='\033[0;32m'
RED='\033[0;31m'
NC='\033[0m'

log() {
    echo -e "${GREEN}[$(date +'%Y-%m-%dT%H:%M:%S%z')] $1${NC}"
}

error() {
    echo -e "${RED}[$(date +'%Y-%m-%dT%H:%M:%S%z')] ERROR: $1${NC}"
}

# Arrêt et nettoyage de l'ancienne installation si elle existe
if systemctl is-active --quiet buzzflix-droplet; then
    log "Arrêt du service existant..."
    systemctl stop buzzflix-droplet
fi

# Création des répertoires
log "Création/Nettoyage des répertoires..."
mkdir -p $APP_DIR
mkdir -p $LOG_DIR

# Création des fichiers de log
touch $LOG_DIR/buzzflix-droplet.log
touch $LOG_DIR/buzzflix-droplet.err.log

# Configuration Python
log "Configuration de l'environnement Python..."
cd $APP_DIR
python3 -m venv venv
source venv/bin/activate
pip install flask flask-cors requests gunicorn

# Copie des fichiers depuis le répertoire git
log "Copie des fichiers de l'application..."
cp /root/buzzflix_droplet/app.py $APP_DIR/
cp /root/buzzflix_droplet/.env $APP_DIR/

# Configuration du service systemd
log "Configuration du service..."
cat > /etc/systemd/system/buzzflix-droplet.service << EOL
[Unit]
Description=Buzzflix Droplet Service
After=network.target

[Service]
Type=simple
User=www-data
Group=www-data
WorkingDirectory=$APP_DIR
Environment="PATH=$APP_DIR/venv/bin"
EnvironmentFile=$APP_DIR/.env
ExecStart=$APP_DIR/venv/bin/gunicorn --bind 0.0.0.0:5000 app:app \
    --log-level=debug \
    --access-logfile=$LOG_DIR/buzzflix-droplet.log \
    --error-logfile=$LOG_DIR/buzzflix-droplet.err.log \
    --capture-output
Restart=always
StandardOutput=append:/var/log/buzzflix_droplet/buzzflix-droplet.log
StandardError=append:/var/log/buzzflix_droplet/buzzflix-droplet.err.log

[Install]
WantedBy=multi-user.target
EOL

# Configuration des permissions
log "Configuration des permissions..."
chown -R www-data:www-data $APP_DIR
chown -R www-data:www-data $LOG_DIR
chmod -R 755 $LOG_DIR

# Démarrage du service
log "Démarrage du service..."
systemctl daemon-reload
systemctl enable buzzflix-droplet
systemctl start buzzflix-droplet

# Configuration du firewall
log "Configuration du firewall..."
if ! command -v ufw &> /dev/null; then
    apt install -y ufw
fi

ufw allow ssh
ufw allow 5000
ufw --force enable

log "Installation terminée!"
log "L'API est accessible sur http://$(curl -s ifconfig.me):5000"
log "---------------------------------------------------"
log "Pour voir les logs, utilisez une des commandes suivantes:"
log "- tail -f /var/log/buzzflix_droplet/buzzflix-droplet.log"
log "- journalctl -u buzzflix-droplet -f"
log "- systemctl status buzzflix-droplet"