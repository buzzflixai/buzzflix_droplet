#!/bin/bash

# Configuration
APP_DIR=/opt/buzzflix_droplet
LOG_DIR=/var/log/buzzflix_droplet
PRISMA_DIR="$APP_DIR/prisma"

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
apt install -y python3 python3-pip python3-venv

# Création des répertoires
log "Création des répertoires..."
mkdir -p $APP_DIR
mkdir -p $LOG_DIR
mkdir -p $PRISMA_DIR

# Configuration Python
log "Configuration de l'environnement Python..."
cd $APP_DIR
python3 -m venv venv
source venv/bin/activate
pip install flask flask-cors requests gunicorn prisma python-dotenv

# Copie des fichiers
log "Copie des fichiers..."
cp /root/buzzflix_droplet/app.py $APP_DIR/
cp /root/buzzflix_droplet/schema.prisma $PRISMA_DIR/
cp /root/buzzflix_droplet/.env $APP_DIR/

# Configuration de Prisma
log "Configuration de Prisma..."
cd $PRISMA_DIR
# Créer un package.json minimal
cat > package.json << EOL
{
  "name": "buzzflix_droplet",
  "version": "1.0.0",
  "private": true
}
EOL

# Installation des dépendances Prisma
npm install @prisma/client prisma

# Copie du schema.prisma vers le bon endroit
cp schema.prisma $PRISMA_DIR

# Génération du client Prisma
log "Génération du client Prisma..."
export PRISMA_CLIENT_ENGINE_TYPE=binary
npx prisma generate

# Configuration des permissions
log "Configuration des permissions..."
chown -R www-data:www-data $APP_DIR
chown -R www-data:www-data $LOG_DIR
chmod -R 755 $APP_DIR
chmod 600 $APP_DIR/.env

# Création d'un script de test
cat > $APP_DIR/test_prisma.py << EOL
from prisma import Prisma
import asyncio

async def test_connection():
    prisma = Prisma()
    try:
        await prisma.connect()
        print("✅ Prisma connection successful!")
        await prisma.disconnect()
    except Exception as e:
        print(f"❌ Error: {e}")
        raise e

if __name__ == "__main__":
    asyncio.run(test_connection())
EOL

# Test de Prisma
log "Test de Prisma..."
cd $APP_DIR
source venv/bin/activate
PYTHONPATH=$APP_DIR python3 test_prisma.py

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
Environment="PYTHONPATH=$APP_DIR:$PRISMA_DIR"
Environment="PYTHONUNBUFFERED=1"
Environment="PRISMA_CLIENT_ENGINE_TYPE=binary"
EnvironmentFile=$APP_DIR/.env

ExecStart=/bin/bash -c 'cd $APP_DIR && \
    source venv/bin/activate && \
    exec gunicorn app:app \
    --bind 0.0.0.0:5000 \
    --workers 1 \
    --log-level debug \
    --access-logfile $LOG_DIR/access.log \
    --error-logfile $LOG_DIR/error.log \
    --capture-output'

Restart=always
RestartSec=5
StandardOutput=append:$LOG_DIR/output.log
StandardError=append:$LOG_DIR/error.log

[Install]
WantedBy=multi-user.target
EOL

# Création des fichiers de log
touch $LOG_DIR/output.log $LOG_DIR/error.log $LOG_DIR/access.log
chown -R www-data:www-data $LOG_DIR
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

log "Installation terminée!"
echo -e "${YELLOW}Pour voir les logs:${NC}"
echo "tail -f $LOG_DIR/access.log"
echo "tail -f $LOG_DIR/error.log"
echo "systemctl status buzzflix-droplet"