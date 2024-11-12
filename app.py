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
import smtplib
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart


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


class EmailNotifier:
    def __init__(self):
        logger.info("📧 Initialisation du système de notification email")
        self.sender_email = os.getenv('GMAIL_USER')
        self.sender_password = os.getenv('GMAIL_APP_PASSWORD')
        try:
            self._test_connection()
            logger.info("✅ Configuration email validée")
        except Exception as e:
            logger.error(f"""
            ❌ Erreur de configuration email:
            ├── Type: {type(e).__name__}
            └── Message: {str(e)}
            """)

    def _test_connection(self):
        """Teste la connexion SMTP"""
        with smtplib.SMTP_SSL('smtp.gmail.com', 465) as server:
            server.login(self.sender_email, self.sender_password)

    def send_video_notification(self, video_info: dict):
        """Envoie une notification pour une nouvelle vidéo"""
        try:
            logger.info(f"📧 Envoi de notification pour video_id: {video_info.get('video_id')}")
            message = MIMEMultipart()
            message["From"] = self.sender_email
            message["To"] = video_info['user_email']
            message["Subject"] = f"🎉 Your New TikTok Video is Ready! - {video_info.get('theme', 'Untitled')}"

            series_url = f"https://www.buzzflix.ai/dashboard/series/{video_info.get('series_id')}"
            
            body = f"""
            <html>
            <body style="font-family: Arial, sans-serif; max-width: 600px; margin: 0 auto; padding: 20px; color: #333;">
                <div style="text-align: center; background-color: #f8f9fa; padding: 20px; border-radius: 10px;">
                    <h1 style="color: #2d3748; margin-bottom: 20px;">🎬 Your Video is Ready!</h1>
                    <p style="font-size: 16px; line-height: 1.5;">We're excited to let you know that your new TikTok video has been successfully generated and is ready for review!</p>
                </div>

                <div style="margin-top: 30px; background-color: white; padding: 20px; border-radius: 10px; border: 1px solid #e2e8f0;">
                    <h2 style="color: #4a5568; border-bottom: 2px solid #e2e8f0; padding-bottom: 10px;">📊 Video Details</h2>
                    <table style="width: 100%; border-collapse: collapse; margin-top: 15px;">
                        <tr>
                            <td style="padding: 8px 0; color: #718096;"><strong>Theme:</strong></td>
                            <td style="padding: 8px 0;">{video_info.get('theme', 'N/A')}</td>
                        </tr>
                        <tr>
                            <td style="padding: 8px 0; color: #718096;"><strong>Language:</strong></td>
                            <td style="padding: 8px 0;">{video_info.get('language', 'N/A')}</td>
                        </tr>
                        <tr>
                            <td style="padding: 8px 0; color: #718096;"><strong>Platform:</strong></td>
                            <td style="padding: 8px 0;">{video_info.get('destination', 'N/A')}</td>
                        </tr>
                        <tr>
                            <td style="padding: 8px 0; color: #718096;"><strong>Status:</strong></td>
                            <td style="padding: 8px 0;">✅ Complete</td>
                        </tr>
                    </table>
                </div>

                <div style="text-align: center; margin-top: 30px;">
                    <a href="{series_url}" style="background-color: #4a90e2; color: white; padding: 12px 25px; text-decoration: none; border-radius: 5px; font-weight: bold; display: inline-block;">View Your Series Dashboard</a>
                </div>

                <div style="margin-top: 40px; text-align: center; padding-top: 20px; border-top: 1px solid #e2e8f0;">
                    <p style="color: #718096; font-size: 12px;">
                        This is an automated message from Buzzflix.<br>
                        Please do not reply to this email.
                    </p>
                </div>
            </body>
            </html>
            """

            message.attach(MIMEText(body, "html"))

            with smtplib.SMTP_SSL('smtp.gmail.com', 465) as server:
                server.login(self.sender_email, self.sender_password)
                server.send_message(message)
                
            logger.info(f"""
            ✅ Notification envoyée:
            ├── Video ID: {video_info.get('video_id')}
            ├── User Email: {video_info['user_email']}
            └── Theme: {video_info.get('theme')}
            """)
        except Exception as e:
            logger.error(f"""
            ❌ Erreur d'envoi de notification:
            ├── Type: {type(e).__name__}
            ├── Message: {str(e)}
            └── Video ID: {video_info.get('video_id')}
            """)


