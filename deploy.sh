#!/bin/bash

# Configuration
APP_DIR=/opt/buzzflix_droplet
LOG_DIR=/var/log/buzzflix_droplet
PYTHON_PACKAGES="flask flask-cors requests gunicorn python-dotenv prisma"

# Couleurs pour les logs
GREEN='\033[0;32m'
RED='\033[0;31m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m'

# Fonctions de logging
log() {
    echo -e "${GREEN}[$(date +'%Y-%m-%dT%H:%M:%S%z')] ✅ $1${NC}"
}

error() {
    echo -e "${RED}[$(date +'%Y-%m-%dT%H:%M:%S%z')] ❌ ERROR: $1${NC}"
}

warning() {
    echo -e "${YELLOW}[$(date +'%Y-%m-%dT%H:%M:%S%z')] ⚠️ WARNING: $1${NC}"
}

info() {
    echo -e "${BLUE}[$(date +'%Y-%m-%dT%H:%M:%S%z')] ℹ️ $1${NC}"
}

# Nettoyage complet du service existant
cleanup_service() {
    if systemctl is-active --quiet buzzflix-droplet; then
        systemctl stop buzzflix-droplet
        systemctl disable buzzflix-droplet
    fi
    rm -f /etc/systemd/system/buzzflix-droplet.service
    systemctl daemon-reload
}

# Installation de Node.js (nécessaire pour Prisma)
install_nodejs() {
    if ! command -v node &> /dev/null; then
        log "Installation de Node.js..."
        curl -fsSL https://deb.nodesource.com/setup_18.x | bash -
        apt-get install -y nodejs
        check_error "Installation de Node.js échouée"
    fi
}

# Vérification des erreurs
check_error() {
    if [ $? -ne 0 ]; then
        error "$1"
        exit 1
    fi
}

# Nettoyage initial
cleanup_service
info "Nettoyage de l'installation précédente..."
rm -rf $APP_DIR/*

# Installation des dépendances système
info "Installation des dépendances système..."
apt update && apt install -y python3 python3-pip python3-venv build-essential curl
check_error "Installation des dépendances système échouée"

# Installation de Node.js
install_nodejs

# Création des répertoires
log "Création des répertoires..."
mkdir -p $APP_DIR
mkdir -p $LOG_DIR

# Configuration des logs
log "Configuration des logs..."
touch $LOG_DIR/buzzflix-droplet.log
touch $LOG_DIR/buzzflix-droplet.err.log
chown -R www-data:www-data $LOG_DIR

# Configuration Python
log "Configuration de l'environnement Python..."
cd $APP_DIR
python3 -m venv venv
source venv/bin/activate

# Installation des dépendances Python
log "Installation des dépendances Python..."
pip install --upgrade pip
pip install $PYTHON_PACKAGES
check_error "Installation des dépendances Python échouée"

# Copie des fichiers
log "Copie des fichiers..."
cp /root/buzzflix_droplet/app.py $APP_DIR/
cp /root/buzzflix_droplet/.env $APP_DIR/
cp /root/buzzflix_droplet/schema.prisma $APP_DIR/
check_error "Copie des fichiers échouée"

# Configuration et génération de Prisma
log "Configuration de Prisma..."
cd $APP_DIR

# Initialisation de npm et installation de Prisma
npm init -y
npm install prisma @prisma/client
check_error "Installation de Prisma échouée"

# Génération du client Prisma
log "Génération du client Prisma..."
npx prisma generate
check_error "Génération du client Prisma échouée"

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
    --workers 4 \
    --bind 0.0.0.0:5000 \
    --log-level=debug \
    --access-logfile=$LOG_DIR/buzzflix-droplet.log \
    --error-logfile=$LOG_DIR/buzzflix-droplet.err.log \
    --capture-output
Restart=always
RestartSec=5
StandardOutput=append:$LOG_DIR/buzzflix-droplet.log
StandardError=append:$LOG_DIR/buzzflix-droplet.err.log

[Install]
WantedBy=multi-user.target
EOL

# Configuration des permissions
log "Configuration des permissions..."
chown -R www-data:www-data $APP_DIR
chmod -R 755 $LOG_DIR
chmod 600 $APP_DIR/.env

# Démarrage du service
log "Démarrage du service..."
systemctl daemon-reload
systemctl enable buzzflix-droplet
systemctl start buzzflix-droplet

# Vérification du démarrage
sleep 5
if ! systemctl is-active --quiet buzzflix-droplet; then
    error "Le service n'a pas démarré correctement"
    error "Logs du service:"
    journalctl -u buzzflix-droplet -n 50
    exit 1
fi

# Configuration du firewall
log "Configuration du firewall..."
if ! command -v ufw &> /dev/null; then
    apt install -y ufw
fi
ufw allow ssh
ufw allow 5000
ufw --force enable

# Test du service
log "Test du service..."
sleep 2
response=$(curl -s -o /dev/null -w "%{http_code}" http://localhost:5000 || echo "failed")
if [ "$response" != "failed" ]; then
    log "Service démarré et accessible"
else
    warning "Le service pourrait ne pas être accessible"
    warning "Vérifiez les logs pour plus de détails"
fi

# Affichage des informations finales
echo -e "\n${GREEN}=== Informations de déploiement ===${NC}"
echo -e "${BLUE}URL de l'API:${NC} http://$(curl -s ifconfig.me):5000"
echo -e "${BLUE}Dossier de l'application:${NC} $APP_DIR"
echo -e "${BLUE}Dossier des logs:${NC} $LOG_DIR"
echo -e "\n${YELLOW}=== Commandes utiles ===${NC}"
echo "📋 Logs d'application:    tail -f $LOG_DIR/buzzflix-droplet.log"
echo "📋 Logs d'erreur:         tail -f $LOG_DIR/buzzflix-droplet.err.log"
echo "📋 Status du service:     systemctl status buzzflix-droplet"
echo "📋 Redémarrer:           systemctl restart buzzflix-droplet"
echo "📋 Logs systemd:         journalctl -u buzzflix-droplet -f"