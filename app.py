from flask import Flask, request, jsonify
from flask_cors import CORS
import requests
import os
import logging
import sys
import json
import uuid
from datetime import datetime, timedelta
from concurrent.futures import ThreadPoolExecutor
from threading import Thread
import time
from dotenv import load_dotenv
import psycopg2

# Configuration
load_dotenv()
db_url = os.getenv('DATABASE_URL')

# Logging
logger = logging.getLogger("buzzflix")
logger.setLevel(logging.INFO)
formatter = logging.Formatter('%(asctime)s [%(levelname)s] %(message)s')
file_handler = logging.FileHandler('/var/log/buzzflix.log')
file_handler.setFormatter(formatter)
logger.addHandler(file_handler)

app = Flask(__name__)
CORS(app)
executor = ThreadPoolExecutor(max_workers=10)

def get_db_connection():
    return psycopg2.connect(db_url)

class VideoAutoScheduler:
    def __init__(self):
        # Démarrage du thread de surveillance
        self.worker = Thread(target=self.check_and_create_videos, daemon=True)
        self.worker.start()

    def check_and_create_videos(self):
        while True:
            try:
                conn = get_db_connection()
                cur = conn.cursor()

                # Vérifier les séries qui nécessitent une nouvelle vidéo
                cur.execute("""
                    SELECT 
                        s.id, s."userId", s.theme, s."destinationType",
                        s."destinationId", s."destinationEmail",
                        s.voice, s.language, s."durationRange", s.frequency,
                        p.name as plan_name,
                        COALESCE(MAX(v."createdAt"), s."createdAt") as last_video_date
                    FROM "Series" s
                    JOIN "User" u ON s."userId" = u.id
                    JOIN "Subscription" sub ON u.id = sub."userId"
                    JOIN "Plan" p ON sub."planId" = p.id
                    LEFT JOIN "Video" v ON s.id = v."seriesId"
                    WHERE s.status = 'active'
                    AND sub.status = 'active'
                    GROUP BY s.id, p.name
                """)
                
                series_list = cur.fetchall()
                current_time = datetime.utcnow()

                for series in series_list:
                    (series_id, user_id, theme, dest_type, dest_id, dest_email, 
                     voice, language, duration_range, frequency, plan_name, last_video_date) = series

                    # Calculer quand la prochaine vidéo devrait être créée
                    days_between = 7 / frequency
                    next_video_date = last_video_date + timedelta(days=days_between)

                    # Si c'est l'heure de créer une nouvelle vidéo
                    if current_time >= next_video_date:
                        # Vérifier qu'il n'y a pas déjà une vidéo en cours
                        cur.execute("""
                            SELECT COUNT(*) FROM "Video"
                            WHERE "seriesId" = %s AND status = 'pending'
                        """, (series_id,))
                        
                        pending_count = cur.fetchone()[0]
                        
                        if pending_count == 0:
                            # Créer une nouvelle vidéo
                            video_id = str(uuid.uuid4())
                            cur.execute("""
                                INSERT INTO "Video" (id, "seriesId", status, "createdAt", "updatedAt")
                                VALUES (%s, %s, 'pending', %s, %s)
                            """, (video_id, series_id, current_time, current_time))

                            conn.commit()

                            # Préparer le payload Lambda
                            lambda_payload = {
                                'user_id': user_id,
                                'series_id': series_id,
                                'video_id': video_id,
                                'destination': dest_type,
                                'destination_id': dest_id,
                                'destination_email': dest_email,
                                'theme': theme,
                                'voice': voice,
                                'language': language,
                                'duration_range': duration_range
                            }

                            # Déclencher Lambda
                            self.trigger_lambda(lambda_payload)
                            
                            logger.info(f"""
                            🎥 Nouvelle vidéo créée automatiquement:
                            ├── Series ID: {series_id}
                            ├── Video ID: {video_id}
                            ├── User ID: {user_id}
                            ├── Plan: {plan_name}
                            └── Prochaine vidéo dans: {days_between} jours
                            """)

                cur.close()
                conn.close()

            except Exception as e:
                logger.error(f"❌ Erreur dans le scheduler: {str(e)}", exc_info=True)
            
            # Vérifier toutes les 5 minutes
            time.sleep(300)

    def trigger_lambda(self, payload):
        try:
            requests.post(
                os.getenv('AWS_LAMBDA_ENDPOINT'),
                json=payload,
                headers={'Content-Type': 'application/json'},
                timeout=1
            )
            logger.info(f"✅ Lambda déclenché pour video_id: {payload['video_id']}")
        except requests.exceptions.Timeout:
            logger.info("⏱️ Lambda timeout (normal)")
        except Exception as e:
            logger.error(f"❌ Erreur Lambda: {str(e)}")

# Initialiser l'auto-scheduler au démarrage
video_scheduler = VideoAutoScheduler()

@app.route('/create_series', methods=['POST'])
def create_series():
    try:
        data = request.json
        series_id = data.get('series_id')
        logger.info(f"📥 Nouvelle requête pour la série: {series_id}")

        # Créer la première vidéo
        conn = get_db_connection()
        cur = conn.cursor()

        try:
            # Vérifier la série
            cur.execute("""
                SELECT s."userId", s.theme, s."destinationType", s."destinationId", 
                       s."destinationEmail", s.voice, s.language, s."durationRange"
                FROM "Series" s
                WHERE s.id = %s
            """, (series_id,))
            
            series = cur.fetchone()
            if not series:
                return jsonify({'status': 'error', 'message': 'Series not found'}), 404

            # Créer la première vidéo
            video_id = str(uuid.uuid4())
            current_time = datetime.utcnow()
            
            cur.execute("""
                INSERT INTO "Video" (id, "seriesId", status, "createdAt", "updatedAt")
                VALUES (%s, %s, 'pending', %s, %s)
                RETURNING id
            """, (video_id, series_id, current_time, current_time))
            
            conn.commit()

            # Déclencher Lambda pour la première vidéo
            lambda_payload = {
                'user_id': series[0],
                'series_id': series_id,
                'video_id': video_id,
                'destination': series[2],
                'destination_id': series[3],
                'destination_email': series[4],
                'theme': series[1],
                'voice': series[5],
                'language': series[6],
                'duration_range': series[7]
            }

            video_scheduler.trigger_lambda(lambda_payload)

            return jsonify({
                'status': 'success',
                'message': 'Series started, videos will be generated automatically',
                'data': {'first_video_id': video_id}
            })

        finally:
            cur.close()
            conn.close()

    except Exception as e:
        logger.error(f"❌ Erreur: {str(e)}", exc_info=True)
        return jsonify({'status': 'error', 'message': 'Internal server error'}), 500

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5000)