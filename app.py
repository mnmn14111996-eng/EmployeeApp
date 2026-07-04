import os
import json
import datetime
from functools import wraps
from flask import Flask, request, jsonify, send_file
from pywebpush import webpush, WebPushException
import jwt
import redis

app = Flask(__name__)

# إعدادات الأمان والمفاتيح (يتم سحبها من Vercel)
app.config['SECRET_KEY'] = os.environ.get('SECRET_KEY', 'default_secret_key')
VAPID_PRIVATE_KEY = os.environ.get('VAPID_PRIVATE_KEY')
VAPID_CLAIMS = {"sub": "mailto:admin@example.com"}

# الاتصال بقاعدة البيانات KV
KV_URL = os.environ.get('KV_URL')
vercel_kv = redis.from_url(KV_URL, decode_responses=True) if KV_URL else None

BASE_DIR = os.path.dirname(os.path.abspath(__file__))

# ----------------- المسارات الأساسية -----------------
@app.route('/')
def index():
    return send_file(os.path.join(BASE_DIR, 'index.html'))

@app.route('/sw.js')
def service_worker():
    return send_file(os.path.join(BASE_DIR, 'sw.js'), mimetype='application/javascript')

# ----------------- نظام حماية المسارات (Middleware) -----------------
def token_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        token = request.headers.get('Authorization')
        if not token:
            return jsonify({'message': 'التوكن مفقود! يرجى تسجيل الدخول.'}), 401
        try:
            token = token.split()[1]
            data = jwt.decode(token, app.config['SECRET_KEY'], algorithms=["HS256"])
            current_user = data['phone']
            current_role = data['role']
        except:
            return jsonify({'message': 'الجلسة انتهت أو غير صالحة!'}), 401
        return f(current_user, current_role, *args, **kwargs)
    return decorated

# ----------------- العمليات الأساسية للمستخدمين -----------------
@app.route('/register', methods=['POST'])
def register():
    """تسجيل مستخدم جديد برقم الهاتف كمفتاح أساسي"""
    data = request.json
    phone = data.get('phone')
    name = data.get('name')
    
    if not phone or not name:
        return jsonify({'message': 'الاسم ورقم الهاتف مطلوبان'}), 400

    user_key = f"emp:{phone}"
    
    if vercel_kv:
        if vercel_kv.exists(user_key):
            return jsonify({'message': 'رقم الهاتف هذا مسجل مسبقاً!'}), 400
            
        # أول شخص يسجل بالموقع يأخذ صلاحية أدمن مؤقتاً للتجربة
        is_first_user = len(list(vercel_kv.scan_iter("emp:*"))) == 0
        role = 'admin' if is_first_user else 'employee'

        vercel_kv.hset(user_key, mapping={"phone": phone, "name": name, "role": role})
        return jsonify({'message': 'تم التسجيل بنجاح!'}), 201
        
    return jsonify({'error': 'قاعدة البيانات غير متاحة'}), 500

@app.route('/login', methods=['POST'])
def login():
    """تسجيل الدخول برقم الهاتف"""
    phone = request.json.get('phone')
    user_key = f"emp:{phone}"
    
    if vercel_kv and vercel_kv.exists(user_key):
        user_data = vercel_kv.hgetall(user_key)
        role = user_data.get('role', 'employee')
        
        token = jwt.encode({
            'phone': phone, 
            'role': role,
            'exp': datetime.datetime.utcnow() + datetime.timedelta(days=7)
        }, app.config['SECRET_KEY'], algorithm="HS256")
        
        return jsonify({
            'token': token, 
            'role': role, 
            'name': user_data.get('name')
        })
        
    return jsonify({'message': 'رقم الهاتف غير مسجل في النظام'}), 401

# ----------------- الإشعارات -----------------
@app.route('/subscribe', methods=['POST'])
@token_required
def subscribe(current_user, current_role):
    sub_info = request.json.get('subscription')
    if vercel_kv:
        vercel_kv.hset(f"emp:{current_user}", "push_sub", json.dumps(sub_info))
        return jsonify({'message': 'تم تفعيل الإشعارات'})
    return jsonify({'error': 'خطأ في الخادم'}), 500

@app.route('/send_notification', methods=['POST'])
@token_required
def send_notification(current_user, current_role):
    if current_role != 'admin':
        return jsonify({'message': 'غير مصرح لك بإرسال الإشعارات'}), 403

    target = request.json.get('target_phone')
    msg = request.json.get('message')
    data = vercel_kv.hgetall(f"emp:{target}") if vercel_kv else None
    
    if data and "push_sub" in data:
        try:
            webpush(json.loads(data["push_sub"]), json.dumps({"title": "تبليغ إداري", "body": msg}), 
                    VAPID_PRIVATE_KEY, VAPID_CLAIMS)
            return jsonify({'message': '✅ تم إرسال الإشعار بنجاح'})
        except Exception as e:
            return jsonify({'error': f'❌ فشل الإرسال: {str(e)}'}), 500
    return jsonify({'error': 'الموظف غير موجود أو لم يفعل الإشعارات'}), 404

# ----------------- لوحة تحكم المدير -----------------
@app.route('/admin/users', methods=['GET'])
@token_required
def get_users(current_user, current_role):
    """جلب جميع الموظفين للمدير"""
    if current_role != 'admin':
        return jsonify({'message': 'غير مصرح لك!'}), 403
        
    users = []
    if vercel_kv:
        for key in vercel_kv.scan_iter("emp:*"):
            user_data = vercel_kv.hgetall(key)
            if 'push_sub' in user_data:
                del user_data['push_sub']
            users.append(user_data)
            
    return jsonify({'users': users})

# ----------------- أداة ترحيل البيانات (تستخدم مرة واحدة) -----------------
@app.route('/migrate_db', methods=['GET'])
def migrate_db():
    count = 0
    if vercel_kv:
        for key in vercel_kv.scan_iter("emp:*"):
            user_data = vercel_kv.hgetall(key)
            phone = user_data.get('phone')
            
            if phone and key != f"emp:{phone}":
                new_key = f"emp:{phone}"
                if 'role' not in user_data:
                    user_data['role'] = 'employee'
                    
                vercel_kv.hset(new_key, mapping=user_data)
                vercel_kv.delete(key)
                count += 1
                
        return jsonify({'message': f'تم بنجاح ترحيل وتحديث بيانات {count} موظف للعمل بالنظام الجديد!'})
    return jsonify({'error': 'قاعدة البيانات غير متصلة'})

if __name__ == '__main__':
    app.run()
