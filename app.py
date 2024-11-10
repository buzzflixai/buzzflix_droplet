from flask import Flask, request, jsonify
from flask_cors import CORS
import requests
import os
import logging
import sys
import socket
from datetime import datetime, timedelta
from concurrent.futures import ThreadPoolExecutor
from logging.handlers import SysLogHandler
from prisma import Prisma
from prisma.models import Series
from dotenv import load_dotenv

# Chargement des variables d'environnement
load_dotenv()

# Configuration du logging avec formatage plus détaillé
logger = logging.getLogger("buzzflix-server")
logger.setLevel(logging.INFO)

# Format de log plus détaillé
log_format = '%(asctime)s [%(levelname)s] %(message)s - {%(pathname)s:%(lineno)d}'
formatter = logging.Formatter(log_format)

# Handler pour syslog
try:
    syslog = SysLogHandler(address='/dev/log', facility=SysLogHandler.LOG_LOCAL0)
    syslog.setFormatter(formatter)
    logger.addHandler(syslog)
except (OSError, socket.error):
    logger.warning("Syslog non disponible, utilisation du stdout uniquement")

# Handler pour stdout
stdout = logging.StreamHandler(sys.stdout)
stdout.setFormatter(formatter)
logger.addHandler(stdout)

app = Flask(__name__)
CORS(app)
executor = ThreadPoolExecutor(max_workers=10)
prisma = Prisma()

def log_series_info(series: Series, message: str):
    """Fonction utilitaire pour logger les informations de la série"""
    subscription_info = series.user.subscription
    plan_info = subscription_info.plan if subscription_info else None
    
    logger.info(f"""
    {message}
    ├── Series ID: {series.id}
    ├── User ID: {series.userId}
    ├── Theme: {series.theme}
    ├── Status: {series.status}
    ├── Frequency: {series.frequency} videos/week
    ├── Subscription Plan: {plan_info.name if plan_info else 'No Plan'}
    └── Destination: {series.destinationType}
    """)

async def verify_series(series_id: str):
    """Vérifie la série et l'abonnement de l'utilisateur"""
    logger.info(f"🔍 Vérification de la série: {series_id}")
    
    try:
        # Récupérer la série avec les informations de l'utilisateur et de l'abonnement
        series = await prisma.series.find_unique(
            where={
                'id': series_id
            },
            include={
                'user': {
                    'include': {
                        'subscription': {
                            'include': {
                                'plan': True
                            }
                        }
                    }
                }
            }
        )

        if not series:
            logger.error(f"❌ Série non trouvée: {series_id}")
            return None, "Series not found"

        log_series_info(series, "✅ Série trouvée:")

        if not series.user.subscription or not series.user.subscription.plan:
            logger.error(f"❌ Pas d'abonnement actif pour l'utilisateur: {series.userId}")
            return None, "No active subscription"

        logger.info(f"✅ Plan vérifié: {series.user.subscription.plan.name}")
        return series, None

    except Exception as e:
        logger.error(f"❌ Erreur lors de la vérification de la série: {str(e)}", exc_info=True)
        return None, str(e)

async def schedule_future_videos(series_id: str, frequency: int):
    """Planifie les futures vidéos pour la semaine"""
    logger.info(f"📅 Planification des futures vidéos pour la série: {series_id}")
    try:
        days_between_videos = 7 / frequency
        next_date = datetime.utcnow()
        scheduled_videos = []

        for i in range(frequency):
            next_date = next_date + timedelta(days=days_between_videos)
            
            # Créer une nouvelle vidéo
            video = await prisma.video.create({
                'data': {
                    'seriesId': series_id,
                    'status': 'pending'
                }
            })

            # Créer la planification
            schedule = await prisma.publicationschedule.create({
                'data': {
                    'seriesId': series_id,
                    'videoId': video.id,
                    'scheduledTime': next_date,
                    'platform': 'tiktok',
                    'status': 'scheduled'
                }
            })
            
            scheduled_videos.append({
                'video_id': video.id,
                'scheduled_time': next_date.isoformat()
            })
            
            logger.info(f"""
            📽️ Vidéo planifiée:
            ├── Video ID: {video.id}
            ├── Schedule ID: {schedule.id}
            └── Date prévue: {next_date.isoformat()}
            """)

        return scheduled_videos
    except Exception as e:
        logger.error(f"❌ Erreur lors de la planification des vidéos: {str(e)}", exc_info=True)
        return False

def trigger_lambda(payload):
    """Déclenche la génération de vidéo via Lambda"""
    try:
        logger.info(f"""
        🚀 Déclenchement de Lambda:
        ├── Series ID: {payload.get('series_id')}
        ├── Video ID: {payload.get('video_id')}
        └── Theme: {payload.get('theme')}
        """)
        
        response = requests.post(
            os.getenv('AWS_LAMBDA_ENDPOINT'),
            json=payload,
            headers={'Content-Type': 'application/json'},
            timeout=1
        )
        
        logger.info("✅ Lambda déclenché avec succès (timeout attendu)")
        
    except requests.exceptions.Timeout:
        logger.info("⏱️ Lambda timeout (comportement normal)")
    except Exception as e:
        logger.error(f"❌ Erreur lors du déclenchement de Lambda: {str(e)}", exc_info=True)

@app.before_first_request
async def connect_db():
    """Initialise la connexion à la base de données"""
    logger.info("🔌 Connexion à la base de données...")
    try:
        await prisma.connect()
        logger.info("✅ Connexion à la base de données établie")
    except Exception as e:
        logger.error(f"❌ Erreur de connexion à la base de données: {str(e)}", exc_info=True)
        raise

@app.route('/create_series', methods=['POST'])
async def create_series():
    try:
        data = request.json
        series_id = data.get('series_id')
        logger.info(f"📥 Nouvelle requête de création pour la série: {series_id}")

        # Vérifier la série et l'abonnement
        series, error = await verify_series(series_id)
        if error:
            return jsonify({
                'status': 'error',
                'message': error
            }), 404 if error == "Series not found" else 403

        # Préparer le payload Lambda
        lambda_payload = {
            'user_id': series.userId,
            'series_id': series_id,
            'video_id': data['video_id'],
            'destination': data.get('destination', 'email'),
            'theme': data['theme'],
            'voice': data['voice'],
            'language': data['language'],
            'duration_range': data['duration_range']
        }

        # Déclencher la première génération
        executor.submit(trigger_lambda, lambda_payload)

        scheduled_videos = []
        # Planification des vidéos futures pour les abonnements payants
        if series.user.subscription.plan.name != 'FREE':
            logger.info(f"💎 Abonnement premium détecté - Planification de {series.frequency} vidéos par semaine")
            scheduled_videos = await schedule_future_videos(series_id, series.frequency)
        else:
            logger.info("🆓 Abonnement gratuit - Génération unique")

        response_data = {
            'status': 'success',
            'message': 'Video generation started',
            'data': {
                'plan_type': series.user.subscription.plan.name,
                'is_free': series.user.subscription.plan.name == 'FREE',
                'frequency': series.frequency,
                'scheduled_videos': scheduled_videos
            }
        }
        
        logger.info(f"✅ Traitement terminé pour la série {series_id}")
        return jsonify(response_data)

    except Exception as e:
        logger.error(f"❌ Erreur dans create_series: {str(e)}", exc_info=True)
        return jsonify({
            'status': 'error',
            'message': 'Internal server error'
        }), 500

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5000)