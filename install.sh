#!/bin/bash
# install-iptv-manager.sh

echo "📦 تثبيت نظام إدارة IPTV..."

# 1. تحديث النظام
sudo apt update && sudo apt upgrade -y

# 2. تثبيت المتطلبات
sudo apt install -y \
    python3 python3-pip python3-venv \
    ffmpeg nginx supervisor \
    sqlite3 curl git

# 3. إنشاء هيكل المجلدات
sudo mkdir -p /opt/iptv-manager/{bin,etc,logs,static,templates,processes,backups}
sudo mkdir -p /opt/iptv-manager/static/{css,js,images}

# 4. إنشاء مستخدم للخدمة
sudo useradd -r -s /bin/false iptvmanager
sudo usermod -aG video iptvmanager

# 5. نسخ ملفات المشروع
git clone https://github.com/your-repo/iptv-manager.git /tmp/iptv-manager
sudo cp -r /tmp/iptv-manager/* /opt/iptv-manager/

# 6. إعداد أذونات
sudo chown -R iptvmanager:iptvmanager /opt/iptv-manager
sudo chmod +x /opt/iptv-manager/bin/*

# 7. إنشاء بيئة Python
cd /opt/iptv-manager
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt

# 8. تكوين النظام
sudo cp /opt/iptv-manager/config/iptv-manager.conf /etc/supervisor/conf.d/
sudo cp /opt/iptv-manager/config/nginx-site /etc/nginx/sites-available/iptv-manager

# 9. تفعيل الخدمات
sudo supervisorctl reread
sudo supervisorctl update
sudo systemctl restart nginx

# 10. إنشاء قاعدة البيانات الأولية
python3 /opt/iptv-manager/bin/init-db.py

echo "✅ تم التثبيت بنجاح!"
echo "🌐 الواجهة: http://$(hostname -I | awk '{print $1}'):8080"
echo "🔑 المستخدم الافتراضي: admin / admin123"