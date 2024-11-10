#!/bin/bash

# Configuration
APP_DIR=/opt/buzzflix_droplet
LOG_DIR=/var/log/buzzflix_droplet

# Couleurs pour les logs
GREEN='\033[0;32m'
RED='\033[0;31m'
YELLOW='\033[1;33m'
NC='\033[0m'

# Fonctions de logging
log() {
    echo -e "${GREEN}[$(date +'%Y-%m-%dT%H:%M:%S%z')] ✅ $1${NC}"
}

error() {
    echo -e "${RED}[$(date +'%Y-%m-%dT%H:%M:%S%z')] ❌ ERROR: $1${NC}"
}

# Nettoyage complet
log "Nettoyage de l'installation existante..."
systemctl stop buzzflix-droplet 2>/dev/null
systemctl disable buzzflix-droplet 2>/dev/null
rm -rf $APP_DIR
rm -rf $LOG_DIR
rm -f /etc/systemd/system/buzzflix-droplet.service
systemctl daemon-reload

# Installation des dépendances système
log "Installation des dépendances système..."
apt update
apt install -y python3 python3-pip python3-venv nodejs npm

# Création des répertoires
log "Création des répertoires..."
mkdir -p $APP_DIR
mkdir -p $LOG_DIR

# Configuration Python
log "Configuration de l'environnement Python..."
cd $APP_DIR
python3 -m venv venv
source venv/bin/activate
pip install flask flask-cors requests gunicorn prisma python-dotenv

# Copie des fichiers
log "Copie des fichiers..."
cp /root/buzzflix_droplet/app.py $APP_DIR/
cp /root/buzzflix_droplet/.env $APP_DIR/
cp /root/buzzflix_droplet/schema.prisma $APP_DIR/

# Configuration de Prisma
log "Configuration de Prisma..."
cd $APP_DIR
npm init -y
npm install prisma @prisma/client

# Génération du client Prisma
log "Génération du client Prisma..."
npx prisma generate

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
Environment="PATH=$APP_DIR/venv/bin:$APP_DIR/node_modules/.bin:/usr/bin"
Environment="PYTHONPATH=$APP_DIR"
EnvironmentFile=$APP_DIR/.env
ExecStart=$APP_DIR/venv/bin/gunicorn app:app \
    --bind 0.0.0.0:5000 \
    --workers 1 \
    --log-level=debug \
    --access-logfile=$LOG_DIR/access.log \
    --error-logfile=$LOG_DIR/error.log
Restart=always

[Install]
WantedBy=multi-user.target
EOL

# Configuration des permissions
log "Configuration des permissions..."
chown -R www-data:www-data $APP_DIR
chown -R www-data:www-data $LOG_DIR
chmod 600 $APP_DIR/.env

# Démarrage du service
log "Démarrage du service..."
systemctl daemon-reload
systemctl enable buzzflix-droplet
systemctl start buzzflix-droplet

# Configuration du firewall
log "Configuration du firewall..."
ufw allow ssh
ufw allow 5000
ufw --force enable

log "Installation terminée!"
echo -e "${YELLOW}Pour voir les logs:${NC}"
echo "tail -f $LOG_DIR/access.log"
echo "tail -f $LOG_DIR/error.log"
echo "systemctl status buzzflix-droplet"