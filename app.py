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

# Configuration du logging
logger = logging.getLogger("video-server")
logger.setLevel(logging.INFO)

# Handler pour syslog
try:
    syslog = SysLogHandler(address='/dev/log', facility=SysLogHandler.LOG_LOCAL0)
    formatter = logging.Formatter('%(name)s[%(process)d]: %(levelname)s %(message)s')
    syslog.setFormatter(formatter)
    logger.addHandler(syslog)
except (OSError, socket.error):
    # Fallback pour le développement local où syslog n'est pas disponible
    pass

# Handler pour stdout
stdout = logging.StreamHandler(sys.stdout)
stdout.setFormatter(formatter)
logger.addHandler(stdout)

app = Flask(__name__)
CORS(app)
executor = ThreadPoolExecutor(max_workers=10)
prisma = Prisma()

async def verify_series(series_id: str):
    """Vérifie la série et l'abonnement de l'utilisateur"""
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
            return None, "Series not found"

        if not series.user.subscription or not series.user.subscription.plan:
            return None, "No active subscription"

        return series, None

    except Exception as e:
        logger.error(f"Error verifying series: {str(e)}")
        return None, str(e)

async def schedule_future_videos(series_id: str, frequency: int):
    """Planifie les futures vidéos pour la semaine"""
    try:
        days_between_videos = 7 / frequency
        next_date = datetime.utcnow()

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
            await prisma.publicationschedule.create({
                'data': {
                    'seriesId': series_id,
                    'videoId': video.id,
                    'scheduledTime': next_date,
                    'platform': 'tiktok',
                    'status': 'scheduled'
                }
            })

        return True
    except Exception as e:
        logger.error(f"Error scheduling future videos: {str(e)}")
        return False

def trigger_lambda(payload):
    try:
        logger.info(f"Triggering Lambda for series_id: {payload.get('series_id')}")
        requests.post(
            os.getenv('AWS_LAMBDA_ENDPOINT'),
            json=payload,
            headers={'Content-Type': 'application/json'},
            timeout=1
        )
    except requests.exceptions.Timeout:
        logger.info("Lambda triggered (timeout as expected)")
    except Exception as e:
        logger.error(f"Error triggering Lambda: {str(e)}")

@app.before_first_request
async def connect_db():
    await prisma.connect()

@app.route('/create_series', methods=['POST'])
async def create_series():
    try:
        data = request.json
        series_id = data.get('series_id')
        logger.info(f"Received series creation request for series_id: {series_id}")

        # Vérifier la série et l'abonnement
        series, error = await verify_series(series_id)
        if error:
            return jsonify({
                'status': 'error',
                'message': error
            }), 404 if error == "Series not found" else 403

        # Préparer le payload Lambda pour la première vidéo
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

        # Déclencher la génération de la première vidéo
        executor.submit(trigger_lambda, lambda_payload)
        logger.info(f"Triggered first video generation for series {series_id}")

        # Si ce n'est pas un abonnement gratuit, planifier les futures vidéos
        if series.user.subscription.plan.name != 'FREE':
            await schedule_future_videos(series_id, series.frequency)
            logger.info(f"Scheduled future videos for series {series_id} with frequency {series.frequency}")

        return jsonify({
            'status': 'success',
            'message': 'Video generation started',
            'data': {
                'plan_type': series.user.subscription.plan.name,
                'is_free': series.user.subscription.plan.name == 'FREE',
                'frequency': series.frequency
            }
        })

    except Exception as e:
        logger.error(f"Error in create_series: {str(e)}", exc_info=True)
        return jsonify({
            'status': 'error',
            'message': 'Internal server error'
        }), 500

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5000)