class VideoAutoScheduler:
    def __init__(self):
        logger.info("🚀 Démarrage du VideoAutoScheduler")
        self.worker = Thread(target=self.check_and_create_videos, daemon=True)
        self.worker.start()
        logger.info("✅ Thread de surveillance démarré")

    def trigger_lambda(self, payload):
        """Déclenche Lambda et envoie une notification email"""
        try:
            logger.info(f"""
            🚀 Envoi à Lambda:
            ├── Video ID: {payload['video_id']}
            ├── Series ID: {payload['series_id']}
            └── Theme: {payload['theme']}
            """)

            # Appel Lambda dans son propre try
            try:
                response = requests.post(
                    os.getenv('AWS_LAMBDA_ENDPOINT'),
                    json=payload,
                    headers={'Content-Type': 'application/json'},
                    timeout=1
                )
                logger.info("✅ Lambda déclenché avec succès")
            except requests.exceptions.Timeout:
                logger.info("⏱️ Lambda timeout (normal)")
            
            # Partie email indépendante de Lambda
            logger.info("📧 Préparation de la notification email...")
            conn = get_db_connection()
            cur = conn.cursor()
            
            try:
                cur.execute("""
                    SELECT u.email
                    FROM "User" u
                    JOIN "Series" s ON s."userId" = u.id
                    WHERE s.id = %s
                """, (payload['series_id'],))
                
                result = cur.fetchone()
                if result is None:
                    logger.error("❌ Email utilisateur non trouvé")
                    return
                    
                user_email = result[0]
                logger.info(f"📧 Email trouvé: {user_email}")

                # Préparer les infos pour la notification
                video_info = {
                    'video_id': payload['video_id'],
                    'series_id': payload['series_id'],
                    'theme': payload['theme'],
                    'language': payload['language'],
                    'destination': payload['destination'],
                    'user_email': user_email
                }

                # Envoyer la notification
                email_notifier.send_video_notification(video_info)
                logger.info("✅ Email de notification envoyé")

            except Exception as e:
                logger.error(f"""
                ❌ Erreur lors de l'envoi de l'email:
                ├── Type: {type(e).__name__}
                ├── Message: {str(e)}
                └── Video ID: {payload['video_id']}
                """)
            finally:
                cur.close()
                conn.close()

        except Exception as e:
            logger.error(f"""
            ❌ Erreur générale dans trigger_lambda:
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





class TikTokTokenRefresher:
    def __init__(self):
        logger.info("🔄 Démarrage du TikTokTokenRefresher")
        self.client_key = os.getenv('AUTH_TIKTOK_ID')
        self.client_secret = os.getenv('AUTH_TIKTOK_SECRET')
        self.worker = Thread(target=self.refresh_tokens_loop, daemon=True)
        self.worker.start()
        logger.info("✅ Thread de refresh des tokens démarré")

    def refresh_token(self, refresh_token: str) -> dict:
        """Rafraîchit un token TikTok"""
        try:
            logger.info("🔄 Tentative de rafraîchissement du token")
            
            response = requests.post(
                "https://open.tiktokapis.com/v2/oauth/token/",
                headers={"Content-Type": "application/x-www-form-urlencoded"},
                data={
                    "client_key": self.client_key,
                    "client_secret": self.client_secret,
                    "grant_type": "refresh_token",
                    "refresh_token": refresh_token
                }
            )
            
            if not response.ok:
                logger.error(f"""
                ❌ Erreur de rafraîchissement:
                ├── Status: {response.status_code}
                └── Response: {response.text}
                """)
                return None

            data = response.json()
            logger.info("✅ Token rafraîchi avec succès")
            return {
                'access_token': data['access_token'],
                'refresh_token': data.get('refresh_token', refresh_token),
                'expires_in': data.get('expires_in', 86400)
            }
            
        except Exception as e:
            logger.error(f"""
            ❌ Erreur lors du rafraîchissement:
            ├── Type: {type(e).__name__}
            └── Message: {str(e)}
            """)
            return None

    def refresh_tokens_loop(self):
        """Boucle principale de rafraîchissement des tokens"""
        while True:
            try:
                conn = get_db_connection()
                cur = conn.cursor()
                
                logger.info("🔍 Recherche des comptes TikTok à rafraîchir...")
                
                # Chercher les comptes dont le token expire dans moins de 6 heures
                cur.execute("""
                    SELECT 
                        id, "userId", "refreshToken", "tokenExpiresAt"
                    FROM "SocialAccount"
                    WHERE platform = 'TIKTOK'
                    AND "tokenExpiresAt" IS NOT NULL
                """)
                
                accounts = cur.fetchall()
                logger.info(f"📊 Trouvé {len(accounts)} comptes à rafraîchir")

                for account in accounts:
                    account_id, user_id, refresh_token, expires_at = account
                    
                    logger.info(f"""
                    🔄 Traitement du compte:
                    ├── Account ID: {account_id}
                    ├── User ID: {user_id}
                    └── Expiration: {expires_at}
                    """)

                    if not refresh_token:
                        logger.error(f"❌ Pas de refresh token pour {account_id}")
                        continue

                    # Rafraîchir le token
                    new_tokens = self.refresh_token(refresh_token)
                    if new_tokens:
                        new_expires_at = datetime.utcnow() + timedelta(seconds=new_tokens['expires_in'])
                        
                        # Mettre à jour la base de données
                        cur.execute("""
                            UPDATE "SocialAccount"
                            SET 
                                "accessToken" = %s,
                                "refreshToken" = %s,
                                "tokenExpiresAt" = %s,
                                "updatedAt" = %s
                            WHERE id = %s
                        """, (
                            new_tokens['access_token'],
                            new_tokens['refresh_token'],
                            new_expires_at,
                            datetime.utcnow(),
                            account_id
                        ))
                        
                        conn.commit()
                        logger.info(f"""
                        ✅ Token mis à jour:
                        ├── Account ID: {account_id}
                        └── Nouvelle expiration: {new_expires_at}
                        """)
                    else:
                        logger.error(f"❌ Échec du rafraîchissement pour {account_id}")

                cur.close()
                conn.close()

            except Exception as e:
                logger.error(f"""
                ❌ Erreur dans la boucle de rafraîchissement:
                ├── Type: {type(e).__name__}
                └── Message: {str(e)}
                """)
            
            # Vérifier toutes les heures
            logger.info("💤 Pause de 1 heure avant prochaine vérification...")
            time.sleep(3600)


# initialisation de l'application
token_refresher = TikTokTokenRefresher()

# Initialiser l'auto-scheduler au démarrage
video_scheduler = VideoAutoScheduler()

# Initialiser le notifieur
email_notifier = EmailNotifier()


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