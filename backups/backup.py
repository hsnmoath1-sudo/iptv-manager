#!/usr/bin/env python3
"""
سكريبت النسخ الاحتياطي
"""

import os
import json
import shutil
import tarfile
from datetime import datetime

def create_backup():
    """إنشاء نسخة احتياطية كاملة"""
    backup_dir = f"/opt/iptv-manager/backups/full_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
    os.makedirs(backup_dir, exist_ok=True)
    
    # الملفات والمجلدات المطلوبة
    items_to_backup = [
        '/opt/iptv-manager/etc',
        '/opt/iptv-manager/logs',
        '/opt/iptv-manager/processes'
    ]
    
    # نسخ الملفات
    for item in items_to_backup:
        if os.path.exists(item):
            if os.path.isdir(item):
                shutil.copytree(item, os.path.join(backup_dir, os.path.basename(item)))
            else:
                shutil.copy2(item, backup_dir)
    
    # تصدير إعدادات القنوات
    with open(os.path.join(backup_dir, 'export.json'), 'w') as f:
        export_data = {
            'timestamp': datetime.now().isoformat(),
            'channels': []  # سيتم ملؤها من التطبيق الرئيسي
        }
        json.dump(export_data, f, indent=2)
    
    # إنرشيف النسخة الاحتياطية
    tar_filename = f"{backup_dir}.tar.gz"
    with tarfile.open(tar_filename, "w:gz") as tar:
        tar.add(backup_dir, arcname=os.path.basename(backup_dir))
    
    # تنظيف المجلد المؤقت
    shutil.rmtree(backup_dir)
    
    print(f"✅ تم إنشاء نسخة احتياطية: {tar_filename}")
    return tar_filename

def restore_backup(backup_file):
    """استعادة نسخة احتياطية"""
    if not os.path.exists(backup_file):
        print("❌ ملف النسخ الاحتياطي غير موجود")
        return False
    
    try:
        # استخراج الأرشيف
        restore_dir = f"/opt/iptv-manager/restore_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
        os.makedirs(restore_dir, exist_ok=True)
        
        with tarfile.open(backup_file, "r:gz") as tar:
            tar.extractall(restore_dir)
        
        # استعادة الملفات
        extracted_dir = os.path.join(restore_dir, os.listdir(restore_dir)[0])
        
        for item in os.listdir(extracted_dir):
            source = os.path.join(extracted_dir, item)
            destination = os.path.join('/opt/iptv-manager', item)
            
            if os.path.exists(destination):
                if os.path.isdir(destination):
                    shutil.rmtree(destination)
                else:
                    os.remove(destination)
            
            if os.path.isdir(source):
                shutil.copytree(source, destination)
            else:
                shutil.copy2(source, destination)
        
        # تنظيف
        shutil.rmtree(restore_dir)
        
        print("✅ تم استعادة النسخة الاحتياطية بنجاح")
        return True
        
    except Exception as e:
        print(f"❌ خطأ في الاستعادة: {e}")
        return False

if __name__ == '__main__':
    import sys
    
    if len(sys.argv) > 1 and sys.argv[1] == 'restore':
        if len(sys.argv) > 2:
            restore_backup(sys.argv[2])
        else:
            print("الاستخدام: backup.py restore <ملف_النسخة_الاحتياطية>")
    else:
        create_backup()