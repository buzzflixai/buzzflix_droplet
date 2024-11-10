#!/bin/bash

# Configuration
APP_DIR=/opt/buzzflix_droplet
LOG_DIR=/var/log/buzzflix_droplet
PYTHON_PACKAGES="flask flask-cors requests gunicorn prisma python-dotenv"

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

# Fonction de vérification des erreurs
check_error() {
    if [ $? -ne 0 ]; then
        error "$1"
        exit 1
    fi
}

# Vérification des prérequis
info "Vérification des prérequis..."

# Vérification du fichier .env
if [ ! -f /root/buzzflix_droplet/.env ]; then
    error "Fichier .env manquant dans /root/buzzflix_droplet/"
    exit 1
fi

# Vérification du fichier app.py
if [ ! -f /root/buzzflix_droplet/app.py ]; then
    error "Fichier app.py manquant dans /root/buzzflix_droplet/"
    exit 1
fi

# Installation des dépendances système
info "Installation des dépendances système..."
apt update && apt install -y python3 python3-pip python3-venv build-essential
check_error "Échec de l'installation des dépendances système"

# Sauvegarde de l'ancienne installation si elle existe
if [ -d "$APP_DIR" ]; then
    warning "Installation existante détectée"
    BACKUP_DIR="${APP_DIR}_backup_$(date +'%Y%m%d_%H%M%S')"
    info "Création d'une sauvegarde dans $BACKUP_DIR"
    cp -r $APP_DIR $BACKUP_DIR
fi

# Arrêt du service existant
if systemctl is-active --quiet buzzflix-droplet; then
    log "Arrêt du service existant..."
    systemctl stop buzzflix-droplet
    check_error "Échec de l'arrêt du service"
fi

# Configuration des répertoires
log "Configuration des répertoires..."
mkdir -p $APP_DIR
mkdir -p $LOG_DIR
check_error "Échec de la création des répertoires"

# Configuration des logs
log "Configuration des logs..."
touch $LOG_DIR/buzzflix-droplet.log
touch $LOG_DIR/buzzflix-droplet.err.log
touch $LOG_DIR/access.log
touch $LOG_DIR/error.log

# Configuration de Python
log "Configuration de l'environnement Python..."
cd $APP_DIR
python3 -m venv venv
source venv/bin/activate
check_error "Échec de la création de l'environnement virtuel"

# Installation des dépendances Python
log "Installation des dépendances Python..."
pip install --upgrade pip
pip install $PYTHON_PACKAGES
check_error "Échec de l'installation des dépendances Python"

# Copie des fichiers
log "Copie des fichiers de l'application..."
cp /root/buzzflix_droplet/app.py $APP_DIR/
cp /root/buzzflix_droplet/.env $APP_DIR/
check_error "Échec de la copie des fichiers"

# Génération du client Prisma si schema.prisma existe
if [ -f "/root/buzzflix_droplet/schema.prisma" ]; then
    log "Copie du schema Prisma..."
    cp /root/buzzflix_droplet/schema.prisma $APP_DIR/
    cd $APP_DIR
    log "Génération du client Prisma..."
    prisma generate
    check_error "Échec de la génération du client Prisma"
fi

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
Environment="PATH=$APP_DIR/venv/bin"
EnvironmentFile=$APP_DIR/.env
ExecStart=$APP_DIR/venv/bin/gunicorn app:app \
    --workers 4 \
    --bind 0.0.0.0:5000 \
    --log-level=debug \
    --access-logfile=$LOG_DIR/access.log \
    --error-logfile=$LOG_DIR/error.log \
    --capture-output \
    --timeout 120
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
chown -R www-data:www-data $LOG_DIR
chmod -R 755 $LOG_DIR
chmod 600 $APP_DIR/.env
check_error "Échec de la configuration des permissions"

# Démarrage du service
log "Démarrage du service..."
systemctl daemon-reload
systemctl enable buzzflix-droplet
systemctl start buzzflix-droplet
check_error "Échec du démarrage du service"

# Vérification du démarrage
sleep 5
if ! systemctl is-active --quiet buzzflix-droplet; then
    error "Le service n'a pas démarré correctement"
    error "Consultez les logs avec: journalctl -u buzzflix-droplet"
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

# Test de l'API
log "Test de l'API..."
sleep 2
if curl -s -o /dev/null -w "%{http_code}" http://localhost:5000; then
    log "API accessible localement"
else
    warning "L'API pourrait ne pas être accessible"
fi

# Affichage des informations finales
log "Installation terminée avec succès!"
echo -e "\n${GREEN}=== Informations importantes ===${NC}"
info "URL de l'API: http://$(curl -s ifconfig.me):5000"
info "Emplacement des fichiers: $APP_DIR"
info "Emplacement des logs: $LOG_DIR"
echo -e "\n${BLUE}=== Commandes utiles ===${NC}"
echo "📋 Voir les logs d'application:  tail -f $LOG_DIR/buzzflix-droplet.log"
echo "📋 Voir les logs d'erreur:       tail -f $LOG_DIR/buzzflix-droplet.err.log"
echo "📋 Voir les logs d'accès:        tail -f $LOG_DIR/access.log"
echo "📋 Voir les logs système:        journalctl -u buzzflix-droplet -f"
echo "📋 Statut du service:           systemctl status buzzflix-droplet"
echo "📋 Redémarrer le service:       systemctl restart buzzflix-droplet"
echo -e "\n${YELLOW}N'oubliez pas de sécuriser votre serveur et de configurer HTTPS!${NC}\n"