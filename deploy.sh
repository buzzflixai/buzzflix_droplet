#!/bin/bash

# Configuration
APP_DIR=/opt/buzzflix_droplet
LOG_DIR=/var/log/buzzflix_droplet

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

# Arrêt et nettoyage
log "Nettoyage de l'installation existante..."
systemctl stop buzzflix-droplet 2>/dev/null || true
systemctl disable buzzflix-droplet 2>/dev/null || true
rm -rf $APP_DIR
rm -rf $LOG_DIR
rm -f /etc/systemd/system/buzzflix-droplet.service

# Création des répertoires
log "Création des répertoires..."
mkdir -p $APP_DIR
mkdir -p $LOG_DIR
cd $APP_DIR

# Configuration Python
log "Configuration de l'environnement Python..."
python3 -m venv venv
source venv/bin/activate

# Installation des dépendances
log "Installation des dépendances..."
pip install wheel
pip install flask flask-cors requests gunicorn prisma python-dotenv

# Copie des fichiers
log "Copie des fichiers..."
cp /root/buzzflix_droplet/app.py $APP_DIR/
cp /root/buzzflix_droplet/schema.prisma $APP_DIR/
cp /root/buzzflix_droplet/.env $APP_DIR/

# Configuration et génération de Prisma
log "Configuration de Prisma..."
cd $APP_DIR
python3 -m prisma generate --schema=schema.prisma

# Vérification de Prisma
log "Vérification de l'installation..."
source venv/bin/activate
python3 -c "from prisma import Prisma; print('Prisma import successful')"

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
EnvironmentFile=$APP_DIR/.env

ExecStart=$APP_DIR/venv/bin/gunicorn app:app \
    --bind 0.0.0.0:5000 \
    --workers 1 \
    --worker-class=sync \
    --timeout 120 \
    --log-level debug \
    --access-logfile $LOG_DIR/access.log \
    --error-logfile $LOG_DIR/error.log

Restart=always
RestartSec=5
StandardOutput=append:$LOG_DIR/output.log
StandardError=append:$LOG_DIR/error.log

[Install]
WantedBy=multi-user.target
EOL

# Configuration des logs
log "Configuration des logs..."
touch $LOG_DIR/output.log
touch $LOG_DIR/error.log
touch $LOG_DIR/access.log

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

# Configuration du firewall
log "Configuration du firewall..."
ufw allow ssh
ufw allow 5000
ufw --force enable

# Vérification du service
sleep 2
if systemctl is-active --quiet buzzflix-droplet; then
    log "Service démarré avec succès!"
    log "L'API est accessible sur http://$(curl -s ifconfig.me):5000"
else
    error "Le service n'a pas démarré correctement"
    journalctl -u buzzflix-droplet -n 50
fi

log "Installation terminée!"
echo -e "\n${YELLOW}Commandes utiles:${NC}"
echo "tail -f $LOG_DIR/access.log     # Logs d'accès"
echo "tail -f $LOG_DIR/error.log      # Logs d'erreur"
echo "systemctl status buzzflix-droplet  # Status du service"