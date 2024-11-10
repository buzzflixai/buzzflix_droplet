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

# Installation correcte de Node.js
setup_nodejs() {
    log "Configuration de Node.js..."
    # Suppression des installations existantes
    apt-get remove -y nodejs npm
    rm -f /etc/apt/sources.list.d/nodesource.list*
    rm -f /etc/apt/sources.list.d/nodejs.list*
    
    # Installation de la nouvelle version
    curl -fsSL https://deb.nodesource.com/setup_20.x | bash -
    apt-get install -y nodejs
    
    # Installation globale de npm
    apt-get install -y npm
    
    # Mise à jour de npm
    npm install -g npm@latest
    
    # Vérification
    node --version
    npm --version
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
apt install -y python3 python3-pip python3-venv curl

# Installation de Node.js
setup_nodejs

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
npm install @prisma/client prisma

# Génération du client Prisma avec les deux approches
log "Génération du client Prisma..."
npx prisma generate
python3 -m prisma generate

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
Environment="NODE_PATH=$APP_DIR/node_modules"
EnvironmentFile=$APP_DIR/.env
ExecStart=/bin/bash -c 'source $APP_DIR/venv/bin/activate && exec gunicorn app:app \
    --bind 0.0.0.0:5000 \
    --workers 1 \
    --log-level debug \
    --access-logfile $LOG_DIR/access.log \
    --error-logfile $LOG_DIR/error.log \
    --capture-output'
Restart=always
StandardOutput=append:$LOG_DIR/output.log
StandardError=append:$LOG_DIR/error.log

[Install]
WantedBy=multi-user.target
EOL

# Création des fichiers de log
touch $LOG_DIR/output.log $LOG_DIR/error.log $LOG_DIR/access.log

# Configuration des permissions
log "Configuration des permissions..."
chown -R www-data:www-data $APP_DIR
chown -R www-data:www-data $LOG_DIR
chmod -R 644 $LOG_DIR/*.log
chmod 600 $APP_DIR/.env

# Vérification de l'installation prisma
log "Vérification de Prisma..."
cd $APP_DIR
source venv/bin/activate
if ! python3 -c "from prisma import Prisma; print('✅ Prisma import successful')"; then
    error "Échec de l'importation Prisma"
    cat $LOG_DIR/error.log
    exit 1
fi

# Démarrage du service
log "Démarrage du service..."
systemctl daemon-reload
systemctl enable buzzflix-droplet
systemctl start buzzflix-droplet

# Vérification du service
sleep 2
if ! systemctl is-active --quiet buzzflix-droplet; then
    error "Le service n'a pas démarré correctement"
    systemctl status buzzflix-droplet
    cat $LOG_DIR/error.log
    exit 1
fi

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