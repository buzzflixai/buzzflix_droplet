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

    def check_and_create_videos(self):
        logger.info("🔄 Démarrage de la boucle de vérification des vidéos")
        while True:
            try:
                conn = get_db_connection()
                cur = conn.cursor()
                logger.info("🔍 Recherche des séries actives...")

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
                logger.info(f"📊 {len(series_list)} séries actives trouvées")
                current_time = datetime.utcnow()

                for series in series_list:
                    (series_id, user_id, theme, dest_type, dest_id, dest_email, 
                     voice, language, duration_range, frequency, plan_name, last_video_date) = series

                    logger.info(f"""
                    📝 Analyse de la série:
                    ├── ID: {series_id}
                    ├── User: {user_id}
                    ├── Plan: {plan_name}
                    ├── Dernière vidéo: {last_video_date}
                    └── Fréquence: {frequency} vidéos/semaine
                    """)

                    # Calculer la prochaine date
                    days_between = 7 / frequency
                    next_video_date = last_video_date + timedelta(days=days_between)
                    
                    logger.info(f"""
                    ⏰ Calcul des dates:
                    ├── Date actuelle: {current_time}
                    ├── Dernière vidéo: {last_video_date}
                    ├── Intervalle: {days_between} jours
                    ├── Prochaine vidéo prévue: {next_video_date}
                    └── Délai restant: {next_video_date - current_time}
                    """)

                    # Si c'est l'heure
                    if current_time >= next_video_date:
                        logger.info(f"⚡ Heure de créer une nouvelle vidéo pour la série {series_id}")
                        
                        # Vérifier les vidéos en attente
                        cur.execute("""
                            SELECT id, status, "createdAt"
                            FROM "Video"
                            WHERE "seriesId" = %s AND status = 'pending'
                        """, (series_id,))
                        
                        pending_videos = cur.fetchall()
                        logger.info(f"🔍 Vidéos en attente trouvées: {len(pending_videos)}")
                        
                        if not pending_videos:
                            logger.info(f"✨ Création d'une nouvelle vidéo pour la série {series_id}")
                            
                            # Créer la vidéo
                            video_id = str(uuid.uuid4())
                            cur.execute("""
                                INSERT INTO "Video" (id, "seriesId", status, "createdAt", "updatedAt")
                                VALUES (%s, %s, 'pending', %s, %s)
                            """, (video_id, series_id, current_time, current_time))

                            conn.commit()
                            logger.info(f"💾 Vidéo créée en base de données: {video_id}")

                            # Préparer Lambda
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

                            logger.info(f"""
                            📦 Payload Lambda préparé:
                            ├── Video ID: {video_id}
                            ├── Theme: {theme}
                            ├── Language: {language}
                            └── Destination: {dest_type}
                            """)

                            # Déclencher Lambda
                            self.trigger_lambda(lambda_payload)
                            
                            logger.info(f"""
                            ✅ Nouvelle vidéo programmée avec succès:
                            ├── Series ID: {series_id}
                            ├── Video ID: {video_id}
                            ├── User ID: {user_id}
                            ├── Plan: {plan_name}
                            ├── Date de création: {current_time}
                            └── Prochaine vérification dans 5 minutes
                            """)
                        else:
                            logger.info(f"""
                            ⏳ Vidéos en attente existantes:
                            ├── Series ID: {series_id}
                            ├── Nombre: {len(pending_videos)}
                            └── Détails: {pending_videos}
                            """)
                    else:
                        logger.info(f"⏳ Trop tôt pour série {series_id}, prochaine vidéo dans {next_video_date - current_time}")

                logger.info("✅ Cycle de vérification terminé")
                cur.close()
                conn.close()

            except Exception as e:
                logger.error(f"""
                ❌ Erreur dans le scheduler:
                ├── Type: {type(e).__name__}
                ├── Message: {str(e)}
                └── Détails: {getattr(e, 'pgerror', 'N/A')}
                """, exc_info=True)
            
            logger.info("💤 Pause de 5 minutes avant la prochaine vérification...")
            time.sleep(300)

    def trigger_lambda(self, payload):
        try:
            logger.info(f"""
            🚀 Envoi à Lambda:
            ├── Video ID: {payload['video_id']}
            ├── Series ID: {payload['series_id']}
            └── URL: {os.getenv('AWS_LAMBDA_ENDPOINT')}
            """)

            response = requests.post(
                os.getenv('AWS_LAMBDA_ENDPOINT'),
                json=payload,
                headers={'Content-Type': 'application/json'},
                timeout=1
            )
            logger.info(f"""
            ✅ Lambda déclenché:
            ├── Status: {response.status_code if response else 'Timeout (normal)'}
            └── Video ID: {payload['video_id']}
            """)
        except requests.exceptions.Timeout:
            logger.info(f"⏱️ Lambda timeout (normal) pour video_id: {payload['video_id']}")
        except Exception as e:
            logger.error(f"""
            ❌ Erreur Lambda:
            ├── Video ID: {payload['video_id']}
            ├── Type: {type(e).__name__}
            └── Message: {str(e)}
            """)


