#!/usr/bin/env python3
"""
IPTV Transcoder Manager - النظام المتكامل
"""

import os
import sys
import json
import time
import signal
import logging
import subprocess
import threading
from datetime import datetime, timedelta
from flask import Flask, render_template, jsonify, request, session, redirect, url_for
from flask_login import LoginManager, UserMixin, login_user, logout_user, login_required, current_user
from flask_socketio import SocketIO, emit
from flask_cors import CORS
from apscheduler.schedulers.background import BackgroundScheduler
import psutil

# إعدادات المسارات
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
CONFIG_DIR = os.path.join(BASE_DIR, 'etc')
LOG_DIR = os.path.join(BASE_DIR, 'logs')
PROCESS_DIR = os.path.join(BASE_DIR, 'processes')

# تهيئة Flask
app = Flask(__name__)
app.secret_key = os.urandom(24)
app.config['PERMANENT_SESSION_LIFETIME'] = timedelta(hours=24)

# تهيئة SocketIO للاتصال المباشر
socketio = SocketIO(app, cors_allowed_origins="*", async_mode='threading')
CORS(app)

# تهيئة Flask-Login
login_manager = LoginManager()
login_manager.init_app(app)
login_manager.login_view = 'login'

# قاعدة بيانات بسيطة للمستخدمين (في الإنتاج استخدم قاعدة بيانات حقيقية)
class User(UserMixin):
    def __init__(self, id, username, role='user'):
        self.id = id
        self.username = username
        self.role = role

users = {
    '1': User('1', 'admin', 'admin'),
    '2': User('2', 'operator', 'operator')
}

@login_manager.user_loader
def load_user(user_id):
    return users.get(user_id)

# إعداد السجلات
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler(os.path.join(LOG_DIR, 'system.log')),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger('IPTV-Manager')

