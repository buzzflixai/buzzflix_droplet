from flask import Flask, request, jsonify
from flask_cors import CORS
import requests
import os
import logging
import sys
import socket
import uuid
from datetime import datetime, timedelta
from concurrent.futures import ThreadPoolExecutor
from logging.handlers import SysLogHandler
import psycopg2
from psycopg2 import sql
from dotenv import load_dotenv

# Chargement des variables d'environnement
load_dotenv()
db_url = os.getenv('DATABASE_URL')

# Configuration du logging centralisé
logger = logging.getLogger("buzzflix")
logger.setLevel(logging.INFO)

# Format de log unifié avec emojis et informations détaillées
log_format = '%(asctime)s [%(levelname)s] %(message)s'
formatter = logging.Formatter(log_format)

# Un seul handler pour le fichier de log
file_handler = logging.FileHandler('/var/log/buzzflix.log')
file_handler.setFormatter(formatter)
logger.addHandler(file_handler)

app = Flask(__name__)
CORS(app)
executor = ThreadPoolExecutor(max_workers=10)


def get_db_connection():
    """Établit une connexion à la base de données"""
    return psycopg2.connect(db_url)

def verify_series(series_id: str):
    """Vérifie la série et l'abonnement de l'utilisateur"""
    logger.info(f"🔍 Vérification de la série: {series_id}")
    conn = get_db_connection()
    cur = conn.cursor()
    
    try:
        # Requête avec les bons noms de colonnes selon le schéma
        cur.execute("""
            SELECT 
                s.id, 
                s."userId",
                s.theme,
                s.status,
                s.frequency,
                s."destinationType",
                p.name as plan_name,
                s."destinationId",
                s."destinationEmail"
            FROM "Series" s
            JOIN "User" u ON s."userId" = u.id
            LEFT JOIN "Subscription" sub ON u.id = sub."userId"
            LEFT JOIN "Plan" p ON sub."planId" = p.id
            WHERE s.id = %s
            AND sub.status = 'active'
        """, (series_id,))
        
        series = cur.fetchone()
        
        if not series:
            logger.error(f"❌ Série non trouvée: {series_id}")
            return None, "Series not found"
            
        series_info = {
            'id': series[0],
            'userId': series[1],
            'theme': series[2],
            'status': series[3],
            'frequency': series[4],
            'destinationType': series[5],
            'plan_name': series[6],
            'destinationId': series[7],
            'destinationEmail': series[8]
        }
        
        if not series_info['plan_name']:
            logger.error(f"❌ Pas d'abonnement actif pour l'utilisateur: {series_info['userId']}")
            return None, "No active subscription"

        logger.info(f"""
        ✅ Série trouvée:
        ├── Series ID: {series_info['id']}
        ├── User ID: {series_info['userId']}
        ├── Theme: {series_info['theme']}
        ├── Status: {series_info['status']}
        ├── Frequency: {series_info['frequency']} videos/week
        ├── Plan: {series_info['plan_name']}
        └── Destination: {series_info['destinationType']}
        """)
        
        return series_info, None
            
    except Exception as e:
        logger.error(f"❌ Erreur lors de la vérification de la série: {str(e)}", exc_info=True)
        return None, str(e)
    finally:
        cur.close()
        conn.close()

def create_video_and_schedule(series_id: str, scheduled_time: datetime):
    """Crée une nouvelle vidéo et sa planification"""
    conn = get_db_connection()
    cur = conn.cursor()
    
    try:
        # Création de la vidéo avec ID unique
        video_id = str(uuid.uuid4())
        current_time = datetime.utcnow()
        
        cur.execute("""
            INSERT INTO "Video" (
                id, "seriesId", status, "createdAt", "updatedAt"
            )
            VALUES (%s, %s, 'pending', %s, %s)
            RETURNING id
        """, (video_id, series_id, current_time, current_time))
        
        video_id = cur.fetchone()[0]

        # Création de la planification
        schedule_id = str(uuid.uuid4())
        cur.execute("""
            INSERT INTO "PublicationSchedule" (
                id, "seriesId", "videoId", "scheduledTime", platform, status, 
                "createdAt", "updatedAt"
            )
            VALUES (%s, %s, %s, %s, 'tiktok', 'scheduled', %s, %s)
            RETURNING id
        """, (schedule_id, series_id, video_id, scheduled_time, current_time, current_time))
        
        schedule_id = cur.fetchone()[0]
        conn.commit()
        
        return video_id, schedule_id
        
    except Exception as e:
        conn.rollback()
        logger.error(f"❌ Erreur lors de la création vidéo/planning: {str(e)}")
        raise
    finally:
        cur.close()
        conn.close()

def schedule_future_videos(series_id: str, frequency: int):
    """Planifie les futures vidéos pour la semaine"""
    logger.info(f"📅 Planification des futures vidéos pour la série: {series_id}")
    scheduled_videos = []

    try:
        days_between_videos = 7 / frequency
        next_date = datetime.utcnow()

        for i in range(frequency):
            next_date = next_date + timedelta(days=days_between_videos)
            
            # Créer une nouvelle vidéo et sa planification
            video_id, schedule_id = create_video_and_schedule(series_id, next_date)
            
            scheduled_videos.append({
                'video_id': video_id,
                'scheduled_time': next_date.isoformat()
            })
            
            logger.info(f"""
            📽️ Vidéo planifiée:
            ├── Video ID: {video_id}
            ├── Schedule ID: {schedule_id}
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

@app.route('/create_series', methods=['POST'])
def create_series():
    try:
        data = request.json
        series_id = data.get('series_id')
        logger.info(f"📥 Nouvelle requête de création pour la série: {series_id}")

        # Vérifier la série et l'abonnement
        series_info, error = verify_series(series_id)
        if error:
            return jsonify({
                'status': 'error',
                'message': error
            }), 404 if error == "Series not found" else 403

        # Préparer le payload Lambda
        lambda_payload = {
            'user_id': series_info['userId'],
            'series_id': series_id,
            'video_id': data['video_id'],
            'destination': series_info['destinationType'],
            'destination_id': series_info['destinationId'],
            'destination_email': series_info['destinationEmail'],
            'theme': data['theme'],
            'voice': data['voice'],
            'language': data['language'],
            'duration_range': data['duration_range']
        }

        # Déclencher la première génération
        executor.submit(trigger_lambda, lambda_payload)

        scheduled_videos = []
        # Planification des vidéos futures pour les abonnements payants
        if series_info['plan_name'] != 'FREE':
            logger.info(f"💎 Abonnement premium détecté - Planification de {series_info['frequency']} vidéos par semaine")
            scheduled_videos = schedule_future_videos(series_id, series_info['frequency'])
        else:
            logger.info("🆓 Abonnement gratuit - Génération unique")

        response_data = {
            'status': 'success',
            'message': 'Video generation started',
            'data': {
                'plan_type': series_info['plan_name'],
                'is_free': series_info['plan_name'] == 'FREE',
                'frequency': series_info['frequency'],
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