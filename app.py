import os
import json
import datetime
from functools import wraps
from flask import Flask, request, jsonify, send_file
from pywebpush import webpush, WebPushException
import jwt
import redis

# تهيئة تطبيق Flask - هذا السطر ضروري جداً لـ Vercel
app = Flask(__name__)

# إعداد المتغيرات السرية من بيئة Vercel
app.config['SECRET_KEY'] = os.environ.get('SECRET_KEY', 'default_secret_key')
VAPID_PRIVATE_KEY = os.environ.get('VAPID_PRIVATE_KEY')
VAPID_CLAIMS = {"sub": "mailto:admin@example.com"}

# الاتصال بقاعدة بيانات Vercel KV
KV_URL = os.environ.get('KV_URL')
vercel_kv = redis.from_url(KV_URL, decode_responses=True) if KV_URL else None

BASE_DIR = os.path.dirname(os.path.abspath(__file__))

# --- المسارات ---

@app.route('/')
def index():
    return send_file(os.path.join(BASE_DIR, 'index.html'))

@app.route('/sw.js')
def service_worker():
    return send_file(os.path.join(BASE_DIR, 'sw.js'), mimetype='application/javascript')

# --- دوال المساعدة ---

def token_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        token = request.headers.get('Authorization')
        if not token:
            return jsonify({'message': 'التوكن مفقود!'}), 401
        try:
            token = token.split()[1]
            data = jwt.decode(token, app.config['SECRET_KEY'], algorithms=["HS256"])
            current_user = data['phone']
        except:
            return jsonify({'message': 'التوكن غير صالح!'}), 401
        return f(current_user, *args, **kwargs)
    return decorated

# --- APIs ---

@app.route('/login', methods=['POST'])
def login():
    phone = request.json.get('phone')
    if vercel_kv and vercel_kv.exists(f"emp:{phone}"):
        token = jwt.encode({'phone': phone, 'exp': datetime.datetime.utcnow() + datetime.timedelta(days=7)}, 
                           app.config['SECRET_KEY'], algorithm="HS256")
        return jsonify({'token': token})
    return jsonify({'message': 'رقم الهاتف غير مسجل'}), 401

@app.route('/subscribe', methods=['POST'])
@token_required
def subscribe(current_user):
    sub_info = request.json.get('subscription')
    if vercel_kv:
        vercel_kv.hset(f"emp:{current_user}", "push_sub", json.dumps(sub_info))
        return jsonify({'message': 'تم الحفظ'})
    return jsonify({'error': 'قاعدة البيانات غير متاحة'}), 500

@app.route('/send_notification', methods=['POST'])
@token_required
def send_notification(current_user):
    target = request.json.get('target_phone')
    msg = request.json.get('message')
    data = vercel_kv.hgetall(f"emp:{target}") if vercel_kv else None
    
    if data and "push_sub" in data:
        try:
            webpush(json.loads(data["push_sub"]), json.dumps({"title": "تبليغ إداري", "body": msg}), 
                    VAPID_PRIVATE_KEY, VAPID_CLAIMS)
            return jsonify({'message': '✅ تم الإرسال'})
        except Exception as e:
            return jsonify({'error': f'❌ فشل الإرسال: {str(e)}'}), 500
    return jsonify({'error': 'الموظف غير موجود'}), 404

if __name__ == '__main__':
    app.run()