class ChannelManager:
    """مدير القنوات المركزي"""
    
    def __init__(self):
        self.channels = {}
        self.load_channels()
        self.scheduler = BackgroundScheduler()
        self.setup_scheduler()
        self.scheduler.start()
    
    def load_channels(self):
        """تحميل إعدادات القنوات"""
        try:
            with open(os.path.join(CONFIG_DIR, 'channels.json'), 'r', encoding='utf-8') as f:
                config = json.load(f)
                self.channels = {ch['id']: ch for ch in config['channels']}
                logger.info(f"تم تحميل {len(self.channels)} قناة")
        except Exception as e:
            logger.error(f"خطأ في تحميل القنوات: {e}")
            self.channels = {}
    
    def save_channels(self):
        """حفظ إعدادات القنوات"""
        try:
            config = {
                'last_updated': datetime.now().isoformat(),
                'channels': list(self.channels.values())
            }
            with open(os.path.join(CONFIG_DIR, 'channels.json'), 'w', encoding='utf-8') as f:
                json.dump(config, f, indent=2, ensure_ascii=False)
            
            # إنشاء نسخة احتياطية
            backup_file = os.path.join(BASE_DIR, 'backups', 
                                      f'channels_backup_{datetime.now().strftime("%Y%m%d_%H%M%S")}.json')
            with open(backup_file, 'w', encoding='utf-8') as f:
                json.dump(config, f, indent=2, ensure_ascii=False)
                
            logger.info("تم حفظ إعدادات القنوات")
            return True
        except Exception as e:
            logger.error(f"خطأ في حفظ القنوات: {e}")
            return False
    
    def parse_m3u8(self, m3u8_url):
        """تحليل ملف M3U8 واستخراج القنوات"""
        try:
            import requests
            response = requests.get(m3u8_url, timeout=10)
            lines = response.text.split('\n')
            
            parsed_channels = []
            current_channel = {}
            
            for line in lines:
                line = line.strip()
                if line.startswith('#EXTINF:'):
                    # استخراج اسم القناة
                    parts = line.split(',', 1)
                    if len(parts) > 1:
                        current_channel['name'] = parts[1].strip()
                elif line.startswith('http://'):
                    current_channel['source_url'] = line
                    current_channel['id'] = self.generate_channel_id(line)
                    
                    # إعدادات افتراضية
                    current_channel.update({
                        'enabled': False,
                        'auto_start': False,
                        'transcode': True,
                        'output': {
                            'protocol': 'udp',
                            'address': '239.255.100.1',
                            'port': self.get_next_port(),
                            'bitrate': '800k',
                            'resolution': '720x576'
                        },
                        'schedule': {
                            'daily': True,
                            'start_time': '06:00',
                            'stop_time': '02:00'
                        },
                        'status': 'stopped',
                        'pid': None,
                        'last_started': None,
                        'stats': {
                            'uptime': 0,
                            'cpu_usage': 0,
                            'memory_usage': 0
                        }
                    })
                    
                    parsed_channels.append(current_channel.copy())
                    current_channel = {}
            
            return parsed_channels
        except Exception as e:
            logger.error(f"خطأ في تحليل M3U8: {e}")
            return []
    
    def generate_channel_id(self, url):
        """إنشاء معرف فريد للقناة"""
        import hashlib
        return hashlib.md5(url.encode()).hexdigest()[:8]
    
    def get_next_port(self):
        """الحصول على المنفذ التالي المتاح"""
        used_ports = [ch['output']['port'] for ch in self.channels.values() 
                     if 'output' in ch and 'port' in ch['output']]
        base_port = 6000
        while base_port in used_ports:
            base_port += 1
        return base_port
    
    def start_channel(self, channel_id):
        """تشغيل قناة محددة"""
        if channel_id not in self.channels:
            return {'success': False, 'message': 'القناة غير موجودة'}
        
        channel = self.channels[channel_id]
        
        # التحقق إذا كانت القناة قيد التشغيل بالفعل
        if channel['status'] == 'running':
            return {'success': False, 'message': 'القناة قيد التشغيل بالفعل'}
        
        # بناء أمر FFmpeg
        cmd = self.build_ffmpeg_command(channel)
        
        # تشغيل العملية
        try:
            process = subprocess.Popen(
                cmd,
                shell=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                preexec_fn=os.setsid
            )
            
            # حفظ معلومات العملية
            channel['status'] = 'running'
            channel['pid'] = process.pid
            channel['last_started'] = datetime.now().isoformat()
            
            # حفظ PID في ملف
            pid_file = os.path.join(PROCESS_DIR, f"channel_{channel_id}.pid")
            with open(pid_file, 'w') as f:
                f.write(str(process.pid))
            
            # بدء مراقبة العملية في خيط منفصل
            monitor_thread = threading.Thread(
                target=self.monitor_channel,
                args=(channel_id, process)
            )
            monitor_thread.daemon = True
            monitor_thread.start()
            
            logger.info(f"تم تشغيل القناة {channel['name']} (PID: {process.pid})")
            socketio.emit('channel_status', {
                'channel_id': channel_id,
                'status': 'running',
                'pid': process.pid
            })
            
            return {'success': True, 'pid': process.pid}
            
        except Exception as e:
            logger.error(f"خطأ في تشغيل القناة {channel_id}: {e}")
            return {'success': False, 'message': str(e)}
    
    def build_ffmpeg_command(self, channel):
        """بناء أمر FFmpeg للقناة"""
        cmd_parts = ['ffmpeg']
        
        # إضافة خيارات إعادة الاتصال
        cmd_parts.extend([
            '-reconnect', '1',
            '-reconnect_at_eof', '1',
            '-reconnect_streamed', '1',
            '-reconnect_delay_max', '5'
        ])
        
        # مصدر الفيديو
        cmd_parts.extend(['-i', f"'{channel['source_url']}'"])
        
        # إذا كان التحويل مفعلاً
        if channel.get('transcode', True):
            cmd_parts.extend([
                '-vf', f"scale={channel['output']['resolution']}",
                '-c:v', 'libx264',
                '-preset', 'veryfast',
                '-b:v', channel['output']['bitrate'],
                '-c:a', 'aac',
                '-b:a', '128k',
                '-ac', '2'
            ])
        else:
            cmd_parts.extend(['-c:v', 'copy', '-c:a', 'copy'])
        
        # المخرج
        output = channel['output']
        cmd_parts.extend([
            '-f', 'mpegts',
            f"'udp://{output['address']}:{output['port']}?pkt_size=1316&ttl=32'"
        ])
        
        # إضافة السجلات
        log_file = os.path.join(LOG_DIR, f"channel_{channel['id']}.log")
        cmd_parts.extend(['2>>', f"'{log_file}'"])
        
        return ' '.join(cmd_parts)
    
    def stop_channel(self, channel_id, force=False):
        """إيقاف قناة محددة"""
        if channel_id not in self.channels:
            return {'success': False, 'message': 'القناة غير موجودة'}
        
        channel = self.channels[channel_id]
        
        if channel['status'] != 'running' or not channel['pid']:
            return {'success': False, 'message': 'القناة غير قيد التشغيل'}
        
        try:
            if force:
                os.kill(channel['pid'], signal.SIGKILL)
            else:
                os.kill(channel['pid'], signal.SIGTERM)
            
            # الانتظار قليلاً والتأكد من الإيقاف
            time.sleep(1)
            try:
                os.kill(channel['pid'], 0)  # التحقق إذا كانت العملية لا تزال تعمل
                os.kill(channel['pid'], signal.SIGKILL)  # إذا لا تزال تعمل، قتلها
            except OSError:
                pass  # العملية توقفت بالفعل
            
            # تحديث الحالة
            channel['status'] = 'stopped'
            channel['pid'] = None
            
            # حذف ملف PID
            pid_file = os.path.join(PROCESS_DIR, f"channel_{channel_id}.pid")
            if os.path.exists(pid_file):
                os.remove(pid_file)
            
            logger.info(f"تم إيقاف القناة {channel['name']}")
            socketio.emit('channel_status', {
                'channel_id': channel_id,
                'status': 'stopped'
            })
            
            return {'success': True}
            
        except Exception as e:
            logger.error(f"خطأ في إيقاف القناة {channel_id}: {e}")
            return {'success': False, 'message': str(e)}
    
    def monitor_channel(self, channel_id, process):
        """مراقبة حالة القناة"""
        channel = self.channels[channel_id]
        
        while True:
            time.sleep(10)  # التحقق كل 10 ثواني
            
            # التحقق إذا كانت العملية لا تزال تعمل
            if process.poll() is not None:
                logger.warning(f"القناة {channel['name']} توقفت (كود الخروج: {process.returncode})")
                
                # تحديث الحالة
                channel['status'] = 'stopped'
                channel['pid'] = None
                
                # إشعار الواجهة
                socketio.emit('channel_stopped', {
                    'channel_id': channel_id,
                    'exit_code': process.returncode
                })
                
                # إعادة التشغيل التلقائي إذا مطلوب
                if channel.get('auto_restart', True):
                    time.sleep(5)
                    if channel['enabled']:
                        logger.info(f"إعادة تشغيل القناة {channel['name']} تلقائياً")
                        self.start_channel(channel_id)
                
                break
    
    def setup_scheduler(self):
        """إعداد الجدولة التلقائية"""
        # مهمة تحديث إحصائيات النظام كل دقيقة
        self.scheduler.add_job(
            func=self.update_system_stats,
            trigger='interval',
            seconds=60,
            id='update_stats'
        )
        
        # مهمة تنظيف السجلات القديمة يومياً
        self.scheduler.add_job(
            func=self.cleanup_old_logs,
            trigger='cron',
            hour=2,
            minute=0,
            id='cleanup_logs'
        )
        
        # مهمة التشغيل التلقائي حسب الجدولة
        self.scheduler.add_job(
            func=self.auto_start_scheduled,
            trigger='interval',
            seconds=300,  # كل 5 دقائق
            id='auto_start'
        )
    
    def update_system_stats(self):
        """تحديث إحصائيات النظام"""
        try:
            stats = {
                'timestamp': datetime.now().isoformat(),
                'cpu_percent': psutil.cpu_percent(),
                'memory_percent': psutil.virtual_memory().percent,
                'disk_usage': psutil.disk_usage('/').percent,
                'network_io': psutil.net_io_counters()._asdict(),
                'running_channels': sum(1 for ch in self.channels.values() if ch['status'] == 'running')
            }
            
            socketio.emit('system_stats', stats)
            return stats
        except Exception as e:
            logger.error(f"خطأ في تحديث الإحصائيات: {e}")
    
    def auto_start_scheduled(self):
        """التشغيل التلقائي للقنوات المجدولة"""
        current_time = datetime.now().strftime('%H:%M')
        
        for channel_id, channel in self.channels.items():
            if channel['enabled'] and channel.get('auto_start', False):
                schedule = channel.get('schedule', {})
                
                if schedule.get('daily', False):
                    start_time = schedule.get('start_time', '00:00')
                    stop_time = schedule.get('stop_time', '23:59')
                    
                    # إذا كان الوقت بين بداية ونهاية التشغيل والقناة متوقفة
                    if (start_time <= current_time <= stop_time and 
                        channel['status'] == 'stopped'):
                        logger.info(f"تشغيل القناة {channel['name']} تلقائياً حسب الجدولة")
                        self.start_channel(channel_id)
                    
                    # إذا كان الوقت خارج ساعات التشغيل والقناة تعمل
                    elif ((current_time < start_time or current_time > stop_time) and 
                          channel['status'] == 'running'):
                        logger.info(f"إيقاف القناة {channel['name']} تلقائياً حسب الجدولة")
                        self.stop_channel(channel_id)
    
    def cleanup_old_logs(self, days=7):
        """تنظيف السجلات القديمة"""
        try:
            import glob
            from pathlib import Path
            
            log_files = glob.glob(os.path.join(LOG_DIR, '*.log'))
            cutoff_time = time.time() - (days * 86400)
            
            for log_file in log_files:
                if os.path.getmtime(log_file) < cutoff_time:
                    os.remove(log_file)
                    
            logger.info(f"تم تنظيف السجلات الأقدم من {days} أيام")
        except Exception as e:
            logger.error(f"خطأ في تنظيف السجلات: {e}")
    
    def get_channel_info(self, channel_id):
        """الحصول على معلومات القناة"""
        if channel_id in self.channels:
            channel = self.channels[channel_id].copy()
            
            # إضافة معلومات حية إذا كانت القناة تعمل
            if channel['status'] == 'running' and channel['pid']:
                try:
                    process = psutil.Process(channel['pid'])
                    channel['stats'] = {
                        'cpu_percent': process.cpu_percent(),
                        'memory_percent': process.memory_percent(),
                        'uptime': (datetime.now() - 
                                  datetime.fromisoformat(channel['last_started'])).total_seconds()
                    }
                except:
                    channel['stats'] = {'cpu_percent': 0, 'memory_percent': 0, 'uptime': 0}
            
            return channel
        return None

