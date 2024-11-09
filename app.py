from flask import Flask, request, jsonify
from flask_cors import CORS
import requests
import os
import logging
import sys
from concurrent.futures import ThreadPoolExecutor
from logging.handlers import SysLogHandler

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

@app.route('/create_series', methods=['POST'])
def create_series():
    try:
        data = request.json
        logger.info(f"Received series creation request for series_id: {data.get('series_id')}")

        lambda_payload = {
            'user_id': data['user_id'],
            'series_id': data['series_id'],
            'video_id': data['video_id'],
            'destination': data.get('destination', 'email'),
            'theme': data['theme'],
            'voice': data['voice'],
            'language': data['language'],
            'duration_range': data['duration_range']
        }

        executor.submit(trigger_lambda, lambda_payload)
        
        logger.info(f"Successfully queued video generation for series_id: {data.get('series_id')}")
        return jsonify({
            'status': 'success',
            'message': 'Video generation started'
        })

    except Exception as e:
        logger.error(f"Error in create_series: {str(e)}", exc_info=True)
        return jsonify({
            'status': 'error',
            'message': 'Internal server error'
        }), 500

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5000)