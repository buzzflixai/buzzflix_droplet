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
        logger.info("🚀 Démarrage du VideoAutoScheduler")
        self.worker = Thread(target=self.check_and_create_videos, daemon=True)
        self.worker.start()
        logger.info("✅ Thread de surveillance démarré")

    def trigger_lambda(self, payload):
        try:
            logger.info(f"""
            🚀 Envoi à Lambda:
            ├── Video ID: {payload['video_id']}
            ├── Series ID: {payload['series_id']}
            └── Theme: {payload['theme']}
            """)

            response = requests.post(
                os.getenv('AWS_LAMBDA_ENDPOINT'),
                json=payload,
                headers={'Content-Type': 'application/json'},
                timeout=1
            )
            logger.info("✅ Lambda déclenché avec succès (timeout attendu)")
        except requests.exceptions.Timeout:
            logger.info("⏱️ Lambda timeout (normal)")
        except Exception as e:
            logger.error(f"""
            ❌ Erreur Lambda:
            ├── Video ID: {payload['video_id']}
            ├── Type: {type(e).__name__}
            └── Message: {str(e)}
            """)

    def check_and_create_videos(self):
        """Vérifie périodiquement les séries qui ont besoin d'une nouvelle vidéo"""
        while True:
            try:
                conn = get_db_connection()
                cur = conn.cursor()
                logger.info("🔍 Recherche des séries actives...")

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
                logger.info(f"📊 Trouvé {len(series_list)} séries actives")
                current_time = datetime.utcnow()
                for series in series_list:
                    (series_id, user_id, theme, dest_type, dest_id, dest_email, 
                     voice, language, duration_range, frequency, plan_name, last_video_date) = series

                    logger.info(f"""
                    📝 Analyse de la série {series_id}:
                    ├── Plan: {plan_name}
                    ├── Dernière vidéo: {last_video_date}
                    └── Fréquence: {frequency} vidéos/semaine
                    """)

                    # Calculer quand la prochaine vidéo devrait être créée
                    days_between = 7 / frequency
                    next_video_date = last_video_date + timedelta(days=days_between)

                    if current_time >= next_video_date:
                        # Vérifier d'abord s'il n'y a pas déjà une vidéo en cours
                        cur.execute("""
                            SELECT COUNT(*) FROM "Video"
                            WHERE "seriesId" = %s AND status = 'pending'
                        """, (series_id,))
                        
                        pending_count = cur.fetchone()[0]
                        
                        if pending_count == 0:
                            logger.info(f"⚡ Création d'une nouvelle vidéo pour {series_id}")
                            
                            # Créer une nouvelle vidéo
                            video_id = str(uuid.uuid4())
                            cur.execute("""
                                INSERT INTO "Video" (id, "seriesId", status, "createdAt", "updatedAt")
                                VALUES (%s, %s, 'pending', %s, %s)
                            """, (video_id, series_id, current_time, current_time))

                            conn.commit()
                            logger.info(f"💾 Vidéo créée: {video_id}")

                            # Déclencher Lambda immédiatement
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
                            
                            self.trigger_lambda(lambda_payload)
                            
                            logger.info(f"""
                            ✅ Nouvelle vidéo traitée:
                            ├── Video ID: {video_id}
                            ├── Series ID: {series_id}
                            └── Prochaine vérification dans: {days_between} jours
                            """)
                        else:
                            logger.info(f"⏳ Vidéo déjà en cours pour {series_id}")
                    else:
                        time_to_next = next_video_date - current_time
                        logger.info(f"⌛ Trop tôt pour {series_id}, prochaine vidéo dans {time_to_next}")

                logger.info("✅ Cycle de vérification terminé")

            except Exception as e:
                logger.error(f"""
                ❌ Erreur dans le scheduler:
                ├── Type: {type(e).__name__}
                └── Message: {str(e)}
                """, exc_info=True)
            finally:
                if 'cur' in locals():
                    cur.close()
                if 'conn' in locals():
                    conn.close()
            
            logger.info("💤 Pause de 5 minutes...")
            time.sleep(300)

# Initialiser l'auto-scheduler au démarrage
video_scheduler = VideoAutoScheduler()

@app.route('/create_series', methods=['POST'])
def create_series():
    try:
        data = request.json
        series_id = data.get('series_id')
        video_id = data.get('video_id')  # On s'attend à recevoir l'ID de la vidéo créée par Next.js
        
        logger.info(f"""
        📨 Nouvelle requête create_series:
        ├── Series ID: {series_id}
        └── Video ID: {video_id}
        """)

        conn = get_db_connection()
        cur = conn.cursor()

        try:
            # Vérifier que la vidéo existe et est en statut pending
            cur.execute("""
                SELECT 
                    s."userId", s.theme, s."destinationType", s."destinationId",
                    s."destinationEmail", s.voice, s.language, s."durationRange",
                    v.status
                FROM "Video" v
                JOIN "Series" s ON v."seriesId" = s.id
                WHERE v.id = %s AND s.id = %s
            """, (video_id, series_id))
            
            video = cur.fetchone()
            
            if not video:
                logger.error(f"""
                ❌ Vidéo non trouvée:
                ├── Series ID: {series_id}
                └── Video ID: {video_id}
                """)
                return jsonify({
                    'status': 'error',
                    'message': 'Video not found'
                }), 404

            if video[8] != 'pending':
                logger.error(f"❌ La vidéo {video_id} n'est pas en statut pending")
                return jsonify({
                    'status': 'error',
                    'message': 'Video not in pending status'
                }), 400

            logger.info(f"""
            ✅ Vidéo trouvée:
            ├── Video ID: {video_id}
            ├── Series ID: {series_id}
            ├── Theme: {video[1]}
            └── Status: {video[8]}
            """)

            # Déclencher Lambda
            lambda_payload = {
                'user_id': video[0],
                'series_id': series_id,
                'video_id': video_id,
                'destination': video[2],
                'destination_id': video[3],
                'destination_email': video[4],
                'theme': video[1],
                'voice': video[5],
                'language': video[6],
                'duration_range': video[7]
            }

            video_scheduler.trigger_lambda(lambda_payload)

            logger.info(f"""
            ✅ Traitement initié:
            ├── Series ID: {series_id}
            ├── Video ID: {video_id}
            └── Lambda: déclenché
            """)

            return jsonify({
                'status': 'success',
                'message': 'Video generation started',
                'data': {
                    'video_id': video_id,
                    'series_id': series_id
                }
            })

        finally:
            cur.close()
            conn.close()

    except Exception as e:
        logger.error(f"""
        ❌ Erreur dans create_series:
        ├── Type: {type(e).__name__}
        ├── Message: {str(e)}
        ├── Series ID: {series_id if 'series_id' in locals() else 'N/A'}
        └── Video ID: {video_id if 'video_id' in locals() else 'N/A'}
        """, exc_info=True)
        
        return jsonify({
            'status': 'error',
            'message': 'Internal server error'
        }), 500

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5000)