# إنشاء مدير القنوات
channel_manager = ChannelManager()

# ============================================================================
# واجهات API
# ============================================================================

@app.route('/')
@login_required
def index():
    """الصفحة الرئيسية"""
    return render_template('dashboard.html')

@app.route('/login', methods=['GET', 'POST'])
def login():
    """تسجيل الدخول"""
    if request.method == 'POST':
        username = request.form.get('username')
        password = request.form.get('password')  # في الإنتاج، استخدم تشفير كلمات المرور
        
        # مصادقة بسيطة (في الإنتاج استخدم قاعدة بيانات)
        if username == 'admin' and password == 'admin123':
            user = users['1']
            login_user(user)
            return redirect(url_for('index'))
        elif username == 'operator' and password == 'op123':
            user = users['2']
            login_user(user)
            return redirect(url_for('index'))
    
    return render_template('login.html')

@app.route('/logout')
@login_required
def logout():
    """تسجيل الخروج"""
    logout_user()
    return redirect(url_for('login'))

@app.route('/api/system/info')
@login_required
def system_info():
    """معلومات النظام"""
    info = {
        'hostname': os.uname().nodename,
        'system': os.uname().sysname,
        'release': os.uname().release,
        'python_version': sys.version,
        'ffmpeg_version': subprocess.getoutput('ffmpeg -version | head -n1'),
        'uptime': subprocess.getoutput('uptime -p'),
        'disk_space': subprocess.getoutput('df -h /').split('\n')[1],
        'load_average': os.getloadavg()
    }
    return jsonify(info)

