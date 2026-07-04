import os
import json
import datetime
from functools import wraps
from flask import Flask, request, jsonify, send_file
from pywebpush import webpush, WebPushException
import jwt
import redis

app = Flask(__name__)

# استدعاء المتغيرات السرية من إعدادات Vercel
app.config['SECRET_KEY'] = os.environ.get('SECRET_KEY', 'default_secret')
VAPID_PRIVATE_KEY = os.environ.get('VAPID_PRIVATE_KEY')
VAPID_CLAIMS = {"sub": "mailto:admin@example.com"}

# الاتصال بقاعدة بيانات Vercel KV (Redis)
KV_URL = os.environ.get('KV_URL')
if KV_URL:
    vercel_kv = redis.from_url(KV_URL, decode_responses=True)
else:
    vercel_kv = None

BASE_DIR = os.path.dirname(os.path.abspath(__file__))

# مسارات عرض الواجهة
@app.route('/')
def index():
    return send_file(os.path.join(BASE_DIR, 'index.html'))

@app.route('/sw.js')
def service_worker():
    return send_file(os.path.join(BASE_DIR, 'sw.js'), mimetype='application/javascript')

# طبقة التحقق من التوكن
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
            return jsonify({'message': 'التوكن غير صالح أو منتهي! يرجى تسجيل الدخول مجدداً.'}), 401
        return f(current_user, *args, **kwargs)
    return decorated

# تسجيل الدخول
@app.route('/login', methods=['POST'])
def login():
    data = request.json
    phone = data.get('phone')
    user_key = f"emp:{phone}"
    
    if vercel_kv and vercel_kv.exists(user_key):
        token = jwt.encode({
            'phone': phone,
            'exp': datetime.datetime.utcnow() + datetime.timedelta(days=7)
        }, app.config['SECRET_KEY'], algorithm="HS256")
        return jsonify({'token': token})
        
    return jsonify({'message': 'رقم الهاتف غير مسجل، يرجى التسجيل عبر بوت التليجرام أولاً.'}), 401

# حفظ اشتراك الإشعارات
@app.route('/subscribe', methods=['POST'])
@token_required
def subscribe(current_user):
    sub_info = request.json.get('subscription')
    user_key = f"emp:{current_user}"
    
    if vercel_kv and vercel_kv.exists(user_key):
        vercel_kv.hset(user_key, "push_sub", json.dumps(sub_info))
        return jsonify({'message': 'تم الحفظ بنجاح'})
        
    return jsonify({'error': 'غير مصرح'}), 403

# إرسال إشعار
@app.route('/send_notification', methods=['POST'])
@token_required
def send_notification(current_user):
    target_phone = request.json.get('target_phone')
    message = request.json.get('message')
    target_key = f"emp:{target_phone}"
    
    if not vercel_kv:
        return jsonify({'error': 'قاعدة البيانات غير متصلة'}), 500
        
    employee_data = vercel_kv.hgetall(target_key)
    
    if employee_data and "push_sub" in employee_data:
        push_sub = json.loads(employee_data["push_sub"])
        try:
            webpush(
                subscription_info=push_sub,
                data=json.dumps({"title": "تبليغ إداري جديد", "body": message}),
                vapid_private_key=VAPID_PRIVATE_KEY,
                vapid_claims=VAPID_CLAIMS
            )
            return jsonify({'message': '✅ تم إرسال الإشعار للموظف بنجاح'})
        except WebPushException as ex:
            return jsonify({'error': '❌ فشل إرسال الإشعار', 'details': str(ex)}), 500
            
    return jsonify({'error': '❌ الموظف غير موجود أو لم يقم بتفعيل الإشعارات من الموقع'}), 404