# Initialiser l'auto-scheduler au démarrage
video_scheduler = VideoAutoScheduler()

@app.route('/create_series', methods=['POST'])
def create_series():
    try:
        data = request.json
        series_id = data.get('series_id')
        logger.info(f"""
        📨 Nouvelle requête create_series reçue:
        ├── Series ID: {series_id}
        ├── Headers: {dict(request.headers)}
        └── Data: {json.dumps(data, indent=2)}
        """)

        # Créer la première vidéo
        conn = get_db_connection()
        cur = conn.cursor()

        try:
            logger.info(f"🔍 Vérification de la série {series_id} dans la base de données...")
            # Vérifier la série avec plus de détails
            cur.execute("""
                SELECT 
                    s."userId", s.theme, s."destinationType", s."destinationId", 
                    s."destinationEmail", s.voice, s.language, s."durationRange",
                    s.frequency, s.status,
                    u.email as user_email,
                    COALESCE(sub.status, 'inactive') as subscription_status,
                    COALESCE(p.name, 'none') as plan_name
                FROM "Series" s
                JOIN "User" u ON s."userId" = u.id
                LEFT JOIN "Subscription" sub ON u.id = sub."userId"
                LEFT JOIN "Plan" p ON sub."planId" = p.id
                WHERE s.id = %s
            """, (series_id,))
            
            series = cur.fetchone()
            if not series:
                logger.error(f"❌ Série {series_id} non trouvée dans la base de données")
                return jsonify({'status': 'error', 'message': 'Series not found'}), 404

            logger.info(f"""
            ✅ Série trouvée:
            ├── User ID: {series[0]}
            ├── User Email: {series[10]}
            ├── Theme: {series[1]}
            ├── Destination: {series[2]}
            ├── Status: {series[9]}
            ├── Subscription: {series[11]}
            ├── Plan: {series[12]}
            └── Fréquence: {series[8]} vidéos/semaine
            """)

            # Vérifier s'il n'y a pas déjà une vidéo en cours
            cur.execute("""
                SELECT id, status, "createdAt"
                FROM "Video"
                WHERE "seriesId" = %s AND status = 'pending'
            """, (series_id,))
            
            existing_videos = cur.fetchall()
            if existing_videos:
                logger.warning(f"""
                ⚠️ Vidéos en attente détectées:
                ├── Series ID: {series_id}
                └── Vidéos: {existing_videos}
                """)
            
            # Créer la première vidéo
            video_id = str(uuid.uuid4())
            current_time = datetime.utcnow()
            
            logger.info(f"""
            📼 Création de la première vidéo:
            ├── Video ID: {video_id}
            ├── Series ID: {series_id}
            ├── Date: {current_time}
            └── Status: pending
            """)
            
            cur.execute("""
                INSERT INTO "Video" (id, "seriesId", status, "createdAt", "updatedAt")
                VALUES (%s, %s, 'pending', %s, %s)
                RETURNING id
            """, (video_id, series_id, current_time, current_time))
            
            conn.commit()
            logger.info("💾 Vidéo enregistrée en base de données")

            # Préparer et déclencher Lambda
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

            logger.info(f"""
            🚀 Préparation du payload Lambda:
            ├── Video ID: {video_id}
            ├── User ID: {series[0]}
            ├── Theme: {series[1]}
            ├── Destination: {series[2]}
            └── Language: {series[6]}
            """)

            video_scheduler.trigger_lambda(lambda_payload)

            response_data = {
                'status': 'success',
                'message': 'Series started, videos will be generated automatically',
                'data': {
                    'first_video_id': video_id,
                    'user_id': series[0],
                    'frequency': series[8],
                    'plan': series[12]
                }
            }

            logger.info(f"""
            ✅ Série initialisée avec succès:
            ├── Series ID: {series_id}
            ├── First Video ID: {video_id}
            ├── Plan: {series[12]}
            └── Response: {json.dumps(response_data, indent=2)}
            """)

            return jsonify(response_data)

        finally:
            logger.info("🔄 Fermeture des connexions DB")
            cur.close()
            conn.close()

    except Exception as e:
        logger.error(f"""
        ❌ Erreur dans create_series:
        ├── Type: {type(e).__name__}
        ├── Message: {str(e)}
        ├── Series ID: {series_id if 'series_id' in locals() else 'N/A'}
        ├── Request Data: {json.dumps(request.json) if request.json else 'N/A'}
        └── Stack Trace: 
        """, exc_info=True)
        
        return jsonify({
            'status': 'error',
            'message': 'Internal server error',
            'error_type': type(e).__name__,
            'error_details': str(e)
        }), 500



if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5000)