@app.route('/api/channels', methods=['GET'])
@login_required
def get_all_channels():
    """جلب جميع القنوات"""
    channels = []
    for channel_id, channel in channel_manager.channels.items():
        channel_info = channel_manager.get_channel_info(channel_id)
        channels.append(channel_info)
    
    return jsonify({
        'count': len(channels),
        'channels': channels,
        'timestamp': datetime.now().isoformat()
    })

@app.route('/api/channels/import', methods=['POST'])
@login_required
def import_channels():
    """استيراد قنوات من ملف M3U8"""
    if current_user.role != 'admin':
        return jsonify({'success': False, 'message': 'صلاحيات غير كافية'}), 403
    
    data = request.get_json()
    m3u8_url = data.get('m3u8_url', 'http://192.168.3.2:800/playlist.m3u8')
    
    parsed_channels = channel_manager.parse_m3u8(m3u8_url)
    
    # إضافة القنوات الجديدة
    for channel in parsed_channels:
        if channel['id'] not in channel_manager.channels:
            channel_manager.channels[channel['id']] = channel
    
    # حفظ التغييرات
    channel_manager.save_channels()
    
    return jsonify({
        'success': True,
        'imported': len(parsed_channels),
        'total': len(channel_manager.channels)
    })

@app.route('/api/channels/<channel_id>', methods=['GET'])
@login_required
def get_channel(channel_id):
    """جلب معلومات قناة محددة"""
    channel_info = channel_manager.get_channel_info(channel_id)
    if channel_info:
        return jsonify(channel_info)
    return jsonify({'success': False, 'message': 'القناة غير موجودة'}), 404

