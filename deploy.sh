#!/bin/bash

# Configuration
APP_DIR=/opt/buzzflix_droplet
LOG_FILE=/var/log/buzzflix.log

BACKUP_DIR=/opt/buzzflix_backup

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

warning() {
    echo -e "${YELLOW}[$(date +'%Y-%m-%dT%H:%M:%S%z')] ⚠️ $1${NC}"
}

# Fonction de vérification d'erreur
check_error() {
    if [ $? -ne 0 ]; then
        error "$1"
        exit 1
    fi
}

# Vérification des variables d'environnement requises
if [ ! -f /root/buzzflix_droplet/.env ]; then
    error "Le fichier .env est manquant dans /root/buzzflix_droplet/"
    exit 1
fi

# Sauvegarde de l'ancienne installation si elle existe
if [ -d "$APP_DIR" ]; then
    warning "Installation existante détectée, sauvegarde en cours..."
    mkdir -p $BACKUP_DIR
    cp -r $APP_DIR $BACKUP_DIR/backup_$(date +%Y%m%d_%H%M%S)
fi

# Nettoyage de l'installation existante
log "Nettoyage de l'installation existante..."
systemctl stop buzzflix-droplet 2>/dev/null || true
systemctl disable buzzflix-droplet 2>/dev/null || true
rm -rf $APP_DIR
rm -f $LOG_FILE
rm -f /etc/systemd/system/buzzflix-droplet.service

# Installation des dépendances système
log "Installation des dépendances système..."
apt update
apt install -y python3 python3-pip python3-venv postgresql-client libpq-dev

# Création des répertoires
log "Création des répertoires..."
mkdir -p $APP_DIR
mkdir -p $LOG_DIR

# Configuration Python
log "Configuration de l'environnement Python..."
cd $APP_DIR
python3 -m venv venv
source venv/bin/activate

log "Installation des dépendances Python..."
pip install --upgrade pip
pip install wheel
pip install flask flask-cors requests "gunicorn[gthread]" \
    psycopg2-binary python-dotenv \
    secure-smtplib email-validator


# Copie des fichiers
log "Copie des fichiers..."
cp /root/buzzflix_droplet/app.py $APP_DIR/
cp /root/buzzflix_droplet/.env $APP_DIR/

# Configuration des logs
log "Configuration des logs..."
touch $LOG_FILE
chmod 644 $LOG_FILE
chown www-data:www-data $LOG_FILE

# Configuration du service systemd
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
Environment="GMAIL_USER=${GMAIL_USER}"
Environment="GMAIL_APP_PASSWORD=${GMAIL_APP_PASSWORD}"
EnvironmentFile=$APP_DIR/.env

ExecStart=$APP_DIR/venv/bin/gunicorn app:app \
    --bind 0.0.0.0:5000 \
    --workers 1 \
    --threads 2 \
    --timeout 120 \
    --log-level debug \
    --capture-output \
    --access-logfile $LOG_FILE \
    --error-logfile $LOG_FILE \
    --worker-class gthread

# Important pour les threads daemon
KillMode=mixed
TimeoutStopSec=5
Restart=always
RestartSec=5
StandardOutput=append:$LOG_FILE
StandardError=append:$LOG_FILE

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

# Test de la connexion à la base de données
log "Test de la connexion à la base de données..."
source $APP_DIR/.env
if ! psql "$DATABASE_URL" -c '\q' 2>/dev/null; then
    error "Impossible de se connecter à la base de données. Vérifiez DATABASE_URL dans .env"
    exit 1
fi

# Test de la connexion SMTP Gmail
log "Test de la connexion SMTP..."
python3 -c "
import smtplib
try:
    with smtplib.SMTP_SSL('smtp.gmail.com', 465) as server:
        server.login('$GMAIL_USER', '$GMAIL_APP_PASSWORD')
    print('SMTP connection successful')
except Exception as e:
    print(f'SMTP connection failed: {str(e)}')
    exit(1)
" || {
    error "Impossible de se connecter au serveur SMTP Gmail. Vérifiez vos identifiants."
    exit 1
}

# Démarrage du service
log "Démarrage du service..."
systemctl daemon-reload
systemctl enable buzzflix-droplet
systemctl start buzzflix-droplet

# Vérification du démarrage
sleep 5
if systemctl is-active --quiet buzzflix-droplet; then
    log "Service démarré avec succès!"
else
    error "Le service n'a pas démarré. Logs:"
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

# Affichage des informations finales
log "Installation terminée avec succès!"
echo -e "\n${GREEN}=== Informations importantes ===${NC}"
echo "API URL: http://$(curl -s ifconfig.me):5000"
echo "App Directory: $APP_DIR"
echo "Logs Directory: $LOG_DIR"

echo -e "\n${YELLOW}=== Commandes utiles ===${NC}"
echo -e "\n${YELLOW}Pour voir les logs en temps réel:${NC}"
echo "tail -f $LOG_FILE"
echo "📋 Status service:   systemctl status buzzflix-droplet"
echo "📋 Redémarrer:      systemctl restart buzzflix-droplet"

echo -e "\n${YELLOW}=== Test de l'API ===${NC}"
echo "curl -X POST http://localhost:5000/create_series \\"
echo "-H \"Content-Type: application/json\" \\"
echo "-d '{
  \"series_id\": \"votre-series-id\",
  \"video_id\": \"video-id\",
  \"theme\": \"test theme\",
  \"voice\": \"alloy\",
  \"language\": \"fr\",
  \"duration_range\": \"30\"
}'"