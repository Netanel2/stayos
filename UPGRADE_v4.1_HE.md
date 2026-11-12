# STAYOS v4.1 — ניהול קבלה וגיבויים

## לפני העלאה ל־Railway

1. גבה את `/data/platform.sqlite` ואת `/data/media` מחוץ ל־Volume. אל תמחק את ה־Volume.
2. העלה ל־GitHub את קובצי הגרסה כולל `backup.py`, `server.py`, `static/`, `tests/`, ו־`requirements.txt`.
3. Railway יפרוס את הקוד. בדוק `/health` — צריך להציג `4.1.0`.
4. לא צריך לאפס מסד נתונים: `reception_actions` מתווספת אוטומטית. זהו שינוי מצטבר.
5. שמור `STAYOS_PLATFORM_DB=/data/platform.sqlite`, `STAYOS_MEDIA_DIR=/data/media`, `STAYOS_DEMO_SEED=0`, `STAYOS_COOKIE_SECURE=1`.

## מה חדש לקבלה

- כל פנייה חדשה מופיעה ב־**Live Reception** בדף Overview, כולל מספר החדר ויעד מסירה.
- **Accept** מאשר שהעובד קיבל את הפנייה; **Assign to me** משייך אליו את הפעולה.
- ניתן לבצע פעולות אלה רק עם הרשאות עריכה לתחום (Food Orders, Requests, Activities, Chat).
- אחרי 3 דקות ללא אישור ההזמנה מסומנת כ־Overdue. ניתן לשנות באמצעות `STAYOS_ALERT_ESCALATE_MINUTES`.
- חיווי **Connected / Connection interrupted** מציין את מצב הקשר עם השרת.
- ההתראות נבדקות כל כ־4 שניות בדפדפן פתוח. **זה לא Push כשהדפדפן סגור**.
- לחיצה על **Accept** איננה משנה את סטטוס ההכנה/המסירה, אלא מאשרת לעובדים שהפנייה התקבלה במערכת.

## גיבויים

- Railway: גיבוי יומי אוטומטי אחרי העלאת השרת, כאשר השירות פועל (`STAYOS_AUTO_BACKUP=1`).
- נוצרת ארכיון עם SQLite עקבי ו־`/data/media`. יש בדיקת SQLite integrity ו־CRC של הקבצים.
- ברירת המחדל שומרת עד שבעה גיבויים תחת `/data/backups`. **אותו Volume אינו גיבוי חיצוני**.
- כדי לשמור גיבוי מחוץ ל־Volume הגדר Bucket פרטי תואם S3 ואת המשתנים הבאים ב־Railway:
  - `STAYOS_BACKUP_S3_BUCKET` — שם ה־bucket הפרטי
  - `AWS_ACCESS_KEY_ID`, `AWS_SECRET_ACCESS_KEY`, `AWS_DEFAULT_REGION` — credentials ייעודיים עם הרשאות כתיבה מצומצמות
  - `STAYOS_BACKUP_S3_ENDPOINT` — רק לשירות S3-compatible שאינו AWS S3
  - `STAYOS_BACKUP_S3_PREFIX` — לא חובה, ברירת מחדל `stayos/backups`
- בדיקת הגיבוי האחרון זמינה ל־owner בלבד ב־`/api/owner/backup-status`.
- להרצת גיבוי ידני: `python backup.py --now`.
- לוודא ארכיון: `python backup.py --verify /path/to/backup.zip`.
- לשחזר **לספרייה חדשה וריקה בלבד**: `python backup.py --restore-to /tmp/stayos-restore --archive /path/to/backup.zip`. בדוק נתונים בסביבה מבודדת לפני כל החלפת מערכת. אל תריץ שחזור על קבצים פעילים.
- אם אין S3, או שהגיבוי נתקע/נכשל, לא נכון להתחיל להכניס נתוני אורחים אמיתיים בלי גיבוי חיצוני שנבדק.

## לפני שימוש מסחרי

תמיד להחזיק Replica אחת בלבד עם SQLite, לבדוק הרשאות ואבטחה, לבצע שחזור ניסיוני של גיבוי ולוודא התראות תפעוליות בזמן אמת. סליקה לא קיימת; חיובים משולמים בקבלה.