@app.route('/api/channels/<channel_id>/start', methods=['POST'])
@login_required
def start_channel_api(channel_id):
    """تشغيل قناة"""
    if current_user.role not in ['admin', 'operator']:
        return jsonify({'success': False, 'message': 'صلاحيات غير كافية'}), 403
    
    result = channel_manager.start_channel(channel_id)
    return jsonify(result)

@app.route('/api/channels/<channel_id>/stop', methods=['POST'])
@login_required
def stop_channel_api(channel_id):
    """إيقاف قناة"""
    if current_user.role not in ['admin', 'operator']:
        return jsonify({'success': False, 'message': 'صلاحيات غير كافية'}), 403
    
    force = request.get_json().get('force', False)
    result = channel_manager.stop_channel(channel_id, force)
    return jsonify(result)

@app.route('/api/channels/<channel_id>', methods=['PUT'])
@login_required
def update_channel(channel_id):
    """تحديث إعدادات القناة"""
    if current_user.role != 'admin':
        return jsonify({'success': False, 'message': 'صلاحيات غير كافية'}), 403
    
    if channel_id not in channel_manager.channels:
        return jsonify({'success': False, 'message': 'القناة غير موجودة'}), 404
    
    data = request.get_json()
    channel = channel_manager.channels[channel_id]
    
    # تحديث الإعدادات المسموح بها
    updatable_fields = ['enabled', 'auto_start', 'transcode', 'output', 'schedule']
    for field in updatable_fields:
        if field in data:
            if field == 'output':
                channel['output'].update(data['output'])
            else:
                channel[field] = data[field]
    
    # إذا تم تعطيل القناة، أوقفها إذا كانت تعمل
    if not data.get('enabled', True) and channel['status'] == 'running':
        channel_manager.stop_channel(channel_id)
    
    # حفظ التغييرات
    channel_manager.save_channels()
    
    return jsonify({'success': True, 'channel': channel})

@app.route('/api/channels/<channel_id>', methods=['DELETE'])
@login_required
def delete_channel(channel_id):
    """حذف قناة"""
    if current_user.role != 'admin':
        return jsonify({'success': False, 'message': 'صلاحيات غير كافية'}), 403
    
    if channel_id not in channel_manager.channels:
        return jsonify({'success': False, 'message': 'القناة غير موجودة'}), 404
    
    # إيقاف القناة إذا كانت تعمل
    if channel_manager.channels[channel_id]['status'] == 'running':
        channel_manager.stop_channel(channel_id)
    
    # حذف القناة
    del channel_manager.channels[channel_id]
    
    # حذف ملفات القناة
    for file_type in ['.pid', '.log']:
        file_path = os.path.join(PROCESS_DIR if file_type == '.pid' else LOG_DIR, 
                                f"channel_{channel_id}{file_type}")
        if os.path.exists(file_path):
            os.remove(file_path)
    
    # حفظ التغييرات
    channel_manager.save_channels()
    
    return jsonify({'success': True, 'message': 'تم حذف القناة'})

@app.route('/api/batch/start', methods=['POST'])
@login_required
def batch_start():
    """تشغيل مجموعة من القنوات"""
    if current_user.role not in ['admin', 'operator']:
        return jsonify({'success': False, 'message': 'صلاحيات غير كافية'}), 403
    
    data = request.get_json()
    channel_ids = data.get('channels', [])
    
    results = []
    for channel_id in channel_ids:
        if channel_id in channel_manager.channels:
            result = channel_manager.start_channel(channel_id)
            result['channel_id'] = channel_id
            results.append(result)
    
    return jsonify({
        'success': True,
        'results': results,
        'total': len(results)
    })

