#!/usr/bin/env python3
"""
سكريبت مراقبة النظام
"""

import time
import psutil
import requests
import json
from datetime import datetime

class SystemMonitor:
    def __init__(self, api_url="http://localhost:8080"):
        self.api_url = api_url
        
    def check_system_health(self):
        """فحص صحة النظام"""
        checks = {
            'timestamp': datetime.now().isoformat(),
            'cpu_usage': psutil.cpu_percent(interval=1),
            'memory_usage': psutil.virtual_memory().percent,
            'disk_usage': psutil.disk_usage('/').percent,
            'network_status': self.check_network(),
            'ffmpeg_processes': self.count_ffmpeg_processes(),
            'api_status': self.check_api()
        }
        
        return checks
    
    def check_network(self):
        """فحص الشبكة"""
        try:
            # محاولة الاتصال بالإنترنت
            requests.get('https://google.com', timeout=5)
            return 'connected'
        except:
            # فحص الشبكة المحلية
            try:
                requests.get('http://192.168.3.2:800/playlist.m3u8', timeout=3)
                return 'local_only'
            except:
                return 'disconnected'
    
    def count_ffmpeg_processes(self):
        """عد عمليات FFmpeg"""
        count = 0
        for proc in psutil.process_iter(['name']):
            if proc.info['name'] and 'ffmpeg' in proc.info['name'].lower():
                count += 1
        return count
    
    def check_api(self):
        """فحص API"""
        try:
            response = requests.get(f"{self.api_url}/api/system/info", timeout=3)
            return response.status_code == 200
        except:
            return False
    
    def send_alert(self, message, level='warning'):
        """إرسال تنبيه"""
        webhook_url = "YOUR_WEBHOOK_URL"  # للـ Telegram أو Slack
        
        payload = {
            'level': level,
            'message': message,
            'timestamp': datetime.now().isoformat()
        }
        
        try:
            requests.post(webhook_url, json=payload, timeout=3)
        except:
            pass  # لا تفشل إذا كان إرسال التنبيه غير متاح
    
    def run_monitor(self):
        """تشغيل المراقبة المستمرة"""
        print("🚀 بدء مراقبة النظام...")
        
        while True:
            health = self.check_system_health()
            
            # تسجيل النتائج
            with open('/opt/iptv-manager/logs/health.log', 'a') as f:
                f.write(json.dumps(health) + '\n')
            
            # إرسال تنبيهات إذا لزم
            if health['cpu_usage'] > 80:
                self.send_alert(f"استخدام CPU عالي: {health['cpu_usage']}%")
            
            if health['memory_usage'] > 85:
                self.send_alert(f"استخدام الذاكرة عالي: {health['memory_usage']}%")
            
            if health['api_status'] == False:
                self.send_alert("API غير متاح!", 'critical')
            
            # الانتظار 30 ثانية قبل الفحص التالي
            time.sleep(30)

if __name__ == '__main__':
    monitor = SystemMonitor()
    monitor.run_monitor()