@app.route('/api/batch/stop', methods=['POST'])
@login_required
def batch_stop():
    """إيقاف مجموعة من القنوات"""
    if current_user.role not in ['admin', 'operator']:
        return jsonify({'success': False, 'message': 'صلاحيات غير كافية'}), 403
    
    data = request.get_json()
    channel_ids = data.get('channels', [])
    
    results = []
    for channel_id in channel_ids:
        if channel_id in channel_manager.channels:
            result = channel_manager.stop_channel(channel_id)
            result['channel_id'] = channel_id
            results.append(result)
    
    return jsonify({
        'success': True,
        'results': results,
        'total': len(results)
    })

@app.route('/api/system/stats')
@login_required
def system_stats():
    """إحصائيات النظام"""
    return jsonify(channel_manager.update_system_stats())

@app.route('/api/logs/<channel_id>')
@login_required
def get_channel_logs(channel_id):
    """الحصول على سجلات القناة"""
    log_file = os.path.join(LOG_DIR, f"channel_{channel_id}.log")
    
    if not os.path.exists(log_file):
        return jsonify({'logs': []})
    
    try:
        with open(log_file, 'r', encoding='utf-8', errors='ignore') as f:
            # آخر 100 سطر
            lines = f.readlines()[-100:]
        
        logs = []
        for line in lines:
            logs.append(line.strip())
        
        return jsonify({'logs': logs})
    
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@app.route('/api/backup', methods=['POST'])
@login_required
def create_backup():
    """إنشاء نسخة احتياطية"""
    if current_user.role != 'admin':
        return jsonify({'success': False, 'message': 'صلاحيات غير كافية'}), 403
    
    try:
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        backup_dir = os.path.join(BASE_DIR, 'backups', timestamp)
        os.makedirs(backup_dir, exist_ok=True)
        
        # نسخ ملفات التكوين
        import shutil
        shutil.copy(os.path.join(CONFIG_DIR, 'channels.json'), 
                   os.path.join(backup_dir, 'channels.json'))
        
        # تصدير قاعدة البيانات (إذا كانت موجودة)
        # ... إضافة كود التصدير ...
        
        return jsonify({
            'success': True,
            'backup_path': backup_dir,
            'timestamp': timestamp
        })
    except Exception as e:
        return jsonify({'success': False, 'message': str(e)}), 500

# ============================================================================
# واجهات SocketIO للاتصال المباشر
# ============================================================================

@socketio.on('connect')
def handle_connect():
    """اتصال عميل جديد"""
    logger.info(f"عميل متصل: {request.sid}")
    emit('connected', {'message': 'مرحباً في نظام IPTV'})

@socketio.on('disconnect')
def handle_disconnect():
    """انفصال عميل"""
    logger.info(f"عميل منفصل: {request.sid}")

@socketio.on('get_channels')
def handle_get_channels():
    """إرسال قائمة القنوات للعميل"""
    channels = []
    for channel_id, channel in channel_manager.channels.items():
        channels.append(channel_manager.get_channel_info(channel_id))
    
    emit('channels_list', {'channels': channels})

# ============================================================================
# تشغيل التطبيق
# ============================================================================

if __name__ == '__main__':
    # إنشاء المجلدات المطلوبة
    for directory in [CONFIG_DIR, LOG_DIR, PROCESS_DIR, os.path.join(BASE_DIR, 'backups')]:
        os.makedirs(directory, exist_ok=True)
    
    # ملفات التكوين الافتراضية إذا لم تكن موجودة
    default_config = {
        'channels': [],
        'system': {
            'max_channels': 50,
            'auto_backup': True,
            'backup_retention_days': 30
        }
    }
    
    config_file = os.path.join(CONFIG_DIR, 'channels.json')
    if not os.path.exists(config_file):
        with open(config_file, 'w', encoding='utf-8') as f:
            json.dump(default_config, f, indent=2, ensure_ascii=False)
    
    # تشغيل التطبيق
    logger.info("بدء تشغيل نظام IPTV Manager...")
    socketio.run(app, 
                 host='0.0.0.0', 
                 port=8080, 
                 debug=False,  # ضع False في الإنتاج
                 use_reloader=False,
                 allow_unsafe_werkzeug=True)